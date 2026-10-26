"""KÜHL 适配器：__NEXT_DATA__ 列表 + collection JSON-LD 库存、标题选取、商品页尺码 / 促销价、翻页停止。不联网。"""
import json

from skideals.adapters.kuhl import KuhlAdapter, parse_detail, parse_listing, pick_title


def listing(products, total_pages=1, page=1, offers=None):
    nd = {"props": {"pageProps": {"pageData": {"products": products, "total": len(products),
                                                "totalPages": total_pages, "page": page}}}}
    ld = {"@type": "CollectionPage", "mainEntity": {"@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "item": {"@type": "Product", "url": "https://www.kuhl.com" + u,
                                       "image": f"https://img/{u.strip('/').rsplit('/', 1)[-1]}.jpg",
                                       "offers": {"price": "1", "availability": f"https://schema.org/{a}"}}}
        for u, a in (offers or {}).items()]}}
    return (f'<script type="application/ld+json" id="collection-jsonld">{json.dumps(ld)}</script>'
            f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script>')


RYDR = {"n": "Rydr™", "d": "Rydr Pant", "u": "/kuhl/mens/pants/rydr-pant/", "m": "5016", "i": 279,
        "p": {"amount": 10900, "currency": "USD"}, "s": "Durable heavyweight everyday pant", "f": "Klassik",
        "c": [["#713d23", "Saddle Brown", True, ["pdp/5016/5016_rydr_pant_sadb_front"], True]]}
LAW = {"n": "Above The Law™", "d": "Above The Law", "u": "/kuhl/mens/pants/above-the-law/", "m": "5222", "i": 1783,
       "p": {"amount": 16900, "currency": "USD"}, "s": "Work pant", "f": "Full", "c": []}
PROMO = {"id": 12, "key": "category_marketing_item-12", "title": "REVOLVR", "link": "/kuhl/mens/pants/revolvr/"}
WLAW = {**LAW, "u": "/kuhl/womens/pants/above-the-law/", "m": "6222"}

DETAIL = {"props": {"pageProps": {"pageData": {"product": {
    "prices": [{"amount": 67500, "currency": "USD", "discountedAmount": 47250}],
    "sizes": ["S", "M", "L"], "waists": [], "inseams": [], "gender": "m",
    "colors": [{"text": "KUHL BLUE", "unavailableSizes": ["S", "M"]}, {"text": "RAVEN", "unavailableSizes": ["S"]}]}}}}}
PANT_DETAIL = {"props": {"pageProps": {"pageData": {"product": {
    "prices": [{"amount": 10900, "discountedAmount": None}], "sizes": [], "waists": ["30", "32"], "inseams": ["30", "32"],
    "colors": [{"text": "COFFEE", "unavailableWaists": ["30"]}, {"text": "CARBON", "unavailableWaists": ["30"]}]}}}}}


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def get_html(self, url, **kw):
        self.calls.append(url)
        return self.pages[url]


def test_pick_title_prefers_name_with_garment_noun():
    assert pick_title("Rydr<sup>™</sup>", "Rydr Pant") == "Rydr Pant"
    assert pick_title("The Defiant™ Shell", "Defiant") == "The Defiant Shell"
    assert pick_title("Above The Law™", "Above The Law") == "Above The Law"


def test_listing_skips_marketing_tiles_and_merges_jsonld():
    html = listing([RYDR, PROMO, LAW], offers={RYDR["u"]: "InStock", LAW["u"]: "OutOfStock"})
    items, pages = parse_listing(html)
    assert pages == 1 and [i["title"] for i in items] == ["Rydr Pant", "Above The Law"]
    rydr, law = items
    assert (rydr["price"], rydr["id"], rydr["available"], rydr["image"]) == (109.0, "5016", True, "https://img/rydr-pant.jpg")
    assert law["available"] is False


def test_detail_sizes_and_discount():
    d = parse_detail(f'<script id="__NEXT_DATA__">{json.dumps(DETAIL)}</script>')
    assert (d["price"], d["compare_at"]) == (472.5, 675.0)
    assert d["sizes"] == [("S", False), ("M", True), ("L", True)]   # 任一颜色有货就算有货
    p = parse_detail(f'<script id="__NEXT_DATA__">{json.dumps(PANT_DETAIL)}</script>')
    assert (p["price"], p["compare_at"], p["sizes"]) == (109.0, None, [("30", False), ("32", True)])


def test_fetch_pagination_gender_and_details():
    base = "https://www.kuhl.com"
    f = FakeFetcher({
        f"{base}/kuhl/mens/pants/": listing([RYDR], total_pages=2),
        f"{base}/kuhl/mens/pants/?page=2": listing([LAW], total_pages=2, page=2),
        f"{base}/kuhl/womens/pants/": listing([WLAW]),
        f"{base}{RYDR['u']}": f'<script id="__NEXT_DATA__">{json.dumps(PANT_DETAIL)}</script>',
    })
    cfg = {"id": "kuhl", "url": base, "category": "outdoor_pants", "details": True, "detail_limit": 1,
           "pages": [{"path": "/kuhl/mens/pants/", "gender": "men"}, {"path": "/kuhl/womens/pants/"}]}
    out = list(KuhlAdapter(cfg, f, log=lambda _: None).fetch())
    assert [(r.title, r.gender_hint, r.external_id) for r in out] == [
        ("Rydr Pant", "men", "5016"), ("Above The Law", "men", "5222"), ("Above The Law", "women", "6222")]
    rydr = out[0]
    assert rydr.url == base + RYDR["u"] and rydr.vendor == "KÜHL" and rydr.tags == ["Klassik"]
    assert [(s.label, s.available) for s in rydr.sizes] == [("30", False), ("32", True)]
    assert out[1].sizes == [] and out[1].available is True     # detail_limit = 1：其余只用列表数据
    assert len(f.calls) == 4                                   # 2 页男款 + 1 页女款 + 1 个商品页
