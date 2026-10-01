"""Shopify 店铺分类自动发现：列出店铺全部 collection，按分类挑出合适的，生成 retailers.toml 配置片段。

用法：python -m skideals discover https://www.some-shop.com
（也用来给已收录的 Shopify 店铺生成全品类配置。结果是“建议”，写进配置前最好看一眼。）
"""
from __future__ import annotations

import re

from .categories import CATEGORIES
from .net import Fetcher
from .normalize import BRANDS, fold

# 分类 → collection handle 的匹配规则（handle 是小写、用 - 连接的英文）
PATTERNS: dict[str, str] = {
    "binding": r"(?:^|-)(?:ski|alpine|downhill|touring|at|alpine-touring|backcountry)-bindings?(?:$|-)|^bindings?$",
    "boot": r"(?:^|-)(?:ski|alpine|downhill|touring|at|alpine-touring|backcountry)-boots?(?:$|-)",
    "pole": r"(?:^|-)(?:ski|alpine|touring|adjustable)-poles?(?:$|-)|^poles$",
    "helmet": r"(?:^|-)(?:ski|snow|snowsports?|ski-snowboard|ski-and-snowboard|winter)-helmets?(?:$|-)|^helmets?$",
    "goggle": r"(?:^|-)(?:ski|snow|snowsports?|ski-snowboard|ski-and-snowboard|winter)-goggles?(?:$|-)|^goggles?$",
    "protection": r"(?:^|-)(?:ski-)?body-armou?r|ski-wrist-guards?|(?:^|-)(?:impact|padded|crash)-(?:shorts|pants)|"
                  r"back-protectors?|spine-protectors?|ski-protect",
    "jacket": r"(?:^|-)(?:ski|snow|snowsports?|ski-snowboard|ski-and-snowboard)-jackets?(?:$|-)|^(?:mens|womens|kids|boys|girls|youth)-jackets$",
    "pants": r"(?:^|-)(?:ski|snow|snowsports?|ski-snowboard|ski-and-snowboard)-(?:pants|bibs|pants-bibs|pants-and-bibs|bibs-pants)(?:$|-)|^(?:mens|womens|kids|boys|girls|youth)-(?:snow-)?(?:pants|bibs)$",
    "suit": r"one-piece-(?:ski-)?snowsuits?|(?:^|-)snow-?suits?(?:$|-)|(?:^|-)ski-suits?(?:$|-)|(?:^|-)onesies?(?:$|-)",
    "midlayer": r"(?:^|-)(?:ski-)?mid-?layers?(?:$|-)|(?:^|-)fleece-jackets(?:$|-)",
    "baselayer": r"base-?layers?|(?:^|-)thermals?(?:$|-)|long-underwear",
    "glove": r"(?:^|-)(?:ski|snow|snowsports?|ski-snowboard|ski-and-snowboard|winter)-(?:gloves|mittens|gloves-mittens|gloves-and-mittens|mitts)(?:$|-)|^(?:gloves|mittens|gloves-mittens|gloves-and-mittens)$|^(?:mens|womens|kids|boys|girls|youth)-(?:ski-)?(?:gloves|mittens|gloves-mittens)",
    "sock": r"(?:^|-)(?:ski|snow|snowsports?|ski-snowboard|ski-and-snowboard)-socks?(?:$|-)",
    "facewear": r"balaclavas?|neck-?gaiters?|(?:ski-)?face-?masks?|neck-?warmers?|neckwear|face-?wear",
    "hat": r"(?:^|-)beanies?(?:$|-)|winter-hats?|ski-hats?",
    "backpack": r"(?:^|-)(?:ski|backcountry-ski|snow)-(?:backpacks?|packs?)(?:$|-)|avalanche-airbags?(?:-packs?)?|airbag-(?:back)?packs?",
    "bag": r"(?:^|-)(?:ski|boot|ski-boot|ski-and-boot)-bags?(?:$|-)|ski-cases?|ski-travel-bags?",
    "avalanche": r"avalanche-(?:shovels?|probes?|beacons?|transceivers?|rescue|safety|kits?|gear)|(?:^|-)beacons?$|"
                 r"transceivers?|(?:^|-)(?:snow-)?shovels?$|(?:^|-)probes?$|(?:^|-)avy-",
    "skin": r"climbing-skins?|(?:^|-)ski-skins?(?:$|-)|^skins$|touring-skins",
    "tuning": r"tuning|(?:^|-)(?:ski-)?wax(?:es)?(?:$|-)|ski-tools?|tune-?kits?",
    "accessory": r"ski-straps?|boot-dryers?|(?:ski-)?boot-heaters?|hand-warmers?|goggle-lenses|replacement-lenses?",
}
# 这些分类要把所有匹配的中性 collection 都抓（它们互相不重叠：信标/探杆/雪铲分开放、蜡和工具分开放…）
UNION = {"avalanche", "tuning", "accessory", "protection", "facewear", "backpack", "bag"}
# 这些分类优先选名字里带 ski/snow 的 collection（通用的 helmets / jackets 可能混着骑行头盔、日常外套）
PREFER_SKI = {"helmet", "goggle", "jacket", "pants", "midlayer", "hat", "backpack", "sock", "facewear"}
# 这些 handle 通常是某个大分类的“子集”（促销、新品、品牌专区、年份…），不选
_EXCLUDE = re.compile(r"-[0-9][a-z0-9]{6}$|-for-(?:men|women|kids)|package|dress|swim|wet|surf|skate|paddle|kayak|wake|"
                      r"cross-country|nordic|(?:^|-)xc(?:$|-)|classic|roller|"
                      r"sale|clearance|outlet|closeout|deals?|new|arrival|gift|best|top-|staff|featured|pick|bundle|"
                      r"snowboard(?!-and-ski|-ski)|bike|mtb|cycling|climb(?!ing-skins?)|hik|trek|run|golf|swim|rain|casual|lifestyle|"
                      r"20\d\d|\bused\b|demo|rental|services?|lessons?|course|education|womens-skirts|sun-|trucker|"
                      r"-copy|test|draft|hidden|private|wholesale|preorder|pre-order|vip|member|promo|coupon|black-friday|"
                      r"cyber|labor|memorial|christmas|holiday|spring|summer|fall|winter-sale|kids-ski-and-snowboard-pants|"
                      r"know-before|intro-to|checklist|class|clinic|camp")
_GENDER = [("women", r"(?:^|-)(?:womens|women|ladies|w)-"), ("kids", r"(?:^|-)(?:kids|kid|youth|junior|jr|boys|girls|toddler)s?-"),
           ("men", r"(?:^|-)(?:mens|men|m)-")]
_BRAND_WORDS = sorted({re.sub(r"[^a-z0-9]+", "-", fold(a)).strip("-") for _, aliases, amb in BRANDS for a in aliases
                       if a.isascii() and len(a) >= 3 and not amb}, key=len, reverse=True)


def all_collections(fetcher: Fetcher, base: str) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for page in range(1, 60):
        cols = fetcher.get_json(f"{base.rstrip('/')}/collections.json", params={"limit": 250, "page": page}).get(
            "collections", [])
        if not cols:
            break
        out += [(c["handle"], int(c.get("products_count") or 0)) for c in cols]
    return out


def _gender(handle: str) -> str | None:
    for g, rx in _GENDER:
        if re.search(rx, handle):
            return g
    return None


def _has_brand(handle: str) -> bool:
    return any(re.search(rf"(?:^|-){re.escape(b)}(?:$|-)", handle) for b in _BRAND_WORDS)


def suggest(collections: list[tuple[str, int]], categories: list[str] | None = None) -> dict[str, dict]:
    """每个分类：main = 抓取的 collection；hints = 性别标注。"""
    out: dict[str, dict] = {}
    for cat in categories or [c.id for c in CATEGORIES if c.id in PATTERNS]:
        rx = re.compile(PATTERNS[cat])
        cands = [(h, n) for h, n in collections if n > 0 and rx.search(h) and not _EXCLUDE.search(h) and not _has_brand(h)]
        if cat != "backpack":
            cands = [(h, n) for h, n in cands if not re.search(r"avalanche-(?:airbag-)?packs?", h)]
        if not cands:
            continue
        by_g: dict[str | None, list] = {}
        for h, n in cands:
            by_g.setdefault(_gender(h), []).append((h, n))
        main, hints = [], {}
        neutral = sorted(by_g.get(None, []), key=lambda x: -x[1])
        if cat in PREFER_SKI:  # 有带 ski/snow 的就优先用
            skiish = [x for x in neutral if re.search(r"ski|snow", x[0])]
            neutral = skiish + [x for x in neutral if x not in skiish]
        from .categories import BY_ID
        gendered_cat = BY_ID[cat].gendered
        gendered = {}
        for g, v in by_g.items():
            if not g or (not gendered_cat and g != "kids"):  # 雪镜/头盔/固定器等不分男女：只保留儿童标注
                continue
            v = sorted(v, key=lambda x: -x[1])
            skiish = [x for x in v if re.search(r"ski|snow", x[0])] if cat in PREFER_SKI else []
            gendered[g] = (skiish or v)[0]
        if neutral:
            main.append(neutral[0][0])
            if cat in UNION:
                main += [h for h, n in neutral[1:6]]
            elif cat in ("binding", "boot", "pole", "skin"):  # 很多店把高山款和登山款分开放：登山/野外分类也要
                main += [h for h, n in neutral[1:] if re.search(r"tour|backcountry|(?:^|-)at-|randonee|skimo", h)][:3]
            elif neutral[0][1] < 200:  # 小店：规模不小的其他中性分类也加进来（最多 2 个）
                main += [h for h, n in neutral[1:3] if n >= max(10, neutral[0][1] * 0.2)]
        for g, (h, n) in gendered.items():
            hints[h] = {"gender": g}
            if not neutral or (gendered_cat and n > neutral[0][1]):  # 性别分类比“全部”还大，说明“全部”不全，也要抓
                main.append(h)
        out[cat] = {"collections": list(dict.fromkeys(main)), "hints": hints,
                    "_counts": {h: n for h, n in collections if h in set(main) | set(hints)}}
    return out


def to_toml(sugg: dict[str, dict]) -> str:
    lines = []
    for cat, s in sugg.items():
        lines.append(f"[retailers.categories.{cat}]")
        lines.append("collections = [" + ", ".join(f'"{h}"' for h in s["collections"]) + "]")
        if s["hints"]:
            lines.append(f"[retailers.categories.{cat}.hints]")
            for h, v in s["hints"].items():
                lines.append(f'"{h}" = {{ gender = "{v["gender"]}" }}')
        lines.append("")
    return "\n".join(lines)


def discover(url: str) -> str:
    f = Fetcher(min_interval=0.8)
    cols = all_collections(f, url)
    sugg = suggest(cols)
    head = f"# {url}：共 {len(cols)} 个 collection，识别出 {len(sugg)} 个分类\n"
    detail = "\n".join(f"#   {cat:10} " + ", ".join(f"{h}({n})" for h, n in s["_counts"].items()) for cat, s in sugg.items())
    return head + detail + "\n\n" + to_toml(sugg)
