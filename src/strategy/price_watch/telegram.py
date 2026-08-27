"""Telegram Bot sender via aiohttp (POST sendMessage, broadcast to multiple chats)."""

from __future__ import annotations

import asyncio
import logging

import aiohttp

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


class TelegramSender:
    """Minimal Telegram sendMessage client. Broadcasts to every chat_id. Never raises."""

    def __init__(self, bot_token: str, chat_ids: list[str]) -> None:
        self.bot_token = bot_token
        self.chat_ids = list(chat_ids)

    async def send(self, text: str) -> bool:
        """Broadcast ``text`` to all chat_ids; return True if every send was accepted."""
        if not self.chat_ids:
            return True
        try:
            async with aiohttp.ClientSession() as session:
                results = await asyncio.gather(
                    *[self._post(session, {"chat_id": cid, "text": text}) for cid in self.chat_ids]
                )
            return all(results)
        except Exception as exc:
            logger.error("telegram send failed: %s", exc, exc_info=True)
            return False

    async def send_to(self, chat_id: str, text: str) -> bool:
        """Send a single message to one chat (used for command replies)."""
        try:
            async with aiohttp.ClientSession() as session:
                return await self._post(session, {"chat_id": chat_id, "text": text})
        except Exception as exc:
            logger.error("telegram send_to failed: %s", exc, exc_info=True)
            return False

    async def fetch_updates(self, offset: int | None = None) -> list[dict]:
        """Poll getUpdates, returning new message entries (or [] on error)."""
        params = {"offset": offset} if offset is not None else {}
        url = f"{API_BASE}/bot{self.bot_token}/getUpdates"
        try:
            async with aiohttp.ClientSession() as session, session.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.error("getUpdates status %s: %s", resp.status, await resp.text())
                    return []
                data = await resp.json()
            return data.get("result", []) if data.get("ok") else []
        except Exception as exc:
            logger.error("getUpdates failed: %s", exc, exc_info=True)
            return []

    async def _post(self, session: aiohttp.ClientSession, payload: dict) -> bool:
        """POST the payload; split out so tests can pass a fake session."""
        url = f"{API_BASE}/bot{self.bot_token}/sendMessage"
        try:
            async with session.post(url, data=payload) as resp:
                if resp.status != 200:
                    logger.error("telegram status %s: %s", resp.status, await resp.text())
                    return False
                return True
        except Exception as exc:
            logger.error("telegram post failed: %s", exc, exc_info=True)
            return False
