"""Tests for the Telegram sender (fake transport)."""

from src.strategy.price_watch.telegram import TelegramSender


class _FakeResp:
    def __init__(self, status, body=""):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def text(self):
        return self._body


class _FakeCtx:
    def __init__(self, resp):
        self.resp = resp

    async def __aenter__(self):
        return self.resp

    async def __aexit__(self, *_a):
        return False


class _FakeSession:
    def __init__(self, resp):
        self.resp = resp
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    def post(self, url, data=None):
        self.posts.append((url, data))
        return _FakeCtx(self.resp)


async def test_post_success_hits_telegram_api():
    sender = TelegramSender("TOKEN", "CHAT")
    session = _FakeSession(_FakeResp(200))
    ok = await sender._post(session, {"chat_id": "CHAT", "text": "hi"})
    assert ok is True
    url, data = session.posts[0]
    assert url == "https://api.telegram.org/botTOKEN/sendMessage"
    assert data == {"chat_id": "CHAT", "text": "hi"}


async def test_post_non_200_returns_false():
    sender = TelegramSender("TOKEN", "CHAT")
    session = _FakeSession(_FakeResp(400, "bad request"))
    assert await sender._post(session, {"chat_id": "CHAT", "text": "x"}) is False


async def test_send_swallows_transport_errors(monkeypatch):
    sender = TelegramSender("TOKEN", "CHAT")

    async def _boom(*_a, **_k):
        raise RuntimeError("network down")

    monkeypatch.setattr(sender, "_post", _boom)
    assert await sender.send("x") is False
