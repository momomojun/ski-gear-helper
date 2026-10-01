"""SQLite 数据库：商品、每日价格历史、国际比价、关注列表、提醒、网站核验结果、汇率、设置。

每次调用 connect() 得到一个新连接（SQLite 连接不能跨线程共享）；开启 WAL 模式，
这样后台抓取在写入时，网页照样可以读。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from .config import DATA_DIR, DB_PATH, DEFAULT_SETTINGS
from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS retailers(
  id TEXT PRIMARY KEY, name TEXT, country TEXT, currency TEXT, url TEXT, adapter TEXT, tier TEXT,
  enabled INTEGER, meta_json TEXT, last_crawl_at TEXT, last_ok_at TEXT, last_status TEXT, last_msg TEXT,
  n_active INTEGER DEFAULT 0, cats_json TEXT);
CREATE TABLE IF NOT EXISTS listings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  retailer_id TEXT NOT NULL, external_id TEXT NOT NULL, url TEXT, title TEXT, image TEXT,
  brand TEXT, model TEXT, tokens TEXT, year INTEGER, gender TEXT, gender_src TEXT, gclass TEXT,
  ski_type TEXT, type_src TEXT, waist INTEGER, waist_src TEXT, bindings INTEGER, binding_name TEXT,
  condition TEXT, model_key TEXT,
  price REAL, compare_at REAL, currency TEXT, sizes_json TEXT, available INTEGER,
  first_seen TEXT, last_seen TEXT, active INTEGER DEFAULT 1, prev_price REAL, price_changed_at TEXT,
  category TEXT DEFAULT 'ski', features TEXT, specs TEXT, vendor TEXT, meta TEXT,
  UNIQUE(retailer_id, external_id));
CREATE INDEX IF NOT EXISTS ix_listings_key ON listings(model_key);
CREATE INDEX IF NOT EXISTS ix_listings_active ON listings(active, retailer_id);
CREATE TABLE IF NOT EXISTS price_history(
  listing_id INTEGER NOT NULL, day TEXT NOT NULL, price REAL, compare_at REAL, available INTEGER, sizes TEXT,
  PRIMARY KEY(listing_id, day));
CREATE TABLE IF NOT EXISTS intl_prices(
  id INTEGER PRIMARY KEY AUTOINCREMENT, model_key TEXT NOT NULL, country TEXT, source TEXT, title TEXT,
  url TEXT, shop TEXT, price REAL, currency TEXT, year INTEGER, bindings INTEGER, match_score REAL,
  manual INTEGER DEFAULT 0, note TEXT, fetched_at TEXT);
CREATE INDEX IF NOT EXISTS ix_intl_key ON intl_prices(model_key);
CREATE TABLE IF NOT EXISTS intl_queries(
  model_key TEXT, country TEXT, source TEXT, query TEXT, fetched_at TEXT, status TEXT, n INTEGER,
  PRIMARY KEY(model_key, country, source));
CREATE TABLE IF NOT EXISTS watchlist(
  model_key TEXT PRIMARY KEY, label TEXT, year INTEGER, length_cm INTEGER, target_price REAL,
  created_at TEXT, last_best REAL, last_notified_price REAL, note TEXT, size_label TEXT);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, model_key TEXT, created_at TEXT, kind TEXT, message TEXT,
  price REAL, retailer_id TEXT, url TEXT, seen INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS site_checks(
  domain TEXT PRIMARY KEY, checked_at TEXT, score INTEGER, level TEXT, report_json TEXT);
CREATE TABLE IF NOT EXISTS fx(day TEXT PRIMARY KEY, base TEXT, rates_json TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS crawl_runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, status TEXT, summary_json TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS spec_cache(
  key TEXT PRIMARY KEY, waist INTEGER, url TEXT, fetched_at TEXT);  -- 双板规格补全（skideals/specs.py）：品牌|型号 → 腰宽
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return datetime.now().date().isoformat()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


_MIGRATIONS = {  # 老数据库补列（只加不删）
    "listings": [("category", "TEXT DEFAULT 'ski'"), ("features", "TEXT"), ("specs", "TEXT"), ("vendor", "TEXT"),
                 ("meta", "TEXT")],  # 网站原始分类信息（商品类型/标签/选项/来源分类页），重新判定分类用
    "retailers": [("cats_json", "TEXT")],
    "watchlist": [("size_label", "TEXT")],
}


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, cols in _MIGRATIONS.items():
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for col, ddl in cols:
                if col not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        conn.execute("CREATE INDEX IF NOT EXISTS ix_listings_cat ON listings(category, active)")
        # 分组键改成了“分类|品牌|…”：旧格式的自动日本比价结果作废（打开详情时会重新查询）
        # 旧键 = 3 个竖线且以 f/b 结尾（brand|tokens|gclass|f）；新双板键有 4 个竖线，其他分类键以 a/w/k 结尾
        old = "model_key GLOB '*|*|*|[fb]' AND model_key NOT GLOB '*|*|*|*|*'"
        conn.execute(f"DELETE FROM intl_prices WHERE manual=0 AND {old}")
        conn.execute(f"DELETE FROM intl_queries WHERE {old}")


# ------------------------------------------------------------------ 零售商

def sync_retailers(conn: sqlite3.Connection, cfgs: list[dict]) -> None:
    """把 retailers.toml 同步进数据库（配置文件是唯一的“真相”，数据库只多存运行状态）。"""
    ids = []
    for c in cfgs:
        ids.append(c["id"])
        conn.execute(
            """INSERT INTO retailers(id, name, country, currency, url, adapter, tier, enabled)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, country=excluded.country,
                 currency=excluded.currency, url=excluded.url, adapter=excluded.adapter,
                 tier=excluded.tier, enabled=excluded.enabled""",
            (c["id"], c["name"], c["country"], c["currency"], c["url"], c.get("adapter", "shopify"),
             c["tier"], int(bool(c.get("enabled", True)))))
    if ids:  # 配置里删掉的网站：停用
        conn.execute(f"UPDATE retailers SET enabled=0 WHERE id NOT IN ({','.join('?' * len(ids))})", ids)


def set_retailer_status(conn, rid: str, status: str, msg: str, meta: dict | None = None, ok: bool = False):
    now = now_iso()
    conn.execute("UPDATE retailers SET last_crawl_at=?, last_status=?, last_msg=? WHERE id=?", (now, status, msg, rid))
    if ok:
        n = conn.execute("SELECT COUNT(*) FROM listings WHERE retailer_id=? AND active=1", (rid,)).fetchone()[0]
        conn.execute("UPDATE retailers SET last_ok_at=?, n_active=? WHERE id=?", (now, n, rid))
    if meta:
        conn.execute("UPDATE retailers SET meta_json=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), rid))


# ------------------------------------------------------------------ 商品

def save_listing(conn: sqlite3.Connection, lst: Listing, seen_at: str, day: str) -> None:
    from .categories import norm_size
    r = lst.raw
    cat = lst.category or "ski"
    sizes = json.dumps([{"cm": s.cm, "l": s.label, "n": norm_size(cat, s.label) or (str(s.cm) if s.cm else None),
                         "a": s.available, "p": s.price, "c": s.compare_at}
                        for s in r.sizes], ensure_ascii=False)
    fields = dict(
        url=r.url, title=r.title, image=r.image, vendor=r.vendor, brand=lst.brand, model=lst.model, tokens=lst.tokens,
        year=lst.year, gender=lst.gender, gender_src=lst.gender_src, gclass=lst.gclass, ski_type=lst.ski_type,
        type_src=lst.type_src, waist=lst.waist, waist_src=lst.waist_src, bindings=int(lst.bindings),
        binding_name=lst.binding_name, condition=lst.condition, model_key=lst.model_key, price=r.price,
        compare_at=r.compare_at, currency=r.currency, sizes_json=sizes,
        available=None if r.available is None else int(r.available), last_seen=seen_at, active=1,
        category=cat, features=json.dumps(lst.features) if lst.features else None,
        specs=json.dumps(lst.specs) if lst.specs else None,
        meta=json.dumps({"pt": r.product_type, "tags": [str(t)[:80] for t in (r.tags or [])][:40],
                         "opts": r.extra.get("options"), "src": r.extra.get("src_category")}, ensure_ascii=False),
    )
    row = conn.execute("SELECT id, price FROM listings WHERE retailer_id=? AND external_id=?",
                       (r.retailer_id, r.external_id)).fetchone()
    if row:
        lid = row["id"]
        if row["price"] is not None and r.price is not None and abs(row["price"] - r.price) >= 0.01:
            fields["prev_price"] = row["price"]
            fields["price_changed_at"] = seen_at
        sets = ", ".join(f"{k}=?" for k in fields)
        conn.execute(f"UPDATE listings SET {sets} WHERE id=?", (*fields.values(), lid))
    else:
        fields.update(retailer_id=r.retailer_id, external_id=r.external_id, first_seen=seen_at)
        cols = ", ".join(fields)
        cur = conn.execute(f"INSERT INTO listings({cols}) VALUES({','.join('?' * len(fields))})",
                           tuple(fields.values()))
        lid = cur.lastrowid
    avail_sizes = ",".join(str(s.cm or s.label) for s in r.sizes if s.available)[:400]
    # 价格历史只在“有变化”或“距上次记录满 7 天”时写一条：全品类几万个商品，每天每个都写会让数据库一年涨到 1GB
    last = conn.execute("SELECT day, price, compare_at, available, sizes FROM price_history WHERE listing_id=? "
                        "ORDER BY day DESC LIMIT 1", (lid,)).fetchone()
    stale = last is None or (datetime.fromisoformat(day) - datetime.fromisoformat(last["day"])).days >= 7
    changed = last is None or (last["price"], last["compare_at"], last["available"], last["sizes"]) !=         (r.price, r.compare_at, fields["available"], avail_sizes)
    if changed or stale or last["day"] == day:
        conn.execute("INSERT OR REPLACE INTO price_history(listing_id, day, price, compare_at, available, sizes) "
                     "VALUES(?,?,?,?,?,?)", (lid, day, r.price, r.compare_at, fields["available"], avail_sizes))


def deactivate_missing(conn: sqlite3.Connection, rid: str, crawl_started: str, category: str | None = None) -> int:
    """这次完整抓取里没再出现的商品 = 已下架。按“来源分类页”判断（meta.src；旧数据没有就用分类）：
    只抓了雪服页，就只下架雪服页里没再出现的商品；从头盔页改判成雪镜的商品，由头盔页负责判断下架。"""
    if category:
        cur = conn.execute("UPDATE listings SET active=0 WHERE retailer_id=? AND active=1 AND last_seen < ? "
                           "AND COALESCE(json_extract(meta, '$.src'), category) = ?", (rid, crawl_started, category))
    else:
        cur = conn.execute("UPDATE listings SET active=0 WHERE retailer_id=? AND active=1 AND last_seen < ?",
                           (rid, crawl_started))
    return cur.rowcount


def set_category_status(conn, rid: str, category: str, status: str, n: int, msg: str) -> None:
    """记录每个网站每个分类最近一次抓取的结果（可信度页和分类统计用）。"""
    row = conn.execute("SELECT cats_json FROM retailers WHERE id=?", (rid,)).fetchone()
    cats = json.loads(row["cats_json"]) if row and row["cats_json"] else {}
    cats[category] = {"status": status, "n": n, "msg": msg, "at": now_iso()}
    conn.execute("UPDATE retailers SET cats_json=? WHERE id=?", (json.dumps(cats, ensure_ascii=False), rid))


# ------------------------------------------------------------------ 设置

def get_settings(conn=None) -> dict:
    own = conn is None
    conn = conn or connect()
    try:
        vals = dict(DEFAULT_SETTINGS)
        for row in conn.execute("SELECT key, value FROM settings"):
            try:
                vals[row["key"]] = json.loads(row["value"])
            except (TypeError, ValueError):
                pass
        return vals
    finally:
        if own:
            conn.close()


def put_settings(conn, values: dict) -> None:
    for k, v in values.items():
        if k in DEFAULT_SETTINGS:
            conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (k, json.dumps(v)))
