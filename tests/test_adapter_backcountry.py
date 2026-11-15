"""测试 Backcountry / Steep & Cheap 适配器。"""
import json
import pytest

from skideals.adapters.backcountry import (
    BackcountryAdapter,
    extract_products_from_next_data,
    parse_next_data,
)

SAMPLE_NEXT_DATA_HTML = """
<!DOCTYPE html>
<html>
<head>
<script id="__NEXT_DATA__" type="application/json">
{
  "props": {
    "pageProps": {
      "plpData": {
        "products": [
          {
            "id": "BCC001A",
            "title": "Salomon QST 92 Skis 2026",
            "brand": {"name": "Salomon"},
            "url": "/salomon-qst-92-skis",
            "image": "https://content.backcountry.com/images/items/large/SAL/SAL001.jpg",
            "price": {
              "current": 449.95,
              "regular": 599.95
            },
            "variants": [
              {"size": "168cm", "inStock": true, "price": 449.95},
              {"size": "176cm", "inStock": true, "price": 449.95},
              {"size": "184cm", "inStock": false, "price": 449.95}
            ]
          }
        ]
      }
    }
  }
}
</script>
</head>
<body></body>
</html>
"""


def test_parse_next_data():
    data = parse_next_data(SAMPLE_NEXT_DATA_HTML)
    assert data is not None
    assert "props" in data


def test_extract_products_from_next_data():
    data = parse_next_data(SAMPLE_NEXT_DATA_HTML)
    products = extract_products_from_next_data(data, "https://www.backcountry.com", "ski")
    assert len(products) == 1
    p = products[0]
    assert p.title == "Salomon QST 92 Skis 2026"
    assert p.vendor == "Salomon"
    assert p.price == 449.95
    assert p.compare_at == 599.95
    assert len(p.sizes) == 3
    assert p.sizes[0].cm == 168
    assert p.sizes[0].available is True
    assert p.sizes[2].cm == 184
    assert p.sizes[2].available is False


class MockFetcher:
    def __init__(self, pages):
        self.pages = pages

    def get_html(self, url, **kw):
        return self.pages.get(url, "")


def test_backcountry_adapter_fetch():
    fetcher = MockFetcher({
        "https://www.backcountry.com/downhill-skis": SAMPLE_NEXT_DATA_HTML,
    })
    cfg = {
        "id": "backcountry",
        "url": "https://www.backcountry.com",
        "adapter": "backcountry",
        "category": "ski",
        "categories": ["/downhill-skis"],
        "max_pages": 1,
    }
    adapter = BackcountryAdapter(cfg, fetcher, log=lambda m: None)
    results = list(adapter.fetch())
    assert len(results) == 1
    assert results[0].retailer_id == "backcountry"
    assert results[0].title == "Salomon QST 92 Skis 2026"
