"""Telegram notification helper.

Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from the environment.
If either is missing, all send() calls are no-ops (bot keeps running).

Usage:
    from . import notify
    await notify.send("OPEN long BTC @ 67500")
"""

from __future__ import annotations

import os
import asyncio
import urllib.request
import urllib.parse
import json

_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

_ENABLED = bool(_TOKEN and _CHAT_ID)


def _post_sync(text: str) -> None:
    """Blocking HTTP POST to Telegram sendMessage."""
    url  = f"https://api.telegram.org/bot{_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": _CHAT_ID, "text": text}).encode()
    req  = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read())
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API error: {body}")


async def send(text: str) -> None:
    """Send a message to the configured Telegram chat (fire-and-forget)."""
    if not _ENABLED:
        return
    try:
        # run blocking I/O in thread so we don't stall the event loop
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _post_sync, text)
    except Exception as e:  # noqa: BLE001
        # Never crash the trading loop because of a notification failure
        print(f"[notify] Telegram send failed: {e}", flush=True)
