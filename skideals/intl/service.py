"""国际比价：美国 vs 日本 vs 中国。

- 日本：价格.com 自动搜索（按需查询，结果缓存几天）
- 中国：淘宝/天猫/京东都要求登录并有严格的反爬验证，无法稳定自动抓取 ——
        提供一键搜索链接，你在自己的浏览器里看到价格后，可以手动记一笔参考价，工具会自动换算对比。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import quote

from .. import catalog, db, fx
from ..net import Fetcher
from ..normalize import fold
from . import kakaku

NOTES = {
    "US": "美国价格不含销售税（马萨诸塞州 6.25%），多数店满额包邮；雪板属于大件，个别店收超大件运费。",
    "JP": "日本价格已含 10% 消费税。游客在日本实体店消费满 ¥5,000 可办免税；网店一般不免税，寄往海外需另付国际运费。",
    "CN": "国内价格含 13% 增值税。国外品牌的“国行”和海外版型号、年份可能不同，注意核对年份和是否含固定器。",
}


def search_links(brand: str, model: str, category: str = "ski") -> dict:
    from ..categories import BY_ID
    c = BY_ID.get(category) or BY_ID["ski"]
    kw = f"{fold(brand)} {model}".strip()
    q = quote(kw)
    jp_word, cn_word = c.jp.split()[-1], c.cn.split()[0]
    kakaku_cat = f"category={c.kakaku}&" if c.kakaku else ""
    return {
        "JP": [
            {"name": "価格.com", "url": f"https://search.kakaku.com/{q}/?{kakaku_cat}sort=priceb"},
            {"name": "楽天市場", "url": f"https://search.rakuten.co.jp/search/mall/{quote(kw + ' ' + jp_word)}/"},
            {"name": "Yahoo!ショッピング", "url": f"https://shopping.yahoo.co.jp/search?p={quote(kw + ' ' + jp_word)}"},
            {"name": "Amazon.co.jp", "url": f"https://www.amazon.co.jp/s?k={quote(kw + ' ' + jp_word)}"},
        ],
        "CN": [
            {"name": "淘宝", "url": f"https://s.taobao.com/search?q={quote(kw + ' ' + cn_word)}"},
            {"name": "天猫", "url": f"https://list.tmall.com/search_product.htm?q={quote(kw + ' ' + cn_word)}"},
            {"name": "京东", "url": f"https://search.jd.com/Search?keyword={quote(kw + ' ' + cn_word)}&enc=utf-8"},
            {"name": "什么值得买", "url": f"https://search.smzdm.com/?c=home&s={quote(kw + ' ' + cn_word)}"},
            {"name": "闲鱼（二手）", "url": f"https://www.goofish.com/search?q={quote(kw + ' ' + cn_word)}"},
        ],
        # 这些美国网站有反爬保护、无法自动抓取，可以手动看一眼
        "US": [
            {"name": "Backcountry", "url": f"https://www.google.com/search?q=site%3Abackcountry.com+{q}"},
            {"name": "Powder7", "url": f"https://www.google.com/search?q=site%3Apowder7.com+{q}"},
            {"name": "Amazon", "url": f"https://www.amazon.com/s?k={quote(kw)}"},
            {"name": "Google 购物", "url": f"https://www.google.com/search?tbm=shop&q={quote(kw)}"},
        ],
    }


_FETCHER = Fetcher(min_interval=1.5, retries=1)  # 全局共用：多次查询也遵守同一个限速


def _key_parts(key: str):
    from ..normalize import parse_key
    k = parse_key(key)
    return k["tokens"], k["gclass"], k["bindings"], k["category"]


def refresh_jp(key: str) -> dict:
    fam = catalog.family(key)
    if fam is None:
        raise KeyError(key)
    tokens, gclass, bindings, category = _key_parts(key)
    fetcher = _FETCHER
    status, n = "ok", 0
    try:
        query, items = kakaku.find(fetcher, fam["b"], fam["m"], tokens, gclass, bindings, category=category)
    except Exception as e:
        query, items, status = f"{fam['b']} {fam['m']}", [], f"error: {str(e)[:120]}"
    now = db.now_iso()
    with db.connect() as conn:
        if status == "ok":
            conn.execute("DELETE FROM intl_prices WHERE model_key=? AND source='kakaku' AND manual=0", (key,))
            for it in items:
                conn.execute("""INSERT INTO intl_prices(model_key, country, source, title, url, shop, price, currency,
                                  year, bindings, match_score, manual, fetched_at)
                                VALUES(?,?,?,?,?,?,?,?,?,?,?,0,?)""",
                             (key, "JP", "kakaku", it["title"], it["url"], it.get("shop"), it["price"], "JPY",
                              it.get("year"), int(bool(it.get("bindings"))), it.get("score"), now))
            n = len(items)
        conn.execute("INSERT OR REPLACE INTO intl_queries(model_key, country, source, query, fetched_at, status, n) "
                     "VALUES(?,?,?,?,?,?,?)", (key, "JP", "kakaku", query, now, status, n))
    return get(key)


def add_manual(key: str, country: str, price: float, currency: str, url: str | None = None,
               title: str | None = None, shop: str | None = None, year: int | None = None,
               bindings: bool | None = None, note: str | None = None) -> int:
    with db.connect() as conn:
        cur = conn.execute("""INSERT INTO intl_prices(model_key, country, source, title, url, shop, price, currency,
                                year, bindings, match_score, manual, note, fetched_at)
                              VALUES(?,?,?,?,?,?,?,?,?,?,1.0,1,?,?)""",
                           (key, country, "manual", title, url, shop, price, currency, year,
                            None if bindings is None else int(bindings), note, db.now_iso()))
        return cur.lastrowid


def delete_entry(entry_id: int) -> None:
    with db.connect() as conn:
        conn.execute("DELETE FROM intl_prices WHERE id=?", (entry_id,))


def get(key: str) -> dict:
    fam = catalog.family(key)
    fxr = fx.current()
    rates = fxr["rates"]
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM intl_prices WHERE model_key=? ORDER BY price", (key,))]
        q = conn.execute("SELECT * FROM intl_queries WHERE model_key=? AND country='JP'", (key,)).fetchone()
        cache_days = db.get_settings(conn).get("intl_cache_days", 3)
    for r in rows:
        r["usd"] = fx.to_usd(r["price"], r["currency"], rates)
        r["cny"] = round(r["usd"] * rates["CNY"]) if r["usd"] else None
    stale = q is None or datetime.fromisoformat(q["fetched_at"]) < datetime.now() - timedelta(days=cache_days)

    # 汇总：每个国家的最低价；优先和美国最低价同一年份比较
    us_best = None
    if fam:
        new = [o for o in fam["o"] if o["cd"] == "new" and o["a"]]
        if new:
            b = min(new, key=lambda o: o["p"])
            us_best = {"usd": b["p"], "year": b["y"], "retailer": b["r"], "url": b["u"],
                       "cny": round(b["p"] * rates["CNY"]), "jpy": round(b["p"] * rates["JPY"])}
    summary = {"US": us_best}
    for country in ("JP", "CN"):
        cands = [r for r in rows if r["country"] == country and r["usd"]]
        if not cands:
            summary[country] = None
            continue
        same_year = [r for r in cands if us_best and r["year"] and r["year"] == us_best["year"]]
        pick = min(same_year or cands, key=lambda r: r["usd"])
        diff = round((pick["usd"] / us_best["usd"] - 1) * 100) if us_best else None
        summary[country] = {"usd": pick["usd"], "cny": pick["cny"], "price": pick["price"], "currency": pick["currency"],
                            "year": pick["year"], "shop": pick["shop"] or pick["source"], "url": pick["url"],
                            "vs_us_pct": diff, "same_year": bool(same_year), "manual": bool(pick["manual"])}
    return {
        "summary": summary,
        "entries": rows,
        "jp_query": dict(q) if q else None,
        "stale": stale,
        "links": search_links(fam["b"], fam["m"], _key_parts(key)[3]) if fam else {},
        "notes": NOTES,
        "fx": {"rates": rates, "day": fxr["day"], "source": fxr["source"], "fallback": fxr["fallback"]},
    }
