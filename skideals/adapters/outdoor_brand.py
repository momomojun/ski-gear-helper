"""户外/滑雪大牌官网适配器 (Arc'teryx, Patagonia, The North Face, Helly Hansen, Columbia, Mountain Hardwear, Oakley 等)。

品牌官网特性：
1. 均有强力 WAF / 反爬机制（Akamai、PerimeterX、Cloudflare Turnstile）；
2. 品牌官网滑雪专区相对聚焦（雪服、硬壳、雪裤、中间层、雪镜）；
3. 现代品牌官网商品页普遍规范植入了 Schema.org JSON-LD，部分使用 Next.js (__NEXT_DATA__)；
4. 结合网底座的 browser_fallback 机制，遇阻时自动触发无头真实浏览器接管。
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser

from ..models import RawProduct, SizeOption
from .base import NOT_SKI_RE, Adapter, parse_cm, to_float
from .schema_org import extract_json_ld, is_product_type, parse_product_json


class OutdoorBrandAdapter(Adapter):
    platform = "outdoor_brand"

    def fetch(self) -> Iterator[RawProduct]:
        self.mark_incomplete("仅覆盖配置入口，未验证完整分页")
        categories = self.cfg.get("categories") or self.cfg.get("pages") or []
        if isinstance(categories, str):
            categories = [categories]

        brand_name = self.cfg.get("brand") or self.cfg.get("name")
        max_items = int(self.cfg.get("max_items", 40))

        for item in categories:
            path = item.get("path") if isinstance(item, dict) else str(item)
            gender_hint = item.get("gender") if isinstance(item, dict) else None
            url = urljoin(self.base_url + "/", path.lstrip("/"))

            try:
                html = self.fetcher.get_html(url)
            except Exception as e:
                self.record_fetch_error(e)
                continue

            products: list[RawProduct] = []
            pdp_from_list: list[str] = []

            # 1. 尝试直接从列表页获取内嵌的 Schema.org JSON-LD
            for obj in extract_json_ld(html):
                if obj.get("@type") == "ItemList" and isinstance(obj.get("itemListElement"), list):
                    for elem in obj["itemListElement"]:
                        tgt = elem.get("item") if isinstance(elem, dict) else None
                        if isinstance(tgt, dict):
                            p = parse_product_json(tgt, self.base_url, self.category)
                            if p:
                                products.append(p)
                        elif isinstance(elem, dict) and elem.get("url"):
                            pdp_from_list.append(urljoin(url, elem["url"]))
                elif is_product_type(obj.get("@type")):
                    # 排除分类级概览（如 "Alpine Skis"、"Skis"、"Clothing" 等集合类聚合项）
                    pname = str(obj.get("name") or "").lower()
                    if pname not in ("alpine skis", "skis", "ski boots", "ski bindings", "clothing", "helmets", "goggles"):
                        p = parse_product_json(obj, self.base_url, self.category)
                        if p:
                            products.append(p)

            # 2. 如果列表页未内嵌完整商品数据，提取列表页中的商品详情链接
            if not products:
                tree = LexborHTMLParser(html)
                pdp_links = set(pdp_from_list)
                for a in tree.css("a[href]"):
                    href = a.attributes.get("href") or ""
                    full_link = urljoin(url, href)
                    if self._is_brand_pdp(full_link):
                        pdp_links.add(full_link)

                count = 0
                if len(pdp_links) > max_items:
                    self.mark_incomplete(f"达到商品详情预算 {max_items}")
                for link in sorted(pdp_links)[:max_items]:
                    try:
                        pdp_html = self.fetcher.get_html(link)
                    except Exception as e:
                        self.record_fetch_error(e)
                        continue
                    for obj in extract_json_ld(pdp_html):
                        if is_product_type(obj.get("@type")):
                            p = parse_product_json(obj, link, self.category)
                            if p and self.wanted(p.title):
                                p.retailer_id = self.rid
                                p.url = link
                                if gender_hint and not p.gender_hint:
                                    p.gender_hint = gender_hint
                                if brand_name and not p.vendor:
                                    p.vendor = brand_name
                                yield p
                                count += 1
                                break
                self.log(f"[{self.rid}/{self.category}] {path}: 抓取商品详情页，产出 {count} 个商品")
                continue

            # 3. 产出列表页直接提取的结果
            count = 0
            for p in products:
                if self.wanted(p.title):
                    p.retailer_id = self.rid
                    if gender_hint and not p.gender_hint:
                        p.gender_hint = gender_hint
                    if brand_name and not p.vendor:
                        p.vendor = brand_name
                    yield p
                    count += 1
            self.log(f"[{self.rid}/{self.category}] {path}: 列表页直接产出 {count} 个商品")

    def _is_brand_pdp(self, url: str) -> bool:
        """初筛是否为商品详情页链接。"""
        u = url.lower()
        if any(x in u for x in ("cart", "checkout", "account", "login", "search", "stores", "help", "about", "contact", "/service/")):
            return False
        return bool(re.search(r"/(?:product|shop|p)/|[a-z0-9-]+-p\d+|\.html$|/(?:jackets?|pants?|bibs?|fleece|goggles?|helmets?)/[a-z0-9-]+|/[a-z0-9-]+/[a-z0-9]{5,14}$", u))
