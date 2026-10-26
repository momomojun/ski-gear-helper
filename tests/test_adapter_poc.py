"""POC 适配器：RSC 数据流里的 Centra 商品（颜色款合并、只取有货最低价、原价）、JSON-LD 后备。不联网。"""
import json

from skideals.adapters.poc import PocAdapter, parse_jsonld, parse_listing


def variant(uri, price, before=None, available=True, color="black"):
    return {"uri": uri, "name": "x", "relation": "variant", "available": available, "price": f"{price},00 USD",
            "priceAsNumber": price, "priceBeforeDiscountAsNumber": before or price, "color_swatch": {"color_name": color},
            "media": [{"url": f"https://poc.centracdn.net/{uri}.jpg"}],
            "items": [{"name": "M-L", "stock": "yes" if available else "no"}]}


FORNIX = {**variant("fornix-mips-bronzite-brown-matt", 230), "relation": "product", "centraProduct": "PC10573",
          "name": "Fornix Mips Ski Helmet", "englishName": "Fornix Mips Ski Helmet", "fit_type_fit_type": "Unifit",
          "relatedVariants": [variant("fornix-mips-white", 161, 230, available=False, color="white"),
                              variant("fornix-mips-green", 184, 230, color="green")]}
CALYX = {**variant("calyx-uranium-black-matt", 280), "relation": "product", "centraProduct": "PC10580",
         "name": "Calyx Helmet", "relatedVariants": []}


def rsc_page(*products) -> str:
    """模拟 Next.js 的 RSC 推流：数据按行 `<id>:<JSON>` 切成几段 push。"""
    stream = '0:["$","div",null,{}]\n' + "".join(
        f'{i + 1:x}:["$","$L1",null,{{"products":{json.dumps([p])}}}]\n' for i, p in enumerate(products))
    half = len(stream) // 2
    return "".join(f'<script>self.__next_f.push({json.dumps([1, part])})</script>'
                   for part in (stream[:half], stream[half:]))


LD = {"@type": "ItemList", "itemListElement": [{"@type": "ListItem", "item": {
    "@type": "Product", "name": "Arcus Ski Helmet", "url": "https://poc.com/en-us/product/arcus-granite-grey-matt",
    "image": "https://poc-images/arcus.jpg",
    "offers": {"@type": "Offer", "price": 300, "priceCurrency": "USD", "availability": "https://schema.org/InStock"}}}]}


def test_rsc_products_merge_colours():
    (fornix, calyx) = parse_listing(rsc_page(FORNIX, CALYX))
    assert fornix["id"] == "PC10573" and len(fornix["variants"]) == 3
    white = next(v for v in fornix["variants"] if v["color"] == "white")
    assert (white["price"], white["compare_at"], white["available"]) == (161.0, 230.0, False)
    assert calyx["title"] == "Calyx Helmet"


def test_jsonld_fallback():
    html = f'<script type="application/ld+json" id="item-list-ld-json">{json.dumps(LD)}</script>'
    assert parse_listing(html) == parse_jsonld(html)
    (arcus,) = parse_jsonld(html)
    assert arcus["variants"][0]["uri"] == "arcus-granite-grey-matt" and arcus["variants"][0]["price"] == 300


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages

    def get_html(self, url, **kw):
        return self.pages[url]


def test_fetch_cheapest_available_colour():
    base = "https://poc.com"
    f = FakeFetcher({f"{base}/en-us/categories/snow/all-mountain/helmets": rsc_page(FORNIX, CALYX),
                     f"{base}/en-us/categories/snow/all-mountain/junior": rsc_page(FORNIX)})
    cfg = {"id": "poc", "url": base, "category": "helmet",
           "pages": [{"path": "/categories/snow/all-mountain/helmets"},
                     {"path": "/categories/snow/all-mountain/junior", "gender": "kids"}]}
    fornix, calyx = PocAdapter(cfg, f, log=lambda _: None).fetch()
    # 白色 $161 已售罄 → 取有货的绿色 $184（原价 $230）
    assert (fornix.price, fornix.compare_at, fornix.available) == (184.0, 230.0, True)
    assert fornix.url == f"{base}/en-us/product/fornix-mips-green" and fornix.vendor == "POC"
    assert fornix.gender_hint == "kids" and fornix.tags == ["Unifit"]
    assert (calyx.price, calyx.compare_at, calyx.gender_hint) == (280.0, None, None)
