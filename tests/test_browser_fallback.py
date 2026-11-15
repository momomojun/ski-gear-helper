"""测试浏览器降级回退机制。"""
from unittest.mock import patch
import pytest

from skideals.browser_fallback import is_browser_available
from skideals.net import BlockedError, Fetcher


def test_is_browser_available():
    # 无论环境中是否安装了 playwright，函数都应返回 bool 而非抛出未捕获异常
    res = is_browser_available()
    assert isinstance(res, bool)


def test_fetcher_fallback_not_available():
    # 模拟没有安装 playwright 时，正常抛出 BlockedError
    with patch("skideals.net.is_browser_available", return_value=False):
        fetcher = Fetcher(browser_fallback=True)
        # looks_blocked 触发时正常报错
        pass


def test_fetcher_browser_fallback_success():
    # 模拟当页面被拦截且浏览器降级成功时
    fake_html = "<html><body><h1>Product page</h1></body></html>"
    fake_cookies = {"cf_clearance": "abc123xyz"}

    with patch("skideals.net.is_browser_available", return_value=True), \
         patch("skideals.net.fetch_with_browser", return_value=(fake_html, fake_cookies)):
        fetcher = Fetcher(browser_fallback=True)

        class DummyResp:
            url = "https://example.com/item"
            status_code = 403
            text = "cf-chl challenge"
            content = b"cf-chl challenge"
            headers = {}

        class DummySession:
            def __init__(self):
                class DummyCookies:
                    def set(self, k, v, domain=None):
                        pass
                self.cookies = DummyCookies()

            def get(self, url, **kw):
                return DummyResp()

        # 直接给 host state 注入 DummySession
        st = fetcher._state("example.com")
        st["session"] = DummySession()

        resp = fetcher.get("https://example.com/item", check_robots=False)
        assert resp.status_code == 200
        assert "Product page" in resp.text
