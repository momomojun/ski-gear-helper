"""SCHEELS HTML discovery, variant pricing and failure semantics; no network."""
import json

import pytest

from skideals.adapters.scheels import ScheelsAdapter, product_groups
from skideals.net import FetchError, RobotsDisallowed

BASE = "https://www.scheels.com"
DEFAULT = BASE + "/p/15171-40246/"


def variant(sku, price, size="XLarge", color="Black", available="InStock", currency="USD"):
    return {"@type": "Product", "sku": sku, "size": size, "color": color,
            "offers": {"@type": "Offer", "price": price, "priceCurrency": currency,
                       "availability": f"https://schema.org/{available}",
                       "url": f"https://scheels.com/p/{sku}/"}}


def group(*variants, style="15171-40246"):
    return {"@type": "ProductGroup", "@id": BASE + f"/p/{style}/",
            "productGroupId": style, "name": "Men's Patagonia R1 Air 1/4 Zip Fleece Pullover",
            "brand": {"@type": "Brand", "name": "Patagonia"},
            "description": "Breathable fleece.",
            "offers": {"price": 145, "priceCurrency": "USD", "url": DEFAULT},
            "hasVariant": list(variants)}


def html(data):
    return '<script type="application/ld+json">' + json.dumps(data) + '</script>'


class FakeFetcher:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def get_html(self, url):
        self.calls.append(url)
        result = self.responses[url]
        if isinstance(result, Exception):
            raise result
        return result


def adapter(fetcher=None, **cfg):
    return ScheelsAdapter({"id": "scheels", "url": BASE, "currency": "USD",
                           "category": "midlayer", **cfg}, fetcher, log=lambda _: None)


def test_sale_colour_purchase_url_and_per_size_minimum():
    data = group(variant("black", 145), variant("blue", 100.99, color="Blue Sag"),
                 variant("medium", 145, size="Medium"))
    # Defensive: the variant incorrectly repeats the default URL.
    data["hasVariant"][1]["offers"]["url"] = DEFAULT
    raw = adapter()._to_raw(product_groups(html(data))[0], DEFAULT)
    assert raw.price == 100.99
    assert raw.url == BASE + "/p/blue/"
    assert raw.extra["selected_color"] == "Blue Sag"
    assert raw.external_id == "15171-40246" and raw.vendor == "Patagonia"
    assert {s.label: s.price for s in raw.sizes} == {"XL": 100.99, "M": 145}
    assert raw.extra["size_urls"]["XL"] == raw.url
    assert len(raw.extra["variants"]) == 3
    assert raw.compare_at is None  # The black colour's $145 is not an MSRP.


def test_sold_out_lower_prices_do_not_participate_in_in_stock_minimum():
    data = group(variant("sold", 10, available="OutOfStock"), variant("black", 145),
                 variant("blue", 100.99, color="Blue Sag"),
                 variant("sold-small", 5, size="Small", available="SoldOut"))
    raw = adapter()._to_raw(data, DEFAULT)
    sizes = {s.label: s for s in raw.sizes}
    assert raw.price == 100.99 and raw.url.endswith("/blue/") and raw.available
    assert sizes["XL"].price == 100.99 and sizes["XL"].available
    assert sizes["S"].price == 5 and not sizes["S"].available


def test_all_sold_out_is_explicitly_unavailable():
    raw = adapter()._to_raw(group(variant("sold", 80, available="OutOfStock")), DEFAULT)
    assert raw.price == 80 and raw.available is False
    assert raw.sizes[0].available is False


def test_foreign_cheaper_offer_cannot_win():
    raw = adapter()._to_raw(group(variant("cad", 1, currency="CAD"), variant("usd", 145)), DEFAULT)
    assert raw.price == 145 and raw.currency == "USD" and raw.url.endswith("/usd/")
    assert len(raw.extra["variants"]) == 1


@pytest.mark.parametrize("currency", ["CAD", "EUR", ""])
def test_no_matching_currency_is_error(currency):
    with pytest.raises(FetchError, match="No usable USD"):
        adapter()._to_raw(group(variant("foreign", 100, currency=currency)), DEFAULT)


@pytest.mark.parametrize("body", ["<html>Just a moment</html>", html({"@type": "Product"}),
                                 html(group()), '<script type="application/ld+json">broken</script>'])
def test_no_product_data_is_error(body):
    with pytest.raises(FetchError, match="No ProductGroup"):
        list(adapter(FakeFetcher({DEFAULT: body}), product_urls=[DEFAULT]).fetch())


def test_graph_array_and_multiple_offers():
    data = group(variant("blue", 145))
    data["hasVariant"][0]["size"] = {"name": "XL"}
    data["hasVariant"][0]["offers"] = [data["hasVariant"][0]["offers"],
                                      variant("blue", 100.99)["offers"]]
    body = '<script type="application/ld+json">broken</script>' + html(
        [{"@type": "BreadcrumbList"}, {"@graph": [data]}])
    raw = adapter()._to_raw(product_groups(body)[0], DEFAULT)
    assert raw.price == 100.99 and raw.sizes[0].label == "XL"


def test_explicit_same_currency_list_price_only():
    data = group(variant("blue", 100.99))
    data["hasVariant"][0]["offers"]["priceSpecification"] = [
        {"priceType": "https://schema.org/ListPrice", "priceCurrency": "USD", "price": 145},
        {"priceType": "https://schema.org/ListPrice", "priceCurrency": "CAD", "price": 999}]
    raw = adapter()._to_raw(data, DEFAULT)
    assert raw.compare_at == 145 and raw.sizes[0].compare_at == 145


def test_directory_discovery_budget_seed_priority_and_group_deduplication():
    page = BASE + "/c/fleece-jackets/"
    seed = BASE + "/p/blue/"
    other = BASE + "/p/other/"
    third = BASE + "/p/third/"
    listing = ('<a href="/p/black/">same group</a><a href="/p/other/">other</a>'
               '<a href="https://bad.example/p/external/">bad</a>'
               '<a href="/api/products">api</a><a href="/search?q=fleece">search</a>'
               '<a href="/p/variation/?prefn1=color">filter</a>'
               '<a href="/p/Product-Variation">variation</a>'
               '<a href="/c/another-category">no category recursion</a>'
               '<a href="/p/third/">beyond budget</a>')
    fetcher = FakeFetcher({page: listing, seed: html(group(variant("blue", 100.99), variant("black", 145))),
                           other: html(group(variant("other", 165), style="15171-40275"))})
    rows = list(adapter(fetcher, pages=[{"path": "/c/fleece-jackets", "gender": "men"}],
                        product_urls=[seed], max_detail_pages=2).fetch())
    assert fetcher.calls == [page, seed, other]
    assert len(rows) == 2 and rows[1].gender_hint == "men"
    assert third not in fetcher.calls


def test_directory_itemlist_and_listing_budget():
    page = BASE + "/c/fleece-jackets/"
    body = html({"@graph": [{"@type": "ItemList", "itemListElement": [
        {"@type": "ListItem", "item": {"@type": "Product", "url": DEFAULT}},
        {"@type": "ListItem", "item": DEFAULT}]}]})
    fetcher = FakeFetcher({page: body, DEFAULT: html(group(variant("blue", 100.99)))})
    rows = list(adapter(fetcher, pages=["/c/fleece-jackets", "/c/jackets-and-coats"],
                        max_listing_pages=1).fetch())
    assert len(rows) == 1 and fetcher.calls == [page, DEFAULT]


def test_empty_directory_can_use_explicit_known_products():
    page = BASE + "/c/fleece-jackets/"
    urls = [BASE + f"/p/15171-{style}/" for style in (40246, 40275, 40245)]
    fetcher = FakeFetcher({page: "<html>client rendered directory</html>", **{
        url: html(group(variant(f"sku-{i}", 100 + i), style=f"15171-{style}"))
        for i, (url, style) in enumerate(zip(urls, (40246, 40275, 40245)))}})
    assert len(list(adapter(fetcher, pages=["/c/fleece-jackets"], product_urls=urls).fetch())) == 3
    assert fetcher.calls == [page] + urls


def test_empty_directory_without_seeds_is_error():
    page = BASE + "/c/fleece-jackets/"
    with pytest.raises(FetchError, match="No product URLs"):
        list(adapter(FakeFetcher({page: "<html></html>"}), pages=["/c/fleece-jackets"]).fetch())


def test_robots_denial_propagates_without_alternative_requests():
    fetcher = FakeFetcher({DEFAULT: RobotsDisallowed(DEFAULT, msg="robots.txt disallows")})
    with pytest.raises(RobotsDisallowed):
        list(adapter(fetcher, product_urls=[DEFAULT]).fetch())
    assert fetcher.calls == [DEFAULT]


@pytest.mark.parametrize("url", ["https://other.example/p/1/", "/api/products", "/search", "/p/1/?sz=50"])
def test_invalid_config_urls_are_rejected_without_requests(url):
    fetcher = FakeFetcher({})
    with pytest.raises(FetchError, match="public same-store"):
        list(adapter(fetcher, product_urls=[url]).fetch())
    assert fetcher.calls == []


@pytest.mark.parametrize("price", [0, -1, "NaN", "Infinity"])
def test_invalid_price_is_error(price):
    with pytest.raises(FetchError, match="No usable USD"):
        adapter()._to_raw(group(variant("bad", price)), DEFAULT)


def test_long_size_aliases_share_the_same_price_bucket():
    data = group(variant("black", 145, size="XXL"),
                 variant("blue", 100.99, size="2XLarge", color="Blue Sag"))
    raw = adapter()._to_raw(data, DEFAULT)
    assert [(s.label, s.price) for s in raw.sizes] == [("XXL", 100.99)]


def test_missing_seed_is_logged_without_losing_valid_products():
    missing = BASE + "/p/15171-40245/"
    fetcher = FakeFetcher({missing: FetchError(missing, 404),
                           DEFAULT: html(group(variant("blue", 100.99)))})
    rows = list(adapter(fetcher, product_urls=[missing, DEFAULT]).fetch())
    assert len(rows) == 1 and fetcher.calls == [missing, DEFAULT]


def test_all_missing_seeds_is_error():
    fetcher = FakeFetcher({DEFAULT: FetchError(DEFAULT, 404)})
    with pytest.raises(FetchError, match="No product data"):
        list(adapter(fetcher, product_urls=[DEFAULT]).fetch())


def test_zero_detail_budget_is_error_without_detail_request():
    fetcher = FakeFetcher({})
    with pytest.raises(FetchError, match="No product data"):
        list(adapter(fetcher, product_urls=[DEFAULT], max_detail_pages=0).fetch())
    assert fetcher.calls == []
