"""分类识别、尺码规范化、特征标签、固定器/雪鞋规格。"""
import pytest

from skideals.categories import assign, classify, decide_batch, features, is_wanted, norm_size, specs


@pytest.mark.parametrize("title,cat", [
    ("Atomic Bent 100 Skis 2026", "ski"),
    ("Atomic Bent 100 Skis + Strive 13 GW Bindings", "ski"),     # 套装算双板
    ("Marker Griffon 13 ID Ski Bindings 2026", "binding"),
    ("Tecnica Mach1 MV 120 Ski Boots", "boot"),
    ("Dakine Boot Bag 50L", "bag"),                                # boot bag 不是雪鞋
    ("Leki Spitfire 3D Ski Poles", "pole"),
    ("Smith Vantage MIPS Helmet", "helmet"),
    ("Smith I/O MAG Goggles", "goggle"),
    ("Arc'teryx Sabre Bib Pants - Men's", "pants"),
    ("Burton Kids' One-Piece Snowsuit", "suit"),
    ("Patagonia R1 Fleece Pullover", "midlayer"),
    ("Smartwool Classic Thermal Merino Base Layer Crew", "baselayer"),
    ("Hestra Army Leather Heli Ski 3-Finger", "glove"),            # 标题里没有 glove 也要认出来
    ("Buff Original Neck Gaiter", "facewear"),
    ("Dakine Heli Pro 20L Backpack", "backpack"),
    ("BCA Tracker 4 Avalanche Beacon", "avalanche"),
    ("Pomoca Climbing Skins", "skin"),
    ("Swix Base Cleaner Wax", "tuning"),
])
def test_classify(title, cat):
    assert classify(title) == cat


def test_is_wanted_rejects_items_from_other_categories():
    assert is_wanted("jacket", "Patagonia R1 Fleece Jacket")      # 雪服页里的抓绒外套可以接受
    assert not is_wanted("glove", "Burton Beanie")                 # 手套页里混进帽子 → 不要
    assert not is_wanted("binding", "Bent 100 Skis w/ Strive 13 Bindings")  # 套装不算单买的固定器
    assert is_wanted("boot", "Salomon S/Pro Supra Boa 120 GW")      # 标题没写 boot 也保留（相信分类页）


@pytest.mark.parametrize("cat,label,expected", [
    ("jacket", "X-Large", "XL"), ("jacket", "Medium Tall", "M"), ("jacket", "XX-Large", "XXL"),
    ("helmet", "S/M", "S/M"), ("pants", "8", "8"), ("glove", "7.5", "7.5"), ("goggle", "One Size", "均码"),
    ("goggle", "Black/Chromapop", None), ("boot", "26.5", "26.5"), ("boot", "MP 27/27.5", "27"),
    ("pole", '46"', "115"), ("pole", "120cm", "120"), ("binding", "100mm", "100mm"), ("binding", "Brake 90", "90mm"),
    ("ski", "170 cm", "170"),
    # 各网站的其他写法（都是实际抓到的）
    ("helmet", "M-L", "M/L"), ("helmet", "M-LG", "M/L"), ("helmet", " 55-59CM", "55-59cm"), ("helmet", "X LARGE", "XL"),
    ("helmet", "YXS", "XS"), ("helmet", "MATTE BLACK + PRIZM SAPPHIRE", None), ("glove", "MEDIUM", "M"),
    ("sock", "37-38 (S)", "S"), ("sock", "8-9", "8-9"), ("jacket", "3 T", "3T"), ("pants", "8L", "8"), ("pants", "1X", "1X"),
    ("suit", "3-6 MO", "3-6M"), ("hat", "UNISIZE", "均码"), ("backpack", "30L", None), ("goggle", "SUN BLACK", None),
    ("jacket", "Small (7)", "S"), ("jacket", "10/12", "10-12"), ("baselayer", "4XL", "4XL"),
])
def test_norm_size(cat, label, expected):
    assert norm_size(cat, label) == expected


def test_binding_and_boot_specs():
    assert specs("binding", "Marker Griffon 13 ID 90mm brake") == {"din_max": 13, "brakes": [90]}
    s = specs("binding", "Look Pivot 15 GW 4-15 B95")
    assert s["din"] == [4.0, 15.0] and s["brakes"] == [95]
    assert specs("binding", "Salomon Shift MNC 13")["din_max"] == 13
    assert specs("binding", "Marker Duke PT 16")["din_max"] == 16
    assert specs("boot", "Tecnica Mach1 MV 120 GW Ski Boots") == {"flex": 120, "last": "中楦 MV"}
    assert "touring" in features("binding", "Salomon Shift MNC 13") and "mnc" in features("binding", "Salomon Shift MNC 13")
    assert set(features("goggle", "Smith I/O MAG Low Bridge Fit + Bonus Lens photochromic")) >= \
        {"asianfit", "magnetic", "bonuslens", "photochromic"}


@pytest.mark.parametrize("title,cat", [   # REI 抓取时发现的误判（都已修复）
    ("Smith 4D MAG Goggles with Lens Protector", "goggle"),
    ("Tyrolia Protector PR 11 GW Ski Bindings", "binding"),
    ("Tecnica Crestone VS Ski Boots", "boot"),
    ("Outdoor Research Keystone Shell Pants", "pants"),
    ("Smartwool Under-Helmet Balaclava", "facewear"),
    ("Turtle Fur Fleece Earband", "hat"),
    ("Patagonia Baby Down Sweater Bunting", "suit"),
    ("Salomon Skis Boots", "boot"),
])
def test_classify_regressions(title, cat):
    assert classify(title) == cat


def test_accessory_page_keeps_dryers_and_heated_items():
    assert is_wanted("accessory", "DryGuy Force Dry DX Boot and Glove Dryer")
    assert assign("accessory", "Lenz Heat Sock 5.0 Heated Socks")[0] == "sock"   # 电加热袜也是袜子 → 改判到袜子
    assert is_wanted("accessory", "Neoprene Boot Gloves")
    assert assign("accessory", "Smith I/O MAG Goggles")[0] == "goggle"           # 配件页里的雪镜 → 雪镜
    assert assign("skin", "Pomoca Skin Wax")[0] == "accessory"   # 止滑带专用蜡是配件，不是止滑带本身


# ---- 混装分类页（都是实际抓到的：Sports Basement 头盔页 = 头盔 + 雪镜，雪杖页 = 雪杖 + 各种配件）
SB_GOGGLE_TAGS = ["00Goggles00", "Accessories", "Activity:Snow", "BreadcrumbClass:Goggles", "BreadcrumbType:Accessories"]


@pytest.mark.parametrize("source,title,kw,expected", [
    ("helmet", "Frontier Low Bridge", {"tags": SB_GOGGLE_TAGS, "options": ["Frame + Lens", "Size"]}, "goggle"),
    ("helmet", "Proxy", {"tags": SB_GOGGLE_TAGS}, "goggle"),                         # 标题什么都没写，靠网站标签
    ("helmet", "Squad + Bonus Lens", {}, "goggle"),
    ("helmet", "Oakley Flight Tracker M", {"options": ["Lens Color"]}, "goggle"),     # 靠选项名
    ("helmet", "Mission MIPS", {"tags": ["BreadcrumbClass:Helmets"]}, "helmet"),
    ("helmet", "Salomon Orka Visor Junior", {}, "helmet"),
    ("helmet", "POC Maxilla Breakaway Chin Bar", {}, "accessory"),
    ("helmet", "Obex Connect Headset", {}, "accessory"),
    ("pole", "Hand Warmer (10 Pack)", {"tags": ["BreadcrumbClass:Warmers"]}, "accessory"),
    ("pole", "Super Padded Shorts", {"tags": ["BreadcrumbClass:Protection"]}, "protection"),
    ("pole", "3Feet Winter - Mid", {"tags": ["BreadcrumbClass:Insoles"]}, "accessory"),
    ("pole", "Black Diamond Powder Baskets", {}, "accessory"),
    ("pole", "Carbide Tech Tips", {}, "accessory"),
    ("pole", "Leki Spitfire 3D Ski Poles with Trigger Grip", {}, "pole"),
    ("pole", "Armada Legion", {}, "pole"),                                         # 干净的分类页：没证据也相信
    ("pole", "Faction Prodigy 0 - 2027", {"lengths": [150, 160, 170, 180]}, None),   # 太长：雪板或越野杖，都不要
    ("pole", "Volkl Mantra 88 W/Marker Griffon 13 - 2027", {}, "ski"),
    ("goggle", "Oakley Flight Deck L Replacement Lens", {}, "accessory"),
    ("goggle", "Smith I/O Mag Replacement Lens", {}, "accessory"),
    ("goggle", "Gogglesoc Aurora Pro Soc", {}, "accessory"),
    ("goggle", "Smith 4D MAG + Bonus Lens", {}, "goggle"),
    ("goggle", "Zeal Portal Lens - Polarized", {}, "accessory"),
    ("hat", "Turtle Fur Double-Layer Neck Warmer", {}, "facewear"),
    ("backpack", "BCA Float 22 Avalanche Airbag Pack", {}, "backpack"),
    ("avalanche", "Mammut Barryvox S Avalanche Transceiver", {}, "avalanche"),
    ("bag", "Dakine Boot Pack 50L", {}, "bag"),
])
def test_assign_mixed_pages(source, title, kw, expected):
    assert assign(source, title, **kw)[0] == expected


@pytest.mark.parametrize("source,title,kw,expected", [   # 全库审计时发现的误判（都已修复）
    ("jacket", "Obermeyer Charger Jacket - Men's", {}, "jacket"),          # Charger 是产品线，不是充电器
    ("helmet", "Dakine Charger MIPS Helmet", {}, "helmet"),
    ("glove", "Gordini Charger Mitt - Youth", {}, "glove"),
    ("hat", "686 Majesty Cable Knit Beanie - Women's", {}, "hat"),        # 麻花针织，不是线缆
    ("sock", "Therm-ic Heat Fusion Power Socks + Bluetooth Batteries", {}, "sock"),
    ("sock", "Lenz rcB 1400 Lithium Battery Packs", {}, "accessory"),
    ("goggle", "Smith I/O MAG ChromaPop Snow Goggles with gogglesoc", {}, "goggle"),
    ("goggle", "BlackStrap Goggle Cover", {}, "accessory"),
    ("jacket", "Jones MTN Surf Recycled Jacket - Men's", {}, "jacket"),
    ("hat", "The North Face Salty Dog Beanie - Youth", {}, "hat"),
    ("hat", "686 Icon Visor Beanie - Boy's", {}, "hat"),
    ("bag", "Thule RoundTrip Ski & Snowboard Boot Bag 80L", {}, "bag"),
    ("bag", "Atomic Boot and Helmet Bag 2027", {}, "bag"),
    ("midlayer", "Oyuki Merino 3/4 Pants - Women's", {}, "midlayer"),
    ("tuning", "Swix Jaw Economy Vises For Skis - 3 pc.", {}, "tuning"),
    ("tuning", "Salomon Junior Adjustable Binding Jig", {}, "tuning"),
    ("binding", "Protector+ Attack 14 GW", {}, "binding"),
    ("avalanche", "Arva Refillable Carbon Airbag Canister", {}, "accessory"),
    ("avalanche", "Ortovox Diract Voice Avalanche Package", {}, "avalanche"),
    ("facewear", "686 Double Layer Face Warmer", {}, "facewear"),
    ("facewear", "Le Bent Heavyweight Fleece Neck Gaitor", {}, "facewear"),
    ("boot", "Armada Ar One 120 Mv  Boots 2026", {"url": "https://x/products/armada-ar-one-120-mv-skis-2026"}, "boot"),
    ("protection", "Rossignol FlexVent Strap Back Pad", {}, "protection"),
    ("hat", "Buff CoolNet UV Ellipse Headband", {}, "hat"),
    ("helmet", "Seirus Kids Jr Helmet Cover Prints", {}, "accessory"),
    ("helmet", "Uvex Junior Visor Pro ESS 400 - Orange Replacement", {}, "accessory"),
    ("goggle", "Smith No Fog Cleaning Cloth", {}, "accessory"),
    ("goggle", "Protect Our Winters", {"vendor": "Gogglesoc"}, "accessory"),
    ("helmet", "Smith Mission MIPS Helmet", {}, "helmet"),
    ("pole", "Power Brake2 Race (Attack 13)", {"tags": ["BreadcrumbClass:Bindings"]}, "accessory"),   # 刹车片
    ("pole", "SL Brake FS 78 (Attack 11)", {"tags": ["BreadcrumbClass:Bindings"]}, "accessory"),
    ("binding", "Pivot 2.0 15 GW Brake 95", {}, "binding"),
    ("baselayer", "Hot Chillys Men's Pepper Skins Crewneck Top", {}, "baselayer"),   # 系列名，不是止滑带
    ("midlayer", "Orage Beacon Vest", {}, "midlayer"),                                # 马甲，不是雪崩信标
    ("binding", "Tyrolia Protector+ Attack 14 GW Ski Bindings", {}, "binding"),
    ("boot", "Rossignol Men's Speed 120 HV+ GripWalk On Piste Ski Boots 2025", {}, "boot"),
    ("boot", "Nordica Men's Speedmachine 3 130 S BOA Cuff Ski Boots 2027", {}, "boot"),
    ("boot", "Nordica 45mm Velcro Ski Boot Straps", {}, "accessory"),
    ("facewear", "Seirus Kids' Jr Fuzzy Helmet Hoodz", {}, "accessory"),
    ("hat", "Mitchie's Matchings Women's Ski Goggles Fox Fur Pom Beanie", {}, "hat"),
    ("bag", "Ollies Backside Ski and Pole Carrier", {}, "accessory"),
    ("bag", "Burton Gig Boot 48L Pack", {}, "bag"),
    ("skin", "Pomoca Climb Pro S-Glide Skins", {}, "skin"),
    ("avalanche", "BCA Tracker4 Avalanche Beacon", {}, "avalanche"),
    ("boot", "Bootcap 2.5 Ski Boot Insulated Toe Cover", {}, "accessory"),
    ("boot", "Sidas Boot Traction Ski Boot Sole Protection", {}, "accessory"),
    ("boot", "Pierre Gignoux Ski Boot Parts", {}, "accessory"),
    ("binding", "Transpack Ski Bindings Cover", {}, "accessory"),
    ("binding", "Burton EST Binding Comp Kit", {}, None),                          # 单板固定器
    ("binding", "Union Force Bindings", {}, None),
    ("backpack", "BCA Extra Consumer Refill Kit", {}, "accessory"),
    ("backpack", "Dakine Replacement 2.0 Bite Valve", {}, "accessory"),
    ("skin", "Black Diamond Glop Stopper Skin Wax", {}, "accessory"),
    ("skin", "Black Diamond Ski Skin Tail Straps", {}, "accessory"),
    ("skin", "Black Diamond Gold Label Adhesive", {}, "accessory"),
    ("skin", "Black Diamond Glidelite Mohair Mix Skins", {}, "skin"),
    ("jacket", "Fire + Ice Saelly2 Insulated Ski Jacket (Women's)", {}, "jacket"),   # 品牌名里的 “+” 不是套装
    ("pants", "Fire + Ice Borja4-T Insulated Ski Pant (Women's)", {}, "pants"),
    ("binding", "Nordica Alldrive 74 Skis and Bindings 2024", {}, "ski"),
    ("binding", "2024 Head Easy Joy Women's Skis w Joy 9 GW Bindings", {}, "ski"),
    ("binding", "Blizzard Brahma SP 88 2024+ Marker TCX Bindings - USED", {}, "ski"),
    ("accessory", "22 Designs - Axl/Vice Heel Tube", {}, "accessory"),
    ("accessory", "Glue Tube 75g", {}, "accessory"),
    ("facewear", "evo Freefall Neck Tube", {}, "facewear"),
    ("boot", "80cm Extension Cord for C-Pack", {}, "accessory"),
    ("boot", "Hotronic Recharger XLP 2P and 1P Recharger", {}, "accessory"),
    ("hat", "Skis Pasta Bowl", {}, None),
    ("jacket", "Columbia Men's Cirque Bowl Jacket", {}, "jacket"),     # bowl 是雪场地形，不是碗
    ("jacket", "Oakley Bowls Gore-tex Shell Jacket Mens 2026", {}, "jacket"),
    ("midlayer", "Reima Ornament Fleece Sweater - Toddlers'", {}, "midlayer"),
    ("pole", "Leki Trigger S Strap S-M-L Poles", {"product_type": "Pole Accessories"}, "accessory"),
    ("pole", "Distance Z Trekking/Running Poles", {}, None),
    ("pole", "Leki Detect S Poles", {"product_type": "Ski Poles"}, "pole"),
    ("glove", "Hestra Heli Ski Liner - 5 Finger Glove", {"product_type": "men accessories gloves mittens"}, "glove"),
    ("goggle", "EMS Extreme Magnetic Ski Goggles with Spare Lens", {"product_type": "Ski Goggles"}, "goggle"),
    ("glove", "Hestra Handcuff Wrist Straps - Women's", {}, "accessory"),
    ("goggle", "Oakley Flight Deck L Goggle Lens", {"product_type": "Goggle Accessories"}, "accessory"),
    ("goggle", "Smith I/O MAG Lens", {}, "accessory"),
    ("goggle", "Bliz Ski Goggles - Rave 12 - Mint with Brown Pink Cat 3 Lens", {}, "goggle"),
    ("goggle", "Smith Boy's Daredevil Clear Lens Snow Goggles", {}, "goggle"),
    ("helmet", "4D MAG + Bonus Lens", {}, "goggle"),
    ("pole", "SkiKare Stop Collars (Pair)", {}, "accessory"),
    ("pole", "Tubbs Trail Walking 2 Piece Poles", {}, None),
    ("pole", "Breidablikk Pole 2-Section V2", {"product_type": "Nordic Ski Poles"}, None),
    ("pole", "MSR DynaLock Ascent Carbon Backcountry Poles - Pair", {"product_type": "Backcountry Ski Poles"}, "pole"),
    ("jacket", "Tactics Cascadia 3L Rainbreaker Jacket", {}, None),              # 雨衣不是雪服
    ("jacket", "The North Face Antora Rain Hoodie - Women's", {}, None),
    ("pants", "Volcom Rain GORE-TEX Bibs - Men's", {}, "pants"),                 # Rain 是 Volcom 雪裤的系列名
    ("jacket", "Arc'teryx Rainier Jacket", {}, "jacket"),
])
def test_assign_regressions(source, title, kw, expected):
    assert assign(source, title, **kw)[0] == expected


@pytest.mark.parametrize("source,title,kw", [
    ("goggle", "Oakley Holbrook Prizm Sunglasses", {}),
    ("pole", "Leki Nordic Soft Grip 16mm", {}),              # 越野
    ("pole", "Emsco Heavy Duty Sled", {}),
    ("helmet", "Giro Aether MIPS", {"tags": ["Category:Bike Helmets"]}),
])
def test_assign_rejects_non_ski(source, title, kw):
    assert assign(source, title, **kw)[0] is None


def test_mixed_page_drops_items_without_evidence():
    # 雪杖页：大部分有证据的商品都指向别的分类 → 这是混装页，没证据的 “Bacon”（防滑垫）不要
    results = [assign("pole", t, tags=g) for t, g in [
        ("Hand Warmer (10 Pack)", ["BreadcrumbClass:Warmers"]), ("Toe Warmer (8 Pack)", ["BreadcrumbClass:Warmers"]),
        ("Super Padded Shorts", ["BreadcrumbClass:Protection"]), ("Jam Master Exo Wrist Guards", ["BreadcrumbClass:Protection"]),
        ("3Feet Winter - Mid", ["BreadcrumbClass:Insoles"]), ("Leki Detect S", ["BreadcrumbClass:Poles"]),
        ("Bacon", ["Accessories"])]]
    final, mixed = decide_batch("pole", results)
    assert mixed and final[-1] is None and final[-2] == "pole"
    # 干净的雪杖页：没证据的照样收
    clean = [assign("pole", t) for t in ("Armada Legion", "Leki Spitfire 3D Poles", "Black Diamond Traverse Poles")]
    assert decide_batch("pole", clean) == (["pole", "pole", "pole"], False)


def test_binding_parts_are_not_bindings():
    assert not is_wanted("binding", "Head Power Brake2 Race")
    assert not is_wanted("binding", "Tyrolia Replacement Brake 95mm")
    assert not is_wanted("binding", "Marker Race Plate")
    assert is_wanted("binding", "Marker Griffon 13 ID Ski Bindings")
    assert is_wanted("binding", "Look Pivot 15 GW Bindings 95mm Brake")   # “95mm 刹车”只是规格，不是零件


@pytest.mark.parametrize("title", [   # 各种写法的“板 + 固定器”套装都要认成双板
    "Salomon Men's QST 92 w/ M10 Bindings '27",
    "Nordica Santa Ana 80 Ski System 7.0 FDT Bindings",
    "Head Absolut Joy + Protector 10 GW Skis",
    "Atomic Bent 100 Skis + Strive 13 GW Bindings",
])
def test_ski_packages(title):
    assert classify(title) == "ski"
    assert not is_wanted("binding", title)


def test_boot_parts_are_not_boots():
    assert not is_wanted("boot", "Nordica 45mm Velcro Ski Boot Straps")
    assert not is_wanted("boot", "Intuition Pro Tour Liner")
    assert is_wanted("boot", "Tecnica Mach1 MV 120 Ski Boots")
    assert is_wanted("boot", "Lange Shadow 130 LV GW Ski Boots")


def test_old_year_rule():
    from skideals.crawl import OLD_YEAR_RE
    assert OLD_YEAR_RE.search("K2 T9 Skye Skis-Women's 2003")
    assert OLD_YEAR_RE.search("Scott Rosa Skis - Women's 2011")
    assert not OLD_YEAR_RE.search("K2 K2000 AT99S Skis 2026")      # 型号名里的数字不是年份
    assert not OLD_YEAR_RE.search("Atomic Redster G9 Revoshock S 183")

