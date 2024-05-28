import asyncio
import os
import time
import typing
from typing import Union

import dotenv
import httpx
import pymongo
from fastapi import FastAPI, HTTPException

dotenv.load_dotenv()

MONGODB_DOMAIN = os.environ.get("MONGODB_DOMAIN", default="localhost")
DISCORD_API_TOKEN: str | None = os.environ.get("DISCORD_API_TOKEN", None)

app = FastAPI()

mongo: pymongo.MongoClient = pymongo.MongoClient(MONGODB_DOMAIN, 27017)


@app.get("/donations/{guild_id}/{donation_index}")
async def read_item(guild_id: int, donation_index: int) -> str:
    COLLECTION = mongo[str(guild_id)]["donations"]
    donations = list(sorted(COLLECTION.find(), key=lambda d: d["_id"]))
    if donation_index >= len(donations):
        raise HTTPException(status_code=404, detail="Item not found")

    collectors = []
    for c in donations[donation_index]["collectors"]:
        while "retry_after" in (
            res := httpx.get(
                f"https://discord.com/api/v9/users/{c}",
                headers={"Authorization": f"Bot {DISCORD_API_TOKEN}"},
            ).json()
        ):
            await asyncio.sleep(res["retry_after"])
        collectors.append(res["username"] if "username" in res else res["id"])

    return (
        f'{donations[donation_index]["_id"].isoformat()}Z;'
        + ", ".join(
            f"{b['amount']} SCU of {b['commodity']}"
            for b in donations[donation_index]["booty"]
        )
        + f';{sum(b["profit"] for b in donations[donation_index]["booty"])}'
        + f';{", ".join(collectors) or "-"}'
        + f';{donations[donation_index]["ship"]["name"] if "ship" in donations[donation_index] else "-"}'
        + f';{donations[donation_index]["location"] if "location" in donations[donation_index] else "-"}'
        + f';{donations[donation_index]["owner"] if "owner" in donations[donation_index] else "-"}'
        + f';{donations[donation_index]["method"] if "method" in donations[donation_index] else "-"}'
    )
