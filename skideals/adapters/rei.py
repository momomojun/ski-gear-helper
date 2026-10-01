"""REI（www.rei.com）适配器 —— 抓 REI 网上在售的滑雪装备（双板、固定器、雪鞋、雪杖、头盔、雪镜、雪服……）。

调度器每个分类调用一次：cfg = 零售商配置（retailers.toml，含双板的配置）+ config/categories.toml 的 [rei.<分类>]
+ cfg["category"]。每个分类抓哪些页面见下面的 DEFAULT_CATEGORY_CFG（分类表里写了路径就以 toml 为准）。
注意零售商级别的键会合并进每个分类，所以分类表要显式写 category_paths（可以是空列表）、gender_paths（可以是 {}）
和 max_detail_pages，免得继承双板的配置。

数据来源（都是 REI 自己前端在用的 JSON 接口，robots.txt 允许）：

1. 分类列表 JSON：REI 的搜索前端翻页时请求「当前路径 + json=true」，返回结构化搜索结果
   （searchResults.results / pagination / query.totalResults），比 HTML 小得多（90 条 ≈ 350 KB）。例：
       /c/downhill-skis?pagesize=90&sort=title&json=true      第 2 页起加 page=N
   - pagesize 只接受 30 / 90；默认 "Best Match" 排序带个性化重排、两次请求顺序会变 → 用 sort=title 保证分页稳定，
     抓完和 totalResults 对账，缺了换一个排序补抓
   - robots.txt 禁止 /c/*r=* 和 /c/*q=*，REI 页面自带的分页 / 筛选链接都带 r= / ir=，所以只用 page/pagesize/sort/json
2. 性别：REI 大多数分类都有按性别分的子分类（/c/mens-ski-jackets、/c/womens-ski-jackets、/c/kids-ski-jackets），
   或者筛选地址（/c/downhill-skis/f/g-mens）。商品出现在哪个列表里就是什么性别；同时出现在男款和女款列表里 = unisex
   （REI 把中性款同时放进男女两个分类）。只配置了性别列表、没有主列表时，这些列表的并集就是全部商品，
   一遍就拿到商品和性别，请求数减半。
3. 尺码：列表里的 sizeDetails 给出有货的尺码，但它来自搜索索引，会滞后（实测 50 个打折双板里 6 个漏列了有货长度）。
   所以对打折商品（按折扣从大到小，最多 max_detail_pages 个）请求 /rest/products/{id}（REI 对比功能用的接口），
   用它的 variants 覆盖每个尺码的价格 / 原价 / 是否有货。
   商品页 HTML（/product/...）会返回 Akamai 行为验证页（HTTP 200，Fetcher 识别不出来），所以完全不抓 HTML 商品页；
   遇到验证页一律当作 BlockedError，不做任何绕过。

REI Outlet（原 REI Garage）已并入普通分类的 Deals（/rei-garage/... 会 301 到 .../f/scd-deals），
所以普通分类已包含 Outlet 商品，都是新品（condition = new）。
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from urllib.parse import urlencode

from ..net import BlockedError, FetchError
from .base import NOT_SKI_RE, Adapter, RawProduct, SizeOption, to_float

# 和 REI 前端 fetch() 一样的 Accept。/rest/products/{id} 会按 Accept 做内容协商：
# Chrome 导航用的默认 Accept 偏好 application/xml，会拿到 XML（而且 CDN 不按 Accept 区分缓存）。
JSON_HEADERS = {"Accept": "application/json, text/plain, */*"}

PAGE_SIZE = 90                    # REI 只接受 30 / 90
SORTS = ("title", "max-price")    # 第二个排序只在第一轮对账缺货时才用
MAX_PAGES = 20                    # 单个列表的翻页上限（防御性）
PATH_KEYS = ("category_path", "category_paths", "gender_paths", "exclude_paths")

# 每个分类的默认抓取配置（合并后的 cfg 里只要有任何一个路径键——比如 categories.toml 的 [rei.<分类>] 写了——
# 这个分类的路径就完全以 cfg 为准）：
#   category_paths   主列表（这些列表里的商品都要）
#   gender_paths     {men/women/unisex/kids: 路径或路径列表}：性别来源，同时也是商品来源
#   exclude_paths    排除列表（比如越野滑雪服）
#   max_detail_pages 最多调用多少次商品接口（只针对打折商品，折扣从大到小）
# REI 没有的分类（protection 护具、midlayer 滑雪专用中间层）不在这里。
DEFAULT_CATEGORY_CFG: dict[str, dict] = {
    "ski": {
        "category_paths": ["/c/downhill-skis"],            # 含 Backcountry Skis；越野板在 /c/cross-country-skis
        "gender_paths": {"men": "/c/downhill-skis/f/g-mens", "women": "/c/downhill-skis/f/g-womens",
                         "unisex": "/c/downhill-skis/f/g-unisex", "kids": "/c/kids-downhill-skis"},
        "max_detail_pages": 80,
    },
    "binding": {
        "category_paths": ["/c/downhill-ski-bindings", "/c/alpine-touring-ski-bindings"],
        "max_detail_pages": 15,
    },
    "boot": {
        "gender_paths": {"men": ["/c/mens-downhill-ski-boots", "/c/mens-alpine-touring-ski-boots"],
                         "women": ["/c/womens-downhill-ski-boots", "/c/womens-alpine-touring-ski-boots"],
                         "kids": "/c/kids-downhill-ski-boots"},
        "max_detail_pages": 30,
    },
    "pole": {
        "category_paths": ["/c/backcountry-ski-poles"],
        "gender_paths": {"men": "/c/mens-ski-poles", "women": "/c/womens-ski-poles", "kids": "/c/kids-ski-poles"},
        "max_detail_pages": 8,
    },
    "helmet": {
        "gender_paths": {"men": "/c/mens-snowsports-helmets", "women": "/c/womens-snowsports-helmets",
                         "kids": "/c/kids-snowsports-helmets"},
        "max_detail_pages": 12,
    },
    "goggle": {
        "gender_paths": {"men": "/c/mens-snowsports-goggles", "women": "/c/womens-snowsports-goggles",
                         "kids": "/c/kids-snowsports-goggles"},
        "max_detail_pages": 15,
    },
    "jacket": {
        "gender_paths": {"men": "/c/mens-ski-jackets", "women": "/c/womens-ski-jackets", "kids": "/c/kids-ski-jackets"},
        "exclude_paths": ["/c/cross-country-ski-jackets"],
        "max_detail_pages": 30,
    },
    "pants": {
        "gender_paths": {"men": "/c/mens-ski-pants", "women": "/c/womens-ski-pants", "kids": "/c/kids-ski-pants"},
        "exclude_paths": ["/c/cross-country-ski-pants"],
        "max_detail_pages": 25,
    },
    "suit": {
        "gender_paths": {"women": "/c/womens-snowsuits", "kids": ["/c/kids-snowsuits", "/c/kids-buntings"]},
        "max_detail_pages": 5,
    },
    "baselayer": {
        "gender_paths": {"men": "/c/mens-base-layers", "women": "/c/womens-base-layers", "kids": "/c/kids-base-layers"},
        "max_detail_pages": 12,
    },
    "glove": {
        "gender_paths": {"men": "/c/mens-ski-gloves-and-mittens", "women": "/c/womens-ski-gloves-and-mittens",
                         "kids": "/c/kids-ski-gloves-and-mittens"},
        "max_detail_pages": 15,
    },
    "sock": {
        "gender_paths": {"men": "/c/mens-ski-socks", "women": "/c/womens-ski-socks", "kids": "/c/kids-ski-socks"},
        "max_detail_pages": 8,
    },
    "facewear": {
        "gender_paths": {"men": ["/c/mens-neck-gaiters", "/c/mens-balaclavas"],
                         "women": ["/c/womens-neck-gaiters", "/c/womens-balaclavas"],
                         "kids": ["/c/kids-neck-gaiters", "/c/kids-balaclavas"]},
        "max_detail_pages": 8,
    },
    "hat": {
        "gender_paths": {"men": ["/c/mens-beanies", "/c/mens-snow-headbands"],
                         "women": ["/c/womens-beanies", "/c/womens-snow-headbands"],
                         "kids": "/c/kids-beanies"},
        "max_detail_pages": 8,
    },
    "backpack": {
        "category_paths": ["/c/ski-backpacks"],
        "gender_paths": {"women": "/c/ski-backpacks/f/g-womens"},
        "max_detail_pages": 8,
    },
    "bag": {
        "category_paths": ["/c/ski-bags", "/c/ski-boot-bags", "/c/ski-gear-bags"],
        "max_detail_pages": 5,
    },
    "avalanche": {
        "category_paths": ["/c/avalanche-safety-gear"],
        "max_detail_pages": 8,
    },
    "skin": {
        "category_paths": ["/c/climbing-skins"],
        "exclude_paths": ["/c/ski-crampons"],              # REI 把雪板冰爪也放在 climbing-skins 里（归 accessory）
        "max_detail_pages": 5,
    },
    "tuning": {
        "category_paths": ["/c/ski-tuning-and-tools"],
        "max_detail_pages": 5,
    },
    "accessory": {  # 只放“剩下的”：靴子配件/烘鞋器、暖宝、替换镜片、头盔配件、背板带、刹车/零件、雪板冰爪、锁
        "category_paths": ["/c/boot-accessories", "/c/boot-dryers", "/c/hand-and-foot-warmers",
                           "/c/goggle-accessories", "/c/snowsports-helmet-accessories", "/c/ski-carriers",
                           "/c/ski-binding-accessories", "/c/ski-crampons", "/c/ski-locks"],
        "max_detail_pages": 5,
    },
}

# Akamai Bot Manager 的行为验证页是 HTTP 200，net.looks_blocked() 只看 403/429/503 等状态码，抓不到它
_CHALLENGE_MARKERS = ("sec-if-cpt-container", "behavioral-content", "scf-akamai", "px-captcha",
                      "captcha-delivery", "access denied", "request unsuccessful")

# 双板列表卡片上的 "Terrain" 属性 → 类型线索
TERRAIN_HINTS = {
    "groomed and powder": "all-mountain",
    "groomed": "frontside",
    "powder": "powder",
    "backcountry": "touring",
}

_FALSE_POSITIVE_RE = re.compile(r"\bunleashed\b", re.I)   # Nordica Unleashed（老版 NOT_SKI_RE 的 leash 会误中）
_EXTRA_NOT_SKI_RE = re.compile(
    r"\bpackages?\b|\bmetal[- ]edge\b|\bsnowshoes?\b|\bski[- ]?(?:and|&|\+)[- ]?boots?\b", re.I)
_BINDINGS_RE = re.compile(r"\bbindings?\b", re.I)
# REI 的雪板标题都是复数 "Skis"（或 "... with Bindings"）；"Pivot 15 GW Ski Bindings" 这种是单卖固定器
_SKI_PRODUCT_RE = re.compile(r"\bskis\b|\bwith bindings?\b", re.I)
_YEAR_RE = re.compile(r"\b20\d\d\b")
_SLUG_YEAR_RE = re.compile(r"-(20\d\d)(20\d\d)?$")
_USED_RE = re.compile(r"re/?supply|\bpre-?owned\b|\bused\b", re.I)
_GENDER_PREFIX_RE = re.compile(r"^(?:men's|women's|kids'|boys'|girls'|unisex|toddlers'|infants')\s+", re.I)
_TOURING_PATH_RE = re.compile(r"alpine-touring|backcountry|telemark")


def is_alpine_ski(title: str) -> bool:
    """双板专用的过滤（REI 的 downhill-skis 分类本身已经很干净，这里只是安全网）。"""
    t = _FALSE_POSITIVE_RE.sub(" ", title or "")
    if NOT_SKI_RE.search(t) or _EXTRA_NOT_SKI_RE.search(t):
        return False
    if _BINDINGS_RE.search(t) and not _SKI_PRODUCT_RE.search(t):   # 只有固定器
        return False
    return True


def _rei_gender(label: str | None) -> str | None:
    """REI 的 Gender 属性值（Men's / Women's / Unisex / Boys' / Girls' / Kids'）→ men/women/unisex/kids。"""
    t = (label or "").lower()
    if not t:
        return None
    if any(k in t for k in ("kid", "boy", "girl", "junior", "youth", "toddler", "infant", "baby")):
        return "kids"
    if "unisex" in t:
        return "unisex"
    if "women" in t:
        return "women"
    if re.search(r"(?<![a-z])men", t):
        return "men"
    return None


def _title_gender(title: str) -> str | None:
    """兜底：REI 的标题里一般带性别，如 "Enforcer 99 Skis - Men's - 2025/2026"。"""
    t = (title or "").lower().replace("’", "'")
    if re.search(r"\b(?:kids?|boys?|girls?|junior|youth|toddlers?|infants?|baby)\b|\bjr\b", t):
        return "kids"
    women = bool(re.search(r"\bwomen'?s\b", t))
    men = bool(re.search(r"(?<![a-z])men'?s\b", t))
    if women and men:
        return "unisex"
    if women:
        return "women"
    if men:
        return "men"
    return None


def _as_int(x) -> int | None:
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _as_paths(v) -> list[str]:
    if not v:
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v if x]


def _challenged(text: str) -> bool:
    head = (text or "")[:30000].lower()
    return any(m in head for m in _CHALLENGE_MARKERS)


def _tiles(r: dict) -> dict[str, str]:
    """列表卡片上的属性（双板：Terrain / Waist Width / Sidecut Radius；其他分类各不相同）。"""
    out: dict[str, str] = {}
    for t in r.get("tileAttributes") or []:
        name = str(t.get("title") or "").strip()
        vals = [str(v) for v in t.get("values") or [] if v]
        if name and vals:
            out[name] = ", ".join(vals)
    return out


def _size_label(label) -> str:
    s = str(label or "").strip()
    return "One Size" if s.upper() == "NONE" else s   # REI 用 "NONE" 表示均码


def listing_prices(r: dict) -> tuple[float | None, float | None, float | None]:
    """返回 (当前最低价, 当前最高价, 原价)；没打折时原价为 None。

    REI 的 priceDisplay 类型：PRICE / MAP_PRICE（原价，compareAt == 售价）、PRICE_RANGE（各尺码/颜色不同价，
    compareAt 为空）、SALE_PRICE（打折：min = 售价，compareAt = 原价）。
    只在 REI 明确标了打折（discounted / SALE / CLEARANCE）且原价 > 售价时才给 compare_at。
    """
    dp = r.get("displayPrice") or {}
    pd = dp.get("priceDisplay") or {}
    pmin = to_float(dp.get("min")) or to_float(r.get("regularPrice"))
    pmax = to_float(dp.get("max")) or pmin
    kind = f"{pd.get('priceDisplayType') or ''} {pd.get('priceType') or ''}".upper()
    discounted = bool(pd.get("discounted")) or "SALE" in kind or "CLEARANCE" in kind
    compare_at = None
    if discounted and pmin:
        for cand in (to_float(dp.get("compareAt")), to_float(r.get("regularPrice"))):
            if cand and cand > pmin + 0.005:
                compare_at = cand
                break
    return pmin, pmax, compare_at


def listing_sizes(r: dict, price: float | None, compare_at: float | None,
                  cm: Callable[[str], int | None]) -> list[SizeOption]:
    """列表里的 sizeDetails：REI 只列出网上有货的尺码（filterState = "available"）。"""
    out: list[SizeOption] = []
    seen: set[str] = set()
    for s in (r.get("sizeDetails") or {}).get("sizeDetails") or []:
        label = _size_label(s.get("size"))
        if not label or label in seen:
            continue
        seen.add(label)
        state = str(s.get("filterState") or "").lower()
        out.append(SizeOption(label=label, cm=cm(label), available=state in ("available", ""),
                              price=price, compare_at=compare_at))
    return out


def detail_sizes(variants: list[dict], cm: Callable[[str], int | None],
                 order: list[str] | None = None) -> list[SizeOption]:
    """/rest/products/{id} 的 variants（颜色 × 尺码）→ 每个尺码一个 SizeOption：
    任一颜色有货就算有货，价格取有货颜色里最便宜的那个。按 REI 页面上的尺码顺序（order）排列。"""
    by_label: dict[str, SizeOption] = {}
    for v in variants:
        label = _size_label(v.get("size"))
        if not label:
            continue
        price = to_float(v.get("price"))
        cmp_ = to_float(v.get("compare_at"))
        if cmp_ is not None and (price is None or cmp_ <= price + 0.005):
            cmp_ = None
        status = str(v.get("status") or "").upper()
        avail = bool(v.get("available")) and status not in ("SOLD_OUT", "UNAVAILABLE", "DISCONTINUED")
        cur = by_label.get(label)
        if cur is None:
            by_label[label] = SizeOption(label, cm(label), avail, price, cmp_)
            continue
        better = (avail and not cur.available) or (
            avail == cur.available and price is not None and (cur.price is None or price < cur.price))
        if better:
            cur.price, cur.compare_at = price, cmp_
        cur.available = cur.available or avail
    sizes = list(by_label.values())
    rank = {_size_label(x): i for i, x in enumerate(order or [])}
    if sizes and all(s.cm for s in sizes):
        sizes.sort(key=lambda s: s.cm)
    elif rank:
        sizes.sort(key=lambda s: rank.get(s.label, len(rank)))
    return sizes


class ReiAdapter(Adapter):
    """cfg（零售商配置 + 分类配置）可选键：
    - category_paths / category_path、gender_paths、exclude_paths：见 DEFAULT_CATEGORY_CFG
    - max_detail_pages：这个分类最多调用多少次商品接口（打折商品，折扣从大到小）
    - detail_mode（默认 "discounted"）："discounted" = 打折商品都抓详情（受上限约束）；
      "auto" = 只抓「打折且各尺码价格不同 / 列表没给尺码」的；"off" = 不抓详情
    """

    platform = "rei"

    # ------------------------------------------------------------------ 配置
    def _cat_cfg(self) -> dict:
        """这个分类的路径配置：toml 里写了任何路径键就完全用 toml 的，否则用内置默认。"""
        if any(self.cfg.get(k) for k in PATH_KEYS):
            return self.cfg
        return DEFAULT_CATEGORY_CFG.get(self.category, {})

    def _paths(self) -> tuple[list[str], dict[str, list[str]], list[str]]:
        c = self._cat_cfg()
        # 分类表里写了 category_paths（哪怕是空列表）就不再看 category_path：
        # 零售商级别（双板）的 category_path 会被合并进每个分类，不能让它漏进雪服等分类
        main = _as_paths(c.get("category_paths")) if "category_paths" in c else _as_paths(c.get("category_path"))
        genders = {g: _as_paths(p) for g, p in (c.get("gender_paths") or {}).items() if _as_paths(p)}
        return main, genders, _as_paths(c.get("exclude_paths"))

    def _max_detail(self) -> int:
        v = self.cfg.get("max_detail_pages")
        if v is None:
            v = DEFAULT_CATEGORY_CFG.get(self.category, {}).get("max_detail_pages", 20)
        return max(0, _as_int(v) or 0)

    # ------------------------------------------------------------------ 主流程
    def fetch(self) -> Iterator[RawProduct]:
        self._blocked = False
        self.stats = {"listing_requests": 0, "detail_requests": 0, "listed": 0, "reported_total": None,
                      "skipped": 0, "skipped_titles": [], "excluded": 0, "detail_failed": 0, "gender_src": {}}
        main, gender_paths, exclude = self._paths()
        if not main and not gender_paths:
            self.log(f"[rei] no REI pages configured for category {self.category!r}; skipping")
            return

        items, labels, sources, members = self._collect(main, gender_paths, exclude)
        self.stats["listed"] = len(items)

        products: list[RawProduct] = []
        needs: dict[str, bool] = {}
        for pid, r in items.items():
            built = self._build(pid, r, labels.get(pid), sources.get(pid, set()), members)
            if built is None:
                self.stats["skipped"] += 1
                self.stats["skipped_titles"].append(str(r.get("title") or pid))
                continue
            products.append(built[0])
            needs[pid] = built[1]

        # 商品接口：只针对打折商品，折扣从大到小，每个分类最多 max_detail_pages 个
        mode = str(self.cfg.get("detail_mode") or "discounted").lower()
        if mode != "off":
            cands = [p for p in products if p.compare_at and p.price and (mode == "discounted" or needs[p.external_id])]
            cands.sort(key=lambda p: p.price / p.compare_at)
            for p in cands[: self._max_detail()]:
                if self._blocked:
                    break
                self._enrich(p)

        for p in products:
            src = p.extra.get("gender_src") or "none"
            self.stats["gender_src"][src] = self.stats["gender_src"].get(src, 0) + 1
            yield p

    # ------------------------------------------------------------------ 列表
    def _get_json(self, url: str):
        resp = self.fetcher.get(url, headers=JSON_HEADERS)
        text = resp.text or ""
        try:
            return json.loads(text)
        except ValueError:
            if _challenged(text):
                raise BlockedError(url, resp.status_code, "Akamai bot-manager challenge page") from None
            raise FetchError(url, resp.status_code, "expected JSON") from None

    def _search(self, path: str, page: int, sort: str) -> dict:
        params: dict = {"page": page} if page > 1 else {}
        params.update(pagesize=PAGE_SIZE, sort=sort, json="true")
        url = f"{self.base_url}{path}?{urlencode(params)}"
        data = self._get_json(url)
        self.stats["listing_requests"] += 1
        sr = data.get("searchResults") if isinstance(data, dict) else None
        if not isinstance(sr, dict):
            raise FetchError(url, None, "no searchResults in JSON")
        code = _as_int((sr.get("searchStatus") or {}).get("responseCode"))
        if code not in (None, 200):
            raise FetchError(url, code, "REI search error")
        return sr

    def _crawl(self, path: str) -> tuple[dict[str, dict], int | None, str | None]:
        """翻完一个分类 / 筛选列表的所有页，返回 ({prodId: 列表条目}, REI 报告的总数, 分类名)。"""
        items: dict[str, dict] = {}
        total: int | None = None
        label: str | None = None
        for sort in SORTS:
            page = pages = 1
            while page <= min(pages, MAX_PAGES):
                sr = self._search(path, page, sort)
                if total is None:
                    total = _as_int((sr.get("query") or {}).get("totalResults"))
                    label = (sr.get("pageMetaData") or {}).get("pageTitle")
                pages = _as_int((sr.get("pagination") or {}).get("totalPages")) or 1
                results = sr.get("results") or []
                for r in results:
                    pid = str(r.get("prodId") or "").strip()
                    if pid and pid not in items:
                        items[pid] = r
                if not results:
                    break
                page += 1
            if total is None or len(items) >= total:
                break
            self.log(f"[rei] {path}: {len(items)}/{total} after sort={sort}; re-paging with another sort")
        return items, total, label

    def _collect(self, main: list[str], gender_paths: dict[str, list[str]], exclude: list[str]):
        """商品 = 主列表 ∪ 性别列表 − 排除列表；同时记下每个商品在哪些性别列表里出现。"""
        items: dict[str, dict] = {}
        labels: dict[str, str] = {}
        sources: dict[str, set[str]] = {}
        members: dict[str, set[str]] = {}
        reported = 0

        def add(path: str, got: dict[str, dict], label: str | None):
            for pid, r in got.items():
                items.setdefault(pid, r)
                if label and pid not in labels:
                    labels[pid] = _GENDER_PREFIX_RE.sub("", label).strip()
                sources.setdefault(pid, set()).add(path)

        main_errors: list[FetchError] = []
        for path in main:
            try:
                got, total, label = self._crawl(path)
            except BlockedError:
                raise                              # 被反爬拦截：立即停止，如实报告
            except FetchError as e:                # 某个小分类改名 / 下架：记下来，其余照抓
                main_errors.append(e)
                self.log(f"[rei] {self.category} list {path} failed: {e}")
                continue
            reported += total or 0
            add(path, got, label)
            self.log(f"[rei] {self.category} {path}: {len(got)} (REI reports {total})")
        if main and len(main_errors) == len(main):
            raise main_errors[0]                   # 主列表全部失败 → 这个分类记为失败

        ok_gender = 0
        errors: list[Exception] = []
        for gender, paths in gender_paths.items():
            for path in paths:
                if self._blocked:
                    break
                try:
                    got, total, label = self._crawl(path)
                except BlockedError as e:
                    if not main:
                        raise                      # 性别列表就是商品来源，被拦截就如实报告
                    self._blocked = True
                    self.log(f"[rei] blocked on {path}; genders fall back to titles ({e})")
                    break
                except FetchError as e:            # 含 RobotsDisallowed
                    errors.append(e)
                    self.log(f"[rei] {self.category} gender list {path} failed: {e}")
                    continue
                ok_gender += 1
                members.setdefault(gender, set()).update(got)
                add(path, got, label)
                if not main:
                    reported += total or 0
                self.log(f"[rei] {self.category} {gender} {path}: {len(got)} (REI reports {total})")
        if not main and not ok_gender and errors:
            raise errors[0]

        for path in exclude:
            if self._blocked:
                break
            try:
                got, _, _ = self._crawl(path)
            except FetchError as e:                # 含 BlockedError：排除失败不致命
                self._blocked = self._blocked or isinstance(e, BlockedError)
                self.log(f"[rei] {self.category} exclude list {path} failed: {e}")
                continue
            dropped = [pid for pid in got if pid in items]
            for pid in dropped:
                del items[pid]
            self.stats["excluded"] += len(dropped)
            self.log(f"[rei] {self.category} excluded {len(dropped)} via {path}")

        self.stats["reported_total"] = reported
        return items, labels, sources, members

    # ------------------------------------------------------------------ 组装
    def _keep(self, raw_title: str) -> bool:
        if self.category == "ski":
            return is_alpine_ski(raw_title)
        # 只看标题：同一页所有商品的 product_type 都一样，传进去反而会盖过标题的纠错（比如手套页里的帽子）
        return self.wanted(raw_title)

    def _build(self, pid: str, r: dict, label: str | None, sources: set[str],
               members: dict[str, set[str]]) -> tuple[RawProduct, bool] | None:
        raw_title = str(r.get("title") or r.get("cleanTitle") or "").strip()
        if not raw_title or not self._keep(raw_title):
            return None
        brand = str(r.get("brand") or "").strip() or None

        # 标题 = 品牌 + REI 标题（REI 商品接口里的完整标题就是这样）；双板的年份 REI 已写在标题里（"2025/2026"）
        title = raw_title
        if brand and not title.lower().startswith(brand.lower()):
            title = f"{brand} {title}"
        link = str(r.get("link") or f"/product/{pid}")
        if self.category == "ski" and not _YEAR_RE.search(title):
            m = _SLUG_YEAR_RE.search(link.rstrip("/"))
            if m:
                title += f" {m.group(1)}/{m.group(2)}" if m.group(2) else f" {m.group(1)}"

        price, pmax, compare_at = listing_prices(r)
        uniform = price is not None and pmax is not None and abs(pmax - price) < 0.005
        sizes = listing_sizes(r, price if uniform else None, compare_at if uniform else None, self.size_cm)

        gender, gsrc = self._gender_for(pid, raw_title, members)
        tiles = _tiles(r)
        hints: list[str] = []
        if self.category == "ski":
            for t in tiles.get("Terrain", "").split(","):
                hint = TERRAIN_HINTS.get(t.strip().lower())
                if hint and hint not in hints:
                    hints.append(hint)
        elif any(_TOURING_PATH_RE.search(s) for s in sources):
            hints.append("touring")

        body_parts = [str(r.get("description") or r.get("benefit") or "").strip()]
        body_parts += [f"{k}: {v}." for k, v in tiles.items()]
        body = " ".join(p for p in body_parts if p) or None

        image = None
        colors = (r.get("colorDetails") or {}).get("colors") or []
        if colors and colors[0].get("imageURIs"):
            image = colors[0]["imageURIs"][0]
        image = image or r.get("thumbnailImageLink")
        if image and image.startswith("/"):
            image = self.base_url + image

        listed_avail = r.get("available")
        available = any(s.available for s in sizes) if sizes else (None if listed_avail is None else bool(listed_avail))
        if listed_avail is False:
            available = False

        condition = "used" if (link.startswith("/used") or _USED_RE.search(raw_title)) else "new"
        pd = (r.get("displayPrice") or {}).get("priceDisplay") or {}
        extra = {
            "style_id": pid,
            "price_type": pd.get("priceType") or pd.get("priceDisplayType"),
            "price_max": pmax,
            "percent_off": to_float(r.get("percentageOff")),
            "rating": to_float(r.get("rating")),
            "reviews": _as_int(r.get("reviewCount")),
            "specs": tiles,
            "rei_category": label,
            "outlet": bool(r.get("outlet")),
            "gender_src": gsrc,
            "detail": False,
        }
        if self.category == "ski":
            extra.update(terrain=tiles.get("Terrain"), waist_width=tiles.get("Waist Width"),
                         sidecut_radius=tiles.get("Sidecut Radius"))
        product = RawProduct(
            retailer_id=self.rid,
            external_id=pid,
            url=link if link.startswith("http") else self.base_url + link,
            title=title,
            price=price,
            compare_at=compare_at,
            currency="USD",
            vendor=brand,
            product_type=label,
            tags=[b["title"] for b in r.get("badgeData") or [] if b.get("title")],
            image=image,
            sizes=sizes,
            available=available,
            gender_hint=gender,
            type_hints=hints,
            body_text=body,
            condition_hint=condition,
            category=self.category,
            extra=extra,
        )
        return product, (not sizes or not uniform)

    @staticmethod
    def _gender_for(pid: str, title: str, members: dict[str, set[str]]) -> tuple[str | None, str | None]:
        hit = {g for g, ids in members.items() if pid in ids}
        if {"men", "women"} <= hit:        # REI 把中性款同时放进男女两个分类
            return "unisex", "facet"
        for g in ("kids", "unisex", "men", "women"):
            if g in hit:
                return g, "facet"
        g = _title_gender(title)
        return g, ("title" if g else None)

    # ------------------------------------------------------------------ 详情
    def _detail(self, pid: str) -> dict:
        url = f"{self.base_url}/rest/products/{pid}"
        self.stats["detail_requests"] += 1
        resp = self.fetcher.get(url, headers=JSON_HEADERS)
        body = resp.content or b""
        head = body[:300].lstrip()
        if head.startswith(b"{"):
            d = json.loads(body)
            return {
                "gender": d.get("gender"),
                "outlet": bool(d.get("isOutlet")),
                "category": (d.get("primaryCategory") or {}).get("label"),
                "order": [s.get("label") for s in d.get("sizes") or [] if isinstance(s, dict)],
                "variants": [{"size": v.get("size"), "available": v.get("isAvailable"), "status": v.get("status"),
                              "price": v.get("price"), "compare_at": v.get("compareAtPrice")}
                             for v in d.get("variants") or []],
            }
        if head.startswith(b"<?xml") or head.startswith(b"<product"):
            # CDN 里可能缓存着 XML 版本（别人用浏览器默认 Accept 请求过）
            root = ET.fromstring(body)
            return {
                "gender": root.findtext("gender"),
                "outlet": (root.findtext("isOutlet") or "").strip() == "true",
                "category": root.findtext("primaryCategory/label"),
                "order": [s.findtext("label") for s in root.findall("sizes")],
                "variants": [{"size": v.findtext("size"),
                              "available": (v.findtext("isAvailable") or "").strip() == "true",
                              "status": v.findtext("status"), "price": v.findtext("price"),
                              "compare_at": v.findtext("compareAtPrice")}
                             for v in root.findall("variants")],
            }
        if _challenged(body[:30000].decode("utf-8", "replace")):
            raise BlockedError(url, resp.status_code, "Akamai bot-manager challenge page")
        raise FetchError(url, resp.status_code, "unexpected product payload")

    def _enrich(self, p: RawProduct) -> None:
        try:
            det = self._detail(p.external_id)
        except BlockedError as e:
            self._blocked = True
            self.stats["detail_failed"] += 1
            self.log(f"[rei] blocked on product API, no more detail requests this run ({e})")
            return
        except (FetchError, ValueError, ET.ParseError) as e:
            self.stats["detail_failed"] += 1
            self.log(f"[rei] product API failed for {p.external_id}: {e}")
            return

        sizes = detail_sizes(det["variants"], self.size_cm, det.get("order"))
        if sizes:
            p.sizes = sizes
            priced = [s for s in sizes if s.price is not None]
            pool = [s for s in priced if s.available] or priced
            if pool:
                best = min(s.price for s in pool)
                cmps = [s.compare_at for s in pool if abs(s.price - best) < 0.005 and s.compare_at]
                p.price = best
                if cmps:
                    p.compare_at = max(cmps)
                elif p.compare_at is not None and p.compare_at <= best + 0.005:
                    p.compare_at = None
            p.available = any(s.available for s in sizes)
        g = _rei_gender(det.get("gender"))
        if not p.gender_hint and g:
            p.gender_hint, p.extra["gender_src"] = g, "detail"
        elif g and g != p.gender_hint:
            # REI 自己的数据偶尔不一致（分类/筛选 vs 商品接口），以分类为准，这里只记录下来
            p.extra["detail_gender"] = g
        if det.get("outlet"):
            p.extra["outlet"] = True
        if det.get("category") and not p.extra.get("rei_category"):
            p.extra["rei_category"] = det["category"]
        p.extra["detail"] = True
