"""Outdoor categories, adapter prefilter, shoe sizes and cross-store identity."""
from dataclasses import replace

import pytest

from skideals.adapters.base import Adapter
from skideals.categories import BY_ID, CATEGORIES, assign, classify, decide_batch, features, norm_size
from skideals.footwear import norm_size as shoe_norm_size, parse_shoe_size, shoe_size_matches
from skideals.models import RawProduct, SizeOption
from skideals.normalize import detect_brand, normalize, parse_key, split_age_variants


OUTDOOR = {"outdoor_jacket", "outdoor_pants", "outdoor_top", "hiking_shoe", "trail_shoe"}
ORIGINAL = {"ski", "binding", "boot", "pole", "helmet", "goggle", "protection", "jacket", "pants", "suit",
            "midlayer", "baselayer", "glove", "sock", "facewear", "hat", "backpack", "bag", "avalanche", "skin",
            "tuning", "accessory"}


def shoe(title="Altra Lone Peak 8 Trail Running Shoes - Men's", category="trail_shoe", **kwargs):
    return RawProduct(retailer_id="example", external_id="shoe", url="https://example.test/shoe", title=title,
                      category=category, sizes=kwargs.pop("sizes", [SizeOption("9.5")]), **kwargs)


def test_categories_append_without_replacing_original_ids():
    assert len(CATEGORIES) == 27 and set(BY_ID) == ORIGINAL | OUTDOOR
    assert BY_ID["boot"].sizing == "mondo"
    assert all(BY_ID[c].sizing == "shoe" for c in ("hiking_shoe", "trail_shoe"))
    assert all(BY_ID[c].group == "户外" for c in OUTDOOR)


@pytest.mark.parametrize("source,title,expected", [
    ("outdoor_jacket", "Patagonia Torrentshell 3L Rain Jacket", "outdoor_jacket"),
    ("outdoor_jacket", "The North Face Antora Rain Hoodie - Women's", "outdoor_jacket"),
    ("outdoor_jacket", "Outdoor Research Raincoat", "outdoor_jacket"),
    ("outdoor_jacket", "Arc'teryx Beta Hardshell Jacket", "outdoor_jacket"),
    ("outdoor_jacket", "Rab Borealis Softshell Jacket", "outdoor_jacket"),
    ("outdoor_jacket", "Rab Insulated Jacket", "outdoor_jacket"),
    ("outdoor_jacket", "Generic Down Jacket", "outdoor_jacket"),
    ("outdoor_pants", "prAna Stretch Hiking Pants", "outdoor_pants"),
    ("outdoor_pants", "Marmot Rain Pants", "outdoor_pants"),
    ("outdoor_top", "Outdoor Research Quick-Dry Shirt", "outdoor_top"),
    ("outdoor_top", "Patagonia Sun Hoody", "outdoor_top"),
    ("outdoor_top", "Merino Base Layer Shirt", "baselayer"),
    ("outdoor_jacket", "Patagonia R1 Air Fleece Hoody", "midlayer"),
    ("outdoor_jacket", "Patagonia Nano Puff Jacket", "midlayer"),
    ("outdoor_jacket", "Smartwool Merino Socks", "sock"),
    ("outdoor_jacket", "Ski Jacket", "jacket"),
    ("outdoor_pants", "Snowboard Pants", "pants"),
    ("hiking_shoe", "Merrell Moab 3 Mid Hiking Boots", "hiking_shoe"),
    ("hiking_shoe", "La Sportiva TX4 Approach Shoes", "hiking_shoe"),
    ("trail_shoe", "Hoka Speedgoat 6 Trail Running Shoes", "trail_shoe"),
    ("trail_shoe", "Brooks Cascadia 18 Trail Runners", "trail_shoe"),
    ("trail_shoe", "Salomon Trail Shoes", "trail_shoe"),
    ("trail_shoe", "Merrell Moab Hiking Shoes", "hiking_shoe"),
    ("hiking_shoe", "Tecnica Mach1 Ski Boots", "boot"),
    ("trail_shoe", "Burton Snowboard Boots", None),
    ("hiking_shoe", "Atomic Hawx Ski Boot Liner", "accessory"),
    ("trail_shoe", "Replacement Hiking Shoe Laces", "accessory"),
    ("hiking_shoe", "Ski Boot Buckles", "accessory"),
    ("trail_shoe", "Superfeet Insoles", "accessory"),
    ("hiking_shoe", "Winter Boots", None),
    ("hiking_shoe", "Generic Boots", None),
    ("trail_shoe", "Hoka Clifton Road Running Shoes", None),
    ("trail_shoe", "Brooks Ghost Running Shoes", None),
    ("trail_shoe", "Altra Casual Sneakers", None),
    ("trail_shoe", "Daily Trainers", None),
    ("jacket", "Rain Jacket", None),
    ("pants", "Rain Pants", None),
    ("pants", "Volcom Rain GORE-TEX Bibs - Men's", "pants"),
    ("jacket", "Outdoor Hiking Jacket", "outdoor_jacket"),
    ("boot", "Merrell Hiking Boots", "hiking_shoe"),
])
def test_assign_and_adapter_prefilter(source, title, expected):
    assert assign(source, title)[0] == expected
    adapter = Adapter({"id": "test", "url": "https://example.test", "category": source}, None)
    assert adapter.wanted(title) == (expected is not None)


@pytest.mark.parametrize("title,category", [
    ("Hiking Shoes", "hiking_shoe"), ("Trail Running Shoes", "trail_shoe"),
    ("Rain Jacket", "outdoor_jacket"), ("Hiking Pants", "outdoor_pants"),
    ("Quick-Dry Shirt", "outdoor_top"), ("Ski Boots", "boot"),
])
def test_classify_explicit_outdoor(title, category):
    assert classify(title) == category


@pytest.mark.parametrize("source,title,kwargs,expected", [
    ("hiking_shoe", "Moab 3", {"product_type": "Hiking Footwear"}, "hiking_shoe"),
    ("trail_shoe", "Speedgoat 6", {"product_type": "Trail Running Shoes"}, "trail_shoe"),
    ("outdoor_jacket", "Beta Jacket", {"product_type": "Rain Jackets"}, "outdoor_jacket"),
    ("outdoor_jacket", "Sabre Jacket", {"product_type": "Ski Jackets"}, "jacket"),
    ("jacket", "Torrentshell Jacket", {"product_type": "Rain Jackets"}, "outdoor_jacket"),
    ("trail_shoe", "Clifton 9", {"product_type": "Road Running Shoes"}, None),
    ("trail_shoe", "Speedgoat 6", {"tags": ["Category:Trail Running Shoes"]}, "trail_shoe"),
    ("hiking_shoe", "Mach1", {"product_type": "Ski Boots"}, "boot"),
    ("hiking_shoe", "Liner", {"product_type": "Ski Boot Accessories"}, "accessory"),
])
def test_metadata_before_page_fallback(source, title, kwargs, expected):
    assert assign(source, title, **kwargs)[0] == expected


def test_outdoor_mixed_pages_drop_only_unproven_products():
    proven = [assign("trail_shoe", "Merrell Hiking Shoes")] * 5
    unknown = assign("trail_shoe", "Unnamed 7")
    assert unknown == ("trail_shoe", "page")
    categories, mixed = decide_batch("trail_shoe", proven + [unknown])
    assert mixed and categories == ["hiking_shoe"] * 5 + [None]
    rejected = [assign("trail_shoe", "Casual Sneakers")] * 5
    assert decide_batch("trail_shoe", rejected + [unknown]) == ([None] * 6, True)
    assert decide_batch("trail_shoe", [assign("trail_shoe", "Trail Shoes"), unknown])[1] is False


@pytest.mark.parametrize("label,context,expected", [
    ("US Men's 9.50", {}, "US:men:9.5:未注明"),
    ("Women's US 8", {}, "US:women:8:未注明"),
    ("UK 8.5", {}, "UK:未注明:8.5:未注明"),
    ("EU 42", {}, "EU:未注明:42:未注明"),
    ("EU 42 2/3", {}, "EU:未注明:42 2/3:未注明"),
    ("EU 42⅔", {}, "EU:未注明:42 2/3:未注明"),
    ("JP 26.5 cm", {}, "JP:未注明:26.5:未注明"),
    ("9.5", {}, "未注明:未注明:9.5:未注明"),
    ("42", {}, "未注明:未注明:42:未注明"),
    ("US M9.5", {}, "US:men:9.5:未注明"),
    ("US W8", {}, "US:women:8:未注明"),
    ("EU 40 Wide", {}, "EU:未注明:40:wide"),
    ("US 9.5 (2E)", {}, "US:未注明:9.5:2e"),
    ("9.5", {"shoe_size_system": "US", "shoe_gender": "men", "shoe_width": "Wide"}, "US:men:9.5:wide"),
    ("EU 42", {"shoe_size_system": "US", "shoe_gender": "women"}, "EU:women:42:未注明"),
    ("US Women's 8", {"shoe_gender": "men"}, "US:women:8:未注明"),
    ("US 9.5 Wide", {"shoe_width": "Narrow"}, "US:未注明:9.5:wide"),
    ("9", {"shoe_size_system": "Canada"}, "未注明:未注明:9:未注明"),
])
def test_shoe_sizes_keep_system_basis_precision(label, context, expected):
    assert shoe_norm_size(label, **context) == expected
    for category in ("hiking_shoe", "trail_shoe"):
        assert norm_size(category, label, **context) == expected
        assert norm_size(category, expected) == expected


@pytest.mark.parametrize("label", ["MP 26.5", "Mondo 26.5", "US 8/9", "US M9 / W10.5", "US 9 / EU 42",
                                   "9-10", "8W", "Black", "GTX", "One Size", "EU", "", None])
def test_ambiguous_and_non_sizes_fail_closed(label):
    assert shoe_norm_size(label) is None


def test_exact_size_matching_has_no_mondo_or_gender_or_system_conversion():
    assert shoe_size_matches("US Men's 9.5", "US Men's 9.50")
    assert not shoe_size_matches("US Men's 9", "US Men's 9.5")
    assert not shoe_size_matches("US Men's 9", "US Women's 9")
    assert not shoe_size_matches("US 9", "UK 9")
    assert not shoe_size_matches("US 9", "9")
    assert not shoe_size_matches("US 9 Wide", "US 9 Narrow")
    assert not shoe_size_matches("JP 26.5", "MP 26.5")
    assert not shoe_size_matches(None, None)
    assert shoe_size_matches("9.5", "US Men's 9.5", left_context={"size_system": "US", "gender": "men"})
    assert norm_size("boot", "MP 27/27.5") == "27"
    assert norm_size("boot", "26.5") == "26.5"


def test_normalize_exports_keys_without_mutating_source_labels():
    raw = shoe(extra={"shoe_size_system": "US", "shoe_width": "Wide"})
    listing = normalize(raw)
    assert listing.gclass == "m" and listing.gender == "men"
    assert listing.specs["shoe_sizes"] == [{"label": "9.5", "key": "US:men:9.5:wide", "system": "US",
                                             "gender": "men", "value": "9.5", "width": "wide"}]
    assert raw.sizes[0].label == "9.5" and listing.raw is raw
    assert normalize(shoe()).specs["shoe_sizes"][0]["key"] == "未注明:men:9.5:未注明"
    assert normalize(shoe("Altra Lone Peak 8", gender_hint="unisex", extra={"shoe_size_system": "US"})).specs["shoe_sizes"][0]["key"] == "US:未注明:9.5:未注明"


@pytest.mark.parametrize("gender,gclass", [("men", "m"), ("unisex", "u"), ("women", "w"), ("kids", "k")])
def test_shoe_gender_groups_keep_all_four_bases(gender, gclass):
    item = normalize(shoe("Altra Lone Peak 8", gender_hint=gender))
    assert item.gclass == gclass and parse_key(item.model_key)["gclass"] == gclass
    assert item.model_key != normalize(shoe("Altra Lone Peak 8", gender_hint="men" if gender != "men" else "unisex")).model_key


@pytest.mark.parametrize("title,extra,expected", [
    ("Altra Lone Peak 8", {}, "u"),
    ("Altra Lone Peak 8 Men's", {}, "m"),
    ("Altra Lone Peak 8 Youth", {}, "k"),
    ("Altra Lone Peak 8 Women's", {}, "w"),
    ("Altra Lone Peak 8 Junior", {"age_partition": "kids"}, "k"),
])
def test_shoe_age_and_gender(title, extra, expected):
    assert normalize(shoe(title, extra=extra)).gclass == expected


@pytest.mark.parametrize("labels", [["6M", "9M"], ["2T", "3T"], ["US 9", "US 10"]])
def test_shoe_widths_and_bare_numbers_are_not_infant_clothing(labels):
    assert normalize(shoe("Altra Lone Peak 8", sizes=[SizeOption(l) for l in labels])).gclass == "u"


def test_shoe_age_partition_retains_separate_prices():
    raw = shoe("Altra Lone Peak 8", sizes=[SizeOption("Adult US 9", price=120), SizeOption("Youth US 4", price=50)])
    adult, child = split_age_variants(raw)
    assert adult.price == 120 and child.price == 50
    assert normalize(adult).gclass == "u" and normalize(child).gclass == "k"


@pytest.mark.parametrize("left,right", [
    ("Altra Lone Peak 8", "Altra Lone Peak 9"),
    ("Altra Lone Peak 8 Shoes", "Altra Lone Peak Shoes 9"),
    ("Salomon X Ultra 4 GTX", "Salomon X Ultra 4"),
    ("Salomon X Ultra 4 Shoes (GTX)", "Salomon X Ultra 4 Shoes (Non-GTX)"),
    ("Salomon X Ultra 4 Shoes - GTX", "Salomon X Ultra 4 Shoes - Non-GTX"),
    ("Salomon X Ultra 4 Mid GTX", "Salomon X Ultra 4 Low GTX"),
])
def test_shoe_model_dimensions_do_not_collapse(left, right):
    assert normalize(shoe(left)).model_key != normalize(shoe(right)).model_key


@pytest.mark.parametrize("left,right", [
    # 楦宽在每个尺码里（US:men:9:wide），不进型号分组：各店写法 D / Regular / 未注明 对不上，进了分组就没法跨店比价
    ("Altra Lone Peak 8 Wide", "Altra Lone Peak 8"),
    ("Altra Lone Peak 8 (Wide)", "Altra Lone Peak 8 (Narrow)"),
    # 低帮款大多数店不写 Low
    ("Oboz Sawtooth X Low", "Oboz Sawtooth X"),
    # gearx 标题：品牌 - 型号 - 性别 - 配色
    ("Lems - Trail Blazer - Men's - Thunder", "Lems - Trail Blazer - Men's - Juniper"),
    ("Hoka Speedgoat 6 - Black/White", "Hoka Speedgoat 6"),
])
def test_shoe_variants_that_are_not_models_group_together(left, right):
    assert normalize(shoe(left)).model_key == normalize(shoe(right)).model_key


def test_width_metadata_and_title_are_used_without_guessing():
    plain = shoe("Altra Lone Peak 8")
    wide = normalize(replace(plain, extra={"shoe_width": "wide"}))
    assert wide.model_key == normalize(plain).model_key == normalize(shoe("Altra Lone Peak 8 Wide")).model_key
    assert wide.specs["shoe_width"] == "wide"
    assert "wide" in wide.features
    assert normalize(shoe("Salomon X Ultra 4 GTX")).model_key == normalize(shoe("Salomon X Ultra 4 Gore-Tex")).model_key


@pytest.mark.parametrize("name,canonical", [("Altra", "Altra"), ("HOKA ONE ONE", "Hoka"), ("Brooks", "Brooks"),
                                           ("Merrell", "Merrell"), ("La Sportiva", "La Sportiva"),
                                           ("Topo", "Topo Athletic"), ("Topo Athletic", "Topo Athletic")])
def test_outdoor_brands(name, canonical):
    assert detect_brand(name, "Example") == canonical
    assert detect_brand(None, name + " Example Hiking Shoes") == canonical


@pytest.mark.parametrize("category,text,expected", [
    ("outdoor_jacket", "GORE-TEX 3L Shell Jacket", {"goretex", "shell", "waterproof"}),
    ("outdoor_jacket", "Primaloft Insulated Jacket", {"insulated"}),
    ("outdoor_jacket", "Stretch Softshell Jacket", {"softshell", "stretch"}),
    ("outdoor_pants", "Waterproof Stretch Rain Pants", {"waterproof", "stretch"}),
    ("outdoor_top", "Merino Quick-Dry UPF 50 Shirt", {"merino", "quickdry", "sunprotect"}),
    ("hiking_shoe", "Leather Mid GTX Hiking Shoes Vibram", {"leather", "midcut", "goretex", "waterproof", "vibram"}),
    ("trail_shoe", "Low Wide GTX Trail Shoes", {"lowcut", "wide", "goretex"}),
])
def test_outdoor_features(category, text, expected):
    assert set(features(category, text)) >= expected


def test_non_gtx_is_not_a_gtx_feature():
    assert "goretex" not in features("hiking_shoe", "X Ultra 4 Non-GTX")



@pytest.mark.parametrize("source,title,expected", [
    ("outdoor_top", "Trail Running Shirt", "outdoor_top"),
    ("outdoor_top", "Trail Running Sun Hoody", "outdoor_top"),
    ("outdoor_top", "Fleece Shirt", "midlayer"),
    ("trail_shoe", "Trail Running Inspired Sneakers", None),
    ("trail_shoe", "Road Running Trail Shoes", None),
])
def test_activity_words_do_not_turn_clothing_or_daily_shoes_into_trail_shoes(source, title, expected):
    assert assign(source, title)[0] == expected


def test_width_from_size_labels_stays_on_sizes():
    wide = normalize(shoe("Altra Lone Peak 8", sizes=[SizeOption("US 9 Wide")]))
    narrow = normalize(shoe("Altra Lone Peak 8", sizes=[SizeOption("US 9 Narrow")]))
    assert wide.model_key == narrow.model_key
    assert wide.specs["shoe_width"] == "wide"
    assert [z["key"] for z in wide.specs["shoe_sizes"]] != [z["key"] for z in narrow.specs["shoe_sizes"]]


@pytest.mark.parametrize("label,expected", [("US Men's 9", "m"), ("US Women's 8", "w"), ("US 9", "u")])
def test_explicit_size_basis_can_supply_missing_product_gender(label, expected):
    assert normalize(shoe("Altra Lone Peak 8", sizes=[SizeOption(label)])).gclass == expected


def test_fraction_half_and_decimal_precision():
    assert shoe_norm_size("EU 42½") == shoe_norm_size("EU 42.5")
    assert shoe_norm_size("US 9.12345678901234567890123456789") == "US:未注明:9.12345678901234567890123456789:未注明"
    assert shoe_norm_size("US 10.0") == "US:未注明:10:未注明"
    assert shoe_norm_size("JP 265") == "JP:未注明:265:未注明"



@pytest.mark.parametrize("label,expected", [
    ("9.5 / Wide", "未注明:未注明:9.5:wide"),
    ("US 9.5 / Wide", "US:未注明:9.5:wide"),
    ("EU 42 2/3 / Regular", "EU:未注明:42 2/3:regular"),
    ("US Women's 8 / B", "US:women:8:b"),
    ("US 8/9 / Wide", None),
    ("US 8 / 9 Wide", None),
    ("EU 42 2/2", None),
])
def test_source_width_suffix_is_not_a_size_range(label, expected):
    assert shoe_norm_size(label) == expected


def test_mixed_source_widths_remain_separate_from_single_width_models():
    raw = shoe("Altra Lone Peak 8", sizes=[SizeOption("9.5 / Wide"), SizeOption("9.5 / Regular")],
               extra={"shoe_size_system": "US", "shoe_width": "Regular, Wide"})
    item = normalize(raw)
    assert item.specs["shoe_width"] == "regular+wide"
    assert {z["key"] for z in item.specs["shoe_sizes"]} == {"US:未注明:9.5:wide", "US:未注明:9.5:regular"}
    assert item.model_key == normalize(shoe("Altra Lone Peak 8", extra={"shoe_width": "wide"})).model_key


def test_gtx_from_structured_fields_not_description_and_explicit_non_gtx_wins():
    raw = shoe("Salomon X Ultra 4")
    plain = normalize(raw)
    # 描述里的 "Unlike the GTX version" / "low-profile, mid-foot" 不是证据
    prose = normalize(replace(raw, body_text="Unlike the GTX version, low-profile with a high-traction mid-foot"))
    assert prose.model_key == plain.model_key and prose.specs["shoe_cut"] == "未注明"
    tagged = normalize(replace(raw, tags=["Waterproofing: GORE-TEX"]))
    assert tagged.model_key != plain.model_key
    assert tagged.model_key == normalize(shoe("Salomon X Ultra 4 GTX")).model_key
    explicit_plain = normalize(replace(raw, title="Salomon X Ultra 4 Non-GTX", body_text="Also offered in GTX"))
    assert explicit_plain.specs["shoe_membrane"] == "non-gtx"
    assert "goretex" not in explicit_plain.features


def test_shopify_local_fixture_into_normalization_without_fetching():
    from skideals.adapters.shopify import ShopifyAdapter
    adapter = ShopifyAdapter({"id": "fixture", "url": "https://example.test", "category": "trail_shoe"}, None)
    product = {"id": 1, "handle": "lone-peak-8", "title": "Altra Lone Peak 8 Men's Trail Running Shoes",
               "vendor": "Altra", "product_type": "Trail Running Shoes", "tags": [],
               "options": [{"name": "US Size"}, {"name": "Width"}, {"name": "Color"}],
               "variants": [{"id": 11, "option1": "9.5", "option2": "Wide", "option3": "Black",
                             "available": True, "price": "100", "compare_at_price": "150"}]}
    raw = adapter._to_raw(product, set(), {}, "USD")
    assert raw.extra["shoe_size_system"] == "US" and raw.extra["shoe_width"].lower() == "wide"
    assert normalize(raw).specs["shoe_sizes"][0]["key"] == "US:men:9.5:wide"



@pytest.mark.parametrize("compact,spaced", [("US9.5", "US 9.5"), ("EU42⅔", "EU 42 2/3"),
                                          ("UK8.5", "UK 8.5"), ("JP26.5cm", "JP 26.5 cm")])
def test_compact_explicit_systems_preserve_the_same_size_key(compact, spaced):
    assert shoe_norm_size(compact) == shoe_norm_size(spaced)
    assert shoe_norm_size(compact) is not None



@pytest.mark.parametrize("title,ptype,wrapped,width", [
    ("Men's Speedgoat 6", "LDAG01", "00Trail-Running-Shoes00", "D (Men's Medium)"),
    ("Women's Challenger ATR 7", "LDCG01", "11Trail-Running-Shoes11", "B (Women's Medium)"),
])
def test_sb_wrapped_trail_category_and_independent_running_width_tags(title, ptype, wrapped, width):
    # Reduced real collection fixtures: no shoe nouns in title or product_type.
    tags = [wrapped, "BreadcrumbType:Footwear", "Running", f"Shoe Width:{width}"]
    assert assign("trail_shoe", title, ptype, tags) == ("trail_shoe", "meta")
    adapter = Adapter({"id": "sportsbasement", "url": "https://www.sportsbasement.com", "category": "trail_shoe"}, None)
    assert adapter.wanted(title, ptype)  # Adapter.wanted has not received tags yet.
    decisions = [assign("trail_shoe", title, ptype, tags)] * 75
    final, mixed = decide_batch("trail_shoe", decisions)
    assert not mixed and final == ["trail_shoe"] * 75


def test_running_and_width_tags_do_not_form_an_exclusion_phrase():
    # No category evidence here: retain page fallback, not false "not-ours".
    assert assign("trail_shoe", "Men's Example 6", "LDAG01",
                  ["Running", "Shoe Width:D (Men's Medium)"]) == ("trail_shoe", "page")


@pytest.mark.parametrize("kwargs", [
    {"product_type": "Running Shoes"},
    {"tags": ["Running Shoes", "Shoe Width:D"]},
    {"product_type": "Road Running Shoes"},
    {"tags": ["Category:Casual Sneakers"]},
])
def test_actual_running_or_casual_shoe_field_is_still_rejected(kwargs):
    assert assign("trail_shoe", "Men's Example 6", **kwargs) == (None, "not-ours")


def test_wrapped_trail_tag_is_evidence_above_generic_running_shoes_tag():
    assert assign("trail_shoe", "Women's Example 7", "LDCG01",
                  ["11Trail-Running-Shoes11", "Running Shoes"]) == ("trail_shoe", "meta")


@pytest.mark.parametrize("title,expected", [("Men's Ski Jacket", "jacket"),
                                           ("Patagonia R1 Air Fleece Jacket", "midlayer"),
                                           ("Neck Gaiter", "facewear")])
def test_outdoor_jacket_page_still_keeps_shared_and_explicit_ski_categories(title, expected):
    assert assign("outdoor_jacket", title)[0] == expected



@pytest.mark.parametrize("name", ["NNormal", "Nnormal", "NNORMAL", "nnormal"])
def test_nnormal_known_canonical_and_title_brand_agree(name):
    from skideals.normalize import canonical_brand, known_brand
    assert known_brand(name) == canonical_brand(name) == "NNormal"
    assert detect_brand(name, "Kjerag Trail Running Shoes") == "NNormal"
    assert detect_brand(None, name + " Kjerag Trail Running Shoes") == "NNormal"


def test_nnormal_registry_cannot_restore_the_noncanonical_spelling(monkeypatch):
    from skideals import normalize as normalization
    registry = normalization.BrandRegistry({"Nnormal": 100, "NNormal": 1})
    assert registry.canonical("Nnormal") == "NNormal"
    monkeypatch.setattr(normalization, "_REGISTRY", registry)
    assert normalization.canonical_brand("Nnormal") == "NNormal"


def test_nnormal_aliases_share_model_but_color_suffixes_stay_separate():
    a = normalize(shoe("NNormal Kjerag Thunder", vendor="NNormal"))
    b = normalize(shoe("Nnormal Kjerag Thunder", vendor="Nnormal"))
    c = normalize(shoe("NNormal Kjerag Juniper", vendor="NNormal"))
    assert a.brand == b.brand == c.brand == "NNormal"
    assert a.model_key == b.model_key
    assert c.model_key != a.model_key
