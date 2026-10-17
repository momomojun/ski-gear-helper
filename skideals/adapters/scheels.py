"""SCHEELS: public category HTML and product-page JSON-LD only.

Config: pages = [{path = "/c/fleece-jackets", gender = "men"}]
        product_urls = ["/p/15171-40246/", "/p/15171-40275/"]
        # Any known /p/ URL (including SKU URLs) can be added; stale 404s are logged.
        max_listing_pages = 5; max_detail_pages = 40

Only configured category pages are inspected; no API, search, variation endpoint,
JS execution or synthesized pagination. Explicit product URLs take priority in the
shared detail budget. Categories currently may contain no server-rendered product
links, so keep product_urls as coverage seeds. The caller supplies the normal
robots-respecting Fetcher. No changes to its session, headers or robots settings.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Iterator
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from ..categories import norm_size
from ..net import FetchError
from .base import Adapter, RawProduct, SizeOption, to_float


def _objects(value) -> Iterator[dict]:
    """JSON-LD can be a single object, an array, or an @graph wrapper."""
    if isinstance(value, list):
        for item in value:
            yield from _objects(item)
    elif isinstance(value, dict):
        yield value
        yield from _objects(value.get("@graph"))


def _typed(obj: dict, kind: str) -> bool:
    types = obj.get("@type", [])
    return kind in (types if isinstance(types, list) else [types])


def _structured(html: str) -> Iterator[dict]:
    for node in HTMLParser(html).css('script[type="application/ld+json"]'):
        try:
            yield from _objects(json.loads(node.text()))
        except (ValueError, TypeError):
            # An unrelated ratings/video script must not hide a valid ProductGroup.
            continue


def product_groups(html: str, url: str = "scheels") -> list[dict]:
    groups = [p for p in _structured(html) if _typed(p, "ProductGroup")
              and isinstance(p.get("hasVariant"), list) and p["hasVariant"]]
    if not groups:
        raise FetchError(url, msg="No ProductGroup.hasVariant product data (blocked or changed HTML)")
    return groups


def _offers(value) -> list[dict]:
    if isinstance(value, dict):
        return [value]
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _price(value) -> float | None:
    price = to_float(value)
    return price if price is not None and math.isfinite(price) and price > 0 else None


def _label(value) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("value")
    return str(value).strip() if value is not None else ""


def _compare(offer: dict, price: float) -> float | None:
    # Other colours' prices are not evidence of this SKU's original/list price.
    prices = [_price(s.get("price")) for s in _offers(offer.get("priceSpecification"))
              if str(s.get("priceType", "")).rsplit("/", 1)[-1]
              in ("ListPrice", "StrikethroughPrice")
              and s.get("priceCurrency", offer.get("priceCurrency")) == offer.get("priceCurrency")]
    return max((p for p in prices if p and p > price + 0.5), default=None)


class ScheelsAdapter(Adapter):
    platform = "scheels"

    def _url(self, href: str, kind: str, source: str | None = None) -> str | None:
        """Accept only canonical public URLs on this store, with no query surface."""
        if not isinstance(href, str) or not href.strip():
            return None
        parts = urlsplit(urljoin(source or self.base_url + "/", href))
        base = urlsplit(self.base_url)
        if parts.scheme not in ("http", "https") or parts.username or parts.password:
            return None
        if (parts.hostname or "").removeprefix("www.") != (base.hostname or "").removeprefix("www."):
            return None
        if parts.port != base.port or parts.query:
            return None
        path = unquote(parts.path)
        if not path.startswith(f"/{kind}/") or not path[len(kind) + 2:].strip("/"):
            return None
        if "Product-Variation" in path or "/api/" in path or ".." in path.split("/"):
            return None
        return urlunsplit((base.scheme, base.netloc, parts.path.rstrip("/") + "/", "", ""))

    def _discover(self, html: str, source: str) -> list[str]:
        links = [n.attributes.get("href", "") for n in HTMLParser(html).css("a[href]")]
        for obj in _structured(html):
            if _typed(obj, "ItemList"):
                for entry in obj.get("itemListElement") or []:
                    if isinstance(entry, str):
                        links.append(entry)
                    elif isinstance(entry, dict):
                        item = entry.get("item") or entry
                        links.append(item if isinstance(item, str) else
                                     (item.get("url") or item.get("@id") or ""))
        return list(dict.fromkeys(url for href in links if (url := self._url(href, "p", source))))

    def _to_raw(self, group: dict, source: str, gender: str | None = None) -> RawProduct | None:
        currency = str(self.cfg.get("currency", "USD")).upper()
        title = _label(group.get("name"))
        style = _label(group.get("productGroupId"))
        if not style:
            style = str(group.get("@id") or "").rstrip("/").rsplit("/", 1)[-1]
        if not title or not style:
            raise FetchError(source, msg="ProductGroup missing name or stable product ID")
        variants = []
        for variant in group.get("hasVariant") or []:
            if not isinstance(variant, dict):
                continue
            size = _label(variant.get("size"))
            # Scheels uses 2XLarge, which the shared normalizer does not recognise.
            size = re.sub(r"^(\d+)\s*x[- ]?large$", r"\1XL", size, flags=re.I)
            size = norm_size(self.category, size) or size
            sku = _label(variant.get("sku"))
            for offer in _offers(variant.get("offers")):
                if str(offer.get("priceCurrency", "")).upper() != currency:
                    continue
                price = _price(offer.get("price"))
                if price is None:
                    continue
                url = self._url(offer.get("url", ""), "p", source)
                # Even an Offer may repeat the default colour URL. Prefer the SKU.
                if sku and re.fullmatch(r"[A-Za-z0-9_-]+", sku):
                    if not url or unquote(urlsplit(url).path).rstrip("/").rsplit("/", 1)[-1] != sku:
                        url = self._url(f"/p/{sku}/", "p")
                if not url or (not sku and url == self._url(group.get("@id", source), "p")):
                    continue
                availability = str(offer.get("availability", "")).rstrip("/").rsplit("/", 1)[-1]
                variants.append({"sku": sku, "size": size, "color": _label(variant.get("color")),
                                 "price": price, "compare_at": _compare(offer, price),
                                 "available": availability in ("InStock", "LimitedAvailability", "OnlineOnly"),
                                 "currency": currency, "url": url, "image": variant.get("image")})
        if not variants:
            raise FetchError(source, msg=f"No usable {currency} variant prices and purchase URLs for {style}")
        if not self.wanted(title, _label(group.get("category")) or None):
            return None
        by_size: dict[str, list[dict]] = {}
        for variant in variants:
            by_size.setdefault(variant["size"], []).append(variant)
        selected = {size: min([v for v in items if v["available"]] or items, key=lambda v: v["price"])
                    for size, items in by_size.items()}
        in_stock = [v for v in variants if v["available"]]
        best = min(in_stock or variants, key=lambda v: v["price"])
        brand = group.get("brand")
        vendor = _label(brand) if not isinstance(brand, str) else brand
        image = best.get("image") or group.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        if isinstance(image, dict):
            image = image.get("url") or image.get("contentUrl")
        return RawProduct(
            retailer_id=self.rid, external_id=style, title=title, url=best["url"],
            price=best["price"], compare_at=best["compare_at"], currency=currency,
            vendor=vendor or None, product_type=_label(group.get("category")) or None,
            image=image, available=bool(in_stock), gender_hint=gender,
            sizes=[SizeOption(label=size, available=v["available"], price=v["price"],
                              compare_at=v["compare_at"], url=v["url"], color=v["color"])
                   for size, v in selected.items() if size],
            body_text=HTMLParser(_label(group.get("description"))).text()[:8000],
            category=self.category,
            extra={"source_url": source, "selected_sku": best["sku"], "selected_color": best["color"],
                   "variants": variants, "size_urls": {s: v["url"] for s, v in selected.items()}},
        )

    def fetch(self) -> Iterator[RawProduct]:
        self.complete = True
        detail_limit = max(0, int(self.cfg.get("max_detail_pages", 40)))
        listing_limit = max(0, int(self.cfg.get("max_listing_pages", 5)))
        pending: dict[str, str | None] = {}
        for spec in self.cfg.get("product_urls") or []:
            href = (spec.get("url") or spec.get("path")) if isinstance(spec, dict) else spec
            url = self._url(href, "p")
            if not url:
                raise FetchError(str(href), msg="Expected a public same-store /p/ URL without query")
            pending[url] = spec.get("gender") if isinstance(spec, dict) else None
        pages = self.cfg.get("pages") or []
        if len(pages) > listing_limit:
            self.complete = False
        for spec in pages[:listing_limit]:
            href = spec.get("path") if isinstance(spec, dict) else spec
            url = self._url(href, "c")
            if not url:
                raise FetchError(str(href), msg="Expected a public same-store /c/ URL without query")
            links = self._discover(self.fetcher.get_html(url), url)
            gender = spec.get("gender") if isinstance(spec, dict) else None
            for link in links:
                pending.setdefault(link, gender)
            self.log(f"[{self.rid}] {url}: {len(links)} public HTML product links")
        if not pending:
            raise FetchError(self.base_url, msg="No product URLs discovered; configure product_urls seeds")
        seen_urls, seen_groups = set(), set()
        results = []
        details = parsed = 0
        for url, gender in pending.items():
            if url in seen_urls:
                continue
            if details >= detail_limit:
                self.complete = False
                self.log(f"[{self.rid}] detail budget reached ({detail_limit}); coverage is partial")
                break
            details += 1
            seen_urls.add(url)
            try:
                detail_html = self.fetcher.get_html(url)
            except FetchError as exc:
                if exc.status not in (404, 410):
                    raise
                self.log(f"[{self.rid}] missing product ({exc.status}): {url}")
                continue
            groups = product_groups(detail_html, url)
            for group in groups:
                raw = self._to_raw(group, url, gender)
                parsed += 1
                if raw:
                    seen_urls.update(v["url"] for v in raw.extra["variants"])
                    for alias in (group.get("@id", ""), f"/p/{raw.external_id}/"):
                        if canonical := self._url(alias, "p"):
                            seen_urls.add(canonical)
                    if raw.external_id not in seen_groups:
                        seen_groups.add(raw.external_id)
                        results.append(raw)
        if not parsed:
            raise FetchError(self.base_url, msg="No product data parsed within detail budget")
        self.log(f"[{self.rid}] {details} detail pages, {len(results)} unique products")
        yield from results
