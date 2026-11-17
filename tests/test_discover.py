"""测试店铺自动发现功能（Shopify、WooCommerce、Schema.org）。"""
from unittest.mock import patch

from skideals.discover import discover


def test_discover_shopify():
    mock_cols = [
        ("mens-skis", 45),
        ("ski-bindings", 30),
        ("ski-boots", 25),
    ]
    with patch("skideals.discover.all_collections", return_value=mock_cols):
        res = discover("https://myshopifyshop.com")
        assert "[Shopify]" in res
        assert "ski" in res
        assert "collections" in res


def test_discover_woocommerce():
    mock_wc_cats = [
        ("skis", 50),
        ("bindings", 20),
    ]
    with patch("skideals.discover.all_collections", side_effect=Exception("not shopify")), \
         patch("skideals.discover.all_woocommerce_categories", return_value=mock_wc_cats):
        res = discover("https://mywooshop.com")
        assert "[WooCommerce]" in res
        assert "pages = " in res


def test_discover_schema_org():
    html_with_schema = """
    <html><head>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "Product", "name": "Atomic Skis"}
    </script>
    </head></html>
    """
    with patch("skideals.discover.all_collections", side_effect=Exception("not shopify")), \
         patch("skideals.discover.all_woocommerce_categories", return_value=[]), \
         patch("skideals.net.Fetcher.get_html", return_value=html_with_schema):
        res = discover("https://customshop.com")
        assert "[Schema.org]" in res


def test_woocommerce_pagination_normalizes_counts_and_skips_missing_slugs():
    from unittest.mock import Mock, call
    from skideals.discover import all_woocommerce_categories

    fetcher = Mock()
    fetcher.get_json.side_effect = [
        [{'slug': 'skis', 'count': '12'}, {'count': 5}, {'slug': 'boots', 'count': None}],
        [{'slug': 'bindings', 'count': 2}], [],
    ]
    assert all_woocommerce_categories(fetcher, 'https://shop.test/') == [('skis', 12), ('boots', 0), ('bindings', 2)]
    assert fetcher.get_json.call_args_list == [
        call('https://shop.test/wp-json/wc/store/v1/products/categories', params={'per_page': 100, 'page': page})
        for page in (1, 2, 3)
    ]


def test_discovery_prefers_shopify_without_probing_other_platforms():
    from unittest.mock import Mock
    fetcher = Mock()
    with patch('skideals.discover.Fetcher', return_value=fetcher), \
         patch('skideals.discover.all_collections', return_value=[('skis', 10)]), \
         patch('skideals.discover.all_woocommerce_categories') as wc:
        assert '[Shopify]' in discover('https://shop.test/')
    wc.assert_not_called()
    fetcher.get_html.assert_not_called()


def test_discovery_handles_all_platform_probes_failing():
    from unittest.mock import Mock
    fetcher = Mock()
    fetcher.get_html.side_effect = RuntimeError('offline')
    with patch('skideals.discover.Fetcher', return_value=fetcher), \
         patch('skideals.discover.all_collections', side_effect=RuntimeError('offline')), \
         patch('skideals.discover.all_woocommerce_categories', side_effect=RuntimeError('offline')):
        result = discover('https://shop.test/')
    assert "adapter = 'schema_org'" in result
    assert '[Schema.org]' not in result
    fetcher.get_html.assert_called_once_with('https://shop.test')
