"""Collection fallback respects robots and the shortened Shopify page boundary."""
from unittest.mock import Mock, call

import pytest

from skideals.adapters.shopify import ShopifyAdapter
from skideals.net import BlockedError, FetchError, RobotsDisallowed


BASE = 'https://shop.test'
COLLECTION = BASE + '/collections/skis/products.json'
ROOT = BASE + '/products.json'


def adapter(fetcher):
    return ShopifyAdapter({'id': 'shop', 'url': BASE}, fetcher, log=lambda _: None)


@pytest.mark.parametrize('count', [0, 149, 150, 249])
def test_collection_page_boundary(count):
    products = [{'id': i} for i in range(count)]
    fetcher = Mock()
    fetcher.get_json.side_effect = [{'products': products}, {'products': []}]
    assert adapter(fetcher)._collection('skis') == products
    expected = [call(COLLECTION, params={'limit': 250, 'page': 1})]
    if count >= 150:
        expected.append(call(COLLECTION, params={'limit': 250, 'page': 2}))
    assert fetcher.get_json.call_args_list == expected


def test_robots_fallback_paginates_root_catalog():
    products = [{'id': i} for i in range(150)]
    tail = [{'id': 'last'}]
    fetcher = Mock()
    fetcher.get_json.side_effect = [RobotsDisallowed(COLLECTION), {'products': products}, {'products': tail}]
    assert adapter(fetcher)._collection('skis') == products + tail
    assert fetcher.get_json.call_args_list == [
        call(COLLECTION, params={'limit': 250, 'page': 1}),
        call(ROOT, params={'limit': 250, 'page': 1}),
        call(ROOT, params={'limit': 250, 'page': 2}),
    ]


@pytest.mark.parametrize('error', [BlockedError(COLLECTION, 403), FetchError(COLLECTION, 500)])
def test_non_robots_failure_propagates_without_root_fallback(error):
    fetcher = Mock()
    fetcher.get_json.side_effect = error
    with pytest.raises(type(error)) as exc:
        adapter(fetcher)._collection('skis')
    assert exc.value is error
    fetcher.get_json.assert_called_once_with(COLLECTION, params={'limit': 250, 'page': 1})
