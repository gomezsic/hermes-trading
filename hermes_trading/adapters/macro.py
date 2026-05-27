from __future__ import annotations

import httpx

_TIMEOUT = httpx.Timeout(10.0)


async def fetch() -> dict:
    """ECB FX rates via frankfurter.app (free, no key)."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        r = await client.get("https://api.frankfurter.dev/v1/latest?from=USD&to=EUR,GBP,JPY")
        r.raise_for_status()
        payload = r.json()

    return {
        "schema_version": 1,
        "fx": {k: float(v) for k, v in payload.get("rates", {}).items()},
    }
