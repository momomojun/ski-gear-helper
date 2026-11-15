"""把本地数据库推送到 Turso（云端 SQLite，libSQL）。抓取照旧写本地 SQLite，推送只上传有变化的行。

    python -m skideals push-turso          # 增量：只推上次之后内容变了的行
    python -m skideals push-turso --full   # 全量：忽略记录，所有行重推一遍（可重复执行，不会重复）

连接信息只从环境变量读（不进代码和仓库）：TURSO_DATABASE_URL、TURSO_AUTH_TOKEN。

为什么这样写：
- 一条一条写或者 executemany，每行都要和云端往返一次（实测每行约 65 毫秒，1000 行要 65 秒）；
  一条 INSERT 带几百行 VALUES 只要约 0.15 秒——所以按块拼成多行 INSERT … ON CONFLICT DO UPDATE。
- 每天每个在售商品的 last_seen 都会变，但服务器用不到它：算“有没有变化”时不看 last_seen，
  每天要推的就只有价格 / 库存 / 分类真正变了的几千行（Turso 免费版每月 1000 万次写入）。
- 每张表、每一块推完就把这些行的“指纹”记到本地 turso_sync 表：中途断网重跑，只补没推上去的部分。

表的分工：本地负责的表由这里推送；服务器负责的表（关注、提醒、设置、日本比价）只在服务器上写，这里不碰。
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime

# 本地负责、需要推送的表 → 计算“是否变化”时忽略的列
PUSH_TABLES: dict[str, tuple[str, ...]] = {
    "retailers": (),
    "listings": ("last_seen",),
    "price_history": (),
    "site_checks": (),
    "spec_cache": (),
    "fx": (),
    "crawl_runs": (),
}
# 只在第一次（云端还是空的时候）带上去的表：之后由服务器负责
SEED_TABLES = ("intl_prices", "intl_queries")
# 服务器负责的表：只在云端建表，不推数据
SERVER_TABLES = ("watchlist", "alerts", "settings")

MAX_PARAMS = 8000          # 每条 INSERT 最多多少个参数（SQLite 上限 32766，留足余量）
MAX_BYTES = 800_000        # 每条 INSERT 大约多少字节（商品行带 JSON，控制请求体大小）
_STATE = "turso_sync"


def configured() -> bool:
    return bool(os.environ.get("TURSO_DATABASE_URL") and os.environ.get("TURSO_AUTH_TOKEN"))


def connect_remote():
    import libsql
    if not configured():
        raise RuntimeError("没有设置环境变量 TURSO_DATABASE_URL / TURSO_AUTH_TOKEN")
    return libsql.connect(database=os.environ["TURSO_DATABASE_URL"], auth_token=os.environ["TURSO_AUTH_TOKEN"])


# ------------------------------------------------------------------ 表结构

def _local_schema(local: sqlite3.Connection, tables) -> list[str]:
    """本地的建表 / 建索引语句（和本地结构完全一致，含后来补的列）。"""
    out = []
    names = ",".join("?" * len(tables))
    for (sql,) in local.execute(f"SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND tbl_name IN ({names}) "
                                "ORDER BY type DESC", list(tables)):   # table 在 index 前面
        sql = sql.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1).replace(
            "CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1).replace(
            "CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ", 1)
        out.append(sql.replace("IF NOT EXISTS IF NOT EXISTS", "IF NOT EXISTS"))
    return out


def ensure_schema(remote, local: sqlite3.Connection, log=print) -> None:
    tables = (*PUSH_TABLES, *SEED_TABLES, *SERVER_TABLES)
    for sql in _local_schema(local, tables):
        remote.execute(sql)
    remote.execute("CREATE TABLE IF NOT EXISTS sync_meta(key TEXT PRIMARY KEY, value TEXT)")
    # 本地后来补了列（db._MIGRATIONS）而云端还没有：补上
    for t in tables:
        have = {r[1] for r in remote.execute(f"PRAGMA table_info({t})").fetchall()}
        for r in local.execute(f"PRAGMA table_info({t})").fetchall():
            if have and r[1] not in have:
                remote.execute(f"ALTER TABLE {t} ADD COLUMN {r[1]} {r[2]}")
                log(f"  云端 {t} 补列 {r[1]}")
    remote.commit()


# ------------------------------------------------------------------ 变化检测

def _ensure_state(local: sqlite3.Connection) -> None:
    local.execute(f"CREATE TABLE IF NOT EXISTS {_STATE}(tbl TEXT, pk TEXT, h TEXT, PRIMARY KEY(tbl, pk))")


def _fingerprint(row, skip_idx: set[int]) -> str:
    vals = tuple(v for i, v in enumerate(row) if i not in skip_idx)
    return hashlib.blake2b(repr(vals).encode("utf-8"), digest_size=10).hexdigest()


def _pk_cols(local: sqlite3.Connection, table: str) -> list[str]:
    info = local.execute(f"PRAGMA table_info({table})").fetchall()
    pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
    return pk or ["rowid"]


def changed_rows(local: sqlite3.Connection, table: str, ignore: tuple[str, ...], full: bool):
    """返回 (列名, 有变化的行, 它们的 (pk, 指纹))。"""
    cols = [r[1] for r in local.execute(f"PRAGMA table_info({table})").fetchall()]
    pk = _pk_cols(local, table)
    pk_idx = [cols.index(c) for c in pk]
    skip = {cols.index(c) for c in ignore if c in cols}
    known = {} if full else dict(local.execute(f"SELECT pk, h FROM {_STATE} WHERE tbl=?", (table,)).fetchall())
    rows, marks = [], []
    for row in local.execute(f"SELECT {', '.join(cols)} FROM {table}"):
        key = json.dumps([row[i] for i in pk_idx], ensure_ascii=False)
        h = _fingerprint(row, skip)
        if known.get(key) != h:
            rows.append(row)
            marks.append((key, h))
    return cols, pk, rows, marks


# ------------------------------------------------------------------ 上传

def _chunks(rows: list, cols: list[str]):
    """按参数个数和大致字节数切块。"""
    per = max(1, MAX_PARAMS // len(cols))
    chunk, size = [], 0
    for r in rows:
        b = sum(len(v) if isinstance(v, (str, bytes)) else 8 for v in r)
        if chunk and (len(chunk) >= per or size + b > MAX_BYTES):
            yield chunk
            chunk, size = [], 0
        chunk.append(r)
        size += b
    if chunk:
        yield chunk


def upsert_sql(table: str, cols: list[str], pk: list[str], n: int) -> str:
    one = "(" + ", ".join("?" * len(cols)) + ")"
    sets = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in pk)
    tail = f" ON CONFLICT({', '.join(pk)}) DO UPDATE SET {sets}" if sets else f" ON CONFLICT({', '.join(pk)}) DO NOTHING"
    return f"INSERT INTO {table}({', '.join(cols)}) VALUES " + ", ".join([one] * n) + tail


def push_table(remote, local: sqlite3.Connection, table: str, ignore=(), full=False, log=print) -> int:
    cols, pk, rows, marks = changed_rows(local, table, ignore, full)
    if not rows:
        return 0
    done = 0
    t0 = time.time()
    for chunk in _chunks(rows, cols):
        remote.execute(upsert_sql(table, cols, pk, len(chunk)), [v for r in chunk for v in r])
        remote.commit()
        local.executemany(f"INSERT OR REPLACE INTO {_STATE}(tbl, pk, h) VALUES (?, ?, ?)",
                          [(table, k, h) for k, h in marks[done:done + len(chunk)]])
        local.commit()   # 每块推完就记下：断网重跑只补剩下的
        before = done
        done += len(chunk)
        if len(rows) > 5000 and (done * 5 // len(rows)) != (before * 5 // len(rows)):   # 大约每 20% 报一次进度
            log(f"  {table}: {done}/{len(rows)}（{time.time() - t0:.0f} 秒）")
    return done


def push(full: bool = False, db_path=None, log=print) -> dict:
    from .config import DB_PATH
    local = sqlite3.connect(db_path or DB_PATH, timeout=30)
    _ensure_state(local)
    remote = connect_remote()
    t0 = time.time()
    ensure_schema(remote, local, log)
    counts = {}
    first = remote.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 0
    for table, ignore in PUSH_TABLES.items():
        counts[table] = push_table(remote, local, table, ignore, full, log)
    if first:   # 云端刚建好：把日本比价也带上去，之后由服务器自己维护
        for table in SEED_TABLES:
            counts[table] = push_table(remote, local, table, (), True, log)
    data_day = local.execute("SELECT MAX(last_seen) FROM listings").fetchone()[0]
    remote.execute("INSERT INTO sync_meta(key, value) VALUES ('pushed_at', ?), ('data_day', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (datetime.now().isoformat(timespec="seconds"), (data_day or "")[:10]))
    remote.commit()
    local.close()
    log("已推送到 Turso：" + "，".join(f"{t} {n}" for t, n in counts.items() if n) +
        f"（用时 {time.time() - t0:.0f} 秒）" if any(counts.values()) else f"没有变化（用时 {time.time() - t0:.0f} 秒）")
    return counts
