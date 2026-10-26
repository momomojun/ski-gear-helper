"""Skimo Co 适配器：列表页格子、品牌列表、商品页尺码 / 库存 / 选项价差、翻页停止。手写的精简 HTML，不联网。"""
import pytest

from skideals.adapters import skimo
from skideals.adapters.skimo import SkimoAdapter, brand_of, parse_brands, parse_detail, parse_listing

TILE = """
<div id="product-{id}">
  <div class="image"><a href="/{slug}"><img src="https://www.skimo.co/image/cache/data/{slug}-201.jpg" alt="" /></a></div>
  <div class="name"><a href="/{slug}">{title}</a></div>
  <div class="price">{price}</div>
</div>"""

SALE = '<span class="sr-only">Was</span> <span class="price-old">$1099.95</span> <span class="sr-only">Now</span> From&nbsp;<span class="price-new">$699.95</span>'


def page(tiles: str, nxt: str | None = None) -> str:
    link = f'<link href="{nxt}" rel="next" />' if nxt else ""
    return f"<html><head>{link}</head><body><div class=\"product-grid\">{tiles}</div></body></html>"


PAGE1 = page(TILE.format(id=4145, slug="dynafit-dna-pro-skis", title="Dynafit DNA Pro Ski", price=SALE)
             + TILE.format(id=3793, slug="ski-trab-gara-world-cup-women", title="Ski Trab Gara World Cup Ski - Women",
                           price="$1199.95"),
             nxt="/skis?limit=100&page=2")
PAGE2 = page(TILE.format(id=5001, slug="voile-skin-strap", title="Voile Ski Strap", price="$9.95")
             + TILE.format(id=4145, slug="dynafit-dna-pro-skis", title="Dynafit DNA Pro Ski", price=SALE))

BRANDS = """<div class="manufacturer-list"><div class="manufacturer-content"><ul>
<li><a href="/dynafit">Dynafit</a></li><li><a href="/ski-trab">Ski&nbsp;Trab</a></li><li><a href="/scarpa">SCARPA</a></li>
</ul></div></div>"""

DETAIL_SKI = """
<div class="options"><div id="option-6139" class="option">
  <label for="option_select_6139" class="product-label">Ski Length</label><br />
  <select id="option_select_6139" name="option[6139]">
    <option value="">- Select</option>
    <option value="60215">162cm ($1099.95)</option>
    <option value="60213">162 - 2024/25 ($699.95)</option>
    <option value="60217">170cm ($1099.95)</option>
  </select></div></div>
<script>
var base_price = 1099.95; var base_special = 1099.95; var base_stock = "In Stock";
	stock_map["60215"] = "In Stock";
	special_map["60213"] = -400;	stock_map["60213"] = "In Stock";
	stock_map["60217"] = "Available Out Of Stock - <a onclick=\\"notify_me('pre');\\">Notify&nbsp;Me</a>";
</script>"""

DETAIL_JACKET = """
<div class="options"><div id="option-5741" class="option">
  <label class="product-label">Color &amp; US Size</label>
  <select name="option[5741]"><option value="">- Select</option>
    <option value="1">Relic, S</option><option value="2">Relic, M (Out Of Stock)</option><option value="3">Black, M</option>
  </select></div></div>
<script>var base_price = 699.95; var base_special = 399.95; var base_stock = "In Stock";
stock_map["1"] = "In Stock"; stock_map["3"] = "In Stock";</script>"""


@pytest.fixture(autouse=True)
def _fresh_brand_cache():
    skimo._BRANDS.clear()   # 品牌列表按网站缓存在模块里（一次抓取的多个分类共用）
    yield
    skimo._BRANDS.clear()


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get_html(self, url, **kw):
        self.calls.append(url)
        return self.pages[url]


def adapter(category, pages, fetcher):
    return SkimoAdapter({"id": "skimo", "url": "https://skimo.co", "category": category, **pages}, fetcher,
                        log=lambda _: None)


def test_listing_tiles_prices_and_next():
    tiles, nxt = parse_listing(PAGE1)
    assert nxt == "/skis?limit=100&page=2"
    dna, gara = tiles
    assert dna["title"] == "Dynafit DNA Pro Ski" and dna["href"] == "/dynafit-dna-pro-skis"
    assert (dna["price"], dna["compare_at"], dna["from"]) == (699.95, 1099.95, True)
    assert (gara["price"], gara["compare_at"]) == (1199.95, None)
    assert parse_listing(PAGE2)[1] is None


def test_brands_match_title_prefix():
    brands = parse_brands(BRANDS)
    assert brand_of("Ski Trab Gara World Cup Ski", brands) == "Ski Trab"
    assert brand_of("SCARPA F1 RS Boot", brands) == "SCARPA"
    assert brand_of("Dynafitter Liner", brands) is None   # 品牌名后面必须是词边界


def test_detail_option_prices_and_stock():
    opts = parse_detail(DETAIL_SKI)
    assert [(o["label"], o["price"], o["compare_at"], o["available"]) for o in opts] == [
        ("162cm", 1099.95, None, True), ("162 - 2024/25", 699.95, 1099.95, True), ("170cm", 1099.95, None, False)]
    jk = parse_detail(DETAIL_JACKET)
    assert [(o["color"], o["label"], o["price"], o["compare_at"], o["available"]) for o in jk] == [
        ("Relic", "S", 399.95, 699.95, True), ("Relic", "M", 399.95, 699.95, False), ("Black", "M", 399.95, 699.95, True)]


DETAIL_FLEX = """
<div class="options"><div class="option"><label class="product-label">Length &amp; Flex</label>
  <select name="option[3389]"><option value="">- Select</option>
    <option value="44795">169cm, Flex 70</option><option value="oos">- Out of Stock Options -</option>
  </select></div></div>
<script>var base_price = 1199.95; var base_special = 999.95; stock_map["44795"] = "In Stock";</script>"""


def test_detail_skips_separator_and_keeps_non_colour_pairs():
    assert parse_detail(DETAIL_FLEX) == [
        {"label": "169cm, Flex 70", "color": None, "price": 999.95, "compare_at": 1199.95, "available": True}]


def test_fetch_pages_dedupe_gender_and_details():
    f = FakeFetcher({"https://skimo.co/skis?limit=100": PAGE1,
                     "https://skimo.co/skis?limit=100&page=2": PAGE2,
                     "https://skimo.co/product/manufacturer": BRANDS,
                     "https://skimo.co/dynafit-dna-pro-skis": DETAIL_SKI,
                     "https://skimo.co/ski-trab-gara-world-cup-women": "<html></html>"})
    out = list(adapter("ski", {"pages": [{"path": "/skis", "gender": "unisex"}], "details": True}, f).fetch())
    assert [r.title for r in out] == ["Dynafit DNA Pro Ski", "Ski Trab Gara World Cup Ski - Women"]  # 雪扣被剔除
    dna, gara = out
    assert dna.vendor == "Dynafit" and gara.vendor == "Ski Trab"
    assert dna.gender_hint == "unisex" and dna.url == "https://skimo.co/dynafit-dna-pro-skis"
    # 同长度两个选项（新款原价 / 上季促销）合并成一个尺码，取有货的低价
    assert [(s.label, s.cm, s.available, s.price) for s in dna.sizes] == [
        ("162 - 2024/25", 162, True, 699.95), ("170cm", 170, False, 1099.95)]
    assert (dna.price, dna.compare_at, dna.available) == (699.95, 1099.95, True)
    assert gara.sizes == [] and gara.price == 1199.95   # 商品页没有尺码选项：保留列表价
    assert f.calls.count("https://skimo.co/skis?limit=100&page=2") == 1


def test_listing_only_when_details_off():
    f = FakeFetcher({"https://skimo.co/jackets?limit=100": page(
        TILE.format(id=1, slug="dynafit-tigard", title="Dynafit Tigard Gore Tex Pro Jacket", price=SALE)),
        "https://skimo.co/product/manufacturer": BRANDS})
    (jk,) = adapter("jacket", {"pages": [{"path": "/jackets", "gender": "men"}]}, f).fetch()
    assert (jk.price, jk.compare_at, jk.available, jk.sizes, jk.gender_hint) == (699.95, 1099.95, True, [], "men")
    assert len(f.calls) == 2
