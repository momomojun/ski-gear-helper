"""真实浏览器回退模块（用于在遭遇强反爬、Cloudflare/PerimeterX 挑战页时自动接管）。

设计原则：
1. 默认常规 HTTP（curl_cffi）优先，兼顾极高速度和低资源消耗；
2. 仅在页面返回反爬拦截（BlockedError / 403 / 202 / 挑战页）时作为降级手段按需调用；
3. 本机环境拥有天然的住宅 IP，真实浏览器加载能通过绝大部分静默防护；
4. 若未安装 playwright，模块平稳降级并给出安装提示，不影响现有功能。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_PLAYWRIGHT_AVAILABLE: bool | None = None


def is_browser_available() -> bool:
    """检查环境中是否安装了 playwright。"""
    global _PLAYWRIGHT_AVAILABLE
    if _PLAYWRIGHT_AVAILABLE is None:
        try:
            import playwright  # noqa: F401
            _PLAYWRIGHT_AVAILABLE = True
        except ImportError:
            _PLAYWRIGHT_AVAILABLE = False
    return _PLAYWRIGHT_AVAILABLE


def fetch_with_browser(url: str, timeout: float = 30.0, wait_selector: str | None = None) -> tuple[str, dict[str, str]]:
    """使用 Playwright 真实浏览器加载页面并绕过前置反爬。
    返回 (html_content, cookies_dict)。
    """
    if not is_browser_available():
        raise RuntimeError("Playwright 未安装。请执行: pip install playwright && playwright install chromium")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            locale="en-US",
            viewport={"width": 1440, "height": 900},
        )
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
            if wait_selector:
                try:
                    page.wait_for_selector(wait_selector, timeout=8000)
                except Exception:
                    pass
            else:
                page.wait_for_timeout(2000)  # 等待前置挑战或 JS 渲染完成

            content = page.content()
            cookies = {c["name"]: c["value"] for c in context.cookies()}
            return content, cookies
        finally:
            browser.close()
