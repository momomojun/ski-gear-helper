"""Peter Glenn Ski & Sports (peterglenn.com) —— BigCommerce Stencil 店铺。支持全部滑雪装备分类。

抓取思路：
1. 分类页 /ski/<分类>/?limit=100&page=N（`limit=100` 被主题支持，每页 100 个）。
   每张商品卡片 <article class="card" data-entity-id=...> 自带结构化 data-* 属性：
   商品 ID、名称、品牌、价格，以及 data-product-category（完整分类路径，如
   "Men's/Skis/Alpine"、"Women's/Jackets/Ski"、"Kids'/Gloves"）→ 任何分类都能直接得到性别
   （同时在 Men's 和 Women's 下 → unisex）。
   划线价在卡片里的 [data-product-rrp-price-without-tax]（"Was $1,000.00"）。
2. 尺码：卡片上的尺码色块是前端用 GraphQL(POST) 异步加载的，拿不到；
   所以只对打折商品（compare_at 非空）按折扣力度从大到小抓商品页，最多 max_detail_pages 个（每个分类单独计）。
   商品页内嵌 `window.variantData = {...}`（服务端渲染好的 GraphQL 变体数据：尺码/颜色、有货、价格、
   相对原价节省的金额），以及 `BCData.product_attributes.in_stock_attributes` 作为后备。
   注意：该主题只渲染“有货”的变体，缺货尺码直接不显示；只认名字像 Size/Length/Brake/Width 的选项当尺码
   （雪镜这类只有颜色选项的商品不会把颜色当尺码）。

分类页（默认值，cfg["categories"] 可覆盖；字符串或 {path=..., gender=...}）见 DEFAULT_CATEGORIES。
Peter Glenn 没有止滑带 / 雪崩装备 / 护具 / 打蜡工具的独立分类（打蜡工具混在 /ski/accessories/ 里，归“其他配件”）。

robots.txt：`User-agent: *` 只禁止 /search.php、/remote.php、购物车/账户页和带 `_bc_fsnf=1`
的筛选链接；分类页（含 ?limit=&page=）与商品页均允许。
"""
from __future__ import annotations

import html as htmllib
import json
import re
from collections.abc import Iterator
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..categories import APPAREL_ORDER, BY_ID, norm_size
from .base import NOT_SKI_RE, Adapter, RawProduct, SizeOption, to_float

DEFAULT_CATEGORIES: dict[str, list] = {
    "ski": ["/ski/skis/"],
    "binding": ["/ski/ski-bindings/"],
    "boot": ["/ski/ski-boots/"],
    "pole": ["/ski/poles/"],
    "helmet": ["/ski/helmets/"],
    "goggle": ["/ski/goggles/"],
    "jacket": ["/ski/jackets/"],
    "pants": ["/ski/pants/", "/ski/bibs/"],
    "suit": ["/ski/ski-suits/"],
    "midlayer": ["/ski/mid-layers/", "/ski/sweaters/"],
    "baselayer": ["/ski/baselayers/"],
    "glove": ["/ski/gloves/", "/ski/mittens/"],
    "sock": ["/ski/socks/"],
    "facewear": ["/ski/neck-gaiters/", "/snowboard/facemasks/"],   # 滑雪区只有围脖；面罩页是和单板共用的
    "hat": ["/ski/hats/", "/ski/headbands/"],
    "backpack": ["/snowboard/backpacks/"],                          # 没有 /ski/ 背包页
    "bag": ["/ski/bags/"],
    "accessory": ["/ski/accessories/"],
}
# 没在 cfg 里写 max_detail_pages 时的默认详情页预算（每个分类每次抓取）
# 固定器：Peter Glenn 每个刹车宽度是单独的商品（只有颜色选项，宽度写在标题里）→ 详情页拿不到尺码，不抓；雪镜同理（均码）
DEFAULT_DETAIL_PAGES = {"ski": 80, "boot": 30, "binding": 0, "jacket": 25, "pants": 20, "glove": 15,
                        "helmet": 10, "goggle": 0, "midlayer": 10, "baselayer": 10, "suit": 5, "pole": 5}
PAGE_LIMIT = 100          # BigCommerce 分类页 ?limit= 上限就是 100
MAX_PAGES = 20            # 翻页保险丝

# 仅用于双板：base.NOT_SKI_RE 之外再排除的越野板固定器体系、套装、配件等
EXTRA_NOT_SKI_RE = re.compile(
    r"positrack|\bnnn\b|prolink|turnamic|harness|edgie|wedgie|chair ?lifter|backpack|"
    r"tip ?(?:lock|connector|clip)|\bpackage\b|snowshoe|water ?ski|wakeboard",
    re.I,
)
NON_ALPINE_CAT_RE = re.compile(r"cross[- ]?country|nordic|\bxc\b|water", re.I)
SEASON_SUFFIX_RE = re.compile(r"\s*-\s*(?:((?:19|20)\d\d)\s+(?:WINTER|SUMMER|SPRING|FALL)|(?:WINTER|SUMMER|SPRING|FALL)\s+((?:19|20)\d\d))\s*$", re.I)
PRICE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
SIZE_OPTION_RE = re.compile(r"size|length|brake|width|mondo", re.I)


def _first_price(text: str | None) -> float | None:
    if not text:
        return None
    m = PRICE_RE.search(text)
    return to_float(m.group(0)) if m else None


def _json_after(text: str, marker: str):
    """解析 `marker` 之后紧跟的一个 JSON 值（raw_decode，不怕后面跟着别的代码）。"""
    i = text.find(marker)
    if i < 0:
        return None
    i += len(marker)
    while i < len(text) and text[i] in " \t\r\n=":
        i += 1
    try:
        return json.JSONDecoder().raw_decode(text, i)[0]
    except ValueError:
        return None


def category_specs(cfg: dict, category: str, defaults: dict) -> list:
    """本分类要抓的页面/过滤条件：cfg["categories"]（列表）优先，否则用模块默认值。
    如果调度器把 retailer 级的 [retailers.categories.*] 整张表（dict）也合并进来了，就取其中本分类的 categories。"""
    c = cfg.get("categories")
    if isinstance(c, dict):
        sub = c.get(category)
        c = sub.get("categories") if isinstance(sub, dict) else None
    if isinstance(c, list) and c:
        return c
    return list(defaults.get(category) or [])


def gender_from_categories(paths: list[str]) -> str | None:
    tops = {p.split("/")[0].strip().lower() for p in paths if p.strip()}
    g = set()
    for t in tops:
        if t.startswith(("women", "woman", "ladies")):
            g.add("women")
        elif t.startswith(("men", "man")):
            g.add("men")
        elif t.startswith(("kid", "boy", "girl", "junior", "youth", "child")):
            g.add("kids")
    if {"men", "women"} <= g:
        return "unisex"
    for k in ("men", "women", "kids"):
        if k in g:
            return k
    return None


def size_sort_key(category: str, s: SizeOption):
    """长度按厘米、服装按 XS…3XL、鞋码等按数字排序。"""
    if s.cm:
        return (0, s.cm, s.label)
    n = norm_size(category, s.label)
    if n in APPAREL_ORDER:
        return (1, APPAREL_ORDER.index(n), s.label)
    m = re.search(r"\d+(?:\.\d+)?", s.label)
    return (2, float(m.group(0)), s.label) if m else (3, 0.0, s.label)


def size_label_ok(category: str, label: str) -> bool:
    """均码类商品（雪镜、帽子、围脖、背包…）只接受像尺码的标签（One Size、S/M…），不把镜片/颜色名当尺码。"""
    cat = BY_ID.get(category)
    if cat is not None and cat.sizing == "one":
        return norm_size(category, label) is not None
    return True


def condition_from_title(title: str) -> str:
    t = title.lower()
    if re.search(r"\bdemo\b", t):
        return "demo"
    if re.search(r"\bused\b|pre-?owned|\brental\b", t):
        return "used"
    if re.search(r"\bblem\b|b-grade|scratch(?:ed)? (?:and|&|n) dent", t):
        return "blem"
    return "new"


def clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", htmllib.unescape(title or "")).strip()
    # "K2 Mindbender 88 Ski (Men's) - 2027 WINTER" -> "K2 Mindbender 88 Ski (Men's) 2027"
    return SEASON_SUFFIX_RE.sub(lambda m: " " + (m.group(1) or m.group(2)), title)


class PeterGlennAdapter(Adapter):
    platform = "bigcommerce"

    def fetch(self) -> Iterator[RawProduct]:
        cats = category_specs(self.cfg, self.category, DEFAULT_CATEGORIES)
        if not cats:
            self.log(f"[{self.rid}] no category pages for '{self.category}' — skipped")
            return
        products: dict[str, RawProduct] = {}
        for cat in cats:
            path, forced_gender = (cat.get("path"), cat.get("gender")) if isinstance(cat, dict) else (cat, None)
            n = 0
            for p in self._crawl_category(path):
                n += 1
                if forced_gender:
                    p.gender_hint = forced_gender if not p.gender_hint or p.gender_hint == forced_gender else "unisex"
                old = products.get(p.external_id)
                if old is None:
                    products[p.external_id] = p
                elif p.gender_hint and p.gender_hint != old.gender_hint:
                    old.gender_hint = p.gender_hint if not old.gender_hint else "unisex"
            self.log(f"[{self.rid}] {self.category} {path}: {n}")

        # 只给打折商品抓详情页（折扣越大越优先），补全尺码
        budget = int(self.cfg.get("max_detail_pages", DEFAULT_DETAIL_PAGES.get(self.category, 0)))
        discounted = [p for p in products.values() if p.compare_at and p.price]
        discounted.sort(key=lambda p: p.price / p.compare_at)
        for p in discounted[:budget]:
            try:
                self._fill_detail(p)
            except Exception as e:  # 单个详情页失败不影响整体
                self.log(f"[{self.rid}] detail failed {p.url}: {e}")
        if len(discounted) > budget:
            self.log(f"[{self.rid}] {self.category}: {len(discounted) - budget} discounted items left without sizes "
                     f"(max_detail_pages={budget})")
        yield from products.values()

    # ---------- 分类页 ----------
    def _crawl_category(self, path: str) -> Iterator[RawProduct]:
        seen: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            url = urljoin(self.base_url + "/", path.lstrip("/"))
            url += ("&" if "?" in url else "?") + f"limit={PAGE_LIMIT}&page={page}"
            doc = HTMLParser(self.fetcher.get_html(url))
            cards = doc.css("article.card[data-entity-id]")
            new = 0
            for card in cards:
                pid = (card.attributes.get("data-entity-id") or "").strip()
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                new += 1
                p = self._parse_card(card)
                if p:
                    yield p
            if len(cards) < PAGE_LIMIT or new == 0:
                break

    def _keep(self, title: str, cats: list[str]) -> bool:
        if self.category != "ski":
            return self.wanted(title)
        # 双板保持原有规则（NOT_SKI_RE + EXTRA）；不用 base.wanted()，它会把 “QST 92 w/ M10 Bindings” 这类板+固定器套装误判成固定器
        if NOT_SKI_RE.search(title) or EXTRA_NOT_SKI_RE.search(title):
            return False
        return not (cats and any(NON_ALPINE_CAT_RE.search(c) for c in cats)
                    and not any("alpine" in c.lower() for c in cats))

    def _parse_card(self, card) -> RawProduct | None:
        a = card.attributes
        pid = (a.get("data-entity-id") or "").strip()
        raw_title = a.get("data-name") or ""
        if not pid or not raw_title:
            return None
        title = clean_title(raw_title)
        cats = [c.strip() for c in htmllib.unescape(a.get("data-product-category") or "").split(",") if c.strip()]
        if not self._keep(title, cats):
            return None

        link = card.css_first("h3.card-title a") or card.css_first("a[href]")
        fig = card.css_first("figure.card-figure")
        href = (link.attributes.get("href") if link else None) or (fig.attributes.get("data-url") if fig else None)
        if not href:
            return None
        url = urljoin(self.base_url + "/", href)

        price = to_float((a.get("data-product-price") or "").strip())
        node = card.css_first("[data-product-price-without-tax]")
        if price is None and node:
            price = _first_price(node.text())
        compare = None
        for sel in ("[data-product-rrp-price-without-tax]", "[data-product-non-sale-price-without-tax]"):
            n = card.css_first(sel)
            v = _first_price(n.text()) if n else None
            if v and price and v > price + 0.009 and (compare is None or v > compare):
                compare = v

        img = card.css_first("figure.card-figure img") or card.css_first("img")
        image = None
        if img:
            image = img.attributes.get("src") or img.attributes.get("data-src")
            if image and image.startswith("data:"):
                image = img.attributes.get("data-src")

        text = card.text().lower()
        available = not ("out of stock" in text or "sold out" in text)
        leaf_types = sorted({c.split("/")[-1].strip().lower() for c in cats if c.count("/") >= 2})
        return RawProduct(
            retailer_id=self.rid,
            external_id=pid,
            url=url,
            title=title,
            price=price,
            compare_at=compare,
            currency="USD",
            vendor=(a.get("data-product-brand") or "").strip() or None,
            product_type=None,  # 网站没有单独的商品类型字段（分类信息在 tags 的分类路径里）
            tags=cats,
            image=image,
            available=available,
            gender_hint=gender_from_categories(cats),
            type_hints=[t for t in leaf_types if t not in ("alpine", "skis")] if self.category == "ski" else [],
            condition_hint=condition_from_title(title),
            extra={"raw_title": htmllib.unescape(raw_title).strip()},
            category=self.category,
        )

    # ---------- 商品页（尺码） ----------
    def _fill_detail(self, p: RawProduct) -> None:
        text = self.fetcher.get_html(p.url)
        sizes: dict[str, SizeOption] = {}
        any_variant_in_stock = None
        vd = _json_after(text, "window.variantData")
        for edge in (vd or {}).get("edges", []) if isinstance(vd, dict) else []:
            node = edge.get("node") or {}
            avail = bool((node.get("inventory") or {}).get("isInStock", True)) and node.get("isPurchasable", True) is not False
            any_variant_in_stock = bool(any_variant_in_stock) or avail
            label = None
            for opt in (node.get("options") or {}).get("edges", []):
                o = opt.get("node") or {}
                vals = [v.get("node", {}).get("label") for v in (o.get("values") or {}).get("edges", [])]
                vals = [v for v in vals if v]
                if vals and SIZE_OPTION_RE.search(o.get("displayName") or ""):
                    label = vals[0]
                    break
            if not label or not size_label_ok(self.category, str(label)):
                continue
            prices = node.get("prices") or {}
            base = to_float((prices.get("basePrice") or {}).get("value"))
            sale = _first_price((prices.get("salePrice") or {}).get("formatted")) or to_float((prices.get("salePrice") or {}).get("value"))
            cur = sale or base
            saved = to_float((prices.get("saved") or {}).get("value"))
            cmp_ = round(cur + saved, 2) if cur and saved and saved > 0.009 else (base if base and cur and base > cur else None)
            label = str(label).strip()
            so = SizeOption(label=label, cm=self.size_cm(label), available=avail, price=cur, compare_at=cmp_)
            prev = sizes.get(label)  # 服装：同一尺码多个颜色 → 有货优先、同样有货取低价
            if prev is None or (so.available and not prev.available) or (
                    so.available == prev.available and cur and prev.price and cur < prev.price):
                sizes[label] = so

        bc = _json_after(text, "BCData")
        attrs = (bc or {}).get("product_attributes") or {} if isinstance(bc, dict) else {}
        if not sizes:  # 后备：HTML 里名为 Size 的单选框/下拉框 + BCData 的有货属性值列表
            in_stock = {int(x) for x in attrs.get("in_stock_attributes") or [] if str(x).isdigit()}
            doc = HTMLParser(text)
            for field in doc.css("[data-product-attribute]"):
                fname = field.attributes.get("field-name") or ""
                label_node = field.css_first("label.form-label")
                if not SIZE_OPTION_RE.search(fname + " " + (label_node.text() if label_node else "")):
                    continue
                opts = [(i.attributes.get("aria-label") or i.attributes.get("data-js-aria-label"), i.attributes.get("value") or "")
                        for i in field.css("input.form-radio")]
                opts += [(o.text().strip(), o.attributes.get("value") or "")
                         for o in field.css("option[data-product-attribute-value]")]
                for label, val in opts:
                    if label and label not in sizes and size_label_ok(self.category, label):
                        avail = (int(val) in in_stock) if (in_stock and val.isdigit()) else True
                        sizes[label] = SizeOption(label=label, cm=self.size_cm(label), available=avail)

        if sizes:
            p.sizes = sorted(sizes.values(), key=lambda s: size_sort_key(self.category, s))
            live = [s for s in p.sizes if s.available and s.price]
            if live:
                p.price = min(s.price for s in live)
                cmp_vals = [s.compare_at for s in live if s.compare_at]
                if cmp_vals and max(cmp_vals) > p.price:
                    p.compare_at = max(cmp_vals)
            p.available = any(s.available for s in p.sizes)
        elif any_variant_in_stock is not None:
            p.available = any_variant_in_stock
        elif attrs:
            p.available = bool(attrs.get("instock", True)) and attrs.get("purchasable", True) is not False
