import datetime
import urllib

import discord
import httpx
import loguru
from bs4 import BeautifulSoup, Tag
from classes import (Activity, Badge, MinOrganisation, Organisation,
                     OrganisationTag, ParsingException, Profile, Rank)
from constants import *

DESC_TOO_LONG = "...\n\n`[DESCRIPTION TOO LONG]`\n"
DESC_MAX_LEN = 4096 - len(DESC_TOO_LONG)

import discord
from bs4 import BeautifulSoup, Tag
from classes import DiscordMarkdownConverter, ParsingException


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


def extract_profile_info(url: str) -> int | Profile:
    try:
        r = httpx.get(url)
    except Exception as e:
        loguru.logger.error(f'Could not get user profile at "{url}": {e}')
        return -1

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


# Discord Embed Conversion
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
