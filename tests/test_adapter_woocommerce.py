"""WooCommerce Store API 适配器：手写的小 fixture（按 2026-10 mountainshop.net / oyuki.com 的响应裁剪），不联网。"""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from skideals.adapters.woocommerce import WooCommerceAdapter, money, variation_options


def prices(now, regular, minor=2):
    return {"price": str(now), "regular_price": str(regular), "sale_price": str(now), "currency_code": "USD",
            "currency_minor_unit": minor}


SOUL = {
    "id": 53038, "type": "variable", "name": "Rossignol Soul Pro Women&#8217;s Skis + Xpress 10 Bindings 2027",
    "permalink": "https://www.mountainshop.net/product/rossignol-soul-pro-womens-skis/",
    "short_description": "", "description": "<p><strong>Terrain:</strong> All Mountain</p>",
    "prices": prices(49995, 59995), "is_in_stock": True, "is_purchasable": True,
    "categories": [{"id": 114, "name": "Women's Skis", "slug": "womens-skis"}], "tags": [],
    "brands": [{"id": 1761, "name": "Rossignol", "slug": "rossignol"}],
    "images": [{"src": "https://x/full.png", "thumbnail": "https://x/400.png"}],
    "attributes": [{"name": "Size", "has_variations": True,
                    "terms": [{"name": "140cm", "slug": "140cm"}, {"name": "150cm", "slug": "150cm"},
                              {"name": "160cm", "slug": "160cm"}]}],
    "variations": [{"id": 53039, "attributes": [{"name": "Size", "value": "140cm"}]},
                   {"id": 53040, "attributes": [{"name": "Size", "value": "150cm"}]}],
}
SOUL_VARS = [
    {"id": 53039, "parent": 53038, "type": "variation", "variation": "Size: 140cm", "prices": prices(44995, 59995),
     "is_in_stock": True, "is_purchasable": True, "permalink": SOUL["permalink"] + "?attribute_pa_size=140cm"},
    {"id": 53040, "parent": 53038, "type": "variation", "variation": "Size: 150cm", "prices": prices(49995, 59995),
     "is_in_stock": False, "is_purchasable": True, "permalink": SOUL["permalink"] + "?attribute_pa_size=150cm"},
]
BEACON = {
    "id": 7, "type": "simple", "name": "BCA Tracker S Avalanche Beacon", "permalink": "https://www.mountainshop.net/product/tracker-s/",
    "prices": prices(27995, 27995), "is_in_stock": True, "is_purchasable": True,
    "categories": [{"name": "Beacons", "slug": "beacons"}], "tags": [], "brands": [], "attributes": [], "variations": [],
    "images": [],
}
GLOVE = {   # oyuki.com：只展示、链接到经销商 → is_purchasable false
    "id": 26140, "type": "variable", "name": "Sencho Gore-Tex Gloves", "permalink": "https://oyuki.com/product/sencho/",
    "prices": prices(16995, 16995), "is_in_stock": True, "is_purchasable": False,
    "categories": [{"name": "Gloves", "slug": "gloves"}], "tags": [{"name": "Mens Gloves &amp; Mitts", "slug": "mens-gloves-mitts"}],
    "brands": [], "images": [],
    "attributes": [{"name": "Color", "has_variations": True, "terms": [{"name": "Green Heron", "slug": "green-heron"}]}],
    "variations": [{"id": 26142, "attributes": [{"name": "Color", "value": "green-heron"}]}],
}
GLOVE_VARS = [{"id": 26142, "parent": 26140, "variation": "Color: Green Heron", "prices": prices(16995, 16995),
               "is_in_stock": True, "is_purchasable": False}]


class FakeFetcher:
    """按 query 参数返回商品列表；记录请求。"""

    def __init__(self, listings: dict, variations: list, total_pages: dict | None = None):
        self.listings, self.variations, self.total = listings, variations, total_pages or {}
        self.calls = []

    def get(self, url, params=None, **kw):
        q = {k: str(v) for k, v in (params or {}).items()}
        self.calls.append(q)
        assert urlsplit(url).path == "/wp-json/wc/store/v1/products"
        if q.get("type") == "variation":
            parents = {int(x) for x in q["parent"].split(",")}
            items = [v for v in self.variations if v["parent"] in parents]
        else:
            items = self.listings.get((q["category"], q["page"]), [])
        headers = {"x-wp-totalpages": str(self.total[q["category"]])} if q.get("category") in self.total else {}
        return SimpleNamespace(json=lambda: items, headers=headers, url=url, status_code=200)


def adapter(fetcher, category="ski", **cfg):
    return WooCommerceAdapter({"id": "mountainshop", "url": "https://www.mountainshop.net", "category": category, **cfg},
                              fetcher, log=lambda _: None)


def test_money_uses_minor_units():
    assert money(prices(49995, 0)) == 499.95
    assert money({"price": "1500", "currency_minor_unit": 0}) == 1500
    assert money({"price": "0", "currency_minor_unit": 2}) is None and money({}) is None


def test_variation_options_from_slugs_or_string():
    assert variation_options(SOUL, SOUL_VARS[0]) == {"Size": "140cm"}
    parent = {"attributes": [{"name": "Size"}, {"name": "Color"}]}
    assert variation_options(parent, {"id": 1, "variation": "Size: 130cm, Color: Grey, Fire Red"}) == \
        {"Size": "130cm", "Color": "Grey, Fire Red"}
    assert variation_options(GLOVE, GLOVE_VARS[0]) == {"Color": "Green Heron"}


def test_variable_ski_sizes_price_compare_and_gender_hint():
    f = FakeFetcher({("skis", "1"): [SOUL]}, SOUL_VARS)
    (raw,) = adapter(f, pages=[{"category": "skis"}], hints={"womens-skis": {"gender": "women"}}).fetch()
    assert raw.title == "Rossignol Soul Pro Women’s Skis + Xpress 10 Bindings 2027"
    assert raw.vendor == "Rossignol" and raw.currency == "USD" and raw.gender_hint == "women"
    assert raw.price == 449.95 and raw.compare_at == 599.95 and raw.available
    assert [(s.label, s.cm, s.available, s.price) for s in raw.sizes] == [
        ("140cm", 140, True, 449.95), ("150cm", 150, False, 499.95)]
    assert raw.image == "https://x/400.png" and raw.body_text == "Terrain: All Mountain"
    assert raw.url == SOUL["permalink"] and raw.external_id == "53038"
    # 1 页商品（< per_page 就停）+ 1 次变体批量查询
    assert len(f.calls) == 2 and f.calls[1]["parent"] == "53038"


def test_pagination_stops_on_total_pages_and_simple_products():
    full = [dict(BEACON, id=1000 + i) for i in range(100)]
    f = FakeFetcher({("avalanche-safety", "1"): full, ("avalanche-safety", "2"): [BEACON]}, [],
                    total_pages={"avalanche-safety": 2})
    out = list(adapter(f, category="avalanche", pages=[{"category": "avalanche-safety", "gender": "unisex"}]).fetch())
    assert len(out) == 101 and [c["page"] for c in f.calls] == ["1", "2"]   # 没有变体商品 → 不查变体
    b = next(r for r in out if r.external_id == "7")
    assert b.price == 279.95 and b.compare_at is None and b.sizes == [] and b.available
    assert b.gender_hint == "unisex" and b.product_type == "Beacons"


def test_catalog_only_products_are_skipped():
    f = FakeFetcher({("gloves", "1"): [GLOVE]}, GLOVE_VARS)
    assert list(adapter(f, category="glove", pages=["gloves"]).fetch()) == []


def test_hiking_shoe_keeps_size_width_color_rows():
    shoe = {**BEACON, "id": 9, "type": "variable", "name": "Oboz Women's Sawtooth X Low Waterproof",
            "categories": [{"name": "Hiking Shoes", "slug": "hiking-shoes"}],
            "attributes": [{"name": "Color", "has_variations": True, "terms": []},
                           {"name": "Size", "has_variations": True, "terms": []},
                           {"name": "Width", "has_variations": True, "terms": []}],
            "variations": [{"id": 91}, {"id": 92}]}
    vars_ = [{"id": 91, "parent": 9, "variation": "Color: Black, Size: 8, Width: Wide", "prices": prices(12000, 15000),
              "is_in_stock": True, "is_purchasable": True, "permalink": "u91"},
             {"id": 92, "parent": 9, "variation": "Color: Sage, Size: 8, Width: Regular", "prices": prices(15000, 15000),
              "is_in_stock": False, "is_purchasable": True, "permalink": "u92"}]
    f = FakeFetcher({("hiking-shoes", "1"): [shoe]}, vars_)
    (raw,) = adapter(f, category="hiking_shoe", pages=["hiking-shoes"]).fetch()
    assert {(s.label, s.color, s.available, s.url) for s in raw.sizes} == {
        ("8 Wide", "Black", True, "u91"), ("8 Regular", "Sage", False, "u92")}
    assert raw.price == 120 and raw.compare_at == 150 and raw.gender_hint == "women"
    assert raw.extra["shoe_widths"] == ["Regular", "Wide"] and "shoe_width" not in raw.extra
    assert len(raw.extra["variants"]) == 2


def test_query_params_shape():
    f = FakeFetcher({("skis", "1"): []}, [])
    assert list(adapter(f, pages=["skis"]).fetch()) == []
    assert parse_qs("&".join(f"{k}={v}" for k, v in f.calls[0].items())) == {
        "category": ["skis"], "per_page": ["100"], "page": ["1"]}
