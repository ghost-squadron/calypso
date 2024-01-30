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
