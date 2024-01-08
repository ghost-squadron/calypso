import datetime
import os
import sys
import urllib.parse

import discord
import dotenv
import httpx
import pymongo
from bs4 import BeautifulSoup, NavigableString, Tag
from loguru import logger
from pydantic import BaseModel

dotenv.load_dotenv()

DISCORD_API_TOKEN: str = os.environ["DISCORD_API_TOKEN"]
RSI_BASE_URL = "https://robertsspaceindustries.com/citizens/"
MONGODB_DOMAIN = os.environ.get("MONGODB_DOMAIN", default="localhost")

mongodb_client: pymongo.MongoClient = pymongo.MongoClient(MONGODB_DOMAIN, 27017)
DB = mongodb_client["database"]
USERS_COLLECTION = DB["users"]
ROLES_COLLECTION = DB["roles"]
WINGS_COLLECTION = DB["wings"]
CONFIG_COLLECTION = DB["config"]
JOIN_COLLECTION = DB["join"]
TRIGGER_COLLECTION = DB["trigger"]
PREFIX = "/"

client = discord.Client(command_prefix=PREFIX, intents=discord.Intents.all())
tree = discord.app_commands.CommandTree(client)


class RSIUser(BaseModel):
    url: str
    user_name: str
    enlisted: datetime.datetime
    image: str


def find_or_except(
    soup: BeautifulSoup | Tag, key: str | None, value: str, desc: str
) -> Tag:
    if key:
        res = soup.find(attrs={key: value})
    else:
        res = soup.find(value)
    if not isinstance(res, Tag):
        raise Exception(f'Could not find {key if key else ""} "{value}" on "{desc}"')
    return res


def find_child_or_except(
    soup: BeautifulSoup | Tag,
    value: str,
    index: int,
    desc: str,
    recursive: bool = False,
) -> Tag:
    res = soup.findChildren(value, recursive=recursive)
    if len(res) < index + 1:
        raise Exception(
            f'Expected at least {index+1} children on "{value}" on "{desc}" but found {len(res)}'
        )
    res_i = res[index]
    if not isinstance(res_i, Tag):
        raise Exception(f'Could not find child {index} on "{value}" on "{desc}"')
    return res_i


def rsi_lookup(url: str) -> int | discord.Embed:
    r = httpx.get(url)

    if not r.is_success:
        return r.status_code

    soup = BeautifulSoup(r.text, "html.parser")

    embed = discord.Embed()

    public_profile = find_or_except(soup, "id", "public-profile", "page")

    # Extract user "Handle name"
    info_tag = find_or_except(public_profile, "class", "info", "public-profile")
    handle_parent_tag = find_child_or_except(info_tag, "p", 1, "info")
    handle = find_or_except(
        handle_parent_tag, None, "strong", "handle parent"
    ).text.strip()
    embed.title = handle
    embed.url = RSI_BASE_URL + urllib.parse.quote(handle)

    # Extract user badge
    badge_parent_tag = find_child_or_except(info_tag, "p", 2, "info")
    badge_icon_url = find_or_except(badge_parent_tag, None, "img", "info badge img")[
        "src"
    ]
    if not isinstance(badge_icon_url, str):
        raise Exception(f'Could not get src from "badge" img on page: "{url}"')
    if not badge_icon_url.startswith("http"):
        badge_icon_url = "https://robertsspaceindustries.com" + badge_icon_url
    badge_text = find_child_or_except(
        badge_parent_tag, "span", 1, "info badge text"
    ).text.strip()
    embed.set_footer(text=f"{badge_text} • Enlisted", icon_url=badge_icon_url)

    # Extract profile image
    thumb_tag = find_or_except(public_profile, "class", "thumb", "public-profile")
    image = find_child_or_except(thumb_tag, "img", 0, "thumb")["src"]
    if not isinstance(image, str):
        raise Exception(f'Could not get src from "thumb" img on page: "{url}"')
    if not image.startswith("http"):
        image = "https://robertsspaceindustries.com" + image
    embed.set_image(url=image)

    # Extract "UEE Citizen Record" id
    citizen_record_tag = find_or_except(
        public_profile, "class", "citizen-record", "public-profile"
    )
    citizen_record_id = find_child_or_except(
        citizen_record_tag, "strong", 0, "citizen-record"
    ).text.strip()
    if citizen_record_id != "n/a":
        embed.description = f"UEE Citizen Record **{citizen_record_id}**"

    # Extract main org
    main_org_tag = find_or_except(public_profile, "class", "main-org", "public-profile")
    main_org_img = main_org_tag.find("img")
    main_org_link = main_org_tag.find("a")
    main_org_info = main_org_tag.find(attrs={"class": "info"})

    if (
        isinstance(main_org_img, Tag)
        and isinstance(main_org_link, Tag)
        and isinstance(main_org_info, Tag)
    ):
        main_org = (
            find_child_or_except(main_org_info, "p", 0, "main-org info").text.strip()
            or "[REDACTED]"
        )

        main_org_image_url = main_org_img["src"]
        assert isinstance(main_org_image_url, str)
        if not main_org_image_url.startswith("http"):
            main_org_image_url = (
                "https://robertsspaceindustries.com" + main_org_image_url
            )

        main_org_href = main_org_link["href"]
        assert isinstance(main_org_href, str)
        if not main_org_href.startswith("http"):
            main_org_href = "https://robertsspaceindustries.com" + main_org_href

        embed.set_author(
            name=f"Main Org: {main_org}", url=main_org_href, icon_url=main_org_image_url
        )
    # Extract bio
    bio_tag = public_profile.find(attrs={"class": "bio"})
    if isinstance(bio_tag, Tag):
        bio_body_tag = bio_tag.find("div")
        if isinstance(bio_body_tag, Tag):
            embed.description = (
                embed.description or "" + f"\n\n**Bio:**\n{bio_body_tag.text.strip()}"
            )

    # Extract enlisted timestamp
    left_col = public_profile.find_all(attrs={"class": "left-col"})[-1]
    embed.timestamp = datetime.datetime.strptime(
        left_col.find_all(attrs={"class": "value"})[0].text.strip(), "%b %d, %Y"
    )

    return embed


def get_members_with_missing_rsi_profiles(guild: discord.Guild) -> list[discord.Member]:
    return [
        m
        for m in guild.members
        if not m.bot and not USERS_COLLECTION.find_one({"_id": m.id})
    ]


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
    if not sorted_db_roles:
        sorted_db_roles = sorted(ROLES_COLLECTION.find(), key=lambda r: r["priority"])

    if not db_wings:
        db_wings = list(WINGS_COLLECTION.find())

    db_user = USERS_COLLECTION.find_one({"_id": member.id})
    if db_user:
        return f'{get_role_icon(member, sorted_db_roles)} {db_user["nick"]} {get_role_icon(member, db_wings)}'.strip()
    else:
        return None


def get_wrong_nicks(guild: discord.Guild) -> list[tuple]:
    sorted_db_roles = sorted(ROLES_COLLECTION.find(), key=lambda r: r["priority"])
    db_wings = list(WINGS_COLLECTION.find())
    wrong_nicks = []
    for member in guild.members:
        if not member.bot:
            desired_nick = get_desired_nick(member, sorted_db_roles, db_wings)

            if desired_nick and desired_nick != member.nick:
                wrong_nicks.append((member, desired_nick))
    return wrong_nicks


# ======== PUBLIC COMMANDS ========
@tree.command(name="profile", description="Add/update your linked RSI profile")
async def profile(interaction: discord.Interaction, username: str) -> None:
    url = RSI_BASE_URL + username
    try:
        user = rsi_lookup(url)
    except Exception as e:
        await interaction.response.send_message(
            f"An error happened, please contact an admin and send them the following: {url} | {e}"
        )

    if isinstance(user, discord.Embed):
        db_user = {"_id": interaction.user.id, "url": user.url, "nick": user.title}
        try:
            USERS_COLLECTION.insert_one(db_user)
        except pymongo.errors.DuplicateKeyError:
            USERS_COLLECTION.replace_one({"_id": db_user["_id"]}, db_user)

        if isinstance(interaction.user, discord.Member):
            desired_nick = get_desired_nick(interaction.user)
            try:
                await interaction.user.edit(nick=desired_nick)
            except discord.errors.Forbidden as e:
                logger.warning(
                    f'Cannot change nickname for "{interaction.user.mention}": {e}'
                )

        await interaction.response.send_message(
            f"Updated linked RSI profile for user {interaction.user.mention} ✅\nRemember you can always update your profile with `{PREFIX}profile username`",
            embed=user,
        )
    else:
        await interaction.response.send_message(
            f'Could not find "{username}", please type your exact username (case insensitive) from https://robertsspaceindustries.com'
        )


@tree.command(
    name="whois",
    description="Looks up the RSI profile linked to a specific discord member",
)
async def whois(interaction: discord.Interaction, member: discord.Member) -> None:
    db_user = USERS_COLLECTION.find_one({"_id": member.id})

    if db_user:
        try:
            user = rsi_lookup(db_user["url"])
        except Exception as e:
            await interaction.response.send_message(
                f"An error happened, please contact an admin and send them the following: {db_user['url']} | {e}"
            )
            return

        if isinstance(user, discord.Embed):
            await interaction.response.send_message(embed=user)
        else:
            await interaction.response.send_message(
                f'User {member.mention} has invalid URL ({db_user["url"]}) please update immediately via `{PREFIX}profile username`'
            )

    else:
        await interaction.response.send_message(
            f"{member.mention} has not yet linked their RSI profile, please do so via `{PREFIX}profile username`"
        )


@tree.command(
    name="lookup",
    description="Looks up an RSI profile (must be exact match, case insensitive)",
)
async def lookup(interaction: discord.Interaction, lookup: str) -> None:
    url = RSI_BASE_URL + lookup
    user = rsi_lookup(url)
    if isinstance(user, int):
        await interaction.response.send_message(f'No profile found on "{url}"')
    elif isinstance(user, discord.Embed):
        await interaction.response.send_message(embed=user)
    else:
        await interaction.response.send_message(
            f"An error happened, please contact an admin and send them the following: {user}"
        )


# ======== ADMIN COMMANDS ========
@tree.command(
    name="addrole",
    description="Adds or updates a role and its icon to the prioritized list of role icons used during user renaming",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def addrole(
    interaction: discord.Interaction, role: discord.Role, icon: str, priority: int
) -> None:
    db_role = {"_id": role.id, "icon": icon, "priority": priority}
    try:
        ROLES_COLLECTION.insert_one(db_role)
        await interaction.response.send_message(
            f"Added role: {role.mention} (`{priority} | {icon}`)"
        )
    except pymongo.errors.DuplicateKeyError:
        ROLES_COLLECTION.replace_one({"_id": role.id}, db_role)
        await interaction.response.send_message(
            f"Updated role: {role.mention} (`{priority} | {icon}`)"
        )


@tree.command(
    name="addwing",
    description="Adds or updates a wing-role and its icon used during user renaming",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def addwing(
    interaction: discord.Interaction, role: discord.Role, icon: str
) -> None:
    db_role = {"_id": role.id, "icon": icon}
    try:
        WINGS_COLLECTION.insert_one(db_role)
        await interaction.response.send_message(f"Added wing: {role.mention} - {icon}")
    except pymongo.errors.DuplicateKeyError:
        WINGS_COLLECTION.replace_one({"_id": role.id}, db_role)
        await interaction.response.send_message(
            f"Updated wing: {role.mention} - {icon}"
        )


@tree.command(
    name="delrole",
    description="Removes a role used during user renaming",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def delrole(interaction: discord.Interaction, role: discord.Role) -> None:
    res = ROLES_COLLECTION.delete_one({"_id": role.id})

    if not res.deleted_count:
        await interaction.response.send_message(
            f"Role not in list - roles can be added with `{PREFIX}addrole role`"
        )
    else:
        await interaction.response.send_message(f"Deleted role: {role.mention}")


@tree.command(
    name="delwing",
    description="Removes a wing-role used during user renaming",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def delwing(interaction: discord.Interaction, role: discord.Role) -> None:
    res = WINGS_COLLECTION.delete_one({"_id": role.id})

    if not res.deleted_count:
        await interaction.response.send_message(
            f"Wing not in list - wings can be added with `{PREFIX}addwing wing`"
        )
    else:
        await interaction.response.send_message(f"Deleted wing: {role.mention}")


@tree.command(
    name="listroles",
    description="List all roles used during user renaming",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def listroles(interaction: discord.Interaction) -> None:
    roles: list[dict] = list(
        sorted(ROLES_COLLECTION.find(), key=lambda r: r["priority"])
    )
    if not roles:
        await interaction.response.send_message("No roles added yet")
        return

    max_priority_width = max(len(str(r["priority"])) for r in roles)

    await interaction.response.send_message(
        "Roles:\n"
        + "\n".join(
            f'`{role["priority"]:0{max_priority_width}}` | {role["icon"]} - <@&{role["_id"]}>'
            for role in roles
        )
    )


@tree.command(
    name="listwings",
    description="List all wings used during user renaming",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def listwings(interaction: discord.Interaction) -> None:
    wings: list[dict] = list(WINGS_COLLECTION.find())
    if not wings:
        await interaction.response.send_message("No wings added yet")
        return

    await interaction.response.send_message(
        "Wings:\n" + "\n".join(f'{wing["icon"]} - <@&{wing["_id"]}>' for wing in wings)
    )


@tree.command(
    name="status",
    description="Get status report for members of guild",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def status(interaction: discord.Interaction) -> None:
    assert interaction.guild

    missing_members = get_members_with_missing_rsi_profiles(interaction.guild)
    wrong_nicks = get_wrong_nicks(interaction.guild)
    total_members = len([m for m in interaction.guild.members if not m.bot])

    description = (
        f" {total_members - len(missing_members)}/{total_members} linked RSI profiles"
    )

    if not missing_members:
        description = f"✅{description}\n"
    else:
        description = f"❌{description}. Members missing RSI profiles:\n"
        description += "\n".join(f"- {m.mention}" for m in missing_members) + "\n"
        description += f"Use `{PREFIX}askall` to ask all members missing RSI profiles to update it\n\n"

    total_members -= len(missing_members)
    if not wrong_nicks:
        description += f"✅ {total_members - len(wrong_nicks)}/{total_members} members with RSI profiles have correct nicknames"
    else:
        description += f"❌ {total_members - len(wrong_nicks)}/{total_members} members with RSI profiles have correct nicknames\n"
        description += (
            "\n".join(f'- {m.mention} -> "{u}"' for m, u in wrong_nicks) + "\n"
        )
        description += f"Use `{PREFIX}updateall` to update all wrong nicknames."

    embed = discord.Embed(
        title=f'"{interaction.guild.name}" status',
        description=description,
    )
    await interaction.response.send_message(embed=embed)


ASK_MSG = '## Hi {member}! "{guild_name}" seems to be missing some information about you - let me help you with that!\n- Please update your linked RSI profile by typing `{prefix}profile username`\n - Use your exact `username` (case insensitive) from https://robertsspaceindustries.com'


@tree.command(
    name="ask",
    description="One member of the Guild to update their linked RSI profile",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def ask(interaction: discord.Interaction, member: discord.Member) -> None:
    assert interaction.guild

    await member.send(
        ASK_MSG.format(
            member=member.mention, guild_name=interaction.guild.name, prefix=PREFIX
        )
    )
    await interaction.response.send_message(
        content=f"✅ {member.mention} has been asked to update their linked RSI profile"
    )


@tree.command(
    name="askall",
    description="Ask all members of the Guild with missing RSI profile to add it",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def askall(interaction: discord.Interaction) -> None:
    assert interaction.guild

    missing_members = get_members_with_missing_rsi_profiles(interaction.guild)

    await interaction.response.send_message(f"Asking {len(missing_members)} members...")
    msg = await interaction.original_response()

    for i, member in enumerate(missing_members):
        await msg.edit(content=f"Asked {i}/{len(missing_members)} members...")
        await member.send(
            ASK_MSG.format(
                member=member.mention, guild_name=interaction.guild.name, prefix=PREFIX
            )
        )

    await msg.edit(
        content=f"✅ Asked all {len(missing_members)} members with missing RSI profiles"
    )


@tree.command(
    name="updateall",
    description="Update the server nicknames of all members of the Guild to match their RSI profile and role icon",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def updateall(interaction: discord.Interaction) -> None:
    assert interaction.guild

    wrong_nicks = get_wrong_nicks(interaction.guild)

    await interaction.response.send_message(
        f"Updating nicknames for {len(wrong_nicks)} members..."
    )
    msg = await interaction.original_response()
    skipped = 0
    for i, (member, nick) in enumerate(wrong_nicks):
        await msg.edit(content=f"Updated {i}/{len(wrong_nicks)} nicknames...")
        try:
            await member.edit(nick=nick)
        except discord.errors.Forbidden as e:
            logger.warning(f'Cannot change nickname for "{member}": {e}')
            skipped += 1

    if not skipped:
        await msg.edit(content=f"✅ Updated all {len(wrong_nicks)} nicknames")
    else:
        await msg.edit(
            content=f"❌ Updated {len(wrong_nicks) - skipped}/{len(wrong_nicks)} nicknames due to permission error - remember bots cannot change owner and admin nicknames on Discord (has to be done manually)"
        )


@tree.command(
    name="adminchal",
    description="Set the admin channel",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def adminchal(
    interaction: discord.Interaction, channel: discord.TextChannel
) -> None:
    assert interaction.guild

    try:
        CONFIG_COLLECTION.insert_one({"_id": "adminchal", "channel": channel.id})
        await interaction.response.send_message(
            f"Added admin channel {channel.mention}"
        )
    except pymongo.errors.DuplicateKeyError:
        CONFIG_COLLECTION.replace_one({"_id": "adminchal"}, {"channel": channel.id})
        await interaction.response.send_message(
            f"Updated admin channel {channel.mention}"
        )


@tree.command(
    name="startrole",
    description="Set the starting role",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def startrole(interaction: discord.Interaction, role: discord.Role) -> None:
    assert interaction.guild

    try:
        CONFIG_COLLECTION.insert_one({"_id": "startrole", "role": role.id})
        await interaction.response.send_message(f"Added starting role {role.mention}")
    except pymongo.errors.DuplicateKeyError:
        CONFIG_COLLECTION.replace_one({"_id": "startrole"}, {"role": role.id})
        await interaction.response.send_message(f"Updated starting role {role.mention}")


@tree.command(
    name="addjoin",
    description="Adds or updates texts for user joining roles",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def addjoin(
    interaction: discord.Interaction, role: discord.Role, text: str
) -> None:
    try:
        JOIN_COLLECTION.insert_one({"_id": role.id, "text": text})
        await interaction.response.send_message(
            f"Added text for join role {role.mention}"
        )
    except pymongo.errors.DuplicateKeyError:
        JOIN_COLLECTION.replace_one({"_id": role.id}, {"text": text})
        await interaction.response.send_message(
            f"Updated text for join role {role.mention}"
        )


@tree.command(
    name="listjoin",
    description="List all current texts for user joining roles",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def listjoin(interaction: discord.Interaction) -> None:
    join_texts: list[dict] = list(JOIN_COLLECTION.find())
    if not join_texts:
        await interaction.response.send_message("No join texts added yet")
        return

    await interaction.response.send_message(
        "Join texts:\n"
        + "\n".join(f'- <@&{j["_id"]}>: "{j["text"]}"' for j in join_texts)
    )


@tree.command(
    name="addtrigger",
    description="Adds a trigger role",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def addtrigger(interaction: discord.Interaction, role: discord.Role) -> None:
    assert interaction.guild

    try:
        TRIGGER_COLLECTION.insert_one({"_id": role.id})
        await interaction.response.send_message(f"{role.mention} added as trigger role")
    except pymongo.errors.DuplicateKeyError:
        await interaction.response.send_message(
            f"{role.mention} already added as trigger role"
        )


@tree.command(
    name="listtriggers",
    description="List all current trigger roles",
)
@discord.app_commands.checks.has_permissions(administrator=True)
async def listtriggers(interaction: discord.Interaction) -> None:
    trigger_roles: list[dict] = list(TRIGGER_COLLECTION.find())
    if not trigger_roles:
        await interaction.response.send_message("No trigger roles added yet")
        return

    await interaction.response.send_message(
        "Trigger roles:\n" + "\n".join(f'- <@&{j["_id"]}>' for j in trigger_roles)
    )


class LetInButton(discord.ui.Button):
    def __init__(self, member_id: int, label: str, style: discord.ButtonStyle):
        self.member_id = member_id
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(interaction.guild, discord.Guild):
            member = interaction.guild.get_member(self.member_id)
            startrole = CONFIG_COLLECTION.find_one({"_id": "startrole"})
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


class KickButton(discord.ui.Button):
    def __init__(self, member_id: int, label: str, style: discord.ButtonStyle):
        self.member_id = member_id
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(interaction.guild, discord.Guild):
            member = interaction.guild.get_member(self.member_id)
            if isinstance(member, discord.Member):
                await member.kick()
                await interaction.response.edit_message(
                    content=f"## {interaction.user.mention} kicked {member.mention}",
                    view=None,
                )


@client.event
async def on_member_update(before: discord.Member, after: discord.Member) -> None:
    if any(
        t
        for t in TRIGGER_COLLECTION.find()
        if t["_id"] not in [b.id for b in before.roles]
        and t["_id"] in [a.id for a in after.roles]
    ):
        await after.send(
            f"# Welcome {after.mention}!\nTo help our dear admins to better get you started I am here to help you link your RSI profile to our Discord.\n\nIt is actually quite simple. Please just run the command `{PREFIX}profile username` and your're all set!\n\nAnd don't worry, you can always update this again with the same command if you make a mistake."
        )
        adminchal = CONFIG_COLLECTION.find_one({"_id": "adminchal"})
        if adminchal:
            channel = after.guild.get_channel(adminchal["channel"])

            if isinstance(channel, discord.TextChannel):
                view = discord.ui.View()

                view.add_item(
                    LetInButton(
                        member_id=after.id,
                        label=f"Let in",
                        style=discord.ButtonStyle.green,
                    )
                )
                view.add_item(
                    discord.ui.Button(label="Kick", style=discord.ButtonStyle.red)
                )

                after_role_ids = [r.id for r in after.roles]
                embed = discord.Embed(
                    title=f"{after.mention} just joined!\n",
                    description=f"What we know about them:\n"
                    + "\n".join(
                        f'- {j["text"]}'
                        for j in JOIN_COLLECTION.find()
                        if j["_id"] in after_role_ids
                    ),
                )
                await channel.send(embed=embed, view=view)


@client.event
async def on_ready() -> None:
    await tree.sync()

    await client.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching, name="for new ships..."
        )
    )


client.run(DISCORD_API_TOKEN)
