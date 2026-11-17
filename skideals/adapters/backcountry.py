"""Backcountry & Steep & Cheap 适配器 (backcountry.com & steepandcheap.com)。

Backcountry.com LLC 旗下两大户外/雪具巨头：
- Backcountry: 全美最大户外及雪具在线零售商之一
- Steep & Cheap: Backcountry 官方清仓折扣站（同套技术栈与库存中台）

数据解析逻辑：
1. 优先从页面内嵌的 Next.js 结构化数据 `<script id="__NEXT_DATA__">` 中提取商品、变体尺码、价格与库存；
2. 后备方案：利用通用 Schema.org JSON-LD `<script type="application/ld+json">` 提取；
3. DOM 卡片兜底：从商品卡片网格提取链接与价格；
4. 结合 browser_fallback：遇到 PerimeterX / Cloudflare 拦截时，自动触发真实浏览器接管过盾。
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from ..models import RawProduct, SizeOption
from .base import NOT_SKI_RE, Adapter, parse_cm, to_float
from .schema_org import extract_json_ld, is_product_type, parse_product_json

DEFAULT_CATEGORIES: dict[str, list[str]] = {
    # 滑雪核心硬货与服装
    "ski": ["/cat/alpine-skis", "/cat/alpine-touring-skis"],
    "boot": ["/cat/alpine-ski-boots"],
    "binding": ["/cat/alpine-ski-bindings-brakes"],
    "pole": ["/cat/ski-poles"],
    "helmet": ["/cat/ski-helmets"],
    "goggle": ["/cat/ski-goggles"],
    "jacket": ["/cat/mens-ski-jackets", "/cat/womens-ski-jackets"],
    "pants": ["/cat/mens-ski-pants-bibs", "/cat/womens-ski-pants-bibs"],
    "midlayer": ["/cat/mens-fleece-jackets", "/cat/womens-fleece-jackets"],
    "baselayer": ["/cat/mens-base-layers", "/cat/womens-base-layers"],
    "glove": ["/cat/mens-ski-gloves", "/cat/womens-ski-gloves"],
    "sock": ["/cat/mens-ski-socks", "/cat/womens-ski-socks"],
    "facewear": ["/cat/balaclavas-neck-gaiters"],
    "hat": ["/cat/beanies"],
    "backpack": ["/cat/ski-packs", "/cat/daypacks"],
    "bag": ["/cat/ski-bags", "/cat/ski-boot-bags"],
    "avalanche": ["/cat/avalanche-safety"],
    "skin": ["/cat/climbing-skins", "/cat/splitboard-skins"],
    "tuning": ["/cat/ski-tuning-tools"],
    "accessory": ["/cat/hand-foot-warmers"],
    # 独立户外专区：外套、徒步裤、速干衣、徒步鞋、越野跑鞋
    "outdoor_jacket": ["/cat/mens-rain-jackets", "/cat/womens-rain-jackets"],
    "outdoor_pants": ["/cat/mens-hiking-pants", "/cat/womens-hiking-pants"],
    "outdoor_top": ["/cat/mens-hiking-shirts", "/cat/womens-hiking-shirts"],
    "hiking_shoe": ["/cat/hiking-shoes", "/cat/hiking-boots"],
    "trail_shoe": ["/cat/trail-running-shoes"],
}


def parse_next_data(html: str) -> dict | None:
    """提取 Next.js __NEXT_DATA__。"""
    tree = LexborHTMLParser(html)
    script = tree.css_first('script#__NEXT_DATA__')
    if script is None or not script.text():
        return None
    try:
        return json.loads(script.text())
    except Exception:
        return None


def extract_products_from_next_data(data: dict, base_url: str, category: str) -> list[RawProduct]:
    """从 __NEXT_DATA__ 的 props.pageProps 递归寻找商品列表。"""
    products: list[RawProduct] = []

    def _find_product_lists(obj: Any):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("products", "items", "results") and isinstance(v, list) and v:
                    if isinstance(v[0], dict) and ("title" in v[0] or "name" in v[0]):
                        for raw_p in v:
                            p = _parse_single_next_product(raw_p, base_url, category)
                            if p:
                                products.append(p)
                        return
                elif k == "edges" and isinstance(v, list) and v:
                    if isinstance(v[0], dict) and isinstance(v[0].get("node"), dict):
                        for edge in v:
                            node = edge.get("node")
                            if isinstance(node, dict):
                                p = _parse_single_next_product(node, base_url, category)
                                if p:
                                    products.append(p)
                        return
                elif k == "productListDataMap" and isinstance(v, dict) and v:
                    for raw_p in v.values():
                        if isinstance(raw_p, dict):
                            p = _parse_single_next_product(raw_p, base_url, category)
                            if p:
                                products.append(p)
                    if products:
                        return
                _find_product_lists(v)
        elif isinstance(obj, list):
            for item in obj:
                _find_product_lists(item)

    _find_product_lists(data.get("props", {}).get("pageProps", {}))
    return products


def _parse_single_next_product(p: dict, base_url: str, category: str) -> RawProduct | None:
    title = p.get("title") or p.get("name")
    if not title or not isinstance(title, str):
        return None
    title = title.strip()

    pid = str(p.get("id") or p.get("productId") or p.get("sku") or title)
    brand = p.get("brand")
    vendor = brand.get("name") if isinstance(brand, dict) else (str(brand) if brand else None)

    # 价格处理
    price_obj = p.get("price") or p.get("prices") or {}
    price = None
    compare_at = None

    if isinstance(price_obj, dict):
        price = to_float(price_obj.get("sale") or price_obj.get("retail") or price_obj.get("min") or price_obj.get("current"))
        compare = to_float(price_obj.get("regular") or price_obj.get("max") or price_obj.get("original"))
        if compare and price and compare > price + 0.009:
            compare_at = compare
    elif isinstance(price_obj, (int, float, str)):
        price = to_float(price_obj)

    # Backcountry aggregates / activePrice / direct prices 格式
    aggregates = p.get("aggregates") or p.get("activePrice")
    if isinstance(aggregates, dict):
        sale = to_float(aggregates.get("minSalePrice") or aggregates.get("salePrice"))
        regular = to_float(aggregates.get("minListPrice") or aggregates.get("listPrice") or aggregates.get("maxListPrice"))
        if sale:
            price = sale
            if regular and regular > sale + 0.009:
                compare_at = regular
        elif regular:
            price = regular
    if price is None:
        sale = to_float(p.get("minSalePrice") or p.get("salePrice"))
        regular = to_float(p.get("minListPrice") or p.get("listPrice"))
        if sale:
            price = sale
            if regular and regular > sale + 0.009:
                compare_at = regular
        elif regular:
            price = regular

    # URL 与图片
    url = p.get("url") or p.get("slug") or ""
    if url and not url.startswith("http"):
        url = urljoin(base_url, url)

    img = p.get("image") or p.get("thumbnail") or p.get("featuredImage")
    if not img and isinstance(p.get("productMainImage"), dict):
        pmi = p["productMainImage"]
        img = pmi.get("largeImg") or pmi.get("mediumImg") or pmi.get("nineHundredImg")
    image_url = None
    if isinstance(img, str):
        image_url = img
    elif isinstance(img, dict):
        image_url = img.get("url") or img.get("src")
    elif isinstance(p.get("colors"), list) and p["colors"]:
        first_color = p["colors"][0]
        if isinstance(first_color, dict):
            image_url = first_color.get("pliImage") or first_color.get("tileImage")

    if image_url and image_url.startswith("//"):
        image_url = "https:" + image_url
    elif image_url and not image_url.startswith("http"):
        image_url = urljoin(base_url, image_url)

    # 变体尺码
    sizes: list[SizeOption] = []
    variants = p.get("variants") or p.get("skus") or []
    if isinstance(variants, list):
        for v in variants:
            if not isinstance(v, dict):
                continue
            lbl = str(v.get("size") or v.get("name") or v.get("label") or "").strip()
            v_avail = bool(v.get("inStock", True) and not v.get("outOfStock", False))
            v_price = to_float(v.get("price") or v.get("salePrice"))
            cm = parse_cm(lbl) if category in ("ski", "pole") else None
            sizes.append(SizeOption(label=lbl or None, available=v_avail, cm=cm, price=v_price))

    if price is None and sizes:
        valid = [s.price for s in sizes if s.price]
        if valid:
            price = min(valid)

    if price is None:
        return None

    stock_status = p.get("stockStatus")
    if stock_status is not None:
        avail = stock_status == "IN_STOCK"
    elif sizes:
        avail = any(s.available for s in sizes)
    else:
        avail = p.get("inStock", True)

    return RawProduct(
        retailer_id="",
        external_id=pid,
        url=url or base_url,
        title=title,
        price=price,
        compare_at=compare_at,
        available=avail,
        image=image_url,
        vendor=vendor,
        sizes=sizes,
        category=category,
    )


class BackcountryAdapter(Adapter):
    platform = "backcountry"

    def fetch(self) -> Iterator[RawProduct]:
        # 少于 12 个商品或解析为空都不是可靠的末页证据。
        self.mark_incomplete("分页终点尚未验证")
        categories = self.cfg.get("categories") or DEFAULT_CATEGORIES.get(self.category, [f"/{self.category}"])
        if isinstance(categories, str):
            categories = [categories]

        max_pages = int(self.cfg.get("max_pages", 5))

        for cat_path in categories:
            for page in range(1, max_pages + 1):
                url = urljoin(self.base_url + "/", cat_path.lstrip("/"))
                params = {"page": page} if page > 1 else None
                headers = {"Referer": self.base_url + "/"}
                try:
                    html = self.fetcher.get_html(url, params=params, headers=headers)
                except Exception as e:
                    if page > 1 and "404" in str(e):
                        # 已翻至最后一页，正常结束
                        break
                    self.record_fetch_error(e)
                    break

                batch_products: list[RawProduct] = []

                # 1. 优先提取 __NEXT_DATA__
                next_data = parse_next_data(html)
                if next_data:
                    batch_products = extract_products_from_next_data(next_data, self.base_url, self.category)

                # 2. Schema.org 兜底
                if not batch_products:
                    for obj in extract_json_ld(html):
                        if obj.get("@type") == "ItemList" and isinstance(obj.get("itemListElement"), list):
                            for elem in obj["itemListElement"]:
                                tgt = elem.get("item") if isinstance(elem, dict) else None
                                if isinstance(tgt, dict):
                                    p = parse_product_json(tgt, self.base_url, self.category)
                                    if p:
                                        batch_products.append(p)
                        elif is_product_type(obj.get("@type")):
                            p = parse_product_json(obj, self.base_url, self.category)
                            if p:
                                batch_products.append(p)

                if not batch_products:
                    # 没有提取到更多商品，结束当前分类翻页
                    break

                count = 0
                for raw in batch_products:
                    if self.wanted(raw.title):
                        raw.retailer_id = self.rid
                        yield raw
                        count += 1

                self.log(f"[{self.rid}/{self.category}] {cat_path} 第 {page} 页提取到 {count} 个商品")
                if len(batch_products) < 12:  # 商品少于一页，不继续翻页
                    break
            else:
                self.mark_incomplete(f"达到分类页预算 {max_pages}")
