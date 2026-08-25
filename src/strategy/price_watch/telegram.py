"""Telegram Bot sender via aiohttp (POST sendMessage)."""

from __future__ import annotations

import logging

import aiohttp

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"


class TelegramSender:
    """Minimal Telegram sendMessage client. Never raises — failures are logged."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    async def send(self, text: str) -> bool:
        """Send a message; return True if accepted (HTTP 200)."""
        try:
            async with aiohttp.ClientSession() as session:
                return await self._post(session, {"chat_id": self.chat_id, "text": text})
        except Exception as exc:
            logger.error("telegram send failed: %s", exc, exc_info=True)
            return False

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
