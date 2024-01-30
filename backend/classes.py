import datetime

import discord
import markdownify  # type: ignore
import pydantic
from bs4 import Tag
from constants import ACTIVITY_LOOKUP


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
