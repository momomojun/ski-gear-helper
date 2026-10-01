"""商品页规格表里的腰宽（skideals/specs.py）。片段都来自真实商品页。"""
from skideals.specs import cache_key, waist_from_page


def test_waist_label_forms():
    assert waist_from_page("<li>Waist: 96mm</li>") == 96
    assert waist_from_page("<td>Waist Width (mm)</td><td>104</td>") == 104
    # Peter Glenn：前面是下划线
    assert waist_from_page("On Sale: YES keyfeature_Profile: Rocker/Camber Hybrid keyfeature_Waist Width: 67 "
                           "feature_Bindings Included: Included") == 67


def test_waist_after_number():
    assert waist_from_page("<p>With a unique 3D shape and a 74-mm waist, these skis are perfect</p>") == 74
    assert waist_from_page("<p>A 112-ish waist width combines with heavy rocker</p>") == 112  # evo
    assert waist_from_page("<p>High floatation with 122mm at the waist</p>") == 122  # Black Crows
    assert waist_from_page("<h3>specs and technical details</h3><dl><dt>width</dt><dd>122 mm</dd></dl>") == 122


def test_range_bucket_is_not_a_waist():
    # Sun & Ski 每个商品页都有腰宽分档（筛选项），不能把 60 当成腰宽
    facet = "Recommended Use : On-Piste Waist Width (mm) : 60 - 79 Gender : Men's Brand : Head"
    assert waist_from_page(facet) is None
    assert waist_from_page("<p>Waist Width (mm) 80 - 89</p>") is None
    assert waist_from_page("<p>Great for 60-79mm waist skis</p>") is None
    # 有分档、也有真实腰宽时取真实的
    assert waist_from_page(f"<div>{facet}</div><p>With a unique 3D shape and a 74-mm waist</p>") == 74
    # 不同长度腰宽略有不同，不算分档
    assert waist_from_page("<li>Waist: 98-100mm</li>") == 98


def test_tip_waist_tail_dimensions():
    assert waist_from_page("<li>Dimensions: 131/96/117</li>") == 96
    assert waist_from_page("<td>Tip-Waist-Tail (mm)</td><td>134-99-120</td>") == 99
    assert waist_from_page("<li>Sidecut 140 x 106 x 128 mm</li>") == 106
    # 没有尺寸上下文的三段数字不算（价格、日期、型号）
    assert waist_from_page("<p>Order 130-96-117 shipped</p>") is None
    # 尖 > 腰 < 尾 才是雪板尺寸
    assert waist_from_page("<li>Dimensions: 96/131/117</li>") is None


def test_no_false_positives():
    assert waist_from_page("<p>Waist 2026 model</p>") is None
    assert waist_from_page("<p>The skis feature a narrow waist and a slalom-inspired design</p>") is None
    assert waist_from_page("<p>Arc'Teryx Mantis 1 Waist Pack</p>") is None
    assert waist_from_page("<p>Waist: 40mm</p>") is None  # 超出 60–145mm 的范围


def test_cache_key_ignores_gender_and_bindings():
    # 同一型号的板身尺寸一样：男女款、含不含固定器共用一条规格
    key = cache_key("ski|blizzard|88 black pearl|w|f")
    assert key == "blizzard|88 black pearl"
    assert cache_key("ski|blizzard|88 black pearl|m|b") == key
