"""KÜHL 官网（盐湖城的户外服装品牌，1983 年创立）：自建 Next.js 站点，分类页服务器渲染。

列表页（`/kuhl/mens/pants/`、`/kuhl/womens/jackets/` …）：
- `script#__NEXT_DATA__` → `props.pageProps.pageData.products[]`，字段是单字母缩写：
  `n` 名称（带 ™/®）、`d` 款式名（有时比 `n` 多一个品类词：“Rydr™” / “Rydr Pant”）、`u` 商品页路径、`m` 款号、`i` 商品 id、`p` {amount（美分）, currency}、`s` 一句话简介、
  `f` 版型（Klassik / Tapered / Tall）、`c` 颜色 [[色值, 名称, 默认?, [图片路径…], …]]；夹在中间的营销格子没有 `p`，跳过。
  `total` / `totalPages` / `page` 给出分页（一般一页就是全部）。
- `script#collection-jsonld` 的 ItemList：每个 Product 的 `offers.availability`（InStock / OutOfStock）和列表图。
列表上只有原价，没有尺码；商品页（可选，`details = true`）`pageData.product`：`prices[0].discountedAmount`（促销价）、
`sizes`（S–XXL）或 `waists` × `inseams`（裤子），每个颜色的 `unavailableSizes` / `unavailableWaists`。
商品页一页约 500–600 KB，默认不抓。
robots.txt：禁止 /search/、/api/、/checkout/、/profiles/、/accounts/（ClaudeBot 等 AI 爬虫同样规则），分类页允许。
**不要用 /api/ 接口。**

配置（config/categories.toml）：
  [kuhl.outdoor_pants]
  pages = [{ path = "/kuhl/mens/pants/", gender = "men" }, { path = "/kuhl/womens/pants/", gender = "women" }]
  details = false
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..net import BlockedError, FetchError
from .base import Adapter, RawProduct, SizeOption

_MAX_PAGES = 10
_MEDIA = "https://www.kuhl.com/media/versions/"


def _clean(name: str | None) -> str:
    """“Raidr<sup>™</sup> Pant” / “Raidr™ Pant” → “Raidr Pant”。"""
    s = re.sub(r"<[^>]+>", "", name or "")
    return re.sub(r"\s+", " ", s.replace("™", "").replace("®", "")).strip()


_NOUN_RE = re.compile(r"\b(?:pants?|shorts?|skorts?|skirts?|dress|jackets?|shells?|parkas?|vests?|hoody|hoodie|"
                      r"shirts?|shirt-jac|tops?|tees?|t-shirt|tanks?|pullover|crew|zip|fleece|sweater|coat|bibs?)\b", re.I)


def pick_title(name: str | None, style: str | None) -> str:
    """`n`（“Rydr™”）有时没有品类词，`d`（“Rydr Pant”）有：哪个带品类词用哪个，都没有就用 `n`。"""
    n, d = _clean(name), _clean(style)
    if n and _NOUN_RE.search(n):
        return n
    if d and _NOUN_RE.search(d):
        return d
    return n or d


def _cents(x) -> float | None:
    try:
        v = round(float(x) / 100, 2)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def next_data(html: str) -> dict:
    node = HTMLParser(html).css_first("script#__NEXT_DATA__")
    if node is None:
        raise BlockedError("kuhl", None, "页面里没有 __NEXT_DATA__（可能被拦截或改版）")
    return (json.loads(node.text()).get("props") or {}).get("pageProps", {}).get("pageData") or {}


def jsonld_offers(html: str) -> dict[str, dict]:
    """ItemList 里每个商品：url 路径 → {available, image}。"""
    node = HTMLParser(html).css_first("script#collection-jsonld")
    out: dict[str, dict] = {}
    if node is None:
        return out
    try:
        data = json.loads(node.text())
    except ValueError:
        return out
    items = ((data.get("mainEntity") or {}).get("itemListElement")) or data.get("itemListElement") or []
    for el in items:
        it = el.get("item") or {}
        url = re.sub(r"^https?://[^/]+", "", it.get("url") or "")
        if not url:
            continue
        avail = str((it.get("offers") or {}).get("availability") or "")
        out[url] = {"available": None if not avail else avail.endswith("InStock"), "image": it.get("image")}
    return out


def parse_listing(html: str) -> tuple[list[dict], int]:
    """列表页 → 商品列表（已合并 JSON-LD 的库存和图片）+ 总页数。"""
    pd = next_data(html)
    offers = jsonld_offers(html)
    out = []
    for p in pd.get("products") or []:
        price = _cents((p.get("p") or {}).get("amount"))
        if not price or not p.get("u") or not p.get("n"):
            continue
        colors = p.get("c") or []
        img = None
        if colors and len(colors[0]) > 3 and colors[0][3]:
            img = f"{_MEDIA}{colors[0][3][0]}_category_section_item.jpg"
        off = offers.get(p["u"], {})
        out.append({"id": str(p.get("m") or p.get("i")), "url": p["u"], "title": pick_title(p.get("n"), p.get("d")),
                    "price": price, "currency": (p.get("p") or {}).get("currency") or "USD",
                    "fit": p.get("f"), "summary": p.get("s"), "colors": [c[1] for c in colors if len(c) > 1],
                    "image": off.get("image") or img, "available": off.get("available")})
    return out, int(pd.get("totalPages") or 1)


def parse_detail(html: str) -> dict:
    """商品页 → {price, compare_at, sizes: [(label, available)], gender}。"""
    p = next_data(html).get("product") or {}
    prices = p.get("prices") or [{}]
    full = _cents(prices[0].get("amount"))
    sale = _cents(prices[0].get("discountedAmount"))
    price, compare = (sale, full) if sale and full and sale < full - 0.5 else (full, None)
    colors = p.get("colors") or []
    if p.get("sizes"):
        labels, key = p["sizes"], "unavailableSizes"
    else:
        labels, key = p.get("waists") or [], "unavailableWaists"
    sizes = []
    for lb in labels:
        ok = any(lb not in (c.get(key) or []) for c in colors) if colors else True
        sizes.append((str(lb), ok))
    return {"price": price, "compare_at": compare, "sizes": sizes, "gender": p.get("gender"),
            "inseams": p.get("inseams") or []}


def _gender_from_url(url: str) -> str | None:
    m = re.match(r"/kuhl/(mens|womens|kids)/", url)
    return {"mens": "men", "womens": "women", "kids": "kids"}.get(m.group(1)) if m else None


class KuhlAdapter(Adapter):
    platform = "kuhl"

    def _listing(self, path: str) -> Iterator[dict]:
        n = 0
        for page in range(1, _MAX_PAGES + 1):
            url = f"{self.base_url}{path}" + (f"?page={page}" if page > 1 else "")
            items, pages = parse_listing(self.fetcher.get_html(url))
            n += len(items)
            yield from items
            if page >= pages or not items:
                break
        self.log(f"[{self.rid}] {self.category} {path}: {n} 个商品")

    def fetch(self) -> Iterator[RawProduct]:
        products: dict[str, dict] = {}
        for spec in self.cfg.get("pages") or []:
            path = spec["path"] if isinstance(spec, dict) else spec
            gender = spec.get("gender") if isinstance(spec, dict) else None
            for it in self._listing(path):
                cur = products.setdefault(it["url"], {**it, "genders": set()})
                g = gender or _gender_from_url(it["url"])
                if g:
                    cur["genders"].add(g)
        details = bool(self.cfg.get("details"))
        budget = int(self.cfg.get("detail_limit") or 200)
        for url, p in products.items():
            if not self.wanted(p["title"]):
                continue
            raw = self._to_raw(url, p)
            if details and budget > 0:
                budget -= 1
                self._add_sizes(raw)
            yield raw

    def _add_sizes(self, raw: RawProduct) -> None:
        try:
            d = parse_detail(self.fetcher.get_html(raw.url))
        except BlockedError:
            raise
        except FetchError as e:
            self.log(f"[{self.rid}] 商品页抓取失败：{e}")
            return
        if d["price"]:
            raw.price, raw.compare_at = d["price"], d["compare_at"]
        raw.sizes = [SizeOption(label=lb, cm=None, available=ok, price=raw.price, compare_at=raw.compare_at,
                                url=raw.url) for lb, ok in d["sizes"]]
        if raw.sizes:
            raw.available = any(s.available for s in raw.sizes)

    def _to_raw(self, url: str, p: dict) -> RawProduct:
        g = p["genders"]
        gender = "unisex" if {"men", "women"} <= g else next(iter(g)) if len(g) == 1 else None
        return RawProduct(
            retailer_id=self.rid,
            external_id=p["id"],
            url=self.base_url + url,
            title=p["title"],
            price=p["price"],
            compare_at=None,   # 列表页只有原价；促销价要抓商品页（details = true）
            currency=p["currency"],
            vendor="KÜHL",
            tags=[x for x in (p.get("fit"),) if x],
            image=p.get("image"),
            available=True if p.get("available") is None else p["available"],
            gender_hint=gender,
            body_text=p.get("summary"),
            category=self.category,
        )
