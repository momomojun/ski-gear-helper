"""测试通用 Schema.org JSON-LD 适配器。"""
import json
import pytest

from skideals.adapters.schema_org import (
    SchemaOrgAdapter,
    extract_cards_from_html,
    extract_json_ld,
    parse_product_json,
)
from skideals.models import RawProduct

SAMPLE_GRAPH_HTML = """
<!DOCTYPE html>
<html>
<head>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "WebPage",
      "@id": "https://example.com/skis/blizzard-rustler-9",
      "name": "Blizzard Rustler 9 Skis"
    },
    {
      "@type": "Product",
      "name": "Blizzard Rustler 9 Skis 2026",
      "sku": "BLZ-RUST9-26",
      "brand": {
        "@type": "Brand",
        "name": "Blizzard"
      },
      "image": "https://example.com/images/rustler9.jpg",
      "description": "All mountain skis with 96mm waist width",
      "offers": {
        "@type": "AggregateOffer",
        "lowPrice": "449.95",
        "highPrice": "699.95",
        "priceCurrency": "USD",
        "offers": [
          {
            "@type": "Offer",
            "name": "172 cm",
            "price": "449.95",
            "availability": "https://schema.org/InStock",
            "sku": "BLZ-RUST9-172"
          },
          {
            "@type": "Offer",
            "name": "180 cm",
            "price": "449.95",
            "availability": "https://schema.org/InStock",
            "sku": "BLZ-RUST9-180"
          },
          {
            "@type": "Offer",
            "name": "188 cm",
            "price": "499.95",
            "availability": "https://schema.org/OutOfStock",
            "sku": "BLZ-RUST9-188"
          }
        ]
      }
    }
  ]
}
</script>
</head>
<body></body>
</html>
"""

SAMPLE_SIMPLE_HTML = """
<html>
<head>
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "Product",
  "name": "Atomic Hawx Prime 120 S Ski Boots",
  "image": ["https://example.com/hawx.jpg"],
  "description": "Stiff 120 flex ski boot",
  "brand": "Atomic",
  "sku": "HAWX-120",
  "offers": {
    "@type": "Offer",
    "price": "549.99",
    "priceCurrency": "USD",
    "availability": "https://schema.org/InStock"
  }
}
</script>
</head>
<body></body>
</html>
"""


def test_extract_json_ld_graph():
    items = extract_json_ld(SAMPLE_GRAPH_HTML)
    assert len(items) == 2
    types = [it.get("@type") for it in items]
    assert "WebPage" in types
    assert "Product" in types


def test_parse_product_aggregate_offers():
    items = extract_json_ld(SAMPLE_GRAPH_HTML)
    prod = next(it for it in items if it.get("@type") == "Product")
    raw = parse_product_json(prod, base_url="https://example.com", category="ski")
    assert raw is not None
    assert raw.title == "Blizzard Rustler 9 Skis 2026"
    assert raw.vendor == "Blizzard"
    assert raw.price == 449.95
    assert raw.compare_at == 699.95
    assert raw.image == "https://example.com/images/rustler9.jpg"
    assert len(raw.sizes) == 3
    # Check 172cm size
    s172 = raw.sizes[0]
    assert s172.label == "172 cm"
    assert s172.cm == 172
    assert s172.available is True
    assert s172.price == 449.95
    # Check 188cm size (OutOfStock)
    s188 = raw.sizes[2]
    assert s188.available is False


def test_parse_product_simple():
    items = extract_json_ld(SAMPLE_SIMPLE_HTML)
    assert len(items) == 1
    raw = parse_product_json(items[0], base_url="https://example.com", category="boot")
    assert raw is not None
    assert raw.title == "Atomic Hawx Prime 120 S Ski Boots"
    assert raw.vendor == "Atomic"
    assert raw.price == 549.99
    assert raw.available is True
    assert raw.image == "https://example.com/hawx.jpg"


class MockFetcher:
    def __init__(self, pages: dict[str, str]):
        self.pages = pages

    def get_html(self, url: str, **kw):
        return self.pages.get(url, "")


def test_schema_org_adapter_fetch():
    fetcher = MockFetcher({
        "https://testshop.com/skis": SAMPLE_GRAPH_HTML,
    })
    cfg = {
        "id": "testshop",
        "url": "https://testshop.com",
        "adapter": "schema_org",
        "category": "ski",
        "pages": ["/skis"],
    }
    adapter = SchemaOrgAdapter(cfg, fetcher, log=lambda m: None)
    products = list(adapter.fetch())
    assert len(products) == 1
    assert products[0].title == "Blizzard Rustler 9 Skis 2026"
    assert products[0].retailer_id == "testshop"


def test_extract_cards_sportconrad():
    html = """
    <div class="product-card">
      <input name="bit-gtag-id" value="70905121-140">
      <input name="bit-gtag-name" value="Atomic Redster J2 130-150 + L 6 GW">
      <input name="bit-gtag-price" value="149">
      <input name="bit-gtag-brand" value="Atomic">
      <a href="/en/products/atomic-redster-j2">Link</a>
      <img src="https://example.com/redster.jpg">
      <div>Sale price: €149.00 MSRP €229.95</div>
    </div>
    """
    products = extract_cards_from_html(html, "https://www.sport-conrad.com", "ski", "sportconrad")
    assert len(products) == 1
    p = products[0]
    assert p.retailer_id == "sportconrad"
    assert p.external_id == "70905121-140"
    assert p.title == "Atomic Redster J2 130-150 + L 6 GW"
    assert p.vendor == "Atomic"
    assert p.price == 149.0
    assert p.compare_at == 229.95
    assert p.url == "https://www.sport-conrad.com/en/products/atomic-redster-j2"


def test_extract_cards_glisshop():
    html = """
    <span class="product-item">
      <a href="/skis/rossignol/arcade-8457058030" title="Rossignol Arcade 84">Link</a>
      <img data-lazy="https://example.com/arcade84.jpg" src="/Assets/placeholder.svg">
      <div>Rossignol</div>
      <div>Ski Arcade 84</div>
      <div class="product-price">
        <div>Recommended price £501.51</div>
        <div class="price-value">£316.71</div>
      </div>
      <ul class="product-axis">
        <li class="product-axis-item">152</li>
        <li class="product-axis-item">160</li>
      </ul>
    </span>
    """
    products = extract_cards_from_html(html, "https://www.glisshop.co.uk", "ski", "glisshop")
    assert len(products) == 1
    p = products[0]
    assert p.retailer_id == "glisshop"
    assert p.vendor == "Rossignol"
    assert "Arcade 84" in p.title
    assert p.price == 316.71
    assert p.compare_at == 501.51
    assert p.image == "https://example.com/arcade84.jpg"
    assert len(p.sizes) == 2
    assert p.sizes[0].cm == 152
    assert p.sizes[1].cm == 160


def test_parse_productgroup_hasvariant():
    data = {
        "@type": "ProductGroup",
        "name": "falketind Gore-Tex Jacket",
        "brand": {"@type": "Brand", "name": "Norrøna"},
        "url": "https://www.norrona.com/product/1834-24",
        "hasVariant": [
            {
                "@type": "Product",
                "sku": "1834-24-M",
                "size": "M",
                "color": "Olive",
                "image": "https://res.cloudinary.com/norrona/image/olive.jpg",
                "offers": {
                    "@type": "Offer",
                    "price": "499.00",
                    "availability": "https://schema.org/InStock",
                },
            },
            {
                "@type": "Product",
                "sku": "1834-24-L",
                "size": "L",
                "color": "Black",
                "image": "https://res.cloudinary.com/norrona/image/black.jpg",
                "offers": {
                    "@type": "Offer",
                    "price": "599.00",
                    "availability": "https://schema.org/InStock",
                },
            },
        ],
    }
    p = parse_product_json(data, base_url="https://www.norrona.com", category="jacket")
    assert p is not None
    assert p.title == "falketind Gore-Tex Jacket"
    assert p.vendor == "Norrøna"
    assert p.price == 499.00
    assert p.available is True
    assert p.image == "https://res.cloudinary.com/norrona/image/olive.jpg"
    assert len(p.sizes) == 2
    assert p.sizes[0].label == "M (Olive)"
    assert p.sizes[0].price == 499.00
    assert p.sizes[1].label == "L (Black)"
    assert p.sizes[1].price == 599.00


def test_parse_offers_item_offered():
    data = {
        "@type": "Product",
        "name": "Quasar Sling Bag",
        "sku": "10007193",
        "brand": {"@type": "Brand", "name": "Osprey"},
        "offers": {
            "@type": "Offer",
            "price": "75.00",
            "offers": [
                {
                    "@type": "Offer",
                    "price": 55.95,
                    "availability": "https://schema.org/InStock",
                    "itemOffered": {
                        "@type": "Product",
                        "size": "O/S",
                        "color": "Grey",
                    },
                },
                {
                    "@type": "Offer",
                    "price": 75.00,
                    "availability": "https://schema.org/InStock",
                    "itemOffered": {
                        "@type": "Product",
                        "size": "O/S",
                        "color": "Black",
                    },
                },
            ],
        },
    }
    p = parse_product_json(data, base_url="https://www.osprey.com", category="backpack")
    assert p is not None
    assert p.title == "Quasar Sling Bag"
    assert p.vendor == "Osprey"
    assert p.price == 55.95
    assert len(p.sizes) == 2
    assert p.sizes[0].label == "O/S (Grey)"
    assert p.sizes[0].price == 55.95
    assert p.sizes[1].label == "O/S (Black)"
    assert p.sizes[1].price == 75.00

