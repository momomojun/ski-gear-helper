"""Running Warehouse（www.runningwarehouse.com，加州 San Luis Obispo，Tennis Warehouse 同一家公司）适配器。

多品牌跑步专卖店，越野跑鞋品类很全（Hoka、Saucony、Merrell 自家官网都有反爬，在这里能拿到价格）。
自建平台，没有公开 JSON 接口，全部解析服务端渲染的 HTML：

1. 分类列表页（一页就是全部商品，不翻页，约 1.2 MB）：
       /trailshoesmen.html   /trailshoeswomen.html
   每个商品卡片 `div.cattable-wrap-cell.gtm_impression`，一个颜色一个卡片（data-code = 款式+颜色代码，如 TO2TRM1）：
   - data-gtm_impression_name / _brand / _price（当前售价）
   - `.cattable-wrap-cell-info-name`（"Topo Athletic Traverse 2"）、`.cattable-wrap-cell-info-sub`（"Men's Shoes - Black/Grey"）
   - 打折商品：`.cattable-wrap-cell-info-price.is-sale` 里是现价，`.cattable-wrap-cell-info-price-msrp` 是划线原价；
     `.cattable-wrap-cell-info-flag.sale` 写着 "Clearance -30%"
   - 中性款（Unisex Shoes）同时出现在男女两个列表里
2. 商品页 /<Name>/descpage-<CODE>.html：`tr.js-ordering-subproduct` 每行一个尺码 × 楦宽，
   data-list-price 是原价，`.js-ordering-price` 是售价，`.js-ordering-available` 是库存：
   数字（"1"、"4+"）= 现货；"15 Days"、"11/13" 这类是到货时间（行上带 data-stock-notify），不算有货。
   尺码样式名写明码制："Men's Shoe US Sizes" / "Women's Shoe US Size" / "Unisex Shoe US Sizes 1.0"（值是 "M7.5/W8.5"）。
   列表页没有尺码，所以只对一部分商品抓商品页（max_detail_pages，打折幅度大的优先）。

robots.txt：`User-agent: *` 只禁止 /zzz/、/mailings/、/search-*.html、/SearchResults/；没有 Crawl-delay（只对 bing 等写了 4 秒）。

配置（config/categories.toml）：
  [runningwarehouse.trail_shoe]
  pages = [{ path = "/trailshoesmen.html", gender = "men" }, { path = "/trailshoeswomen.html", gender = "women" }]
  max_detail_pages = 80
"""
from __future__ import annotations

import html as htmllib
import re
from collections.abc import Iterator

from ..net import BlockedError, FetchError
from .base import Adapter, RawProduct, SizeOption, to_float

_CARD_SPLIT = re.compile(r'<div class="cattable__item-group"')
_ROW = re.compile(r'<tr\s+class="js-ordering-subproduct[^"]*"(.*?)>(.*?)</tr>', re.S)
_STYLE = re.compile(r'js-ordering-style-name style">([^<]*)</strong>\s*:\s*'
                    r'<span class="js-ordering-style-item-name styleitem">([^<]*)<', re.S)
_DEFAULT_DETAIL = 80


def _text(s: str | None) -> str:
    return " ".join(htmllib.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def _attr(block: str, name: str) -> str | None:
    m = re.search(rf'{name}="([^"]*)"', block)
    return htmllib.unescape(m.group(1)).strip() if m else None


def _div(block: str, cls: str) -> str | None:
    m = re.search(rf'<div class="{cls}[^"]*">(.*?)</div>', block, re.S)
    return _text(m.group(1)) if m else None


def parse_listing(page: str) -> list[dict]:
    """列表页 → 卡片列表（每个颜色一个）。没有 data-code 的是品牌故事卡片，跳过。"""
    cards = []
    for block in _CARD_SPLIT.split(page)[1:]:
        code = _attr(block, "data-gtm_impression_code") or _attr(block, "data-code")
        if not code or "gtm_impression" not in block:
            continue
        url = re.search(r'href="\s*(https://[^"\s]+/descpage-[^"\s]+\.html)\s*"', block)
        name = _div(block, "cattable-wrap-cell-info-name") or ""
        sub = _div(block, "cattable-wrap-cell-info-sub") or ""
        kind, _, color = sub.partition(" - ")
        price_div = re.search(r'<div class="cattable-wrap-cell-info-price[^"]*">(.*?)</div>', block, re.S)
        price = msrp = None
        if price_div:
            body = price_div.group(1)
            m = re.search(r'cattable-wrap-cell-info-price-msrp">(.*?)</span>\s*\*?\s*</span>', body, re.S)
            if m:
                msrp = to_float(re.sub(r"[^\d.]", "", _text(m.group(1))))
                body = body[:m.start()]
            price = to_float(re.sub(r"[^\d.]", "", _text(body)))
        price = price or to_float(_attr(block, "data-gtm_impression_price"))
        flag = re.search(r'cattable-wrap-cell-info-flag sale">([^<]*)<', block)
        off = re.search(r"-(\d+)%", flag.group(1)) if flag else None
        img = re.search(r'(?:data-)?src="(https://img\.runningwarehouse\.com/watermark/rs\.php\?path=[^"&]+)', block)
        cards.append({
            "code": code,
            "url": url.group(1) if url else None,
            "title": " ".join(x for x in (name, kind) if x).strip(),
            "color": color.strip() or None,
            "brand": _attr(block, "data-gtm_impression_brand"),
            "category": _attr(block, "data-gtm_impression_category"),
            "price": price,
            "msrp": msrp if msrp and price and msrp > price + 0.5 else None,
            "percent_off": int(off.group(1)) if off else None,
            "flag": _text(flag.group(1)) if flag else None,
            "image": (img.group(1) + "&nw=564") if img else None,
        })
    return cards


def _in_stock(attrs: str, avail: str) -> bool:
    """数字库存（"1"、"4+"）才算现货；"15 Days"/"11/13" 是到货时间，行上会带 data-stock-notify。"""
    return "data-stock-notify" not in attrs and bool(re.fullmatch(r"\d+\+?", avail)) and avail != "0"


def size_label(styles: list[tuple[str, str]]) -> tuple[str | None, str | None, str | None]:
    """尺码样式 → (label, 楦宽, 中性款的女码)。
    "Men's Shoe US Sizes: 8.0" + "Men's Shoe Widths: D" → "US Men's 8.0 D"；
    中性款 "M7.5/W8.5" 只用网站写的男码（"US Men's 7.5"），不做男女码换算，女码另外记下。"""
    size = width = alt = None
    system = basis = ""
    for name, value in styles:
        value = value.strip()
        if re.search(r"width", name, re.I):
            width = value or None
            continue
        if re.search(r"size", name, re.I):
            size = value
            system = "US " if re.search(r"\bUS\b", name) else ""
            basis = ("Women's " if re.search(r"women", name, re.I) else "Men's " if re.search(r"\bmen", name, re.I)
                     else "")
    if not size:
        return None, width, None
    m = re.fullmatch(r"M\s*(\d+(?:\.\d)?)\s*(?:/\s*W\s*(\d+(?:\.\d)?))?", size, re.I)
    if m:
        size, basis, alt = m.group(1), "Men's ", m.group(2)
    else:
        w = re.fullmatch(r"W\s*(\d+(?:\.\d)?)", size, re.I)
        if w:
            size, basis = w.group(1), "Women's "
    label = f"{system}{basis}{size}" + (f" {width}" if width else "")
    return label, width, alt


def parse_detail(page: str) -> dict:
    """商品页 → 每个尺码行 + 简介。"""
    rows = []
    for m in _ROW.finditer(page):
        attrs, body = m.group(1), m.group(2)
        avail_m = re.search(r'js-ordering-available available">([^<]*)<', body)
        price_m = re.search(r'js-ordering-price price">([^<]*)<', body)
        styles = [(htmllib.unescape(a).strip(), htmllib.unescape(b).strip()) for a, b in _STYLE.findall(body)]
        label, width, alt = size_label(styles)
        if not label:
            continue
        avail = (avail_m.group(1) if avail_m else "").strip()
        rows.append({"sku": _attr(attrs, "data-code"), "label": label, "width": width, "women_size": alt,
                     "price": to_float(price_m.group(1)) if price_m else None,
                     "list": to_float(_attr(attrs, "data-list-price")),
                     "stock": avail, "available": _in_stock(attrs, avail)})
    desc = re.search(r'id="product_chars">\s*<h2>(.*?)</h2>', page, re.S)
    return {"rows": rows, "body": _text(desc.group(1))[:1000] if desc else None,
            "has_table": "js-ordering-table" in page}


class RunningWarehouseAdapter(Adapter):
    platform = "runningwarehouse"

    def fetch(self) -> Iterator[RawProduct]:
        cards: dict[str, dict] = {}
        genders: dict[str, set[str]] = {}
        for spec in self.cfg.get("pages") or []:
            path = spec["path"] if isinstance(spec, dict) else spec
            gender = spec.get("gender") if isinstance(spec, dict) else None
            found = parse_listing(self.fetcher.get_html(self.base_url + path))
            if not found:
                raise BlockedError(self.base_url + path, None, "列表页没有商品卡片（可能改版或被拦截）")
            for c in found:
                cards.setdefault(c["code"], c)
                if gender:
                    genders.setdefault(c["code"], set()).add(gender)
            self.log(f"[{self.rid}] {self.category} {path}: {len(found)} 个商品（颜色）")
        # 列表页没有尺码：打折幅度大的优先抓商品页，其余按列表顺序
        cap = int(self.cfg.get("max_detail_pages", _DEFAULT_DETAIL))
        order = sorted(cards, key=lambda k: -(cards[k]["percent_off"] or 0))
        detail_codes = set(order[:cap])
        n_detail = 0
        for code, card in cards.items():
            if not card["url"] or not card["price"] or not self.wanted(card["title"], card["category"]):
                continue
            det = None
            if code in detail_codes:
                try:
                    det = parse_detail(self.fetcher.get_html(card["url"]))
                    n_detail += 1
                except BlockedError:
                    raise
                except FetchError as e:
                    self.log(f"[{self.rid}] 商品页失败 {code}: {e}")
            yield self._to_raw(card, genders.get(code, set()), det)
        self.log(f"[{self.rid}] {self.category}: 抓了 {n_detail} 个商品页（上限 {cap}）")

    def _to_raw(self, card: dict, genders: set[str], det: dict | None) -> RawProduct:
        price, compare = card["price"], card["msrp"]
        sizes: list[SizeOption] = []
        extra: dict = {"style_code": card["code"], "flag": card["flag"], "percent_off": card["percent_off"],
                       "detail": det is not None, "colors": [card["color"]] if card["color"] else []}
        available = None
        if det and det["rows"]:
            for r in det["rows"]:
                p = r["price"] or price
                cmp_ = r["list"] if r["list"] and p and r["list"] > p + 0.5 else None
                sizes.append(SizeOption(label=r["label"], cm=self.size_cm(r["label"]), available=r["available"],
                                        price=p, compare_at=cmp_, color=card["color"],
                                        url=card["url"]))
            pool = [s for s in sizes if s.available and s.price] or [s for s in sizes if s.price]
            if pool:
                best = min(pool, key=lambda s: s.price)
                price, compare = best.price, best.compare_at
            available = any(s.available for s in sizes)
            extra["variants"] = det["rows"]
            if all("US" in s.label for s in sizes):
                extra["shoe_size_system"] = "US"
            widths = {r["width"] for r in det["rows"]}
            if widths - {None}:
                extra["shoe_widths"] = sorted(widths - {None})
            if len(widths) == 1 and None not in widths:
                extra["shoe_width"] = next(iter(widths))
            alts = {r["label"]: r["women_size"] for r in det["rows"] if r["women_size"]}
            if alts:
                extra["unisex_women_sizes"] = alts
        elif det is not None and det["has_table"]:
            available = False     # 有尺码表但一行都没有 = 全部售罄
        if "Unisex" in card["title"] or {"men", "women"} <= genders:
            gender = "unisex"
        else:
            gender = next(iter(genders)) if len(genders) == 1 else None
        return RawProduct(
            retailer_id=self.rid,
            external_id=card["code"],
            url=card["url"],
            title=card["title"],
            price=price,
            compare_at=compare if compare and price and compare > price + 0.5 else None,
            currency="USD",
            vendor=card["brand"] or None,
            product_type=card["category"],
            tags=[card["flag"]] if card["flag"] else [],
            image=card["image"],
            sizes=sizes,
            available=available,
            gender_hint=gender,
            body_text=det["body"] if det else None,
            category=self.category,
            extra=extra,
        )
