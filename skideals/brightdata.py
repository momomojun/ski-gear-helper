"""经由 Bright Data Web Unlocker 请求网页（只给配置了 fetch_via = "brightdata" 的网站用）。

为什么需要：REI（一轮请求太多会被拦）、Sun & Ski（防火墙按 IP 封）这类网站，从家里的网络直连会被反爬拦下。
Bright Data 是商业抓取服务（按成功请求计费），由它负责换 IP、浏览器指纹和验证码。

用法：
- 在 Bright Data 后台建一个 Web Unlocker 区域（zone），拿到 API Token；
- 在自己电脑上设置环境变量（不要写进代码或仓库）：
      BRIGHTDATA_API_KEY = <API Token>
      BRIGHTDATA_ZONE    = <Web Unlocker 区域名>
- retailers.toml 里给要走 Bright Data 的网站加 fetch_via = "brightdata"。没设置环境变量时自动退回直连，并在日志里提示。

接口（官方文档 / brightdata/skills 仓库的 fetch、scrape 说明）：
    POST https://api.brightdata.com/request
    Authorization: Bearer <token>
    {"zone": "<zone>", "url": "<目标网址>", "format": "json"}
format=json 时返回 {status_code, headers, body}，这样目标网站真实的状态码和响应头还在，
net.Fetcher 原有的 robots.txt 检查、限速、退避重试、“挑战页”识别都原样复用。
"""
from __future__ import annotations

import json
import os

from curl_cffi import requests as creq

API = "https://api.brightdata.com/request"


def configured() -> bool:
    return bool(os.environ.get("BRIGHTDATA_API_KEY") and os.environ.get("BRIGHTDATA_ZONE"))


class _Response:
    """和 curl_cffi 的响应对象用法一致（status_code / text / content / headers / url / json()）。"""

    def __init__(self, url: str, status: int, headers: dict, body: str | bytes):
        self.url = url
        self.status_code = status
        self.headers = {str(k).lower(): v for k, v in (headers or {}).items()}
        self.content = body.encode("utf-8") if isinstance(body, str) else (body or b"")
        self.text = body if isinstance(body, str) else self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


class BrightDataSession:
    """替换 net.Fetcher 里每个域名的 session：.get(url, ...) 变成一次 Web Unlocker 请求。"""

    def __init__(self, token: str, zone: str, country: str | None = "us"):
        self.zone = zone
        self.country = country
        self._http = creq.Session()
        self._auth = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def get(self, url: str, headers: dict | None = None, timeout: float = 60, allow_redirects: bool = True):
        body = {"zone": self.zone, "url": url, "format": "json"}
        if self.country:
            body["country"] = self.country   # 美国零售商：用美国出口，价格和库存才是美国站的
        r = self._http.post(API, headers=self._auth, data=json.dumps(body), timeout=max(timeout, 90))
        if r.status_code != 200:
            # Bright Data 自己拒绝（Token 无效、余额不足、合规策略不允许这个网站…）：原样交给上层当作失败
            return _Response(url, r.status_code, dict(r.headers), r.text)
        try:
            env = r.json()
        except ValueError:
            return _Response(url, 200, dict(r.headers), r.text)
        status = int(env.get("status_code") or env.get("status") or 200)
        return _Response(url, status, env.get("headers") or {}, env.get("body") or "")


def make_fetcher(min_interval: float = 1.0, log=print):
    """返回经由 Bright Data 的 Fetcher；没配置环境变量时返回 None（调用方退回直连）。"""
    from .net import Fetcher
    if not configured():
        return None
    session = BrightDataSession(os.environ["BRIGHTDATA_API_KEY"], os.environ["BRIGHTDATA_ZONE"],
                                os.environ.get("BRIGHTDATA_COUNTRY", "us") or None)

    class _BDFetcher(Fetcher):
        via = "brightdata"

        def _state(self, host: str) -> dict:
            st = super()._state(host)
            st["session"] = session
            return st

    # Web Unlocker 自己会重试，这里少重试一次省钱；超时放宽（解挑战页可能要几十秒）
    return _BDFetcher(min_interval=min_interval, timeout=90, retries=1)
