"""非 Shopify 抓取器的解析函数（用裁剪过的真实页面结构做小样本）。"""
import json

import pytest

from skideals.adapters.marmot import search_data, style_of
from skideals.adapters.tactics import estimate_list_price, parse_tiles
from skideals.adapters.zappos import gender_of, parse_state
from skideals.net import BlockedError


def test_zappos_state_and_gender():
    html = '<html><script>window.__INITIAL_STATE__ = {"products": {"totalProductCount": 1, "list": [{"productId": "1"}]}};' \
           '</script></html>'
    assert parse_state(html)["products"]["totalProductCount"] == 1
    with pytest.raises(BlockedError):
        parse_state("<html>Access denied</html>")          # 没有商品数据 = 被拦截
    assert gender_of(["Boys", "Girls"]) == "kids"
    assert gender_of(["Men", "Women"]) == "unisex"
    assert gender_of(["Women"]) == "women"
    assert gender_of([]) is None


TACTICS_HTML = """
<link rel="next" href="/snowboard-jackets/page-2" />
<div class="browse-grid-item"><a href="/airblaster/beast-3l-jacket/flames"><img src="/a/1.jpg">
  <span class="browse-grid-item-brand">Airblaster</span> Beast 3L Jacket <span class="browse-grid-item-color">flames</span></a>
  <span class="browse-grid-item-sale-price">$258.95 <span class="browse-grid-item-discount">(30% off)</span></span></div>
<div class="browse-grid-item"><a href="/airblaster/beast-3l-jacket/black"><img src="/a/2.jpg">
  <span class="browse-grid-item-brand">Airblaster</span> Beast 3L Jacket <span class="browse-grid-item-color">black</span></a>
  <span class="browse-grid-item-price">$369.95</span></div>
"""


def test_tactics_tiles_and_list_price():
    tiles, nxt = parse_tiles(TACTICS_HTML)
    assert nxt == "/snowboard-jackets/page-2"
    assert [t["product"] for t in tiles] == ["/airblaster/beast-3l-jacket"] * 2      # 两个颜色 = 同一商品
    assert tiles[0]["brand"] == "Airblaster" and tiles[0]["name"] == "Beast 3L Jacket" and tiles[0]["color"] == "flames"
    assert tiles[0]["price"] == 258.95 and tiles[0]["pct"] == 30
    assert tiles[1]["price"] == 369.95 and tiles[1]["pct"] is None                  # 原价格子
    assert estimate_list_price(258.95, 30) == 369.95                                # 258.95 / 0.7 = 369.93 → 369.95
    assert estimate_list_price(100.0, None) is None


def test_marmot_search_data():
    state = {"__PRELOADED_STATE__": {"__reactQuery": {"queries": [
        {"queryKey": ["/commerce-sdk-react", "/categories"], "state": {"data": {}}},
        {"queryKey": ["/commerce-sdk-react", "/product-search", {"refine": ["cgid=x"]}],
         "state": {"data": {"total": 1, "hits": [{"productId": "SP_3185007_VG_Sale"}]}}}]}}}
    html = f'<script id="mobify-data" type="application/json">{json.dumps(state)}</script>'
    assert search_data(html)["hits"][0]["productId"] == "SP_3185007_VG_Sale"
    assert style_of("SP_3185007_VG_Sale") == style_of("SP_3185007_VG_List") == "3185007"   # 原价 / 促销库存是同一款
    with pytest.raises(BlockedError):
        search_data("<html></html>")
