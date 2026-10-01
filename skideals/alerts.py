"""关注列表 → 降价提醒。每次抓取结束后调用 check_watchlist()。

提醒规则（全新、有货的报价；如果关注时指定了长度/年份，只看符合的）：
  1. 最低价 ≤ 目标价，且比上次提醒时更便宜  → “到达目标价”
  2. 最低价比上次记录下降 ≥ 3%             → “降价”
"""
from __future__ import annotations

import json

from . import db, fx


def best_offer(conn, key: str, year: int | None = None, length: int | None = None,
               size_label: str | None = None) -> dict | None:
    rates = fx.current()["rates"]
    q = """SELECT l.*, r.name AS rname FROM listings l JOIN retailers r ON r.id=l.retailer_id
           WHERE l.model_key=? AND l.active=1 AND r.enabled=1 AND l.condition='new' AND l.available=1"""
    args: list = [key]
    if year:
        q += " AND l.year=?"
        args.append(year)
    best = None
    for row in conn.execute(q, args):
        price = row["price"]
        if length:
            sizes = [s for s in json.loads(row["sizes_json"] or "[]")
                     if s.get("cm") and abs(s["cm"] - length) <= 2 and s.get("a")]
            if not sizes:
                continue
            price = min((s.get("p") or row["price"]) for s in sizes)
        elif size_label:  # 服装/雪鞋：按规范化尺码（M、26.5、100mm…）匹配；"S/M" 这种两码合一也算
            want = size_label.strip().upper()
            sizes = [s for s in json.loads(row["sizes_json"] or "[]")
                     if s.get("a") and want in str(s.get("n") or s.get("l") or "").upper().split("/")]
            if not sizes:
                continue
            price = min((s.get("p") or row["price"]) for s in sizes)
        usd = fx.to_usd(price, row["currency"] or "USD", rates)
        if usd and (best is None or usd < best["usd"]):
            best = {"usd": usd, "retailer": row["rname"], "retailer_id": row["retailer_id"], "url": row["url"],
                    "year": row["year"]}
    return best


def check_watchlist() -> int:
    created = 0
    with db.connect() as conn:
        settings = db.get_settings(conn)
        for w in conn.execute("SELECT * FROM watchlist").fetchall():
            best = best_offer(conn, w["model_key"], w["year"], w["length_cm"], w["size_label"])
            if best is None:
                continue
            price, label = best["usd"], w["label"] or w["model_key"]
            kind = msg = None
            target, notified, last = w["target_price"], w["last_notified_price"], w["last_best"]
            if target and price <= target and (notified is None or price < notified - 0.5):
                kind, msg = "target", f"{label} 降到 ${price:.0f}（目标 ${target:.0f}）· {best['retailer']}"
            elif last and price <= last * 0.97:
                kind, msg = "drop", f"{label} 降价 ${last:.0f} → ${price:.0f} · {best['retailer']}"
            if kind:
                conn.execute("""INSERT INTO alerts(model_key, created_at, kind, message, price, retailer_id, url)
                                VALUES(?,?,?,?,?,?,?)""",
                             (w["model_key"], db.now_iso(), kind, msg, price, best["retailer_id"], best["url"]))
                conn.execute("UPDATE watchlist SET last_notified_price=? WHERE model_key=?", (price, w["model_key"]))
                created += 1
                if settings.get("notify_desktop"):
                    from .notify import toast
                    toast("雪板降价提醒 ❄", msg)
            conn.execute("UPDATE watchlist SET last_best=? WHERE model_key=?", (price, w["model_key"]))
    return created
