"""Danner 官网（www.danner.com，Magento 2；LaCrosse Footwear 旗下，母公司 ABC-Mart）。

分类页 HTML 只是 JS 外壳，价格不在 HTML 里；数据来自 Magento 自带的 GraphQL 接口，用 GET（可缓存，和前端一样）：
    /graphql?query={products(filter:{category_id:{eq:"498"}},pageSize:50,currentPage:1){total_count page_info{…}
        items{sku name url_key url_suffix small_image{url} price_range{…}
              ... on ConfigurableProduct{configurable_options{…} variants{attributes{code label} product{sku stock_status price_range{…}}}}}}}
- 分类 id 用 categoryList(filters:{url_path:{eq:"men/hike"}}) 查：men/hike = 498（141 个），women/hike = 509（110 个）
- 一个颜色一个 ConfigurableProduct（name 里带颜色："Women's MTN45 Mid GTX Ash/Timber Wolf"）
- 变体属性：sizes（"9"、"9.5"）+ wth（"Men's D - Medium"、"Men's EE - Wide"、"Women's M - Medium"、"Women's W - Wide"）
  每个变体有 stock_status（IN_STOCK / OUT_OF_STOCK）和自己的 regular_price / final_price（打折时 final < regular）
- name 里带颜色；颜色属性 color 是选项 id，用 customAttributeMetadata 查一次 id → 名称，标题里去掉颜色
  （"Men's MTN45 Mid GTX Smoky Olive" → 标题 "Men's MTN45 Mid GTX"，颜色 "Smoky Olive"；原名称存在 extra["site_title"]），
  这样同型号不同颜色能归到一组比价
- Hike 分类里混有打猎款（"Skyridge Mossy Oak"）：用 exclude_title 正则去掉

尺码 label："Men's 9 D" / "Men's 9 EE" / "Women's 9 Medium" / "Women's 9 Wide"——楦宽字母能被尺码解析器识别的
（D、EE、B…）用字母；女款的 M / W 字母会和男码 / 女码简写混淆，改用后面的单词（Medium / Wide）。
网站没写尺码是 US，所以默认不填 shoe_size_system；cfg 写了 shoe_size_system = "US" 才填。

robots.txt：只禁止 /customer/、/checkout/cart、/catalogsearch；/graphql 允许，没有 Crawl-delay。

配置（config/categories.toml）：
  [danner.hiking_shoe]
  pages = [{ category_id = 498, gender = "men", name = "men/hike" }, { category_id = 509, gender = "women", name = "women/hike" }]
  exclude_title = "Mossy Oak|Realtree|Kryptek|Optifade"
"""
from __future__ import annotations

import re
from collections.abc import Iterator
from urllib.parse import quote

from ..footwear import UNKNOWN, shoe_width
from ..net import BlockedError, FetchError
from .base import Adapter, RawProduct, SizeOption, to_float

_PAGE = 50
_MAX_PAGES = 20
_QUERY = """{products(filter:{category_id:{eq:"%s"}},pageSize:%d,currentPage:%d){total_count page_info{total_pages current_page}
items{__typename sku name color url_key url_suffix stock_status small_image{url}
price_range{minimum_price{regular_price{value currency} final_price{value currency}}}
... on ConfigurableProduct{configurable_options{attribute_code label values{value_index label}}
variants{attributes{code label value_index} product{sku stock_status
price_range{minimum_price{regular_price{value} final_price{value}}}}}}}}}"""


_COLORS = '{customAttributeMetadata(attributes:[{attribute_code:"color",entity_type:"4"}]){items{attribute_options{value label}}}}'


def split_color(name: str, color: str | None) -> str:
    """名称里去掉颜色（"Men's MTN45 Mid GTX Smoky Olive" + "Smoky Olive" → "Men's MTN45 Mid GTX"），
    这样同一型号的不同颜色能归成一组；名称里找不到这个颜色就原样返回。"""
    if not color:
        return name
    m = re.search(rf"(?<![\w]){re.escape(color)}(?![\w])", name, re.I)
    if not m:
        return name
    rest = " ".join((name[:m.start()] + " " + name[m.end():]).split())
    return rest if len(rest) >= 3 else name


def query_url(base: str, category_id, page: int, size: int = _PAGE) -> str:
    q = " ".join((_QUERY % (category_id, size, page)).split())
    return f"{base}/graphql?query={quote(q, safe='')}"


def _prices(node: dict) -> tuple[float | None, float | None]:
    mp = ((node or {}).get("price_range") or {}).get("minimum_price") or {}
    return to_float((mp.get("final_price") or {}).get("value")), to_float((mp.get("regular_price") or {}).get("value"))


def width_label(width: str | None) -> tuple[str, str | None]:
    """"Men's D - Medium" → ("Men's", "D")；"Women's M - Medium" → ("Women's", "Medium")；"Women's W - Wide" → ("Women's", "Wide")。"""
    s = str(width or "").strip()
    m = re.match(r"(Men's|Women's|Unisex|Kids?'?s?)?\s*([A-Z0-9]+)?\s*(?:-\s*(.+))?$", s, re.I)
    if not s or not m:
        return "", None
    basis, code, word = (m.group(1) or ""), m.group(2), (m.group(3) or "").strip() or None
    if code and shoe_width(code) != UNKNOWN:
        return basis, code
    if word and shoe_width(word) != UNKNOWN:
        return basis, word
    return basis, s if not basis else s[len(basis):].strip() or None


def size_label(size: str, width: str | None) -> str:
    basis, w = width_label(width)
    return " ".join(x for x in (basis, str(size).strip(), w) if x)


class DannerAdapter(Adapter):
    platform = "danner"

    def _colors(self) -> dict[str, str]:
        """颜色属性的 选项 id → 名称（一次请求，约 27 KB）；失败就不拆颜色。"""
        try:
            data = self.fetcher.get_json(f"{self.base_url}/graphql?query={quote(_COLORS, safe='')}")
            items = ((data.get("data") or {}).get("customAttributeMetadata") or {}).get("items") or []
            return {str(o.get("value")): str(o.get("label")).strip() for it in items
                    for o in it.get("attribute_options") or [] if o.get("label")}
        except (FetchError, ValueError, AttributeError) as e:
            self.log(f"[{self.rid}] 颜色表获取失败（标题保留颜色）：{e}")
            return {}

    def _items(self, category_id) -> Iterator[dict]:
        size = int(self.cfg.get("page_size", _PAGE))
        for page in range(1, _MAX_PAGES + 1):
            data = self.fetcher.get_json(query_url(self.base_url, category_id, page, size))
            if not isinstance(data, dict) or (data.get("errors") and not data.get("data")):
                raise BlockedError(self.base_url + "/graphql", None, f"GraphQL 错误：{str(data.get('errors'))[:200]}")
            prods = (data.get("data") or {}).get("products") or {}
            items = prods.get("items") or []
            yield from items
            info = prods.get("page_info") or {}
            if not items or page >= int(info.get("total_pages") or 1):
                break

    def fetch(self) -> Iterator[RawProduct]:
        skip = re.compile(self.cfg["exclude_title"], re.I) if self.cfg.get("exclude_title") else None
        seen: dict[str, RawProduct] = {}
        self.colors = self._colors()
        for spec in self.cfg.get("pages") or []:
            cid, gender = spec["category_id"], spec.get("gender")
            n = total = 0
            for item in self._items(cid):
                total += 1
                sku = str(item.get("sku") or "")
                if not sku:
                    continue
                if sku in seen:   # 同一个商品出现在男女两个分类 = 中性款
                    if gender and seen[sku].gender_hint not in (None, gender):
                        seen[sku].gender_hint = "unisex"
                    continue
                name = " ".join(str(item.get("name") or "").split())
                if not name or (skip and skip.search(name)) or not self.wanted(name):
                    continue
                raw = self._to_raw(item, name, gender)
                if raw:
                    seen[sku] = raw
                    n += 1
            self.log(f"[{self.rid}] {self.category} {spec.get('name') or cid}: {total} 个商品，收 {n} 个")
        yield from seen.values()

    def _to_raw(self, item: dict, name: str, gender: str | None) -> RawProduct | None:
        url = f"{self.base_url}/{item.get('url_key')}{item.get('url_suffix') or '.html'}"
        color = getattr(self, "colors", {}).get(str(item.get("color")))
        title = split_color(name, color)
        sizes: list[SizeOption] = []
        rows, widths = [], set()
        for v in item.get("variants") or []:
            attrs = {a.get("code"): a.get("label") for a in v.get("attributes") or []}
            size = attrs.get("sizes") or attrs.get("size")
            if not size:
                continue
            width = attrs.get("wth") or attrs.get("width")
            label = size_label(size, width)
            prod = v.get("product") or {}
            price, regular = _prices(prod)
            avail = prod.get("stock_status") == "IN_STOCK"
            cmp_ = regular if regular and price and regular > price + 0.5 else None
            sizes.append(SizeOption(label=label, cm=self.size_cm(label), available=avail, price=price,
                                    compare_at=cmp_, url=url, color=color))
            widths.add(width_label(width)[1])
            rows.append({"sku": prod.get("sku"), "size": size, "width": width, "label": label,
                         "available": avail, "price": price, "regular": regular})
        price, regular = _prices(item)
        if sizes:
            pool = [s for s in sizes if s.available and s.price] or [s for s in sizes if s.price]
            if pool:
                best = min(pool, key=lambda s: s.price)
                price, regular = best.price, best.compare_at
        if not price:
            return None
        extra = {"variants": rows, "site_title": name, "colors": [color] if color else [], "options": [o.get("label") for o in item.get("configurable_options") or []]}
        if widths - {None}:
            extra["shoe_widths"] = sorted(widths - {None})
        if len(widths) == 1 and None not in widths:
            extra["shoe_width"] = next(iter(widths))
        if self.cfg.get("shoe_size_system"):
            extra["shoe_size_system"] = self.cfg["shoe_size_system"]
        img = (item.get("small_image") or {}).get("url")
        return RawProduct(
            retailer_id=self.rid,
            external_id=str(item["sku"]),
            url=url,
            title=title,
            price=price,
            compare_at=regular if regular and regular > price + 0.5 else None,
            currency="USD",
            vendor="Danner",
            product_type=item.get("__typename"),
            image=img,
            sizes=sizes,
            available=any(s.available for s in sizes) if sizes else item.get("stock_status") == "IN_STOCK",
            gender_hint=gender,
            category=self.category,
            extra=extra,
        )
