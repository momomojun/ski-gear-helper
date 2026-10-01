"""Zappos（亚马逊旗下的大型鞋服零售商）：卖不少滑雪服装，而且有 The North Face、Columbia、Obermeyer、Spyder 这些
我们在别处抓不到的品牌（它们的官网有强反爬）。

数据：分类页（*.zso）的 HTML 里有一份 `window.__INITIAL_STATE__` JSON，`products.list[]` 就是商品列表
（品牌、名称、现价、原价、折扣、性别、库存数 onHand、各颜色的价格和库存），一页最多 100 个，翻页用 `?p=N`（从 0 开始）。
没有尺码信息（要进商品页才有），所以只知道有货 / 无货。
robots.txt：分类页允许；禁止的是站内搜索 `/search?term=`、`?plsr=` 等 —— 我们只用分类页。

配置（config/categories.toml）：
  [zappos.jacket]
  paths = ["/ski-and-snowboard-jackets/CKvXARDH1wEYu-wB4gIDAQID.zso"]   # 分类页路径（全部性别），可以写多个
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator

from ..net import BlockedError
from .base import Adapter, RawProduct, to_float

_STATE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*")
_MAX_PAGES = 20


def parse_state(html: str) -> dict:
    """页面里的 __INITIAL_STATE__；找不到就是被拦截了（反爬页 / 验证页）。"""
    m = _STATE.search(html or "")
    if not m:
        raise BlockedError("zappos", None, "页面里没有商品数据（可能被反爬拦截）")
    state, _ = json.JSONDecoder().raw_decode(html, m.end())
    return state


def gender_of(values: list[str] | None) -> str | None:
    g = {str(v).lower() for v in values or []}
    if g & {"boys", "girls", "kids", "baby"} and not g & {"men", "women"}:
        return "kids"
    if {"men", "women"} <= g:
        return "unisex"
    return "men" if "men" in g else "women" if "women" in g else None


class ZapposAdapter(Adapter):
    platform = "zappos"

    def fetch(self) -> Iterator[RawProduct]:
        seen: set[str] = set()
        for path in self.cfg.get("paths") or []:
            for page in range(_MAX_PAGES):
                url = self.base_url + path + (f"?p={page}" if page else "")
                state = parse_state(self.fetcher.get_html(url))
                prods = state.get("products") or {}
                items = prods.get("list") or []
                total = int(prods.get("totalProductCount") or 0)
                if page == 0:
                    self.log(f"[{self.rid}] {self.category} {path.split('/')[1]}: {total} 个商品")
                for it in items:
                    raw = self._to_raw(it)
                    if raw and raw.external_id not in seen:
                        seen.add(raw.external_id)
                        yield raw
                limit = int(prods.get("productLimit") or 100)
                if not items or (page + 1) * limit >= total:
                    break

    def _to_raw(self, it: dict) -> RawProduct | None:
        pid = str(it.get("productId") or "")
        brand = (it.get("brandName") or "").strip()
        name = (it.get("productName") or "").strip()
        if not pid or not name:
            return None
        title = f"{brand} {name}".strip()
        if not self.wanted(title, it.get("productType")):
            return None
        # 同一款的不同颜色价格可能不一样（某个颜色在打折）：取有货的颜色里最便宜的
        styles = [s for s in (it.get("relatedStyles") or []) if to_float(s.get("price"))] or [it]
        live = [s for s in styles if (s.get("onHand") or 0) > 0] or styles
        best = min(live, key=lambda s: to_float(s.get("price")) or 1e9)
        price = to_float(best.get("price"))
        if price is None:
            return None
        orig = to_float(best.get("originalPrice"))
        img = it.get("thumbnailImageUrl") or next(iter((it.get("imageMap") or {}).values()), None)
        return RawProduct(
            retailer_id=self.rid,
            external_id=pid,
            url="https://www.zappos.com" + (best.get("productUrl") or it.get("productUrl") or f"/p/product/{pid}"),
            title=title,
            price=price,
            compare_at=orig if orig and orig > price + 0.5 else None,
            currency="USD",
            vendor=brand or None,
            product_type=it.get("productType"),
            tags=[],
            image=img if isinstance(img, str) else None,
            available=any((s.get("onHand") or 0) > 0 for s in styles),
            gender_hint=gender_of(it.get("txAttrFacet_Gender")),
            category=self.category,
        )
