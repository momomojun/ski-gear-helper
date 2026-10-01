"""日本价格：价格.com（kakaku.com）商品搜索。

价格.com 汇总了乐天、Yahoo!购物等日本网店的报价（价格为含 10% 消费税的日元价）。
这里按「品牌 + 型号」在“スキー板”分类里按价格从低到高搜索，再逐条判断是不是同一款板子。
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, unquote, urlsplit

from selectolax.parser import HTMLParser

from ..net import Fetcher
from ..normalize import brand_aliases, detect_bindings, extract_year, fold, split_tokens

SEARCH = "https://search.kakaku.com/{kw}/"
SKI_CATEGORY = "0009_0003_0024"   # スポーツ > スキー・スノボー用品 > スキー板

_NOT_SKI = re.compile(r"ブーツ|ポール|ストック|ケース|バッグ|シール|ワックス|チューン|ゴーグル|ヘルメット|グローブ|"
                      r"ウェア|パンツ|ジャケット|ソックス|ストッパー|ブレーキ|スキーカバー|スノーボード|ビンディング単品|"
                      r"ビンディングのみ|金具のみ|クロスカントリー|ショートスキー|スキーボード|ファンスキー")
_KIDS = re.compile(r"ジュニア|キッズ|子供|こども|\bjunior\b|\bjr\b|\bkids?\b")
_WOMEN = re.compile(r"レディース|ウィメンズ|ウーマン|\bwomen'?s?\b|\blady\b|\bladies\b")
_MEN = re.compile(r"メンズ|\bmen'?s\b")
# 这些 token 出现在日本标题里、却不在我们的型号里 → 很可能是另一个版本（Ti 版、女款、儿童款…）
_VARIANT_TOKENS = {"ti", "c", "w", "jr", "pro", "lux", "x", "s", "tour", "team", "mini", "chetler", "ul", "ltd",
                   "cti", "ca", "free", "carbon", "st", "sl", "gs", "fis", "wc", "rs", "rc", "ts", "sw", "hd"}


def _real_url(jump: str) -> str:
    """价格.com 的跳转链接 → 真正的网店商品页（去掉联盟推广参数）。"""
    try:
        qs = parse_qs(urlsplit(jump).query)
        u = qs.get("u", [jump])[0]
        inner = parse_qs(urlsplit(u).query)
        for k in ("pc", "vc_url", "url"):
            if k in inner:
                return unquote(inner[k][0])
        return u
    except Exception:
        return jump


def _shop(jump: str) -> str | None:
    try:
        raw_q = urlsplit(jump).query
        qs = parse_qs(raw_q, encoding="cp932")
        return (qs.get("sn") or [None])[0]
    except Exception:
        return None


def search(fetcher: Fetcher, keyword: str, kakaku_category: str | None = SKI_CATEGORY) -> list[dict]:
    url = SEARCH.format(kw=quote(keyword))
    params = {"sort": "priceb"}
    if kakaku_category:
        params["category"] = kakaku_category
    html = fetcher.get_html(url, params=params)
    doc = HTMLParser(html)
    out = []
    for it in doc.css(".p-resultItem"):
        img = it.css_first("img")
        link = it.css_first("a.s-targetLink") or it.css_first(".p-item_name a")
        price_el = it.css_first(".p-item_priceNum")
        name_el = it.css_first(".p-item_name")
        title = (name_el.text(strip=True) if name_el else "") or (img.attributes.get("alt") if img else "") or ""
        if not price_el or not title:
            continue
        try:
            price = int(re.sub(r"[^\d]", "", price_el.text()))
        except ValueError:
            continue
        href = link.attributes.get("href", "") if link else ""
        out.append({"title": title.strip(), "price": price, "url": _real_url(href) if href else url,
                    "shop": _shop(href) if href else None,
                    "image": (img.attributes.get("data-src") or img.attributes.get("src")) if img else None})
    return out


def match(item_title: str, brand: str, tokens: list[str], gclass: str, bindings: bool,
          category: str = "ski") -> float:
    """0~1：这条日本商品和我们的型号有多像。0 = 肯定不是。"""
    t = fold(item_title)
    if category == "ski":
        if _NOT_SKI.search(item_title):
            return 0.0
    else:
        from ..categories import classify
        detected = classify(item_title)
        if detected is not None and detected != category:  # 比如搜雪镜时混进来的头盔
            return 0.0
    aliases = [a for a in brand_aliases(brand) if a]
    if not any((re.search(rf"(?<![a-z0-9]){re.escape(a)}(?![a-z0-9])", t) if a.isascii() else a in t)
               for a in aliases):
        return 0.0
    toks = set(split_tokens(item_title))
    need = set(tokens)
    if not need <= toks:
        return 0.0
    kids = bool(_KIDS.search(t))
    women, men = bool(_WOMEN.search(t)), bool(_MEN.search(t))
    if (gclass == "k") != kids and not (gclass == "k" and not kids and "jr" in need):
        if gclass != "k" and kids:
            return 0.0
    score = 1.0
    if gclass == "a" and women and not men:
        return 0.0
    if gclass == "w" and not women and "w" not in toks:
        score -= 0.25
    if category == "ski":
        has_bind, _ = detect_bindings(item_title)
        if has_bind != bindings:
            return 0.0
    extra = (toks - need) & _VARIANT_TOKENS
    score -= 0.3 * len(extra)
    return max(score, 0.0)


def find(fetcher: Fetcher, brand: str, model: str, tokens: list[str], gclass: str, bindings: bool,
         limit: int = 20, category: str = "ski") -> tuple[str, list[dict]]:
    from ..categories import BY_ID
    c = BY_ID.get(category)
    query = f"{fold(brand)} {model}" + ("" if category == "ski" or not c else f" {c.jp.split()[-1]}")
    items = search(fetcher, query, c.kakaku if c else None)
    out = []
    for it in items:
        s = match(it["title"], brand, tokens, gclass, bindings, category)
        if s >= 0.6:
            it.update(score=round(s, 2), year=extract_year(it["title"]),
                      bindings=detect_bindings(it["title"])[0] if category == "ski" else None)
            out.append(it)
    out.sort(key=lambda x: x["price"])
    return query, out[:limit]
