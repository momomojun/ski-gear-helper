"""Dynafit 适配器：Nuxt devalue 解码、Algolia hits（颜色款合并、只用公开价、有货）、性别、总条数提示。不联网。"""
import json

from skideals.adapters.dynafit import DynafitAdapter, devalue, parse_listing, public_price


def encode(obj) -> list:
    """最简单的 devalue 编码（测试用）：每个值一个槽位，对象 / 数组里存下标；外面包一层 Reactive。"""
    arr: list = []

    def put(v):
        i = len(arr)
        arr.append(None)
        if isinstance(v, dict):
            arr[i] = {k: put(x) for k, x in v.items()}
        elif isinstance(v, list):
            arr[i] = [put(x) for x in v]
        else:
            arr[i] = v
        return i

    arr.append(["Reactive", 1])
    put(obj)
    return arr


GROUP_PRICES = [{"regular": "$599.95", "discounted": "$299.97", "customerGroupID": "pro", "isDiscounted": True},
                {"regular": "$599.95", "discounted": "", "customerGroupID": None, "isDiscounted": False}]


def hit(key, name, color, price="$599.95", sale="", out=False, sku=None):
    return {"key": key, "name": name, "color": color, "size": "UNI", "isOutOfStock": out, "sku": sku or f"{key}_{color}",
            "url": f"/en-us/{name.lower().replace(' ', '-')}-{key}", "subtitle": "Light and fast.",
            "images": [{"url": "https://oberalp.imgix.net/a.mp4?type=video"}, {"url": f"https://oberalp.imgix.net/{color}.png"}],
            "prices": [{"regular": price, "discounted": sale, "customerGroupID": None, "isDiscounted": bool(sale)}]}


def page(hits, nb=None) -> str:
    state = {"data": {}, "state": {"$sinstantsearch_ssr_results_": {"dynafit_prod_us_combined": {
        "results": [{"hits": hits, "nbHits": nb if nb is not None else len(hits), "page": 0}]}}}}
    return f'<script type="application/json" id="__NUXT_DATA__">{json.dumps(encode(state))}</script>'


RIDGE = {**hit("08-0000049571", "Ridge Binding", "black"), "prices": GROUP_PRICES,
         "variants": [hit("08-0000049571", "Ridge Binding", "green", out=True)]}
SKIN = {**hit("08-0000048888", "Blacklight 88 Skin", "red", price="$219.95", sale="$109.97")}
SKIN["variants"] = [dict(SKIN), hit("08-0000048888", "Blacklight 88 Skin", "blue", price="$219.95")]


def test_devalue_handles_wrappers_and_shared_refs():
    arr = [["ShallowReactive", 1], {"a": 2, "b": 2, "c": -1}, [4, 5], 0, "x", ["Set", 4]]
    assert devalue(arr) == {"a": ["x", ["x"]], "b": ["x", ["x"]], "c": None}


def test_public_price_ignores_customer_group_discounts():
    assert public_price(GROUP_PRICES) == (599.95, None)
    assert public_price(SKIN["prices"]) == (109.97, 219.95)


def test_listing_merges_colours():
    items, total = parse_listing(page([RIDGE, SKIN], nb=23))
    assert total == 23
    ridge, skin = items
    assert [(v["color"], v["available"], v["price"]) for v in ridge["variants"]] == [
        ("black", True, 599.95), ("green", False, 599.95)]
    assert ridge["variants"][0]["image"] == "https://oberalp.imgix.net/black.png"   # 跳过视频
    assert [(v["color"], v["price"], v["compare_at"]) for v in skin["variants"]] == [
        ("red", 109.97, 219.95), ("blue", 219.95, None)]


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages

    def get_html(self, url, **kw):
        return self.pages[url]


def test_fetch_gender_and_dedupe():
    base = "https://www.dynafit.com"
    boot_m = hit("08-0000061931", "Ridge Ski Touring Boot Men", "black", price="$899.95")
    f = FakeFetcher({f"{base}/en-us/equipment/skins": page([SKIN]),
                     f"{base}/en-us/men/footwear/ski-touring-boots": page([boot_m]),
                     f"{base}/en-us/women/footwear/ski-touring-boots": page([boot_m])})
    logs = []
    (skin,) = DynafitAdapter({"id": "dynafit", "url": base, "category": "skin",
                              "pages": [{"path": "/equipment/skins"}]}, f, log=logs.append).fetch()
    assert (skin.price, skin.compare_at, skin.available, skin.vendor) == (109.97, 219.95, True, "Dynafit")
    assert skin.url == f"{base}/en-us/blacklight-88-skin-08-0000048888" and skin.body_text == "Light and fast."
    cfg = {"id": "dynafit", "url": base, "category": "boot",
           "pages": [{"path": "/men/footwear/ski-touring-boots", "gender": "men"},
                     {"path": "/women/footwear/ski-touring-boots", "gender": "women"}]}
    (boot,) = DynafitAdapter(cfg, f, log=logs.append).fetch()
    assert boot.gender_hint == "unisex" and boot.external_id == "08-0000061931"


def test_log_mentions_unrendered_pages():
    base = "https://www.dynafit.com"
    logs = []
    f = FakeFetcher({f"{base}/en-us/equipment/bindings": page([RIDGE], nb=23)})
    list(DynafitAdapter({"id": "dynafit", "url": base, "category": "binding",
                         "pages": [{"path": "/equipment/bindings"}]}, f, log=logs.append).fetch())
    assert "23" in logs[0]
