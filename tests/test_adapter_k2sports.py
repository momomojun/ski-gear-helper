"""K2 Sports 平台（K2 / Line / Völkl / Marker / Dalbello）：分类页 __NUXT_DATA__ 的解析。不联网。
页面片段按真实结构手写成最小样本（devalue 格式：数组里的元素用下标互相引用）。"""
import json

import pytest

from skideals.adapters.k2sports import (K2SportsAdapter, category_products, clean_title, dedupe_products,
                                        nuxt_payload, title_brand, title_gender)
from skideals.net import BlockedError

QUIET = lambda m: None  # noqa: E731


def devalue(obj) -> str:
    """把普通的 dict / list 编码成 Nuxt 3 的 devalue 数组（测试用的最小实现）。"""
    arr: list = []

    def enc(x) -> int:
        i = len(arr)
        arr.append(None)
        if isinstance(x, dict):
            arr[i] = {k: enc(v) for k, v in x.items()}
        elif isinstance(x, list):
            arr[i] = [enc(v) for v in x]
        else:
            arr[i] = x
        return i

    root = len(arr)
    arr.append(["ShallowReactive", 1])     # 真实页面最外层是 ShallowReactive / Reactive 包装
    enc(obj)
    assert root == 0
    return json.dumps(arr)


def variant(size, price, stock, binding=None, color=None):
    opts = [{"option_display_name": "Size", "values": [{"label": size}]}]
    if binding:
        opts.insert(0, {"option_display_name": "Binding", "values": [{"label": binding}]})
    if color:
        opts.append({"option_display_name": "Color", "values": [{"label": color}]})
    return {"isInStock": stock, "isPurchasable": True, "calculatedPrice": {"value": price}, "option_values": opts}


def item(url, display, title, price, retail, variants, sizes=None, amp=None, regions=("us", "ca")):
    return {"url": url, "display_title": display, "title": title, "regions": list(regions),
            "image": {"amplience_id": amp, "imageset": "Yes"},
            "bcProduct": {"id": 1, "calculatedPrice": {"value": price}, "retailPrice": {"value": retail},
                          "isInStock": any(v["isInStock"] for v in variants), "variants": variants,
                          "product_options": [{"displayName": "Size", "values": [{"label": s} for s in sizes or []]}]}}


SKIS = [
    item("/mindbender-96-womens-skis-ks261813", "K2 Mindbender 96 Women's Skis 2027", "Mindbender 96 Women's Skis - 2627",
         849.95, 849.95, [variant("158", 849.95, True), variant("153", 849.95, False)], sizes=["153", "158"],
         amp="k2_2627_mindbender-96-w_KS261813"),
    # 带固定器选项：同一长度两个 SKU，取有货里最便宜的
    item("/indy-youth-skis-ks240229", "K2 Indy Youth Skis 2027", "Indy Youth Skis - 2627", 185.97, 309.95,
         [variant("124", 309.95, False, "4.5 FDT JR"), variant("124", 185.97, True, "4.5 FDT JR LARGE PLATE"),
          variant("136", 309.95, False, "7.0 FDT JR")], sizes=["124", "136"]),
    item("/fatty-skiboards-ks240232", "K2 Fatty Skiboards 2027", "K2 Fatty Skiboards - 2627", 399.95, 399.95,
         [variant("88", 399.95, True)]),                               # 雪板短板：双板分类不要
    item("/eu-only-ski", "K2 Euro Ski 2027", "Euro Ski - 2627", 500, 500, [variant("170", 500, True)], regions=("eu",)),
    {"url": "/no-bc-product", "display_title": "K2 Ghost Ski", "title": "Ghost", "bcProduct": None},
    # ?page=N 要多了：末尾重复出现前面的商品，只有部分字段
    {"url": "/mindbender-96-womens-skis-ks261813", "display_title": None, "bcProduct": None},
]


def page(products, total=None, more=False, imgs=""):
    data = {"data": {"$abc": {"page": {}},
                     "categoryProducts-us-35-3-120--[]": {"products": products, "total": total or len(products),
                                                          "hasNextPage": more, "endCursor": "x"}},
            "pinia": {}}
    return (f"<html><body>{imgs}<script type=\"application/json\" data-nuxt-data=\"nuxt-app\" "
            f"data-ssr=\"true\" id=\"__NUXT_DATA__\">{devalue(data)}</script></body></html>")


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get_html(self, url):
        self.urls.append(url)
        return self.pages[url] if isinstance(self.pages, dict) else self.pages


def make(category, pages, fetcher, **extra):
    cfg = {"id": "k2", "url": "https://k2snow.com/en-us", "name": "K2", "category": category, "pages": pages, **extra}
    return K2SportsAdapter(cfg, fetcher, QUIET)


def test_nuxt_payload_and_dedupe():
    html = page(SKIS)
    cp = category_products(nuxt_payload(html))
    assert cp["total"] == 6 and len(cp["products"]) == 6
    items = dedupe_products(cp["products"])
    assert len(items) == 5            # 重复的那条去掉，保留字段完整的
    assert items[0]["display_title"] == "K2 Mindbender 96 Women's Skis 2027"
    assert nuxt_payload("<html>no data</html>") is None


def test_ski_listing():
    img = '<img src="https://cdn.media.amplience.net/s/k2/k2_2627_mindbender-96-w_KS261813?w=210&amp;qlt=90">'
    f = FakeFetcher(page(SKIS, imgs=img))
    ps = list(make("ski", ["/c/skis/"], f).fetch())
    assert f.urls == ["https://k2snow.com/en-us/c/skis/?page=3"]
    assert [p.title for p in ps] == ["K2 Mindbender 96 Women's Skis 2027", "K2 Indy Youth Skis 2027"]
    w, kid = ps
    assert w.external_id == "mindbender-96-womens-skis-ks261813"
    assert w.url == "https://k2snow.com/en-us/p/mindbender-96-womens-skis-ks261813"
    assert (w.price, w.compare_at, w.currency, w.vendor) == (849.95, None, "USD", "K2")
    assert w.image == "https://cdn.media.amplience.net/s/k2/k2_2627_mindbender-96-w_KS261813"
    assert [(s.label, s.cm, s.available) for s in w.sizes] == [("153", 153, False), ("158", 158, True)]  # 网站的尺码顺序
    assert w.gender_hint == "women" and w.available is True
    assert (kid.price, kid.compare_at, kid.gender_hint) == (185.97, 309.95, "kids")
    assert [(s.label, s.available, s.price, s.compare_at) for s in kid.sizes] == [
        ("124", True, 185.97, 309.95), ("136", False, 309.95, None)]


def test_page_gender_overrides_title_and_pagination():
    # 第一次 page=3 只拿到 120 个里的 2 个、网站说总共 200 个 → 按总数算出 page=6 再要一次；拿全了就停
    p1 = page(SKIS[:2], total=200, more=True)
    p2 = page(SKIS[:2], total=2, more=False)
    f = FakeFetcher({"https://k2snow.com/en-us/c/skis/womens-skis/?page=3": p1,
                     "https://k2snow.com/en-us/c/skis/womens-skis/?page=6": p2})
    ps = list(make("ski", [{"path": "/c/skis/womens-skis/", "gender": "women"}], f).fetch())
    assert len(f.urls) == 2
    assert {p.title: p.gender_hint for p in ps}["K2 Indy Youth Skis 2027"] == "kids"   # 分类页说女款、标题是儿童 → 儿童
    assert {p.title: p.gender_hint for p in ps}["K2 Mindbender 96 Women's Skis 2027"] == "women"


def test_other_categories_sizes_colors_and_vendor():
    helmet = item("/route-mens-helmet-2026", "K2 Route Men's Helmet", "Route Mens Helmet", 189.95, 189.95,
                  [variant("S", 189.95, False, color="BLACK"), variant("M", 189.95, True, color="BLACK"),
                   variant("M", 179.95, True, color="SAGE")], sizes=["S", "M"])
    ps = list(make("helmet", ["/c/gear/helmets/"], FakeFetcher(page([helmet]))).fetch())
    assert len(ps) == 1
    h = ps[0]
    assert [(s.label, s.cm, s.available, s.price, s.color) for s in h.sizes] == [
        ("S", None, False, 189.95, None), ("M", None, True, 179.95, "SAGE")]
    assert (h.price, h.gender_hint) == (179.95, "men")
    binding = item("/duke-pt-16-bindings-7650y1", "MARKER Duke Pt 16 Bindings 2027", "Duke PT 16 - 2627", 749.99, 749.99,
                   [{"isInStock": True, "isPurchasable": True, "calculatedPrice": {"value": 749.99},
                     "option_values": [{"option_display_name": "Brake Width", "values": [{"label": "100MM"}]}]}])
    b = list(make("binding", ["/c/ski-bindings/"], FakeFetcher(page([binding]))).fetch())[0]
    assert b.vendor == "Marker"                          # K2 官网卖的 Marker 固定器
    assert [(s.label, s.cm, s.available) for s in b.sizes] == [("100MM", None, True)]   # 唯一的选项（刹车宽度）当尺码
    assert b.price == 749.99 and b.available is True


def test_blocked_or_changed_page():
    with pytest.raises(BlockedError):
        list(make("ski", ["/c/skis/"], FakeFetcher("<html>Access denied</html>")).fetch())


def test_title_helpers():
    assert title_gender("Blur 74 Men's/Unisex Skis - 2627") == "unisex"
    assert title_gender("Mindbender Jr Skis (US/CA) - 2627") == "kids"
    assert title_gender("Veloce 75 Mv W Ski Boots - 2627") == "women"        # Dalbello 的 W
    assert title_gender("Alpinist 8 W/O Brake Bindings - 2627") is None
    assert title_gender("Mindbender 96 Mens Skis - 2627") == "men"
    assert clean_title("Omen Jr Skis (US/CA) - 2627", "K2") == "K2 Omen Jr Skis 2027"
    assert title_brand("VÖLKL Blaze 86 Skis 2026", "Völkl") == "Völkl"
    assert title_brand("Speedstick Jr Poles 2025", "Völkl") == "Völkl"
    assert title_brand("Speedstick Jr Poles 2025", None) is None
