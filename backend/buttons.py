import discord
import numpy
from classes import Organisation
from constants import *
from loguru import logger
from rsi_profile import org_to_embed
from snare import Snare, pretty_print_dist


class UpdateAllButton(discord.ui.Button):
    def __init__(
        self,
        wrong_nicks: list[tuple[discord.Member, str]],
        label: str,
        style: discord.ButtonStyle,
    ):
        self.wrong_nicks = wrong_nicks
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(interaction.guild, discord.Guild)

        await interaction.response.send_message(
            f"Updating nicknames for {len(self.wrong_nicks)} members...",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )
        msg = await interaction.original_response()
        skipped = 0
        for i, (member, nick) in enumerate(self.wrong_nicks):
            await msg.edit(content=f"Updated {i}/{len(self.wrong_nicks)} nicknames...")
            try:
                await member.edit(nick=nick)
            except discord.errors.Forbidden as e:
                logger.warning(f'Cannot change nickname for "{member}": {e}')
                skipped += 1

        if not skipped:
            await msg.edit(content=f"✅ Updated all {len(self.wrong_nicks)} nicknames")
        else:
            await msg.edit(
                content=f"❌ Updated {len(self.wrong_nicks) - skipped}/{len(self.wrong_nicks)} nicknames due to permission error - remember bots cannot change owner and admin nicknames on Discord (has to be done manually)"
            )


class AskAllButton(discord.ui.Button):
    def __init__(
        self,
        missing_members: list[discord.Member],
        label: str,
        style: discord.ButtonStyle,
    ):
        self.missing_members = missing_members
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(interaction.guild, discord.Guild)
        await interaction.response.send_message(
            f"Asking {len(self.missing_members)} members...",
            ephemeral=True,
            delete_after=MESSAGE_TIMEOUT,
        )

        msg = await interaction.original_response()
        not_asked = []
        for i, member in enumerate(self.missing_members):
            try:
                await member.send(
                    ASK_MSG.format(
                        member=member.mention,
                        guild_name=interaction.guild.name,
                        prefix=PREFIX,
                    )
                )
            except discord.errors.Forbidden:
                not_asked.append(member)
            await msg.edit(content=f"Asked {i}/{len(self.missing_members)} members...")

        content = f"✅ Asked {len(self.missing_members) - len(not_asked)}/{len(self.missing_members)} members with missing RSI profiles\n"
        if not_asked:
            content += f"❌ {len(not_asked)}/{len(self.missing_members)} has disabled the ability for bots to send direct messages and can therefore not be asked link their RSI profile:\n"
            content += "\n".join(f"- {m.mention}" for m in not_asked)
        await msg.edit(content=content)


class GenericShowEmbedButton(discord.ui.Button):
    def __init__(
        self,
        embed: discord.Embed,
        view: discord.ui.View | None,
        label: str,
        style: discord.ButtonStyle,
    ):
        self.input_embed = embed
        self.input_view = view
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.input_view:
            await interaction.response.send_message(
                delete_after=MESSAGE_TIMEOUT,
                embed=self.input_embed,
                view=self.input_view,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                delete_after=MESSAGE_TIMEOUT,
                embed=self.input_embed,
                ephemeral=True,
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


class DisplayOrgButton(discord.ui.Button):
    def __init__(self, org: Organisation, label: str, style: discord.ButtonStyle):
        self.org = org
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction) -> None:
        if isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                delete_after=MESSAGE_TIMEOUT,
                embed=org_to_embed(self.org),
                ephemeral=True,
            )


class SnareCheckModal(discord.ui.Modal):
    def __init__(self, snare: Snare, title: str) -> None:
        self.snare = snare
        super().__init__(title=title)

        self.add_item(
            discord.ui.TextInput(label='Please paste the output of "/showlocation"')
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert isinstance(self.children[0], discord.ui.TextInput)
        try:
            location = numpy.array(
                [float(l.split(":")[-1]) for l in self.children[0].value.split()[1:]]
            )
            route = self.snare.get_route(location)
            if not route:
                await interaction.response.send_message(
                    "# ❌ WITHIN PHYSICS GRID!\nPlease reset and try again",
                    ephemeral=True,
                    delete_after=MESSAGE_TIMEOUT,
                )
                return

            if route.snare_cone_dist < 0:
                description = "# ✅ Within snare cone!"
                colour = discord.Colour.green()
            else:
                description = f"# ❌ {pretty_print_dist(route.snare_cone_dist)} outside snare cone!"
                colour = discord.Colour.red()

            description += (
                "\n## Route to centerline:\nFacing your destination and rotated so up for your ship is Stanton north:"
                + (
                    f"\n- Travel {pretty_print_dist(abs(route.z_mag))} {route.z_dir}"
                    if abs(route.z_mag) > 1
                    else ""
                )
                + (
                    f"\n- Travel {pretty_print_dist(abs(route.s_mag))} {route.s_dir}"
                    if abs(route.s_mag) > 1
                    else ""
                )
                + (
                    f"\n### Final travel to optimal pullout:\n- Travel {pretty_print_dist(abs(route.f_mag))} {route.f_dir}"
                    if abs(route.f_mag) > 1
                    else ""
                )
            )

            embed = discord.Embed(
                title="Snare check", description=description, colour=colour
            )
            embed.add_field(
                name="Distance to centerline",
                value=pretty_print_dist(route.centerline_dist),
            )
            embed.add_field(
                name="Distance to Physics Grid",
                value=pretty_print_dist(
                    route.destination_dist - route.destination["GRIDRadius"]
                ),
            )
            embed.add_field(
                name="Location score", value=f"{route.location_score:.1f}/10"
            )
            await interaction.response.send_message(
                embed=embed, ephemeral=True, delete_after=MESSAGE_TIMEOUT
            )
        except Exception as e:
            logger.error(e)
            await interaction.response.send_message(
                'Something went wrong while parsing your coordinates - make sure you paste the exact output of the "/showlocation" command',
                ephemeral=True,
                delete_after=MESSAGE_TIMEOUT,
            )


class SnareCheckButton(discord.ui.Button):
    def __init__(self, snare: Snare, label: str, style: discord.ButtonStyle):
        self.snare = snare
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            SnareCheckModal(self.snare, "Check your location")
        )
