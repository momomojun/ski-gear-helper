"""Lightspeed eCom 适配器：手写的小 fixture（按 2026-10 bentgate.com / wasatchtouring.com 的 ?format=json 响应裁剪），不联网。"""
from skideals.adapters.lightspeed import LightspeedAdapter, color_of, size_of, variant_options


def price(now, old):
    return {"price": now, "price_incl": now, "price_excl": now,
            "price_old": old, "price_old_incl": old, "price_old_excl": old}


def item(pid, title, now, old, url, variant="Default", available=True, brand="Black Diamond Equipment"):
    return {"id": pid, "vid": pid * 10, "image": 71272653, "url": url, "title": title,
            "fulltitle": f"{brand} {title}", "variant": variant, "available": available,
            "brand": {"id": 1, "title": brand} if brand else False,
            "price": price(now, old), "description": "A mid-sized carbon touring ski."}


def page(products, page=1, pages=1, title="Skis"):
    return {"shop": {"id": 634685}, "template": "pages/collection.rain",
            "collection": {"count": len(products), "page": page, "pages": pages,
                           "page_next": page + 1 if page < pages else False, "limit": 100, "title": title,
                           "products": {str(p["id"]): p for p in products}}}


HELIO = item(67579741, "Black Diamond Helio Carbon 95 Skis", 949.95, 949.95,
             "black-diamond-helio-carbon-95-skis.html", variant="Size : 176cm")
RADICAL = item(60420771, "Radical 97 Ski", 524.96, 749.95, "radical-97-ski.html",
               variant='"Color: Rock Khaki","Size: 170cm"', brand="Dynafit")
SKINS = item(20681132, "Pomoca Climb 2.0 Skins", 189.95, 0, "pomoca-climb.html", brand="Pomoca")

HELIO_DETAIL = {"product": {
    "id": 67579741, "title": "Black Diamond Helio Carbon 95 Skis", "price": price(949.95, 949.95),
    "stock": {"available": True}, "tags": [], "content": "<p>A mid-sized, <b>objective-focused</b> carbon ski.</p>",
    "variants": {
        "1": {"id": 1, "title": "Size : 155cm", "price": price(949.95, 949.95), "stock": {"available": False}},
        "2": {"id": 2, "title": "Size : 169cm", "price": price(899.95, 949.95), "stock": {"available": True}},
        "3": {"id": 3, "title": "Size : 176cm", "price": price(949.95, 949.95), "stock": {"available": True}}}}}

RADICAL_DETAIL = {"product": {
    "id": 60420771, "title": "Radical 97 Ski", "price": price(524.96, 749.95), "stock": {"available": True},
    "variants": {
        "a": {"id": 11, "title": '"Color: W\'s Royal Purple","Size: 170cm"', "price": price(524.96, 749.95),
              "stock": {"available": False}},
        "b": {"id": 12, "title": '"Color: Rock Khaki","Size: 170cm"', "price": price(499.0, 749.95),
              "stock": {"available": True}},
        "c": {"id": 13, "title": '"Color: Rock Khaki","Size: 177cm"', "price": price(524.96, 749.95),
              "stock": {"available": True}}}}}


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages
        self.urls = []

    def get_json(self, url, **kw):
        self.urls.append(url)
        return self.pages[url]


def adapter(fetcher, category="ski", **cfg):
    return LightspeedAdapter({"id": "bentgate", "url": "https://www.bentgate.com/", "category": category, **cfg},
                             fetcher, log=lambda _: None)


BASE = "https://www.bentgate.com"


def test_variant_titles():
    assert variant_options("Size : 176cm") == {"Size": "176cm"}
    opts = variant_options('"Color: Rock Khaki","Size: 170cm"')
    assert opts == {"Color": "Rock Khaki", "Size": "170cm"}
    assert size_of(opts) == "170cm" and color_of(opts) == "Rock Khaki"
    assert variant_options("Default") == {} and size_of({}) is None
    assert size_of({"Color": "Black"}) is None


def test_listing_pagination_dedupe_gender_and_listing_only_price():
    f = FakeFetcher({
        f"{BASE}/snowsports/skiing/skis/?format=json&limit=100": page([HELIO], 1, 2),
        f"{BASE}/snowsports/skiing/skis/page2.html?format=json&limit=100": page([RADICAL, SKINS], 2, 2),
        f"{BASE}/womens/?format=json&limit=100": page([RADICAL], title="Womens Skis"),
    })
    pages = [{"path": "/snowsports/skiing/skis/", "gender": "men"}, {"path": "womens", "gender": "women"}]
    out = {r.external_id: r for r in adapter(f, pages=pages).fetch()}
    assert len(f.urls) == 3                      # 第 2 页 page_next = False → 停止翻页；默认不抓商品页
    assert set(out) == {"67579741", "60420771"}  # 雪板止滑带不是双板
    helio, radical = out["67579741"], out["60420771"]
    assert helio.price == 949.95 and helio.compare_at is None and helio.sizes == []
    assert helio.vendor == "Black Diamond Equipment" and helio.gender_hint == "men"
    assert helio.url == f"{BASE}/black-diamond-helio-carbon-95-skis.html"
    assert helio.image == "https://cdn.shoplightspeed.com/shops/634685/files/71272653/400x400x2/image.jpg"
    assert radical.price == 524.96 and radical.compare_at == 749.95
    assert radical.gender_hint == "unisex"       # 男、女两个分类页都有
    assert radical.product_type == "Skis" and radical.type_hints == ["Skis", "Womens Skis"]


def test_detail_sizes_stock_and_lowest_available_price():
    f = FakeFetcher({
        f"{BASE}/skis/?format=json&limit=100": page([HELIO, RADICAL]),
        f"{BASE}/black-diamond-helio-carbon-95-skis.html?format=json": HELIO_DETAIL,
        f"{BASE}/radical-97-ski.html?format=json": RADICAL_DETAIL,
    })
    out = {r.external_id: r for r in adapter(f, pages=[{"path": "/skis", "condition": "demo"}],
                                             max_detail_pages=5).fetch()}
    helio = out["67579741"]
    assert [(s.label, s.cm, s.available, s.price) for s in helio.sizes] == [
        ("155cm", 155, False, 949.95), ("169cm", 169, True, 899.95), ("176cm", 176, True, 949.95)]
    assert helio.price == 899.95 and helio.compare_at == 949.95 and helio.available
    assert helio.body_text == "A mid-sized, objective-focused carbon ski." and helio.condition_hint == "demo"
    radical = out["60420771"]
    # 同一长度两个颜色：一个有货就算有货，价格取有货里的最低
    assert [(s.label, s.available, s.price) for s in radical.sizes] == [("170cm", True, 499.0), ("177cm", True, 524.96)]
    assert radical.price == 499.0 and radical.compare_at == 749.95


def test_detail_budget_prefers_discounted_items():
    f = FakeFetcher({
        f"{BASE}/skis/?format=json&limit=100": page([HELIO, RADICAL]),
        f"{BASE}/radical-97-ski.html?format=json": RADICAL_DETAIL,
    })
    out = {r.external_id: r for r in adapter(f, pages=["/skis"], max_detail_pages=1).fetch()}
    assert f.urls[-1].endswith("radical-97-ski.html?format=json")   # 打折的先抓
    assert out["60420771"].sizes and out["67579741"].sizes == []


def test_non_ski_category_keeps_size_labels_without_cm():
    boot = item(5, "Scarpa Maestrale Alpine Touring Ski Boot", 519.99, 799, "maestrale.html", brand="Scarpa")
    f = FakeFetcher({
        f"{BASE}/boots/?format=json&limit=100": page([boot], title="Mens Alpine Touring Boots"),
        f"{BASE}/maestrale.html?format=json": {"product": {"id": 5, "price": price(519.99, 799), "stock": {"available": True},
                                                          "variants": {"1": {"id": 1, "title": "Size : 27.5",
                                                                             "price": price(519.99, 799),
                                                                             "stock": {"available": True}},
                                                                       "2": {"id": 2, "title": "Size : 28.5",
                                                                             "price": price(519.99, 799),
                                                                             "stock": {"available": False}}}}},
    })
    (raw,) = adapter(f, category="boot", pages=[{"path": "/boots", "gender": "men"}], max_detail_pages=3).fetch()
    assert [(s.label, s.cm, s.available) for s in raw.sizes] == [("27.5", None, True), ("28.5", None, False)]
    assert raw.compare_at == 799 and raw.gender_hint == "men"


def test_brake_widths_sort_numerically():
    a = adapter(None, category="binding")
    sizes = a._sizes({str(i): {"id": i, "title": f"Brake Width : {w}", "price": price(599.95, 599.95),
                               "stock": {"available": True}} for i, w in enumerate(["120mm", "97mm", "105mm"])})
    assert [s.label for s in sizes] == ["97mm", "105mm", "120mm"]
