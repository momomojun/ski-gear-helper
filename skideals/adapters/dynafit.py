"""Dynafit 美国官网（Oberalp / Salewa 集团，dynafit.com/en-us）：Nuxt 3 前端 + Algolia 商品搜索 + commercetools。

分类页（`/en-us/equipment/bindings`、`/en-us/men/footwear/ski-touring-boots` …）服务器渲染时把 Algolia 的搜索结果
放进了 `script#__NUXT_DATA__`（Nuxt 的 devalue 格式：一个扁平数组，对象 / 数组里存的是其他元素的下标）。
解开后在 `state["$sinstantsearch_ssr_results_"]["dynafit_prod_us_combined"].results[0]`：
`hits[]` 每条是“一个商品的一个颜色”（`groupHitVariantsBy` = 款号_色号），`variants[]` 是同款的各颜色，
字段：`name`、`key`（款号 08-0000049571）、`url`、`color`、`size`（只有主 SKU 的一个尺码，不是全部尺码）、
`isOutOfStock`、`images[]`、`subtitle`、`prices[]`（多个客户组的价格：**只用 customerGroupID 为空的公开价**，
其他是会员 / 专业折扣价；公开价 `regular` 原价、`discounted` 促销价、`isDiscounted`）。
分类页只列有货商品（Algolia 过滤 `isInStock:true`）。
**只有第一页（20 条）是服务器渲染的**，`?page=N` 不起作用（翻页在浏览器里直接查 Algolia，这里不调用）；
`nbHits` 比拿到的多时打一行日志，配置里可以拆成子分类页。目前（2026-10）只有固定器页是 23 条，其余都在 20 条以内。
页面很大（每页约 2.5 MB）。robots.txt：禁止 `/*search*`、checkout、account 和非美国站点，分类页允许。

配置（config/categories.toml）：
  [dynafit.binding]
  pages = [{ path = "/equipment/bindings" }]
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..net import BlockedError
from .base import Adapter, RawProduct, to_float

_INDEX_KEY = "$sinstantsearch_ssr_results_"
_WRAPPERS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef", "NuxtError"}


def devalue(arr: list):
    """Nuxt devalue 扁平数组 → 普通的 dict / list（只处理会遇到的类型；负数下标 = undefined / null 等）。"""
    memo: dict[int, object] = {}

    def r(i):
        if not isinstance(i, int) or isinstance(i, bool) or i < 0 or i >= len(arr):
            return None
        if i in memo:
            return memo[i]
        v = arr[i]
        if isinstance(v, dict):
            out: dict = {}
            memo[i] = out
            for k, x in v.items():
                out[k] = r(x)
            return out
        if isinstance(v, list):
            if v and isinstance(v[0], str):
                tag = v[0]
                if tag in _WRAPPERS:
                    memo[i] = r(v[1]) if len(v) > 1 else None
                    return memo[i]
                if tag == "Set":
                    return [r(x) for x in v[1:]]
                if tag in ("Map", "null"):
                    return {str(r(v[j])): r(v[j + 1]) for j in range(1, len(v) - 1, 2)} if tag == "Map" else None
                if tag in ("Date", "BigInt", "RegExp", "undefined"):
                    return v[1] if len(v) > 1 else None
            lst: list = []
            memo[i] = lst
            lst.extend(r(x) for x in v)
            return lst
        return v

    return r(0)


def search_result(html: str) -> dict:
    node = HTMLParser(html).css_first("script#__NUXT_DATA__")
    if node is None:
        raise BlockedError("dynafit", None, "页面里没有 __NUXT_DATA__（可能被拦截或改版）")
    root = devalue(json.loads(node.text())) or {}
    ssr = ((root.get("state") or {}).get(_INDEX_KEY)) or {}
    for idx in ssr.values():
        results = (idx or {}).get("results") or []
        if results and isinstance(results[0], dict) and "hits" in results[0]:
            return results[0]
    return {}


def public_price(prices: list | None) -> tuple[float | None, float | None]:
    """公开价（customerGroupID 为空）→ (现价, 原价或 None)。"""
    for p in prices or []:
        if isinstance(p, dict) and not p.get("customerGroupID"):
            regular = to_float(p.get("regular"))
            sale = to_float(p.get("discounted")) if p.get("isDiscounted") else None
            if sale and regular and sale < regular - 0.5:
                return sale, regular
            return regular, None
    return None, None


def parse_listing(html: str) -> tuple[list[dict], int]:
    """分类页 → 每个款号一条 {key, title, subtitle, variants: [...]} + Algolia 的总条数 nbHits。"""
    res = search_result(html)
    out: dict[str, dict] = {}
    for h in res.get("hits") or []:
        key = h.get("key") or h.get("productID")
        name = re.sub(r"\s+", " ", h.get("name") or "").strip()
        if not key or not name:
            continue
        cur = out.setdefault(key, {"key": key, "title": name, "subtitle": h.get("subtitle"), "variants": {}})
        for v in [h, *(h.get("variants") or [])]:
            if not isinstance(v, dict):
                continue
            price, compare = public_price(v.get("prices"))
            sku = v.get("sku") or v.get("id")
            if not price or not sku:
                continue
            imgs = [i.get("url") for i in v.get("images") or [] if isinstance(i, dict) and "type=video" not in (i.get("url") or "")]
            cur["variants"].setdefault(sku, {
                "sku": sku, "url": v.get("url") or h.get("url"), "color": v.get("color"), "size": v.get("size"),
                "price": price, "compare_at": compare, "available": not v.get("isOutOfStock"),
                "image": imgs[0] if imgs else None})
    items = [{**p, "variants": list(p["variants"].values())} for p in out.values() if p["variants"]]
    return items, int(res.get("nbHits") or 0)


class DynafitAdapter(Adapter):
    platform = "dynafit"

    @property
    def market(self) -> str:
        return self.cfg.get("market", "/en-us")

    def fetch(self) -> Iterator[RawProduct]:
        products: dict[str, dict] = {}
        for spec in self.cfg.get("pages") or []:
            path = spec["path"] if isinstance(spec, dict) else spec
            gender = spec.get("gender") if isinstance(spec, dict) else None
            items, total = parse_listing(self.fetcher.get_html(f"{self.base_url}{self.market}{path}"))
            hits = sum(1 for _ in items)
            for it in items:
                cur = products.setdefault(it["key"], {**it, "genders": set()})
                if cur is not it:
                    seen = {v["sku"] for v in cur["variants"]}
                    cur["variants"] += [v for v in it["variants"] if v["sku"] not in seen]
                if gender:
                    cur["genders"].add(gender)
            more = f"（网站共 {total} 条，服务器只渲染了第一页）" if total > 20 else ""
            self.log(f"[{self.rid}] {self.category} {path}: {hits} 个商品{more}")
        for key, p in products.items():
            if not self.wanted(p["title"]):
                continue
            yield self._to_raw(key, p)

    def _to_raw(self, key: str, p: dict) -> RawProduct:
        variants = p["variants"]
        live = [v for v in variants if v["available"]]
        best = min(live or variants, key=lambda v: v["price"])
        g = p["genders"]
        gender = "unisex" if {"men", "women"} <= g else next(iter(g)) if len(g) == 1 else None
        url = best["url"] or ""
        return RawProduct(
            retailer_id=self.rid,
            external_id=key,
            url=self.base_url + url if url.startswith("/") else url,
            title=p["title"],
            price=best["price"],
            compare_at=best["compare_at"],
            currency="USD",
            vendor="Dynafit",
            image=best.get("image"),
            available=bool(live),
            gender_hint=gender,
            body_text=p.get("subtitle"),
            extra={"colors": len({v["color"] for v in variants}), "color": best.get("color")},
            category=self.category,
        )
