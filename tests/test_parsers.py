"""HTML 解析（selectolax 的 lexbor 解析器）：各抓取器 / 价格.com / 网站可信度的解析函数。
页面片段按代码里用到的选择器手写成最小结构（不是真实网页的拷贝），保证换解析器、升级 selectolax 时解析结果不变。"""
from selectolax.lexbor import LexborHTMLParser

from skideals import trust
from skideals.adapters.peterglenn import PeterGlennAdapter
from skideals.adapters.sfcc import parse_size_swatches, parse_tiles
from skideals.adapters.skiscom import SkisComAdapter
from skideals.intl import kakaku

QUIET = lambda m: None  # noqa: E731

SFCC_TILES = """
<div class="product" data-pid="12345" data-isorderable="true">
  <script>var gtmdata = JSON.stringify({"name": "Atomic Bent 100 Skis", "brand": "Atomic"});</script>
  <img class="tile-image" src="/img/bent.jpg">
  <div class="pdp-link"><a class="link" href="/atomic-bent-100-skis/12345.html">Atomic Bent 100 Skis</a></div>
  <div class="season-attr">Season: 25 / 26</div>
  <div class="price"><del><span class="value" content="649.95">$649.95</span></del>
    <span class="sales"><span class="value" content="389.95">$389.95</span></span></div>
</div>
<div class="product" data-pid=" "></div>
"""

SFCC_SIZES = """
<div class="size-attributes"><span class="non-input-label">Select Binding Size: 90</span>
  <button class="size-attribute"><span data-attr-value="90"></span></button></div>
<div class="size-attributes"><span class="non-input-label">Select Ski Size:</span>
  <button class="size-attribute"><span data-attr-value="165" data-display-value="165 cm"></span></button>
  <button class="size-attribute unselectable"><span data-attr-value="172"></span></button></div>
"""


def test_sfcc_tiles():  # Christy Sports（Salesforce Commerce Cloud）
    tiles = parse_tiles(SFCC_TILES)
    assert len(tiles) == 1  # 没有 pid 的卡片跳过
    t = tiles[0]
    assert (t["pid"], t["name"], t["href"]) == ("12345", "Atomic Bent 100 Skis", "/atomic-bent-100-skis/12345.html")
    assert t["sales"] == [389.95] and t["lists"] == [649.95]
    assert t["image"] == "/img/bent.jpg" and t["orderable"] == "true"
    assert t["season"] == "25/26" and t["year"] == 2026
    assert t["gtm"] == {"name": "Atomic Bent 100 Skis", "brand": "Atomic"}


def test_sfcc_size_swatches():
    assert parse_size_swatches(SFCC_SIZES, "ski") == [("165 cm", True), ("172", False)]  # 双板跳过套装里的固定器尺码
    assert parse_size_swatches(SFCC_SIZES, "binding") == [("90", True)]  # 其他分类优先标签里带 Size 的那一组


OSCAR_ITEM = """
<div class="OSCAR_item">
  <a href="/product/equipment-ski-poles/12345/leki-carbon-14-3d-poles#reviews"><img class="image" src="/images/leki.jpg" alt="Leki Carbon 14 3D Poles"></a>
  <h3><a href="/product/equipment-ski-poles/12345/leki-carbon-14-3d-poles">Leki Carbon 14 3D Poles <span class="flag-sale">Sale</span></a></h3>
  <div class="OSCAR_price"><span id="ctl00_lblPriceReference">$129.95</span> <span id="ctl00_lblPrice">$89.95</span></div>
</div>
"""


def test_oscar_item():  # Skis.com（和 Buckman's 共用 OSCAR 平台的解析）
    a = SkisComAdapter({"id": "skiscom", "url": "https://www.skis.com", "category": "pole"}, None, QUIET)
    p = a._parse_item(LexborHTMLParser(OSCAR_ITEM).css_first("div.OSCAR_item"))
    assert p.external_id == "12345" and p.title == "Leki Carbon 14 3D Poles"  # 标题里的 Sale 标记拿出来放到 tags
    assert p.tags == ["Sale"] and p.price == 89.95 and p.compare_at == 129.95
    assert p.url == "https://www.skis.com/product/equipment-ski-poles/12345/leki-carbon-14-3d-poles"
    assert p.image == "https://www.skis.com/images/leki.jpg"


PG_CARD = """
<article class="card" data-entity-id="777" data-name="Leki Carbon 14 3D Ski Poles" data-product-price="79.99"
  data-product-brand="Leki" data-product-category="Ski/Ski Poles, Sale/Ski Sale">
  <figure class="card-figure" data-url="/leki-carbon-14-3d-ski-poles/"><img src="data:image/gif;base64,R0lGOD" data-src="/img/leki.jpg"></figure>
  <h3 class="card-title"><a href="/leki-carbon-14-3d-ski-poles/">Leki Carbon 14 3D Ski Poles</a></h3>
  <span data-product-rrp-price-without-tax>$99.99</span>
</article>
"""


def test_peterglenn_card():  # Peter Glenn（BigCommerce）
    a = PeterGlennAdapter({"id": "peterglenn", "url": "https://peterglenn.com", "category": "pole"}, None, QUIET)
    p = a._parse_card(LexborHTMLParser(PG_CARD).css_first("article.card"))
    assert p.external_id == "777" and p.price == 79.99 and p.compare_at == 99.99 and p.vendor == "Leki"
    assert p.url == "https://peterglenn.com/leki-carbon-14-3d-ski-poles/"
    assert p.image == "/img/leki.jpg"  # 懒加载占位图 data: → 用 data-src
    assert p.available and p.tags == ["Ski/Ski Poles", "Sale/Ski Sale"]


KAKAKU = """
<div class="p-resultItem"><a class="s-targetLink" href="https://kakaku.com/jump/?u=https%3A%2F%2Fitem.rakuten.co.jp%2Fshop%2Fbent100%2F&amp;x=1">
  <img data-src="https://img.example/x.jpg" alt="ATOMIC BENT 100"></a>
  <p class="p-item_name">ATOMIC アトミック BENT 100 25-26 スキー板</p><span class="p-item_priceNum">65,900円</span></div>
<div class="p-resultItem"><p class="p-item_name">価格なし</p></div>
"""


class _Fetcher:
    def __init__(self, pages):
        self.pages = pages

    def get_html(self, url, **kw):
        return self.pages.get(url, next(iter(self.pages.values())))

    def get(self, url, **kw):
        page = self.get_html(url)
        return type("Resp", (), {"content": page.encode(), "headers": {"content-type": "text/html; charset=utf-8"}, "url": url})()


def test_kakaku_results():  # 价格.com 搜索结果
    out = kakaku.search(_Fetcher({"any": KAKAKU}), "Atomic Bent 100")
    assert len(out) == 1  # 没有价格的结果跳过
    assert out[0]["price"] == 65900 and out[0]["title"] == "ATOMIC アトミック BENT 100 25-26 スキー板"
    assert out[0]["url"].startswith("https://item.rakuten.co.jp/shop/bent100") and out[0]["image"] == "https://img.example/x.jpg"


TRUST_HOME = """<html><head><title> Snow Shop | Skis &amp; Boots </title><script src="https://cdn.shopify.com/s/x.js"></script></head>
<body><a href="/pages/returns">Returns &amp; Exchanges</a><a href="/policies/shipping-policy">Shipping</a>
<a href="/pages/contact">Contact us</a><p>Email: help@snowshop.com</p></body></html>"""
TRUST_CONTACT = "<html><body><p>Call (802) 555-0123</p><p>123 Main Street, Burlington, VT 05401</p></body></html>"


def test_trust_page_evidence():  # 网站可信度：首页 + 联系我们页
    f = _Fetcher({"https://snowshop.com/": TRUST_HOME, "https://snowshop.com/pages/contact": TRUST_CONTACT})
    ev = trust.page_evidence(f, "https://snowshop.com/")
    assert ev["reachable"] and ev["title"] == "Snow Shop | Skis & Boots" and ev["platform"] == "Shopify"
    assert ev["policies"]["returns"] == "https://snowshop.com/pages/returns"
    assert ev["policies"]["shipping"] == "https://snowshop.com/policies/shipping-policy"
    assert ev["emails"] == ["help@snowshop.com"] and ev["phones"] == ["(802) 555-0123"]
    assert "123 Main Street" in ev["address"]
