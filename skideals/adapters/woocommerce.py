"""通用 WooCommerce 适配器（WordPress 网店），用公开的 WooCommerce Store API（区块结账用的那套只读接口，不需要密钥）。

接口（robots.txt 一般只禁止 /wp-admin/、wc-logs 和 ?add-to-cart=，/wp-json/ 允许）：
- 分类列表：`/wp-json/wc/store/v1/products/categories?per_page=100&page=N` → id / slug / parent / count（配置新店时用）
- 商品：`/wp-json/wc/store/v1/products?category=<slug 或 id>&per_page=100&page=N`（分类含子分类；
  响应头 X-WP-TotalPages = 总页数）。每个商品有 name（HTML 实体）、permalink、`prices`（**最小货币单位**的字符串：
  "49995" + `currency_minor_unit: 2` = $499.95；`price` 现价、`regular_price` 原价）、`is_in_stock`、`is_purchasable`、
  categories / tags / brands、attributes（含每个属性值的 slug → 名称）、`variations[]`（变体 id + 属性 slug）。
- 变体：`/wp-json/wc/store/v1/products?type=variation&parent=<id1,id2,…>&per_page=100` —— 一次取很多个商品的全部变体
  （每个变体的价格、库存、`variation` = "Size: 150cm, Color: Black"、permalink 带 ?attribute_pa_size=…）。
  所以一个分类只要 “商品页数 + ⌈变体商品数 / 40⌉” 个请求。商品 `variations[]` 里只列出网站没隐藏的变体（通常是有货的）。
- 所有变体都 `is_purchasable: false` 的商品是“目录模式”（品牌官网只展示、链接到经销商，如 oyuki.com），跳过。

配置（config/categories.toml）：
  [mountainshop.ski]
  pages = [{ category = "skis" }, { category = "skis-backcountry" }]     # 也可以写 gender / condition
  [mountainshop.ski.hints]                 # 商品自己的分类 / 标签 slug → 线索（商品只挂在最细的分类上，如 womens-skis）
  "womens-skis" = { gender = "women" }
  "junior-skis" = { gender = "kids" }
"""
from __future__ import annotations

import html
import re
from collections.abc import Iterator

from ..net import FetchError
from ..normalize import known_brand
from .base import Adapter, RawProduct, SizeOption
from .shopify import (OUTDOOR_CATEGORIES, OUTDOOR_HARDWARE_RE, SHOE_CATEGORIES, explicit_shoe_system,
                      outdoor_gender, shoe_width)

_API = "/wp-json/wc/store/v1/products"
_PER_PAGE = 100
_MAX_PAGES = 20
_PARENTS_PER_CALL = 40      # 一次查 40 个商品的变体（URL 不会太长；一般一页 100 个变体内）
_SIZE_ATTR = re.compile(r"size|length|cm|brake|width|waist|inseam", re.I)
_COLOR_ATTR = re.compile(r"colou?r", re.I)
_SKIP = re.compile(r"gift ?card|e-?gift|warranty|\bmounting\b|\bservice\b|tune[- ]?up|\brental\b", re.I)


def money(prices: dict | None, key: str = "price") -> float | None:
    """Store API 的金额是最小货币单位的字符串："49995" + minor_unit 2 → 499.95；空 / 0 → None。"""
    prices = prices or {}
    raw = prices.get(key)
    if raw in (None, ""):
        return None
    try:
        val = int(str(raw)) / (10 ** int(prices.get("currency_minor_unit", 2)))
    except ValueError:
        return None
    return round(val, 2) if val > 0 else None


def _size_key(s: SizeOption) -> tuple:
    """尺码排序：先按厘米，再按标签里的第一个数字（"97mm" 排在 "102mm" 前面），最后按文字。"""
    m = re.search(r"\d+(?:\.\d+)?", s.label or "")
    return (s.cm or 0, float(m.group()) if m else 1e9, s.label or "")


def _text(s: str | None) -> str:
    return html.unescape(s or "").strip()


def _strip_html(s: str | None, limit: int = 4000) -> str | None:
    if not s:
        return None
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()
    return s[:limit] or None


def variation_options(parent: dict, var: dict) -> dict[str, str]:
    """变体的属性 → {属性名: 显示名}。优先用父商品 variations[].attributes（slug）+ attributes[].terms 换成显示名；
    没有时解析变体的 "Size: 150cm, Color: Black" 字符串（按已知属性名切分，属性值里带逗号也不会切错）。"""
    terms = {a.get("name"): {t.get("slug"): _text(t.get("name")) for t in a.get("terms") or []}
             for a in parent.get("attributes") or []}
    for v in parent.get("variations") or []:
        if v.get("id") == var.get("id") and v.get("attributes"):
            return {a["name"]: terms.get(a["name"], {}).get(a.get("value"), _text(a.get("value")))
                    for a in v["attributes"] if a.get("name") and a.get("value")}
    text = _text(var.get("variation"))
    names = [a.get("name") for a in parent.get("attributes") or [] if a.get("name")]
    if not text:
        return {}
    if names:
        rx = re.compile(r"(?:^|,\s*)(" + "|".join(re.escape(n) for n in names) + r"):\s*")
        marks = list(rx.finditer(text))
        if marks:
            return {m.group(1): text[m.end():(marks[i + 1].start() if i + 1 < len(marks) else len(text))].strip()
                    for i, m in enumerate(marks)}
    return {k.strip(): v.strip() for k, _, v in (part.partition(":") for part in text.split(", ")) if v.strip()}


class WooCommerceAdapter(Adapter):
    platform = "woocommerce"

    # ------------------------------------------------------------------ 抓取
    def _products(self, params: dict) -> Iterator[dict]:
        for page in range(1, _MAX_PAGES + 1):
            resp = self.fetcher.get(f"{self.base_url}{_API}", params={**params, "per_page": _PER_PAGE, "page": page})
            items = resp.json()
            if not isinstance(items, list):
                raise FetchError(str(resp.url), resp.status_code, "Store API 返回的不是商品列表")
            yield from items
            total = resp.headers.get("x-wp-totalpages")
            if len(items) < _PER_PAGE or (total and page >= int(total)):
                break

    def _variations(self, parents: list[int]) -> dict[int, list[dict]]:
        out: dict[int, list[dict]] = {}
        for i in range(0, len(parents), _PARENTS_PER_CALL):
            ids = ",".join(str(x) for x in parents[i:i + _PARENTS_PER_CALL])
            for v in self._products({"type": "variation", "parent": ids}):
                out.setdefault(v.get("parent"), []).append(v)
        return out

    def fetch(self) -> Iterator[RawProduct]:
        found: dict[int, dict] = {}
        for spec in self.cfg.get("pages") or []:
            spec = spec if isinstance(spec, dict) else {"category": spec}
            cat = str(spec.get("category") or "")
            if not cat:
                continue
            n = 0
            for p in self._products({"category": cat}):
                if not p.get("id") or p.get("type") not in ("simple", "variable"):
                    continue
                n += 1
                e = found.setdefault(p["id"], {"p": p, "genders": set(), "conds": set()})
                if spec.get("gender"):
                    e["genders"].add(spec["gender"])
                if spec.get("condition"):
                    e["conds"].add(spec["condition"])
            self.log(f"[{self.rid}] {self.category} {cat}: {n} 个商品")
        keep = [e for e in found.values() if self._keep(e["p"])]
        parents = [e["p"]["id"] for e in keep if e["p"].get("type") == "variable" and e["p"].get("variations")]
        variations = self._variations(parents) if parents else {}
        if parents:
            self.log(f"[{self.rid}] {self.category}: {len(parents)} 个商品的变体，{sum(map(len, variations.values()))} 个")
        catalog_only = 0
        for e in keep:
            raw = self._to_raw(e, variations.get(e["p"]["id"], []))
            if raw is None and not self._purchasable(e["p"], variations.get(e["p"]["id"], [])):
                catalog_only += 1
            if raw:
                yield raw
        if catalog_only:
            self.log(f"[{self.rid}] {self.category}: {catalog_only} 个商品不能在本站购买（目录模式），已跳过")

    # ------------------------------------------------------------------ 过滤 & 转换
    def _keep(self, p: dict) -> bool:
        title = _text(p.get("name"))
        ptype = _text((p.get("categories") or [{}])[0].get("name")) if p.get("categories") else None
        if not title or _SKIP.search(title):
            return False
        if self.category in OUTDOOR_CATEGORIES and OUTDOOR_HARDWARE_RE.search(title):
            return False
        return self.wanted(title, ptype)

    @staticmethod
    def _purchasable(p: dict, vars_: list[dict]) -> bool:
        return bool(p.get("is_purchasable")) or any(v.get("is_purchasable") for v in vars_)

    def _hints(self, p: dict) -> tuple[set[str], set[str]]:
        hints: dict = self.cfg.get("hints") or {}
        genders, conds = set(), set()
        for item in (p.get("categories") or []) + (p.get("tags") or []):
            h = hints.get(item.get("slug")) or {}
            if h.get("gender"):
                genders.add(h["gender"])
            if h.get("condition"):
                conds.add(h["condition"])
        return genders, conds

    def _to_raw(self, e: dict, vars_: list[dict]) -> RawProduct | None:
        p = e["p"]
        if not self._purchasable(p, vars_):
            return None
        from ..categories import norm_size
        title = _text(p.get("name"))
        currency = (p.get("prices") or {}).get("currency_code") or self.cfg.get("currency", "USD")
        outdoor = self.category in OUTDOOR_CATEGORIES
        footwear = self.category in SHOE_CATEGORIES
        attr_names = [a.get("name") or "" for a in p.get("attributes") or [] if a.get("has_variations")]
        size_names = [n for n in attr_names if _SIZE_ATTR.search(n) and not _COLOR_ATTR.search(n)]
        rows, widths, systems = [], set(), set()
        by_key: dict[str, SizeOption] = {}
        offers = vars_ if p.get("type") == "variable" else [p]
        for v in offers:
            if not v.get("is_purchasable") and p.get("type") == "variable":
                continue
            opts = variation_options(p, v) if v is not p else {}
            color = next((val for k, val in opts.items() if _COLOR_ATTR.search(k)), None)
            parts = [opts[n] for n in size_names if opts.get(n)]
            if not parts:   # 属性名不像尺码（"Option"…）：非颜色的选项当尺码
                parts = [val for k, val in opts.items() if not _COLOR_ATTR.search(k)][:1]
            label = (" " if footwear else " / ").join(parts) if outdoor else (parts[0] if parts else "")
            width = None
            if footwear:
                width = next((opts[n] for n in size_names if re.search(r"width", n, re.I) and opts.get(n)), None) \
                    or shoe_width(label)
                if width:
                    widths.add(width)
                systems.add(explicit_shoe_system(" ".join(size_names + [label])))
            price = money(v.get("prices"))
            reg = money(v.get("prices"), "regular_price")
            cmp_ = reg if reg and price and reg > price + 0.5 else None
            ok = bool(v.get("is_in_stock"))
            url = v.get("permalink") or p.get("permalink")
            cm = self.size_cm(label)
            if outdoor:
                rows.append({"id": v.get("id"), "label": label, "width": width, "color": color, "price": price,
                             "compare_at": cmp_, "available": ok, "url": url})
                key = str(len(rows))
            else:
                key = str(cm) if cm else (norm_size(self.category, label) or label or "default")
            cur = by_key.get(key)
            if cur is None:
                by_key[key] = SizeOption(label=label, cm=cm, available=ok, price=price, compare_at=cmp_,
                                         url=url if outdoor else None, color=color if outdoor else None)
            else:   # 同一尺码多个颜色：有一个有货就算有货，价格取有货里的最低
                if ok and price and (not cur.available or price < (cur.price or 1e9)):
                    cur.price, cur.compare_at = price, cmp_
                cur.available = cur.available or ok
        sizes = sorted(by_key.values(), key=_size_key)
        pool = [s for s in sizes if s.available and s.price] or [s for s in sizes if s.price]
        if pool:
            best = min(pool, key=lambda s: s.price)
            price, compare, available = best.price, best.compare_at, any(s.available for s in sizes)
        else:   # 变体都隐藏了（售罄）：用商品本身的价格，标为无货
            price = money(p.get("prices"))
            reg = money(p.get("prices"), "regular_price")
            compare = reg if reg and price and reg > price + 0.5 else None
            available = bool(p.get("is_in_stock")) and p.get("type") != "variable"
        if not price:
            return None
        genders, conds = self._hints(p)
        genders |= e["genders"]
        gender = "unisex" if {"men", "women"} <= genders else next(iter(genders)) if len(genders) == 1 else None
        if outdoor and gender is None:
            gender = outdoor_gender(title)
        brands = [_text(b.get("name")) for b in p.get("brands") or [] if b.get("name")]
        vendor = brands[0] if brands else (self.cfg.get("brand") or known_brand(title.split(" ")[0]) or None)
        cats = [_text(c.get("name")) for c in p.get("categories") or [] if c.get("name")]
        tags = [_text(t.get("name")) for t in p.get("tags") or [] if t.get("name")]
        images = p.get("images") or []
        extra: dict = {"categories": [c.get("slug") for c in p.get("categories") or []], "options": attr_names}
        if outdoor:
            extra["variants"] = rows
            extra["colors"] = sorted({r["color"] for r in rows if r["color"]})
        if footwear:
            if len(systems) == 1 and None not in systems:
                extra["shoe_size_system"] = next(iter(systems))
            if widths:
                extra["shoe_widths"] = sorted(widths)
                if len({r["width"] for r in rows}) == 1:
                    extra["shoe_width"] = next(iter(widths))
        if self.category in ("ski", "pole"):
            sizes = [s for s in sizes if s.cm]
        else:
            sizes = [s for s in sizes if s.label]
        return RawProduct(
            retailer_id=self.rid,
            external_id=str(p["id"]),
            url=p.get("permalink") or f"{self.base_url}/?p={p['id']}",
            title=title,
            price=price,
            compare_at=compare,
            currency=currency,
            vendor=vendor,
            product_type=cats[0] if cats else None,
            tags=cats[1:] + tags,
            image=(images[0].get("thumbnail") or images[0].get("src")) if images else None,
            sizes=sizes,
            available=available,
            gender_hint=gender,
            type_hints=[c.get("slug") for c in p.get("categories") or [] if c.get("slug")],
            body_text=_strip_html(p.get("short_description")) or _strip_html(p.get("description")),
            condition_hint=next(iter(conds | e["conds"]), None),
            extra=extra,
            category=self.category,
        )
