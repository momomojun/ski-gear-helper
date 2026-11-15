"""通用 Schema.org JSON-LD 适配器。

绝大多数现代独立电商（包括 BigCommerce、Magento 2、WooCommerce、自研 Next.js 等）
均在 HTML 中注入 `<script type="application/ld+json">`（标准 Schema.org Product / AggregateOffer）。
本适配器提供通用提取机制，支持：
1. 列表页直接内嵌 ItemList / Product JSON-LD 的解析
2. 分类页提取商品详情链接并抓取商品页 JSON-LD
3. 提取品牌、现价、划线原价、尺码库存变体（SizeOption）、图片与在售状态
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from ..models import RawProduct, SizeOption
from ..normalize import known_brand
from .base import NOT_SKI_RE, Adapter, parse_cm, to_float


def extract_json_ld(html: str) -> list[dict]:
    """提取页面中所有的 JSON-LD 对象（展平 @graph 和顶层列表）。"""
    tree = LexborHTMLParser(html)
    results: list[dict] = []
    for script in tree.css('script[type="application/ld+json"]'):
        text = script.text() or ""
        text = text.strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except Exception:
            # 尝试修复少量未转义字符
            try:
                cleaned = re.sub(r"[\x00-\x1f]+", " ", text)
                data = json.loads(cleaned)
            except Exception:
                continue

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    results.extend(_flatten_graph(item))
        elif isinstance(data, dict):
            results.extend(_flatten_graph(data))
    return results


def _flatten_graph(obj: dict) -> list[dict]:
    out: list[dict] = []
    if "@graph" in obj and isinstance(obj["@graph"], list):
        for item in obj["@graph"]:
            if isinstance(item, dict):
                out.append(item)
    else:
        out.append(obj)
    return out


def is_product_type(t: Any) -> bool:
    if isinstance(t, str):
        return t.lower() in ("product", "productmodel", "individualproduct", "productgroup")
    if isinstance(t, list):
        return any(is_product_type(x) for x in t)
    return False


def _extract_brand(item: dict) -> str | None:
    brand = item.get("brand")
    if isinstance(brand, dict):
        return brand.get("name") or brand.get("legalName")
    if isinstance(brand, str) and brand.strip():
        return brand.strip()
    return None


def _is_in_stock(avail: Any) -> bool:
    if not avail:
        return True  # 没写通常视为默认可售或按价格判断
    s = str(avail).lower()
    return "instock" in s or "in_stock" in s or "limitedavailability" in s or "presale" in s


def _extract_image(item: dict) -> str | None:
    img = item.get("image")
    if isinstance(img, str) and img.strip():
        return img.strip()
    if isinstance(img, list) and img:
        first = img[0]
        if isinstance(first, str):
            return first.strip()
        if isinstance(first, dict):
            return first.get("url")
    if isinstance(img, dict):
        return img.get("url")
    return None


def parse_product_json(item: dict, base_url: str = "", category: str = "ski") -> RawProduct | None:
    """将单个 Schema.org Product 字典解析为 RawProduct。"""
    if not is_product_type(item.get("@type")):
        return None

    title = item.get("name") or item.get("title")
    if not title or not isinstance(title, str):
        return None
    title = title.strip()

    pid = str(item.get("sku") or item.get("productID") or item.get("mpn") or title)
    vendor = _extract_brand(item)
    image = _extract_image(item)
    url = item.get("url") or ""
    if url and base_url and not url.startswith("http"):
        url = urljoin(base_url, url)

    description = item.get("description") or ""

    offers_data = item.get("offers")
    price: float | None = None
    compare_at: float | None = None
    available = True
    sizes: list[SizeOption] = []

    if isinstance(offers_data, dict):
        if is_product_type(offers_data.get("@type")):
            # 嵌套了另一层商品
            return parse_product_json(offers_data, base_url, category)

        sub_offers = offers_data.get("offers")
        if isinstance(sub_offers, list) and sub_offers:
            sizes = _offers_to_sizes(sub_offers, category)
            sub_prices = [s.price for s in sizes if s.price is not None]
            if sub_prices:
                price = min(sub_prices)

        if offers_data.get("@type") == "AggregateOffer":
            agg_price = to_float(offers_data.get("lowPrice") or offers_data.get("price"))
            if price is None or (agg_price and agg_price < price):
                price = agg_price
            high = to_float(offers_data.get("highPrice"))
            if high and price and high > price + 0.009:
                compare_at = high
        else:
            if price is None:
                price = to_float(offers_data.get("price"))
            available = _is_in_stock(offers_data.get("availability"))
            compare = to_float(offers_data.get("priceSpecification", {}).get("price") if isinstance(offers_data.get("priceSpecification"), dict) else None)
            if compare and price and compare > price + 0.009:
                compare_at = compare

    elif isinstance(offers_data, list):
        sizes = _offers_to_sizes(offers_data, category)
        valid_prices = [s.price for s in sizes if s.price]
        if valid_prices:
            price = min(valid_prices)
        else:
            for off in offers_data:
                if isinstance(off, dict) and off.get("price"):
                    p = to_float(off.get("price"))
                    if p:
                        price = min(price or p, p)
        available = any(s.available for s in sizes) if sizes else True

    # 支持 Schema.org ProductGroup (hasVariant: [Product, ...])
    has_variant = item.get("hasVariant")
    if isinstance(has_variant, list) and has_variant:
        variant_sizes: list[SizeOption] = []
        variant_prices: list[float] = []
        for v in has_variant:
            if not isinstance(v, dict):
                continue
            v_offers = v.get("offers")
            v_price = None
            v_avail = True
            v_compare = None
            if isinstance(v_offers, dict):
                v_price = to_float(v_offers.get("price"))
                v_avail = _is_in_stock(v_offers.get("availability"))
                v_comp = to_float(v_offers.get("priceSpecification", {}).get("price") if isinstance(v_offers.get("priceSpecification"), dict) else None)
                if v_comp and v_price and v_comp > v_price + 0.009:
                    v_compare = v_comp
            elif isinstance(v_offers, list) and v_offers:
                for off in v_offers:
                    if isinstance(off, dict):
                        p = to_float(off.get("price"))
                        if p:
                            v_price = min(v_price or p, p)
                        if "availability" in off:
                            v_avail = _is_in_stock(off.get("availability"))

            sz = v.get("size")
            col = v.get("color")
            lbl = ""
            if sz and col:
                lbl = f"{sz} ({col})"
            elif sz:
                lbl = str(sz)
            elif col:
                lbl = str(col)
            else:
                lbl = v.get("name") or v.get("sku") or ""
            lbl = str(lbl).strip()
            cm = parse_cm(lbl) if category in ("ski", "pole") else None
            if v_price is not None:
                variant_prices.append(v_price)
            variant_sizes.append(SizeOption(label=lbl or None, available=v_avail, cm=cm, price=v_price))
            if v_compare and (compare_at is None or v_compare > compare_at):
                compare_at = v_compare

        if variant_sizes:
            sizes = variant_sizes
            available = any(s.available for s in sizes)
        if variant_prices and (price is None or min(variant_prices) < price):
            price = min(variant_prices)
        if not image:
            for v in has_variant:
                if isinstance(v, dict):
                    v_img = _extract_image(v)
                    if v_img:
                        image = v_img
                        break

    if price is None:
        return None

    return RawProduct(
        retailer_id="",  # 外部填入
        external_id=pid,
        title=title,
        url=url or base_url,
        price=price,
        compare_at=compare_at,
        available=available,
        image=image,
        vendor=vendor,
        sizes=sizes,
        category=category,
        body_text=description[:2000] if description else None,
    )


def _offers_to_sizes(offers: list, category: str) -> list[SizeOption]:
    sizes: list[SizeOption] = []
    for off in offers:
        if not isinstance(off, dict):
            continue
        p = to_float(off.get("price"))
        avail = _is_in_stock(off.get("availability"))
        label = ""
        item_offered = off.get("itemOffered")
        if isinstance(item_offered, dict):
            sz = item_offered.get("size")
            col = item_offered.get("color")
            if sz and col:
                label = f"{sz} ({col})"
            elif sz:
                label = str(sz)
            elif col:
                label = str(col)
            elif item_offered.get("name"):
                label = str(item_offered.get("name"))
        if not label:
            label = off.get("name") or off.get("sku") or off.get("description") or ""
        label = str(label).strip()
        cm = parse_cm(label) if category in ("ski", "pole") else None
        sizes.append(SizeOption(label=label or None, available=avail, cm=cm, price=p))
    return sizes


def extract_cards_from_html(html: str, base_url: str, category: str, retailer_id: str) -> list[RawProduct]:
    """从分类页 DOM 商品卡片提取结构化商品数据 (支持 Sport Conrad、Glisshop 等独立站)。"""
    tree = LexborHTMLParser(html)
    products: list[RawProduct] = []

    # 1. Sport Conrad 风格: div.product-card
    sc_cards = tree.css("div.product-card")
    if sc_cards:
        for c in sc_cards:
            inputs = {inp.attributes.get("name"): inp.attributes.get("value") for inp in c.css("input")}
            title = inputs.get("bit-gtag-name") or inputs.get("product-name")
            if not title:
                continue
            price = to_float(inputs.get("bit-gtag-price"))
            if price is None:
                continue
            vendor = inputs.get("bit-gtag-brand")
            pid = str(inputs.get("bit-gtag-id") or inputs.get("lineItems[referencedId]") or title)
            a = c.css_first("a[href]")
            p_url = urljoin(base_url, a.attributes.get("href")) if a and a.attributes.get("href") else base_url
            img = c.css_first("img")
            image = (img.attributes.get("src") or img.attributes.get("data-src")) if img else None

            # 提取原价 (MSRP €xxx)
            compare_at = None
            msrp_m = re.search(r"MSRP\s*€?\s*([\d,.]+)", c.text() or "")
            if msrp_m:
                val = to_float(msrp_m.group(1).replace(",", "."))
                if val and val > price + 0.009:
                    compare_at = val

            raw = RawProduct(
                retailer_id=retailer_id,
                external_id=pid,
                title=title.strip(),
                url=p_url,
                price=price,
                compare_at=compare_at,
                available=True,
                image=image,
                vendor=vendor,
                sizes=[],
                category=category,
            )
            products.append(raw)
        return products

    # 2. Glisshop 风格: .product-item
    gs_items = tree.css(".product-item")
    if gs_items:
        for it in gs_items:
            a = it.css_first("a[href]")
            p_url = urljoin(base_url, a.attributes.get("href")) if a and a.attributes.get("href") else base_url
            img = it.css_first("img")
            image = (img.attributes.get("data-lazy") or img.attributes.get("src")) if img else None
            if image and "placeholder" in image:
                image = img.attributes.get("data-lazy") or None

            price_el = it.css_first(".price-value") or it.css_first(".c-price")
            price = None
            if price_el:
                price = to_float(re.sub(r"[^\d.]", "", price_el.text() or ""))
            if price is None:
                continue

            # 原价 / MSRP
            compare_at = None
            msrp_m = re.search(r"Recommended price\s*[^\d]*([\d.]+)", it.text() or "")
            if msrp_m:
                val = to_float(msrp_m.group(1))
                if val and val > price + 0.009:
                    compare_at = val

            lines = [line.strip() for line in (it.text() or "").splitlines() if line.strip()]
            vendor = None
            title = None
            for idx, line in enumerate(lines):
                if line.lower() in ("link", "view", "details") or line.endswith("reviews") or line.endswith("review"):
                    continue
                kb = known_brand(line)
                if kb:
                    vendor = kb
                    if idx + 1 < len(lines):
                        next_l = lines[idx + 1]
                        title = f"{vendor} {next_l}" if not next_l.lower().startswith(vendor.lower()) else next_l
                    break
                if vendor is None:
                    vendor = line
                elif title is None:
                    title = f"{vendor} {line}" if not line.lower().startswith(vendor.lower()) else line
                    break
            if not title:
                title = vendor or (a.attributes.get("title") if a else "Ski")

            sizes = []
            for s_el in it.css(".product-axis-item, li.d-lg-inline-block"):
                lbl = s_el.text().strip()
                if lbl and lbl.isdigit():
                    cm = parse_cm(lbl) if category in ("ski", "pole") else None
                    sizes.append(SizeOption(label=lbl, available=True, cm=cm, price=price))

            pid = re.sub(r"\D+", "", p_url.rpartition("-")[-1]) or title
            raw = RawProduct(
                retailer_id=retailer_id,
                external_id=str(pid),
                title=title,
                url=p_url,
                price=price,
                compare_at=compare_at,
                available=True,
                image=image,
                vendor=vendor,
                sizes=sizes,
                category=category,
            )
            products.append(raw)
        return products

    return []


class SchemaOrgAdapter(Adapter):
    """通用 Schema.org 适配器。支持从分类页和商品页抽取结构化数据。"""
    platform = "schema_org"

    def fetch(self) -> Iterator[RawProduct]:
        # 当前实现只读取配置入口，没有验证分类分页是否穷尽。
        self.mark_incomplete("仅覆盖配置入口，未验证完整分页")
        pages = self.cfg.get("pages") or self.cfg.get("categories") or [self.cfg.get("path") or ""]
        if isinstance(pages, str):
            pages = [pages]

        for p in pages:
            path = p.get("path") if isinstance(p, dict) else str(p)
            url = urljoin(self.base_url + "/", path.lstrip("/"))
            try:
                html = self.fetcher.get_html(url)
            except Exception as e:
                self.record_fetch_error(e)
                continue

            # 1. 尝试直接从分类页寻找 Product JSON-LD
            items = extract_json_ld(html)
            found_count = 0
            for item in items:
                # 检查 ItemList
                if item.get("@type") == "ItemList" and isinstance(item.get("itemListElement"), list):
                    for elem in item["itemListElement"]:
                        target = elem.get("item") if isinstance(elem, dict) else None
                        if isinstance(target, dict):
                            raw = parse_product_json(target, self.base_url, self.category)
                            if raw and self.wanted(raw.title):
                                raw.retailer_id = self.rid
                                yield raw
                                found_count += 1
                elif is_product_type(item.get("@type")):
                    raw = parse_product_json(item, self.base_url, self.category)
                    if raw and self.wanted(raw.title):
                        raw.retailer_id = self.rid
                        yield raw
                        found_count += 1

            if found_count > 0:
                self.log(f"[{self.rid}/{self.category}] {path}: 从列表页提取到 {found_count} 个商品")
                continue

            # 1.5 尝试从分类页 DOM 商品卡片直接解析
            card_items = extract_cards_from_html(html, self.base_url, self.category, self.rid)
            found_cards = 0
            for raw in card_items:
                if self.wanted(raw.title):
                    yield raw
                    found_cards += 1
            if found_cards > 0:
                self.log(f"[{self.rid}/{self.category}] {path}: 从页面商品卡片提取到 {found_cards} 个商品")
                continue

            # 2. 如果列表页未内嵌，解析商品卡片链接并抓取商品页
            tree = LexborHTMLParser(html)
            pdp_links = set()
            for a in tree.css("a[href]"):
                href = a.attributes.get("href") or ""
                full = urljoin(url, href)
                if self._is_product_url(full):
                    pdp_links.add(full)

            max_items = int(self.cfg.get("max_items_per_cat", 40))
            if len(pdp_links) > max_items:
                self.mark_incomplete(f"达到商品详情预算 {max_items}")
            fetched = 0
            for link in sorted(pdp_links)[:max_items]:
                try:
                    pdp_html = self.fetcher.get_html(link)
                except Exception as e:
                    self.record_fetch_error(e)
                    continue
                for item in extract_json_ld(pdp_html):
                    if is_product_type(item.get("@type")):
                        raw = parse_product_json(item, link, self.category)
                        if raw and self.wanted(raw.title):
                            raw.retailer_id = self.rid
                            raw.url = link
                            yield raw
                            fetched += 1
                            break

            self.log(f"[{self.rid}/{self.category}] {path}: 抓取商品页完成，获得 {fetched} 个商品")

    def _is_product_url(self, url: str) -> bool:
        """初筛是否为商品详情页链接。"""
        u = url.lower()
        if any(x in u for x in ("cart", "account", "login", "checkout", "search", "wishlist", "contact", "about")):
            return False
        return bool(re.search(r"/(?:products?|items?|p)/|[a-z0-9-]+-p\d+|\.html$|\b(?:skis?|boots?|bindings?|jacket)/[a-z0-9-]+", u))
