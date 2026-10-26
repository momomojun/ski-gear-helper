"""K2 Sports 品牌官网平台：K2（k2snow.com/en-us）、Line（lineskis.com）、Völkl（volkl.com）、
Marker（markerbindings.com）、Dalbello（dalbelloboots.com）。五个网站是同一套 Nuxt 3 前端 + BigCommerce 后台
（商品价格 / 库存）+ Contentstack（商品标题、分类页）。

数据来源：分类页 `/c/<分类>/` 的 HTML 里有一段 `<script id="__NUXT_DATA__">`（devalue 格式的 JSON 数组，
元素之间用下标互相引用），`data["categoryProducts-us-<分类ID>-<页>-<条数>-<排序>-<筛选>"]` 就是浏览器看到的商品列表：
  - products[]：`display_title`（网页上显示的标题，如 "K2 Omen 85 Men's Skis 2027"）、`title`（内部标题，
    "Omen 85 Men's Skis - 2627"，性别词更全：Unisex / Youth / Jr）、`url`（商品页 slug）、`image.amplience_id`、
    `bcProduct`（BigCommerce：calculatedPrice = 现价，retailPrice = 原价，variants[] 每个 SKU 的价格、
    isInStock / isPurchasable、option_values 尺码 / 固定器 / 颜色）
  - total / hasNextPage：分页。网页上的 “Load More” 走 /api（robots 禁止），但分类页本身支持 `?page=N`，
    服务器直接渲染前 30 × (N + 1) 个商品 —— 所以一个分类一般一次请求就够（默认 page=3 → 120 个），
    不够时按 total 算出需要的 page 再请求一次。
美国站的价格是 USD（payload 里的 key 带 "-us-"，CurrenciesStore 只有 USD）。

robots.txt（五个网站相同）：**Crawl-delay: 20**，禁止 /api、/*/api、*.php、/video、/wp-content，分类页 /c/ 允许。
Fetcher 会遵守 20 秒间隔，所以只抓分类页（不抓商品详情页）：每个品牌每个分类 1–2 个列表页。
K2 的美国站在 /en-us 下；其他四个网站的美国站在根路径（/en-us 会 301 到 /），商品页链接网站自己写成 /en-us/p/<slug>。

配置（config/categories.toml），pages 里的路径相对于 retailers.toml 的 url：
  [volkl.ski]
  pages = [{ path = "/c/skis/" }, { path = "/c/sale/sale-skis/" }]
  可选：page 里写 gender = "women"（女款分类页）；分类级 page_start（默认 3）、max_page（默认 20）、
  product_prefix（默认 "/en-us/p"）。
性别：分类页给的 gender 优先；否则用内部标题里的性别词（Men's / Women's / Unisex / Youth / Jr / Juvy）。
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterator
from urllib.parse import urlsplit

from ..net import BlockedError, FetchError
from .base import Adapter, RawProduct, SizeOption, to_float
from .sfcc import merge_genders, reconcile_gender

_PER_PAGE = 30            # 网站每次 “Load More” 30 个；?page=N 渲染 30 × (N + 1) 个
_PAGE_START = 3           # 第一次就要 120 个，大部分分类一次请求就够
_MAX_PAGE = 20

_NUXT_RE = re.compile(r'<script[^>]*\bid="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)
# devalue 的特殊类型：["Reactive", 下标] 之类的包装
_WRAPPERS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "NuxtError", "Island", "Error"}
_EMPTY = {"EmptyRef", "EmptyShallowRef"}
_SPECIAL = {-1: None, -2: None, -3: float("nan"), -4: float("inf"), -5: float("-inf"), -6: 0}

# 平台上的品牌（K2 官网也卖 Marker 固定器、Line 官网也卖 K2 的东西）：标题以这些品牌开头时 vendor 用它
_PLATFORM_BRANDS = {"k2": "K2", "line": "Line", "volkl": "Völkl", "völkl": "Völkl", "marker": "Marker",
                    "dalbello": "Dalbello", "madshus": "Madshus", "full tilt": "Full Tilt"}
_SIZE_OPT_RE = re.compile(r"\bsize\b|length|mondo", re.I)
_COLOR_OPT_RE = re.compile(r"colou?r", re.I)


# ============================================================================ 页面解析
def nuxt_payload(html: str):
    """把 __NUXT_DATA__（devalue 格式）还原成普通的 dict / list。找不到就返回 None（被拦截或改版）。"""
    m = _NUXT_RE.search(html)
    if not m:
        return None
    try:
        arr = json.loads(m.group(1))
    except ValueError:
        return None
    if not isinstance(arr, list) or not arr:
        return None
    memo: dict[int, object] = {}

    def rev(i):
        if not isinstance(i, int):
            return i
        if i < 0:
            return _SPECIAL.get(i)
        if i in memo:
            return memo[i]
        v = arr[i]
        if isinstance(v, list):
            if v and isinstance(v[0], str):
                tag = v[0]
                if tag in _WRAPPERS:
                    memo[i] = out = rev(v[1]) if len(v) > 1 else None
                    return out
                if tag in _EMPTY:
                    memo[i] = None
                    return None
                if tag == "Set":
                    memo[i] = out = [rev(x) for x in v[1:]]
                    return out
                if tag == "Map":
                    memo[i] = out = {}
                    for k, x in zip(v[1::2], v[2::2]):
                        out[str(rev(k))] = rev(x)
                    return out
                if tag in ("Date", "RegExp", "BigInt"):
                    memo[i] = v[1] if len(v) > 1 else None
                    return memo[i]
            out = []
            memo[i] = out
            out.extend(rev(x) for x in v)
            return out
        if isinstance(v, dict):
            out = {}
            memo[i] = out
            for k, x in v.items():
                out[k] = rev(x)
            return out
        return v

    return rev(0)


def category_products(payload) -> dict | None:
    """payload["data"] 里 key 以 categoryProducts- 开头的那一项：{products, total, hasNextPage, ...}。"""
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    for k, v in data.items():
        if k.startswith("categoryProducts") and isinstance(v, dict) and isinstance(v.get("products"), list):
            return v
    return None


def dedupe_products(items: list) -> list[dict]:
    """?page=N 要的条数超过总数时，列表末尾会重复出现前面的商品（而且只有部分字段）：按 url 去重，
    保留字段完整的那条（有 display_title 和 bcProduct）。"""
    out: dict[str, dict] = {}
    for p in items:
        if not isinstance(p, dict) or not p.get("url"):
            continue
        key = str(p["url"]).strip("/")
        cur = out.get(key)
        full = bool(p.get("display_title")) + bool(p.get("bcProduct"))
        if cur is None or full > bool(cur.get("display_title")) + bool(cur.get("bcProduct")):
            out[key] = p
    return list(out.values())


def image_urls(html: str) -> dict[str, str]:
    """页面里渲染出来的商品图：amplience 图片 ID → 不带参数的图片地址（/s/ 图集或 /i/ 单图，账号各品牌不同）。"""
    out: dict[str, str] = {}
    for m in re.finditer(r"https://cdn\.media\.amplience\.net/([si])/([\w-]+)/([\w.-]+)", html):
        out.setdefault(m.group(3), m.group(0))
    return out


def _price(x) -> float | None:
    return to_float(x.get("value")) if isinstance(x, dict) else to_float(x)


_KIDS_RE = re.compile(r"\b(?:youth|juniors?|jr|juvy|kids?|girls?|boys?|toddler|team ?jr)\b", re.I)
_UNISEX_RE = re.compile(r"\bunisex\b", re.I)
_WOMEN_RE = re.compile(r"\bw(?:o)?m[ae]n'?s?\b|\bwomens\b", re.I)
_MEN_RE = re.compile(r"\bmen'?s?\b", re.I)
_W_TOKEN_RE = re.compile(r"(?<![\w/])W(?![\w/'])")   # Dalbello 女款："Veloce 75 Mv W Ski Boots"（不含 W/O Brake）


def title_gender(title: str) -> str | None:
    """内部标题里的性别词："Blur 74 Men's/Unisex Skis" → unisex；"Mindbender Jr Skis" → kids。"""
    t = title or ""
    if _KIDS_RE.search(t):
        return "kids"
    if _UNISEX_RE.search(t):
        return "unisex"
    if _WOMEN_RE.search(t) or _W_TOKEN_RE.search(t):
        return "women"
    if _MEN_RE.search(t):
        return "men"
    return None


def clean_title(title: str, brand: str | None) -> str:
    """display_title 为空时用内部标题："Omen Jr Skis (US/CA) - 2627" → "K2 Omen Jr Skis 2027"。"""
    t = re.sub(r"\((?:US|CA|EU|US/CA)[^)]*\)", " ", title or "", flags=re.I)
    m = re.search(r"[-\s]+(\d{2})(\d{2})\s*$", t)
    year = None
    if m and int(m.group(2)) == int(m.group(1)) + 1:
        year = 2000 + int(m.group(2))
        t = t[:m.start()]
    t = " ".join(t.split())
    if brand and not t.lower().startswith(brand.lower()):
        t = f"{brand} {t}"
    return f"{t} {year}" if year else t


def title_brand(title: str, default: str | None) -> str | None:
    """"MARKER Duke Pt 16 Bindings 2027"（K2 官网卖的 Marker）→ "Marker"；认不出就用网站本身的品牌。"""
    t = (title or "").lower()
    for key in sorted(_PLATFORM_BRANDS, key=len, reverse=True):
        if t == key or t.startswith(key + " "):
            return _PLATFORM_BRANDS[key]
    return default


def variant_options(v: dict) -> dict[str, str]:
    """一个 SKU 的选项：{"Size": "170", "Binding": "No Binding"}。"""
    out = {}
    for o in v.get("option_values") or []:
        vals = o.get("values") or []
        if o.get("option_display_name") and vals and isinstance(vals[0], dict):
            out[str(o["option_display_name"])] = str(vals[0].get("label") or "").strip()
    return out


# ============================================================================ 适配器
class K2SportsAdapter(Adapter):
    platform = "k2sports"

    @property
    def origin(self) -> str:
        p = urlsplit(self.cfg["url"])
        return f"{p.scheme}://{p.netloc}"

    @property
    def brand(self) -> str | None:
        return self.cfg.get("brand") or self.cfg.get("name")

    def _pages(self) -> list[dict]:
        out = []
        for spec in self.cfg.get("pages") or []:
            d = {"path": spec} if isinstance(spec, str) else dict(spec)
            if d.get("path"):
                out.append(d)
        return out

    def _list_url(self, path: str, page: int) -> str:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        return url + ("&" if "?" in url else "?") + f"page={page}"

    def _get(self, url: str, have: bool) -> str | None:
        """被反爬拦截：还没拿到商品就抛出（如实报告），否则保留已有结果；其他错误记录后跳过这一页。"""
        try:
            return self.fetcher.get_html(url)
        except BlockedError:
            if not have:
                raise
            self.log(f"[{self.rid}] BLOCKED on {url} -- keeping what was collected so far")
            return None
        except FetchError as e:
            self.log(f"[{self.rid}] list page failed: {e}")
            return None

    def _category_page(self, path: str, have: bool) -> tuple[list[dict], dict[str, str], int | None]:
        """抓一个分类页的全部商品（一般 1 次请求，商品多时 2 次）。返回 (商品, 图片地址, 网站给的总数)。"""
        page = int(self.cfg.get("page_start") or _PAGE_START)
        max_page = int(self.cfg.get("max_page") or _MAX_PAGE)
        products: list[dict] = []
        images: dict[str, str] = {}
        total = None
        for _ in range(3):
            html = self._get(self._list_url(path, page), have)
            if html is None:
                break
            cp = category_products(nuxt_payload(html))
            if cp is None:
                if not have and not products:
                    raise BlockedError(self._list_url(path, page), None, "页面里没有 __NUXT_DATA__ 商品列表（可能被拦截或改版）")
                self.log(f"[{self.rid}] {path}: no product list in the page")
                break
            products = dedupe_products(cp.get("products") or [])
            images = image_urls(html)
            total = cp.get("total") if isinstance(cp.get("total"), int) else None
            if not cp.get("hasNextPage") or total is None or len(products) >= total:
                break
            need = math.ceil(total / _PER_PAGE) - 1
            if need <= page or page >= max_page:      # 页数参数不再起作用 / 到上限：保留已有的
                break
            page = min(need, max_page)
        return products, images, total

    def fetch(self) -> Iterator[RawProduct]:
        pages = self._pages()
        if not pages:
            self.log(f"[{self.rid}] no pages configured for '{self.category}'; skipping")
            return
        found: dict[str, RawProduct] = {}
        genders: dict[str, set[str]] = {}
        for spec in pages:
            items, images, total = self._category_page(spec["path"], bool(found))
            n_new = 0
            for item in items:
                p = self._to_raw(item, images)
                if p is None:
                    continue
                if spec.get("gender"):
                    genders.setdefault(p.external_id, set()).add(spec["gender"])
                if p.external_id not in found:
                    found[p.external_id] = p
                    n_new += 1
                found[p.external_id].extra["pages"].append(spec["path"])
            self.log(f"[{self.rid}/{self.category}] {spec['path']}: {len(items)} products"
                     + (f" (site says {total})" if total is not None else "") + f", {n_new} new")
        for eid, p in found.items():
            tg = p.extra.get("title_gender")
            # 标题明确是儿童款（Jr / Youth）时以标题为准；否则分类页的性别优先，再用标题里的性别词
            site = merge_genders(genders.get(eid, set()) | ({"kids"} if tg == "kids" else set())) or tg
            p.gender_hint = reconcile_gender(site, p.title)
        yield from found.values()

    def _to_raw(self, item: dict, images: dict[str, str]) -> RawProduct | None:
        bc = item.get("bcProduct")
        slug = str(item.get("url") or "").strip("/")
        if not isinstance(bc, dict) or not slug:
            return None           # 没有 BigCommerce 数据 = 美国站不卖
        regions = item.get("regions")
        if isinstance(regions, list) and regions and "us" not in regions:
            return None
        raw_title = " ".join(str(item.get("title") or "").split())
        title = " ".join(str(item.get("display_title") or "").split()) or clean_title(raw_title, self.brand)
        if not title or not self.wanted(title):
            return None
        vendor = title_brand(title, self.brand)
        if vendor and title_brand(title, None) is None:   # "Speedstick Jr Poles 2025"：标题前面补上品牌
            title = f"{vendor} {title}"
        retail = _price(bc.get("retailPrice"))
        sizes, color = self._sizes(bc.get("variants") or [], retail, bc.get("product_options") or [])
        stock = [s.price for s in sizes if s.available and s.price]
        if not sizes:          # 没有尺码选项（单一 SKU）：直接看各个 SKU
            stock = [_price(v.get("calculatedPrice")) for v in bc.get("variants") or []
                     if isinstance(v, dict) and v.get("isInStock") and v.get("isPurchasable") is not False]
            stock = [x for x in stock if x]
        price = min(stock) if stock else _price(bc.get("calculatedPrice"))
        if not price:
            return None
        if stock:
            available = True
        elif sizes:
            available = False
        else:
            available = bool(bc.get("isInStock")) if "isInStock" in bc else None
        img = item.get("image") or {}
        aid = img.get("amplience_id") if isinstance(img, dict) else None
        image = images.get(aid) if aid else None
        return RawProduct(
            retailer_id=self.rid,
            external_id=slug,
            url=f"{self.origin}{self.cfg.get('product_prefix', '/en-us/p')}/{slug}",
            title=title,
            price=price,
            compare_at=retail if (retail and retail > price + 0.005) else None,
            currency="USD",
            vendor=vendor,
            image=image,
            sizes=sizes,
            available=available,
            category=self.category,
            extra={"pages": [], "bc_id": bc.get("id"), "raw_title": raw_title, "color": color,
                   "title_gender": title_gender(raw_title) or title_gender(title)},
        )

    def _sizes(self, variants: list, retail: float | None, options: list | None = None
               ) -> tuple[list[SizeOption], str | None]:
        """按尺码合并 SKU（同一尺码多种固定器 / 颜色：任一有货即有货，价格取有货里最低的）。
        尺码顺序按网站的选项顺序（product_options），没有的排在后面。"""
        by: dict[str, dict] = {}
        for v in variants:
            if not isinstance(v, dict):
                continue
            opts = variant_options(v)
            size_key = next((k for k in opts if _SIZE_OPT_RE.search(k)), None)
            if size_key is None and len(opts) == 1 and not _COLOR_OPT_RE.search(next(iter(opts))) \
                    and not re.search(r"binding", next(iter(opts)), re.I):
                size_key = next(iter(opts))
            if size_key is None or not opts[size_key]:
                continue
            color_key = next((k for k in opts if _COLOR_OPT_RE.search(k)), None)
            label = opts[size_key]
            vp = _price(v.get("calculatedPrice"))
            ok = bool(v.get("isInStock")) and v.get("isPurchasable") is not False
            d = by.setdefault(label, {"ok": False, "best": None, "any": None, "color": None})
            if vp and (d["any"] is None or vp < d["any"]):
                d["any"] = vp
            if ok:
                d["ok"] = True
                if vp and (d["best"] is None or vp < d["best"]):
                    d["best"] = vp
                    d["color"] = opts.get(color_key) if color_key else None
        sizes = []
        for label, d in by.items():
            p = d["best"] if d["ok"] else d["any"]
            sizes.append(SizeOption(label, self.size_cm(label), d["ok"], p,
                                    retail if (retail and p and retail > p + 0.005) else None,
                                    None, d["color"]))
        order: dict[str, int] = {}
        for o in options or []:
            if isinstance(o, dict) and _SIZE_OPT_RE.search(str(o.get("displayName") or "")):
                for i, x in enumerate(o.get("values") or []):
                    if isinstance(x, dict):
                        order.setdefault(str(x.get("label") or "").strip(), i)
        sizes.sort(key=lambda s: (order.get(s.label, 999), s.cm or 0))
        colors = Counter(s.color for s in sizes if s.available and s.color)
        return sizes, (colors.most_common(1)[0][0] if colors else None)


__all__ = ["K2SportsAdapter", "nuxt_payload", "category_products", "dedupe_products", "title_gender",
           "title_brand", "clean_title"]
