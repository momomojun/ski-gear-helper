"""Buckman's Ski & Snowboard (buckmans.com) —— ASP.NET "OSCAR" 电商平台；OscarAdapter 是平台通用实现，
Skis.com 用的是同一个平台（见 skiscom.py，那里配置了全部装备分类）。

注意：Buckman's 与 Skis.com 是同一家公司、同一套商品库（商品 ID 和价格相同），registry 里 Buckman's 默认停用；
这里只保留它的双板分类。

抓取思路：
1. 列表：按性别子分类抓（/products/<分类ID>/<slug>?p=N，每页 48 个），分类配置里带 gender / type / condition。
   卡片 div.OSCAR_item：商品链接（含商品 ID）、标题（h2 或 h3）、lblPrice（现价）、lblPriceReference（划线原价）、图片。
   同一商品出现在男款和女款分类 → unisex。
2. 尺码：列表页没有尺码，只对打折商品（compare_at 非空）按折扣从大到小抓商品页，最多 max_detail_pages 个（每个分类单独计）。
   商品页有完整的 JSON-LD ProductGroup：hasVariant[] → 每个变体的 size（雪板长度 / 鞋码 / S-XL / 刹车宽度）、sku、价格、
   StrikethroughPrice（原价）、availability（有货/缺货）、itemCondition；另有 brand、完整分类路径、描述。
   服装同一尺码多个颜色 → 合并（任一颜色有货即有货，取最低价）。

robots.txt：`User-agent: *` 只禁止 /store/checkout、/store/account/secure、/store/items.aspx?q=*（站内搜索）等；
分类页和商品页允许。声明了 crawl-delay: 2 —— 这里每个请求间隔至少 2 秒（cfg["min_interval"] 可调大）。
"""
from __future__ import annotations

import html as htmllib
import json
import re
from collections.abc import Iterator
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..categories import APPAREL_ORDER, BY_ID, norm_size
from ..normalize import fold
from .base import NOT_SKI_RE, Adapter, RawProduct, SizeOption, to_float


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", re.sub(r"['’.]", "", fold(s).lower())).strip("-")


def site_category(href: str) -> str | None:
    """商品网址 /product/<网站分类>/<id>/<名字> 里的网站分类（"goggles-unisex" → "goggles unisex"），用作分类证据。"""
    m = re.search(r"/product/([a-z0-9-]+)/\d+", href or "", re.I)
    return m.group(1).replace("-", " ") if m else None


def vendor_from_slug(href: str, title: str) -> str | None:
    """网址最后一段 = “品牌-标题”。标题里没写品牌（"Men's Stormrider 95 Skis" ← stockli-mens-stormrider-95-skis）
    或写错（"Saloomon"、"Oakely"）时，网址开头多出来的那段就是品牌；标题本身以品牌开头时网址里不会重复，返回 None。"""
    seg = re.sub(r"-\d+$", "", (href or "").split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1].lower())
    tslug = _slugify(title)
    if tslug and seg.endswith(tslug) and len(seg) > len(tslug) + 1:
        prefix = seg[: -len(tslug)].strip("-")
        if prefix and prefix.count("-") <= 2:
            return prefix.replace("-", " ")
    return None

# 仅用于双板：base.NOT_SKI_RE 之外再排除的
EXTRA_NOT_SKI_RE = re.compile(
    r"positrack|\bnnn\b|prolink|turnamic|harness|edgie|wedgie|chair ?lifter|backpack|"
    r"tip ?(?:lock|connector|clip)|\bpackage\b|snowshoe|water ?ski|wakeboard|teaching",
    re.I,
)
PRICE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
LD_RE = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)
AVAILABLE_OFFERS = ("instock", "limitedavailability", "onlineonly")
SIZE_TOKEN_RE = re.compile(r"\d{2,3}(?:\.\d)?(?:cm|mm)?|\d{1,2}(?:\.5)?|x{0,3}[sml]|x{1,3}l|\d?xl|os|one ?size", re.I)
MAX_PAGES = 30
# 雪镜的变体只有颜色/镜片（均码）→ 详情页拿不到尺码，默认不抓
DEFAULT_DETAIL_PAGES = {"ski": 80, "boot": 30, "binding": 15, "jacket": 25, "pants": 20, "glove": 15,
                        "helmet": 10, "goggle": 0, "midlayer": 10, "baselayer": 10, "suit": 5, "pole": 5}


def _first_price(text: str | None) -> float | None:
    if not text:
        return None
    m = PRICE_RE.search(text)
    return to_float(m.group(0)) if m else None


def condition_from_title(title: str) -> str:
    t = title.lower()
    if re.search(r"\bdemo\b", t):
        return "demo"
    if re.search(r"\bused\b|pre-?owned|\brental\b", t):
        return "used"
    if re.search(r"\bblem\b|b-grade", t):
        return "blem"
    return "new"


def resolve_gender(gs: set[str]) -> str | None:
    if {"men", "women"} <= gs:
        return "unisex"
    for k in ("men", "women", "kids", "unisex"):
        if k in gs:
            return k
    return None


def size_sort_key(category: str, s: SizeOption):
    if s.cm:
        return (0, s.cm, s.label)
    n = norm_size(category, s.label)
    if n in APPAREL_ORDER:
        return (1, APPAREL_ORDER.index(n), s.label)
    m = re.search(r"\d+(?:\.\d+)?", s.label)
    return (2, float(m.group(0)), s.label) if m else (3, 0.0, s.label)


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


def size_label_ok(category: str, label: str) -> bool:
    """均码类商品（雪镜、帽子、围脖、背包…）只接受像尺码的标签（One Size、S/M…），不把镜片/颜色名当尺码。"""
    cat = BY_ID.get(category)
    if cat is not None and cat.sizing == "one":
        return norm_size(category, label) is not None
    return True


def _strip_html(s: str | None) -> str | None:
    if not s:
        return None
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s))).strip() or None


class OscarAdapter(Adapter):
    """OSCAR 平台通用实现；子类只需给出 DEFAULT_CATEGORIES = {分类: [{path, gender?, type?, condition?}, ...]}。"""

    platform = "oscar"
    DEFAULT_CATEGORIES: dict[str, list[dict]] = {}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.min_interval = float(self.cfg.get("min_interval", 2.0))  # robots.txt: crawl-delay: 2

    def _get(self, url: str) -> str:
        return self.fetcher.get_html(url, min_interval=self.min_interval)

    def fetch(self) -> Iterator[RawProduct]:
        cats = category_specs(self.cfg, self.category, self.DEFAULT_CATEGORIES)
        if not cats:
            self.log(f"[{self.rid}] no category pages for '{self.category}' — skipped")
            return
        products: dict[str, RawProduct] = {}
        genders: dict[str, set[str]] = {}
        for cat in cats:
            cat = {"path": cat} if isinstance(cat, str) else cat
            n = 0
            for p in self._crawl_category(cat["path"]):
                n += 1
                if cat.get("gender"):
                    genders.setdefault(p.external_id, set()).add(cat["gender"])
                old = products.setdefault(p.external_id, p)
                if cat.get("type") and cat["type"] not in old.type_hints:
                    old.type_hints.append(cat["type"])
                if cat.get("condition") and old.condition_hint == "new":
                    old.condition_hint = cat["condition"]
            self.log(f"[{self.rid}] {self.category} {cat['path']}: {n}")
        for pid, p in products.items():
            p.gender_hint = resolve_gender(genders.get(pid, set()))

        budget = int(self.cfg.get("max_detail_pages", DEFAULT_DETAIL_PAGES.get(self.category, 0)))
        discounted = sorted((p for p in products.values() if p.compare_at and p.price),
                            key=lambda p: p.price / p.compare_at)
        for p in discounted[:budget]:
            try:
                self._fill_detail(p)
            except Exception as e:
                self.log(f"[{self.rid}] detail failed {p.url}: {e}")
        if len(discounted) > budget:
            self.log(f"[{self.rid}] {self.category}: {len(discounted) - budget} discounted items left without sizes "
                     f"(max_detail_pages={budget})")

        noprice = [p for p in products.values() if p.price is None]
        if noprice:
            self.log(f"[{self.rid}] {self.category}: skipped {len(noprice)} items without a listed price")
        yield from (p for p in products.values() if p.price is not None)

    # ---------- 列表页 ----------
    def _crawl_category(self, path: str) -> Iterator[RawProduct]:
        seen: set[str] = set()
        url = urljoin(self.base_url + "/", path.lstrip("/"))
        for page in range(1, MAX_PAGES + 1):
            text = self._get(url)
            doc = HTMLParser(text)
            new = 0
            for item in doc.css("div.OSCAR_item"):
                p = self._parse_item(item)
                if p and p.external_id not in seen:
                    seen.add(p.external_id)
                    new += 1
                    yield p
            nxt = self._next_url(doc, text, url, page)
            if not nxt or new == 0:
                break
            url = nxt

    def _next_url(self, doc: HTMLParser, text: str, url: str, page: int) -> str | None:
        for a in doc.css("a[id$='lnkNext']"):
            href = a.attributes.get("href")
            if href and "javascript:" not in href:
                return urljoin(url, htmllib.unescape(href))
        base = url.split("?", 1)[0]
        path = base.split("://", 1)[-1].split("/", 1)[-1]
        m = re.search(r'href="(/%s\?(?:[^"]*&(?:amp;)?)?p=%d)"' % (re.escape(path), page + 1), text)
        return urljoin(url, htmllib.unescape(m.group(1))) if m else None

    def _keep(self, title: str) -> bool:
        if self.category == "ski":
            # 双板保持原有规则（NOT_SKI_RE + EXTRA）；不用 base.wanted()，它会把 “QST 92 w/ M10 Bindings” 这类板+固定器套装误判成固定器
            return not NOT_SKI_RE.search(title) and not EXTRA_NOT_SKI_RE.search(title)
        return self.wanted(title)

    def _parse_item(self, item) -> RawProduct | None:
        link = None
        for a in item.css("a[href]"):
            if "/product/" in (a.attributes.get("href") or ""):
                link = a
                break
        if link is None:
            return None
        href = link.attributes["href"].split("#", 1)[0]
        m = re.search(r"/product/(?:[^/]+/)?(\d+)(?:/|$)", href)
        if not m:
            return None
        pid = m.group(1)

        flags: list[str] = []
        head = item.css_first("h2") or item.css_first("h3")
        title = ""
        if head is not None:
            for fl in head.css("span[class*='flag']"):
                if fl.text().strip() and fl.text().strip() not in flags:
                    flags.append(fl.text().strip())
                fl.decompose()
            title = head.text()
        if not title.strip():
            img = item.css_first("img")
            title = htmllib.unescape(img.attributes.get("alt") or "") if img else ""
        title = re.sub(r"\s+", " ", htmllib.unescape(title)).strip()
        if not title or not self._keep(title):
            return None

        price = compare = None
        box = item.css_first(".OSCAR_price")
        if box is not None:
            for sp in box.css("span[id]"):
                sid = sp.attributes.get("id") or ""
                if sid.endswith("lblPriceReference"):
                    compare = _first_price(sp.text())
                elif sid.endswith("lblPrice"):
                    price = _first_price(sp.text())
            if price is None:
                price = _first_price(box.text())
        if not (compare and price and compare > price + 0.009):
            compare = None

        img = item.css_first("img.image") or item.css_first("img")
        image = urljoin(self.base_url + "/", img.attributes.get("src")) if img and img.attributes.get("src") else None
        return RawProduct(
            retailer_id=self.rid,
            external_id=pid,
            url=urljoin(self.base_url + "/", href),
            title=title,
            price=price,
            compare_at=compare,
            currency="USD",
            vendor=vendor_from_slug(href, title),
            product_type=site_category(href),
            tags=flags,
            image=image,
            available=True,
            condition_hint=condition_from_title(title),
            extra={"flags": flags, "price_from": bool(box is not None and "starting at" in box.text().lower())},
            category=self.category,
        )

    # ---------- 商品页（JSON-LD） ----------
    def _fill_detail(self, p: RawProduct) -> None:
        text = self._get(p.url)
        group = None
        for m in LD_RE.finditer(text):
            try:
                d = json.loads(m.group(1))
            except ValueError:
                continue
            for obj in d if isinstance(d, list) else [d]:
                if isinstance(obj, dict) and obj.get("@type") in ("ProductGroup", "Product"):
                    group = obj
                    break
            if group:
                break
        if not group:
            return
        brand = group.get("brand")
        if isinstance(brand, dict) and brand.get("name"):
            p.vendor = brand["name"].strip()
        if group.get("category"):
            p.tags = list(dict.fromkeys(p.tags + [c.strip() for c in str(group["category"]).split(" / ") if c.strip()]))
        p.body_text = _strip_html(group.get("description")) or p.body_text
        if group.get("productGroupID"):
            p.extra["style"] = group["productGroupID"]

        variants = group.get("hasVariant") or ([group] if group.get("offers") else [])
        sizes: dict[str, SizeOption] = {}
        conds = set()
        any_avail = None
        for v in variants:
            offers = v.get("offers") or []
            offer = offers[0] if isinstance(offers, list) and offers else offers if isinstance(offers, dict) else {}
            avail = str(offer.get("availability") or "").rsplit("/", 1)[-1].lower() in AVAILABLE_OFFERS
            any_avail = bool(any_avail) or avail
            cond = str(offer.get("itemCondition") or "").rsplit("/", 1)[-1].lower()
            if cond:
                conds.add(cond)
            label = str(v.get("size") or "").strip()
            if not label and v is not group:  # 有些变体只在名字开头写尺码："163 Volkl Peregrine ..."
                tok = str(v.get("name") or "").split(" ", 1)[0]
                label = tok if SIZE_TOKEN_RE.fullmatch(tok) else ""
            if not label or not size_label_ok(self.category, label):
                continue
            price = to_float(offer.get("price"))
            spec = offer.get("PriceSpecification") or offer.get("priceSpecification") or {}
            ref = None
            for s in spec if isinstance(spec, list) else [spec]:
                if isinstance(s, dict) and "strikethrough" in str(s.get("priceType", "")).lower():
                    ref = to_float(s.get("price"))
            so = SizeOption(label=label, cm=self.size_cm(label), available=avail, price=price,
                            compare_at=ref if (ref and price and ref > price + 0.009) else None)
            prev = sizes.get(label)
            if prev is None or (so.available and not prev.available) or (
                    so.available == prev.available and price and prev.price and price < prev.price):
                sizes[label] = so
        if p.condition_hint in (None, "new"):
            if any("used" in c or "refurbished" in c for c in conds):
                p.condition_hint = "used"
            elif any("damaged" in c for c in conds):
                p.condition_hint = "blem"
        if not sizes:
            if any_avail is not None:
                p.available = any_avail
            return
        p.sizes = sorted(sizes.values(), key=lambda s: size_sort_key(self.category, s))
        p.available = any(s.available for s in p.sizes)
        live = [s for s in p.sizes if s.available and s.price]
        if live:
            p.price = min(s.price for s in live)
            cmps = [s.compare_at for s in live if s.compare_at]
            p.compare_at = max(cmps) if cmps and max(cmps) > p.price else p.compare_at


class BuckmansAdapter(OscarAdapter):
    # 已停用（与 Skis.com 重复）；只保留双板分类
    DEFAULT_CATEGORIES = {
        "ski": [
            {"path": "/products/1002/ski-mens-all-mountain-skis-with-bindings", "gender": "men"},
            {"path": "/products/1003/ski-mens-all-mountain-freeride-skis", "gender": "men"},
            {"path": "/products/1005/ski-womens-all-mountain-skis-with-bindings", "gender": "women"},
            {"path": "/products/1006/ski-womens-all-mountain-freeride-skis", "gender": "women"},
            {"path": "/products/1007/ski-kids-skis", "gender": "kids"},
            {"path": "/products/1004/ski-park-and-pipe-skis", "type": "park"},
            {"path": "/products/1944/ski-race-skis", "type": "race"},
        ],
    }
