"""Topo Athletic 官网（www.topoathletic.com，NetSuite SuiteCommerce；2022 年起属 Designer Brands / DSW）。

数据：前端自己调用的公开商品搜索接口（JSON，一次请求就是一个分类的全部款式）：
    /api/cacheable/items?c=3583691&commercecategoryurl=/men/mens-shoes&country=US&currency=USD&fieldset=search
        &include=facets&language=en&limit=100&matrixchilditems_fieldset=matrixchilditems_search&n=3&offset=0&pricelevel=5
  （n、pricelevel、matrixchilditems_fieldset 必须带，否则 400；女款 commercecategoryurl=/women/womens-shoes）
- items[] 每个是一个款式（itemid "M103"，所有颜色在一起）：storedisplayname2（"Ultraventure 5"）、custitem_gender
  （Men / Women / Unisex）、custitem_collection（Road / Trail / Recovery）、custitem_bestfor_activity
  （"Trail Running, Hiking"）、onlinecustomerprice_detail（现价）、custitem_sca_original_price（打折前原价，没打折为空）、
  urlcomponent（商品页路径）、itemimages_detail（按颜色的图片）
- matrixchilditems_detail[] 每个是一个 颜色 × 尺码 SKU：itemid "M103-070W-BLKDTE"（尺码代码 070 = 7.0，带 W = Wide），
  custitem_sca_shoe_colors（颜色）、isinstock、onlinecustomerprice_detail
- 男款列表里有护腿套（Switchback Gaiter，尺码 SML/MED/LRG），尺码不是数字的款式不收

分类：网站没有“越野跑 / 徒步”分类页（/activity/ 筛选页 robots 禁止），按 custitem_collection 和
custitem_bestfor_activity 在本地筛：越野跑鞋 = activities 含 "Trail Running"；徒步鞋 = 含 "Hiking" 且不含 "Trail Running"。

尺码码制：接口和商品页都没写 US（只有 "9.0" / "9.0 Wide"），所以默认不填 shoe_size_system；
cfg 里写了 shoe_size_system = "US" 才填。Wide 写进尺码 label；没写 Wide 的尺码楦宽未注明（不推断为 Regular）。

robots.txt：`User-agent: *` 禁止 */price/*、*/activity/*、*/commercecategory/*、*/search*、*page=* 等筛选路径；
/api/cacheable/items 允许，没有 Crawl-delay。

配置（config/categories.toml）：
  [topo.trail_shoe]
  pages = [{ path = "/men/mens-shoes", gender = "men" }, { path = "/women/womens-shoes", gender = "women" }]
  collections = ["Trail"]
  activities = ["Trail Running"]
"""
from __future__ import annotations

import html as htmllib
import re
from collections.abc import Iterator
from urllib.parse import urlencode

from ..net import BlockedError
from .base import Adapter, RawProduct, SizeOption, to_float

_LIMIT = 100
_MAX_PAGES = 10
_PARAMS = {"c": "3583691", "country": "US", "currency": "USD", "fieldset": "search", "include": "facets",
           "language": "en", "matrixchilditems_fieldset": "matrixchilditems_search", "n": "3", "pricelevel": "5"}
_CHILD = re.compile(r"^[A-Z]+\d+-(\d{3})(W?)-([A-Z0-9]+)$")
_GENDER = {"men": "men", "women": "women", "unisex": "unisex"}


def _clean(s) -> str:
    return " ".join(htmllib.unescape(str(s or "")).replace("\xa0", " ").split())


def _price(d: dict) -> float | None:
    return to_float((d.get("onlinecustomerprice_detail") or {}).get("onlinecustomerprice") or d.get("onlinecustomerprice")
                    or d.get("pricelevel5"))


def child_size(itemid: str) -> str | None:
    """"M103-070W-BLKDTE" → "7.0 Wide"；"U913-SML-BLKBLK"（护腿套）→ None。"""
    m = _CHILD.match(str(itemid or ""))
    if not m:
        return None
    n = int(m.group(1))
    if not 10 <= n <= 200:
        return None
    return f"{n // 10}.{n % 10}" + (" Wide" if m.group(2) else "")


def activities(item: dict) -> set[str]:
    return {a.strip() for a in _clean(item.get("custitem_bestfor_activity")).split(",") if a.strip()}


class TopoAdapter(Adapter):
    platform = "topo"

    def _items(self, category_url: str) -> Iterator[dict]:
        offset = 0
        for _ in range(_MAX_PAGES):
            params = {**_PARAMS, "commercecategoryurl": category_url, "limit": _LIMIT, "offset": offset}
            data = self.fetcher.get_json(f"{self.base_url}/api/cacheable/items?{urlencode(params)}")
            if not isinstance(data, dict) or "items" not in data:
                raise BlockedError(category_url, None, "接口没有返回 items（可能改版）")
            items = data.get("items") or []
            yield from items
            offset += len(items)
            if not items or offset >= int(data.get("total") or 0):
                break

    def keep(self, item: dict) -> bool:
        """按配置的 collections / activities / exclude_activities 在本地筛选越野跑鞋或徒步鞋。"""
        cols = self.cfg.get("collections")
        if cols and _clean(item.get("custitem_collection")) not in cols:
            return False
        acts = activities(item)
        want = set(self.cfg.get("activities") or [])
        if want and not acts & want:
            return False
        return not acts & set(self.cfg.get("exclude_activities") or [])

    def fetch(self) -> Iterator[RawProduct]:
        seen: set[str] = set()
        for spec in self.cfg.get("pages") or []:
            path = spec["path"] if isinstance(spec, dict) else spec
            gender = spec.get("gender") if isinstance(spec, dict) else None
            n = total = 0
            for item in self._items(path):
                total += 1
                iid = str(item.get("itemid") or item.get("internalid") or "")
                if not iid or iid in seen or not self.keep(item):
                    continue
                raw = self._to_raw(item, gender)
                if raw:
                    seen.add(iid)
                    n += 1
                    yield raw
            self.log(f"[{self.rid}] {self.category} {path}: {total} 个款式，收 {n} 个")

    def _to_raw(self, item: dict, page_gender: str | None) -> RawProduct | None:
        name = _clean(item.get("storedisplayname2") or item.get("displayname"))
        if not name:
            return None
        g = _GENDER.get(_clean(item.get("custitem_gender")).lower()) or page_gender
        title = f"{'Men' if g == 'men' else 'Women'}'s {name}" if g in ("men", "women") else name
        if not self.wanted(title, _clean(item.get("custitem_collection"))):
            return None
        url = f"{self.base_url}/{item.get('urlcomponent')}" if item.get("urlcomponent") else self.base_url
        original = to_float(_clean(item.get("custitem_sca_original_price")))
        # "SELECT COLOR ON MARKDOWN"：只有部分颜色降价时 original_price 为空，价格区间的上限就是原价
        span = [to_float(x) for x in _clean(item.get("onlinematrixpricerange")).split()]
        original = original or max((x for x in span if x), default=None)
        base_price = _price(item)
        sizes: list[SizeOption] = []
        variants = []
        for c in item.get("matrixchilditems_detail") or []:
            label = child_size(c.get("itemid"))
            if not label:
                continue
            p = _price(c) or base_price
            color = _clean(c.get("custitem_sca_shoe_colors")) or None
            avail = bool(c.get("isinstock")) and c.get("ispurchasable") is not False
            cmp_ = original if original and p and original > p + 0.5 else None
            sizes.append(SizeOption(label=label, cm=self.size_cm(label), available=avail, price=p,
                                    compare_at=cmp_, url=url, color=color))
            variants.append({"sku": c.get("itemid"), "label": label, "color": color, "available": avail, "price": p,
                             "width": "Wide" if label.endswith("Wide") else None})
        if not sizes:      # 护腿套等非鞋类（尺码 SML/MED/LRG）
            return None
        pool = [s for s in sizes if s.available and s.price] or [s for s in sizes if s.price]
        if not pool:
            return None
        best = min(pool, key=lambda s: s.price)
        colors = sorted({s.color for s in sizes if s.color})
        images = item.get("itemimages_detail") or {}
        default = _clean(item.get("custitem_sca_default_item_color"))
        img_set = images.get(default) or next((v for v in images.values() if isinstance(v, dict)), {})
        urls = (img_set or {}).get("urls") or []
        extra = {"style_id": item.get("itemid"), "colors": colors, "variants": variants,
                 "activities": sorted(activities(item)), "collection": _clean(item.get("custitem_collection")),
                 "final_sale": bool(item.get("custitem_sca_final_sale")),
                 "drop": _clean(item.get("custitem_sca_shoe_drop")) or None,
                 "cushioning": _clean(item.get("custitem_sca_cushioning")) or None}
        widths = {v["width"] for v in variants}
        if widths - {None}:
            extra["shoe_widths"] = sorted(widths - {None})
        if widths == {"Wide"}:
            extra["shoe_width"] = "Wide"
        if self.cfg.get("shoe_size_system"):
            extra["shoe_size_system"] = self.cfg["shoe_size_system"]
        promo = _clean(item.get("custitem_sca_promotion"))
        return RawProduct(
            retailer_id=self.rid,
            external_id=str(item.get("itemid")),
            url=url,
            title=title,
            price=best.price,
            compare_at=best.compare_at,
            currency="USD",
            vendor="Topo Athletic",
            product_type=" / ".join(x for x in (_clean(item.get("custitem_collection")),
                                                _clean(item.get("custitem_bestfor_activity"))) if x) or None,
            tags=[x for x in (promo, _clean(item.get("custitem_sca_item_badges"))) if x],
            image=urls[0].get("url") if urls and isinstance(urls[0], dict) else None,
            sizes=sizes,
            available=any(s.available for s in sizes),
            gender_hint=g,
            body_text=_clean(item.get("storedescription")) or None,
            category=self.category,
            extra=extra,
        )
