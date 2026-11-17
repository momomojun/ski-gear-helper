import json

import pytest

from skideals.adapters.corbetts import CorbettsAdapter, parse_product
from skideals.adapters.bobs import BobsAdapter, parse_cards


def option(variant, name, value, price, stock=1):
    return {'id': variant, 'option': name, 'value': value, 'calculated_price': price,
            'retail_price': 600, 'inventory_level': stock, 'purchasing_disabled': False,
            'product_option_id': name, 'option_value_id': value}


def product(options=None):
    return {'uid': '123', 'name': 'Atomic Bent 100 Skis 2026', 'url': '/atomic-bent/', 'price': '200',
            'msrp': '600', 'ss_in_stock': '1', 'child_sku_options': json.dumps(options or [])}


def test_corbetts_keeps_currency_and_excludes_optional_binding_bundles():
    options = [option(1, 'Ski Size', '180', 300), option(1, 'Select a Binding', 'Ski Only', 300),
               option(2, 'Ski Size', '180', 200), option(2, 'Select a Binding', 'Marker Griffon', 200),
               option(3, 'Ski Size', '172', 150, 0), option(3, 'Select a Binding', 'Ski Only', 150, 0)]
    p = parse_product(product(options), 'ski', 'CAD')
    assert p.currency == 'CAD' and p.price == 300
    assert p.compare_at == 600
    assert len(p.sizes) == 2 and p.sizes[1].available is False
    assert 'attribute%5B' in p.sizes[0].url


CONTEXT = '<main data-currency-code="CAD"></main><script src="https://snapui.searchspring.io/r4nj22/bundle.js">category = { path : "Clearance> Ski >Skis" };</script>'


class CorbettsFetcher:
    def get_html(self, url):
        return CONTEXT

    def get_json(self, url, params):
        assert params['bgfilter.categories_hierarchy'] == 'Clearance>Ski>Skis'
        p = product()
        p['uid'] = str(params['page'])
        return {'pagination': {'totalResults': 2, 'totalPages': 2}, 'results': [p]}


def test_corbetts_complete_only_after_all_declared_pages():
    cfg = {'id': 'corbetts', 'url': 'https://www.corbetts.com', 'pages': ['/sale'], 'currency': 'CAD'}
    a = CorbettsAdapter(cfg, CorbettsFetcher())
    assert len(list(a.fetch())) == 2 and a.complete
    a = CorbettsAdapter({**cfg, 'max_pages': 1}, CorbettsFetcher())
    assert len(list(a.fetch())) == 1 and not a.complete


def test_corbetts_currency_mismatch_not_silently_imported():
    a = CorbettsAdapter({'id': 'corbetts', 'url': 'https://www.corbetts.com', 'pages': ['/sale'], 'currency': 'USD'}, CorbettsFetcher(), log=lambda _: None)
    assert list(a.fetch()) == [] and a.fetch_errors


CARD = '<div class="ps-shoe"><a class="ps-shoe__name" href="/brand/skis-123">Atomic Bent 100 Skis</a><span class="ps-shoe__price"><del>$600.00</del>$300.00</span></div>'


def test_bobs_card_does_not_invent_stock_or_size():
    p = list(parse_cards(CARD, 'https://www.bobssportschalet.com', 'ski'))[0]
    assert p.price == 300 and p.compare_at == 600
    assert p.available is None and not p.sizes


def test_bobs_detail_uses_in_stock_offer_not_sold_out_cheapest():
    class F:
        def get_html(self, url):
            if url.endswith('/skis'):
                return CARD
            return '<script type="application/ld+json">' + json.dumps({'@type': 'Product', 'name': 'Atomic Skis',
                'offers': [{'@type': 'Offer', 'priceCurrency': 'USD', 'price': 100, 'availability': 'https://schema.org/OutOfStock'},
                           {'@type': 'Offer', 'priceCurrency': 'USD', 'price': 350, 'availability': 'https://schema.org/InStock'}]}) + '</script>'
    a = BobsAdapter({'id': 'bobssportschalet', 'url': 'https://www.bobssportschalet.com', 'pages': ['/skis']}, F())
    p = list(a.fetch())[0]
    assert p.price == 350 and p.available is True
    assert p.sizes == [] and not a.complete


@pytest.mark.parametrize('missing', ['uid', 'name', 'url', 'price'])
def test_corbetts_rejects_incomplete_catalog_entries(missing):
    data = product()
    del data[missing]
    assert parse_product(data, 'ski', 'CAD') is None


@pytest.mark.parametrize('disabled', [True, 'true'])
def test_corbetts_disabled_variant_cannot_supply_live_price(disabled):
    cheap = option(1, 'Ski Size', '172', 100)
    cheap['purchasing_disabled'] = disabled
    raw = parse_product(product([cheap, option(2, 'Ski Size', '180', 300)]), 'ski', 'CAD')
    assert raw.price == 300 and raw.available is True
    assert [size.available for size in raw.sizes] == [False, True]
    assert [size.cm for size in raw.sizes] == [172, 180]


def test_corbetts_all_sold_out_uses_cheapest_variant_without_claiming_stock():
    raw = parse_product(product([option(1, 'Ski Size', '172', 100, 0),
                                 option(2, 'Ski Size', '180', 300, 0)]), 'ski', 'CAD')
    assert raw.price == 100 and raw.available is False
    assert len(raw.sizes) == 2 and not any(size.available for size in raw.sizes)


@pytest.mark.parametrize('markup', [
    '<div class="ps-shoe"><span class="ps-shoe__price">$300.00</span></div>',
    '<div class="ps-shoe"><a class="ps-shoe__name" href="/ski-1">Atomic Skis</a></div>',
    CARD.replace('$300.00', '$200.00 - $300.00'),
    CARD.replace('$300.00', 'Call for price'),
])
def test_bobs_skips_missing_or_ambiguous_card_quotes(markup):
    assert list(parse_cards(markup, 'https://shop.test', 'ski')) == []


def test_bobs_detail_budget_keeps_card_quote_and_avoids_detail_request():
    from unittest.mock import Mock
    fetcher = Mock()
    fetcher.get_html.return_value = CARD
    cfg = {'id': 'bobssportschalet', 'url': 'https://shop.test',
           'pages': ['/skis', '/sale'], 'max_detail_pages': 0}
    adapter = BobsAdapter(cfg, fetcher, log=lambda _: None)
    products = list(adapter.fetch())
    assert len(products) == 1
    assert products[0].price == 300 and products[0].available is None
    assert not adapter.complete
    assert [c.args[0] for c in fetcher.get_html.call_args_list] == ['https://shop.test/skis', 'https://shop.test/sale']
    assert not adapter.fetch_errors
