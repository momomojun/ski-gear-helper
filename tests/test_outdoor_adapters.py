"""Offline parser/shape fixtures, trimmed from 2026-10-18 Fetcher probes. No DB/network."""
import copy
import json
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from skideals.adapters.rei import ReiAdapter, detail_shoe_system, detail_sizes, listing_sizes
from skideals.adapters.shopify import ShopifyAdapter, ajax_product, explicit_shoe_system
from skideals.models import RawProduct
from skideals.net import BlockedError, FetchError, RobotsDisallowed


def shop(category="hiking_shoe", **cfg):
    return ShopifyAdapter({"id": "gearx", "url": "https://www.gearx.com", "category": category, **cfg}, None)


def product():
    # gearx's third option really is Width; size can also be misnamed Clothing Size.
    return {"id": 1, "handle": "pedroc", "title": "Salewa - Women's Pedroc 2 PTX",
            "product_type": "Footwear > Hiking > Hike Low", "vendor": "Salewa",
            "options": [{"name": "Color"}, {"name": "Clothing Size (Women's)"}, {"name": "Width"}],
            "variants": [
                {"id": 11, "option1": "Black", "option2": "8", "option3": "Regular",
                 "price": "140", "compare_at_price": "180", "available": True},
                {"id": 12, "option1": "Black", "option2": "8", "option3": "Wide",
                 "price": "120", "compare_at_price": "180", "available": True},
                {"id": 13, "option1": "Green", "option2": "8", "option3": "Wide",
                 "price": "80", "compare_at_price": "180", "available": False}]}


def rei(category="hiking_shoe", fetcher=None, **cfg):
    adapter = ReiAdapter({"id": "rei", "url": "https://www.rei.com", "category": category, **cfg}, fetcher,
                         log=lambda _: None)
    adapter.stats = {"detail_requests": 0, "detail_failed": 0}
    adapter._blocked = False
    return adapter


def listing():
    return {"prodId": "201989", "title": "Moab 3 Hiking Shoes - Men's", "brand": "Merrell",
            "link": "/product/201989/merrell-moab-3-hiking-shoes-mens", "available": True,
            "displayPrice": {"min": 100, "max": 145, "compareAt": 145,
                             "priceDisplay": {"priceDisplayType": "PRICE_RANGE"}},
            "colorDetails": {"colors": [{"vendorColorId": 1, "vendorColorName": "WALNUT"},
                                        {"vendorColorId": 2, "vendorColorName": "GRANITE"}]},
            "sizeDetails": {"sizeDetails": [
                {"size": "7", "filterState": "available", "colors": [{"colorId": "1", "skuId": "001"}]},
                {"size": "7 Wide", "filterState": "available",
                 "colors": [{"colorId": "1", "skuId": "002"}, {"colorId": "2", "skuId": "003"}]}]}}


def detail():
    # Width in the actual REI response is embedded in variants[].size, not inferred from foot shape.
    return {"gender": "Men's", "sizeLabel": "Size", "primaryCategory": {"label": "Men's Hiking Shoes"},
            "sizechart": {"valueTable": {"U.S. Men's": {"7": "7", "7.5": "7.5"},
                                          "UK": {"7": "6.5", "7.5": "7"},
                                          "EU": {"7": "40", "7.5": "41"}}},
            "sizes": [{"label": "7"}, {"label": "7 Wide"}], "variants": [
                {"sku": "001", "size": "7", "color": {"displayLabel": "Walnut"},
                 "price": 145, "compareAtPrice": 145, "isAvailable": True, "status": "AVAILABLE"},
                {"sku": "002", "size": "7 Wide", "color": {"label": "WALNUT"},
                 "price": 120, "compareAtPrice": 145, "isAvailable": True, "status": "AVAILABLE"},
                {"sku": "003", "size": "7 Wide", "color": {"displayLabel": "Granite"},
                 "price": 80, "compareAtPrice": 145, "isAvailable": False, "status": "SOLD_OUT"}]}


class DetailFetcher:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        assert "/rest/products/" in url
        if isinstance(self.payload, Exception):
            raise self.payload
        content = json.dumps(self.payload).encode() if isinstance(self.payload, dict) else self.payload
        return SimpleNamespace(content=content, status_code=200)


def test_shopify_preserves_width_color_stock_price_and_sku_urls():
    p = product()
    raw = shop()._to_raw(p, set(), {}, "USD")
    assert raw.price == 120 and raw.compare_at == 180 and raw.available
    assert raw.gender_hint == "women" and len(raw.sizes) == 3
    assert {s.label for s in raw.sizes} == {"8 Regular", "8 Wide"}
    assert {(s.label, s.color, s.price, s.available) for s in raw.sizes} == {
        ("8 Regular", "Black", 140, True), ("8 Wide", "Black", 120, True), ("8 Wide", "Green", 80, False)}
    assert all(s.cm is None for s in raw.sizes)
    assert {s.url.rsplit("=", 1)[-1] for s in raw.sizes} == {"11", "12", "13"}
    assert "shoe_size_system" not in raw.extra
    assert "shoe_width" not in raw.extra
    assert raw.extra["shoe_widths"] == ["Regular", "Wide"]
    assert len(raw.extra["variants"]) == 3
    assert p == product()  # parsing does not mutate the feed


def test_shopify_width_before_size_does_not_replace_size():
    p = product()
    p["options"] = [{"name": "Width"}, {"name": "Footwear Size (US Women's)"}, {"name": "Color"}]
    for v in p["variants"]:
        v["option1"], v["option3"] = v["option3"], v["option1"]
    raw = shop()._to_raw(p, set(), {}, "USD")
    assert raw.extra["shoe_size_system"] == "US"
    assert all("8" in s.label for s in raw.sizes)
    assert len({s.label for s in raw.sizes}) == 2


@pytest.mark.parametrize("name,system", [("Shoe Size (US Men's)", "US"), ("Size U.S. Women's", "US"),
                                         ("UK Size", "UK"), ("EU Size", "EU"),
                                         ("Size", None), ("US / EU", None)])
def test_explicit_size_system_only(name, system):
    assert explicit_shoe_system(name) == system


def test_shopify_mixed_systems_do_not_get_a_product_system():
    p = product()
    p["variants"][0]["option2"] = "US 8"
    p["variants"][1]["option2"] = "EU 40"
    raw = shop()._to_raw(p, set(), {}, "USD")
    assert "shoe_size_system" not in raw.extra
    assert any("EU 40" in s.label for s in raw.sizes)


@pytest.mark.parametrize("title,gender", [("Men's Revolvr Pant", "men"), ("Women's Rain Jacket", "women"),
                                        ("Youth Cypress Brook III Pant", "kids"), ("Unisex Sun Hoody", "unisex"),
                                        ("Sahara Shirt", None)])
def test_shopify_title_gender_on_mixed_collections(title, gender):
    p = product()
    p["title"] = title
    assert shop("outdoor_top")._to_raw(p, set(), {}, "USD").gender_hint == gender


def test_shopify_gender_membership_and_dual_size_labels():
    p = product()
    p["variants"][0]["option2"] = "M10/W11.5"
    hints = {"mens-hiking-shoes": {"gender": "men"}, "womens-hiking-shoes": {"gender": "women"}}
    raw = shop()._to_raw(p, set(hints), hints, "USD")
    assert raw.gender_hint == "unisex"
    assert any("M10/W11.5" in s.label for s in raw.sizes)


def test_shopify_pants_keep_waist_inseam_and_color():
    p = product()
    p["options"] = [{"name": "Color"}, {"name": "Waist"}, {"name": "Inseam"}]
    p["variants"] = [{"id": i, "option1": "Khaki", "option2": "32", "option3": inseam,
                      "price": price, "available": available}
                     for i, inseam, price, available in [(1, "30", "50", True), (2, "32", "40", False)]]
    raw = shop("outdoor_pants")._to_raw(p, set(), {}, "USD")
    assert {s.label for s in raw.sizes} == {"32 / 30", "32 / 32"}
    assert raw.price == 50 and raw.extra["colors"] == ["Khaki"]


def test_shopify_collection_and_ajax_supplement_remain_available():
    p = product()
    ajax = {"id": 2, "handle": "extra", "title": "Women's Hiking Shoes", "type": "Hiking Shoes",
            "description": "<p>Real shoes</p>", "options": ["Color", "Size", "Width"],
            "images": ["https://example.test/shoe.jpg"],
            "variants": [{"id": 21, "options": ["Blue", "8", "Wide"], "price": 9900,
                          "compare_at_price": 15000, "available": True}]}
    calls = []

    class Fetcher:
        def get_json(self, url, **kwargs):
            calls.append(url)
            if url.endswith("/meta.json"):
                return {"currency": "USD"}
            if url.endswith("/products/extra.js"):
                return ajax
            assert url.endswith("/collections/mens-hiking-shoes/products.json")
            assert kwargs["params"] == {"limit": 250, "page": 1}
            return {"products": [p]}

    adapter = shop(collections=["mens-hiking-shoes"], product_handles=["extra"])
    adapter.fetcher = Fetcher()
    raws = list(adapter.fetch())
    assert len(raws) == 2 and len(calls) == 3
    supplemental = next(r for r in raws if r.external_id == "2")
    assert supplemental.price == 99 and supplemental.compare_at == 150
    assert supplemental.sizes[0].label == "8 Wide" and supplemental.sizes[0].color == "Blue"
    assert ajax["variants"][0]["price"] == 9900
    assert ajax_product(ajax)["variants"][0]["price"] == 99


def test_shopify_legacy_ski_still_aggregates_colors():
    p = product()
    p["options"] = [{"name": "Color"}, {"name": "Length"}]
    for v in p["variants"]:
        v["option2"] = "177 cm"
    raw = shop("ski")._to_raw(p, set(), {}, "USD")
    assert len(raw.sizes) == 1 and raw.sizes[0].cm == 177 and raw.price == 120
    assert "variants" not in raw.extra


@pytest.mark.parametrize("category", ["outdoor_jacket", "outdoor_pants", "outdoor_top", "hiking_shoe", "trail_shoe"])
@pytest.mark.parametrize("title", ["Atomic Bent Skis", "Marker Ski Bindings", "Tecnica Ski Boots",
                                  "Ski Helmet", "Snowboard Boots", "Ski Poles"])
def test_outdoor_adapters_reject_ski_hardware(category, title):
    assert not shop(category)._keep({"title": title})
    assert not rei(category)._keep(title)


def test_rei_listing_preserves_only_proven_size_color_associations():
    r = listing()
    sizes = listing_sizes(r, None, None, lambda _: None, preserve_variants=True, url="https://www.rei.com/product/201989")
    assert [(s.label, s.color) for s in sizes] == [("7", "WALNUT"), ("7 Wide", "WALNUT"), ("7 Wide", "GRANITE")]
    assert all(s.price is None and s.cm is None and s.available for s in sizes)
    assert all("?sku=" in s.url for s in sizes)
    adapter = rei()
    raw, needs = adapter._build("201989", r, "Hiking Shoes", {"/c/mens-hiking-shoes"}, {})
    assert needs and raw.gender_hint == "men" and raw.price == 100
    assert raw.extra["size_source"] == "search_index"
    assert "shoe_size_system" not in raw.extra


def test_rei_detail_json_preserves_stock_price_color_and_wide_label():
    fetcher = DetailFetcher(detail())
    adapter = rei(fetcher=fetcher)
    raw = RawProduct("rei", "201989", "https://www.rei.com/product/201989", "Moab 3", category="hiking_shoe")
    adapter._enrich(raw)
    assert raw.price == 120 and raw.compare_at == 145 and raw.gender_hint == "men"
    assert len(raw.sizes) == 3 and {s.label for s in raw.sizes} == {"7", "7 Wide"}
    assert [(s.color, s.available, s.price) for s in raw.sizes] == [
        ("Walnut", True, 145), ("WALNUT", True, 120), ("Granite", False, 80)]
    assert raw.extra["shoe_size_system"] == "US" and "shoe_width" not in raw.extra
    assert raw.extra["shoe_widths"] == ["Wide"]
    assert raw.extra["size_source"] == "product_api" and raw.extra["detail"]
    assert len(raw.extra["variants"]) == 3 and all(s.cm is None for s in raw.sizes)


def test_rei_xml_detail_retains_separate_width_color_and_sku():
    xml = b'''<?xml version="1.0"?><product><gender>Women's</gender><sizeLabel>US Size</sizeLabel>
    <sizes><label>8</label></sizes><variants><sku>002</sku><size>8</size><width>Wide</width>
    <color><displayLabel>Walnut</displayLabel></color><isAvailable>true</isAvailable><status>AVAILABLE</status>
    <price>100</price><compareAtPrice>150</compareAtPrice></variants></product>'''
    adapter = rei(fetcher=DetailFetcher(xml))
    raw = RawProduct("rei", "201989", "https://www.rei.com/product/201989", "Moab", category="hiking_shoe")
    adapter._enrich(raw)
    assert raw.sizes[0].label == "8 Wide" and raw.sizes[0].color == "Walnut"
    assert raw.sizes[0].url.endswith("?sku=002")
    assert raw.gender_hint == "women" and raw.extra["shoe_size_system"] == "US"


def test_rei_chart_conversion_column_does_not_imply_us():
    assert detail_shoe_system({}) is None
    assert detail_shoe_system({"sizechart": {"valueTable": {"US": {"40": "7"}, "EU": {"40": "40"}}}}) == "EU"
    assert detail_shoe_system({"sizechart": {"valueTable": {"US": {"7": "7"}, "UK": {"7": "7"}}}}) is None


def test_rei_legacy_size_aggregation_keeps_best_available_color():
    d = rei(fetcher=DetailFetcher(detail()))._detail("201989")
    sizes = detail_sizes(d["variants"], lambda _: None)
    assert len(sizes) == 2 and sizes[1].label == "7 Wide" and sizes[1].price == 120 and sizes[1].available


def test_rei_full_price_detail_mode_has_a_hard_cap(monkeypatch):
    adapter = rei(detail_mode="all", max_detail_pages=1, category_paths=["/c/mens-hiking-shoes"], gender_paths={})
    r = listing()
    items = {str(i): copy.deepcopy(r) for i in (1, 2)}
    monkeypatch.setattr(adapter, "_collect", lambda *args: (items, {}, {}, {}))
    enriched = []
    monkeypatch.setattr(adapter, "_enrich", lambda p: enriched.append(p.external_id))
    assert len(list(adapter.fetch())) == 2 and len(enriched) == 1
    adapter.cfg["detail_mode"] = "discounted"
    enriched.clear()
    assert len(list(adapter.fetch())) == 2 and not enriched


@pytest.mark.parametrize("error", [BlockedError("https://www.rei.com/rest/products/1", 403),
                                  RobotsDisallowed("https://www.rei.com/rest/products/1"),
                                  FetchError("https://www.rei.com/rest/products/1", 404)])
def test_rei_detail_failure_keeps_listing_and_does_not_retry(error):
    fetcher = DetailFetcher(error)
    adapter = rei(fetcher=fetcher)
    raw, _ = adapter._build("201989", listing(), "Hiking Shoes", set(), {})
    original = copy.deepcopy(raw)
    adapter._enrich(raw)
    assert raw == original and len(fetcher.urls) == 1
    assert adapter.stats["detail_failed"] == 1
    assert adapter._blocked == isinstance(error, BlockedError)


def test_rei_200_challenge_is_not_parsed_as_product():
    adapter = rei(fetcher=DetailFetcher(b'<html><div class="sec-if-cpt-container"></div></html>'))
    with pytest.raises(BlockedError):
        adapter._detail("201989")


def test_rei_empty_paths_never_fall_back_to_ski_defaults():
    adapter = rei(category_path="/c/downhill-skis", category_paths=[], gender_paths={}, exclude_paths=[])
    assert adapter._paths() == ([], {}, [])


def test_configuration_covers_all_five_categories_with_two_registered_stores():
    root = Path(__file__).resolve().parents[1]
    cfg = tomllib.loads((root / "config/categories.toml").read_text(encoding="utf-8"))
    stores = tomllib.loads((root / "config/retailers.toml").read_text(encoding="utf-8"))["retailers"]
    registered = {store["id"] for store in stores}
    for category in ("outdoor_jacket", "outdoor_pants", "outdoor_top", "hiking_shoe", "trail_shoe"):
        sources = {rid for rid, cats in cfg.items() if category in cats}
        assert len(sources) >= 2 and sources <= registered
        c = cfg["rei"][category]
        adapter = rei(category, **c, category_path="/c/downhill-skis")
        main, genders, excludes = adapter._paths()
        assert not main and not excludes and set(genders) == {"men", "women"}
        assert all("ski" not in path for paths in genders.values() for path in paths)
        assert c["detail_mode"] == "all" and c["max_detail_pages"] == 30
    assert "hiking_shoe" not in cfg["sportsbasement"]
    assert cfg["sportsbasement"]["trail_shoe"]["collections"] == ["mens-trail-running-shoes", "womens-trail-running-shoes"]


def test_mixed_rei_widths_do_not_apply_wide_context_to_unmarked_sizes():
    from skideals.footwear import parse_shoe_size
    adapter = rei(fetcher=DetailFetcher(detail()))
    raw = RawProduct("rei", "201989", "https://www.rei.com/product/201989", "Moab", category="hiking_shoe")
    adapter._enrich(raw)
    parsed = [parse_shoe_size(s.label, size_system=raw.extra.get("shoe_size_system"),
                              width=raw.extra.get("shoe_width")) for s in raw.sizes]
    assert [p.width for p in parsed] == ["未注明", "wide", "wide"]
    assert all(p.system == "US" for p in parsed)


def test_shopify_uniform_width_is_a_safe_product_context():
    from skideals.footwear import parse_shoe_size
    p = product()
    p["variants"] = p["variants"][:1]
    raw = shop()._to_raw(p, set(), {}, "USD")
    assert raw.extra["shoe_width"] == "Regular"
    assert parse_shoe_size(raw.sizes[0].label).width == "regular"


def test_shopify_challenge_stops_before_collection_or_product_requests():
    calls = []

    class Fetcher:
        def get_json(self, url, **kwargs):
            calls.append(url)
            raise BlockedError(url, 403)

    adapter = shop(collections=["mens-hiking-shoes"], product_handles=["extra"])
    adapter.fetcher = Fetcher()
    with pytest.raises(BlockedError):
        list(adapter.fetch())
    assert calls == ["https://www.gearx.com/meta.json"]


@pytest.mark.parametrize("gender,ptype,width,prefix", [("men", "LDAG01", "D", "00"),
                                                      ("women", "LDCG01", "B", "11")])
def test_sb_coded_type_and_trail_tags_preserved_with_explicit_width(gender, ptype, width, prefix):
    p = product()
    p["title"] = "Men's Speedgoat 6" if gender == "men" else "Women's Speedgoat 6"
    p["product_type"] = ptype
    p["options"] = [{"name": "Color"}, {"name": "Size"}]
    p["tags"] = [f"{prefix}Trail-Running-Shoes{prefix}", "Activity:Running", "Best Use:Trail",
                 f"BreadcrumbClass:{gender.title()}'s Trail", "BreadcrumbType:Footwear", "Footwear",
                 "Shoes", "Trail Running", f"Shoe Width:{width} ({gender.title()}'s Medium)"]
    raw = shop("trail_shoe")._to_raw(p, set(), {}, "USD")
    assert raw.product_type == ptype and raw.tags == p["tags"]
    assert raw.extra["shoe_width"] == width
    assert all(s.label == f"8 {width}" for s in raw.sizes)
    assert "shoe_size_system" not in raw.extra
    assert raw.gender_hint == gender
    assert len(raw.sizes) == 3 and raw.sizes[-1].available is False


def test_sb_width_tag_never_overrides_variant_width():
    p = product()
    p["tags"] = ["Shoe Width:D (Men's Medium)"]
    raw = shop()._to_raw(p, set(), {}, "USD")
    assert {s.label for s in raw.sizes} == {"8 Regular", "8 Wide"}
    assert "shoe_width" not in raw.extra
    assert raw.extra["shoe_widths"] == ["Regular", "Wide"]
