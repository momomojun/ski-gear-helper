"""Running Warehouse 解析测试：列表卡片 / 商品页尺码行，均裁剪自 2026-10-25 的真实页面。不联网。"""
from skideals.adapters.runningwarehouse import RunningWarehouseAdapter, parse_detail, parse_listing, size_label
from skideals.footwear import parse_shoe_size

CARD_FULL = """
<div class="cattable__item-group" role="group" aria-label="item">
<div class="cattable-wrap-cell gtm_impression" data-code="TO2TRM1"
data-gtm_impression_code="TO2TRM1" data-gtm_impression_name="Topo Athletic Traverse 2 Men&apos;s Shoes Black/Grey" data-gtm_impression_price="154.95" data-gtm_impression_brand="Topo Athletic" data-gtm_impression_category="Shoes / Men's"
>
<div class="cattable-wrap-cell-imgwrap">
<a href="
https://www.runningwarehouse.com/Topo_Athletic_Traverse_2/descpage-TO2TRM1.html
" class="cattable-wrap-cell-imgwrap-inner"><img src="https://img.runningwarehouse.com/watermark/rs.php?path=TO2TRM1-1.jpg&nw=339" /></a>
</div>
<a class="cattable-wrap-cell-info" href="
https://www.runningwarehouse.com/Topo_Athletic_Traverse_2/descpage-TO2TRM1.html
">
<div class="cattable-wrap-cell-info-flag new">New</div>
<div class="cattable-wrap-cell-info-name">Topo Athletic Traverse 2</div>
<div class="cattable-wrap-cell-info-sub">Men's Shoes - Black/Grey</div>
<div class="cattable-wrap-cell-info-price">

<span>$</span>

</div>
</a>
</div>
</div>
"""

CARD_SALE = """
<div class="cattable__item-group" role="group" aria-label="item">
<div class="cattable-wrap-cell gtm_impression" data-code="KRSUMM2"
data-gtm_impression_code="KRSUMM2" data-gtm_impression_name="KIPRUN Kipsummit Men&apos;s Shoes Black Orange" data-gtm_impression_price="89.88" data-gtm_impression_brand="KIPRUN" data-gtm_impression_category="Shoes / Men's"
>
<img class="lz-img" src="https://img.runningwarehouse.com/blank.gif" data-src="https://img.runningwarehouse.com/watermark/rs.php?path=KRSUMM2-1.jpg&nw=339" />
<a class="cattable-wrap-cell-info" href="
https://www.runningwarehouse.com/KIPRUN_Kipsummit/descpage-KRSUMM2.html
">
<div class="cattable-wrap-cell-info-flag sale">Clearance -30%</div>
<div class="cattable-wrap-cell-info-name">KIPRUN Kipsummit</div>
<div class="cattable-wrap-cell-info-sub">Men's Shoes - Black Orange</div>
<div class="cattable-wrap-cell-info-price is-sale">
$<span>89.88</span><span class="cattable-wrap-cell-info-price-msrp"><span class="is-crossout">$130.00</span>*</span>
</div>
</a>
</div>
</div>
"""

CARD_UNISEX = """
<div class="cattable__item-group" role="group" aria-label="item">
<div class="cattable-wrap-cell gtm_impression" data-code="NN2TOU7"
data-gtm_impression_code="NN2TOU7" data-gtm_impression_name="NNormal Tomir 02 Unisex Shoes Blue" data-gtm_impression_price="190.00" data-gtm_impression_brand="NNormal" data-gtm_impression_category="Shoes / Unisex"
>
<a class="cattable-wrap-cell-info" href="
https://www.runningwarehouse.com/NNormal_Tomir_02/descpage-NN2TOU7.html
">
<div class="cattable-wrap-cell-info-name">NNormal Tomir 02</div>
<div class="cattable-wrap-cell-info-sub">Unisex Shoes - Blue</div>
<div class="cattable-wrap-cell-info-price"><span>$</span></div>
</a>
</div>
</div>
"""

# 品牌故事卡片：有 data-code 但不是商品（没有商品链接、没有价格）
CARD_STORY = """
<div class="cattable__item-group" role="group" aria-label="item">
<div class="cattable-wrap-cell gtm_impression" data-code="ATTAB" data-gtm_impression_category="Shoes">
<div class="cattable-wrap-cell-info-name">Terrex Agravic TT</div>
</div>
</div>
"""

LIST_MEN = "<html><div class='cattable'>" + CARD_FULL + CARD_SALE + CARD_UNISEX + CARD_STORY + "</div></html>"
LIST_WOMEN = "<html><div class='cattable'>" + CARD_UNISEX + "</div></html>"


def _row(code, size, width, stock, price, lst, flags="data-webordering data-web-stock", size_name="Men's Shoe US Sizes"):
    width_li = ("" if width is None else f"""
<li class="js-ordering-style" data-styleabbrev="Width" data-styleitemcode="{width}">
<strong class="js-ordering-style-name style">Men's Shoe Widths</strong>:
<span class="js-ordering-style-item-name styleitem">{width}</span>
</li>""")
    lp = f'data-list-price="{lst}"' if lst else ""
    return f"""
<tr
class="js-ordering-subproduct subproduct cf"
itemprop="offers"
data-code="{code}"
{flags}
{lp}
>
<td><strong class="js-ordering-name name" itemprop="itemOffered">x</strong>
<div class="fl stock"><strong>In Stock:</strong>
<span class="js-ordering-available available">{stock}</span></div>
<div class="fr"><strong>Price:</strong>
$<span class="js-ordering-price price">{price}</span></div>
<ul class="js-ordering-stylelist stylelist fl">
<li class="js-ordering-style" data-styleabbrev="SIZE">
<strong class="js-ordering-style-name style">{size_name}</strong>:
<span class="js-ordering-style-item-name styleitem">{size}</span>
</li>{width_li}
</ul></td>
</tr>"""


def detail_page(rows: str) -> str:
    return ('<div itemprop="description"> <span id="product_chars"> <h2><p>A trail shoe that&apos;s tough.</p></h2>'
            '<table class="js-ordering-table styled_subproduct_list">' + rows + "</table>")


DETAIL_SALE = detail_page(
    _row("KRSUMM2080D", "8.0", "D", "11/13", "89.88", "130.00",
         "data-webordering data-stock-notify data-no-backorders data-web-stock data-closeout-item")
    + _row("KRSUMM2090D", "9.0", "D", "3", "89.88", "130.00")
    + _row("KRSUMM2100D", "10.0", "D", "4+", "89.88", "130.00"))
DETAIL_FULL = detail_page(
    _row("TO2TRM1090D", "9.0", "D", "15 Days", "154.95", "155.00", "data-webordering data-stock-notify data-web-stock")
    + _row("TO2TRM1095D", "9.5", "D", "1", "154.95", "155.00"))
DETAIL_UNISEX = detail_page(
    _row("NN2TOU7075", "M7.5/W8.5", None, "1", "190.00", None, size_name="Unisex Shoe US Sizes 1.0")
    + _row("NN2TOU7120", "M12.0", None, "0", "190.00", None, size_name="Unisex Shoe US Sizes 1.0"))


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get_html(self, url, **kw):
        self.calls.append(url)
        return self.pages[url]


BASE = "https://www.runningwarehouse.com"
PAGES = {
    BASE + "/trailshoesmen.html": LIST_MEN,
    BASE + "/trailshoeswomen.html": LIST_WOMEN,
    BASE + "/KIPRUN_Kipsummit/descpage-KRSUMM2.html": DETAIL_SALE,
    BASE + "/Topo_Athletic_Traverse_2/descpage-TO2TRM1.html": DETAIL_FULL,
    BASE + "/NNormal_Tomir_02/descpage-NN2TOU7.html": DETAIL_UNISEX,
}
CFG = {"id": "runningwarehouse", "url": BASE, "category": "trail_shoe",
       "pages": [{"path": "/trailshoesmen.html", "gender": "men"}, {"path": "/trailshoeswomen.html", "gender": "women"}]}


def run(**cfg):
    f = FakeFetcher(PAGES)
    out = list(RunningWarehouseAdapter({**CFG, **cfg}, f, log=lambda _: None).fetch())
    return {p.external_id: p for p in out}, f


def test_listing_cards():
    cards = {c["code"]: c for c in parse_listing(LIST_MEN)}
    assert set(cards) == {"TO2TRM1", "KRSUMM2", "NN2TOU7", "ATTAB"}
    full, sale = cards["TO2TRM1"], cards["KRSUMM2"]
    assert full["title"] == "Topo Athletic Traverse 2 Men's Shoes" and full["color"] == "Black/Grey"
    assert full["price"] == 154.95 and full["msrp"] is None      # 原价商品的价格只在 gtm 属性里
    assert sale["price"] == 89.88 and sale["msrp"] == 130.0 and sale["percent_off"] == 30
    assert sale["image"].startswith("https://img.runningwarehouse.com/watermark/rs.php?path=KRSUMM2-1.jpg")
    assert cards["ATTAB"]["price"] is None and cards["ATTAB"]["url"] is None


def test_size_labels_parse_as_us():
    assert size_label([("Men's Shoe US Sizes", "8.0"), ("Men's Shoe Widths", "D")]) == ("US Men's 8.0 D", "D", None)
    assert size_label([("Women's Shoe US Size", "6.5"), ("Women's Shoe Widths", "B")])[0] == "US Women's 6.5 B"
    # 中性款只用网站写的男码，不做男女换算
    assert size_label([("Unisex Shoe US Sizes 1.0", "M7.5/W8.5")]) == ("US Men's 7.5", None, "8.5")
    s = parse_shoe_size("US Men's 8.0 D")
    assert (s.system, s.gender, s.value, s.width) == ("US", "men", "8", "d")


def test_detail_rows_stock():
    rows = parse_detail(DETAIL_SALE)["rows"]
    assert [r["available"] for r in rows] == [False, True, True]   # "11/13" 是到货日期
    assert rows[0]["list"] == 130.0 and rows[0]["sku"] == "KRSUMM2080D"
    assert parse_detail(DETAIL_FULL)["rows"][0]["available"] is False   # "15 Days" 预订
    assert parse_detail(DETAIL_UNISEX)["rows"][1]["available"] is False  # 库存 0


def test_fetch_products():
    out, f = run()
    assert set(out) == {"TO2TRM1", "KRSUMM2", "NN2TOU7"}          # 故事卡片不收，中性款去重
    sale = out["KRSUMM2"]
    assert sale.price == 89.88 and sale.compare_at == 130.0 and sale.gender_hint == "men"
    assert sale.vendor == "KIPRUN" and sale.available is True and sale.category == "trail_shoe"
    assert [(s.label, s.available, s.compare_at) for s in sale.sizes] == [
        ("US Men's 8.0 D", False, 130.0), ("US Men's 9.0 D", True, 130.0), ("US Men's 10.0 D", True, 130.0)]
    assert sale.sizes[1].color == "Black Orange"
    assert sale.extra["shoe_size_system"] == "US" and sale.extra["shoe_width"] == "D"
    full = out["TO2TRM1"]
    assert full.price == 154.95 and full.compare_at is None          # 155.00 vs 154.95 不算打折
    assert full.body_text == "A trail shoe that's tough."
    uni = out["NN2TOU7"]
    assert uni.gender_hint == "unisex" and uni.extra["unisex_women_sizes"] == {"US Men's 7.5": "8.5"}
    assert "shoe_width" not in uni.extra
    assert len(f.calls) == 5                                           # 2 个列表 + 3 个商品页


def test_detail_cap_prefers_discounts():
    out, f = run(max_detail_pages=1)
    assert f.calls[-1].endswith("descpage-KRSUMM2.html") and len(f.calls) == 3
    assert out["TO2TRM1"].sizes == [] and out["TO2TRM1"].available is None
    assert out["TO2TRM1"].price == 154.95
