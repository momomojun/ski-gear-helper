"""Skimo Co（盐湖城的登山滑雪专卖店，skimo.co）：自建的 OpenCart 站点，分类页是服务器渲染的 HTML。

列表页：`/skis?limit=100&page=N`（`<link rel="next">` 给出下一页），每个格子 `div[id^=product-]`：
`div.name a` = 标题和商品页地址（`/dynafit-dna-pro-skis`），`div.price` 里有 `price-old`（原价）/ `price-new`（现价，
可能带 “From”，表示最低尺码价）；没打折时 `div.price` 只有一个价格。格子上没有品牌、尺码和库存。
品牌：`/product/manufacturer` 品牌列表（每次抓取 1 个请求），按标题开头匹配。
商品页（可选，`details = true`）：`div.options select` 的尺码（“162cm ($1099.95)” / “Relic, XS” / “30.0 (Out Of Stock)”），
脚本里的 `base_price` / `base_special` 和 `price_map` / `special_map`（每个选项的价差）/ `stock_map`（库存文字）。
robots.txt：只禁止 /account/、/checkout/、/affiliate/、/product/product/review，分类页和商品页允许。

配置（config/categories.toml）：
  [skimo.ski]
  pages = [{ path = "/skis" }]
  details = true          # 抓商品页拿尺码和库存（一个商品一个请求）
  detail_limit = 250
"""
from __future__ import annotations

import html as htmllib
import re
from collections.abc import Iterator

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..net import BlockedError, FetchError
from .base import Adapter, RawProduct, SizeOption, to_float

_PAGE = 100
_MAX_PAGES = 10
_PRICE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
_MAP_RE = re.compile(r"\b(price_map|special_map|stock_map)\[\"(\d+)\"\]\s*=\s*(-?[\d.]+|\"(?:[^\"\\]|\\.)*\")")
_BASE_RE = re.compile(r"\bvar (base_price|base_special) = (-?[\d.]+)")
_BRANDS: dict[str, list[str]] = {}   # base_url → 品牌名（长的在前）


def _text(node) -> str:
    return re.sub(r"\s+", " ", (node.text(separator=" ") if node else "").replace("\xa0", " ")).strip()


def _money(text: str) -> float | None:
    m = _PRICE_RE.search(text or "")
    return to_float(m.group(1)) if m else None


def parse_listing(html: str) -> tuple[list[dict], str | None]:
    """列表页 → 格子列表 + 下一页地址。"""
    doc = HTMLParser(html)
    out = []
    for t in doc.css("div.product-grid > div[id^=product-]"):
        a = t.css_first("div.name a[href]")
        if a is None:
            continue
        old, new = t.css_first(".price .price-old"), t.css_first(".price .price-new")
        if new is not None:
            price, compare = _money(_text(new)), _money(_text(old))
        else:
            price, compare = _money(_text(t.css_first("div.price"))), None
        img = t.css_first("div.image img")
        out.append({"id": t.attributes.get("id", "").removeprefix("product-"),
                    "href": a.attributes.get("href") or "",
                    "title": _text(a),
                    "price": price,
                    "compare_at": compare if compare and price and compare > price + 0.5 else None,
                    "from": "from" in _text(t.css_first("div.price")).lower(),
                    "image": img.attributes.get("src") if img else None})
    nxt = doc.css_first('link[rel="next"]')
    return out, (nxt.attributes.get("href") if nxt else None)


def parse_brands(html: str) -> list[str]:
    names = {_text(a) for a in HTMLParser(html).css("div.manufacturer-content li a")}
    return sorted((n for n in names if n), key=len, reverse=True)


def brand_of(title: str, brands: list[str]) -> str | None:
    low = title.lower()
    return next((b for b in brands if low.startswith(b.lower()) and
                 (len(low) == len(b) or not low[len(b)].isalnum())), None)


def _available(stock: str) -> bool:
    s = htmllib.unescape(stock or "").lower()
    return "out of stock" not in s and "sold out" not in s and "unavailable" not in s


def parse_detail(html: str) -> list[dict]:
    """商品页 → 每个选项 {label, color, price, compare_at, available}；没有尺码选项就是空列表。"""
    doc = HTMLParser(html)
    select, kind = None, ""
    for opt in doc.css("div.options div.option"):
        label = _text(opt.css_first("label")).lower()
        sel = opt.css_first("select")
        if sel is not None and (select is None or re.search(r"size|length", label)):
            select, kind = sel, label
            if re.search(r"size|length", label):
                break
    if select is None:
        return []
    has_color = "color" in kind or "colour" in kind   # “Color & US Size” → “Relic, XS”；“Length & Flex” → “169cm, Flex 70”
    base = {k: float(v) for k, v in _BASE_RE.findall(html)}
    maps: dict[str, dict[str, str]] = {"price_map": {}, "special_map": {}, "stock_map": {}}
    for name, oid, val in _MAP_RE.findall(html):
        maps[name][oid] = val
    bp, bs = base.get("base_price"), base.get("base_special") or 0.0
    out = []
    for o in select.css("option"):
        oid = o.attributes.get("value") or ""
        if not oid.isdigit():   # “- Select” 和 “- Out of Stock Options -”（value="oos"，点了弹到货通知）不是尺码
            continue
        text = _text(o)
        oos = bool(re.search(r"\(out of stock\)", text, re.I))
        label = re.sub(r"\s*\((?:\$[\d.,]+|out of stock)\)", "", text, flags=re.I).strip()
        color = None
        if has_color and ", " in label:
            color, label = (x.strip() for x in label.split(", ", 1))
        price = compare = None
        if bp is not None:
            reg = round(bp + float(maps["price_map"].get(oid, 0)), 2)
            sale = round(bs + float(maps["special_map"].get(oid, 0)), 2) if bs else reg
            price, compare = (sale, reg) if sale < reg - 0.5 else (reg, None)
        stock = maps["stock_map"].get(oid)
        out.append({"label": label, "color": color, "price": price, "compare_at": compare,
                    "available": not oos and (stock is None or _available(stock))})
    return out


class SkimoAdapter(Adapter):
    platform = "skimo"

    def _brands(self) -> list[str]:
        if self.base_url not in _BRANDS:
            try:
                _BRANDS[self.base_url] = parse_brands(self.fetcher.get_html(f"{self.base_url}/product/manufacturer"))
            except FetchError as e:
                self.log(f"[{self.rid}] 品牌列表抓取失败（品牌交给标题识别）：{e}")
                _BRANDS[self.base_url] = []
        return _BRANDS[self.base_url]

    def _tiles(self, path: str) -> Iterator[dict]:
        sep = "&" if "?" in path else "?"
        url, n = f"{self.base_url}{path}{sep}limit={_PAGE}", 0
        for _ in range(_MAX_PAGES):
            html = self.fetcher.get_html(url)
            tiles, nxt = parse_listing(html)
            if not tiles and n == 0 and "product-grid" not in html:
                raise BlockedError(url, None, "页面里没有商品格子（可能被拦截或改版）")
            n += len(tiles)
            yield from tiles
            if not nxt or not tiles:
                break
            url = self.base_url + nxt if nxt.startswith("/") else nxt
        self.log(f"[{self.rid}] {self.category} {path}: {n} 个商品")

    def fetch(self) -> Iterator[RawProduct]:
        products: dict[str, dict] = {}
        for spec in self.cfg.get("pages") or []:
            path = spec["path"] if isinstance(spec, dict) else spec
            gender = spec.get("gender") if isinstance(spec, dict) else None
            for t in self._tiles(path):
                if t["price"] is None or not t["title"]:
                    continue
                cur = products.setdefault(t["href"], {**t, "genders": set()})
                if gender:
                    cur["genders"].add(gender)
        brands = self._brands() if products else []
        details = bool(self.cfg.get("details"))
        budget = int(self.cfg.get("detail_limit") or 300)
        for href, p in products.items():
            if not self.wanted(p["title"]):
                continue
            raw = self._to_raw(href, p, brands)
            if details and budget > 0:
                budget -= 1
                self._add_sizes(raw)
            yield raw

    def _add_sizes(self, raw: RawProduct) -> None:
        try:
            opts = parse_detail(self.fetcher.get_html(raw.url))
        except BlockedError:
            raise
        except FetchError as e:
            self.log(f"[{self.rid}] 商品页抓取失败：{e}")
            return
        sizes: dict = {}
        for o in opts:
            cm = self.size_cm(o["label"])
            key = cm or o["label"]
            cur = sizes.get(key)
            if cur is None:
                sizes[key] = SizeOption(label=o["label"], cm=cm, available=o["available"], price=o["price"],
                                        compare_at=o["compare_at"], url=raw.url, color=o["color"])
            elif o["available"] and (not cur.available or (o["price"] or 1e9) < (cur.price or 1e9)):
                cur.label, cur.available, cur.price, cur.compare_at, cur.color = (
                    o["label"], True, o["price"], o["compare_at"], o["color"])
        if not sizes:
            return
        raw.sizes = list(sizes.values())
        raw.available = any(s.available for s in raw.sizes)
        live = [s for s in raw.sizes if s.available and s.price] or [s for s in raw.sizes if s.price]
        if live:
            best = min(live, key=lambda s: s.price)
            raw.price, raw.compare_at = best.price, best.compare_at

    def _to_raw(self, href: str, p: dict, brands: list[str]) -> RawProduct:
        g = p["genders"]
        gender = "unisex" if {"men", "women"} <= g else next(iter(g)) if len(g) == 1 else None
        url = self.base_url + href if href.startswith("/") else href
        return RawProduct(
            retailer_id=self.rid,
            external_id=p["id"] or href.strip("/"),
            url=url,
            title=p["title"],
            price=p["price"],
            compare_at=p["compare_at"],
            currency="USD",
            vendor=brand_of(p["title"], brands),
            image=p.get("image"),
            available=True,   # 列表页只有在售商品的价格；抓了商品页会按尺码库存改写
            gender_hint=gender,
            category=self.category,
        )
