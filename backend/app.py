import asyncio
import datetime
import enum
import math
import pathlib
import time
import typing
import urllib
import uuid

import discord
import httpx
import numpy
import pymongo
from buttons import (DisplayOrgButton, GenericShowEmbedButton, KickButton,
                     SnareCheckButton, UpdateAllButton)
from classes import Organisation, ParsingException, Profile
from constants import *
from loguru import logger
from rsi_profile import (extract_profile_info, org_to_embed, orgs_lookup,
                         profile_to_embed, url_to_org)
from snare import (Snare, line_point_dist, location_to_str, point_point_dist,
                   pretty_print_dist)

mongo: pymongo.MongoClient = pymongo.MongoClient(MONGODB_DOMAIN, 27017)

client = discord.Client(command_prefix=PREFIX, intents=discord.Intents.all())
tree = discord.app_commands.CommandTree(client)


class LetInButton(discord.ui.Button):
    def __init__(self, member_id: int, label: str, style: discord.ButtonStyle):
        self.member_id = member_id
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(interaction.guild, discord.Guild):
            member = interaction.guild.get_member(self.member_id)
            startrole = mongo[str(interaction.guild.id)]["config"].find_one(
                {"_id": "startrole"}
            )
            if startrole and member:
                role = interaction.guild.get_role(startrole["role"])
                if role:
                    await member.add_roles(role)

                    desired_nick = get_desired_nick(member)
                    if desired_nick:
                        await member.edit(nick=desired_nick)

                    await interaction.response.edit_message(
                        content=f"## {interaction.user.mention} let {member.mention} in, giving them the starting role {role.mention}",
                        view=None,
                    )
            elif not startrole:
                await interaction.response.edit_message(
                    content=f"## ERROR: missing start role, please set it with `{PREFIX}startrole`",
                )
            else:
                await interaction.response.edit_message(
                    content=f'## ERROR: could not find member with id "{self.member_id}" in {interaction.guild.name}',
                )
        else:
            logger.warning(
                f"LetInButton instantiazed outside Guild: {self.member_id} | {interaction}"
            )


def is_admin(interaction: discord.Interaction) -> bool:
    return (
        isinstance(interaction.user, discord.Member)
        and interaction.user.guild_permissions.administrator
    )


async def check_admin(interaction: discord.Interaction) -> bool:
    if not is_admin(interaction):
        await interaction.response.send_message(
            "Command only available for admins!", delete_after=MESSAGE_TIMEOUT
        )
        return False
    return True


async def check_guild(interaction: discord.Interaction) -> bool:
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "Command only available inside guilds (aka. Discord servers)!",
            delete_after=MESSAGE_TIMEOUT,
        )
        return False
    return True


def get_members_without_rsi_profiles(guild: discord.Guild) -> list[discord.Member]:
    return [
        m
        for m in guild.members
        if not m.bot and not mongo["global"]["profiles"].find_one({"_id": m.id})
    ]


def get_members_with_rsi_profiles(
    guild: discord.Guild,
) -> list[tuple[discord.Member, dict]]:
    res: list[tuple[discord.Member, dict]] = []
    for m in guild.members:
        if not m.bot and (dbm := mongo["global"]["profiles"].find_one({"_id": m.id})):
            res.append((m, dbm))
    return res


def get_role_icon(member: discord.Member, sorted_db_roles: list[dict]) -> str:
    member_role_ids = [r.id for r in member.roles]
    for db_role in sorted_db_roles:
        if db_role["_id"] in member_role_ids:
            return str(db_role["icon"]) if db_role["icon"] else ""
    return ""


def get_desired_nick(
    member: discord.Member,
    sorted_db_roles: list | None = None,
    db_wings: list | None = None,
) -> str | None:
    GUILD_DB = mongo[str(member.guild.id)]

    if not sorted_db_roles:
        sorted_db_roles = sorted(GUILD_DB["roles"].find(), key=lambda r: r["priority"])

    if not db_wings:
        db_wings = list(GUILD_DB["wings"].find())

    db_user = mongo["global"]["profiles"].find_one({"_id": member.id})
    if db_user:
        middle = db_user["nick"]
    else:
        middle = member.name

    return f"{get_role_icon(member, sorted_db_roles)} {middle} {get_role_icon(member, db_wings)}".strip()


def get_wrong_nicks(guild: discord.Guild) -> list[tuple]:
    GUILD_DB = mongo[str(guild.id)]

    sorted_db_roles = sorted(GUILD_DB["roles"].find(), key=lambda r: r["priority"])
    db_wings = list(GUILD_DB["wings"].find())
    wrong_nicks = []
    for member in guild.members:
        if not member.bot:
            desired_nick = get_desired_nick(member, sorted_db_roles, db_wings)

            if desired_nick and desired_nick != member.nick:
                wrong_nicks.append((member, desired_nick))
    return wrong_nicks


def get_feedback_admins(
    collection: pymongo.collection.Collection, guild: discord.Guild
) -> typing.Generator[discord.Member, None, None]:
    fba = collection.find_one({"_id": "feedbackadminrole"})
    if not isinstance(fba, dict) or "value" not in fba:
        return
    for member in guild.members:
        for role in member.roles:
            if role.id == fba["value"]:
                yield member


# ======== PUBLIC COMMANDS ========
@tree.command(name="snare", description=SNARE_DESCRIPTION)
@discord.app_commands.choices(
    source=[
        discord.app_commands.Choice(name=location_to_str(l), value=i)
        for i, l in enumerate(LOCATIONS)
    ]
)
@discord.app_commands.choices(
    destination=[
        discord.app_commands.Choice(name=location_to_str(l), value=i)
        for i, l in enumerate(LOCATIONS)
    ]
)
async def snare(
    interaction: discord.Interaction,
    source: discord.app_commands.Choice[int],
    destination: discord.app_commands.Choice[int],
) -> None:
    await interaction.response.defer(thinking=True)

    snare = Snare(source.name, destination.name)

    embed = discord.Embed(
        title=f"Full Coverage Snare Plan",
        description=f"`{location_to_str(snare.source)} -> {location_to_str(snare.destination)}`"
        + (
            f"\n## ❌ Only {snare.coverage*100:.1f}% coverage possible on this route!\nJust get as close to the Physics grid (**without passing into it**) on the centerline as you dare. The better you do the more you'll catch."
            if snare.coverage < 1
            else f"\n## ✅ Full route coverage possible\nAs always, try to get as close to the centerline as possible.\n\nAt `{pretty_print_dist(snare.optimal_pullout_dist)}` from `{location_to_str(snare.destination)}` you'll have `{pretty_print_dist(20_000 - line_point_dist(snare.hyp, snare.optimal_pullout))}` of leeway to be off the centerline and be `{pretty_print_dist(point_point_dist(snare.optimal_pullout, snare.point_of_physics))}` away from the physics grid of `{location_to_str(snare.destination)}`. This is therefore the location that gives you the most leeway in all directions."
        ),
        colour=discord.Colour.red() if snare.coverage < 1 else discord.Colour.green(),
    )
    view = discord.ui.View()

    embed.add_field(
        name="Centerline length",
        value=pretty_print_dist(
            point_point_dist(snare.source_point, snare.destination_point)
        ),
    )
    embed.add_field(
        name=f'"{location_to_str(snare.source)}" physics grid range',
        value=pretty_print_dist(snare.source["GRIDRadius"]),
    )
    embed.add_field(
        name=f'"{location_to_str(snare.destination)}" physics grid range',
        value=pretty_print_dist(snare.destination["GRIDRadius"]),
    )
    if snare.coverage >= 1:
        embed.add_field(
            name="Earliest pullout", value=pretty_print_dist(snare.min_pullout_dist)
        )
        embed.add_field(
            name="Optimal pullout", value=pretty_print_dist(snare.optimal_pullout_dist)
        )
        view.add_item(
            SnareCheckButton(snare, "Check my location!", discord.ButtonStyle.green)
        )

    await interaction.followup.send(embed=embed, view=view)


@tree.command(name="profile", description=PROFILE_DESCRIPTION)
async def profile(interaction: discord.Interaction, username: str) -> None:
    await interaction.response.defer(thinking=True)
    url = RSI_BASE_URL + username
    try:
        profile = extract_profile_info(url)
    except ParsingException as e:
        await interaction.followup.send(
            f"An error happened, please contact an admin and send them the following: {url} | {e}",
        )

    if isinstance(profile, Profile):
        COLLECTION = mongo["global"]["profiles"]

        db_user = {
            "_id": interaction.user.id,
            "url": RSI_BASE_URL + urllib.parse.quote(profile.handle),
            "nick": profile.handle,
        }
        try:
            COLLECTION.insert_one(db_user)
        except pymongo.errors.DuplicateKeyError:
            COLLECTION.replace_one({"_id": db_user["_id"]}, db_user)

        if isinstance(interaction.user, discord.Member):
            try:
                await interaction.user.edit(nick=get_desired_nick(interaction.user))
            except discord.errors.Forbidden as e:
                logger.warning(
                    f'Cannot change nickname for "{interaction.user.name}": {e}'
                )

        await interaction.followup.send(
            f"Updated linked RSI profile for user {interaction.user.mention} ✅\nRemember you can always update your profile with `{PREFIX}profile username`",
            embed=profile_to_embed(profile),
        )
    else:
        await interaction.followup.send(
            f'Could not find "{username}", please type your exact username (case insensitive) from https://robertsspaceindustries.com'
        )


@tree.command(
    name="whois",
    description=WHOIS_DESCRIPTION,
)
async def whois(interaction: discord.Interaction, member: discord.Member) -> None:
    await interaction.response.defer(thinking=True)

    if member == client.user:
        await interaction.followup.send(embed=get_bot_embed())
        return

    db_user = mongo["global"]["profiles"].find_one({"_id": member.id})
    view = discord.ui.View()

    if db_user:
        try:
            profile = extract_profile_info(db_user["url"])
            try:
                organisations = orgs_lookup(db_user["url"])
            except ParsingException:
                organisations = []
            if isinstance(organisations, list):
                for o in organisations:
                    view.add_item(
                        DisplayOrgButton(
                            org=o,
                            label=o.name
                            + (f" • {o.rank.name} ({o.rank.rank}/5)" if o.rank else ""),
                            style=o.primary_activity.button_style(),
                        )
                    )
            elif isinstance(organisations, int):
                await interaction.followup.send(
                    f'User {member.mention} has invalid URL ({db_user["url"]}) please update immediately via `{PREFIX}profile username`'
                )
        except ParsingException as e:
            await interaction.followup.send(
                f"An error happened, please contact an admin and send them the following: {db_user['url']} | {e}"
            )
            return

        if isinstance(profile, Profile):
            await interaction.followup.send(embed=profile_to_embed(profile), view=view)
            return
        else:
            await interaction.followup.send(
                f'User {member.mention} has invalid URL ({db_user["url"]}) please update immediately via `{PREFIX}profile username`'
            )
    else:
        await interaction.followup.send(
            f"{member.mention} has not yet linked their RSI profile, please do so via `{PREFIX}profile username`"
        )


@tree.command(
    name="lookup",
    description=LOOKUP_DESCRIPTION,
)
async def lookup(interaction: discord.Interaction, username: str) -> None:
    await interaction.response.defer(thinking=True)

    url = RSI_BASE_URL + username
    view = discord.ui.View()
    try:
        profile = extract_profile_info(url)
        try:
            organisations = orgs_lookup(url)
        except ParsingException:
            organisations = []
        if isinstance(organisations, list):
            for o in organisations:
                view.add_item(
                    DisplayOrgButton(
                        org=o,
                        label=o.name
                        + (f" • {o.rank.name} ({o.rank.rank}/5)" if o.rank else ""),
                        style=o.primary_activity.button_style(),
                    )
                )
    except ParsingException as e:
        await interaction.followup.send(
            f"An error happened, please contact an admin and send them the following: {url} | {e}"
        )
        return

    if isinstance(profile, Profile):
        await interaction.followup.send(embed=profile_to_embed(profile), view=view)
    else:
        await interaction.followup.send(f'No profile found on "{url}"')


def get_bot_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Calypso",
        description="A Star Citizen (RSI) management bot for Discord.\nGot a good idea or found a bug? Please create an issue: https://github.com/ghost-squadron/calypso/issues\n\n**Non-admin commands:**",
        url="https://github.com/ghost-squadron/calypso",
    )
    embed.set_author(
        name="Ghost Squadron presents:",
        url="https://gsag.space",
        icon_url="https://gsag.space/images/gsag-trans-padded.png",
    )
    embed.set_thumbnail(
        url="https://gsag.space/images/calypso.png",
    )
    embed.add_field(name="`/profile`", value=PROFILE_DESCRIPTION)
    embed.add_field(name="`/whois`", value=WHOIS_DESCRIPTION)
    embed.add_field(name="`/lookup`", value=LOOKUP_DESCRIPTION)
    embed.add_field(name="`/help`", value="Displays this message")
    embed.set_footer(
        text="https://github.com/ghost-squadron/calypso",
        icon_url="https://github.githubassets.com/favicons/favicon-dark.png",
    )
    return embed


@tree.command(
    name="help",
    description="Display basic information about the Bot",
)
async def bot_help(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(embed=get_bot_embed())


class Vote(enum.Enum):
    Nay = 0
    Yay = 1


@tree.command(
    name="feedback",
    description="Give/update feedback on another member",
)
async def feedback(
    interaction: discord.Interaction,
    member: discord.Member,
    feedback: str | None,
    vote: Vote | None,
) -> None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "Feedback only available inside Discord guild (server)",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    GUILD_DB = mongo[str(interaction.guild.id)]
    COLLECTION = GUILD_DB[f"feedback-{interaction.user.id}"]

    initial = COLLECTION.find_one({"_id": member.id}) or {}
    new = {**initial}
    if feedback is not None:
        new["feedback"] = feedback
    if vote is not None:
        new["vote"] = vote.value

    try:
        COLLECTION.insert_one({"_id": member.id, **new})
    except pymongo.errors.DuplicateKeyError:
        COLLECTION.replace_one({"_id": member.id}, new)

    feedback_channel = GUILD_DB["config"].find_one({"_id": "feedbackchannel"})
    if isinstance(feedback_channel, dict) and "value" in feedback_channel:
        if initial != new:
            for channel in interaction.guild.channels:
                if (
                    isinstance(channel, discord.TextChannel)
                    and channel.id == feedback_channel["value"]
                ):
                    embed = discord.Embed(title="Feedback updated")
                    embed.add_field(name="Giver", value=f"<@{interaction.user.id}>")
                    embed.add_field(name="Reciever", value=f"<@{member.id}>")
                    await channel.send(embed=embed)

    await interaction.followup.send(
        embed=discord.Embed(
            description=f"# {member.mention}"
            + (f" - Vote: {Vote(new['vote']).name}" if new["vote"] != None else "")
            + (f'\n{new["feedback"]}' if new["feedback"] != None else ""),
        ),
        ephemeral=True,
    )


@tree.command(
    name="myfeedback",
    description="List all feedback given on other members",
)
async def myfeedback(
    interaction: discord.Interaction, member: discord.Member | None
) -> None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "Feedback only available inside Discord guild (server)",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    GUILD_DB = mongo[str(interaction.guild.id)]
    COLLECTION = GUILD_DB[f"feedback-{interaction.user.id}"]

    await interaction.followup.send(
        embed=discord.Embed(
            description="\n".join(
                f"# <@{m['_id']}>"
                + (f" - Vote: {Vote(m['vote']).name}" if m["vote"] != None else "")
                + (f'\n{m["feedback"]}' if m["feedback"] != None else "")
                for m in COLLECTION.find()
                if not member or member.id == m["_id"]
            ),
        ),
        ephemeral=True,
    )


# ======== ADMIN COMMANDS ========
CURRENT_OPS = {}


@tree.command(name="adminprofile", description="Set RSI profiles for specific user")
async def adminprofile(
    interaction: discord.Interaction, member: discord.Member, username: str
) -> None:
    if not interaction.guild:
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    url = RSI_BASE_URL + username
    try:
        profile = extract_profile_info(url)
    except ParsingException as e:
        await interaction.followup.send(
            f"An error happened, please contact an admin and send them the following: {url} | {e}",
            ephemeral=True,
        )

    if isinstance(profile, Profile):
        COLLECTION = mongo["global"]["profiles"]

        db_user = {
            "_id": member.id,
            "url": RSI_BASE_URL + urllib.parse.quote(profile.handle),
            "nick": profile.handle,
        }
        try:
            COLLECTION.insert_one(db_user)
        except pymongo.errors.DuplicateKeyError:
            COLLECTION.replace_one({"_id": db_user["_id"]}, db_user)

        try:
            await member.edit(nick=get_desired_nick(member))
        except discord.errors.Forbidden as e:
            logger.warning(f'Cannot change nickname for "{member.mention}": {e}')

        await interaction.followup.send(
            f"Updated linked RSI profile for user {member.mention} ✅\nRemember you can always update your profile with `{PREFIX}profile username`",
            embed=profile_to_embed(profile),
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            f'Could not find "{username}", please type your exact username (case insensitive) from https://robertsspaceindustries.com',
            ephemeral=True,
        )


@tree.command(
    name="allfeedback",
    description="List all feedback. Either in total or given to a specific member",
)
async def allfeedback(
    interaction: discord.Interaction, member: discord.Member | None
) -> None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    GUILD_DB = mongo[str(interaction.guild.id)]
    fba = list(get_feedback_admins(GUILD_DB["config"], interaction.guild))

    if interaction.user not in fba:
        await interaction.followup.send(
            'Command only awailable for "feedback admins" - which you are not!'
        )
        return

    embeds = []
    if member:
        description = f"# Feedback for {member.mention}:"
        for collection_name in GUILD_DB.list_collection_names():
            if "feedback-" in collection_name and (
                f := GUILD_DB[collection_name].find_one({"_id": member.id})
            ):
                member_id = collection_name.split("-")[-1]
                description += (
                    f"\n## Giver: <@{member_id}>"
                    + (f" | Vote: {Vote(f['vote']).name}" if f["vote"] != None else "")
                    + (f'\n{f["feedback"]}' if f["feedback"] != None else "")
                )
        description = (
            description.strip() or f"No feedback given for {member.mention} yet"
        )
        embeds.append(discord.Embed(description=description))
    else:
        for collection_name in GUILD_DB.list_collection_names():
            if "feedback-" in collection_name:
                description = f'# Feedback for <@{collection_name.split("-")[-1]}>'
                for f in GUILD_DB[collection_name].find():
                    description += (
                        f"\n## Giver: <@{f['_id']}>"
                        + (
                            f" | Vote: {Vote(f['vote']).name}"
                            if f["vote"] != None
                            else ""
                        )
                        + (f'\n{f["feedback"]}' if f["feedback"] != None else "")
                    )
                if description:
                    embeds.append(discord.Embed(description=description))

    if embeds:
        await interaction.followup.send(embeds=embeds, ephemeral=True)
    else:
        await interaction.followup.send(
            "No feedback given yet" + (f" for {member.mention}" if member else ""),
            ephemeral=True,
        )


@tree.command(
    name="feedbackadmin",
    description="Set feedback admin role",
)
async def feedbackadmin(interaction: discord.Interaction, role: discord.Role) -> None:
    if not interaction.guild or not await check_admin(interaction):
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    GUILD_DB = mongo[str(interaction.guild.id)]
    COLLECTION = GUILD_DB["config"]
    try:
        COLLECTION.insert_one({"_id": "feedbackadminrole", "value": role.id})
    except pymongo.errors.DuplicateKeyError:
        COLLECTION.replace_one({"_id": "feedbackadminrole"}, {"value": role.id})

    await interaction.followup.send(
        embed=discord.Embed(
            title="Feedback admins",
            description="\n".join(
                f"- {a.mention}"
                for a in get_feedback_admins(COLLECTION, interaction.guild)
            ),
        ),
        ephemeral=True,
    )


@tree.command(
    name="feedbackchannel",
    description="Set feedback updates channel",
)
async def feedbackchannel(
    interaction: discord.Interaction, channel: discord.TextChannel
) -> None:
    if not interaction.guild or not await check_admin(interaction):
        return

    await interaction.response.defer(thinking=True, ephemeral=True)

    GUILD_DB = mongo[str(interaction.guild.id)]
    COLLECTION = GUILD_DB["config"]
    try:
        COLLECTION.insert_one({"_id": "feedbackchannel", "value": channel.id})
    except pymongo.errors.DuplicateKeyError:
        COLLECTION.replace_one({"_id": "feedbackchannel"}, {"value": channel.id})

    await interaction.followup.send(
        embed=discord.Embed(
            title="Feedback updates channel",
            description=f"<#{COLLECTION.find_one({'_id': 'feedbackchannel'})['value']}>",  # type: ignore
        ),
        ephemeral=True,
    )


@tree.command(
    name="startop",
    description="Starts an operation in a specific Voice Channel",
)
async def startop(
    interaction: discord.Interaction, lookup: str, channel: discord.VoiceChannel
) -> None:
    if (
        not isinstance(interaction.user, discord.Member)
        or not interaction.guild
        or not await check_admin(interaction)
    ):
        return

    await interaction.response.defer(thinking=True)

    GUILD_DB = mongo[str(interaction.guild.id)]
    COLLECTION = GUILD_DB["ops"]
    VOICE_IDS = {"Dorothy": "ThT5KcBeYPX3keUQqHPh", "Grace": "oWAxZDx7w5VEj9dCyTzz"}

    if description := COLLECTION.find_one({"_id": lookup}):
        text = f'An active operation is currently underway in "{channel.name.split("・")[-1]}".\n\n{description["description"]}\n\nIf you are not interested in participating in this operation please leave the voice channel. However, if you are, remember to respect ranks and good luck!'
        response = httpx.post(
            f'https://api.elevenlabs.io/v1/text-to-speech/{VOICE_IDS["Dorothy"]}',
            json={"model_id": "eleven_multilingual_v1", "text": text},
            headers={
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": ELEVENLABS_API_KEY,
            },
            timeout=5 * 60,
        )
        if not response.is_success:
            logger.warning(response)
            logger.warning(response.text)
            return
        audio_file = AUDIO_DIR / f"{uuid.uuid4()}.mp3"
        with open(audio_file, "wb") as f:
            f.write(response.content)
        CURRENT_OPS[str(channel.id)] = {
            "audio_file": audio_file,
            "informed_members": [],
            "voice_client": None,
            "afters": [],
        }

        await channel.edit(status=":siren: LIVE OPERATION !!!")  # type: ignore

        await interaction.followup.send(
            f"Operation activated in {channel.mention} - Good luck commander o7"
        )
    else:
        await interaction.followup.send(
            f'Could not find operation "{lookup}"',
            ephemeral=True,
        )


@tree.command(
    name="endop",
    description="Ends a currently running operation",
)
async def endops(
    interaction: discord.Interaction, channel: discord.VoiceChannel
) -> None:
    if not interaction.guild or not await check_admin(interaction):
        return

    if str(channel.id) in CURRENT_OPS:
        del CURRENT_OPS[str(channel.id)]
        await channel.edit(status=None)  # type: ignore
        await interaction.response.send_message(f"Operation ended in {channel.mention}")
    else:
        await interaction.response.send_message(
            f"No operation currently active in {channel.mention} ¯\\_(ツ)_/¯"
        )


@tree.command(
    name="runningops",
    description="Lists all currently running operations",
)
async def runningops(interaction: discord.Interaction) -> None:
    if not interaction.guild or not await check_admin(interaction):
        return

    description = ""
    for c in interaction.guild.channels:
        if str(c.id) in CURRENT_OPS:
            description += f"- {c.mention}\n"

    if description:
        await interaction.response.send_message(
            embed=discord.Embed(
                title="Current operations",
                description=description
                + f"\n\nRemember you can end operations with the `{PREFIX}endop` command, and start them with `{PREFIX}startop`",
            )
        )
    else:
        await interaction.response.send_message(
            f"There are no currently running operations, you can start a new operation with the `{PREFIX}startop`",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )


@tree.command(
    name="listops",
    description=f"List all operations used to lookup when calling {PREFIX}startop",
)
async def listops(interaction: discord.Interaction) -> None:
    if not await check_admin(interaction):
        return
    assert interaction.guild

    GUILD_DB = mongo[str(interaction.guild.id)]
    COLLECTION = GUILD_DB["ops"]
    embed = discord.Embed(title="Operations")
    for c in COLLECTION.find():
        embed.add_field(name=c["_id"], value=c["description"])
    await interaction.response.send_message(
        embed=embed, ephemeral=True, delete_after=MESSAGE_TIMEOUT
    )


@tree.command(
    name="setop",
    description=f"Sets/adds an operations lookup used when calling {PREFIX}startop",
)
async def setop(
    interaction: discord.Interaction, lookup: str, description: str
) -> None:
    if not await check_admin(interaction):
        return
    assert interaction.guild

    lookup = lookup.lower().strip()
    description = description.strip()

    GUILD_DB = mongo[str(interaction.guild.id)]
    COLLECTION = GUILD_DB["ops"]
    try:
        COLLECTION.insert_one({"_id": lookup, "description": description})
    except pymongo.errors.DuplicateKeyError:
        COLLECTION.replace_one({"_id": lookup}, {"description": description})

    embed = discord.Embed(title=lookup, description=description)
    await interaction.response.send_message(
        embed=embed, ephemeral=True, delete_after=MESSAGE_TIMEOUT
    )


@tree.command(
    name="setbotvoice",
    description=f"Sets the voice channel used by the bot",
)
async def setbotvoice(
    interaction: discord.Interaction, channel: discord.VoiceChannel
) -> None:
    if not interaction.guild or not await check_admin(interaction):
        return

    GUILD_DB = mongo[str(interaction.guild.id)]
    COLLECTION = GUILD_DB["config"]
    try:
        COLLECTION.insert_one({"_id": "botvoice", "value": channel.id})
    except pymongo.errors.DuplicateKeyError:
        COLLECTION.replace_one({"_id": "botvoice"}, {"value": channel.id})

    await interaction.response.send_message(
        f"Set bot voice channel to {channel.mention}",
        ephemeral=True,
        delete_after=MESSAGE_TIMEOUT,
    )


@tree.command(
    name="clear",
    description="Clears all messages sent by Calypso in this channel",
)
async def clear(interaction: discord.Interaction) -> None:
    if not await check_admin(interaction):
        return

    if isinstance(interaction.channel, discord.TextChannel):
        await interaction.response.send_message(
            f"Deleting all messages by Calypso...",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        msg = await interaction.original_response()
        total = 0
        async for m in interaction.channel.history(limit=None):
            if m.author == client.user:
                await m.delete()
                total += 1
                await msg.edit(content=f"Deleted {total} messages...")
    else:
        await interaction.response.send_message(
            "Command only works in TextChannels", delete_after=MESSAGE_TIMEOUT
        )
    await msg.edit(content=f"✅ Deleted all {total} messages")


@tree.command(
    name="setorg",
    description="Sets the currently associated RSI org",
)
async def setorg(interaction: discord.Interaction, sid: str) -> None:
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message(
            "Command only available inside guild",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        return

    if not await check_admin(interaction):
        return

    url = f"https://robertsspaceindustries.com/orgs/{sid}"

    try:
        org = url_to_org(url, None)
        assert isinstance(org, Organisation)
        embed = org_to_embed(org)
    except ParsingException as e:
        await interaction.response.send_message(
            f'Could not organisation on "{url}": {e}', delete_after=MESSAGE_TIMEOUT
        )
    COLLECTION = mongo[str(interaction.guild.id)]["config"]
    try:
        COLLECTION.insert_one({"_id": "rsiorg", "sid": sid})
        await interaction.response.send_message(
            embed=embed, delete_after=MESSAGE_TIMEOUT
        )
    except pymongo.errors.DuplicateKeyError:
        COLLECTION.replace_one({"_id": "rsiorg"}, {"sid": sid})
        await interaction.response.send_message(
            embed=embed, delete_after=MESSAGE_TIMEOUT
        )


@tree.command(
    name="addrole",
    description="Adds or updates a role and its icon to the prioritized list of role icons used during user renaming",
)
async def addrole(
    interaction: discord.Interaction,
    role: discord.Role,
    icon: str,
    priority: int,
    rsirank: int,
) -> None:
    if not await check_admin(interaction):
        return
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message(
            "Command only available inside guild",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        return
    await interaction.response.defer(thinking=True, ephemeral=True)

    COLLECTION = mongo[str(interaction.guild.id)]["roles"]
    db_role = {"_id": role.id, "icon": icon, "priority": priority, "rsirank": rsirank}
    try:
        COLLECTION.insert_one(db_role)
        await interaction.followup.send(
            f"Added role: {role.mention} (`{priority} | {rsirank}/5 | {icon}`)",
            ephemeral=True,
        )
    except pymongo.errors.DuplicateKeyError:
        COLLECTION.replace_one({"_id": role.id}, db_role)
        await interaction.followup.send(
            f"Updated role: {role.mention} (`{priority} | {rsirank}/5 | {icon}`)",
            ephemeral=True,
        )


@tree.command(
    name="addwing",
    description="Adds or updates a wing-role and its icon used during user renaming",
)
async def addwing(
    interaction: discord.Interaction, role: discord.Role, icon: str
) -> None:
    if not await check_admin(interaction):
        return
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message(
            "Command only available inside guild",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        return

    COLLECTION = mongo[str(interaction.guild.id)]["wings"]
    db_role = {"_id": role.id, "icon": icon}
    try:
        COLLECTION.insert_one(db_role)
        await interaction.response.send_message(
            f"Added wing: {role.mention} - {icon}", delete_after=MESSAGE_TIMEOUT
        )
    except pymongo.errors.DuplicateKeyError:
        COLLECTION.replace_one({"_id": role.id}, db_role)
        await interaction.response.send_message(
            f"Updated wing: {role.mention} - {icon}", delete_after=MESSAGE_TIMEOUT
        )


@tree.command(
    name="delrole",
    description="Removes a role used during user renaming",
)
async def delrole(interaction: discord.Interaction, role: discord.Role) -> None:
    if not await check_admin(interaction):
        return
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message(
            "Command only available inside guild",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        return

    res = mongo[str(interaction.guild.id)]["roles"].delete_one({"_id": role.id})

    if not res.deleted_count:
        await interaction.response.send_message(
            f"Role not in list - roles can be added with `{PREFIX}addrole role`",
            delete_after=MESSAGE_TIMEOUT,
        )
    else:
        await interaction.response.send_message(
            f"Deleted role: {role.mention}", delete_after=MESSAGE_TIMEOUT
        )


@tree.command(
    name="delwing",
    description="Removes a wing-role used during user renaming",
)
async def delwing(interaction: discord.Interaction, role: discord.Role) -> None:
    if not await check_admin(interaction):
        return
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message(
            "Command only available inside guild",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        return

    res = mongo[str(interaction.guild.id)]["wings"].delete_one({"_id": role.id})

    if not res.deleted_count:
        await interaction.response.send_message(
            f"Wing not in list - wings can be added with `{PREFIX}addwing wing`",
            delete_after=MESSAGE_TIMEOUT,
        )
    else:
        await interaction.response.send_message(
            f"Deleted wing: {role.mention}", delete_after=MESSAGE_TIMEOUT
        )


@tree.command(
    name="listroles",
    description="List all roles used during user renaming",
)
async def listroles(interaction: discord.Interaction) -> None:
    if not await check_admin(interaction):
        return
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message(
            "Command only available inside guild",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        return

    roles: list[dict] = list(
        sorted(
            mongo[str(interaction.guild.id)]["roles"].find(),
            key=lambda r: r["priority"],
        )
    )
    if not roles:
        await interaction.response.send_message(
            "No roles added yet", delete_after=MESSAGE_TIMEOUT
        )
        return

    max_priority_width = max(len(str(r["priority"])) for r in roles)

    await interaction.response.send_message(
        "Roles:\n"
        + "\n".join(
            f'`{role["priority"]:0{max_priority_width}} | {role["rsirank"]}/5 | {role["icon"]}` - <@&{role["_id"]}>'
            for role in roles
        ),
        delete_after=MESSAGE_TIMEOUT,
    )


@tree.command(
    name="listwings",
    description="List all wings used during user renaming",
)
async def listwings(interaction: discord.Interaction) -> None:
    if not await check_admin(interaction) or not interaction.guild:
        return
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message(
            "Command only available inside guild",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        return

    COLLECTION = mongo[str(interaction.guild.id)]["wings"]
    wings: list[dict] = []
    role_ids = [w.id for w in interaction.guild.roles]
    for wing in COLLECTION.find():
        if wing["_id"] not in role_ids:
            COLLECTION.delete_one(wing)
        else:
            wings.append(wing)

    if not wings:
        await interaction.response.send_message(
            "No wings added yet", delete_after=MESSAGE_TIMEOUT
        )
        return

    await interaction.response.send_message(
        "Wings:\n" + "\n".join(f'{wing["icon"]} - <@&{wing["_id"]}>' for wing in wings),
        delete_after=MESSAGE_TIMEOUT,
    )


@tree.command(
    name="status",
    description="Get status report for members of guild",
)
async def status(interaction: discord.Interaction) -> None:
    if not await check_admin(interaction):
        return

    await interaction.response.defer(thinking=True)

    assert interaction.guild

    view = discord.ui.View()

    missing_members = get_members_without_rsi_profiles(interaction.guild)
    total_members = len([m for m in interaction.guild.members if not m.bot])

    description = f" {total_members - len(missing_members)}/{total_members} members have linked their RSI profiles"

    if not missing_members:
        description = f"✅{description}\n"
    else:
        description = f"❌{description}\n"
        view.add_item(
            GenericShowEmbedButton(
                discord.Embed(
                    title="Members missing RSI profiles",
                    description="\n".join(f"- {m.mention}" for m in missing_members),
                ),
                None,
                label="Show members missing RSI Profiles",
                style=discord.ButtonStyle.red,
            )
        )

    wrong_nicks = get_wrong_nicks(interaction.guild)
    if not wrong_nicks:
        description += f"✅ {total_members - len(wrong_nicks)}/{total_members} members have correct nicknames"
    else:
        description += f"❌ {total_members - len(wrong_nicks)}/{total_members} members have correct nicknames"

        wrong_nicks_view = discord.ui.View()
        wrong_nicks_view.add_item(
            UpdateAllButton(
                wrong_nicks=wrong_nicks,
                label="Update all wrong nicknames",
                style=discord.ButtonStyle.red,
            ),
        )
        view.add_item(
            GenericShowEmbedButton(
                discord.Embed(
                    title="Members with wrong nicknames",
                    description="\n".join(
                        f'- {m.mention} -> "{u}"' for m, u in wrong_nicks
                    ),
                ),
                wrong_nicks_view,
                label="Show wrong nicknames",
                style=discord.ButtonStyle.red,
            )
        )

    for member, db_member in get_members_with_rsi_profiles(interaction.guild):
        try:
            rsi_profile = extract_profile_info(db_member["url"])
        except ParsingException as e:
            await interaction.followup.send(
                f"An error happened, please contact an admin and send them the following: {db_member['url']} | {e}"
            )

    embed = discord.Embed(
        title=f'"{interaction.guild.name}" status',
        description=description,
    )
    await interaction.followup.send(embed=embed, view=view)


@tree.command(
    name="adminchal",
    description="Set the admin channel",
)
async def adminchal(
    interaction: discord.Interaction, channel: discord.TextChannel
) -> None:
    if not await check_admin(interaction):
        return

    assert interaction.guild

    COLLECTION = mongo[str(interaction.guild.id)]["config"]
    try:
        COLLECTION.insert_one({"_id": "adminchal", "channel": channel.id})
        await interaction.response.send_message(
            f"Added admin channel {channel.mention}", delete_after=MESSAGE_TIMEOUT
        )
    except pymongo.errors.DuplicateKeyError:
        COLLECTION.replace_one({"_id": "adminchal"}, {"channel": channel.id})
        await interaction.response.send_message(
            f"Updated admin channel {channel.mention}", delete_after=MESSAGE_TIMEOUT
        )


@tree.command(
    name="startrole",
    description="Set the starting role",
)
async def startrole(interaction: discord.Interaction, role: discord.Role) -> None:
    if not await check_admin(interaction):
        return

    assert interaction.guild

    COLLECTION = mongo[str(interaction.guild.id)]["config"]
    try:
        COLLECTION.insert_one({"_id": "startrole", "role": role.id})
        await interaction.response.send_message(
            f"Added starting role {role.mention}", delete_after=MESSAGE_TIMEOUT
        )
    except pymongo.errors.DuplicateKeyError:
        COLLECTION.replace_one({"_id": "startrole"}, {"role": role.id})
        await interaction.response.send_message(
            f"Updated starting role {role.mention}", delete_after=MESSAGE_TIMEOUT
        )


@tree.command(
    name="addjoin",
    description="Adds or updates texts for user joining roles",
)
async def addjoin(
    interaction: discord.Interaction, role: discord.Role, text: str
) -> None:
    if not await check_admin(interaction):
        return
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message(
            "Command only available inside guild",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        return

    COLLECTION = mongo[str(interaction.guild.id)]["join"]
    try:
        COLLECTION.insert_one({"_id": role.id, "text": text})
        await interaction.response.send_message(
            f"Added text for join role {role.mention}", delete_after=MESSAGE_TIMEOUT
        )
    except pymongo.errors.DuplicateKeyError:
        COLLECTION.replace_one({"_id": role.id}, {"text": text})
        await interaction.response.send_message(
            f"Updated text for join role {role.mention}", delete_after=MESSAGE_TIMEOUT
        )


@tree.command(
    name="listjoin",
    description="List all current texts for user joining roles",
)
async def listjoin(interaction: discord.Interaction) -> None:
    if not await check_admin(interaction):
        return
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message(
            "Command only available inside guild",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        return

    join_texts: list[dict] = list(mongo[str(interaction.guild.id)]["join"].find())
    if not join_texts:
        await interaction.response.send_message(
            "No join texts added yet", delete_after=MESSAGE_TIMEOUT
        )
        return

    await interaction.response.send_message(
        "Join texts:\n"
        + "\n".join(f'- <@&{j["_id"]}>: "{j["text"]}"' for j in join_texts),
        delete_after=MESSAGE_TIMEOUT,
    )


@tree.command(
    name="addtrigger",
    description="Adds a trigger role",
)
async def addtrigger(interaction: discord.Interaction, role: discord.Role) -> None:
    if not await check_admin(interaction):
        return

    assert interaction.guild

    try:
        mongo[str(interaction.guild.id)]["trigger"].insert_one({"_id": role.id})
        await interaction.response.send_message(
            f"{role.mention} added as trigger role", delete_after=MESSAGE_TIMEOUT
        )
    except pymongo.errors.DuplicateKeyError:
        await interaction.response.send_message(
            f"{role.mention} already added as trigger role",
            delete_after=MESSAGE_TIMEOUT,
        )


@tree.command(
    name="listtriggers",
    description="List all current trigger roles",
)
async def listtriggers(interaction: discord.Interaction) -> None:
    if not await check_admin(interaction):
        return
    if not isinstance(interaction.guild, discord.Guild):
        await interaction.response.send_message(
            "Command only available inside guild",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        return

    trigger_roles: list[dict] = list(mongo[str(interaction.guild.id)]["trigger"].find())
    if not trigger_roles:
        await interaction.response.send_message(
            "No trigger roles added yet", delete_after=MESSAGE_TIMEOUT
        )
        return

    await interaction.response.send_message(
        "Trigger roles:\n" + "\n".join(f'- <@&{j["_id"]}>' for j in trigger_roles),
        delete_after=MESSAGE_TIMEOUT,
    )


# ======== EVENTS ========
@client.event
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    is_new = (not after.joined_at) or (
        (after.joined_at - datetime.datetime.now(datetime.timezone.utc))
        < datetime.timedelta(days=1)
    )
    GUILD_DB = mongo[str(after.guild.id)]
    has_role = any(
        t for t in GUILD_DB["roles"].find() if t["_id"] in [a.id for a in after.roles]
    )
    if any(
        t
        for t in GUILD_DB["config"].find()
        if t["_id"] not in [b.id for b in before.roles]
        and t["_id"] in [a.id for a in after.roles]
    ) and (not has_role or is_new):
        description = f"## What we know about them:\n"
        try:
            await after.send(WELCOME_MSG.format(member=after.mention, prefix=PREFIX))
        except discord.errors.Forbidden:
            description = f"{after.mention} has disabled the ability for bots to send them direct messages - **please ask them to add their RSI profile manually with `{PREFIX}profile`**\n{description}"
        adminchal = GUILD_DB["config"].find_one({"_id": "adminchal"})
        if adminchal:
            channel = after.guild.get_channel(adminchal["channel"])

            if isinstance(channel, discord.TextChannel):
                view = discord.ui.View()

                view.add_item(
                    LetInButton(
                        member_id=after.id,
                        label="Let in",
                        style=discord.ButtonStyle.green,
                    )
                )
                view.add_item(
                    KickButton(
                        member_id=after.id, label="Kick", style=discord.ButtonStyle.red
                    )
                )

                after_role_ids = [r.id for r in after.roles]
                embed = discord.Embed(
                    title=after.name,
                    description=description
                    + "\n".join(
                        f'- {j["text"]}'
                        for j in GUILD_DB["join"].find()
                        if j["_id"] in after_role_ids
                    ),
                )
                embed.set_image(url=after.avatar)
                await channel.send(
                    content=f"## {after.mention} just joined!", embed=embed, view=view
                )


class AfterPlaybackAction:
    def __init__(
        self,
        member: discord.Member,
        ops_channel: discord.VoiceChannel,
        afters: list,
    ):
        self.member = member
        self.ops_channel = ops_channel
        self.afters = afters

    def after(self, error: Exception | None) -> None:
        voice_client = CURRENT_OPS[str(self.ops_channel.id)]["voice_client"]

        if error:
            logger.error(error)

        CURRENT_OPS[str(self.ops_channel.id)]["informed_members"].append(self.member.id)  # type: ignore
        asyncio.run_coroutine_threadsafe(
            self.member.move_to(self.ops_channel), client.loop
        )
        if isinstance(voice_client, discord.VoiceClient):
            asyncio.run_coroutine_threadsafe(voice_client.disconnect(), client.loop)
            CURRENT_OPS[str(self.ops_channel.id)]["voice_client"] = None

        for a in self.afters:
            a.afters = []
            a.after(None)


@client.event
async def on_voice_state_update(
    member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
) -> None:
    if (
        isinstance(after.channel, discord.VoiceChannel)
        and str(after.channel.id) in CURRENT_OPS
        and member.id not in CURRENT_OPS[str(after.channel.id)]["informed_members"]  # type: ignore
        and before.channel != after.channel
    ):
        await after.channel.edit(status=":siren: LIVE OPERATION !!!")  # type: ignore
        GUILD_DB = mongo[str(member.guild.id)]
        COLLECTION = GUILD_DB["config"]
        if bot_voice_id := COLLECTION.find_one({"_id": "botvoice"}):
            bot_voice = member.guild.get_channel(bot_voice_id["value"])
            if (
                isinstance(bot_voice, discord.VoiceChannel)
                and before.channel != bot_voice
            ):
                audio_file = CURRENT_OPS[str(after.channel.id)]["audio_file"]
                voice_client = CURRENT_OPS[str(after.channel.id)]["voice_client"]
                ops_channel = after.channel

                await member.move_to(bot_voice)  # THIS CHANGES "after.channel"...
                if not voice_client:
                    CURRENT_OPS[str(ops_channel.id)][
                        "voice_client"
                    ] = await bot_voice.connect()
                    voice_client = CURRENT_OPS[str(ops_channel.id)]["voice_client"]

                assert isinstance(audio_file, pathlib.Path)
                assert isinstance(voice_client, discord.VoiceClient)
                after_play = AfterPlaybackAction(
                    member,
                    ops_channel,
                    CURRENT_OPS[str(ops_channel.id)]["afters"],  # type: ignore
                )
                CURRENT_OPS[str(ops_channel.id)]["afters"].append(after_play)  # type: ignore
                time.sleep(1)  # Wait for client to join
                try:
                    voice_client.play(
                        discord.FFmpegOpusAudio(str(audio_file.absolute())),
                        after=after_play.after,
                    )
                except discord.errors.ClientException:
                    voice_client.pause()
                    voice_client.play(
                        discord.FFmpegOpusAudio(str(audio_file.absolute())),
                        after=after_play.after,
                    )


@client.event
async def on_ready() -> None:
    await tree.sync()

    await client.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching, name="for new ships..."
        )
    )


client.run(DISCORD_API_TOKEN)
