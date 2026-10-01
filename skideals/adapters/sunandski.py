"""Sun & Ski Sports (sunandski.com；skichalet.com 会跳转到这里) —— Odoo 商城 + Constructor.io 搜索。支持全部滑雪装备分类。

为什么不直接解析 HTML：
- Odoo 的分类页是服务端用 Constructor.io 的结果渲染的（页面上有 data-cnstrc-browse / data-cnstrc-num-results），
  每页只有约 36 个商品，而且卡片上没有尺码；
- 商品页每个 ~4 MB（内嵌 base64 图片），而且 JSON-LD 里没有每个尺码的库存 —— 抓详情页既不礼貌也拿不到库存。

所以用网站前端自己加载的 Constructor.io 公开 browse 接口（GET，JSON）：
    https://ac.cnstrc.com/browse/group_id/<分组>?key=<公开 index key>&filters[name]=<商品类型>&num_results_per_page=200
- key 写在网站每个页面里：`window.cnstrc.indexKey = 'key_...'`（设计上就是公开的前端 key）；
  每次抓取先到分类页读一次（不写死在代码里），如果接口报 key 无效，再重新读取一次。
- 分类 = 分组 + "name"（商品类型）分面：25 = Snow（冬季全部），104 = Ski；袜子不在 Snow 分组里，用根分组 all。
  例：雪服 = group 25 + name=Insulated Snow Jackets；中间层 = Fleece / Vests / Insulated Jackets。见 DEFAULT_CATEGORIES。
- `variations_map` 参数让接口把每个商品的变体按 data.size 聚合成 [{size, price, compare}]，
  一次请求就拿到所有商品的全部尺码（长度 / 鞋码 / S-XL）与各尺码价格 —— 不需要任何详情页（max_detail_pages 无意义）。
  索引里只收录可售（有货）的变体，所以这些尺码都视为有货。
- 性别：对 Gender 分面（Men's / Women's / Kids / Girls / Boys / Unisex）逐个过滤，只取 id（fmt_options[fields]=id，
  每次 ~24 KB），任何分组都适用；同一商品同时在男款和女款里 → unisex。
- 双板额外：按 "Recommended Use" 分面取 id → type_hints；其中 Cross-Country 的直接排除（有的越野板标题里没有 nordic）。
- 年份：标题用 '27 这种写法 → 改成 2027；双板/雪鞋/固定器标题没写年份时用数据里的 years
  （Fall-Winter 季的起始年份，'27 款对应 years=2026）+1 补上。

每个分类约 8~15 个请求，全部分类约 200 个。
robots.txt：sunandski.com 对 * 没有任何 Disallow；ac.cnstrc.com 没有 robots.txt（404）。
没有的分类：连体服、护具、雪崩装备、止滑带（Constructor 分面里没有对应商品类型）。
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from urllib.parse import urlencode, urlsplit, urlunsplit

from ..categories import APPAREL_ORDER, BY_ID, norm_size
from ..net import FetchError
from .base import NOT_SKI_RE, Adapter, RawProduct, SizeOption, to_float

API = "https://ac.cnstrc.com"
KEY_PAGE = "/shop/category/ski-equipment-104"


def _spec(group: str, *names: str) -> list[dict]:
    return [{"group_id": group, "filters": {"name": list(names)}}]


DEFAULT_CATEGORIES: dict[str, list[dict]] = {
    "ski": _spec("104", "Skis"),
    "boot": _spec("25", "Ski Boots"),
    "binding": _spec("25", "Ski Bindings"),
    "pole": _spec("25", "Ski Poles"),
    "helmet": _spec("25", "Snow Helmets"),
    "goggle": _spec("25", "Snow Goggles"),
    "jacket": _spec("25", "Insulated Snow Jackets"),
    "pants": _spec("25", "Snow Pants"),
    "midlayer": _spec("25", "Fleece", "Vests", "Insulated Jackets"),
    "baselayer": _spec("all", "Base Layer Bottoms", "Base Layer Tops"),
    "glove": _spec("25", "Gloves", "Mittens"),
    "sock": _spec("all", "Snow Socks"),
    "facewear": _spec("25", "Balaclavas", "Neck Gaiters"),
    "hat": _spec("25", "Beanies"),
    "backpack": _spec("25", "Backpacks"),
    "bag": _spec("25", "Ski Bags", "Boot Bags", "Hard Cases"),
    "tuning": _spec("25", "Snow Tuning & Tools"),
    "accessory": _spec("25", "Hand & Foot Warmers", "Snow Gear Accessories", "Snow Helmet Accessories"),
}
PER_PAGE = 200
MAX_PAGES = 15
YEAR_CATEGORIES = {"ski", "boot", "binding"}   # 这些品类的 years 字段才能当“型号年份”用

VARIATIONS_MAP = json.dumps({
    "group_by": [{"name": "size", "field": "data.size[0]"}],
    "values": {
        "price": {"aggregation": "min", "field": "data.price.retail"},
        "compare": {"aggregation": "max", "field": "data.price.compare_list_price"},
    },
    "dtype": "array",
}, separators=(",", ":"))

# 仅用于双板
EXTRA_NOT_SKI_RE = re.compile(
    r"positrack|\bnnn\b|prolink|turnamic|harness|edgie|wedgie|chair ?lifter|backpack|"
    r"tip ?(?:lock|connector|clip)|\bpackage\b|snowshoe|water ?ski|wakeboard",
    re.I,
)
XC_USE_RE = re.compile(r"cross[- ]?country|nordic|\bxc\b", re.I)
GENDER_MAP = [
    (re.compile(r"^wom|^ladies|^female", re.I), "women"),
    (re.compile(r"^men|^male", re.I), "men"),
    (re.compile(r"kid|boy|girl|junior|youth|child|toddler", re.I), "kids"),
    (re.compile(r"unisex|adult", re.I), "unisex"),
]
TYPE_MAP = {
    "all-mountain": "all-mountain", "freeride": "freeride", "freestyle": "freestyle",
    "on-piste": "frontside", "park & pipe": "park", "backcountry": "touring", "touring": "touring",
    "race": "race", "racing": "race", "powder": "powder",
}
YEAR_TICK_RE = re.compile(r"\s*['’‘`](\d{2})\s*$")


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


def _gender_of(value: str) -> str | None:
    for rx, g in GENDER_MAP:
        if rx.search(value.strip()):
            return g
    return None


def _resolve_gender(gs: set[str]) -> str | None:
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
    if re.search(r"\bblem\b|b-grade", t):
        return "blem"
    return "new"


def fix_title(title: str, years: list | None, use_years: bool = True) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    m = YEAR_TICK_RE.search(title)
    if m:  # "... Skis '27" -> "... Skis 2027"
        return title[: m.start()] + f" 20{m.group(1)}"
    if not use_years or re.search(r"\b(?:19|20)\d\d\b", title):
        return title
    try:  # years = Fall-Winter 季的起始年份（'27 款 = 2026），型号年份 = +1
        y = max(int(str(x)) for x in (years or []) if str(x).isdigit())
    except ValueError:
        return title
    return f"{title} {y + 1}" if 2010 <= y <= 2040 else title


class SunAndSkiAdapter(Adapter):
    platform = "odoo-constructor"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.api_key = self.cfg.get("api_key")
        self._key_refreshed = False

    # ---------- Constructor.io ----------
    def _read_key(self) -> str | None:
        html = self.fetcher.get_html(self.base_url + (self.cfg.get("key_page") or KEY_PAGE))
        m = re.search(r"indexKey\s*=\s*['\"](key_[A-Za-z0-9]+)['\"]", html)
        return m.group(1) if m else None

    def _browse(self, group_id: str, pairs: list[tuple[str, str]], page: int, ids_only: bool = False) -> dict:
        if not self.api_key:
            self.api_key = self._read_key()
            if not self.api_key:
                raise FetchError(self.base_url + KEY_PAGE, msg="Constructor.io index key not found")
        q = [("key", self.api_key), *pairs, ("num_results_per_page", str(PER_PAGE)), ("page", str(page))]
        q.append(("fmt_options[fields]", "id") if ids_only else ("variations_map", VARIATIONS_MAP))
        url = f"{API}/browse/group_id/{group_id}?{urlencode(q)}"
        try:
            return self.fetcher.get_json(url)["response"]
        except FetchError as e:
            if e.status in (400, 401, 403) and not self._key_refreshed and self._refresh_key():
                return self._browse(group_id, pairs, page, ids_only)
            raise

    def _refresh_key(self) -> bool:
        """index key 失效时，从网站分类页重新读取（页面里有 window.cnstrc.indexKey = 'key_...'）。"""
        self._key_refreshed = True
        key = self._read_key()
        if key and key != self.api_key:
            self.log(f"[{self.rid}] constructor key changed")
            self.api_key = key
            return True
        return False

    def _browse_all(self, group_id: str, pairs: list[tuple[str, str]], ids_only: bool = False):
        """翻页取完整结果；返回 (results, 第一页的 facets)。"""
        results, facets = [], []
        for page in range(1, MAX_PAGES + 1):
            resp = self._browse(group_id, pairs, page, ids_only)
            if page == 1:
                facets = resp.get("facets") or []
            batch = resp.get("results") or []
            results.extend(batch)
            if not batch or len(results) >= int(resp.get("total_num_results") or 0):
                break
        return results, facets

    # ---------- main ----------
    def fetch(self) -> Iterator[RawProduct]:
        specs = category_specs(self.cfg, self.category, DEFAULT_CATEGORIES)
        if not specs:
            self.log(f"[{self.rid}] no Constructor filters for '{self.category}' — skipped")
            return
        out: dict[str, RawProduct] = {}
        for spec in specs:
            gid = str(spec.get("group_id", "25"))
            pairs = []
            for k, v in (spec.get("filters") or {}).items():
                for val in (v if isinstance(v, list) else [v]):
                    pairs.append((f"filters[{k}]", str(val)))
            items, facets = self._browse_all(gid, pairs)
            facet_opts = {f.get("name"): [o.get("value") for o in f.get("options") or [] if o.get("count")]
                          for f in facets}

            genders: dict[str, set[str]] = {}
            if spec.get("gender"):
                for it in items:
                    genders.setdefault(str(it["data"]["id"]), set()).add(spec["gender"])
            elif items:
                for val in facet_opts.get("Gender") or []:
                    g = _gender_of(val)
                    if not g:
                        continue
                    for it in self._browse_all(gid, pairs + [("filters[Gender]", val)], ids_only=True)[0]:
                        genders.setdefault(str(it["data"]["id"]), set()).add(g)

            types: dict[str, set[str]] = {}
            excluded: set[str] = set()
            if self.category == "ski":
                for val in facet_opts.get("Recommended Use") or []:
                    ids = [str(it["data"]["id"]) for it in
                           self._browse_all(gid, pairs + [("filters[Recommended Use]", val)], ids_only=True)[0]]
                    if XC_USE_RE.search(val):
                        excluded.update(ids)
                    else:
                        t = TYPE_MAP.get(val.strip().lower(), val.strip().lower())
                        for i in ids:
                            types.setdefault(i, set()).add(t)

            skipped_noprice = bad = 0
            for it in items:
                try:
                    p = self._to_product(it, genders, types, excluded)
                except Exception as e:  # 单条数据格式异常不影响整个分类
                    bad += 1
                    if bad <= 3:
                        self.log(f"[{self.rid}] bad item {str(it.get('data', {}).get('id'))}: {type(e).__name__}: {e}")
                    continue
                if p is None:
                    continue
                if p.price is None:
                    skipped_noprice += 1
                    continue
                out.setdefault(p.external_id, p)
            self.log(f"[{self.rid}] {self.category} group {gid} {dict(spec.get('filters') or {})}: {len(items)} results"
                     + (f", skipped {skipped_noprice} without price" if skipped_noprice else ""))
        yield from out.values()

    def _keep(self, title: str) -> bool:
        if self.category == "ski":
            # 双板保持原有规则（NOT_SKI_RE + EXTRA）；不用 base.wanted()，它会把 “QST 92 w/ M10 Bindings” 这类板+固定器套装误判成固定器
            return not NOT_SKI_RE.search(title) and not EXTRA_NOT_SKI_RE.search(title)
        return self.wanted(title)

    def _to_product(self, it: dict, genders: dict, types: dict, excluded: set) -> RawProduct | None:
        d = it.get("data") or {}
        pid = str(d.get("id") or "")
        raw_title = str(it.get("value") or "").strip()
        if not pid or not raw_title or pid in excluded or not self._keep(raw_title):
            return None
        title = fix_title(raw_title, d.get("years"), use_years=self.category in YEAR_CATEGORIES)

        sizes: dict[str, SizeOption] = {}
        for v in it.get("variations_map") or []:
            label = str(v.get("size") or "").strip()
            if not label or not size_label_ok(self.category, label):
                continue
            price, cmp_ = to_float(v.get("price")), to_float(v.get("compare"))
            so = SizeOption(label=label, cm=self.size_cm(label), available=True, price=price,
                            compare_at=cmp_ if (cmp_ and price and cmp_ > price + 0.009) else None)
            prev = sizes.get(label)
            if prev is None or (price and prev.price and price < prev.price):
                sizes[label] = so
        size_list = sorted(sizes.values(), key=lambda s: size_sort_key(self.category, s))

        pr = d.get("price") or {}
        price = to_float(pr.get("retail"))
        compare = to_float(pr.get("compare_list_price"))
        live = [s.price for s in size_list if s.price]
        if live:
            price = min(live)
            cmps = [s.compare_at for s in size_list if s.compare_at]
            if cmps:
                compare = max(cmps)
        if not (compare and price and compare > price + 0.009):
            compare = None

        parts = urlsplit(d.get("url") or "")
        url = urlunsplit((parts.scheme or "https", parts.netloc or urlsplit(self.base_url).netloc, parts.path, "", ""))
        brand = str(d.get("Brand") or "").strip()   # 品牌 "686" 在 JSON 里是整数
        vendor = re.sub(r"\s+(?:Skis?|Snow)$", "", brand, flags=re.I) if brand else raw_title.split()[0]
        return RawProduct(
            retailer_id=self.rid,
            external_id=pid,
            url=url,
            title=title,
            price=price,
            compare_at=compare,
            currency="USD",
            vendor=vendor,
            product_type=None,  # 按网站的分类组抓取，没有单独的商品类型字段
            image=d.get("image_url"),
            sizes=size_list,
            available=bool(size_list) or price is not None,
            gender_hint=_resolve_gender(genders.get(pid, set())),
            type_hints=sorted(types.get(pid, set())),
            body_text=d.get("description"),
            condition_hint=condition_from_title(raw_title),
            extra={"raw_title": raw_title, "years": d.get("years"), "variation_id": d.get("variation_id")},
            category=self.category,
        )
