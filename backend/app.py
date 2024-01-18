import asyncio
import datetime
import json
import os
import pathlib
import string
import sys
import time
import math
import typing
import urllib.parse
import uuid

import discord
import dotenv
import httpx
import markdownify  # type: ignore
import numpy
import numpy.typing
import pydantic
import pymongo
import sympy
from bs4 import BeautifulSoup, PageElement, Tag
from loguru import logger

# from sentence_transformers import SentenceTransformer, util  # type: ignore

dotenv.load_dotenv()

DISCORD_API_TOKEN: str = os.environ["DISCORD_API_TOKEN"]
ELEVENLABS_API_KEY: str = os.environ["ELEVENLABS_API_KEY"]
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

# Command descriptions
PROFILE_DESCRIPTION = "Add/update your linked RSI profile"
WHOIS_DESCRIPTION = "Looks up the RSI profile linked to a specific discord member"
LOOKUP_DESCRIPTION = "Looks up an RSI profile (must be exact match, case insensitive)"
SNARE_DESCRIPTION = "Command for assisting in planning where to set up your snare to *actually* catch everyone"

client = discord.Client(command_prefix=PREFIX, intents=discord.Intents.all())
tree = discord.app_commands.CommandTree(client)

ACTIVITY_LOOKUP = {
    "Bounty Hunting": (discord.ButtonStyle.green, discord.Colour.green()),
    "Engineering": (discord.ButtonStyle.blurple, discord.Colour.blurple()),
    "Exploration": (discord.ButtonStyle.blurple, discord.Colour.blurple()),
    "Freelancing": (discord.ButtonStyle.gray, discord.Colour.from_str("#888888")),
    "Infiltration": (discord.ButtonStyle.gray, discord.Colour.from_str("#888888")),
    "Medical": (discord.ButtonStyle.blurple, discord.Colour.blurple()),
    "Piracy": (discord.ButtonStyle.red, discord.Colour.red()),
    "Resources": (discord.ButtonStyle.blurple, discord.Colour.blurple()),
    "Scouting": (discord.ButtonStyle.green, discord.Colour.green()),
    "Security": (discord.ButtonStyle.green, discord.Colour.green()),
    "Smuggling": (discord.ButtonStyle.red, discord.Colour.red()),
    "Social": (discord.ButtonStyle.blurple, discord.Colour.blurple()),
    "Trading": (discord.ButtonStyle.blurple, discord.Colour.blurple()),
    "Transport": (discord.ButtonStyle.blurple, discord.Colour.blurple()),
}

ASK_MSG = '## Hi {member}! "{guild_name}" seems to be missing some information about you - let me help you with that!\n- Please update your linked RSI profile by typing `{prefix}profile username`\n - Use your exact `username` (case insensitive) from https://robertsspaceindustries.com'
MESSAGE_TIMEOUT = 5 * 60
TYPE_BLACKLIST = ["Star", "Lagrange", "JumpPoint", "Lagrange Point", "Naval Station"]
INTERNAL_NAME_BLACKLIST = ["-L5-", "-L4-", "ARC-L3-A"]
SYSTEM = "Stanton"
DEFAULT_OM_RADIUS = 20_000
LOCATIONS = [
    l
    for l in json.load(open("locations.json"))
    if l["Type"] not in TYPE_BLACKLIST
    and not any(i for i in INTERNAL_NAME_BLACKLIST if i in l["InternalName"])
    and l["System"] == SYSTEM
]
LOCATION_TYPES = list(set([l["Type"] for l in LOCATIONS]))
# SENTENCE_TRANSFORMER = SentenceTransformer("sentence-transformers/all-mpnet-base-v2")


class ParsingException(Exception):
    pass


class Activity(pydantic.BaseModel):
    name: str
    url: str

    def button_style(self) -> discord.ButtonStyle:
        return (
            ACTIVITY_LOOKUP[self.name][0]
            if self.name in ACTIVITY_LOOKUP
            else discord.ButtonStyle.blurple
        )

    def colour(self) -> discord.Colour:
        return (
            ACTIVITY_LOOKUP[self.name][1]
            if self.name in ACTIVITY_LOOKUP
            else discord.Colour.blurple()
        )


class OrganisationTag(pydantic.BaseModel):
    name: str
    value: str


class Rank(pydantic.BaseModel):
    rank: int
    name: str


class Organisation(pydantic.BaseModel):
    name: str
    body: str
    history: str
    tags: list[OrganisationTag]
    sid: str
    rank: Rank | None
    icon_url: str
    url: str
    primary_activity: Activity
    secondary_activity: Activity


class MinOrganisation(pydantic.BaseModel):
    name: str
    icon_url: str


class Badge(pydantic.BaseModel):
    name: str
    icon_url: str


class Profile(pydantic.BaseModel):
    handle: str
    bio: str
    badge: Badge
    image_url: str
    citizen_record_id: str
    main_org: Organisation | MinOrganisation | None
    enlisted: datetime.datetime
    location: str | None
    fluency: str | None


class DiscordMarkdownConverter(markdownify.MarkdownConverter):
    def convert_a(self, el: Tag, text: str, convert_as_inline: bool) -> str:
        return text

    def convert_hn(self, n: int, el: Tag, text: str, convert_as_inline: bool) -> str:
        if n > 3:
            return f"**{text}**"
        return str(super().convert_hn(n, el, text, convert_as_inline))


def location_to_str(location: dict) -> str:
    match location["Type"]:
        case "RestStop" | "Refinery Station" | "Naval Station":
            return f'{location["InternalName"].replace("Station", "").strip()} - {location["ObjectContainer"].strip()}'.strip()
        case "Moon" | "Planet":
            return str(location["ObjectContainer"].strip())
        case _:
            return f'{location["InternalName"].strip()} - {location["ObjectContainer"].strip()}'.strip()


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


def find_or_except(
    soup: BeautifulSoup | Tag, key: str | None, value: str, desc: str
) -> Tag:
    if key:
        res = soup.find(attrs={key: value})
    else:
        res = soup.find(value)
    if not isinstance(res, Tag):
        raise ParsingException(
            f'Could not find {key if key else ""} "{value}" on "{desc}"'
        )
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
        raise ParsingException(
            f'Expected at least {index+1} children on "{value}" on "{desc}" but found {len(res)}'
        )
    res_i = res[index]
    if not isinstance(res_i, Tag):
        raise ParsingException(f'Could not find child {index} on "{value}" on "{desc}"')
    return res_i


def key_or_except(
    soup: BeautifulSoup | Tag,
    err: str,
    key: str = "src",
    link: bool = True,
    join: str = "",
) -> str:
    value = soup[key]
    if isinstance(value, list):
        value = join.join(value)
    if not isinstance(value, str):
        raise ParsingException(f'Could not get "{key}" on "{err}"')
    if link and not value.startswith("http"):
        value = "https://robertsspaceindustries.com" + value
    return value


def extract_thumbnail_src(tag: Tag, err: str) -> str:
    thumb_tag = find_or_except(tag, "class", "thumb", err)
    image = find_child_or_except(thumb_tag, "img", 0, "thumb")["src"]
    if not isinstance(image, str):
        raise ParsingException(f'Could not get src from "thumb" img in "{err}"')
    if not image.startswith("http"):
        image = "https://robertsspaceindustries.com" + image
    return image


def soup_to_discord_markdown(soup: Tag) -> str:
    md = str(DiscordMarkdownConverter().convert_soup(soup))
    while "\n\n\n" in md:
        md = md.replace("\n\n\n", "\n\n")
    while ("-" * 69) in md:
        md = md.replace("-" * 69, "-" * 68)
    while ("_" * 69) in md:
        md = md.replace("_" * 69, "_" * 68)
    while ("\_" * 69) in md:
        md = md.replace("\_" * 69, "\_" * 68)

    return md.strip()


def url_to_org(url: str, rank: Rank | None) -> Organisation | None:
    r = httpx.get(url)
    if not r.is_success:
        return None
    soup = BeautifulSoup(r.text, "html.parser")

    img = find_or_except(
        find_or_except(soup, "class", "logo", url), None, "img", f"logo {url}"
    )

    body = soup_to_discord_markdown(find_or_except(soup, "class", "body", url))

    history = soup_to_discord_markdown(
        find_child_or_except(
            find_or_except(soup, "id", "tab-history", url), "div", 0, url
        )
    )

    tags = [
        OrganisationTag(
            name=key_or_except(c, f"li {url}", key="class", link=False).capitalize(),
            value=c.text.strip(),
        )
        for c in find_or_except(soup, "class", "tags", url).find_all("li")
    ]
    primary_activity_tag = find_or_except(
        find_or_except(soup, "class", "primary", url), None, "img", f"primary {url}"
    )
    primary_activity_name = key_or_except(
        primary_activity_tag, f"img primary {url}", key="alt", link=False
    )
    primary_activity = Activity(
        name=primary_activity_name,
        url=key_or_except(primary_activity_tag, f"img primary {url}"),
    )
    secondary_activity_tag = find_or_except(
        find_or_except(soup, "class", "secondary", url), None, "img", f"secondary {url}"
    )
    secondary_activity_name = key_or_except(
        secondary_activity_tag, f"img secondary {url}", key="alt", link=False
    )
    secondary_activity = Activity(
        name=secondary_activity_name,
        url=key_or_except(secondary_activity_tag, f"img secondary {url}"),
    )

    h1 = find_or_except(soup, None, "h1", f"h1 {url}")

    return Organisation(
        name=h1.text.rsplit("/", 1)[0].strip(),
        body=body,
        history=history,
        tags=tags,
        sid=find_child_or_except(h1, "span", 0, f"span h1 {url}").text.strip(),
        rank=rank,
        icon_url=key_or_except(img, f"img {url}"),
        url=url,
        primary_activity=primary_activity,
        secondary_activity=secondary_activity,
    )


def extract_org_info(org_tag: Tag, err: str) -> Organisation | MinOrganisation | None:
    try:
        thumb = find_or_except(org_tag, "class", "thumb", err)
    except ParsingException:
        return None

    try:
        url = key_or_except(
            find_or_except(thumb, None, "a", f"thumb {err}"),
            f"a thumb {err}",
            key="href",
        )
    except ParsingException:
        return MinOrganisation(
            name="[REDACTED]",
            icon_url=key_or_except(
                find_or_except(thumb, None, "img", f"img thumb {err}"),
                f"img thumb {err}",
            ),
        )

    info = find_or_except(org_tag, "class", "info", err)
    ranking = find_or_except(org_tag, "class", "ranking", f"info {err}")
    return url_to_org(
        url,
        Rank(
            rank=len(ranking.findChildren(attrs={"class": "active"}, recursive=False)),
            name=find_child_or_except(
                info, "strong", 1, f"info {err}", recursive=True
            ).text.strip(),
        ),
    )


DESC_TOO_LONG = "...\n\n`[DESCRIPTION TOO LONG]`\n"
DESC_MAX_LEN = 4096 - len(DESC_TOO_LONG)


def org_to_embed(org: Organisation) -> discord.Embed:
    description = f"{org.body}\n# History:\n{org.history}"
    if len(description) > DESC_MAX_LEN:
        description = description[:DESC_MAX_LEN] + DESC_TOO_LONG

    org_embed = discord.Embed(
        title=org.name,
        url=org.url,
        colour=org.primary_activity.colour(),
        description=description,
    )
    org_embed.set_author(
        name=f"Primary activity: {org.primary_activity.name}",
        icon_url=org.primary_activity.url,
    )
    org_embed.set_image(url=org.icon_url)
    org_embed.add_field(name="SID", value=org.sid)
    for t in org.tags:
        org_embed.add_field(name=t.name, value=t.value)
    if org.rank:
        org_embed.add_field(
            name="Member Rank", value=f"{org.rank.name} ({org.rank.rank}/5)"
        )
    org_embed.set_footer(
        text=f"Secondary activity: {org.secondary_activity.name}",
        icon_url=org.secondary_activity.url,
    )
    return org_embed


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


def orgs_lookup(url: str) -> int | list[Organisation] | MinOrganisation:
    r = httpx.get(url + "/organizations")

    if not r.is_success:
        return r.status_code

    soup = BeautifulSoup(r.text, "html.parser")

    main_org_tag = find_or_except(soup, "class", "main", "page")
    left_col_tag = find_or_except(main_org_tag, "class", "left-col", "main org")

    main_org = extract_org_info(left_col_tag, "main org")
    try:
        affiliation_orgs = [
            extract_org_info(c, "main org")
            for c in find_or_except(soup, "class", "affiliation", "page").children
            if isinstance(c, Tag)
        ]
    except ParsingException as e:
        affiliation_orgs = []

    if isinstance(main_org, MinOrganisation):
        return main_org
    else:
        return [o for o in [main_org] + affiliation_orgs if isinstance(o, Organisation)]


def profile_to_embed(profile: Profile) -> discord.Embed:
    embed = discord.Embed(
        title=profile.handle,
        url=RSI_BASE_URL + urllib.parse.quote(profile.handle),
        description=profile.bio,
        timestamp=profile.enlisted,
    )

    embed.set_footer(
        text=f"{profile.badge.name} │ Enlisted", icon_url=profile.badge.icon_url
    )

    embed.set_image(url=profile.image_url)
    if profile.citizen_record_id != "n/a":
        embed.add_field(name=f"UEE Citizen Record", value=profile.citizen_record_id)

    if profile.location:
        embed.add_field(name="Location", value=profile.location)

    if profile.fluency:
        embed.add_field(name="Fluency", value=profile.fluency)

    if profile.main_org:
        if isinstance(profile.main_org, Organisation):
            embed.colour = profile.main_org.primary_activity.colour()
            embed.set_thumbnail(url=profile.main_org.primary_activity.url)
            embed.add_field(
                name="Primary activity", value=profile.main_org.primary_activity.name
            )
            embed.add_field(
                name="Secondary activity",
                value=profile.main_org.secondary_activity.name,
            )

        embed.set_author(
            name=f"Main Org: {profile.main_org.name}",
            url=profile.main_org.url
            if isinstance(profile.main_org, Organisation)
            else None,
            icon_url=profile.main_org.icon_url,
        )

    return embed


def extract_profile_info(url: str) -> int | Profile:
    r = httpx.get(url)

    if not r.is_success:
        return r.status_code

    soup = BeautifulSoup(r.text, "html.parser")

    public_profile = find_or_except(soup, "id", "public-profile", "page")

    # Extract user "Handle name"
    info_tag = find_or_except(public_profile, "class", "info", "public-profile")
    handle_parent_tag = find_child_or_except(info_tag, "p", 1, "info")
    handle = find_or_except(
        handle_parent_tag, None, "strong", "handle parent"
    ).text.strip()

    # Extract user badge
    badge_parent_tag = find_child_or_except(info_tag, "p", 2, "info")
    badge_icon_url = key_or_except(
        find_or_except(badge_parent_tag, None, "img", "info badge img"),
        "info badge img",
    )
    badge_text = find_child_or_except(
        badge_parent_tag, "span", 1, "info badge text"
    ).text.strip()

    # Extract profile image
    image_url = extract_thumbnail_src(public_profile, "public-profile")

    # Extract "UEE Citizen Record" id
    citizen_record_id = find_child_or_except(
        find_or_except(public_profile, "class", "citizen-record", "public-profile"),
        "strong",
        0,
        "citizen-record",
    ).text.strip()

    # Extract main org
    main_org = extract_org_info(
        find_or_except(public_profile, "class", "main-org", "public-profile"),
        "main-org",
    )

    # Extract bio
    bio_tag = public_profile.find(attrs={"class": "bio"})
    bio = ""
    if isinstance(bio_tag, Tag):
        bio_body_tag = bio_tag.find("div")
        if isinstance(bio_body_tag, Tag):
            bio = bio_body_tag.text.strip()

    # Extract enlisted, localtion, fluency
    left_col = public_profile.find_all(attrs={"class": "left-col"})[-1]
    enlisted = datetime.datetime.now()
    location = None
    fluency = None
    for i, entry in enumerate(left_col.find_all(attrs={"class": "entry"})):
        label = find_or_except(
            entry, "class", "label", f"entry-{i} left-col public-profile"
        ).text.strip()
        value = (
            find_or_except(
                entry, "class", "value", f"entry-{i} left-col public-profile"
            )
            .text.replace("\n", "")
            .strip()
        )
        while " ," in value:
            value = value.replace(" ,", ",")
        if label == "Enlisted":
            enlisted = datetime.datetime.strptime(value, "%b %d, %Y")
        elif label == "Location":
            location = value
        elif label == "Fluency":
            fluency = value

    return Profile(
        handle=handle,
        bio=bio,
        badge=Badge(name=badge_text, icon_url=badge_icon_url),
        image_url=image_url,
        citizen_record_id=citizen_record_id,
        main_org=main_org,
        enlisted=enlisted,
        location=location,
        fluency=fluency,
    )


def get_members_without_rsi_profiles(guild: discord.Guild) -> list[discord.Member]:
    return [
        m
        for m in guild.members
        if not m.bot and not USERS_COLLECTION.find_one({"_id": m.id})
    ]


def get_members_with_rsi_profiles(
    guild: discord.Guild,
) -> list[tuple[discord.Member, dict]]:
    res: list[tuple[discord.Member, dict]] = []
    for m in guild.members:
        if not m.bot and (dbm := USERS_COLLECTION.find_one({"_id": m.id})):
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


# def search(
#     collection: list, search_str: str, to_str: typing.Callable[[typing.Any], str]
# ) -> typing.Any:
#     search_str_embedding = SENTENCE_TRANSFORMER.encode(
#         search_str, convert_to_tensor=True
#     )

#     closest = 0
#     for m in collection:
#         if (
#             similarity := util.pytorch_cos_sim(
#                 SENTENCE_TRANSFORMER.encode(to_str(m), convert_to_tensor=True),
#                 search_str_embedding,
#             )
#         ) > closest:
#             closest = similarity
#             document = m

#     return document


def point_point_dist(
    a: numpy.typing.NDArray, b: numpy.typing.NDArray
) -> numpy.floating:
    return numpy.linalg.norm(a - b)


def line_point_dist(
    line: tuple[numpy.typing.NDArray, numpy.typing.NDArray], point: numpy.typing.NDArray
) -> numpy.floating:
    p1, p2 = line
    return numpy.linalg.norm(numpy.cross(p2 - p1, p1 - point)) / numpy.linalg.norm(
        p2 - p1
    )


def closest_point(
    line: tuple[numpy.typing.NDArray, numpy.typing.NDArray], point: numpy.typing.NDArray
) -> numpy.typing.NDArray:
    p1, p2 = line
    x1, y1, z1 = p1
    x2, y2, z2 = p2
    x3, y3, z3 = point
    dx, dy, dz = x2 - x1, y2 - y1, z2 - z1
    det = dx * dx + dy * dy + dz * dz
    a = (dx * (x3 - x1) + dy * (y3 - y1) + dz * (z3 - z1)) / det
    return numpy.array([x1 + a * dx, y1 + a * dy, z1 + a * dz])


def perpendicular_unit_vector(v: numpy.typing.NDArray) -> numpy.typing.NDArray:
    if v[0] == 0 and v[1] == 0:
        if v[2] == 0:
            # v is Vector(0, 0, 0)
            raise ValueError("zero vector")

        # v is Vector(0, 0, v.z)
        return numpy.array([0, 1, 0])

    res_v = numpy.array([-v[1], v[0], 0])
    return numpy.array(res_v / numpy.linalg.norm(res_v))


def pretty_print_dist(number: float | numpy.floating) -> str:
    if number > 1_000:
        return f"{number/1000:,.1f} km"

    return f"{number:,.1f} m"


def is_left_of(
    line: tuple[numpy.typing.NDArray, numpy.typing.NDArray], point: numpy.typing.NDArray
) -> bool:
    aX = line[0][0]
    aY = line[0][1]
    bX = line[1][0]
    bY = line[1][1]
    cX = point[0]
    cY = point[1]

    val = (bX - aX) * (cY - aY) - (bY - aY) * (cX - aX)
    if val >= 0:
        return True
    else:
        return False


class SnareCheckModal(discord.ui.Modal):
    def __init__(
        self,
        centerline: tuple[numpy.typing.NDArray, numpy.typing.NDArray],
        hypotenuse: tuple[numpy.typing.NDArray, numpy.typing.NDArray],
        physics_range: float,
        optimal_range: float,
        title: str,
    ) -> None:
        self.centerline = centerline
        self.hypotenuse = hypotenuse
        self.physics_range = physics_range
        self.optimal_range = optimal_range
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
            destination_dist = point_point_dist(location, self.centerline[1])
            if destination_dist < self.physics_range:
                await interaction.response.send_message(
                    "# ❌ WITHIN PHYSICS GRID!\nPlease reset and try again",
                    ephemeral=True,
                    delete_after=MESSAGE_TIMEOUT,
                )
                return

            centerline_dist = line_point_dist(self.centerline, location)
            max_dist = 20_000 - line_point_dist(
                self.hypotenuse, closest_point(self.centerline, location)
            )

            if centerline_dist <= max_dist:
                colour = discord.Colour.green()
                description = "# ✅ Within snare cone!"
            else:
                colour = discord.Colour.red()
                description = f"# ❌ {pretty_print_dist(centerline_dist- max_dist)} outside snare cone!"

            closest_centerline_point = closest_point(self.centerline, location)
            z_mag = abs(closest_centerline_point[2] - location[2])
            z_dir = "up" if closest_centerline_point[2] > 0 else "down"
            s_mag = numpy.linalg.norm((closest_centerline_point - location)[:2])
            s_dir = "right" if is_left_of(self.centerline, location) else "left"
            f_mag = (
                point_point_dist(closest_centerline_point, self.centerline[1])
                - self.optimal_range
            )
            f_dir = "forward" if f_mag > 0 else "backwards"

            description += (
                "\n## Route to centerline:\nFacing your destination and rotated so up for your ship is Stanton north:"
                + (
                    f"\n- Travel {pretty_print_dist(abs(z_mag))} {z_dir}"
                    if abs(z_mag) > 1
                    else ""
                )
                + (
                    f"\n- Travel {pretty_print_dist(abs(s_mag))} {s_dir}"
                    if abs(s_mag) > 1
                    else ""
                )
                + (
                    f"\n### Final travel to optimal pullout:\n- Travel {pretty_print_dist(abs(f_mag))} {f_dir}"
                    if abs(f_mag) > 1
                    else ""
                )
            )

            closest_edge = min(
                20_000 - line_point_dist(self.hypotenuse, location),
                destination_dist - self.physics_range,
            )
            location_score = (
                closest_edge / (self.optimal_range - self.physics_range) * 10
            )

            embed = discord.Embed(
                title="Snare check", description=description, colour=colour
            )
            embed.add_field(
                name="Distance to centerline",
                value=pretty_print_dist(centerline_dist),
            )
            embed.add_field(
                name="Distance to Physics Grid",
                value=pretty_print_dist(destination_dist - self.physics_range),
            )
            embed.add_field(name="Location score", value=f"{location_score:.1f}/10")
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
    def __init__(
        self,
        centerline: tuple[numpy.typing.NDArray, numpy.typing.NDArray],
        hypotenuse: tuple[numpy.typing.NDArray, numpy.typing.NDArray],
        physics_range: float,
        optimal_range: float,
        label: str,
        style: discord.ButtonStyle,
    ):
        self.centerline = centerline
        self.hypotenuse = hypotenuse
        self.physics_range = physics_range
        self.optimal_range = optimal_range
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            SnareCheckModal(
                self.centerline,
                self.hypotenuse,
                self.physics_range,
                self.optimal_range,
                "Check your location",
            )
        )


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
    source_obj = next(l for l in LOCATIONS if location_to_str(l) == source.name)
    destination_obj = next(
        l for l in LOCATIONS if location_to_str(l) == destination.name
    )

    # The travel source represented as a 3D coordinate
    source_point = numpy.array(
        [source_obj["XCoord"], source_obj["YCoord"], source_obj["ZCoord"]]
    )

    # The travel distination represented as a 3D coordinate
    destination_point = numpy.array(
        [
            destination_obj["XCoord"],
            destination_obj["YCoord"],
            destination_obj["ZCoord"],
        ]
    )

    # Represents the centerline as a vector
    # with origin in the source point
    centerline_vector = source_point - destination_point

    # Calculate the point on the centerline
    # where you enter the physics grid of the destination
    point_of_physics = (
        destination_point
        + centerline_vector
        / point_point_dist(destination_point, source_point)
        * destination_obj["GRIDRadius"]
    )

    # Orbital Markers are generally the furthest away from the center
    # of a celestial body anybody traveling from said body will travel before
    # jumping towards a new target
    om_radius = source_obj["OrbitalMarkerRadius"] or DEFAULT_OM_RADIUS

    # All OM points orbit at the same height - this variable represents
    # an imaginary abitrary OM point placed perpendicular on the centerline
    # i.e. the worst case in order to catch a potential traveller
    puv = perpendicular_unit_vector(destination_point - source_point)
    assert numpy.linalg.norm(puv) == 1.0
    arbitrary_om_point = source_point + puv * om_radius

    # A linalg representation of an arbitrary worst case travel line
    hyp = (arbitrary_om_point, destination_point)

    # Approximates the point (down to 0.01m) closest to the source point
    # on the centerline which is less than 20,000m (snare range) from
    # the worst case travel line
    # i.e. the earliest possible point to catch everyone
    sp = source_point
    dp = point_of_physics
    while point_point_dist(sp, dp) > 0.01:
        h = sp + (dp - sp) / 2
        hd = line_point_dist(hyp, h)
        if hd < 20_000:
            dp = h
        else:
            sp = h
    min_pullout = h
    min_pullout_dist = point_point_dist(min_pullout, destination_point)

    # Approximates the point (down to 0.01m) where a ship would have to travel
    # the furthest to escape the cone in which it would still catch everyone
    sp = min_pullout
    dp = point_of_physics
    while point_point_dist(sp, dp) > 0.01:
        h = sp + (dp - sp) / 2
        hd = 20_000 - line_point_dist(hyp, h)
        hpp = point_point_dist(h, point_of_physics)
        if hpp > hd:
            sp = h
        else:
            dp = h
    optimal_pullout = h
    optimal_pullout_dist = point_point_dist(optimal_pullout, destination_point)

    # Calculate the coverage
    # i.e. at the clostest point possible to the destination (just before the physics grid)
    # how much of the required area to catch everyone does a 20,000m radius cover
    point_of_physics_radius = line_point_dist(hyp, point_of_physics)
    point_of_physics_area = point_of_physics_radius**2 * math.pi
    snare_coverage = 20_000**2 * math.pi
    coverage = snare_coverage / point_of_physics_area

    embed = discord.Embed(
        title=f"Full Coverage Snare Plan",
        description=f"`{location_to_str(source_obj)} -> {location_to_str(destination_obj)}`"
        + (
            f"\n## ❌ Only {coverage*100:.1f}% coverage possible on this route!\nJust get as close to the Physics grid (**without passing into it**) on the centerline as you dare. The better you do the more you'll catch."
            if coverage < 1
            else f"\n## ✅ Full route coverage possible\nAs always, try to get as close to the centerline as possible.\n\nAt `{pretty_print_dist(optimal_pullout_dist)}` from `{location_to_str(destination_obj)}` you'll have `{pretty_print_dist(20_000 - line_point_dist(hyp, optimal_pullout))}` of leeway to be off the centerline and be `{pretty_print_dist(point_point_dist(optimal_pullout, point_of_physics))}` away from the physics grid of `{location_to_str(destination_obj)}`. This is therefore the location that gives you the most leeway in all directions."
        ),
        colour=discord.Colour.red() if coverage < 1 else discord.Colour.green(),
    )
    view = discord.ui.View()

    embed.add_field(
        name="Centerline length",
        value=pretty_print_dist(point_point_dist(source_point, destination_point)),
    )
    embed.add_field(
        name=f'"{location_to_str(source_obj)}" physics grid range',
        value=pretty_print_dist(source_obj["GRIDRadius"]),
    )
    embed.add_field(
        name=f'"{location_to_str(destination_obj)}" physics grid range',
        value=pretty_print_dist(destination_obj["GRIDRadius"]),
    )
    if coverage >= 1:
        embed.add_field(
            name="Earliest pullout", value=pretty_print_dist(min_pullout_dist)
        )
        embed.add_field(
            name="Optimal pullout", value=pretty_print_dist(optimal_pullout_dist)
        )
        view.add_item(
            SnareCheckButton(
                (source_point, destination_point),
                hyp,
                destination_obj["GRIDRadius"],
                float(optimal_pullout_dist),
                "Check my location!",
                discord.ButtonStyle.green,
            )
        )

    await interaction.followup.send(embed=embed, view=view)


@tree.command(name="profile", description=PROFILE_DESCRIPTION)
async def profile(interaction: discord.Interaction, username: str) -> None:
    url = RSI_BASE_URL + username
    try:
        profile = extract_profile_info(url)
    except ParsingException as e:
        await interaction.response.send_message(
            f"An error happened, please contact an admin and send them the following: {url} | {e}",
            delete_after=MESSAGE_TIMEOUT,
        )

    if isinstance(profile, Profile):
        db_user = {
            "_id": interaction.user.id,
            "url": RSI_BASE_URL + urllib.parse.quote(profile.handle),
            "nick": profile.handle,
        }
        try:
            USERS_COLLECTION.insert_one(db_user)
        except pymongo.errors.DuplicateKeyError:
            USERS_COLLECTION.replace_one({"_id": db_user["_id"]}, db_user)

        if isinstance(interaction.user, discord.Member):
            try:
                await interaction.user.edit(nick=get_desired_nick(interaction.user))
            except discord.errors.Forbidden as e:
                logger.warning(
                    f'Cannot change nickname for "{interaction.user.mention}": {e}'
                )

        await interaction.response.send_message(
            f"Updated linked RSI profile for user {interaction.user.mention} ✅\nRemember you can always update your profile with `{PREFIX}profile username`",
            embed=profile_to_embed(profile),
            delete_after=MESSAGE_TIMEOUT,
        )
    else:
        await interaction.response.send_message(
            f'Could not find "{username}", please type your exact username (case insensitive) from https://robertsspaceindustries.com',
            delete_after=MESSAGE_TIMEOUT,
        )


@tree.command(
    name="whois",
    description=WHOIS_DESCRIPTION,
)
async def whois(interaction: discord.Interaction, member: discord.Member) -> None:
    if member == client.user:
        await interaction.response.send_message(embed=get_bot_embed())
        return

    db_user = USERS_COLLECTION.find_one({"_id": member.id})
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
                await interaction.response.send_message(
                    f'User {member.mention} has invalid URL ({db_user["url"]}) please update immediately via `{PREFIX}profile username`',
                    delete_after=MESSAGE_TIMEOUT,
                )
        except ParsingException as e:
            await interaction.response.send_message(
                f"An error happened, please contact an admin and send them the following: {db_user['url']} | {e}",
                delete_after=MESSAGE_TIMEOUT,
            )
            return

        if isinstance(profile, Profile):
            await interaction.response.send_message(
                embed=profile_to_embed(profile), view=view, delete_after=MESSAGE_TIMEOUT
            )
            return
        else:
            await interaction.response.send_message(
                f'User {member.mention} has invalid URL ({db_user["url"]}) please update immediately via `{PREFIX}profile username`',
                delete_after=MESSAGE_TIMEOUT,
            )
    else:
        await interaction.response.send_message(
            f"{member.mention} has not yet linked their RSI profile, please do so via `{PREFIX}profile username`",
            delete_after=MESSAGE_TIMEOUT,
        )


@tree.command(
    name="lookup",
    description=LOOKUP_DESCRIPTION,
)
async def lookup(interaction: discord.Interaction, username: str) -> None:
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
        await interaction.response.send_message(
            f"An error happened, please contact an admin and send them the following: {url} | {e}",
            delete_after=MESSAGE_TIMEOUT,
        )
        return

    if isinstance(profile, Profile):
        await interaction.response.send_message(
            embed=profile_to_embed(profile), view=view, delete_after=MESSAGE_TIMEOUT
        )
    else:
        await interaction.response.send_message(
            f'No profile found on "{url}"', delete_after=MESSAGE_TIMEOUT
        )


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


def normalize_string(input_string: str) -> str:
    return "".join(
        c for c in input_string if c in string.ascii_letters + string.whitespace
    ).strip()


# ======== ADMIN COMMANDS ========
CURRENT_OPS = {}
AUDIO_DIR = pathlib.Path("audio")
AUDIO_DIR.mkdir(exist_ok=True)
CHUNK_SIZE = 1024


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

    GUILD_DB = mongodb_client[str(interaction.guild.id)]
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

    GUILD_DB = mongodb_client[str(interaction.guild.id)]
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

    GUILD_DB = mongodb_client[str(interaction.guild.id)]
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

    GUILD_DB = mongodb_client[str(interaction.guild.id)]
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

    try:
        CONFIG_COLLECTION.insert_one({"_id": "rsiorg", "sid": sid})
        await interaction.response.send_message(
            embed=embed, delete_after=MESSAGE_TIMEOUT
        )
    except pymongo.errors.DuplicateKeyError:
        CONFIG_COLLECTION.replace_one({"_id": "rsiorg"}, {"sid": sid})
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

    db_role = {"_id": role.id, "icon": icon, "priority": priority, "rsirank": rsirank}
    try:
        ROLES_COLLECTION.insert_one(db_role)
        await interaction.response.send_message(
            f"Added role: {role.mention} (`{priority} | {rsirank}/5 | {icon}`)",
            delete_after=MESSAGE_TIMEOUT,
        )
    except pymongo.errors.DuplicateKeyError:
        ROLES_COLLECTION.replace_one({"_id": role.id}, db_role)
        await interaction.response.send_message(
            f"Updated role: {role.mention} (`{priority} | {rsirank}/5 | {icon}`)",
            delete_after=MESSAGE_TIMEOUT,
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

    db_role = {"_id": role.id, "icon": icon}
    try:
        WINGS_COLLECTION.insert_one(db_role)
        await interaction.response.send_message(
            f"Added wing: {role.mention} - {icon}", delete_after=MESSAGE_TIMEOUT
        )
    except pymongo.errors.DuplicateKeyError:
        WINGS_COLLECTION.replace_one({"_id": role.id}, db_role)
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

    res = ROLES_COLLECTION.delete_one({"_id": role.id})

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

    res = WINGS_COLLECTION.delete_one({"_id": role.id})

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

    roles: list[dict] = list(
        sorted(ROLES_COLLECTION.find(), key=lambda r: r["priority"])
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
    if not await check_admin(interaction):
        return

    wings: list[dict] = list(WINGS_COLLECTION.find())
    if not wings:
        await interaction.response.send_message(
            "No wings added yet", delete_after=MESSAGE_TIMEOUT
        )
        return

    await interaction.response.send_message(
        "Wings:\n" + "\n".join(f'{wing["icon"]} - <@&{wing["_id"]}>' for wing in wings),
        delete_after=MESSAGE_TIMEOUT,
    )


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
        for i, member in enumerate(self.missing_members):
            await msg.edit(content=f"Asked {i}/{len(self.missing_members)} members...")
            await member.send(
                ASK_MSG.format(
                    member=member.mention,
                    guild_name=interaction.guild.name,
                    prefix=PREFIX,
                )
            )

        await msg.edit(
            content=f"✅ Asked all {len(self.missing_members)} members with missing RSI profiles"
        )


class GenericShowEmbedButton(discord.ui.Button):
    def __init__(
        self,
        embed: discord.Embed,
        view: discord.ui.View,
        label: str,
        style: discord.ButtonStyle,
    ):
        self.input_embed = embed
        self.input_view = view
        super().__init__(label=label, style=style)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            delete_after=MESSAGE_TIMEOUT,
            embed=self.input_embed,
            view=self.input_view,
            ephemeral=True,
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
    wrong_nicks = get_wrong_nicks(interaction.guild)
    total_members = len([m for m in interaction.guild.members if not m.bot])

    description = f" {total_members - len(missing_members)}/{total_members} members has linked their RSI profiles"

    if not missing_members:
        description = f"✅{description}\n"
    else:
        description = f"❌{description}\n"

        missing_members_view = discord.ui.View()
        missing_members_view.add_item(
            AskAllButton(
                missing_members=missing_members,
                label="Ask all members missing",
                style=discord.ButtonStyle.red,
            ),
        )
        view.add_item(
            GenericShowEmbedButton(
                discord.Embed(
                    title="Members missing RSI profiles",
                    description="\n".join(f"- {m.mention}" for m in missing_members)
                    + f"\nUse `{PREFIX}ask` to ask a specific member to update/add their linked RSI profile\n\n",
                ),
                missing_members_view,
                label="Show members missing RSI Profiles",
                style=discord.ButtonStyle.red,
            )
        )

    total_members -= len(missing_members)
    if not wrong_nicks:
        description += f"✅ {total_members - len(wrong_nicks)}/{total_members} members with RSI profiles have correct nicknames"
    else:
        description += f"❌ {total_members - len(wrong_nicks)}/{total_members} members with RSI profiles have correct nicknames"

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
    name="ask",
    description="One member of the Guild to update their linked RSI profile",
)
async def ask(interaction: discord.Interaction, member: discord.Member) -> None:
    if not await check_admin(interaction):
        return

    assert interaction.guild

    await member.send(
        ASK_MSG.format(
            member=member.mention, guild_name=interaction.guild.name, prefix=PREFIX
        )
    )
    await interaction.response.send_message(
        delete_after=MESSAGE_TIMEOUT,
        content=f"✅ {member.mention} has been asked to update their linked RSI profile",
    )


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

    try:
        CONFIG_COLLECTION.insert_one({"_id": "adminchal", "channel": channel.id})
        await interaction.response.send_message(
            f"Added admin channel {channel.mention}", delete_after=MESSAGE_TIMEOUT
        )
    except pymongo.errors.DuplicateKeyError:
        CONFIG_COLLECTION.replace_one({"_id": "adminchal"}, {"channel": channel.id})
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

    try:
        CONFIG_COLLECTION.insert_one({"_id": "startrole", "role": role.id})
        await interaction.response.send_message(
            f"Added starting role {role.mention}", delete_after=MESSAGE_TIMEOUT
        )
    except pymongo.errors.DuplicateKeyError:
        CONFIG_COLLECTION.replace_one({"_id": "startrole"}, {"role": role.id})
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

    try:
        JOIN_COLLECTION.insert_one({"_id": role.id, "text": text})
        await interaction.response.send_message(
            f"Added text for join role {role.mention}", delete_after=MESSAGE_TIMEOUT
        )
    except pymongo.errors.DuplicateKeyError:
        JOIN_COLLECTION.replace_one({"_id": role.id}, {"text": text})
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

    join_texts: list[dict] = list(JOIN_COLLECTION.find())
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
        TRIGGER_COLLECTION.insert_one({"_id": role.id})
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

    trigger_roles: list[dict] = list(TRIGGER_COLLECTION.find())
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
    ) and not any(
        t for t in ROLES_COLLECTION.find() if t["_id"] in [a.id for a in after.roles]
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
                    description=f"What we know about them:\n"
                    + "\n".join(
                        f'- {j["text"]}'
                        for j in JOIN_COLLECTION.find()
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
        GUILD_DB = mongodb_client[str(member.guild.id)]
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
