from __future__ import annotations

import os

import httpx

_TIMEOUT = httpx.Timeout(10.0)


async def fetch() -> dict:
    """Free default: Fear & Greed Index from alternative.me.
    If NEWS_API_KEY is set, newsapi.org could be plugged in here; we stick
    to the free signal until the operator opts in.
    """
    if os.getenv("NEWS_API_KEY"):
        # Reserved for newsapi.org integration; not used in paper-mode default.
        pass

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get("https://api.alternative.me/fng/")
        r.raise_for_status()
        payload = r.json()

    item = payload["data"][0]
    return {
        "schema_version": 1,
        "fear_greed": int(item["value"]),
        "classification": str(item["value_classification"]),
    }
