import json
import os
import pathlib
import typing

import discord
import dotenv

dotenv.load_dotenv()

PREFIX = "/"

DISCORD_API_TOKEN: str | None = os.environ.get("DISCORD_API_TOKEN", None)
ELEVENLABS_API_KEY: str | None = os.environ.get("ELEVENLABS_API_KEY", None)
RSI_BASE_URL = "https://robertsspaceindustries.com/citizens/"
MONGODB_DOMAIN = os.environ.get("MONGODB_DOMAIN", default="localhost")
TRANSFER_FEE = 0.005

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

VOICE_IDS = {
    "Dorothy": "ThT5KcBeYPX3keUQqHPh",
    "Grace": "oWAxZDx7w5VEj9dCyTzz",
    "Dooley": "5G7PW5XIusZbDc52ep63",
}

COMMODITIES = [
    "AcryliPlex Composite",
    "Agricium",
    "Agricultural Supplies",
    "Altruciatoxin",
    "Aluminum",
    "Astatine",
    "Beryl",
    "Bexalite",
    "Borase",
    "Chlorine",
    "Compboard",
    "Copper",
    "Corundum",
    "Diamond",
    "Diluthermex",
    "Distilled Spirits",
    "E'tam",
    "Extortion",
    "Fluorine",
    "Gold",
    "Hephaestanite",
    "Hydrogen",
    "Iodine",
    "Laranite",
    "Maze",
    "Medical Supplies",
    "Neon",
    "Processed Food",
    "Quantainium",
    "Quartz",
    "Red Festival Envelope",
    "Revenant Tree Pollen",
    "RMC (Recycled Material Composite)",
    "Scrap",
    "SLAM",
    "Stims",
    "Taranite",
    "Titanium",
    "Tungsten",
    "Waste",
    "WiDoW",
    "Zeta-Prolanide",
]

Ship = typing.Literal[
    "Aegis Hammerhead",
    "Aegis Reclaimer",
    "Anvil Carrack",
    "Anvil Valkyrie",
    "Argo MOLE",
    "Argo RAFT",
    "CNOU Nomad",
    "Crusader A2 Hercules Starlifter",
    "Crusader C1 Spirit",
    "Crusader C2 Hercules Starlifter",
    "Crusader M2 Hercules Starlifter",
    "Crusader Mercury Star Runner",
    "Drake Caterpillar",
    "Drake Corsair",
    "Drake Cutlass",
    "Drake Vulture",
    "MISC Freelancer",
    "MISC Hull A",
    "MISC Hull C",
    "MISC Starfarer",
    "Origin 400i",
    "Origin 600i Touring",
    "Origin 600i",
    "Origin 890 Jump",
    "RSI Constellation",
]
