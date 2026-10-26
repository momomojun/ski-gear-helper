"""Topo Athletic（SuiteCommerce items 接口）解析测试，字段裁剪自 2026-10-25 的真实响应。不联网。"""
from skideals.adapters.topo import TopoAdapter, child_size
from skideals.footwear import parse_shoe_size


def child(iid, color, instock=True, price=155.0):
    return {"itemid": iid, "isinstock": instock, "ispurchasable": instock, "custitem_sca_shoe_colors": color,
            "onlinecustomerprice_detail": {"onlinecustomerprice": price}}


ULTRAVENTURE_4 = {
    "itemid": "M081", "storedisplayname2": "Ultraventure 4", "displayname": "M-Ultraventure 4", "custitem_gender": "Men",
    "custitem_collection": "Trail", "custitem_bestfor_activity": "Trail Running, Hiking, Road to Trail",
    "urlcomponent": "mens-ultraventure-4", "custitem_sca_original_price": "155", "custitem_sca_promotion": "Sale",
    "custitem_sca_final_sale": True, "onlinematrixpricerange": "119 119",
    "onlinecustomerprice_detail": {"onlinecustomerprice": 119.0}, "custitem_sca_default_item_color": "Grey-Grey",
    "itemimages_detail": {"Grey-Grey": {"urls": [{"url": "https://www.topoathletic.com/sca-product-images/M081.Grey-Grey_00.jpg"}]}},
    "matrixchilditems_detail": [child("M081-080-GRYGRY", "Grey-Grey", price=119.0),
                                child("M081-085-GRYGRY", "Grey-Grey", False, 119.0),
                                child("M081-070W-GRYGRY", "Grey-Grey", price=119.0)],
}
TRAVERSE = {   # 部分颜色降价：original_price 为空，价格区间 119–155；降价颜色没有 SKU
    "itemid": "M059", "storedisplayname2": "Traverse", "custitem_gender": "Men", "custitem_collection": "Trail",
    "custitem_bestfor_activity": "Hiking", "urlcomponent": "mens-traverse-1", "custitem_sca_original_price": "",
    "custitem_sca_promotion": "Sale", "onlinematrixpricerange": "119 155",
    "onlinecustomerprice_detail": {"onlinecustomerprice": 119.0},
    "matrixchilditems_detail": [child("M059-085-GRYCHA", "Grey-Charcoal"), child("M059-105W-GRYCHA", "Grey-Charcoal")],
}
WOMEN_TRAVERSE_2 = {
    "itemid": "W106", "storedisplayname2": "Traverse 2", "custitem_gender": "Women", "custitem_collection": "Trail",
    "custitem_bestfor_activity": "Hiking", "urlcomponent": "womens-traverse-2", "custitem_sca_original_price": "",
    "onlinematrixpricerange": "155 155", "onlinecustomerprice_detail": {"onlinecustomerprice": 155.0},
    "matrixchilditems_detail": [child("W106-060-CLATAN", "Clay-Tan"), child("W106-075W-CLATAN", "Clay-Tan")],
}
SPECTER = {   # 公路跑鞋
    "itemid": "M110", "storedisplayname2": "Specter Elite", "custitem_gender": "Men", "custitem_collection": "Road",
    "custitem_bestfor_activity": "Race", "urlcomponent": "mens-specter-elite",
    "matrixchilditems_detail": [child("M110-080-WHTSKY", "White-Sky", price=250.0)],
}
GAITER = {    # 护腿套：尺码不是数字
    "itemid": "U913", "storedisplayname2": "Switchback Gaiter", "custitem_gender": "Unisex",
    "custitem_collection": "Trail", "custitem_bestfor_activity": "Trail Running, Hiking", "urlcomponent": "Switchback-Gaiter",
    "matrixchilditems_detail": [child("U913-SML-BLKBLK", "Black-Black", price=35.0)],
}


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get_json(self, url, **kw):
        self.calls.append(url)
        key = "women" if "womens-shoes" in url else "men"
        offset = int(url.split("offset=")[1].split("&")[0])
        items = self.pages[key]
        return {"total": len(items), "items": items[offset:offset + 2]}   # 每页 2 个，测翻页


PAGES = {"men": [SPECTER, ULTRAVENTURE_4, TRAVERSE, GAITER], "women": [WOMEN_TRAVERSE_2, GAITER]}
BASE_CFG = {"id": "topo", "url": "https://www.topoathletic.com",
            "pages": [{"path": "/men/mens-shoes", "gender": "men"}, {"path": "/women/womens-shoes", "gender": "women"}]}


def run(category, **cfg):
    f = FakeFetcher(PAGES)
    out = list(TopoAdapter({**BASE_CFG, "category": category, **cfg}, f, log=lambda _: None).fetch())
    return {p.external_id: p for p in out}, f


def test_child_size():
    assert child_size("M103-070-BLKDTE") == "7.0"
    assert child_size("M081-125W-GRYGRY") == "12.5 Wide"
    assert child_size("U913-SML-BLKBLK") is None
    s = parse_shoe_size("12.5 Wide", gender="men")
    assert (s.value, s.width) == ("12.5", "wide")


def test_trail_filter_and_sale():
    out, f = run("trail_shoe", collections=["Trail"], activities=["Trail Running"])
    assert set(out) == {"M081"}             # 公路鞋、徒步鞋、护腿套都不收
    p = out["M081"]
    assert p.title == "Men's Ultraventure 4" and p.vendor == "Topo Athletic" and p.gender_hint == "men"
    assert p.price == 119.0 and p.compare_at == 155.0
    assert p.url == "https://www.topoathletic.com/mens-ultraventure-4"
    assert [(s.label, s.available) for s in p.sizes] == [("8.0", True), ("8.5", False), ("7.0 Wide", True)]
    assert p.sizes[0].color == "Grey-Grey" and p.image.endswith("M081.Grey-Grey_00.jpg")
    assert p.extra["final_sale"] is True and p.extra["shoe_widths"] == ["Wide"]
    assert "shoe_width" not in p.extra and "shoe_size_system" not in p.extra   # 混合楦宽；网站没写 US
    assert len(f.calls) == 3                # 男款 2 页 + 女款 1 页（offset 达到 total 就停）


def test_hiking_filter_partial_markdown():
    out, _ = run("hiking_shoe", collections=["Trail"], activities=["Hiking"], exclude_activities=["Trail Running"],
                 shoe_size_system="US")
    assert set(out) == {"M059", "W106"}
    t = out["M059"]
    assert t.price == 155.0 and t.compare_at is None      # 降价颜色没有库存 SKU，现有 SKU 都是原价
    w = out["W106"]
    assert w.title == "Women's Traverse 2" and w.gender_hint == "women"
    assert w.extra["shoe_size_system"] == "US"            # 只有配置写明时才填
