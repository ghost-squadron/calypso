# import urllib

# import discord
# import httpx
# from bs4 import BeautifulSoup, Tag
# from classes import (Activity, DiscordMarkdownConverter, MinOrganisation,
#                      Organisation, OrganisationTag, ParsingException, Profile,
#                      Rank)
# from constants import *
# from html_parsing import find_or_except, key_or_except

# DESC_TOO_LONG = "...\n\n`[DESCRIPTION TOO LONG]`\n"
# DESC_MAX_LEN = 4096 - len(DESC_TOO_LONG)


# def format_markdown(markdown: str) -> str:
#     replacements = {
#         "\n\n\n": "\n\n",
#         "-" * 69: "-" * 68,
#         "_" * 69: "_" * 68,
#         "\_" * 69: "\_" * 68,
#     }
#     for old, new in replacements.items():
#         while old in markdown:
#             markdown = markdown.replace(old, new)
#     return markdown.strip()


# def soup_to_discord_markdown(soup: Tag) -> str:
#     converter = DiscordMarkdownConverter()
#     md = str(converter.convert_soup(soup))
#     return format_markdown(md)


# def extract_image_info(tag: Tag, context: str) -> str:
#     image_tag = find_or_except(tag, None, "img", context)
#     return key_or_except(image_tag, context)


# def extract_text_info(tag: Tag, selector: str, context: str, err: str) -> str:
#     child = find_or_except(tag, selector, context, err)
#     return child.text.strip()


# def extract_thumbnail_src(tag: Tag, err: str) -> str:
#     thumb_tag = find_or_except(tag, "class", "thumb", err)
#     image = key_or_except(thumb_tag, "thumb", "src")
#     return image


# def get_soup_from_url(url: str) -> BeautifulSoup:
#     r = httpx.get(url)
#     if not r.is_success:
#         raise ParsingException(f"Could not retrieve content from {url}")
#     return BeautifulSoup(r.text, "html.parser")


# def extract_multiple_tags(
#     tag: Tag, class_name: str, context: str
# ) -> list[OrganisationTag]:
#     container = find_or_except(tag, "class", class_name, context)
#     return [
#         OrganisationTag(
#             name=extract_text_info(c, "class", context).capitalize(),
#             value=c.text.strip(),
#         )
#         for c in container.find_all("li")
#     ]


# def extract_activity(tag: Tag, context: str) -> Activity:
#     activity_name = extract_text_info(tag, "alt", context)
#     activity_url = extract_image_info(tag, context)
#     return Activity(name=activity_name, url=activity_url)


# def extract_org_details(
#     soup: BeautifulSoup, url: str, rank: Rank | None
# ) -> Organisation:
#     img = extract_image_info(soup, f"logo {url}")
#     body = soup_to_discord_markdown(extract_text_info(soup, "class", "body", url))
#     history = soup_to_discord_markdown(
#         extract_text_info(soup, "id", "tab-history", url)
#     )
#     tags = extract_multiple_tags(soup, "tags", url)

#     primary_activity = extract_activity(
#         find_or_except(soup, "class", "primary", url), f"primary {url}"
#     )
#     secondary_activity = extract_activity(
#         find_or_except(soup, "class", "secondary", url), f"secondary {url}"
#     )
#     h1 = find_or_except(soup, None, "h1", f"h1 {url}")

#     return Organisation(
#         name=extract_text_info(h1, "rsplit('/', 1)[0]", url),
#         body=body,
#         history=history,
#         tags=tags,
#         sid=extract_text_info(
#             find_child_or_except(h1, "span", 0, f"span h1 {url}"), None, url
#         ),
#         rank=rank,
#         icon_url=img,
#         url=url,
#         primary_activity=primary_activity,
#         secondary_activity=secondary_activity,
#     )


# def url_to_org(url: str, rank: Rank | None) -> Organisation | None:
#     try:
#         soup = get_soup_from_url(url)
#         return extract_org_details(soup, url, rank)
#     except ParsingException:
#         return None


# def extract_org_info(org_tag: Tag, err: str) -> Organisation | MinOrganisation | None:
#     try:
#         thumb = find_or_except(org_tag, "class", "thumb", err)
#         info = find_or_except(org_tag, "class", "info", err)
#         ranking_tag = find_or_except(org_tag, "class", "ranking", f"info {err}")
#         url = extract_image_info(
#             find_or_except(thumb, None, "a", f"thumb {err}"), f"a thumb {err}"
#         )
#         rank = Rank(
#             rank=len(
#                 ranking_tag.findChildren(attrs={"class": "active"}, recursive=False)
#             ),
#             name=extract_text_info(info, "strong", f"info {err}"),
#         )
#         return url_to_org(url, rank)
#     except ParsingException:
#         return MinOrganisation(
#             name="[REDACTED]",
#             icon_url=extract_image_info(
#                 find_or_except(thumb, None, "img", f"img thumb {err}"),
#                 f"img thumb {err}",
#             ),
#         )


# # Discord Embed Conversion
# def profile_to_embed(profile: Profile) -> discord.Embed:
#     embed = discord.Embed(
#         title=profile.handle,
#         url=RSI_BASE_URL + urllib.parse.quote(profile.handle),
#         description=profile.bio,
#         timestamp=profile.enlisted,
#     )

#     embed.set_footer(
#         text=f"{profile.badge.name} │ Enlisted", icon_url=profile.badge.icon_url
#     )

#     embed.set_image(url=profile.image_url)
#     if profile.citizen_record_id != "n/a":
#         embed.add_field(name=f"UEE Citizen Record", value=profile.citizen_record_id)

#     if profile.location:
#         embed.add_field(name="Location", value=profile.location)

#     if profile.fluency:
#         embed.add_field(name="Fluency", value=profile.fluency)

#     if profile.main_org:
#         if isinstance(profile.main_org, Organisation):
#             embed.colour = profile.main_org.primary_activity.colour()
#             embed.set_thumbnail(url=profile.main_org.primary_activity.url)
#             embed.add_field(
#                 name="Primary activity", value=profile.main_org.primary_activity.name
#             )
#             embed.add_field(
#                 name="Secondary activity",
#                 value=profile.main_org.secondary_activity.name,
#             )

#         embed.set_author(
#             name=f"Main Org: {profile.main_org.name}",
#             url=profile.main_org.url
#             if isinstance(profile.main_org, Organisation)
#             else None,
#             icon_url=profile.main_org.icon_url,
#         )

#     return embed


# def org_to_embed(org: Organisation) -> discord.Embed:
#     description = f"{org.body}\n# History:\n{org.history}"
#     if len(description) > DESC_MAX_LEN:
#         description = description[:DESC_MAX_LEN] + DESC_TOO_LONG

#     org_embed = discord.Embed(
#         title=org.name,
#         url=org.url,
#         colour=org.primary_activity.colour(),
#         description=description,
#     )
#     org_embed.set_author(
#         name=f"Primary activity: {org.primary_activity.name}",
#         icon_url=org.primary_activity.url,
#     )
#     org_embed.set_image(url=org.icon_url)
#     org_embed.add_field(name="SID", value=org.sid)
#     for t in org.tags:
#         org_embed.add_field(name=t.name, value=t.value)
#     if org.rank:
#         org_embed.add_field(
#             name="Member Rank", value=f"{org.rank.name} ({org.rank.rank}/5)"
#         )
#     org_embed.set_footer(
#         text=f"Secondary activity: {org.secondary_activity.name}",
#         icon_url=org.secondary_activity.url,
#     )
#     return org_embed
