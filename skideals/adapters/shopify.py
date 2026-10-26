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
from ..net import BlockedError
from ..normalize import known_brand
from .base import NOT_SKI_RE, Adapter, parse_cm, to_float

_EXCLUDED_TYPES = re.compile(
    r"\bboots?\b|\bbindings?\b|\bpoles?\b|apparel|jacket|\bpants?\b|glove|mitt|goggle|helmet|\bbags?\b|\bskins?\b|"
    r"accessor|\bwax\b|\btools?\b|\bsocks?\b|cloth|gift|snowboard|\bnordic\b|cross[- ]?country|\bxc\b|\bskate\b|"
    r"\bclassic\b|layer|shirt|hood|sweat|sunglass|\blocks?\b|\bstraps?\b|\btun(?:e|ing)\b|service",
    re.I)
_SIZE_OPTION = re.compile(r"size|length|cm|brake|width|waist|inseam|长度|サイズ", re.I)   # 尺码类选项（不是颜色）


OUTDOOR_CATEGORIES = frozenset({"outdoor_jacket", "outdoor_pants", "outdoor_top", "hiking_shoe", "trail_shoe"})
SHOE_CATEGORIES = frozenset({"hiking_shoe", "trail_shoe"})
OUTDOOR_HARDWARE_RE = re.compile(
    r"\b(?:skis?|snowboards?|bindings?|poles?|helmets?|goggles?|skins?|crampons?|snowshoes?)\b", re.I)


def outdoor_gender(title: str) -> str | None:
    """Only explicit gender words; numeric shoe sizes never imply gender."""
    t = title.replace("’", "'")
    if re.search(r"\b(?:kids?|boys?|girls?|youth|junior|toddlers?|infants?)\b", t, re.I):
        return "kids"
    if re.search(r"\bunisex\b", t, re.I):
        return "unisex"
    women = bool(re.search(r"\bwomen(?:'?s)?\b|\bw's\b", t, re.I))
    men = bool(re.search(r"\bmen(?:'?s)?\b|\bm's\b", t, re.I))
    return "unisex" if women and men else "women" if women else "men" if men else None


def explicit_shoe_system(text: str) -> str | None:
    """A system must be named by the source, never inferred from a number or country."""
    systems = set()
    for system, pattern in (("US", r"\bU\.?S\.?(?![a-z])"), ("UK", r"\bU\.?K\.?(?![a-z])"),
                            ("EU", r"\bEU(?:R)?\b|\bEuropean\b")):
        if re.search(pattern, text, re.I):
            systems.add(system)
    return next(iter(systems)) if len(systems) == 1 else None


def shoe_width(label: str) -> str | None:
    match = re.search(r"\b(?:extra[- ]wide|x[- ]wide|wide|narrow|regular|standard|medium|[2-6]E|EE|D|B)\b", label, re.I)
    return match.group(0) if match else None


def _strip_html(s: str | None, limit: int = 4000) -> str:
    if not s:
        return ""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()[:limit]


def ajax_product(data: dict) -> dict:
    """公开单品 .js 有逐变体库存，金额是美分；.json 没有 available，不能拿它判售罄。"""
    variants = []
    for v in data.get("variants") or []:
        converted = dict(v)
        for key in ("price", "compare_at_price"):
            cents = to_float(v.get(key))
            converted[key] = round(cents / 100, 2) if cents is not None else None
        for i, option in enumerate(v.get("options") or [], 1):
            converted.setdefault(f"option{i}", option)
        variants.append(converted)
    return {**data, "variants": variants, "product_type": data.get("type"), "body_html": data.get("description"),
            "images": [{"src": image} if isinstance(image, str) else image for image in data.get("images") or []],
            "options": [{"name": o} if isinstance(o, str) else o for o in data.get("options") or []]}


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
        except BlockedError:
            raise
        except Exception:
            return {}

    def fetch(self) -> Iterator[RawProduct]:
        self.store_meta = self._meta()
        # currency_override：欧洲品牌的一个 Shopify 店挂多个地区（atomic.com/en-us），/en-us 下的商品价格已经是美元，
        # 但 meta.json 仍然报店铺本币 EUR——这时以配置为准，否则美元价会被当成欧元换算
        currency = self.cfg.get("currency_override") or self.store_meta.get("currency") or self.cfg.get("currency", "USD")
        if currency != self.cfg.get("currency", "USD"):
            self.log(f"[{self.rid}] 注意：店铺币种是 {currency}，配置写的是 {self.cfg.get('currency')}")

        main = list(self.cfg.get("collections", []))
        hints: dict[str, dict] = self.cfg.get("hints", {})
        products: dict[int, dict] = {}
        member: dict[int, set[str]] = defaultdict(set)
        for handle in main + [h for h in hints if h not in main]:
            try:
                items = self._collection(handle)
            except BlockedError:
                raise
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

        # 补充商家放在混合服装集合中的已核实商品，避免把整页普通运动衫当成抓绒。
        for handle in self.cfg.get("product_handles", []):
            if not re.fullmatch(r"[\w-]+", handle):
                raise ValueError(f"无效的 Shopify 商品 handle: {handle}")
            data = self.fetcher.get_json(f"{self.base_url}/products/{handle}.js")
            p = ajax_product(data)
            if not p or not p.get("id"):
                raise ValueError(f"商品数据缺失: {handle}")
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
        if self.category in OUTDOOR_CATEGORIES and OUTDOOR_HARDWARE_RE.search(title + " " + ptype):
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
        tags = p.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        # SB publishes explicit D/B widths as a structured product tag. Keep the
        # literal standard; do not equate D/B to Regular by assumed gender rules.
        tag_widths = {shoe_width(str(t).partition(":")[2]) for t in tags
                      if re.fullmatch(r"shoe\s*width", str(t).partition(":")[0].strip(), re.I)}
        tag_width = next(iter(tag_widths)) if len(tag_widths) == 1 and None not in tag_widths else None
        options = [o.get("name", "") for o in p.get("options", [])]
        outdoor = self.category in OUTDOOR_CATEGORIES
        footwear = self.category in SHOE_CATEGORIES
        size_indices = [i for i, n in enumerate(options)
                        if _SIZE_OPTION.search(n) and not re.search(r"colou?r", n, re.I)]
        size_idx = next((i for i in size_indices if not re.search(r"width", options[i], re.I)), None)
        if size_idx is None and not footwear:
            size_idx = next(iter(size_indices), None)
        color_idx = next((i for i, n in enumerate(options) if re.search(r"colou?r", n, re.I)), None)
        width_idx = next((i for i, n in enumerate(options) if re.search(r"width", n, re.I)), None)
        variant_rows = []
        widths, systems = set(), set()
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
            width = v.get(f"option{width_idx + 1}") if width_idx is not None else None
            color = v.get(f"option{color_idx + 1}") if color_idx is not None else None
            if outdoor:
                # Preserve all size axes, including widths and waist/inseam combinations.
                parts = [str(v[f"option{i + 1}"]).strip() for i in size_indices if v.get(f"option{i + 1}")]
                label = (" " if footwear else " / ").join(parts) or label
            if footwear:
                # 网站的结构化标签优先于标题；标题里单个字母 D / B 不算楦宽（"Topo Plan B Hiker" 的 B 是型号名）
                title_width = shoe_width(p.get("title") or "")
                if title_width and len(title_width) == 1:
                    title_width = None
                width = width or shoe_width(str(label or "")) or tag_width or title_width
                if width:
                    widths.add(str(width))
                    if label and str(width).lower() not in str(label).lower():
                        label = f"{label} {width}"
                system = explicit_shoe_system(" ".join([options[size_idx] if size_idx is not None else "", str(label or "")]))
                systems.add(system)
            cm = self.size_cm(label)
            price = to_float(v.get("price"))
            cmp_ = to_float(v.get("compare_at_price"))
            cmp_ = cmp_ if (cmp_ and price and cmp_ > price + 0.5) else None
            avail = bool(v.get("available"))
            variant_url = f"{self.base_url}/products/{p['handle']}"
            if v.get("id") is not None:
                variant_url += f"?variant={v['id']}"
            if outdoor:
                # One row per variant: never collapse a sold-out colour or a width into another SKU.
                variant_rows.append({"id": v.get("id"), "label": label, "width": width, "color": color,
                                     "options": [v.get(f"option{i + 1}") for i in range(len(options))],
                                     "price": price, "compare_at": cmp_, "available": avail, "url": variant_url})
                key = str(len(variant_rows))
            else:
                key = str(cm) if cm else (norm_size(self.category, label) or label or "default")
            cur = by_cm.get(key)
            if cur is None:
                # 只有颜色变体（雪镜等）时 label 为空 → 当作均码，不能把颜色名当尺码
                shown = label if label else (v.get("title") if length_based else "")
                by_cm[key] = SizeOption(label=str(shown or ""), cm=cm, available=avail, price=price, compare_at=cmp_,
                                        color=color if outdoor else None, url=variant_url if outdoor else None)
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
        if outdoor and gender_hint is None:
            gender_hint = outdoor_gender(p.get("title") or "")
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
        extra = {"published_at": p.get("published_at"), "created_at": p.get("created_at"), "options": options}
        if outdoor:
            extra["variants"] = variant_rows
            extra["colors"] = sorted({row["color"] for row in variant_rows if row["color"]})
        if footwear:
            if len(systems) == 1 and None not in systems:
                extra["shoe_size_system"] = next(iter(systems))
            if widths:
                extra["shoe_widths"] = sorted(widths)
                # A product context must apply to every variant. Mixed or unknown
                # widths belong on individual labels, never on the whole product.
                if len({row["width"] for row in variant_rows}) == 1:
                    extra["shoe_width"] = next(iter(widths))
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
            extra=extra,
            category=self.category,
        )

    def _final_sizes(self, sizes: list[SizeOption]) -> list[SizeOption]:
        if self.category in ("ski", "pole"):
            return [s for s in sizes if s.cm] if any(s.cm for s in sizes) else []
        # 其他分类：保留有意义的尺码标签（没有尺码选项的均码商品 → 空列表）
        return [s for s in sizes if s.label and s.label.lower() not in ("default", "default title")]
