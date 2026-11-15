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
