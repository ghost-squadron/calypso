import json
import os
import pathlib

import discord
import dotenv

dotenv.load_dotenv()

PREFIX = "/"

DISCORD_API_TOKEN: str = os.environ.get("DISCORD_API_TOKEN", None)
ELEVENLABS_API_KEY: str = os.environ.get("ELEVENLABS_API_KEY", None)
RSI_BASE_URL = "https://robertsspaceindustries.com/citizens/"
MONGODB_DOMAIN = os.environ.get("MONGODB_DOMAIN", default="localhost")

# Command descriptions
PROFILE_DESCRIPTION = "Add/update your linked RSI profile"
WHOIS_DESCRIPTION = "Looks up the RSI profile linked to a specific discord member"
LOOKUP_DESCRIPTION = "Looks up an RSI profile (must be exact match, case insensitive)"
SNARE_DESCRIPTION = "Command for assisting in planning where to set up your snare to *actually* catch everyone"

ASK_MSG = '## Hi {member}! "{guild_name}" seems to be missing some information about you - let me help you with that!\n- Please update your linked RSI profile by typing `{prefix}profile username`\n - Use your exact `username` (case insensitive) from https://robertsspaceindustries.com'
WELCOME_MSG = "# Welcome {member}!\nTo help our dear admins to better get you started I am here to help you link your RSI profile to our Discord.\n\nIt is actually quite simple. Please just run the command `{prefix}profile username` and your're all set!\n\nAnd don't worry, you can always update this again with the same command if you make a mistake."
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

AUDIO_DIR = pathlib.Path("audio")
AUDIO_DIR.mkdir(exist_ok=True)
CHUNK_SIZE = 1024
