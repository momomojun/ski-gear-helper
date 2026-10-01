"""通用 Shopify 适配器。

Shopify 店铺都公开 /collections/<分类>/products.json（robots.txt 允许），一次最多 250 个商品，
里面有每个尺码(variant)的价格、原价、是否有货 —— 比解析网页稳定得多。

配置（config/retailers.toml）：
  collections = ["mens-skis", "womens-skis"]        # 抓取这些分类里的商品（取并集）
  [retailers.hints]                                   # 分类 → 标注线索；只出现在 hints 里的分类只用于打标签
  "womens-skis" = { gender = "women" }
  "ski-demos"   = { condition = "demo" }
  "system-skis" = { bindings = true }
"""
from __future__ import annotations

import html
import re
from collections import defaultdict
from collections.abc import Iterator

from ..models import RawProduct, SizeOption
from ..normalize import known_brand
from .base import NOT_SKI_RE, Adapter, parse_cm, to_float

_EXCLUDED_TYPES = re.compile(
    r"\bboots?\b|\bbindings?\b|\bpoles?\b|apparel|jacket|\bpants?\b|glove|mitt|goggle|helmet|\bbags?\b|\bskins?\b|"
    r"accessor|\bwax\b|\btools?\b|\bsocks?\b|cloth|gift|snowboard|\bnordic\b|cross[- ]?country|\bxc\b|\bskate\b|"
    r"\bclassic\b|layer|shirt|hood|sweat|sunglass|\blocks?\b|\bstraps?\b|\btun(?:e|ing)\b|service",
    re.I)
_SIZE_OPTION = re.compile(r"size|length|cm|brake|width|长度|サイズ", re.I)   # 尺码类选项（不是颜色）


def _strip_html(s: str | None, limit: int = 4000) -> str:
    if not s:
        return ""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()[:limit]


class ShopifyAdapter(Adapter):
    platform = "shopify"

    def __init__(self, cfg, fetcher, log=print):
        super().__init__(cfg, fetcher, log)
        self.store_meta: dict = {}

    # ------------------------------------------------------------------ 抓取
    def _collection(self, handle: str) -> list[dict]:
        out: list[dict] = []
        for page in range(1, 81):
            data = self.fetcher.get_json(f"{self.base_url}/collections/{handle}/products.json",
                                         params={"limit": 250, "page": page})
            items = data.get("products", [])
            out.extend(items)
            # 注意：隐藏商品不计入，所以“满页”常常只有 240 多个，不能用 < 250 判断最后一页
            if len(items) < 150:
                break
        return out

    def _meta(self) -> dict:
        try:
            return self.fetcher.get_json(f"{self.base_url}/meta.json")
        except Exception:
            return {}

    def fetch(self) -> Iterator[RawProduct]:
        self.store_meta = self._meta()
        currency = self.store_meta.get("currency") or self.cfg.get("currency", "USD")
        if currency != self.cfg.get("currency", "USD"):
            self.log(f"[{self.rid}] 注意：店铺币种是 {currency}，配置写的是 {self.cfg.get('currency')}")

        main = list(self.cfg.get("collections", []))
        hints: dict[str, dict] = self.cfg.get("hints", {})
        products: dict[int, dict] = {}
        member: dict[int, set[str]] = defaultdict(set)
        for handle in main + [h for h in hints if h not in main]:
            try:
                items = self._collection(handle)
            except Exception as e:  # 某个分类失败不影响其他分类
                self.log(f"[{self.rid}] 分类 {handle} 抓取失败：{e}")
                if handle in main:
                    raise
                continue
            self.log(f"[{self.rid}] {handle}: {len(items)} 个商品")
            for p in items:
                member[p["id"]].add(handle)
                if handle in main:
                    products[p["id"]] = p

        for pid, p in products.items():
            if not self._keep(p):
                continue
            raw = self._to_raw(p, member[pid], hints, currency)
            if raw:
                yield raw

    # ------------------------------------------------------------------ 过滤 & 转换
    def _keep(self, p: dict) -> bool:
        title = p.get("title") or ""
        ptype = p.get("product_type") or ""
        if self._is_rental(p) or re.search(r"gift ?card|e-?gift|warranty|\bmounting\b|\bservice\b|tune[- ]?up",
                                           title, re.I):
            return False
        if self.category != "ski":  # 其他分类：相信分类页，只排除明显属于别的分类的东西
            return self.wanted(title, ptype)
        if NOT_SKI_RE.search(title) or (ptype and _EXCLUDED_TYPES.search(ptype)):
            return False
        if self.cfg.get("strict"):  # 混杂分类（衣服+雪板）时要求明确是雪板
            return bool(re.search(r"\bskis?\b", title, re.I) or re.search(r"\bskis?\b", ptype, re.I))
        return self.wanted(title, ptype)

    @staticmethod
    def _is_rental(p: dict) -> bool:
        """租赁商品（按天/周/雪季收费）不是卖的板子。例：Sports Basement 的 “Demo Skis” 其实是租板，
        variant 是 “1 Day $25 / Weekend / Week / Season”。"""
        tags = p.get("tags") or []
        if isinstance(tags, str):
            tags = tags.split(",")
        if re.search(r"rent(al)?s?\b|sbrents|lease", p.get("handle") or "", re.I):
            return True
        if any(re.search(r"\brentals?\b|\blease\b", t, re.I) for t in tags):
            return True
        vtitles = [str(v.get("title") or "") for v in p.get("variants") or []]
        return sum(bool(re.match(r"\s*(\d+\s*)?(day|days|weekend|week|season|night)\b", t, re.I)) for t in vtitles) >= 2

    def _to_raw(self, p: dict, handles: set[str], hints: dict, currency: str) -> RawProduct | None:
        variants = p.get("variants") or []
        if not variants:
            return None
        from ..categories import norm_size
        options = [o.get("name", "") for o in p.get("options", [])]
        size_idx = next((i for i, n in enumerate(options)
                         if _SIZE_OPTION.search(n) and not re.search(r"colou?r", n, re.I)), None)
        length_based = self.category in ("ski", "pole")

        by_cm: dict[str, SizeOption] = {}
        for v in variants:
            label = None
            if size_idx is not None:
                label = v.get(f"option{size_idx + 1}")
            if length_based and (label is None or parse_cm(label) is None):  # 找不到“长度”选项时，挨个试所有选项
                label = next((v.get(f"option{i}") for i in (1, 2, 3) if parse_cm(v.get(f"option{i}"))), label)
            if not length_based and label is None:  # 服装等：没有尺码选项时，看选项值像不像尺码（S/M/L、26.5…）
                label = next((v.get(f"option{i}") for i in (1, 2, 3) if norm_size(self.category, v.get(f"option{i}"))),
                             None)
            if label in ("Default Title", "Default"):
                label = None
            cm = self.size_cm(label)
            price = to_float(v.get("price"))
            cmp_ = to_float(v.get("compare_at_price"))
            cmp_ = cmp_ if (cmp_ and price and cmp_ > price + 0.5) else None
            avail = bool(v.get("available"))
            key = str(cm) if cm else (norm_size(self.category, label) or label or "default")
            cur = by_cm.get(key)
            if cur is None:
                # 只有颜色变体（雪镜等）时 label 为空 → 当作均码，不能把颜色名当尺码
                shown = label if label else (v.get("title") if length_based else "")
                by_cm[key] = SizeOption(label=str(shown or ""), cm=cm, available=avail, price=price, compare_at=cmp_)
            else:  # 同一长度多个颜色：有一个有货就算有货，价格取有货里的最低
                if avail and (not cur.available or (price or 1e9) < (cur.price or 1e9)):
                    cur.price, cur.compare_at = price, cmp_
                cur.available = cur.available or avail

        sizes = sorted(by_cm.values(), key=lambda s: (s.cm or 0, s.label))
        pool = [s for s in sizes if s.available and s.price] or [s for s in sizes if s.price]
        if not pool:
            return None
        best = min(pool, key=lambda s: s.price)

        genders, conds, extra_tags = set(), set(), []
        for h in handles:
            hint = hints.get(h, {})
            if hint.get("gender"):
                genders.add(hint["gender"])
            if hint.get("condition"):
                conds.add(hint["condition"])
            if hint.get("bindings"):
                extra_tags.append("system")
        gender_hint = None
        if {"men", "women"} <= genders:
            gender_hint = "unisex"
        elif "kids" in genders and not genders & {"men", "women"}:
            gender_hint = "kids"
        else:
            gender_hint = next((g for g in ("women", "men", "unisex", "kids") if g in genders), None)
        type_hints = sorted(handles) + [hints[h]["type"] for h in handles if hints.get(h, {}).get("type")]

        tags = p.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        images = p.get("images") or []
        # 品牌官网一般用官网品牌；但官网也转卖别家的东西（J Skis 官网卖 Marker 固定器）：
        # 商品 vendor 是另一个“认识的”品牌就用它；vendor 也写官网自己时，再看标题开头（Black Crows 的 "Marker Griffon 13 ID"）。
        # vendor 是母公司/店名（"Amer Sports"）时仍用官网品牌。
        vendor = (p.get("vendor") or "").strip() or None
        store_brand = self.cfg.get("brand")
        if store_brand:
            own = known_brand(store_brand) or store_brand
            other = known_brand(vendor)
            if not other or other == own:
                in_title = known_brand(p.get("title"))
                vendor = in_title if in_title and in_title != own else store_brand
        return RawProduct(
            retailer_id=self.rid,
            external_id=str(p["id"]),
            url=f"{self.base_url}/products/{p['handle']}",
            title=p.get("title", "").strip(),
            price=best.price,
            compare_at=best.compare_at,
            currency=currency,
            vendor=vendor,
            product_type=p.get("product_type"),
            tags=list(tags) + extra_tags,
            image=images[0].get("src") if images else None,
            sizes=self._final_sizes(sizes),
            available=any(s.available for s in sizes),
            gender_hint=gender_hint,
            type_hints=type_hints,
            body_text=_strip_html(p.get("body_html")),
            condition_hint=next(iter(conds), None),
            extra={"published_at": p.get("published_at"), "created_at": p.get("created_at"),
                   "options": [o.get("name", "") for o in p.get("options") or []]},
            category=self.category,
        )

    def _final_sizes(self, sizes: list[SizeOption]) -> list[SizeOption]:
        if self.category in ("ski", "pole"):
            return [s for s in sizes if s.cm] if any(s.cm for s in sizes) else []
        # 其他分类：保留有意义的尺码标签（没有尺码选项的均码商品 → 空列表）
        return [s for s in sizes if s.label and s.label.lower() not in ("default", "default title")]
