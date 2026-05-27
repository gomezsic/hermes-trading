from __future__ import annotations

import httpx

_TIMEOUT = httpx.Timeout(10.0)


async def fetch(asset: str | None = None) -> dict:
    """Free BTC-family onchain signal: mempool.space recommended fees + tx count.

    If the asset isn't BTC-family, return {"schema_version": 1, "available": false}
    and the loop will simply ignore it.
    """
    base = (asset or "").split("/")[0].upper()
    if base != "BTC":
        return {"schema_version": 1, "available": False}

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        fees_r = await client.get("https://mempool.space/api/v1/fees/recommended")
        fees_r.raise_for_status()
        fees = fees_r.json()

        mempool_r = await client.get("https://mempool.space/api/mempool")
        mempool_r.raise_for_status()
        mempool = mempool_r.json()

    return {
        "schema_version": 1,
        "available": True,
        "fees_sat_vb": int(fees.get("halfHourFee", fees.get("fastestFee", 0))),
        "mempool_tx_count": int(mempool.get("count", 0)),
    }
