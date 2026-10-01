"""汇率（以美元为基准）。优先欧洲央行参考汇率（frankfurter.dev），备用 open.er-api.com，每天缓存一次。"""
from __future__ import annotations

import json

from . import db
from .net import Fetcher

CURRENCIES = ["JPY", "CNY", "EUR", "CAD"]
# 两个接口都连不上、数据库里也没有缓存时才用这组“兜底”汇率（界面上会提示是估算值）
FALLBACK = {"USD": 1.0, "JPY": 150.0, "CNY": 7.1, "EUR": 0.9, "CAD": 1.38}


def _fetch() -> tuple[dict, str]:
    f = Fetcher(min_interval=0.5, retries=1, timeout=15)
    try:
        d = f.get_json("https://api.frankfurter.dev/v1/latest", params={"from": "USD", "to": ",".join(CURRENCIES)},
                       check_robots=False)
        return {"USD": 1.0, **d["rates"]}, f"欧洲央行参考汇率 {d.get('date', '')}"
    except Exception:
        d = f.get_json("https://open.er-api.com/v6/latest/USD", check_robots=False)
        return {"USD": 1.0, **{c: d["rates"][c] for c in CURRENCIES}}, "open.er-api.com"


def refresh(force: bool = False) -> dict:
    day = db.today()
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM fx WHERE day=?", (day,)).fetchone()
        if row and not force:
            return json.loads(row["rates_json"])
        rates, source = _fetch()
        conn.execute("INSERT OR REPLACE INTO fx(day, base, rates_json, source) VALUES(?,?,?,?)",
                     (day, "USD", json.dumps(rates), source))
        return rates


def current() -> dict:
    """返回 {"rates": {...}, "day": ..., "source": ..., "fallback": bool}，优先用今天，其次最近一次缓存。"""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM fx ORDER BY day DESC LIMIT 1").fetchone()
    if row is None or row["day"] != db.today():
        try:
            refresh()
            with db.connect() as conn:
                row = conn.execute("SELECT * FROM fx ORDER BY day DESC LIMIT 1").fetchone()
        except Exception:
            pass
    if row is None:
        return {"rates": FALLBACK, "day": None, "source": "兜底估算值（未能联网获取汇率）", "fallback": True}
    return {"rates": json.loads(row["rates_json"]), "day": row["day"], "source": row["source"], "fallback": False}


def to_usd(amount: float | None, currency: str, rates: dict) -> float | None:
    if amount is None:
        return None
    r = rates.get(currency or "USD")
    return round(amount / r, 2) if r else None
