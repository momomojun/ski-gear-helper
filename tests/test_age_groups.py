"""成人/儿童报价隔离，包括原始尺码、混合标签和旧数据迁移。"""
import json

import pytest

from skideals import catalog, db, maintenance, normalize as norm
from skideals.models import RawProduct, SizeOption
from skideals.categories import assign


def product(title="Patagonia Nano Puff Jacket", sizes=(), category="midlayer", **kwargs):
    return RawProduct(retailer_id="evo", external_id=title, title=title, vendor=kwargs.pop("vendor", "Patagonia"),
                      url="https://www.evo.com/example", category=category, available=True,
                      sizes=[SizeOption(s) for s in sizes], **kwargs)


@pytest.mark.parametrize("age", ["Infants'", "Toddlers'", "Baby", "Babies", "Newborn", "Teen", "Kids'", "Junior"])
def test_child_titles_never_share_adult_price_group(age):
    adult = norm.normalize(product("Patagonia Nano Puff Jacket - Men's", price=143.99, compare_at=239))
    child = norm.normalize(product(f"Patagonia Nano Puff Jacket - {age}", price=61.99, compare_at=89))
    assert child.gclass == "k" and child.gender == "kids"
    assert child.model_key != adult.model_key


@pytest.mark.parametrize("sizes", [["3-6M", "6-12M", "12-18M"], ["12M-18M", "18-24 MO"],
                                  ["2T", "3T", "4T", "5T"], ["Youth S", "YM", "YL"], ["3T (90)"]])
def test_missing_age_in_title_uses_sizes(sizes):
    p = norm.normalize(product(sizes=sizes))
    assert (p.gender, p.gender_src, p.gclass) == ("kids", "sizes", "k")


@pytest.mark.parametrize("sizes", [["S", "M", "L"], ["4", "6", "8"], ["XS/S", "M/L"],
                                  ["Adult", "Youth"], ["10M", "11M"], []])
def test_ambiguous_or_mixed_sizes_are_not_kids(sizes):
    assert not norm.kids_by_sizes(sizes, "midlayer")


def test_month_sizes_not_applied_to_boot_widths():
    assert not norm.kids_by_sizes(["6M", "9M"], "boot")
    assert norm.normalize(product("Patagonia Nano Puff Jacket - Baby Blue", sizes=["S", "M"])).gclass == "a"


@pytest.mark.parametrize("category", ["helmet", "goggle", "pole", "binding", "boot", "jacket", "midlayer", "ski"])
def test_age_evidence_also_used_in_non_gendered_categories(category):
    p = norm.normalize(product("Patagonia Example", category=category, tags=["Kids", "Unisex"]))
    assert p.gclass == "k" and p.gender_src == "tags"


def test_product_type_and_collection_age_hints():
    assert norm.normalize(product(product_type="Infant Jackets")).gclass == "k"
    assert norm.normalize(product(category="goggle", gender_hint="kids")).gclass == "k"
    assert norm.normalize(product(tags=["Kids", "Men's", "Women's"])).gclass == "a"
    assert norm.normalize(product("Patagonia Men's Nano Puff Jacket", tags=["Kids"])).gclass == "a"


def test_mixed_page_uses_each_age_variants_own_price():
    p = product("Guard", category="protection", price=10)
    p.sizes = [SizeOption("Adult", price=24, compare_at=30), SizeOption("Youth", price=10, compare_at=20)]
    adult, child = norm.split_age_variants(p)
    assert adult.price == 24 and adult.compare_at == 30
    assert child.price == 10 and child.external_id != adult.external_id
    assert norm.normalize(adult).gclass == "a" and norm.normalize(child).gclass == "k"
    assert p.price == 10 and len(p.sizes) == 2  # 原始记录不被原地修改
    p.sizes[0].price = None
    adult_unknown, child = norm.split_age_variants(p)
    assert adult_unknown.price is None and child.price == 10


def test_mixed_title_and_unknown_options_never_restore_child_price_to_adults():
    p = product("Guard Adult / Junior", category="protection", price=10)
    p.sizes = [SizeOption("Adult XL", price=100), SizeOption("Junior 130", price=10), SizeOption("Default", price=10)]
    adult, child, unknown = norm.split_age_variants(p)
    assert adult.price == 100 and norm.normalize(adult).gclass == "a"
    assert child.price == 10 and norm.normalize(child).gclass == "k"
    assert unknown.price is None and unknown.extra["age_uncertain"]
    assert norm.kids_evidence(p.title, adult.sizes, partition="adult") is None
    p.sizes = [SizeOption("Adult / Youth", price=10)]
    uncertain, = norm.split_age_variants(p)
    assert uncertain.price is None


def test_adult_and_junior_protection_prices_are_separated():
    p = product("POC Resistance Layer Tights 2026", category="pants", vendor="POC", price=134.95)
    p.sizes = [SizeOption("Adult - XL", price=164.95), SizeOption("Junior 130", price=134.95)]
    adult, child = norm.split_age_variants(p)
    assert adult.price == 164.95 and child.price == 134.95
    assert norm.normalize(child).gclass == "k"
    assert assign("pants", p.title, "PROTECTIVE GEAR")[0] == "protection"


def test_liners_and_race_variants_keep_distinct_models():
    full = norm.normalize(product("Hestra Heli Ski Glove", category="glove", vendor="Hestra"))
    liner = norm.normalize(product("Hestra Heli Ski Glove Liner", category="glove", vendor="Hestra"))
    gs = norm.normalize(product("One Way Ski Poles RD 16 GS - flame 2027", category="pole", vendor="One Way"))
    sl = norm.normalize(product("One Way Ski Poles RD 16 SL - flame 2027", category="pole", vendor="One Way"))
    assert full.model_key != liner.model_key
    assert "liner" in liner.tokens
    assert gs.model_key != sl.model_key


def test_r1_air_price_variants_match_without_merging_different_cuts():
    a = norm.normalize(product("Patagonia R1 Air Zip Neck Top - Men's"))
    b = norm.normalize(product("Men's Patagonia R1 Air 1/4 Zip Fleece Pullover"))
    c = norm.normalize(product("Patagonia R1 Air Fleece Jacket for Men", gender_hint="men"))
    d = norm.normalize(product("Patagonia Men's R1 Air Jacket"))
    hoodie = norm.normalize(product("Patagonia Men's R1 Air Full-Zip Fleece Hoody"))
    crew = norm.normalize(product("Patagonia Men's R1 Air Fleece Crewneck Fleece"))
    assert a.model_key == b.model_key
    assert c.model_key == d.model_key
    assert len({a.model_key, c.model_key, hoodie.model_key, crew.model_key}) == 4
    assert assign("jacket", "Patagonia Men's Nano Puff Jacket")[0] == "midlayer"
    assert assign("hat", "Patagonia R1 Air Beanie")[0] == "hat"


def test_renormalize_splits_legacy_prices_and_invalidates_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(catalog.fx, "current", lambda: {"rates": {"USD": 1, "CNY": 7, "JPY": 150}})
    monkeypatch.setattr(norm, "_REGISTRY", None)
    monkeypatch.setattr(catalog, "_CACHE", {})
    db.init()
    adult = norm.normalize(product("Patagonia Nano Puff Jacket - Men's", price=143.99, compare_at=239))
    child = norm.normalize(product(sizes=["3-6M", "6-12M"], price=61.99, compare_at=89))
    tagged = norm.normalize(product("Patagonia Nano Puff Jacket - Child metadata", tags=["Kids", "Unisex"], price=70))
    with db.connect() as conn:
        conn.execute("INSERT INTO retailers(id, name, enabled) VALUES('evo', 'evo', 1)")
        for listing in (adult, child, tagged):
            db.save_listing(conn, listing, db.now_iso(), db.today())
        conn.execute("UPDATE listings SET gender='unisex', gender_src='default', gclass='a', model_key=? WHERE title!=?",
                     (adult.model_key, adult.raw.title))
        lid = conn.execute("SELECT id FROM listings WHERE title=?", (adult.raw.title,)).fetchone()[0]
        conn.execute("INSERT INTO price_history(listing_id,day,price,available,sizes) VALUES(?, '2025-01-01',10,1,'Adult,Youth')", (lid,))
    before = catalog.build("midlayer")
    assert len(before["families"]) == 1
    result = maintenance.renormalize(log=lambda _: None)
    assert result["kids"] == 2
    after = catalog.build("midlayer")  # 无 force，也必须看到新分组
    assert after is not before
    fam = next(f for f in after["families"] if f["k"] == adult.model_key)
    assert [o["p"] for o in fam["o"]] == [143.99]
    assert fam["o"][0]["c"] == 239
    detail = catalog.detail(adult.model_key)
    assert all(p[1] != 10 for o in detail["offers"] for p in o["history"])
    assert maintenance.renormalize(log=lambda _: None)["kids"] == 0
    with db.connect() as conn:
        row = conn.execute("SELECT gender, gender_src, model_key, meta FROM listings WHERE title=?", (child.raw.title,)).fetchone()
    assert row["gender"] == "kids" and row["gender_src"] == "sizes"
    assert row["model_key"].endswith("|k")
    assert "gender_hint" in json.loads(row["meta"])


def test_size_specific_sale_url_survives_database_catalog_and_alerts(tmp_path, monkeypatch):
    from skideals.alerts import best_offer
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "variants.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(catalog.fx, "current", lambda: {"rates": {"USD": 1, "CNY": 7, "JPY": 150}})
    monkeypatch.setattr(catalog, "_CACHE", {})
    db.init()
    p = product("Men's Patagonia R1 Air Zip Neck", price=97.30)
    p.sizes = [SizeOption("S", price=97.30, url="https://www.scheels.com/p/small/", color="Green"),
               SizeOption("XL", price=100.99, url="https://www.scheels.com/p/xl-blue/", color="Blue Sag"),
               SizeOption("M", price=None)]
    listing = norm.normalize(p)
    with db.connect() as conn:
        conn.execute("INSERT INTO retailers(id,name,enabled) VALUES('evo','evo',1)")
        db.save_listing(conn, listing, db.now_iso(), db.today())
        xl = best_offer(conn, listing.model_key, size_label="XL")
        assert best_offer(conn, listing.model_key, size_label="M") is None
    assert xl["usd"] == 100.99 and xl["url"].endswith("/xl-blue/")
    fam = catalog.build("midlayer")["families"][0]
    z = next(z for z in fam["o"][0]["z"] if z[0] == "XL")
    assert z[2:] == [100.99, xl["url"], "Blue Sag"]
    assert next(z for z in fam["o"][0]["z"] if z[0] == "M")[2] is None
    size = next(s for s in catalog.detail(listing.model_key)["offers"][0]["sizes"] if s["n"] == "XL")
    assert size["u"] == xl["url"] and size["p"] == 100.99


def test_shopify_single_product_supplement_has_real_stock_and_currency_units():
    from skideals.adapters.shopify import ShopifyAdapter
    data = {"id": 1, "handle": "mens-r1-air-zip", "title": "Mens R1 Air Zip Neck", "vendor": "Patagonia",
            "type": "Fleece", "description": "Technical fleece", "images": ["https://example.com/a.jpg"],
            "options": ["Color", "Size"], "variants": [
                {"title": "Black / M", "options": ["Black", "M"], "price": 13900, "available": True},
                {"title": "Green / S", "options": ["Green", "S"], "price": 1000, "available": False}]}

    class Fetcher:
        def get_json(self, url):
            assert url.endswith('/meta.json') or url.endswith('/products/mens-r1-air-zip.js')
            return {} if url.endswith('/meta.json') else data

    adapter = ShopifyAdapter({"id": "test", "url": "https://example.com", "category": "midlayer",
                              "product_handles": ["mens-r1-air-zip"]}, Fetcher())
    raw, = adapter.fetch()
    assert raw.price == 139 and raw.available
    assert next(s for s in raw.sizes if s.label == "M").available
    assert data["variants"][0]["price"] == 13900
