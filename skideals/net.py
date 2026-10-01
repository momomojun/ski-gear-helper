"""礼貌的 HTTP 抓取器。

- 使用 curl_cffi 模拟 Chrome 的 TLS 指纹（很多网站会拒绝 python-requests 这类“机器人指纹”）
- 同一个域名的请求串行执行，并保证最小间隔（默认 1.5 秒）——不给别人的网站添麻烦
- 遵守 robots.txt（支持 Google 风格的 * 和 $ 通配符；Python 自带的 robotparser 不支持通配符）
- 对 429/5xx 自动退避重试；识别 Cloudflare / 验证码等“反爬挑战页”并抛出 BlockedError
"""
from __future__ import annotations

import random
import re
import threading
import time
from urllib.parse import urlencode, urlsplit

from curl_cffi import requests as creq

DEFAULT_HEADERS = {"Accept-Language": "en-US,en;q=0.9"}


class FetchError(Exception):
    def __init__(self, url: str, status: int | None = None, msg: str = ""):
        super().__init__(" ".join(str(x) for x in (status, msg, url) if x))
        self.url, self.status, self.msg = url, status, msg


class BlockedError(FetchError):
    """被网站的反爬系统拦截（验证码 / 挑战页）。"""


class RobotsDisallowed(FetchError):
    """robots.txt 不允许抓取这个地址。"""


class Robots:
    """只解析 `User-agent: *` 组；最长匹配规则胜出，长度相同时 Allow 优先。"""

    def __init__(self, text: str):
        groups: list[tuple[list[str], list[tuple[bool, str]], float | None]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        delay: float | None = None
        seen_rule = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, val = (x.strip() for x in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if seen_rule:
                    groups.append((agents, rules, delay))
                    agents, rules, delay, seen_rule = [], [], None, False
                agents.append(val.lower())
            elif key in ("allow", "disallow"):
                seen_rule = True
                if val:
                    rules.append((key == "allow", val))
            elif key == "crawl-delay":
                seen_rule = True
                try:
                    delay = min(float(val), 30.0)  # 上限 30 秒，防止离谱的值
                except ValueError:
                    pass
        groups.append((agents, rules, delay))
        star = [r for ags, rs, _ in groups if "*" in ags for r in rs]
        self.rules = [(allow, len(p), self._compile(p)) for allow, p in star]
        # 网站要求的最小请求间隔（Crawl-delay），抓取器会遵守
        self.crawl_delay = max((d for ags, _, d in groups if "*" in ags and d), default=None)

    @staticmethod
    def _compile(pattern: str) -> re.Pattern:
        anchored = pattern.endswith("$")
        if anchored:
            pattern = pattern[:-1]
        rx = "".join(".*" if ch == "*" else re.escape(ch) for ch in pattern)
        return re.compile(rx + ("$" if anchored else ""))

    def allowed(self, path: str) -> bool:
        best: tuple[int, bool] | None = None
        for allow, length, rx in self.rules:
            if rx.match(path) and (best is None or length > best[0] or (length == best[0] and allow)):
                best = (length, allow)
        return True if best is None else best[1]


_BLOCK_MARKERS = ("just a moment", "cf-chl", "challenge-platform", "px-captcha", "captcha-delivery",
                  "are you a robot", "access denied", "request unsuccessful", "京东验证", "risk_handler")


# 有的反爬系统返回 HTTP 200 的挑战页 / 假页面（2026-09 调研时实际遇到）：
# Patagonia “Hang Tight! Routing to checkout…”、Sierra /securityfailover/、Kjus 的 Akamai bm-verify 算力挑战、
# Nordstrom 的 F5 脚本页、Dick's 的 Akamai 行为挑战、PerimeterX 的 PX-Show 页
_BLOCK_200_MARKERS = ("botfailoveroriginal_files", "/securityfailover/", "bm-verify", "/_sec/verify", "istlwashere",
                      "sec-if-cpt-container", "px-show", "access to this page has been denied", "px-captcha")


def looks_blocked(resp) -> bool:
    url = str(resp.url)
    if any(m in url for m in ("risk_handler", "/captcha", "validate.perfdrive", "securityfailover", "PX-Show")):
        return True
    head = (resp.text or "")[:30000].lower()
    if resp.status_code == 200:
        return any(m in head for m in _BLOCK_200_MARKERS)
    if resp.status_code not in (202, 403, 405, 429, 503):
        return False
    return len(head) < 6000 or any(m in head for m in _BLOCK_MARKERS)


def decode_html(resp) -> str:
    """正确解码日文等非 UTF-8 页面（价格.com 用 Shift_JIS）。"""
    raw = resp.content
    enc = None
    ctype = resp.headers.get("content-type", "")
    m = re.search(r"charset=([\w-]+)", ctype, re.I)
    if m:
        enc = m.group(1)
    else:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", raw[:4000], re.I)
        if m:
            enc = m.group(1).decode()
    if enc and enc.lower() in ("shift_jis", "shift-jis", "sjis", "x-sjis", "windows-31j"):
        enc = "cp932"
    try:
        return raw.decode(enc or "utf-8", errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(self, min_interval: float = 1.5, timeout: float = 30, retries: int = 2,
                 respect_robots: bool = True, headers: dict | None = None):
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self.respect_robots = respect_robots
        self.headers = {**DEFAULT_HEADERS, **(headers or {})}
        self._hosts: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.request_count = 0

    def _state(self, host: str) -> dict:
        with self._lock:
            st = self._hosts.get(host)
            if st is None:
                st = {"lock": threading.Lock(), "last": 0.0, "robots": None,
                      "session": creq.Session(impersonate="chrome", headers=self.headers)}
                self._hosts[host] = st
            return st

    def _robots(self, scheme: str, host: str, st: dict) -> Robots:
        """robots.txt：200 → 按规则；404 等 → 没有限制；5xx → 全部禁止（RFC 9309）；
        连 robots.txt 都被反爬拦截（Helly Hansen 403、Backcountry 202 挑战页）→ 整个网站当作“被拦截”，不再请求。"""
        if st["robots"] is None:
            try:
                r = st["session"].get(f"{scheme}://{host}/robots.txt", timeout=self.timeout)
                if looks_blocked(r) or r.status_code in (202, 401, 403):
                    st["robots_blocked"] = True
                    st["robots"] = Robots("User-agent: *\nDisallow: /")
                elif r.status_code >= 500:
                    st["robots"] = Robots("User-agent: *\nDisallow: /")
                else:
                    st["robots"] = Robots(r.text if r.status_code == 200 else "")
            except Exception:
                st["robots"] = Robots("")
            st["last"] = time.monotonic()
        return st["robots"]

    def get(self, url: str, *, params: dict | None = None, headers: dict | None = None,
            check_robots: bool = True, allow_redirects: bool = True, min_interval: float | None = None):
        if params:
            url = url + ("&" if "?" in url else "?") + urlencode(params)
        parts = urlsplit(url)
        st = self._state(parts.netloc)
        with st["lock"]:
            robots = None
            if check_robots and self.respect_robots:
                robots = self._robots(parts.scheme, parts.netloc, st)
                if st.get("robots_blocked"):
                    raise BlockedError(url, None, "robots.txt 被反爬系统拦截")
                path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
                if not robots.allowed(path):
                    raise RobotsDisallowed(url, msg="robots.txt disallows")
            interval = self.min_interval if min_interval is None else min_interval
            if robots is not None and robots.crawl_delay:
                interval = max(interval, robots.crawl_delay)
            for attempt in range(self.retries + 1):
                wait = interval - (time.monotonic() - st["last"])
                if wait > 0:
                    time.sleep(wait + random.uniform(0, 0.4))
                try:
                    resp = st["session"].get(url, headers=headers, timeout=self.timeout,
                                             allow_redirects=allow_redirects)
                except Exception as e:  # 网络错误
                    st["last"] = time.monotonic()
                    if attempt >= self.retries:
                        raise FetchError(url, None, str(e)[:160]) from e
                    time.sleep(2 ** attempt * 2)
                    continue
                st["last"] = time.monotonic()
                self.request_count += 1
                if looks_blocked(resp):
                    raise BlockedError(url, resp.status_code, "anti-bot challenge")
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(2 ** attempt * 3)
                    continue
                if resp.status_code >= 400:
                    raise FetchError(url, resp.status_code)
                return resp
        raise FetchError(url, None, "unreachable")

    def get_json(self, url: str, **kw):
        return self.get(url, **kw).json()

    def get_html(self, url: str, **kw) -> str:
        return decode_html(self.get(url, **kw))
