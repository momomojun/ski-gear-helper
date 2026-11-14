"""Bright Data 传输层：不联网，模拟 Web Unlocker 的 format=json 响应。"""
import json

from skideals import brightdata
from skideals.net import BlockedError


class _FakeHTTP:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append((url, headers, json.loads(data)))
        status, payload = self.replies.pop(0)

        class R:
            status_code = status
            text = json.dumps(payload) if not isinstance(payload, str) else payload
            headers = {}

            def json(self):
                return json.loads(self.text)
        return R()


def _fetcher(monkeypatch, replies):
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "tok_test")
    monkeypatch.setenv("BRIGHTDATA_ZONE", "web_unlocker1")
    f = brightdata.make_fetcher(min_interval=0)
    fake = _FakeHTTP(replies)
    orig_state = f._state

    def state(host):
        st = orig_state(host)
        st["session"]._http = fake
        return st
    f._state = state
    return f, fake


def test_not_configured_returns_none(monkeypatch):
    monkeypatch.delenv("BRIGHTDATA_API_KEY", raising=False)
    monkeypatch.delenv("BRIGHTDATA_ZONE", raising=False)
    assert brightdata.make_fetcher() is None


def test_unlocker_envelope_is_unwrapped_and_robots_respected(monkeypatch):
    robots = {"status_code": 200, "headers": {}, "body": "User-agent: *\nDisallow: /cart\n"}
    page = {"status_code": 200, "headers": {"Content-Type": "application/json"}, "body": '{"ok": 1}'}
    f, fake = _fetcher(monkeypatch, [(200, robots), (200, page)])
    assert f.get_json("https://www.rei.com/c/downhill-skis?json=true") == {"ok": 1}
    url, headers, body = fake.calls[1]
    assert url == brightdata.API and headers["Authorization"] == "Bearer tok_test"
    assert body == {"zone": "web_unlocker1", "url": "https://www.rei.com/c/downhill-skis?json=true", "format": "json",
                    "country": "us"}
    try:   # robots.txt 不允许的地址不会发出请求（不花钱）
        f.get("https://www.rei.com/cart")
        raise AssertionError("should be disallowed")
    except Exception as e:
        assert type(e).__name__ == "RobotsDisallowed"
    assert len(fake.calls) == 2


def test_target_challenge_page_still_detected(monkeypatch):
    robots = {"status_code": 404, "headers": {}, "body": ""}
    challenge = {"status_code": 403, "headers": {}, "body": "<html>Just a moment...</html>"}
    f, _ = _fetcher(monkeypatch, [(200, robots), (200, challenge)])
    try:
        f.get("https://www.sunandski.com/shop/category/ski-equipment-104")
        raise AssertionError("should be blocked")
    except BlockedError:
        pass
