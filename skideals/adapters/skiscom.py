"""Skis.com —— 和 Buckman's 同一个 ASP.NET "OSCAR" 平台，抓取逻辑完全复用 buckmans.OscarAdapter。支持全部滑雪装备分类。

分类：Skis.com 的父分类（如 /products/2340/equipment-skis、/products/2401/goggles）是没有商品卡片的导航页，
必须抓叶子分类；叶子分类本身按男 / 女 / 儿童划分 → 直接得到 gender（“Unisex” 叶子分类 → unisex）。
Demo Skis（二手试板）分类 → condition_hint = "demo"。没有：雪崩装备、止滑带（站点没有对应分类）。
卡片模板与 Buckman's 略有不同（标题在 h3 > a[id$=lnkName]，价格前有 "Starting at:"，翻页用 lnkNext），通用解析器都已兼容。

robots.txt 与 Buckman's 相同（crawl-delay: 2，禁止 /store/items.aspx?q=* 站内搜索、结账/账户页等），分类页/商品页允许。
"""
from __future__ import annotations

from .buckmans import OscarAdapter


def _p(cid: int, slug: str, gender: str | None = None, **kw) -> dict:
    d = {"path": f"/products/{cid}/{slug}", **kw}
    if gender:
        d["gender"] = gender
    return d


class SkisComAdapter(OscarAdapter):
    DEFAULT_CATEGORIES = {
        "ski": [
            _p(2707, "equipment-mens-all-mountain-system-skis", "men"),
            _p(2712, "equipment-mens-all-mountain-freeride-skis", "men"),
            _p(2726, "equipment-womens-all-mountain-system-skis", "women"),
            _p(2713, "equipment-womens-all-mountain-freeride-skis", "women"),
            _p(2718, "equipment-kids-skis", "kids"),
            _p(2730, "equipment-park-and-pipe-skis", type="park"),
            _p(2801, "equipment-race-skis", type="race"),
            _p(2859, "equipment-demo-skis", condition="demo"),
        ],
        "boot": [
            _p(2514, "equipment-mens-boots", "men"),
            _p(2601, "equipment-womens-boots", "women"),
            _p(2678, "equipment-kids-boots", "kids"),
            _p(2802, "equipment-race-ski-boots", type="race"),
        ],
        "binding": [
            _p(2677, "equipment-unisex-bindings"),
            _p(2719, "equipment-youth-bindings", "kids"),
            _p(2803, "equipment-race-bindings", type="race"),
        ],
        "pole": [
            _p(2708, "equipment-ski-poles"),
            _p(2804, "equipment-race-poles", type="race"),
        ],
        "helmet": [
            _p(2457, "helmets-unisex", "unisex"),
            _p(2586, "helmets-womens", "women"),
            _p(2551, "helmets-youth", "kids"),
        ],
        "goggle": [
            _p(2434, "goggles-unisex", "unisex"),
            _p(2521, "goggles-womens", "women"),
            _p(2435, "goggles-youth", "kids"),
        ],
        "protection": [_p(2805, "equipment-protective-race-gear", type="race")],
        "jacket": [
            _p(2502, "men-ski-snowboard-outerwear-insulated-jackets", "men"),
            _p(2575, "men-ski-snowboard-outerwear-shell-jackets", "men"),
            _p(2554, "men-ski-snowboard-outerwear-softshell-jackets", "men"),
            _p(2529, "men-ski-snowboard-outerwear-down-synthetic-down-jackets", "men"),
            _p(2664, "men-ski-snowboard-outerwear-heated-jackets", "men"),
            _p(2480, "women-ski-snowboard-outerwear-insulated-jackets", "women"),
            _p(2648, "women-ski-snowboard-outerwear-shell-jackets", "women"),
            _p(2484, "women-ski-snowboard-outerwear-softshell-jackets", "women"),
            _p(2492, "women-ski-snowboard-outerwear-down-synthetic-down-jackets", "women"),
            _p(2666, "women-ski-snowboard-outerwear-heated-jackets", "women"),
            _p(2812, "women-ski-snowboard-outerwear-petite-and-plus-jackets", "women"),
            _p(2442, "kids-ski-snowboard-outerwear-junior-jackets", "kids"),
            _p(2599, "kids-ski-snowboard-outerwear-preschool-jackets", "kids"),
        ],
        "pants": [
            _p(2383, "men-ski-snowboard-outerwear-pants-bibs", "men"),
            _p(2343, "women-ski-snowboard-outerwear-pants-bibs", "women"),
            _p(2422, "kids-ski-snowboard-outerwear-pants-bibs", "kids"),
        ],
        "suit": [
            _p(2701, "women-ski-snowboard-outerwear-one-piece-suits", "women"),
            _p(2689, "kids-ski-snowboard-outerwear-one-piece-snowsuits", "kids"),
        ],
        "midlayer": [
            _p(2486, "men-mid-layer-full-zips", "men"),
            _p(2527, "men-mid-layer-partial-zips", "men"),
            _p(2636, "men-mid-layer-t-necks", "men"),
            _p(2639, "men-mid-layer-crews", "men"),
            _p(2487, "men-ski-snowboard-outerwear-fleece-jackets", "men"),
            _p(2611, "women-mid-layer-full-zips", "women"),
            _p(2570, "women-mid-layer-partial-zips", "women"),
            _p(2621, "women-mid-layer-t-necks", "women"),
            _p(2644, "women-mid-layer-crews", "women"),
            _p(2496, "women-ski-snowboard-outerwear-fleece-jackets", "women"),
            _p(2462, "kids-base-mid-casual-layers-fleece", "kids"),
        ],
        "baselayer": [
            _p(2414, "men-base-mid-casual-layers-baselayer", "men"),
            _p(2588, "women-base-mid-casual-layers-baselayer", "women"),
            _p(2535, "kids-base-mid-casual-layers-baselayer", "kids"),
        ],
        "glove": [
            _p(2372, "men-accessories-gloves-mittens", "men"),
            _p(2374, "women-accessories-gloves-mittens", "women"),
            _p(2556, "kids-accessories-gloves-mittens", "kids"),
        ],
        "sock": [
            _p(2313, "men-accessories-socks", "men"),
            _p(2316, "women-accessories-socks", "women"),
            _p(2594, "kids-accessories-socks", "kids"),
        ],
        "facewear": [
            _p(2330, "men-accessories-face-masks-neck-ups", "men"),
            _p(2332, "women-accessories-face-masks-neck-ups", "women"),
            _p(2466, "kids-accessories-face-masks-neck-ups", "kids"),
        ],
        "hat": [
            _p(2454, "men-accessories-hats", "men"),
            _p(2429, "women-accessories-hats", "women"),
            _p(2388, "kids-accessories-hats", "kids"),
        ],
        "backpack": [_p(2460, "bags-backpacks")],
        "bag": [_p(2369, "bags-ski"), _p(2392, "bags-boot")],
        "tuning": [_p(2366, "accessories-tuning-tools-kits"), _p(2407, "accessories-wax")],
        "accessory": [
            _p(2341, "equipment-ski-accessories"),
            _p(2327, "equipment-ski-boot-accessories"),
            _p(2362, "equipment-ski-boot-heaters-and-dryers"),
            _p(2432, "accessories-locks"),
        ],
    }
