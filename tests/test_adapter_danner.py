"""Danner（Magento GraphQL）解析测试，字段裁剪自 2026-10-25 的真实响应。不联网。"""
from urllib.parse import unquote

from skideals.adapters.danner import DannerAdapter, query_url, size_label, split_color, width_label
from skideals.footwear import parse_shoe_size


def variant(size, width, stock="IN_STOCK", final=270, regular=270, sku="x"):
    return {"attributes": [{"code": "sizes", "label": size}, {"code": "wth", "label": width}],
            "product": {"sku": sku, "stock_status": stock,
                        "price_range": {"minimum_price": {"regular_price": {"value": regular},
                                                          "final_price": {"value": final}}}}}


def product(sku, name, variants, final=270, regular=270, color=None):
    return {"__typename": "ConfigurableProduct", "sku": sku, "name": name, "color": color, "url_key": name.lower().replace(" ", "-"),
            "url_suffix": ".html", "stock_status": "IN_STOCK", "small_image": {"url": f"https://i.lfi.media/is/image/LaCrosse/{sku}"},
            "price_range": {"minimum_price": {"regular_price": {"value": regular}, "final_price": {"value": final}}},
            "configurable_options": [{"label": "Size"}, {"label": "Width"}], "variants": variants}


MTN45 = product("32886", "Men's MTN45 Mid GTX Smoky Olive", [
    variant("9", "Men's D - Medium"), variant("9", "Men's EE - Wide", "OUT_OF_STOCK"), variant("10", "Men's D - Medium")], color="15322")
TRAIL2650 = product("61300", "Women's Trail 2650 3\" Brown/Red", [
    variant("7", "Women's M - Medium", "OUT_OF_STOCK", 114, 190), variant("8", "Women's M - Medium", final=114, regular=190)],
    final=114, regular=190)
N45 = product("32844", "N45 Low Ghost", [variant("6", "Women's M - Medium", final=220, regular=220)], 220, 220)
HUNT = product("30163", "Men's Skyridge Mossy Oak Original Bottomland", [variant("9", "Men's D - Medium", final=200, regular=200)])


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get_json(self, url, **kw):
        self.calls.append(unquote(url))
        q = unquote(url)
        if "customAttributeMetadata" in q:
            return {"data": {"customAttributeMetadata": {"items": [{"attribute_options": [
                {"value": "15322", "label": "Smoky Olive"}, {"value": "13219", "label": "Bone Brown"}]}]}}}
        cid = q.split('eq:"')[1].split('"')[0]
        page = int(q.split("currentPage:")[1].split(")")[0])
        pages = self.pages[cid]
        return {"data": {"products": {"total_count": sum(map(len, pages)), "items": pages[page - 1],
                                      "page_info": {"total_pages": len(pages), "current_page": page}}}}


CFG = {"id": "danner", "url": "https://www.danner.com", "category": "hiking_shoe",
       "pages": [{"category_id": 498, "gender": "men"}, {"category_id": 509, "gender": "women"}],
       "exclude_title": "Mossy Oak|Realtree|Kryptek|Optifade"}


def run(pages, **cfg):
    f = FakeFetcher(pages)
    out = list(DannerAdapter({**CFG, **cfg}, f, log=lambda _: None).fetch())
    return {p.external_id: p for p in out}, f


def test_width_labels_parse():
    assert width_label("Men's D - Medium") == ("Men's", "D")
    assert width_label("Women's M - Medium") == ("Women's", "Medium")   # 女款字母 M 不能当男码简写
    assert size_label("9.5", "Men's EE - Wide") == "Men's 9.5 EE"
    assert size_label("8", "Women's W - Wide") == "Women's 8 Wide"
    s = parse_shoe_size("Men's 9.5 EE")
    assert (s.gender, s.value, s.width) == ("men", "9.5", "2e")
    s = parse_shoe_size("Women's 8 Wide")
    assert (s.gender, s.value, s.width) == ("women", "8", "wide")


def test_query_url():
    q = unquote(query_url("https://www.danner.com", 498, 2))
    assert q.startswith("https://www.danner.com/graphql?query={products(filter:{category_id:{eq:\"498\"}}")
    assert "currentPage:2" in q and "\n" not in q


def test_fetch_pages_sizes_prices():
    out, f = run({"498": [[MTN45, HUNT], [MTN45]], "509": [[TRAIL2650, N45]]})
    assert set(out) == {"32886", "61300", "32844"}              # 打猎款被 exclude_title 去掉
    assert len(f.calls) == 4                                    # 颜色表 + 男款 2 页、女款 1 页
    m = out["32886"]
    assert m.title == "Men's MTN45 Mid GTX" and m.extra["site_title"] == "Men's MTN45 Mid GTX Smoky Olive"
    assert m.sizes[0].color == "Smoky Olive" and m.extra["colors"] == ["Smoky Olive"]
    assert m.vendor == "Danner" and m.gender_hint == "men"
    assert m.url == "https://www.danner.com/men's-mtn45-mid-gtx-smoky-olive.html"
    assert [(s.label, s.available) for s in m.sizes] == [("Men's 9 D", True), ("Men's 9 EE", False), ("Men's 10 D", True)]
    assert m.price == 270 and m.compare_at is None
    assert m.extra["shoe_widths"] == ["D", "EE"] and "shoe_width" not in m.extra
    assert "shoe_size_system" not in m.extra
    w = out["61300"]
    assert w.title == "Women's Trail 2650 3\" Brown/Red"           # 没有颜色 id：标题原样
    assert w.price == 114 and w.compare_at == 190 and w.sizes[0].compare_at == 190
    assert w.extra["shoe_width"] == "Medium" and w.gender_hint == "women"


def test_unisex_and_size_system():
    out, _ = run({"498": [[N45]], "509": [[N45]]}, shoe_size_system="US")
    assert out["32844"].gender_hint == "unisex"
    assert out["32844"].extra["shoe_size_system"] == "US"


def test_split_color():
    assert split_color("Mountain 600 Leaf 4.5\" Grizzly Brown/Rhodo Red GTX W", "Grizzly Brown/Rhodo Red") ==         "Mountain 600 Leaf 4.5\" GTX W"
    assert split_color("Men's Skyridge Mossy Oak Original Bottomland", "Brown/Camo") ==         "Men's Skyridge Mossy Oak Original Bottomland"
    assert split_color("Women's Danner Light Black", None) == "Women's Danner Light Black"
