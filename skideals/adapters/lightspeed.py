"""通用 Lightspeed eCom（C-Series，原 SEOshop；图片在 cdn.shoplightspeed.com）适配器。

Lightspeed eCom 的每个页面加 `?format=json` 就会返回模板用的数据（公开、robots 允许，和网页同一个地址）：
- 分类页 `/<分类路径>/?format=json&limit=100` → `collection`：`count` / `page` / `pages` / `page_next`，
  `products{}` 每个商品有 id、`url`（相对地址 xxx.html）、`title`、`brand.title`、`price.price`（现价）、
  `price.price_old`（原价；没打折时等于现价，有的店写 0）、`available`、`variant`（默认变体名）、`description`（简介）、
  `image`（图片 id）。翻页：`/<分类路径>/page2.html?format=json&limit=100`（limit 最大 100，大多数分类一页就够）。
- 商品页 `/<商品>.html?format=json` → `product.variants{}`：每个尺码 / 颜色组合的价格和库存（`stock.available`），
  变体名写法 `Size : 176cm` 或 `"Color: Rock Khaki","Size: 170cm"`，没有选项时是 `Default`。
  分类页只有默认变体，所以要逐个尺码的有货情况必须抓商品页 —— 按 `max_detail_pages` 限量（打折的优先）。
- 整个店的分类树在任意页面的 `shop.categories`（含每个分类的商品数），配置新店时看一眼就能找到分类路径。
- 图片：`https://cdn.shoplightspeed.com/shops/<店铺 id>/files/<图片 id>/400x400x2/image.jpg`。

robots.txt：Bentgate / Wasatch Touring 都是 `Crawl-delay: 2`，禁止 /search/、/compare/、/cart/、/account/、/checkout/，
分类页和商品页允许（抓取器会遵守 Crawl-delay）。

配置（config/categories.toml）：
  [bentgate.boot]
  pages = [{ path = "/snowsports/skiing/mens-alpine-touring-boots", gender = "men" },
           { path = "/snowsports/skiing/womens-alpine-touring-boots", gender = "women" }]
  max_detail_pages = 80          # 抓多少个商品页取逐尺码库存（默认 0 = 只用分类页：价格对，但没有尺码列表）
  # 每个 page 还可以写 condition = "demo"（试板 / 二手专区）
"""
from __future__ import annotations

import html
import re
from collections.abc import Iterator

from ..net import FetchError
from .base import Adapter, RawProduct, SizeOption, to_float

_LIMIT = 100
_MAX_PAGES = 20
_SIZE_KEY = re.compile(r"size|length|cm|brake|width|größe|taille", re.I)
_COLOR_KEY = re.compile(r"colou?r|farbe", re.I)
_PART = re.compile(r'"?\s*([^":]+?)\s*:\s*([^"]*?)\s*"?\s*(?:,(?=\s*")|$)')


def variant_options(title: str | None) -> dict[str, str]:
    """`Size : 176cm` → {'Size': '176cm'}；`"Color: Rock Khaki","Size: 170cm"` → {'Color': ..., 'Size': ...}；
    `Default` → {}。"""
    t = (title or "").strip()
    if not t or t.lower() == "default" or ":" not in t:
        return {}
    return {k.strip(): v.strip() for k, v in _PART.findall(t) if k.strip() and v.strip()}


def size_of(opts: dict[str, str]) -> str | None:
    """变体选项里的“尺码”（Size / Length…）；只有颜色没有尺码时返回 None。"""
    for k, v in opts.items():
        if _SIZE_KEY.search(k) and not _COLOR_KEY.search(k):
            return v
    others = [v for k, v in opts.items() if not _COLOR_KEY.search(k)]
    return others[0] if len(others) == 1 else None


def color_of(opts: dict[str, str]) -> str | None:
    return next((v for k, v in opts.items() if _COLOR_KEY.search(k)), None)


def _size_key(s: SizeOption) -> tuple:
    """尺码排序：先按厘米，再按标签里的第一个数字（"97mm" 排在 "102mm" 前面），最后按文字。"""
    m = re.search(r"\d+(?:\.\d+)?", s.label or "")
    return (s.cm or 0, float(m.group()) if m else 1e9, s.label or "")


def _strip_html(s: str | None, limit: int = 4000) -> str | None:
    if not s:
        return None
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s))).strip()
    return s[:limit] or None


def _price(block: dict | None) -> tuple[float | None, float | None]:
    """(现价, 原价)；原价不高于现价时为 None。"""
    block = block or {}
    price = to_float(block.get("price_incl") if block.get("price_incl") not in (None, "") else block.get("price"))
    old = to_float(block.get("price_old_incl") if block.get("price_old_incl") not in (None, "") else block.get("price_old"))
    return price, (old if old and price and old > price + 0.5 else None)


class LightspeedAdapter(Adapter):
    platform = "lightspeed"

    def __init__(self, cfg, fetcher, log=print):
        super().__init__(cfg, fetcher, log)
        self.shop_id: int | None = None

    # ------------------------------------------------------------------ 抓取
    def _page_url(self, path: str, page: int) -> str:
        path = "/" + path.strip("/")
        return f"{self.base_url}{path}/" + (f"page{page}.html" if page > 1 else "") + f"?format=json&limit={_LIMIT}"

    def _listing(self, path: str) -> Iterator[tuple[dict, dict]]:
        """逐页 yield (分类信息, 商品)。"""
        for page in range(1, _MAX_PAGES + 1):
            data = self.fetcher.get_json(self._page_url(path, page))
            if self.shop_id is None:
                self.shop_id = (data.get("shop") or {}).get("id")
            coll = data.get("collection")
            if not isinstance(coll, dict):   # 不是分类页（路径写错时 Lightspeed 会给 404 或首页）
                raise FetchError(self._page_url(path, page), msg="不是 Lightspeed 分类页（没有 collection）")
            products = coll.get("products") or {}
            items = list(products.values()) if isinstance(products, dict) else list(products)
            info = {"title": coll.get("title"), "count": coll.get("count")}
            for p in items:
                yield info, p
            if not items or page >= int(coll.get("pages") or 1) or not coll.get("page_next"):
                break

    def _detail(self, url: str) -> dict | None:
        try:
            data = self.fetcher.get_json(f"{self.base_url}/{url.lstrip('/')}?format=json")
        except FetchError as e:
            if e.status in (404, 410):
                return None
            raise
        prod = data.get("product")
        return prod if isinstance(prod, dict) else None

    def fetch(self) -> Iterator[RawProduct]:
        found: dict[int, dict] = {}
        for spec in self.cfg.get("pages") or []:
            path = spec["path"] if isinstance(spec, dict) else spec
            spec = spec if isinstance(spec, dict) else {}
            n = 0
            for info, p in self._listing(path):
                if not p.get("id") or not p.get("url"):
                    continue
                n += 1
                entry = found.setdefault(p["id"], {"p": p, "genders": set(), "conds": set(), "types": []})
                if spec.get("gender"):
                    entry["genders"].add(spec["gender"])
                if spec.get("condition"):
                    entry["conds"].add(spec["condition"])
                if info.get("title") and info["title"] not in entry["types"]:
                    entry["types"].append(info["title"])
            self.log(f"[{self.rid}] {self.category} {path}: {n} 个商品")
        if not found:
            return
        # 先筛掉明显不是这个分类的，再给剩下的抓商品页（打折的优先），省请求
        keep = [e for e in found.values() if self.wanted((e["p"].get("title") or "").strip(),
                                                         e["types"][0] if e["types"] else None)]
        budget = max(0, int(self.cfg.get("max_detail_pages", 0)))
        order = sorted(keep, key=lambda e: _price(e["p"].get("price"))[1] is None)
        details = 0
        for e in order[:budget]:
            e["detail"] = self._detail(e["p"]["url"])
            details += 1
        if budget:
            self.log(f"[{self.rid}] {self.category}: {details} 个商品页（逐尺码库存），{len(keep) - details} 个只用分类页数据")
        for e in keep:
            raw = self._to_raw(e)
            if raw:
                yield raw

    # ------------------------------------------------------------------ 转换
    def _image(self, image_id) -> str | None:
        if not image_id or not self.shop_id:
            return None
        return f"https://cdn.shoplightspeed.com/shops/{self.shop_id}/files/{image_id}/400x400x2/image.jpg"

    def _sizes(self, variants: dict | list) -> list[SizeOption]:
        rows = variants.values() if isinstance(variants, dict) else variants
        by_key: dict[str, SizeOption] = {}
        for v in rows:
            opts = variant_options(v.get("title"))
            label = size_of(opts)
            if not label:
                continue
            price, old = _price(v.get("price"))
            ok = bool((v.get("stock") or {}).get("available"))
            cm = self.size_cm(label)
            key = str(cm) if cm else label.lower()
            cur = by_key.get(key)
            if cur is None:
                by_key[key] = SizeOption(label=label, cm=cm, available=ok, price=price, compare_at=old)
            else:   # 同一尺码多个颜色：有一个有货就算有货，价格取有货里的最低
                if ok and price and (not cur.available or price < (cur.price or 1e9)):
                    cur.price, cur.compare_at = price, old
                cur.available = cur.available or ok
        sizes = sorted(by_key.values(), key=_size_key)
        if self.category in ("ski", "pole"):
            return [s for s in sizes if s.cm]
        return sizes

    def _to_raw(self, e: dict) -> RawProduct | None:
        p, det = e["p"], e.get("detail")
        title = html.unescape((p.get("title") or "").strip())
        if not title:
            return None
        url = f"{self.base_url}/{p['url'].lstrip('/')}"
        price, compare = _price((det or p).get("price"))
        available = bool(p.get("available"))
        sizes: list[SizeOption] = []
        if det:
            sizes = self._sizes(det.get("variants") or {})
            stock = det.get("stock") or {}
            available = bool(stock.get("available", available))
            pool = [s for s in sizes if s.available and s.price] or [s for s in sizes if s.price]
            if pool:
                best = min(pool, key=lambda s: s.price)
                price, compare = best.price, best.compare_at
                available = any(s.available for s in sizes)
        if not price:
            return None
        brand = p.get("brand") if isinstance(p.get("brand"), dict) else {}
        g = e["genders"]
        gender = "unisex" if {"men", "women"} <= g else next(iter(g)) if len(g) == 1 else None
        raw_tags = (det or {}).get("tags") or []
        raw_tags = raw_tags.values() if isinstance(raw_tags, dict) else raw_tags
        tags = [str(t.get("title") if isinstance(t, dict) else t) for t in raw_tags if t]
        body = _strip_html((det or {}).get("content")) or _strip_html(p.get("description"))
        return RawProduct(
            retailer_id=self.rid,
            external_id=str(p["id"]),
            url=url,
            title=title,
            price=price,
            compare_at=compare,
            currency=self.cfg.get("currency", "USD"),
            vendor=(brand.get("title") or "").strip() or None,
            product_type=e["types"][0] if e["types"] else None,
            tags=tags,
            image=self._image(p.get("image")),
            sizes=sizes,
            available=available,
            gender_hint=gender,
            type_hints=list(e["types"]),
            body_text=body,
            condition_hint=next(iter(e["conds"]), None),
            extra={"detail": bool(det), "default_variant": p.get("variant")},
            category=self.category,
        )
