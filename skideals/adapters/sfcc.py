"""Salesforce Commerce Cloud（SFCC，原 Demandware）平台适配器：Christy Sports（全部滑雪装备分类）、The House（只抓双板）。

调度器按分类调用适配器：cfg = retailers.toml 里的零售商配置 + config/categories.toml 里的 [<零售商id>.<分类>] 表
+ cfg["category"] = <分类>（双板的配置仍在 retailers.toml 的零售商级）。每个分类自己的来源配置
（categories 分类页列表、max_detail_pages、include / exclude）的默认值见 CHRISTY_CATEGORY_DEFAULTS，配置文件里的同名键覆盖它。
注意：[<id>.<分类>] 表里要写上 categories 和 max_detail_pages，否则会继承零售商级（双板）的值；
万一漏写 categories，适配器能认出继承来的双板分类页列表并改用本分类的默认值。

Christy Sports（christysports.com）
  - 普通分类页 + `?start=N&sz=M` 分页。robots.txt 允许这两个参数；禁止的是 `*/demandware.store*`
    （所以不能用 Search-UpdateGrid 那个 AJAX 接口）、prefn / prefv / pmin / pmax / srule 和 q=。
  - 商品卡片 `div.product[data-pid]` 里有一段内联脚本，写着 gtmdata JSON：品牌 + Christy 自己的商品分类
    （"M ALL MOUNTAIN"、"M BASELAYER TOPS"、"Y SKI CUSHION SOCKS"、"DOUBLE SKI BAGS" …）；
    价格在 `.value[content]`；雪季写成 `Season: 25/26`（→ 标题末尾加 2026）。
  - 同一个分类页被两个分类共用时（中间层 / 保暖内衣共用 "Layers & Insulation"，背包 / 雪板包共用 "Bags, Packs & Travel"），
    用 include / exclude 正则按 Christy 的商品分类拆开（匹配 "分类 | 品类 | 部门 | 标题"），保证一个商品只进一个分类。
  - 卡片上没有尺码 → 只对打折商品抓详情页（每个分类最多 max_detail_pages 个，折扣大的优先），
    解析尺码按钮（缺货的带 unselectable）；尺码原样保留（S / M / L、26.5、90mm…），只有双板 / 雪杖换算厘米。
  - 性别：每个分类都分别抓男款 / 女款 / 儿童分类页（有的话）；同时出现在男款和女款里 → unisex；
    都不在 → 用 Christy 商品分类的前缀（M / W / Y / JR）兜底。
  - 二手 / 试板（demo）是在 Christy 的 eBay 店卖的，官网列表里基本没有；标题里出现 demo / used 时照样识别。

The House（the-house.com）—— 只支持双板（默认停用，见 retailers.toml 的说明）
  - robots.txt 禁止 start= / sz= / cgid= / format= 以及任何含 "search" 的路径 → SFCC 自带的分页参数都不能用。
  - 分类页（/skis、/ski-packages）的商品网格由浏览器端的 Algolia 渲染，HTML 里只有空壳和一份
    `algoliaData` 配置（应用 ID、只读搜索 key、索引名）。做法：先抓分类页（robots 允许），读出这份配置，
    再用同一个只读 key、按同样的分类 ID 查询 Algolia —— 得到的就是浏览器打开 /skis 时看到的数据
    （key 本身限定了 online + searchable + in_stock）。Algolia 主机没有 robots.txt 限制；请求同样走
    Fetcher（限速 + robots 检查），并带 analytics=false，不污染对方的搜索统计。
  - Algolia 记录里有每个长度的有货状态和价格、原价、性别（spGenderList），大多数商品不用抓详情页；
    只有没有尺码信息的打折商品才去抓详情页 /<pid>.html。
  - 找不到 algoliaData（或 cfg 里 listing_source = "html"）时，退回解析分类页里服务端渲染的卡片（如果有）。
"""
from __future__ import annotations

import html as _html
import json
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from functools import cached_property
from urllib.parse import quote, urlsplit, urlunsplit

from selectolax.parser import HTMLParser, Node

from ..net import BlockedError, FetchError
from .base import NOT_SKI_RE, Adapter, RawProduct, SizeOption, to_float

# ============================================================================ 默认配置
# 分类页项：path = 分类页路径；gender = 该分类页代表的性别（可选）；type = 类型线索（可选）；
#           include / exclude = 只对这个分类页生效的过滤正则（可选）。
# 分类级（和 categories 同级）也可以写 include / exclude，对该分类的所有分类页生效。
# 正则匹配的文本是 "Christy 商品分类 | 品类 | 部门 | 标题"，不区分大小写。
_MEN, _WOMEN, _KIDS = "men", "women", "kids"


def _gendered(men: str, women: str, kids: str | None = None, all_: str | None = None, **extra) -> list[dict]:
    rows = [{"path": all_}] if all_ else []
    rows += [{"path": men, "gender": _MEN}, {"path": women, "gender": _WOMEN}]
    if kids:
        rows.append({"path": kids, "gender": _KIDS})
    for r in rows:
        r.update(extra)
    return rows


_LAYERS = _gendered("/mens/mens-clothing/layers-and-insulation/", "/womens/womens-clothing/layers-and-insulation/",
                    "/kids/kids-clothing/layers-and-insulation/")
# 连体服只看标题：Christy 把很多童装夹克也归到 "TODDLER SUITS" / "JUNIOR SUITS"。
# 结尾的 [^|]*$ 表示“匹配必须落在最后一段（标题）里”。
_SUIT_RX = r"(?:\bsuits?\b|snow ?suit|one[- ]?piece|\bonesie\b|jumpsuit|coverall)[^|]*$"

CHRISTY_CATEGORY_DEFAULTS: dict[str, dict] = {
    "ski": {"max_detail_pages": 80, "categories": [
        {"path": "/ski/skis/"},                                    # 全部双板（其余分类页的超集）
        {"path": "/mens/mens-ski/skis/", "gender": _MEN},
        {"path": "/womens/womens-ski/skis/", "gender": _WOMEN},
        {"path": "/kids/kids-ski-and-snowboard/skis/", "gender": _KIDS},
        {"path": "/more-activities/backcountry/at-skis/", "type": "touring"},
    ]},
    "binding": {"max_detail_pages": 15, "exclude": r"snowboard|splitboard", "categories": [
        {"path": "/ski/bindings/"},
        *_gendered("/mens/mens-ski/ski-bindings/", "/womens/womens-ski/ski-bindings/",
                   "/kids/kids-ski-and-snowboard/ski-bindings/"),
        {"path": "/more-activities/backcountry/at-bindings/", "type": "touring"},
    ]},
    "boot": {"max_detail_pages": 45, "exclude": r"snowboard|splitboard|insoles?|footbeds?", "categories": [
        {"path": "/ski/boots/"},
        *_gendered("/mens/mens-ski/ski-boots/", "/womens/womens-ski/ski-boots/",
                   "/kids/kids-ski-and-snowboard/ski-boots/"),
        {"path": "/more-activities/backcountry/at-boots/", "type": "touring"},
    ]},
    "pole": {"max_detail_pages": 8, "exclude": r"trekking|hiking|nordic", "categories": [
        {"path": "/ski/poles/"},
        {"path": "/more-activities/backcountry/poles/", "type": "touring"},
    ]},
    "helmet": {"max_detail_pages": 10, "exclude": r"\bbike\b|cycling", "categories": _gendered(
        "/mens/mens-gear/helmets/", "/womens/womens-gear/helmets/", "/kids/kids-gear/helmets/", all_="/ski/helmets/")},
    "goggle": {"max_detail_pages": 0, "categories": _gendered(
        "/mens/mens-gear/goggles/", "/womens/womens-gear/goggles/", "/kids/kids-gear/goggles/", all_="/ski/goggles/")},
    # 雪服 / 雪裤页里的连体服归 suit，偶尔混进来的保暖内衣归 baselayer，半拉链上衣归 midlayer。
    # （抓绒夹克不排除：大多只挂在雪服页，排除掉就哪个分类都没有了）
    "jacket": {"max_detail_pages": 25, "exclude": _SUIT_RX + r"|baselayer|base layer|half[- ]zip|t-neck",
               "categories": _gendered(
        "/mens/mens-clothing/snow-jackets/", "/womens/womens-clothing/snow-jackets/", "/kids/kids-clothing/snow-jackets/")},
    "pants": {"max_detail_pages": 25, "exclude": _SUIT_RX + r"|baselayer|base layer", "categories": _gendered(
        "/mens/mens-clothing/snow-pants/", "/womens/womens-clothing/snow-pants/", "/kids/kids-clothing/snow-pants/")},
    # 女款连体服页里还有背带裤（归 pants）；童装连体服混在童装雪服 / 雪裤页里
    "suit": {"max_detail_pages": 5, "include": _SUIT_RX, "categories": [
        {"path": "/womens/womens-clothing/snowsuits/", "gender": _WOMEN},
        {"path": "/kids/kids-clothing/snow-jackets/", "gender": _KIDS},
        {"path": "/kids/kids-clothing/snow-pants/", "gender": _KIDS},
    ]},
    # "Layers & Insulation" 同时有中间层和保暖内衣，还混着法兰绒衬衫之类的休闲服（Christy 分类 L/S/CASUAL 等）。
    # 两边的 baselayer 正则互为补集，保证一件商品只进一个分类；注意 Nano Puff 这类保暖夹克的品类字段是
    # "CASUAL JACKETS"，所以只看分类名里的 l/s/casual、s/s casual、fashion/casual，
    # 而且标题里有 fleece / half-zip / vest / insulated 等中间层字眼时照样保留。
    "midlayer": {"max_detail_pages": 20,
                 "exclude": r"baselayer|base layer|underwear|logo|"
                            r"^(?!.*(?:fleece|half[- ]zip|quarter[- ]zip|1/4|¼|pullover|vest|hood(?:ie|y)?|insulated|"
                            r"puff|down\b)).*(?:l/s/casual|s/s casual|fashion/casual)",
                 "categories": _LAYERS},
    "baselayer": {"max_detail_pages": 15, "include": r"baselayer|base layer|underwear", "categories": _LAYERS},
    "glove": {"max_detail_pages": 20, "exclude": r"\bbike\b|cycling|golf|tennis", "categories": _gendered(
        "/mens/mens-gear/gloves-and-mittens/", "/womens/womens-gear/gloves-and-mittens/",
        "/kids/kids-gear/gloves-and-mittens/", all_="/accessories/gloves-and-mittens/")},
    # 袜子页里还有网球 / 徒步 / 跑步袜，只要 Christy 分类或标题里带 SKI / SNOW / HEAT 的
    "sock": {"max_detail_pages": 5, "include": r"\bski\b|snow|heat", "categories": _gendered(
        "/mens/mens-clothing/socks/", "/womens/womens-clothing/socks/", "/kids/kids-clothing/socks/",
        all_="/accessories/socks/")},
    "facewear": {"max_detail_pages": 0, "categories": _gendered(
        "/mens/mens-gear/masks-and-neck-gaiters/", "/womens/womens-gear/masks-and-neck-gaiters/",
        "/kids/kids-gear/masks-and-neck-gaiters/", all_="/accessories/masks-and-neck-gaiters/")},
    # 帽子页里约四成是棒球帽 / 遮阳帽（Christy 分类 TRUCKER/BALL CAPS、SUN/PERFORMANCE HATS），不算滑雪装备
    "hat": {"max_detail_pages": 0, "exclude": r"trucker|ball caps|sun/performance|tennis", "categories": _gendered(
        "/mens/mens-gear/hats-and-beanies/", "/womens/womens-gear/hats-and-beanies/",
        "/kids/kids-gear/hats-and-beanies/", all_="/accessories/hats-and-beanies/")},
    # "Bags, Packs & Travel" 里 A/T BAGS / DAY PACKS / HYDRATION 是背包，其余是雪板包 / 雪鞋包
    "backpack": {"max_detail_pages": 0, "categories": [
        {"path": "/more-activities/backcountry/backpacks/", "type": "touring"},
        {"path": "/ski/bagsand-packs-and-travel/", "include": r"a/t bags|day packs|hydration"},
    ]},
    # "snowboard bags" 是 Christy 的分类名（复数）；标题里的 "Ski + Snowboard Bag" 不受影响
    "bag": {"max_detail_pages": 0, "exclude": r"a/t bags|day packs|hydration|snowboard bags", "categories": [
        {"path": "/ski/bagsand-packs-and-travel/"},
    ]},
    "avalanche": {"max_detail_pages": 0, "categories": [
        {"path": "/more-activities/backcountry/beaconsand-probes-and-shovels/"},
    ]},
    "skin": {"max_detail_pages": 5, "exclude": r"splitboard", "categories": [
        {"path": "/more-activities/backcountry/skins/"},
    ]},
    # 保养页里还有防水喷雾之类的杂项（Christy 分类 MISC ACCESSORIES，归 accessory）；\bwax 不会匹配 "Nikwax"
    "tuning": {"max_detail_pages": 0, "exclude": r"snowboard",
               "include": r"tools|\bwax|tun|edge|\bfile|brush|scraper|iron|vise|sharpen|stone", "categories": [
        {"path": "/ski/tuning-equipment/"},      # 只有几件，所以再加上滑雪 / 单板共用的保养页
        {"path": "/accessories/tuning/"},
    ]},
    # 配件页里混着手套、袜子、背心、雪鞋包、信标等——它们各有自己的分类，这里排除掉，避免一个商品进两个分类
    "accessory": {"max_detail_pages": 0,
                  "exclude": r"gloves|mittens|\bmitts\b|socks|vests|boot bags|beacon|transceiver|\bprobes?\b|"
                             r"shovel|\bskins\b|balaclava|gaiter|beanie|goggles|helmet",
                  "categories": [
                      {"path": "/ski/accessories/"},
                      {"path": "/accessories/warmers-and-electronic-heating/"},
                      {"path": "/more-activities/backcountry/accessories/", "type": "touring"},
                  ]},
    # protection（护具）：Christy 没有雪上护具分类页（只有自行车护具），不配置
}
CHRISTY_CATEGORIES = CHRISTY_CATEGORY_DEFAULTS["ski"]["categories"]   # 兼容旧名字
CHRISTY_PAGE_SIZE = 150   # 网站默认每页 36 个；robots.txt 允许 start/sz，每页大一点能少发请求

# The House：只抓双板。路径最后一段就是 SFCC / Algolia 里的分类 ID（也可以显式写 id = "..."）
THEHOUSE_CATEGORIES: list[dict] = [
    {"path": "/skis"},
    {"path": "/ski-packages"},    # 板 + 固定器套装（带雪鞋的会被过滤掉）
]
ALGOLIA_ATTRS = ["ID", "name", "brand", "url", "price", "priceData", "variants", "in_stock",
                 "spGenderList", "image_groups", "categoriesFlat", "productHighlights"]
# The House 里挂在这些分类下的肯定不是高山双板
THEHOUSE_BAD_CATEGORIES = {"xc-equipment", "xc-skis", "xc-boots", "xc-binders", "xc-poles", "xc-packages",
                           "splitboard-gear", "splitboards", "splitboard-bindings", "splitboard-accessories"}

# ============================================================================ 双板专用过滤（其他分类不用）
# base.NOT_SKI_RE 之外的补充：Positrack = Rossignol 越野板的鳞片底；skate = 越野滑冰板；
# approach ski = 给单板 / 分体板用户爬坡用的短板
EXTRA_NOT_SKI_RE = re.compile(r"positrack|\bskate\b|approach ski|\bsnow ?shoes?\b", re.I)
_BINDING_RE = re.compile(r"\bbindings?\b", re.I)
_SKI_WORD_RE = re.compile(r"\bskis?\b(?!\s*bindings?\b)", re.I)   # "Ski Bindings" 里的 ski 不算


def exclude_reason(title: str) -> str | None:
    """（只用于双板）不是“高山双板（可以带固定器）”就返回排除原因，否则 None。"""
    m = NOT_SKI_RE.search(title)
    if m:
        return "not-ski:" + m.group(0).lower()
    m = EXTRA_NOT_SKI_RE.search(title)
    if m:
        return "not-alpine:" + m.group(0).lower()
    if _BINDING_RE.search(title) and not _SKI_WORD_RE.search(title):
        return "bindings-only"
    return None


# ============================================================================ 小工具
def _clean(s) -> str:
    return " ".join(_html.unescape(str(s or "")).split())


def _fold(s: str) -> str:
    """去重音 + 小写：Völkl → volkl，用来比较品牌前缀。"""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def _txt(node: Node | None) -> str:
    return " ".join(node.text(separator=" ").split()) if node is not None else ""


def _strip_query(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def _json_at(text: str, pos: int):
    try:
        return json.JSONDecoder().raw_decode(text, pos)[0]
    except ValueError:
        return None


def compose_title(name: str, brand: str | None, year: int | None = None) -> str:
    """标题 = 品牌 + 名称（名称里已有品牌就不重复）；网站单独给了年份且标题里没有时，末尾加上年份。"""
    title = _clean(name)
    if brand and not _fold(title).startswith(_fold(brand)):
        title = f"{brand} {title}"
    if year and not re.search(r"\b(?:19|20)\d{2}\b|\b\d{2}/\d{2}\b", title):
        title = f"{title} {year}"
    return title


def condition_from_title(title: str) -> str:
    t = title.lower()
    if re.search(r"\bdemo\b", t):
        return "demo"
    if re.search(r"\bused\b|pre-?owned|\bex-?rental\b|\brental\b", t):
        return "used"
    if re.search(r"\bblem(?:ished)?\b|\bb-?grade\b|scratch (?:and|&) dent", t):
        return "blem"
    return "new"


def merge_genders(gs: set[str]) -> str | None:
    """同一件商品出现在多个性别分类里时合并：男 + 女 → unisex；有儿童分类 → kids。"""
    gs = {g for g in gs if g}
    if not gs:
        return None
    if "kids" in gs:
        return "kids"
    if "unisex" in gs or {"men", "women"} <= gs:
        return "unisex"
    return next(iter(gs))


_TITLE_WOMEN_RE = re.compile(r"\bwom[ae]n'?s?\b|\bladies\b", re.I)
_TITLE_MEN_RE = re.compile(r"\bmen'?s?\b", re.I)


def reconcile_gender(site_gender: str | None, title: str) -> str | None:
    """网站偶尔把女款放进男款分类（反之亦然，如 Christy 的 "Armada ARW 100 Skis Womens" 只挂在男款下）。
    只在“单一性别”互相矛盾时以标题为准；unisex / kids / None 保持不变。"""
    w, m = bool(_TITLE_WOMEN_RE.search(title)), bool(_TITLE_MEN_RE.search(title))
    tg = "women" if w and not m else "men" if m and not w else None
    if tg and site_gender in ("men", "women") and site_gender != tg:
        return tg
    return site_gender


def normalize_categories(raw) -> list[dict]:
    """分类页列表：["/path/", {path=..., gender=...}, ...] 或 {men="/..", women="/..", all="/.."}。"""
    items = raw.items() if isinstance(raw, dict) else [(None, x) for x in (raw or [])]
    out = []
    for label, item in items:
        if isinstance(item, str):
            d = {"path": item}
            if label in ("men", "women", "kids", "unisex"):
                d["gender"] = label
        elif isinstance(item, dict):
            d = dict(item)
        else:
            continue
        if not (d.get("path") or d.get("id")):
            continue
        if not d.get("path"):
            d["path"] = "/" + str(d["id"]).strip("/")
        out.append(d)
    return out


def _is_path_list(v) -> bool:
    return isinstance(v, list) or (isinstance(v, dict) and bool(v) and all(isinstance(x, str) for x in v.values()))


_RESULTS_RE = re.compile(r"(\d[\d,]*)\s+Results\b")
_MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
_GTM_RE = re.compile(r"gtmdata\s*=\s*JSON\.stringify\(\s*")


def result_count(html: str) -> int | None:
    m = _RESULTS_RE.search(html)
    return int(m.group(1).replace(",", "")) if m else None


def _money(node: Node) -> float | None:
    v = node.css_first("[content]")
    if v is not None:
        f = to_float(v.attributes.get("content"))
        if f:
            return f
    m = _MONEY_RE.search(node.text())
    return to_float(m.group(1)) if m else None


def parse_tiles(html: str) -> list[dict]:
    """解析 SFRA 分类页的商品卡片（Christy 的格式；The House 服务端渲染的卡片也兼容）。"""
    out = []
    for node in HTMLParser(html).css("div.product[data-pid]"):
        pid = (node.attributes.get("data-pid") or "").strip()
        if not pid:
            continue
        gtm: dict = {}
        for sc in node.css("script"):
            s = sc.text()
            m = _GTM_RE.search(s)
            if m:
                obj = _json_at(s, m.end())
                gtm = obj if isinstance(obj, dict) else {}
                break
        link = node.css_first(".pdp-link a") or node.css_first("a.link")
        sales: list[float] = []
        lists: list[float] = []
        price = node.css_first(".price")
        if price is not None:
            sales = [x for x in (_money(n) for n in price.css(".sales")) if x]
            lists = [x for x in (_money(n) for n in price.css(".strike-through, .list-price, del")) if x]
        img = node.css_first("img.tile-image") or node.css_first("img")
        season = re.search(r"(\d{2})\s*/\s*(\d{2})", _txt(node.css_first(".season-attr")))
        out.append({
            "pid": pid,
            "name": _clean(link.text() if link is not None else "") or _clean(gtm.get("name")),
            "href": (link.attributes.get("href") or "") if link is not None else "",
            "sales": sales,
            "lists": lists,
            "image": (img.attributes.get("src") or img.attributes.get("data-src")) if img is not None else None,
            "orderable": node.attributes.get("data-isorderable"),
            "season": f"{season.group(1)}/{season.group(2)}" if season else None,
            "year": 2000 + int(season.group(2)) if season else None,
            "gtm": gtm,
        })
    return out


# 双板套装（bundle）页面里固定器那一组按钮是刹车宽度："Select Binding Size: 83"——抓双板时跳过
_SKI_SKIP_SIZE_LABEL = re.compile(r"binding|brake|boot|pole|width|mondo", re.I)


def parse_size_swatches(html: str, category: str = "ski") -> list[tuple[str, bool]]:
    """SFRA 商品详情页的尺码按钮：div.size-attributes > button.size-attribute > span[data-attr-value]。
    当前不可选（缺货）时 button 或 span 带 unselectable / unavailable。返回 [(标签, 是否有货)]。
    双板：跳过套装里固定器 / 雪鞋那一组，优先用标签里带 "Ski" 的；其他分类：优先标签里带 "Size" 的那一组。"""
    blocks = []
    for b in HTMLParser(html).css("div.size-attributes"):
        label = _txt(b.css_first(".non-input-label"))
        if category == "ski" and _SKI_SKIP_SIZE_LABEL.search(label):
            continue
        blocks.append((label, b))
    if not blocks:
        return []
    prefer = r"\bski\b" if category == "ski" else r"\bsize\b"
    block = next((b for label, b in blocks if re.search(prefer, label, re.I)), blocks[0][1])
    out, seen = [], set()
    for span in block.css("[data-attr-value]"):
        label = _clean(span.attributes.get("data-display-value") or span.attributes.get("data-attr-value"))
        if not label or label in seen:
            continue
        seen.add(label)
        btn = span.parent
        cls = f"{span.attributes.get('class') or ''} {(btn.attributes.get('class') or '') if btn is not None else ''}"
        out.append((label, not re.search(r"\b(?:unselectable|unavailable|disabled)\b", cls)))
    return out


def jsonld_product(html: str) -> dict:
    for m in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for x in data if isinstance(data, list) else [data]:
            if isinstance(x, dict) and x.get("@type") == "Product":
                return x
    return {}


def html_to_text(s: str) -> str:
    s = re.sub(r"<br\s*/?>|</p>|</li>|</tr>", "\n", s or "", flags=re.I)
    s = _html.unescape(re.sub(r"<[^>]+>", " ", s))
    return "\n".join(" ".join(line.split()) for line in s.splitlines() if line.strip())


# ============================================================================ 公共基类
class SFCCAdapter(Adapter):
    """SFCC 站点公共部分：按分类解析来源配置、列表页出错处理、详情页补尺码（只补打折商品，有上限）。"""

    platform = "sfcc"
    CATEGORY_DEFAULTS: dict[str, dict] = {}
    SUPPORTED: set[str] | None = None        # None = 任何配置了分类页的分类都支持

    def __init__(self, cfg: dict, fetcher, log=print):
        super().__init__(cfg, fetcher, log)
        self.stats: dict = {"category": self.category, "list_requests": 0, "detail_requests": 0,
                            "detail_with_sizes": 0, "skipped": {}, "skipped_titles": [], "blocked": False,
                            "errors": []}
        self._rejected: dict[str, tuple[str, str]] = {}   # pid → (标题, 排除原因)

    # ---- 本分类的来源配置
    @cached_property
    def source_cfg(self) -> dict:
        """优先级：模块默认值 < cfg 顶层（调度器已合并好的 [<id>.<分类>] 表）< 未合并的 cfg["categories"][<分类>] 表。"""
        out = dict(self.CATEGORY_DEFAULTS.get(self.category, {}))
        for key in ("max_detail_pages", "include", "exclude", "page_size", "max_list_pages"):
            if self.cfg.get(key) is not None:
                out[key] = self.cfg[key]
        raw = self.cfg.get("categories")
        if _is_path_list(raw) and not self._inherited_ski_pages(raw):
            out["categories"] = raw
        elif isinstance(raw, dict) and isinstance(raw.get(self.category), dict):
            out.update(raw[self.category])
        return out

    def _inherited_ski_pages(self, raw) -> bool:
        """非双板分类却拿到了双板的分类页列表 = [<id>.<分类>] 表漏写 categories，继承了零售商级（双板）的值。"""
        if self.category == "ski":
            return False
        ski = {c["path"] for c in normalize_categories(self.CATEGORY_DEFAULTS.get("ski", {}).get("categories"))}
        got = {c["path"] for c in normalize_categories(raw)}
        if ski and got == ski:
            self.log(f"[{self.rid}/{self.category}] config has no 'categories' of its own (inherited the ski pages); "
                     f"using the built-in defaults for '{self.category}'")
            return True
        return False

    @property
    def categories(self) -> list[dict]:
        return normalize_categories(self.source_cfg.get("categories"))

    def fetch(self) -> Iterator[RawProduct]:
        if self.SUPPORTED is not None and self.category not in self.SUPPORTED:
            self.log(f"[{self.rid}] category '{self.category}' is not supported by this adapter; skipping")
            return
        if not self.categories:
            self.log(f"[{self.rid}] no category pages configured for '{self.category}'; skipping")
            return
        products = self._listing()
        for p in products:
            p.category = self.category
        self._finish_rejections()
        self._enrich_details(products)
        self.log(f"[{self.rid}/{self.category}] {len(products)} products | list requests "
                 f"{self.stats['list_requests']}, product pages {self.stats['detail_requests']}")
        yield from products

    # ---- 子类实现
    def _listing(self) -> list[RawProduct]:
        raise NotImplementedError

    def _parse_detail(self, p: RawProduct, html: str) -> None:
        raise NotImplementedError

    # ---- 公共工具
    def _abs(self, href: str) -> str:
        return href if href.startswith(("http://", "https://")) else f"{self.base_url}/{href.lstrip('/')}"

    def _reject(self, pid: str, title: str, reason: str) -> None:
        self._rejected[pid] = (title, reason)

    def _finish_rejections(self) -> None:
        self.stats["skipped"] = dict(Counter(r.split(":")[0] for _, r in self._rejected.values()))
        self.stats["skipped_titles"] = [f"{t}  [{r}]" for t, r in self._rejected.values()]

    def _list_page(self, url: str, have_results: bool) -> str | None:
        """抓一个列表页。被反爬拦截时：还没拿到任何商品就抛出 BlockedError（让上层如实报告），
        否则记下来、返回 None（保留已抓到的结果）。其他错误（404 等）记录后返回 None。"""
        self.stats["list_requests"] += 1
        try:
            return self.fetcher.get_html(url)
        except BlockedError as e:
            self.stats["blocked"] = True
            self.stats["errors"].append(str(e))
            if not have_results:
                raise
            self.log(f"[{self.rid}] BLOCKED: {e} -- keeping what was collected so far")
            return None
        except FetchError as e:
            self.stats["errors"].append(str(e))
            self.log(f"[{self.rid}] list page failed: {e}")
            return None

    def _detail_limit(self) -> int:
        return int(self.source_cfg.get("max_detail_pages", 80))

    def _enrich_details(self, products: list[RawProduct]) -> None:
        """只给“打折且还没有尺码”的商品抓详情页，折扣大的优先，每个分类最多 max_detail_pages 个。"""
        if self.stats["blocked"]:
            return
        limit = self._detail_limit()
        todo = [p for p in products if p.compare_at and p.price and not p.sizes]
        todo.sort(key=lambda p: (p.compare_at - p.price) / p.compare_at, reverse=True)
        if len(todo) > limit:
            self.log(f"[{self.rid}/{self.category}] {len(todo)} discounted items need sizes; "
                     f"fetching the top {limit} by % off")
        for p in todo[:limit]:
            self.stats["detail_requests"] += 1
            try:
                html = self.fetcher.get_html(p.url)
            except BlockedError as e:
                self.stats["blocked"] = True
                self.stats["errors"].append(str(e))
                self.log(f"[{self.rid}] BLOCKED on a product page, stopping detail fetches: {e}")
                break
            except FetchError as e:
                self.stats["errors"].append(str(e))
                self.log(f"[{self.rid}] product page failed: {e}")
                continue
            try:
                self._parse_detail(p, html)
            except Exception as e:  # 单个页面解析失败不影响其他商品
                self.stats["errors"].append(f"parse {p.url}: {e!r}")
                continue
            if p.sizes:
                self.stats["detail_with_sizes"] += 1

    def _sizes_from_swatches(self, p: RawProduct, html: str) -> None:
        sizes = parse_size_swatches(html, self.category)
        if sizes:
            p.sizes = [SizeOption(label, self.size_cm(label), ok, p.price, p.compare_at) for label, ok in sizes]
            p.available = any(s.available for s in p.sizes)


# ============================================================================ Christy Sports
_CHRISTY_TYPES = {"ALL MOUNTAIN": "all-mountain", "FRONTSIDE": "frontside", "BACKSIDE": "freeride",
                  "A/T": "touring", "BACKCOUNTRY": "touring", "RACE": "race", "PARK": "park",
                  "FREESTYLE": "park"}


def christy_class_gender(cls: str | None) -> str | None:
    """Christy 商品分类的前缀：M / MENS = 男款，W / WOMENS = 女款，Y / JR / YOUTH / KIDS = 儿童。"""
    c = (cls or "").strip().upper()
    if re.match(r"(?:JR|JUNIOR|KIDS?|YOUTH|Y|BOYS?|GIRLS?|TODDLER|INFANT|TWEEN)\b", c):
        return "kids"
    if re.match(r"(?:M|MENS?|MEN'S)\b", c):
        return "men"
    if re.match(r"(?:W|WOMENS?|WOMEN'S)\b", c):
        return "women"
    return None


def christy_class_type(cls: str | None) -> str | None:
    return _CHRISTY_TYPES.get(re.sub(r"^(?:M|W|JR)\s+", "", (cls or "").strip().upper()))


class ChristySportsAdapter(SFCCAdapter):
    CATEGORY_DEFAULTS = CHRISTY_CATEGORY_DEFAULTS

    def _listing(self) -> list[RawProduct]:
        src = self.source_cfg
        size = int(src.get("page_size") or CHRISTY_PAGE_SIZE)
        max_pages = int(src.get("max_list_pages") or 20)
        found: dict[str, RawProduct] = {}
        genders: dict[str, set[str]] = {}
        for cat in self.categories:
            if self.stats["blocked"]:
                break
            path, start, total, seen = cat["path"], 0, None, set()
            for _ in range(max_pages):
                url = self._abs(path)
                url += ("&" if "?" in url else "?") + f"start={start}&sz={size}"
                html = self._list_page(url, bool(found))
                if html is None:
                    break
                if total is None:
                    total = result_count(html)
                tiles = parse_tiles(html)
                fresh = [t for t in tiles if t["pid"] not in seen]
                for t in fresh:
                    seen.add(t["pid"])
                    self._add_tile(t, cat, found, genders)
                start += len(tiles)
                if not fresh or (total is not None and start >= total) or (total is None and len(tiles) < size):
                    break
            self.log(f"[{self.rid}/{self.category}] {path}: {len(seen)} tiles"
                     + (f" (site says {total})" if total is not None else ""))
        for pid, p in found.items():
            site = merge_genders(genders.get(pid, set())) or p.extra.get("class_gender")
            p.gender_hint = reconcile_gender(site, p.title)
        return list(found.values())

    def _add_tile(self, t: dict, cat: dict, found: dict, genders: dict) -> None:
        pid = t["pid"]
        if cat.get("gender"):                    # 性别是分类页的事实，不管这一页的过滤结果如何都记下来
            genders.setdefault(pid, set()).add(cat["gender"])
        p = found.get(pid)
        if p is None:
            reason = self._filter_reason(t, cat)
            if reason:
                self._reject(pid, t["name"], reason)
                return
            p = found[pid] = self._tile_to_product(t)
            self._rejected.pop(pid, None)        # 别的分类页排除过、这一页接受了
        p.extra["categories"].append(cat["path"])
        if cat.get("type") and cat["type"] not in p.type_hints:
            p.type_hints.append(cat["type"])

    def _filter_reason(self, t: dict, cat: dict) -> str | None:
        """返回排除原因（None = 保留）。"""
        gtm, name = t["gtm"], t["name"]
        cls = _clean(gtm.get("classificationCategoryDisplayName"))
        eps = _clean(gtm.get("categoryEpsilon"))
        text = " | ".join(x for x in (cls, eps, _clean(gtm.get("departmentEpsilon")), name) if x)
        src = self.source_cfg
        for rx in (src.get("include"), cat.get("include")):
            if rx and not re.search(rx, text, re.I):
                return "include"
        for rx in (src.get("exclude"), cat.get("exclude")):
            m = re.search(rx, text, re.I) if rx else None
            if m:
                return "exclude:" + m.group(0).lower()
        if self.category == "ski":               # 双板：保持原来的严格规则
            reason = exclude_reason(name)
            if not reason and eps and eps.upper() not in ("SKIS", "SKI"):
                reason = f"category:{eps.lower()}"            # BOOTS / BINDINGS / NORDIC ...
            if not reason and cls and re.search(r"skate|nordic|\bxc\b|classic|cross", cls, re.I):
                reason = f"category:{cls.lower()}"
            return reason
        if not self.wanted(name, cls or None):
            from ..categories import classify
            return f"other-category:{classify(name, cls or None)}"
        return None

    def _tile_to_product(self, t: dict) -> RawProduct:
        gtm = t["gtm"]
        cls = _clean(gtm.get("classificationCategoryDisplayName")) or None
        brand = _clean(gtm.get("brand")) or None
        price = min(t["sales"]) if t["sales"] else to_float(gtm.get("price"))
        lst = min(t["lists"]) if t["lists"] else None
        compare_at = lst if (lst and price and lst > price + 0.005) else None
        title = compose_title(t["name"], brand, t["year"])
        typ = christy_class_type(cls) if self.category == "ski" else None
        return RawProduct(
            retailer_id=self.rid,
            external_id=t["pid"],
            url=_strip_query(self._abs(t["href"] or f"/{t['pid']}.html")),
            title=title,
            price=price,
            compare_at=compare_at,
            currency="USD",
            vendor=brand,
            product_type=cls,
            tags=[x for x in (cls, t["season"] and f"season {t['season']}") if x],
            image=t["image"],
            available=(t["orderable"] == "true") if t["orderable"] else None,
            type_hints=[typ] if typ else [],
            condition_hint=condition_from_title(title),
            category=self.category,
            extra={"season": t["season"], "classification": cls, "categories": [],
                   "class_gender": christy_class_gender(cls)},
        )

    def _parse_detail(self, p: RawProduct, html: str) -> None:
        self._sizes_from_swatches(p, html)
        tree = HTMLParser(html)
        parts = [_txt(n) for n in tree.css('div.content[id^="collapsible-description"]')]
        spec = tree.css_first("#collapsible-specs")
        if spec is not None:
            for para in spec.css("p"):
                line = _txt(para)
                if not line:
                    continue
                parts.append(line)
                key, _, val = line.partition(":")
                if self.category == "ski" and key.strip().lower() == "ski type":
                    for x in val.split(","):
                        x = x.strip().lower()
                        if x and x not in p.type_hints:
                            p.type_hints.append(x)
        body = "\n".join(x for x in parts if x)
        if body:
            p.body_text = body[:6000]


# ============================================================================ The House（只支持双板）
_HOUSE_GENDERS = {"men's": "men", "mens": "men", "men": "men", "women's": "women", "womens": "women",
                  "women": "women", "unisex": "unisex", "youth": "kids", "junior": "kids", "kids": "kids",
                  "kid's": "kids", "child": "kids", "children": "kids", "infant": "kids", "toddler": "kids",
                  "boys": "kids", "girls": "kids"}

_TITLE_SIZE_RES = (
    re.compile(r"(?<![\d.\-])(\d{2,3})\s?cm\b", re.I),        # "... - 164cm" / ", 172 cm"
    re.compile(r"\bskis?\s+(\d{3})\s*$", re.I),                 # "Armada Reliance 88 C Skis 176"
    re.compile(r"(?<![\d.\-])(\d{3})\s+skis?\b", re.I),         # "Stance 80 Men's 161 Ski w/ ..."
)


def title_size(title: str) -> str | None:
    """单一尺码商品把长度写在标题里；只在 Algolia 没给尺码时使用。"""
    for rx in _TITLE_SIZE_RES:
        m = rx.search(title)
        if m and 70 <= int(m.group(1)) <= 215:
            return f"{m.group(1)} cm"
    return None


def algolia_config(html: str) -> dict | None:
    """从分类页里读出浏览器端用的 Algolia 配置（应用 ID、只读搜索 key、商品索引名）。"""
    m = re.search(r"\balgoliaData\s*=\s*", html)
    data = _json_at(html, m.end()) if m else None
    if not isinstance(data, dict) or data.get("enable") is False:
        return None
    app, key, idx = data.get("applicationID"), data.get("searchApiKey"), data.get("productsIndex")
    if not (app and key and idx):
        return None
    return {"app": str(app), "key": str(key), "index": str(idx)}


def _algolia_image(groups) -> str | None:
    groups = [g for g in groups or [] if isinstance(g, dict)]
    for g in sorted(groups, key=lambda g: g.get("view_type") != "large"):
        for im in g.get("images") or []:
            if isinstance(im, dict) and im.get("dis_base_link"):
                return im["dis_base_link"]
    return None


class TheHouseAdapter(SFCCAdapter):
    CATEGORY_DEFAULTS = {"ski": {"max_detail_pages": 80, "categories": THEHOUSE_CATEGORIES}}
    SUPPORTED = {"ski"}

    def _listing(self) -> list[RawProduct]:
        cats = self.categories
        html = self._list_page(self._abs(cats[0]["path"]), False)   # 被拦截会直接抛 BlockedError
        if html is None:
            return []
        ids = [str(c.get("id") or c["path"].strip("/").split("/")[-1]) for c in cats]
        conf = algolia_config(html) if self.cfg.get("listing_source", "algolia") == "algolia" else None
        if conf:
            try:
                hits = self._algolia_hits(conf, ids)
            except FetchError as e:
                self.stats["errors"].append(str(e))
                self.log(f"[{self.rid}] Algolia query failed ({e}); falling back to server-rendered tiles")
            else:
                out: dict[str, RawProduct] = {}
                for h in hits:
                    p = self._hit_to_product(h)
                    if p is not None:
                        out.setdefault(p.external_id, p)
                # 少数记录没有 brand 字段：标题以本次见过的品牌开头时补上
                brands = sorted({p.vendor for p in out.values() if p.vendor}, key=len, reverse=True)
                for p in out.values():
                    if not p.vendor:
                        p.vendor = next((b for b in brands if _fold(p.title).startswith(_fold(b) + " ")), None)
                self.log(f"[{self.rid}] Algolia categories {ids}: {len(hits)} records -> {len(out)} skis")
                return list(out.values())
        else:
            self.log(f"[{self.rid}] no algoliaData on {cats[0]['path']}; using server-rendered tiles")
        return self._ssr_listing(cats, html)

    # ---- Algolia（和浏览器打开分类页时发出的查询一样：只读 key + 分类过滤）
    def _algolia_hits(self, conf: dict, ids: list[str]) -> list[dict]:
        url = f"https://{conf['app'].lower()}-dsn.algolia.net/1/indexes/{quote(conf['index'], safe='')}"
        headers = {"X-Algolia-Application-Id": conf["app"], "X-Algolia-API-Key": conf["key"]}
        flt = " OR ".join(f'categoriesFlat:"{c}"' for c in ids)
        hits: list[dict] = []
        page, pages = 0, 1
        while page < pages and page < 20:
            params = {"query": "", "filters": flt, "hitsPerPage": 1000, "page": page,
                      "attributesToRetrieve": json.dumps(ALGOLIA_ATTRS, separators=(",", ":")),
                      "attributesToHighlight": "[]", "attributesToSnippet": "[]",
                      "analytics": "false", "clickAnalytics": "false"}
            self.stats["list_requests"] += 1
            data = self.fetcher.get_json(url, params=params, headers=headers)
            hits.extend(h for h in data.get("hits") or [] if isinstance(h, dict))
            pages = int(data.get("nbPages") or 1)
            page += 1
        return hits

    def _hit_to_product(self, h: dict) -> RawProduct | None:
        pid = _clean(h.get("ID") or h.get("objectID"))
        name = _clean(h.get("name"))
        if not pid or not name:
            return None
        cats = [c for c in h.get("categoriesFlat") or [] if isinstance(c, str)]
        reason = exclude_reason(name)
        bad = THEHOUSE_BAD_CATEGORIES.intersection(cats)
        if not reason and bad:
            reason = "category:" + sorted(bad)[0]
        if reason:
            self._reject(pid, name, reason)
            return None
        pdata = h.get("priceData") or {}
        gtm = pdata.get("gtmData") or {}
        brand = _clean(h.get("brand") or gtm.get("brand")) or None
        list_price = to_float(pdata.get("listPrice"))
        sizes = self._variant_sizes(h.get("variants") or [], list_price)
        stock_prices = [s.price for s in sizes if s.available and s.price]
        price = min(stock_prices) if stock_prices else to_float((h.get("price") or {}).get("USD"))
        compare_at = list_price if (list_price and price and list_price > price + 0.005) else None
        if not sizes:
            label = title_size(name)
            if label:   # 单一尺码商品；key 只返回有货商品，所以这个尺码有货
                sizes = [SizeOption(label, self.size_cm(label), True, price, compare_at)]
        genders = h.get("spGenderList") or [v.get("spGender") for v in h.get("variants") or [] if isinstance(v, dict)]
        title = compose_title(name, brand)
        return RawProduct(
            retailer_id=self.rid,
            external_id=pid,
            url=_strip_query(self._abs(h.get("url") or f"/{pid}.html")),
            title=title,
            price=price,
            compare_at=compare_at,
            currency="USD",
            vendor=brand,
            product_type="ski package" if "ski-packages" in cats else "skis",
            tags=[c for c in cats if c in ("skis", "ski-packages")],
            image=_algolia_image(h.get("image_groups")),
            sizes=sizes,
            available=bool(h.get("in_stock")) if "in_stock" in h else None,
            gender_hint=reconcile_gender(
                merge_genders({_HOUSE_GENDERS.get(str(g).strip().lower()) for g in genders if g}), title),
            body_text=_clean(h.get("productHighlights")) or None,
            condition_hint=condition_from_title(title),
            category=self.category,
            extra={"source": "algolia", "categories": cats, "percent_off": gtm.get("percentOff"),
                   "gender_raw": list(dict.fromkeys(g for g in genders if g))},
        )

    def _variant_sizes(self, variants: list, list_price: float | None) -> list[SizeOption]:
        by_label: dict[str, SizeOption] = {}
        for v in variants:
            if not isinstance(v, dict):
                continue
            label = _clean(v.get("size"))
            if not label or label.lower() in ("none", "null"):
                continue
            vp = to_float((v.get("price") or {}).get("USD"))
            ok = bool(v.get("in_stock")) and v.get("online", True) is not False
            cmp = list_price if (list_price and vp and list_price > vp + 0.005) else None
            s = by_label.get(label)
            if s is None:
                by_label[label] = SizeOption(label, self.size_cm(label), ok, vp, cmp)
            else:                        # 同一长度多个颜色：任一有货即有货，价格取低
                s.available = s.available or ok
                if vp and (s.price is None or vp < s.price):
                    s.price, s.compare_at = vp, cmp
        return sorted(by_label.values(), key=lambda s: (s.cm or 999, s.label))

    # ---- 退路：分类页里服务端渲染的卡片（带 ?page=N 链接；robots 允许 page=）
    def _ssr_listing(self, cats: list[dict], first_html: str) -> list[RawProduct]:
        max_pages = int(self.cfg.get("max_list_pages", 20))
        found: dict[str, RawProduct] = {}
        for i, cat in enumerate(cats):
            url = self._abs(cat["path"])
            html = first_html if i == 0 else self._list_page(url, bool(found))
            if html is None:
                continue
            pages = sorted({int(n) for n in re.findall(r"[?&]page=(\d+)", html)} - {1})
            htmls = [html] + [x for x in (self._list_page(f"{url}?page={n}", True)
                                          for n in pages[:max_pages]) if x]
            for page_html in htmls:
                for t in parse_tiles(page_html):
                    if t["pid"] in found:
                        continue
                    # 退路页面可能是大分类（含服装等），所以这里额外要求标题里有 ski / skis
                    reason = exclude_reason(t["name"]) or (None if _SKI_WORD_RE.search(t["name"]) else "no-ski-word")
                    if reason:
                        self._reject(t["pid"], t["name"], reason)
                        continue
                    price = min(t["sales"]) if t["sales"] else None
                    lst = min(t["lists"]) if t["lists"] else None
                    title = compose_title(t["name"], None)
                    found[t["pid"]] = RawProduct(
                        retailer_id=self.rid, external_id=t["pid"],
                        url=_strip_query(self._abs(t["href"] or f"/{t['pid']}.html")), title=title,
                        price=price, compare_at=lst if (lst and price and lst > price + 0.005) else None,
                        image=t["image"], condition_hint=condition_from_title(title), category=self.category,
                        extra={"source": "html", "categories": [cat["path"]]})
        self.log(f"[{self.rid}] server-rendered tiles: {len(found)} skis")
        return list(found.values())

    def _parse_detail(self, p: RawProduct, html: str) -> None:
        self._sizes_from_swatches(p, html)
        desc = html_to_text(jsonld_product(html).get("description") or "")
        if len(desc) > len(p.body_text or ""):
            p.body_text = desc[:6000]


__all__ = ["SFCCAdapter", "ChristySportsAdapter", "TheHouseAdapter", "CHRISTY_CATEGORY_DEFAULTS",
           "CHRISTY_CATEGORIES", "THEHOUSE_CATEGORIES", "parse_tiles", "parse_size_swatches", "exclude_reason"]
