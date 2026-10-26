"""POC 官网（瑞典头盔 / 雪镜 / 护具品牌，poc.com/en-us；pocsports.com 301 跳转过来）：Next.js（App Router）+ Centra 无头电商。

分类页（`/en-us/categories/snow/all-mountain/helmets` …）服务器渲染，数据在两处：
- `self.__next_f.push([1, "..."])` 的 RSC 数据流：拼起来按行切（`<id>:<JSON>`），里面每个商品是带 `centraProduct` 的对象：
  `name`、`uri`（颜色款的商品页 `/en-us/product/<uri>`）、`priceAsNumber` / `priceBeforeDiscountAsNumber`、`showAsOnSale`、
  `available`、`price`（“300,00 USD”，用来认币种）、`color_swatch.color_name`、`media[0].url`，
  `relatedVariants[]` 是同一商品的其他颜色（字段同上）。
  注意列表里每个颜色的 `items` 只有一个尺码（不是全部尺码），所以列表页不给尺码，只看颜色款是否有货。
- `script#item-list-ld-json` 的 ItemList（名称、地址、价格、库存）：RSC 解析不到时的后备。
分页：数据里 `currentPage` / `totalPages`，目前 snow 分类都是 1 页。
robots.txt：全部允许，只禁止 /api/centra/basket-session-state 等几个接口。

配置（config/categories.toml）：
  [poc.helmet]
  pages = [{ path = "/categories/snow/all-mountain/helmets" }, { path = "/categories/snow/backcountry/helmets" }]
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..net import BlockedError
from .base import Adapter, RawProduct, to_float

_PUSH_RE = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.S)
_ROW_RE = re.compile(r"\n(?=[0-9a-f]{1,6}:)")


def rsc_text(html: str) -> str:
    """把页面里所有 `self.__next_f.push([1, "..."])` 的字符串拼起来。"""
    parts = []
    for chunk in _PUSH_RE.findall(html):
        try:
            arr = json.loads(chunk)
        except ValueError:
            continue
        if len(arr) > 1 and isinstance(arr[1], str):
            parts.append(arr[1])
    return "".join(parts)


def rsc_products(text: str) -> list[dict]:
    """RSC 数据流里所有带 `centraProduct` 的商品对象（按出现顺序，不展开它们内部）。"""
    found: list[dict] = []

    def walk(o):
        if isinstance(o, dict):
            if "centraProduct" in o:
                found.append(o)
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for row in _ROW_RE.split("\n" + text):
        m = re.match(r"([0-9a-f]{1,6}):(.*)", row, re.S)
        if not m or m.group(2)[:1] not in "[{":
            continue
        try:
            walk(json.loads(m.group(2)))
        except ValueError:
            continue
    return found


def _num(v) -> float | None:
    return to_float(v) if isinstance(v, (int, float)) else None


def _currency(price: str | None) -> str:
    m = re.search(r"\b([A-Z]{3})\b", price or "")
    return m.group(1) if m else "USD"


def _variant(v: dict) -> dict | None:
    price = _num(v.get("priceAsNumber"))
    uri = v.get("uri")
    if not price or not uri:
        return None
    before = _num(v.get("priceBeforeDiscountAsNumber"))
    media = v.get("media") or []
    return {"uri": uri, "price": price,
            "compare_at": before if before and before > price + 0.5 else None,
            "available": bool(v.get("available")),
            "currency": _currency(v.get("price")),
            "color": (v.get("color_swatch") or {}).get("color_name"),
            "image": media[0].get("url") if media and isinstance(media[0], dict) else None}


def parse_listing(html: str) -> list[dict]:
    """分类页 → 每个 Centra 商品一条：{id, title, variants: [...]}（同一商品的颜色款合并）。"""
    out: dict[str, dict] = {}
    for p in rsc_products(rsc_text(html)):
        pid = str(p.get("centraProduct") or p.get("product") or "")
        name = (p.get("englishName") or p.get("name") or "").strip()
        if not pid or not name:
            continue
        cur = out.setdefault(pid, {"id": pid, "title": name, "fit": p.get("fit_type_fit_type"), "variants": {}})
        for v in [p, *(p.get("relatedVariants") or [])]:
            if v.get("relation") not in (None, "product", "variant"):
                continue
            var = _variant(v)
            if var:
                cur["variants"].setdefault(var["uri"], var)
    if out:
        return [{**p, "variants": list(p["variants"].values())} for p in out.values() if p["variants"]]
    return parse_jsonld(html)


def parse_jsonld(html: str) -> list[dict]:
    """后备：ItemList JSON-LD（每个颜色款一条，没有原价）。"""
    node = HTMLParser(html).css_first("script#item-list-ld-json")
    if node is None:
        return []
    try:
        data = json.loads(node.text())
    except ValueError:
        return []
    out = []
    for el in data.get("itemListElement") or []:
        it = el.get("item") or {}
        off = it.get("offers") or {}
        uri = (it.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
        price = to_float(off.get("price"))
        if not uri or not price or not it.get("name"):
            continue
        out.append({"id": uri, "title": it["name"], "fit": None,
                    "variants": [{"uri": uri, "price": price, "compare_at": None,
                                  "available": str(off.get("availability") or "").endswith("InStock"),
                                  "currency": off.get("priceCurrency") or "USD", "color": None,
                                  "image": it.get("image")}]})
    return out


class PocAdapter(Adapter):
    platform = "poc"

    @property
    def market(self) -> str:
        return self.cfg.get("market", "/en-us")

    def fetch(self) -> Iterator[RawProduct]:
        products: dict[str, dict] = {}
        for spec in self.cfg.get("pages") or []:
            path = spec["path"] if isinstance(spec, dict) else spec
            gender = spec.get("gender") if isinstance(spec, dict) else None
            url = f"{self.base_url}{self.market}{path}"
            html = self.fetcher.get_html(url)
            items = parse_listing(html)
            if not items and "__next_f" not in html:
                raise BlockedError(url, None, "页面里没有商品数据（可能被拦截或改版）")
            for it in items:
                cur = products.setdefault(it["id"], {**it, "genders": set()})
                if cur is not it:
                    seen = {v["uri"] for v in cur["variants"]}
                    cur["variants"] += [v for v in it["variants"] if v["uri"] not in seen]
                if gender:
                    cur["genders"].add(gender)
            self.log(f"[{self.rid}] {self.category} {path}: {len(items)} 个商品")
        for pid, p in products.items():
            if not self.wanted(p["title"]):
                continue
            raw = self._to_raw(pid, p)
            if raw:
                yield raw

    def _to_raw(self, pid: str, p: dict) -> RawProduct | None:
        variants = p["variants"]
        live = [v for v in variants if v["available"]]
        best = min(live or variants, key=lambda v: v["price"])
        g = p["genders"]
        gender = "unisex" if {"men", "women"} <= g else next(iter(g)) if len(g) == 1 else None
        return RawProduct(
            retailer_id=self.rid,
            external_id=pid,
            url=f"{self.base_url}{self.market}/product/{best['uri']}",
            title=p["title"],
            price=best["price"],
            compare_at=best["compare_at"],
            currency=best["currency"],
            vendor="POC",
            tags=[x for x in (p.get("fit"),) if x],
            image=best.get("image"),
            available=bool(live),
            gender_hint=gender,
            extra={"colors": len(variants), "color": best.get("color")},
            category=self.category,
        )
