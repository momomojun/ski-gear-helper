"""Tactics（俄勒冈 Eugene 的老牌单板 / 户外店，1999 年开业）：单板店，但雪服、雪裤、手套、保暖内衣、雪镜、头盔
和双板是同一批商品（Airblaster、686、Volcom、Burton、Smith、Oakley、Anon…）。

分类页是服务器渲染的 HTML，一页 48 个格子，翻页看 `<link rel="next" href="/snowboard-jackets/page-2">`。
每个格子是“一个商品的一个颜色”（/airblaster/beast-3l-jacket/flames）：同一商品的不同颜色合并成一条，取最便宜的颜色。
格子上只有现价和“(30% off)”，没有原价 → 原价用折扣反推（按零售价常见的 .95 / .00 结尾取整）。
没有尺码（要进商品页才有），只知道列出来的就是有货的。
robots.txt：禁止 /search、/*/rp- 等，分类页允许。

配置（config/categories.toml）：
  [tactics.jacket]
  pages = [{ path = "/snowboard-jackets", gender = "men" }, { path = "/womens-snowboard-jackets", gender = "women" }]
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterator

from selectolax.parser import HTMLParser

from ..net import BlockedError
from .base import Adapter, RawProduct, to_float

_MAX_PAGES = 12


def estimate_list_price(price: float, pct: float | None) -> float | None:
    """“$258.95 (30% off)” → 原价 ≈ 258.95 / 0.7 = 369.93 → 取最接近的 .95 / .00 结尾：369.95。"""
    if not pct or pct <= 0 or pct >= 95:
        return None
    raw = price / (1 - pct / 100)
    base = math.floor(raw)
    cands = [base - 0.05, base + 0.95, float(base), float(base + 1)]
    best = min(cands, key=lambda c: abs(c - raw))
    return round(best, 2) if best > price + 0.5 else None


def parse_tiles(html: str) -> tuple[list[dict], str | None]:
    doc = HTMLParser(html)
    tiles = doc.css("div.browse-grid-item")
    out = []
    for t in tiles:
        a = t.css_first("a[href]")
        if a is None:
            continue
        href = a.attributes.get("href") or ""
        parts = [p for p in href.split("?")[0].split("/") if p]
        if len(parts) < 2:
            continue
        brand_node = a.css_first(".browse-grid-item-brand")
        color_node = a.css_first(".browse-grid-item-color")
        brand = brand_node.text(strip=True) if brand_node else ""
        color = color_node.text(strip=True) if color_node else ""
        text = re.sub(r"\s+", " ", a.text(separator=" ")).strip()
        name = text
        if brand and name.startswith(brand):
            name = name[len(brand):]
        if color and name.rstrip().endswith(color):
            name = name.rstrip()[: -len(color)]
        name = re.sub(r"\s+", " ", name).strip(" -")
        price_node = t.css_first(".browse-grid-item-sale-price") or t.css_first(".browse-grid-item-price")
        price_text = price_node.text(separator=" ", strip=True) if price_node else ""
        m = re.search(r"\$\s*([\d,]+(?:\.\d{2})?)", price_text)
        price = to_float(m.group(1)) if m else None
        pm = re.search(r"\((\d{1,2})%\s*off\)", price_text, re.I)
        img = t.css_first("img")
        out.append({"product": "/" + "/".join(parts[:2]), "url": href, "brand": brand, "name": name, "color": color,
                    "price": price, "pct": float(pm.group(1)) if pm else None,
                    "image": (img.attributes.get("src") if img else None)})
    nxt = doc.css_first('link[rel="next"]')
    return out, (nxt.attributes.get("href") if nxt else None)


class TacticsAdapter(Adapter):
    platform = "tactics"

    def fetch(self) -> Iterator[RawProduct]:
        products: dict[str, dict] = {}
        for spec in self.cfg.get("pages") or []:
            path = spec["path"] if isinstance(spec, dict) else spec
            gender = spec.get("gender") if isinstance(spec, dict) else None
            url, n = self.base_url + path, 0
            for _ in range(_MAX_PAGES):
                html = self.fetcher.get_html(url)
                tiles, nxt = parse_tiles(html)
                if not tiles and n == 0 and "browse-grid" not in html:
                    raise BlockedError(url, None, "页面里没有商品格子（可能被拦截或改版）")
                n += len(tiles)
                for t in tiles:
                    if t["price"] is None or not t["name"]:
                        continue
                    cur = products.get(t["product"])
                    if cur is None:
                        products[t["product"]] = {**t, "genders": {gender} if gender else set(), "colors": 1}
                    else:
                        cur["colors"] += 1
                        if gender:
                            cur["genders"].add(gender)
                        if t["price"] < cur["price"]:   # 同一商品不同颜色：取最便宜的颜色（链接也指向它）
                            cur.update(price=t["price"], pct=t["pct"], url=t["url"], color=t["color"], image=t["image"])
                if not nxt:
                    break
                url = self.base_url + nxt
            self.log(f"[{self.rid}] {self.category} {path}: {n} 个格子")
        for key, p in products.items():
            title = f"{p['brand']} {p['name']}".strip()
            if not self.wanted(title):
                continue
            g = p["genders"]
            gender = "unisex" if {"men", "women"} <= g else next(iter(g)) if len(g) == 1 else None
            yield RawProduct(
                retailer_id=self.rid,
                external_id=key,
                url=self.base_url + p["url"],
                title=title,
                price=p["price"],
                compare_at=estimate_list_price(p["price"], p["pct"]),
                currency="USD",
                vendor=p["brand"] or None,
                product_type=None,
                tags=[],
                image=(self.base_url + p["image"]) if (p.get("image") or "").startswith("/") else p.get("image"),
                available=True,
                gender_hint=gender,
                category=self.category,
            )
