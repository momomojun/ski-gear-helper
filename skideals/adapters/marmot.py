"""Marmot 官网（Salesforce Commerce Cloud 的 PWA 前端，Newell Brands 旗下）。

数据：分类页 HTML 里的 `script#mobify-data` JSON → `__PRELOADED_STATE__.__reactQuery.queries[]` 里 queryKey 含
`/product-search` 的那条 → `state.data`（Salesforce 商品搜索结果）：`hits[]` 是“款式 × 价格类型”的变体组，
`SP_<款号>_VG_List`（原价库存）/ `SP_<款号>_VG_Sale`（促销库存），每个 hit 带各尺码 × 颜色的有货状态和价格。
页面很大（一页 2–4 MB），而 Marmot 的滑雪服装不多，所以只抓“滑雪”活动页（男 / 女，含雪服、雪裤、中间层，
由分类判定按标题分开），再抓 Sale 页 —— **只用来给滑雪页里已有的款式补上促销价**（Sale 页里的雨衣、城市羽绒服不收）。
翻页用 `?offset=N`，`?limit=100` 可以一次多取。robots.txt：禁止 /search?、/*Search，分类页 /c/ 允许。

配置（config/categories.toml）：
  [marmot.jacket]
  pages = [{ path = "/c/marmot-men-activity-skiing", gender = "men" }, { path = "/c/marmot-women-activity-skiing", gender = "women" }]
  sale_pages = ["/c/marmot-sale"]
"""
from __future__ import annotations

import json
from collections.abc import Iterator

from selectolax.parser import HTMLParser

from ..net import BlockedError, FetchError
from .base import Adapter, RawProduct, SizeOption, to_float

_PAGE = 100
_MAX_PAGES = 10


def search_data(html: str) -> dict:
    """页面里的商品搜索结果；没有就是被拦截或改版了。"""
    node = HTMLParser(html).css_first("script#mobify-data")
    if node is None:
        raise BlockedError("marmot", None, "页面里没有 mobify-data（可能被拦截或改版）")
    state = json.loads(node.text())
    for q in (state.get("__PRELOADED_STATE__") or {}).get("__reactQuery", {}).get("queries", []):
        if "/product-search" in json.dumps(q.get("queryKey")):
            return (q.get("state") or {}).get("data") or {}
    raise BlockedError("marmot", None, "页面里没有商品搜索结果")


def style_of(product_id: str) -> str | None:
    parts = str(product_id or "").split("_")
    return parts[1] if len(parts) >= 3 and parts[0] == "SP" else None


class MarmotAdapter(Adapter):
    platform = "marmot"

    def _pages(self, path: str, limit: int = _PAGE) -> Iterator[dict]:
        offset = 0
        for _ in range(_MAX_PAGES * 3):
            sep = "&" if "?" in path else "?"
            data = search_data(self.fetcher.get_html(f"{self.base_url}{path}{sep}limit={limit}" +
                                                     (f"&offset={offset}" if offset else "")))
            hits = data.get("hits") or []
            yield from hits
            offset += len(hits)
            if not hits or offset >= int(data.get("total") or 0):
                break

    def fetch(self) -> Iterator[RawProduct]:
        styles: dict[str, dict] = {}
        for spec in self.cfg.get("pages") or []:
            path = spec["path"] if isinstance(spec, dict) else spec
            gender = spec.get("gender") if isinstance(spec, dict) else None
            n = 0
            for h in self._pages(path):
                st = style_of(h.get("productId"))
                if not st:
                    continue
                n += 1
                info = styles.setdefault(st, {"hits": {}, "genders": set()})
                info["hits"][h["productId"]] = h
                if gender:
                    info["genders"].add(gender)
            self.log(f"[{self.rid}] {self.category} {path}: {n} 个款式")
        for path in self.cfg.get("sale_pages") or []:   # 只给已有款式补促销价
            n = 0
            try:
                # Sale 页商品多，一次要 100 个时对方服务器会 502：用小分页
                for h in self._pages(path, limit=24):
                    st = style_of(h.get("productId"))
                    if st in styles:
                        styles[st]["hits"][h["productId"]] = h
                        n += 1
            except FetchError as e:   # Sale 页失败不影响原价数据，只是这次没有促销价
                self.log(f"[{self.rid}] {path} 抓取失败（这次没有促销价）：{e}")
            self.log(f"[{self.rid}] {path}: {n} 个滑雪款式有促销价")
        for st, info in styles.items():
            raw = self._to_raw(st, info)
            if raw:
                yield raw

    def _to_raw(self, style: str, info: dict) -> RawProduct | None:
        hits = [h for h in info["hits"].values() if h.get("orderable") and to_float(h.get("price"))]
        if not hits:
            return None
        best = min(hits, key=lambda h: to_float(h.get("price")))
        title = (best.get("productName") or "").strip()
        if not title or not self.wanted(title):
            return None
        price = to_float(best.get("price"))
        lists = [to_float(h.get("c_listPrice")) for h in info["hits"].values() if to_float(h.get("c_listPrice"))]
        compare = max(lists) if lists else None
        sizes: dict[str, SizeOption] = {}
        for v in best.get("variants") or []:
            size = str((v.get("variationValues") or {}).get("size") or "").strip()
            if not size:
                continue
            p = to_float(v.get("price")) or price
            cur = sizes.get(size)
            ok = bool(v.get("orderable"))
            if cur is None:
                sizes[size] = SizeOption(label=size, cm=None, available=ok, price=p,
                                         compare_at=compare if compare and compare > p + 0.5 else None)
            else:
                cur.available = cur.available or ok
                if ok and p < (cur.price or 1e9):
                    cur.price = p
        g = info["genders"]
        gender = "unisex" if {"men", "women"} <= g else next(iter(g)) if len(g) == 1 else None
        rep = (best.get("representedProduct") or {}).get("id") or ""
        img = best.get("image") or {}
        return RawProduct(
            retailer_id=self.rid,
            external_id=style,
            url=f"{self.base_url}/p/{best.get('c_productUrlName')}/{style}/{rep}.html",
            title=title,
            price=price,
            compare_at=compare if compare and compare > price + 0.5 else None,
            currency=best.get("currency") or "USD",
            vendor="Marmot",
            product_type=best.get("c_categoryDisplayName"),
            tags=[x for x in (best.get("c_categoryPath"),) if isinstance(x, str)],
            image=img.get("link") if isinstance(img, dict) else None,
            sizes=list(sizes.values()),
            available=any(s.available for s in sizes.values()) if sizes else True,
            gender_hint=gender,
            body_text=best.get("c_descriptiveSubText"),
            category=self.category,
        )
