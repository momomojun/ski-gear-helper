"""Salesforce Commerce Cloud（SFRA）品牌官网：Rossignol 美国站（rossignol.com/us-en）、Dynastar-Lange 美国站
（dynastar-lange.com/us-en，Rossignol 集团）、Giro（giro.com，头盔 + 雪镜）。

在 sfcc.SFCCAdapter（Christy Sports 用的公共基类）之上只换“卡片解析 + 翻页”：配置解析（categories 分类页列表、
include / exclude）、列表页出错处理、详情页补尺码这些都沿用基类。

卡片：`div.product[data-pid]`
  - GA4 商品数据：Rossignol / Dynastar 在子元素 `.product-tile[data-product-data]`，Giro 在 `div.product[data-gtmdata]`：
    item_brand、item_category2（Men / Women / Unisex / Junior …= 性别）、item_category3/4（品类）、
    item_category5（"Fall Winter 26/27" = 雪季 → 标题末尾加 2027）、item_stock、price
  - 价格：`.price .sales .value[content]` = 现价（Rossignol 的 `.sales.discount-percentage` 是 “Save 40%”，要跳过），
    `.strike-through .value[content]` = 原价
  - Rossignol / Dynastar 卡片上直接有尺码：`.sizes-variations a.size-variation`，带 line-through = 缺货
  - Giro 卡片上有颜色色块：`span.swatch[data-color-sale-price / data-color-list-price]`（每个颜色的价格，取最便宜的颜色）；
    Giro 的列表里每个颜色还会再出一张 “颜色组” 卡片（pid = `VG-<款号>-<颜色码>`），按款号合并成一个商品
翻页：分类页自带 `?start=N&sz=M`。
  - Rossignol / Dynastar：网页上的分页链接带 `prefn1=marketOnline&prefv1=true`（只显示网上能买的），照抄；
    robots.txt 禁止 `*size=*`、`*html?dwvar_*`、`*search?q*`、购物车 / 账户，`start` / `sz` / `prefn1` 不受限制。
    一页默认 10 个，sz=100 一次取完。
  - Giro：robots.txt 禁止 `/*sz=*=*`（sz 后面还有别的参数）、`/*prefn*=*`、`/*srule=*`、`/*/search*`，
    所以 URL 写成 `?start=N&sz=60`（sz 放最后），不加筛选参数。
详情页一般不抓（max_detail_pages 默认 0）：卡片上的数据已经够用。

配置（config/categories.toml），categories（或 pages）里的路径相对于 retailers.toml 的 url：
  [rossignol.ski]
  categories = [{ path = "/sports/alpine-ski/skis" }]
  [giro.helmet]
  categories = [{ path = "/snow/helmets/" }, { path = "/kids/snow/helmets/", gender = "kids" }]
"""
from __future__ import annotations

import html as _html
import json
import re
from datetime import datetime
from functools import cached_property
from urllib.parse import urlsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser, LexborNode as Node

from .base import RawProduct, SizeOption, to_float
from .sfcc import (SFCCAdapter, _clean, _fold, _strip_query, compose_title, condition_from_title,
                   merge_genders, normalize_categories, reconcile_gender)

_GENDERS = {"men": "men", "man": "men", "mens": "men", "men's": "men", "women": "women", "woman": "women",
            "womens": "women", "women's": "women", "unisex": "unisex", "junior": "kids", "juniors": "kids",
            "junior unisex": "kids", "kids": "kids", "kid": "kids", "youth": "kids", "boy": "kids", "boys": "kids",
            "girl": "kids", "girls": "kids", "child": "kids", "children": "kids", "toddler": "kids"}
_SEASON_RE = re.compile(r"fall\s*winter\s*(\d{2})\s*/\s*(\d{2})", re.I)
_MONEY_RE = re.compile(r"\$\s*([\d,]+(?:\.\d{1,2})?)")
_VG_RE = re.compile(r"^VG-(.+)-[A-Za-z0-9]+$")    # Giro 的颜色组卡片：VG-200000001600000049-0520


def current_season() -> int:
    """当前雪季的结束年份（和 crawl.current_season 一样）：2026 年 10 月 → 2027。"""
    now = datetime.now()
    return now.year + 1 if now.month >= 7 else now.year


# ============================================================================ 卡片解析
def _attr_json(node: Node | None, name: str) -> dict:
    if node is None:
        return {}
    raw = node.attributes.get(name)
    if not raw:
        return {}
    try:
        data = json.loads(_html.unescape(raw))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _value(node: Node | None) -> float | None:
    if node is None:
        return None
    v = node.css_first(".value[content]")
    if v is not None:
        f = to_float(v.attributes.get("content"))
        if f:
            return f
    m = _MONEY_RE.search(node.text())
    return to_float(m.group(1)) if m else None


def _money_attr(s: str | None) -> float | None:
    m = _MONEY_RE.search(s or "")
    return to_float(m.group(1)) if m else None


def tile_prices(node: Node) -> tuple[float | None, float | None]:
    """(现价, 原价)。现价 = 第一个不是折扣百分比的 .sales；原价 = .strike-through 里的值。"""
    price_box = node.css_first(".price")
    if price_box is None:
        return None, None
    sale = None
    for s in price_box.css(".sales"):
        cls = s.attributes.get("class") or ""
        if "discount-percentage" in cls:
            continue
        # .sales 里面可能嵌着 <del>（原价）：去掉以后再取值
        inner = re.sub(r"<del\b.*?</del>", " ", s.html or "", flags=re.S | re.I)
        sale = _value(HTMLParser(inner).css_first(".sales") or HTMLParser(inner).body)
        if sale:
            break
    lst = _value(price_box.css_first(".strike-through"))
    return sale, lst


def parse_brand_tiles(html: str) -> list[dict]:
    """解析品牌官网的商品卡片（Rossignol / Dynastar-Lange / Giro 三种写法都兼容）。"""
    out, seen = [], set()
    for node in HTMLParser(html).css("div.product[data-pid]"):
        pid = (node.attributes.get("data-pid") or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        gtm = _attr_json(node, "data-gtmdata") or _attr_json(node.css_first("[data-product-data]"), "data-product-data")
        link = node.css_first(".pdp-link a") or node.css_first(".tile-header a") or node.css_first("a.link")
        sale, lst = tile_prices(node)
        # Giro：每个颜色的价格（色块上），取最便宜的颜色；原价取这个颜色的原价
        swatches = []
        for sw in node.css("[data-color-sale-price]"):
            sp = _money_attr(sw.attributes.get("data-color-sale-price"))
            if sp:
                swatches.append((sp, _money_attr(sw.attributes.get("data-color-list-price"))))
        if swatches:
            sp, lp = min(swatches, key=lambda x: x[0])
            if sale is None or sp < sale:
                sale, lst = sp, lp or (lst if lst and lst > sp else None)
        sizes = []
        for a in node.css(".sizes-variations a.size-variation, .sizes-variations .size-variation"):
            label = _clean(a.text())
            if label and label not in [s[0] for s in sizes]:
                cls = a.attributes.get("class") or ""
                sizes.append((label, not re.search(r"line-through|unavailable|disabled|unselectable", cls)))
        img = node.css_first("img.tile-image") or node.css_first("img")
        src = None
        if img is not None:
            for k in ("data-src", "src"):
                v = img.attributes.get(k) or ""
                if v and not v.startswith("data:"):
                    src = v
                    break
        season = _SEASON_RE.search(str(gtm.get("item_category5") or ""))
        vg = _VG_RE.match(pid)
        out.append({
            "pid": pid,
            "master": vg.group(1) if vg else pid,
            "name": _clean(link.text() if link is not None else "") or _clean(gtm.get("item_name")),
            "href": (link.attributes.get("href") or "") if link is not None else "",
            "sale": sale or to_float(gtm.get("price")),
            "list": lst,
            "sizes": sizes,
            "image": src,
            "brand": _clean(gtm.get("item_brand")) or None,
            "gender": _GENDERS.get(_clean(gtm.get("item_category2")).lower()),
            "ptype": " / ".join(_clean(gtm.get(k)) for k in ("item_category3", "item_category4") if gtm.get(k)) or None,
            "stock": _clean(gtm.get("item_stock")).lower() or None,
            "year": 2000 + int(season.group(2)) if season else None,
            "season": f"{season.group(1)}/{season.group(2)}" if season else None,
        })
    return out


def brand_name(raw: str | None, known: list[str]) -> str | None:
    """"ROSSIGNOL" → "Rossignol"（按配置里的品牌写法）；不认识的全大写品牌改成首字母大写（LOOK 例外，保持原样）。"""
    if not raw:
        return None
    for k in known:
        if _fold(k) == _fold(raw):
            return k
    return raw.title() if raw.isupper() and len(raw) > 4 else raw


# ============================================================================ 适配器
class SFCCBrandAdapter(SFCCAdapter):
    platform = "sfcc_brand"
    PAGE_SIZE = 100
    LIST_QUERY = ""                 # 加在分页参数前面的固定参数（Rossignol：prefn1=marketOnline&prefv1=true）
    BRANDS: list[str] = []          # 网站上会出现的品牌的标准写法
    MAX_PAGES = 10

    @cached_property
    def source_cfg(self) -> dict:
        out = super().source_cfg
        if self.cfg.get("pages") and not self.cfg.get("categories"):   # 也接受 pages（和其他适配器一致）
            out["categories"] = self.cfg["pages"]
        return out

    def _detail_limit(self) -> int:
        return int(self.source_cfg.get("max_detail_pages", 0))

    def _abs(self, href: str) -> str:
        if href.startswith(("http://", "https://")):
            return href
        if href.startswith("/"):                 # 网站给的链接已经带 /us-en 前缀
            p = urlsplit(self.cfg["url"])
            return f"{p.scheme}://{p.netloc}{href}"
        return f"{self.base_url}/{href}"

    def _page_url(self, path: str, start: int, size: int) -> str:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        q = (self.cfg.get("list_query", self.LIST_QUERY) or "").strip("&")
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}{q + '&' if q else ''}start={start}&sz={size}"     # sz 放最后（Giro 的 robots 规则）

    def _listing(self) -> list[RawProduct]:
        size = int(self.source_cfg.get("page_size") or self.PAGE_SIZE)
        max_pages = int(self.source_cfg.get("max_list_pages") or self.MAX_PAGES)
        found: dict[str, RawProduct] = {}
        genders: dict[str, set[str]] = {}
        for cat in self.categories:
            if self.stats["blocked"]:
                break
            start, seen = 0, set()
            for _ in range(max_pages):
                html = self._list_page(self._page_url(cat["path"], start, size), bool(found))
                if html is None:
                    break
                tiles = parse_brand_tiles(html)
                fresh = [t for t in tiles if t["pid"] not in seen]
                for t in fresh:
                    seen.add(t["pid"])
                    self._add(t, cat, found, genders)
                start += len(tiles)
                if not fresh or len(tiles) < size:
                    break
            models = len({_VG_RE.sub(r"\1", x) for x in seen})
            self.log(f"[{self.rid}/{self.category}] {cat['path']}: {len(seen)} tiles"
                     + (f" = {models} models" if models != len(seen) else ""))
        for pid, p in found.items():
            site = merge_genders(genders.get(pid, set())) or merge_genders(genders.get("item:" + pid, set()))
            p.gender_hint = reconcile_gender(site, p.title)
        return list(found.values())

    def _add(self, t: dict, cat: dict, found: dict, genders: dict) -> None:
        pid = t["master"]                         # 颜色组卡片并到款号上
        # 分类页给的性别优先（Dynastar 男款雪鞋页里 item_category2 写的是 Unisex），其次是商品数据里的 item_category2
        if cat.get("gender"):
            genders.setdefault(pid, set()).add(cat["gender"])
        elif t["gender"]:
            genders.setdefault("item:" + pid, set()).add(t["gender"])
        p = found.get(pid)
        if p is None:
            reason = self._filter_reason(t, cat)
            if reason:
                self._reject(pid, t["name"], reason)
                return
            p = found[pid] = self._tile_to_product(t, pid)
            self._rejected.pop(pid, None)
        elif t["sale"] and p.price and t["sale"] < p.price - 0.005:    # 某个颜色更便宜
            p.price = t["sale"]
            p.compare_at = t["list"] if (t["list"] and t["list"] > p.price + 0.005) else p.compare_at
            if p.compare_at and p.compare_at <= p.price + 0.005:
                p.compare_at = None
        if cat["path"] not in p.extra["categories"]:
            p.extra["categories"].append(cat["path"])
        if cat.get("type") and cat["type"] not in p.type_hints:
            p.type_hints.append(cat["type"])

    def _filter_reason(self, t: dict, cat: dict) -> str | None:
        text = " | ".join(x for x in (t["ptype"], t["name"]) if x)
        src = self.source_cfg
        for rx in (src.get("include"), cat.get("include")):
            if rx and not re.search(rx, text, re.I):
                return "include"
        for rx in (src.get("exclude"), cat.get("exclude")):
            m = re.search(rx, text, re.I) if rx else None
            if m:
                return "exclude:" + m.group(0).lower()
        if not t["sale"]:
            return "no-price"
        if not self.wanted(t["name"], t["ptype"]):
            return "not-wanted"
        return None

    def _tile_to_product(self, t: dict, pid: str | None = None) -> RawProduct:
        known = self.BRANDS or [x for x in (self.cfg.get("brand"), self.cfg.get("name")) if x]
        brand = brand_name(t["brand"], known) or (known[0] if known else None)
        # 只有当前及以前雪季的年份才加到标题里（有的新品已经标成下一季 "Fall Winter 27/28"）
        year = t["year"] if t["year"] and t["year"] <= current_season() else None
        title = compose_title(t["name"], brand, year)
        price, lst = t["sale"], t["list"]
        compare_at = lst if (lst and price and lst > price + 0.005) else None
        sizes = [SizeOption(label, self.size_cm(label), ok, price, compare_at) for label, ok in t["sizes"]]
        if sizes:
            available = any(s.available for s in sizes)
        elif t["stock"]:
            available = "in stock" in t["stock"] or t["stock"] in ("instock", "available")
        else:
            available = None
        return RawProduct(
            retailer_id=self.rid,
            external_id=pid or t["master"],
            url=_strip_query(self._abs(t["href"] or f"/{t['pid']}.html")),
            title=title,
            price=price,
            compare_at=compare_at,
            currency="USD",
            vendor=brand,
            product_type=t["ptype"],
            tags=[x for x in (t["ptype"], t["season"] and f"season {t['season']}") if x],
            image=t["image"],
            sizes=sizes,
            available=available,
            condition_hint=condition_from_title(title),
            category=self.category,
            extra={"season": t["season"], "categories": [], "site_gender": t["gender"]},
        )

    def _parse_detail(self, p: RawProduct, html: str) -> None:   # 只有配置了 max_detail_pages 才会用到
        self._sizes_from_swatches(p, html)


# ============================================================================ 各网站的默认分类页
def _pages(*paths, **extra) -> list[dict]:
    return [{"path": p, **extra} if isinstance(p, str) else {**p, **extra} for p in paths]


ROSSIGNOL_DEFAULTS: dict[str, dict] = {
    "ski": {"categories": _pages("/sports/alpine-ski/skis")},
    "boot": {"categories": _pages("/sports/alpine-ski/ski-boots")},
    "binding": {"categories": _pages("/sports/alpine-ski/look-ski-bindings")},
    "pole": {"categories": _pages("/sports/alpine-ski/poles")},
    "helmet": {"categories": _pages("/sports/alpine-ski/helmets-protections"), "exclude": r"protect|back ?pad|armou?r|\bvest\b"},
    "protection": {"categories": _pages("/sports/alpine-ski/helmets-protections"), "include": r"protect|back ?pad|armou?r|\bvest\b"},
    "goggle": {"categories": _pages("/sports/alpine-ski/goggles-lenses")},
}
DYNASTAR_LANGE_DEFAULTS: dict[str, dict] = {
    "ski": {"categories": _pages({"path": "/skis/men", "gender": "men"}, {"path": "/skis/women", "gender": "women"},
                                 {"path": "/skis/kids", "gender": "kids"})},
    "boot": {"categories": _pages({"path": "/ski-boots/men", "gender": "men"}, {"path": "/ski-boots/women", "gender": "women"},
                                  {"path": "/ski-boots/junior", "gender": "kids"})},
    "binding": {"categories": _pages("/bindings")},
    "pole": {"categories": _pages("/equipment/poles")},
    "skin": {"categories": _pages("/equipment/skins")},
}
GIRO_DEFAULTS: dict[str, dict] = {
    "helmet": {"categories": _pages("/snow/helmets/", {"path": "/kids/snow/helmets/", "gender": "kids"})},
    "goggle": {"categories": _pages("/snow/goggles/ski-and-snowboard/", "/snow/goggles/replacement-lenses/",
                                    {"path": "/kids/snow/goggles/", "gender": "kids"})},
}


class RossignolAdapter(SFCCBrandAdapter):
    CATEGORY_DEFAULTS = ROSSIGNOL_DEFAULTS
    LIST_QUERY = "prefn1=marketOnline&prefv1=true"
    BRANDS = ["Rossignol", "Look", "Dynastar", "Lange"]


class DynastarLangeAdapter(SFCCBrandAdapter):
    CATEGORY_DEFAULTS = DYNASTAR_LANGE_DEFAULTS
    LIST_QUERY = "prefn1=marketOnline&prefv1=true"
    BRANDS = ["Dynastar", "Lange", "Look", "Rossignol"]


class GiroAdapter(SFCCBrandAdapter):
    CATEGORY_DEFAULTS = GIRO_DEFAULTS
    PAGE_SIZE = 60                  # 网站默认每页 60；每个颜色另有一张卡片，头盔页约 450 张卡片 = 8 页
    MAX_PAGES = 15
    BRANDS = ["Giro"]


__all__ = ["SFCCBrandAdapter", "RossignolAdapter", "DynastarLangeAdapter", "GiroAdapter", "parse_brand_tiles",
           "tile_prices", "brand_name", "normalize_categories"]
