"""标准化规则的单元测试 —— 用的都是真实网站上抓到的标题。运行：.venv/Scripts/python.exe -m pytest -q"""
import pytest

from skideals.models import RawProduct, SizeOption
from skideals.normalize import detect_bindings, detect_brand, extract_year, normalize, split_tokens


def raw(title, vendor=None, hint=None, sizes=(), body=None, tags=(), ptype=None, url="https://x/p"):
    return RawProduct(retailer_id="t", external_id=title, url=url, title=title, vendor=vendor,
                      gender_hint=hint, sizes=[SizeOption(str(s), cm=s) for s in sizes], body_text=body,
                      tags=list(tags), product_type=ptype)


@pytest.mark.parametrize("title,vendor,expected", [
    ("Völkl M7 Mantra Skis 2026", "Völkl", "Völkl"),
    ("Volkl Mantra M7 Skis", None, "Völkl"),
    ("REDSTER Q9.8 REVOSHOCK S + I 12 GW", "Atomic", "Atomic"),
    ("2025 K2 Mindbender 99Ti", None, "K2"),
    ("Used Blizzard Rustler 9 Skis", None, "Blizzard"),
    ("【スキー板】アトミック ATOMIC ベント100 BENT 100 (板のみ)", None, "Atomic"),
    ("Line Blade Optic 104", "LINE Skis", "Line"),
])
def test_brand(title, vendor, expected):
    assert detect_brand(vendor, title) == expected


@pytest.mark.parametrize("text,year", [
    ("Atomic Bent 100 Skis 2026", 2026),
    ("ATOMIC 25-26 Bent85 板のみ", 2026),
    ("【24-25】【2025】【旧モデル】", 2025),
    ("Skis 2025/2026", 2026),
    ("Rossignol Sender 106 Ti+ 136-106-126", None),
    ("(26-27 2027)", 2027),
])
def test_year(text, year):
    assert extract_year(text) == year


def test_same_ski_two_retailers_same_key():
    a = normalize(raw("Blizzard Black Pearl 88 Skis - Women's 2026", "Blizzard"))
    b = normalize(raw("Blizzard Black Pearl 88 Women's Skis 2026", None))
    c = normalize(raw("Blizzard Black Pearl 88 Skis 2026", "Blizzard"))  # 没写 Women's，靠女款型号线识别
    assert a.model_key == b.model_key == c.model_key
    assert a.gender == b.gender == c.gender == "women"
    assert a.model == "Black Pearl 88"
    assert a.year == 2026


def test_word_order_and_digit_split():
    a = normalize(raw("Völkl M7 Mantra Skis 2026", "Völkl"))
    b = normalize(raw("Volkl Mantra M7 Skis 2025", None))
    assert a.model_key == b.model_key
    k1 = normalize(raw("K2 Mindbender 99Ti Skis", "K2"))
    k2 = normalize(raw("K2 Mindbender 99 Ti Skis 2026", "K2"))
    assert k1.model_key == k2.model_key and k1.waist == 99


def test_women_w_suffix_separates_from_mens():
    men = normalize(raw("Elan Ripstick 94 Skis 2026", "Elan"))
    w1 = normalize(raw("Elan Ripstick 94 Skis - Women's 2026", "Elan"))
    w2 = normalize(raw("Elan Ripstick 94 W Skis 2026", "Elan"))
    assert w1.model_key == w2.model_key != men.model_key
    assert w2.model == "Ripstick 94"


def test_redster_variants_do_not_merge():
    s9 = normalize(raw("Atomic Redster S9 Revoshock S Skis", "Atomic"))
    x9 = normalize(raw("Atomic Redster X9 Revoshock S Skis", "Atomic"))
    assert s9.model_key != x9.model_key


def test_kids_and_junior_alias():
    a = normalize(raw("Völkl Mantra Junior Skis - Kids' 2026", "Völkl", sizes=[118, 128, 138]))
    b = normalize(raw("Volkl Mantra Jr. Skis", "Volkl"))
    assert a.gender == b.gender == "kids"
    assert a.model_key == b.model_key


def test_bindings_detection_and_key():
    flat = normalize(raw("Atomic Bent 100 Skis 2026", "Atomic"))
    system = normalize(raw("Atomic Bent 100 Skis + Strive 13 GW Bindings 2026", "Atomic"))
    assert not flat.bindings and system.bindings
    assert flat.model_key != system.model_key
    assert flat.model == system.model == "Bent 100"
    assert detect_bindings("ATOMIC アトミック 25-26 Bent85 板のみモデル 金具別売") == (False, None)
    assert detect_bindings("Bent 100 / [ AA0030694 ] + アトミック STRIVE 12 GW ビンディング セット")[0]


def test_gender_from_collection_and_tags():
    assert normalize(raw("Armada ARV 88 Skis 2026", "Armada", hint="unisex")).gender == "unisex"
    assert normalize(raw("Nordica Enforcer 94 Skis", "Nordica", hint="men")).gender == "men"
    both = normalize(raw("Line Chronic 94", "Line", tags=["Men's", "Women's"]))
    assert both.gender == "unisex" and both.gclass == "a"
    kid_hint_adult_len = normalize(raw("K2 Mindbender 99Ti", "K2", hint="kids", sizes=[177, 184]))
    assert kid_hint_adult_len.gender != "kids"


def test_condition_and_waist():
    used = normalize(raw("Used Atomic Bent 100 Skis 2024", "Atomic"))
    assert used.condition == "used" and used.model == "Bent 100"
    spec = normalize(raw("Season Primer Skis 2026", "Season", body="a 99mm waist width and poplar core"))
    assert spec.waist == 99 and spec.waist_src == "spec"
    dims = normalize(raw("Faction Dancer 2", "Faction", body="Dimensions: 131-96-116 mm"))
    assert dims.waist == 96


def test_split_tokens():
    assert split_tokens("M-Free 99Ti") == ["m", "free", "99", "ti"]
    assert split_tokens("Maverick 96 CTI") == ["maverick", "96", "c", "ti"]


def test_not_ski_filter_does_not_hit_brand_names():
    from skideals.adapters.base import NOT_SKI_RE
    for ok in ["2026 Nordica ENFORCER 89 Ski", "Nordica Unleashed 98", "Nordica Santa Ana 92 Demo Ski Package"]:
        assert not NOT_SKI_RE.search(ok), ok
    for bad in ["Atomic Hawx Prime 110 Ski Boots", "Fischer Nordic Crown Skis", "Pomoca Climbing Skins",
                "Columbia The Works Package w/ Pants - Boy's Ski", "Ski Leash"]:
        assert NOT_SKI_RE.search(bad), bad


def test_fold_keeps_japanese_dakuten():
    from skideals.normalize import fold
    assert fold("Völkl") == "volkl"
    assert fold("レディース ブーツ ジュニア") == "レディース ブーツ ジュニア"
    assert fold("ＡＴＯＭＩＣ") == "atomic"


def test_japanese_gender_and_bindings():
    from skideals.intl.kakaku import match
    t = "BLIZZARD ブリザード スキー板 BLACK PEARL 88 (FLAT) 板単品 25-26 モデル レディース"
    assert match(t, "Blizzard", ["88", "black", "pearl"], "w", False) == 1.0
    assert match(t, "Blizzard", ["88", "black", "pearl"], "a", False) == 0.0  # 女款不能配给成人款
    kid = "ATOMIC アトミック ジュニア スキー板 Bent 100 板のみ"
    assert match(kid, "Atomic", ["100", "bent"], "a", False) == 0.0
    boots = "ATOMIC アトミック Bent 100 スキーブーツ"
    assert match(boots, "Atomic", ["100", "bent"], "a", False) == 0.0
    system = "ATOMIC スキー板 Bent 100 + STRIVE 12 GW ビンディング セット"
    assert match(system, "Atomic", ["100", "bent"], "a", False) == 0.0
    assert match(system, "Atomic", ["100", "bent"], "a", True) > 0.5


def test_display_name_has_no_gender_words():
    assert normalize(raw("Nordica Enforcer 100 Men's Skis 2024", "Nordica")).model == "Enforcer 100"
    assert normalize(raw("Nordica Santa Ana 93 Women's Skis", "Nordica")).model == "Santa Ana 93"


def test_race_ski_length_in_title_and_open_box():
    a = normalize(raw("Fischer RC4 Worldcup GS 188 Ski 2023 Mens M-Plate Open Box Return", "Fischer"))
    b = normalize(raw("Fischer RC4 Worldcup GS Skis 2023", "Fischer"))
    assert "188" not in a.tokens.split() and a.condition == "blem"
    assert normalize(raw("Atomic Bent 120 Skis", "Atomic")).tokens == "120 bent"  # 120 是腰宽，要保留


def test_known_brand_and_brand_store_resale():
    from skideals.adapters.shopify import ShopifyAdapter
    from skideals.normalize import known_brand
    assert known_brand("Marker") == "Marker" and known_brand("K2 Skis Inc") == "K2"
    assert known_brand("Amer Sports") is None and known_brand(None) is None

    def product(vendor):
        return {"id": 1, "handle": "x", "title": "Griffon 13 ID", "vendor": vendor, "options": [{"name": "Size"}],
                "variants": [{"option1": "100mm", "price": "299.00", "available": True}]}

    ad = ShopifyAdapter({"id": "jskis", "url": "https://x", "brand": "J Skis", "category": "binding"}, fetcher=None)
    assert ad._to_raw(product("Marker"), set(), {}, "USD").vendor == "Marker"       # 官网转卖别家：用商品 vendor
    assert ad._to_raw(product("Amer Sports"), set(), {}, "USD").vendor == "J Skis"  # 母公司/店名：用官网品牌
    assert ad._to_raw(product("J Skis Inc"), set(), {}, "USD").vendor == "J Skis"


def test_binding_color_and_brake_range_are_not_part_of_model():
    from skideals.normalize import clean_model
    assert clean_model("Marker Griffon 13 ID Black 90mm", "Marker", "Marker", "binding")[1] == ["griffon", "13", "id"]
    assert clean_model("Look Pivot 2.0 15 Blue GW 95-105mm", "Look", "Look", "binding")[0] == "Pivot 2.0 15 GW"
    assert clean_model("Smith Vantage MIPS Matte Black", "Smith", "Smith", "helmet")[0] == "Vantage MIPS"
    assert clean_model("Toko Performance Red", "Toko", "Toko", "tuning")[0] == "Performance Red"  # 蜡的颜色 = 温度档，保留


def test_brand_store_title_brand():
    from skideals.adapters.shopify import ShopifyAdapter
    ad = ShopifyAdapter({"id": "blackcrows", "url": "https://x", "brand": "Black Crows", "category": "binding"}, fetcher=None)
    p = {"id": 1, "handle": "x", "title": "Marker Griffon 13 ID Black 90mm", "vendor": "Black Crows", "options": [],
         "variants": [{"option1": "Default Title", "price": "345.00", "available": True}]}
    assert ad._to_raw(p, set(), {}, "USD").vendor == "Marker"
    p["title"] = "Corvus Freebird"
    assert ad._to_raw(p, set(), {}, "USD").vendor == "Black Crows"


@pytest.mark.parametrize("raw_name,expected", [   # 各网站 vendor 字段的实际写法
    ("AUCLAIR", "Auclair"), ("LE BENT", "Le Bent"), ("Gravity Grabber®", "Gravity Grabber"),
    ("DEATHGRIP GLOVE CO.", "Deathgrip"), ("1910 LLC", "1910"), ("LA THUILE SRL", "La Thuile"),
    ("GOGGLESOC (DEDE)", "Gogglesoc"), ("THE MOUNTAIN STUDIO", "The Mountain Studio"), ("FK SKS", "FK SKS"),
    ("thirtytwo", "ThirtyTwo"), ("KUHL", "Kühl"), ("DB BAGS", "Db"), ("4FRNT", "4FRNT"), ("Default", None),
])
def test_canonical_brand(raw_name, expected):
    from skideals.normalize import canonical_brand
    assert canonical_brand(raw_name) == expected


def test_brand_registry_merges_variants_and_reads_titles():
    from skideals.normalize import BrandRegistry, fold, use_brand_registry
    reg = BrandRegistry({"Alp-N-Rock": 5, "Alp N Rock": 4, "Screamer Hats": 3, "Wooly Warm": 2, "SEASE": 30, "Sease": 2},
                        stores={"evo": "evo", "sportsbasement": "Sports Basement"})
    use_brand_registry(reg)
    try:
        assert reg.canonical("Alp N Rock") == "Alp-N-Rock"
        assert detect_brand(None, "Wooly Warm Kids Beanie") == "Wooly Warm"      # 标题开头认出学到的品牌
        assert detect_brand("Sports Basement", "Rental Demo Skis", "Sports Basement") is None  # vendor 填的是店名
        assert detect_brand("evo", "evo Merge Ski Poles", "evo") == "evo"      # 店的自有品牌
        assert detect_brand(None, "EVO 9 GW CA Junior") is None                 # 固定器型号不是品牌
        assert detect_brand(None, fold("Sease Mountain Jacket")) == "Sease"
    finally:
        use_brand_registry(None)


@pytest.mark.parametrize("title,has_bind", [   # Ski Depot 的系统板：固定器型号直接接在板名后面
    ("Elan WILDCAT 80 TI Shift X EL 9.0 2026", True),
    ("Head Supershape e-Rally SW+ Prot. 2027", True),
    ("Elan ACE SC11+ FX EMX12.0 2027", True),
    ("Nordica Santa Ana 80 FDT", True),
    ("Stockli LASER SX 2027", False),                       # SX 是板名
    ("Head WCR e-SL Rebel Team SW RP WCR T Race", False),    # RP = 竞技板座，不是固定器
    ("Fischer RC4 Worldcup GS Jr jr plate (SKI ONLY)", False),
])
def test_system_skis_without_connector(title, has_bind):
    assert detect_bindings(title)[0] is has_bind
    if has_bind:
        assert "el" not in normalize(raw(title)).tokens.split()   # 型号 token 里不带固定器型号

