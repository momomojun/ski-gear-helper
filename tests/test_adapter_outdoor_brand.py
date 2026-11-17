"""测试户外/滑雪大牌官网适配器。"""
from unittest.mock import patch
import pytest

from skideals.adapters.outdoor_brand import OutdoorBrandAdapter
from tests.test_adapter_schema_org import SAMPLE_GRAPH_HTML


class MockFetcher:
    def __init__(self, pages):
        self.pages = pages

    def get_html(self, url, **kw):
        return self.pages.get(url, "")


def test_outdoor_brand_adapter_list():
    fetcher = MockFetcher({
        "https://arcteryx.com/us/en/c/mens/skiing": SAMPLE_GRAPH_HTML,
    })
    cfg = {
        "id": "arcteryx",
        "name": "Arc'teryx",
        "brand": "Arc'teryx",
        "url": "https://arcteryx.com",
        "adapter": "outdoor_brand",
        "category": "ski",
        "categories": [{"path": "/us/en/c/mens/skiing", "gender": "men"}],
    }
    adapter = OutdoorBrandAdapter(cfg, fetcher, log=lambda m: None)
    results = list(adapter.fetch())
    assert len(results) == 1
    p = results[0]
    assert p.retailer_id == "arcteryx"
    assert p.gender_hint == "men"
    assert p.vendor == "Blizzard"  # JSON-LD 中声明的品牌优先，若无则使用 brand_name
