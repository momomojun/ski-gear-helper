"""数据快照：数据库 ⇄ 仓库里的 snapshot/ 文本文件。

为什么不直接把 skideals.db 放进 git：
- 数据库是二进制文件，每天更新一次，git 每次都要存一整份（约 100 MB），仓库很快就涨爆；
- GitHub 单个文件超过 100 MB 直接拒收。

所以导出成“一行一条记录”的文本（JSON Lines），按网站拆成多个文件、按主键排序：
每天只有价格/库存变了的那几行不同，git 只记差异，历史也能逐行对比。

不导出的表：watchlist（关注）、alerts（降价提醒）、settings（设置）——这些是你本人的使用记录，不公开。

文件格式：第一行 {"table": 表名, "columns": [列名…]}，之后每行一个 JSON 数组（顺序同列名）。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from .config import ROOT

SNAPSHOT_DIR = ROOT / "snapshot"
FORMAT = 1

# 表名 → (排序方式, 是否按网站拆分)
_TABLES = {
    "retailers": ("id", None),
    "listings": ("id", "retailer_id"),
    "price_history": ("listing_id, day", "retailer"),   # 历史表本身没有网站列，导出时按商品所属网站拆
    "intl_prices": ("id", None),
    "intl_queries": ("model_key, country, source", None),
    "site_checks": ("domain", None),
    "spec_cache": ("key", None),
    "fx": ("day", None),
    "crawl_runs": ("id", None),
}
PRIVATE_TABLES = ("watchlist", "alerts", "settings")
_WARN_MB = 45  # GitHub 对 50 MB 以上的文件会警告，100 MB 以上拒收


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name or "unknown")


def _line(values) -> str:
    return json.dumps(list(values), ensure_ascii=False, separators=(",", ":"))


def _write(path: Path, table: str, columns: list[str], rows) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"table": table, "columns": columns}, ensure_ascii=False) + "\n")
        for r in rows:
            f.write(_line(r) + "\n")
            n += 1
    os.replace(tmp, path)
    return n


def export(db_path: Path | str | None = None, out_dir: Path | str | None = None, log=print) -> dict:
    """数据库 → snapshot/。整个目录按数据库现状重写（删掉已经不存在的网站的文件）。"""
    from .config import DB_PATH
    db_path = Path(db_path or DB_PATH)
    out = Path(out_dir or SNAPSHOT_DIR)
    if not db_path.exists():
        raise FileNotFoundError(f"找不到数据库 {db_path}")
    # 只读打开：导出时本地网页/抓取可能还开着，不能锁住或改动它
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    written: set[Path] = set()
    counts: dict[str, int] = {}
    try:
        have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, (order, split) in _TABLES.items():
            if table not in have:
                continue
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            if split is None:
                p = out / f"{table}.jsonl"
                counts[table] = _write(p, table, cols, conn.execute(f"SELECT * FROM {table} ORDER BY {order}"))
                written.add(p)
                continue
            if table == "listings":
                groups = [r[0] for r in conn.execute("SELECT DISTINCT retailer_id FROM listings ORDER BY 1")]
                query = "SELECT * FROM listings WHERE retailer_id=? ORDER BY id"
            else:  # price_history：按商品所属网站拆
                groups = [r[0] for r in conn.execute(
                    "SELECT DISTINCT l.retailer_id FROM price_history h JOIN listings l ON l.id=h.listing_id ORDER BY 1")]
                query = ("SELECT h.* FROM price_history h JOIN listings l ON l.id=h.listing_id "
                         "WHERE l.retailer_id=? ORDER BY h.listing_id, h.day")
            counts[table] = 0
            for g in groups:
                p = out / table / f"{_safe(g)}.jsonl"
                counts[table] += _write(p, table, cols, conn.execute(query, (g,)))
                written.add(p)
        last_seen = conn.execute("SELECT MAX(last_seen) FROM listings").fetchone()[0]
    finally:
        conn.close()
    # 已经不存在的网站留下的旧文件删掉（只删 snapshot/ 里我们自己的 .jsonl）
    for p in out.rglob("*.jsonl"):
        if p not in written:
            p.unlink()
    manifest = {"format": FORMAT, "exported_at": datetime.now().isoformat(timespec="seconds"),
                "data_day": (last_seen or "")[:10] or None, "counts": counts,
                "private_tables_excluded": list(PRIVATE_TABLES)}
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8",
                                       newline="\n")
    big = [(p, p.stat().st_size / 1e6) for p in written if p.stat().st_size > _WARN_MB * 1e6]
    for p, mb in big:
        log(f"⚠ {p.relative_to(out)} 有 {mb:.0f} MB，接近 GitHub 单文件上限，需要再拆分")
    total = sum(p.stat().st_size for p in written) / 1e6
    log(f"快照已导出到 {out}：" + "，".join(f"{t} {n}" for t, n in counts.items()) + f"（共 {total:.0f} MB）")
    return manifest


def read_manifest(snap_dir: Path | str | None = None) -> dict:
    p = Path(snap_dir or SNAPSHOT_DIR) / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def bootstrap_if_empty(log=print) -> bool:
    """本地还没有数据（新 clone / 新电脑）而仓库里有快照：先从快照还原，打开就能看到全部历史数据。"""
    from .config import DB_PATH
    if not (SNAPSHOT_DIR / "manifest.json").exists():
        return False
    if DB_PATH.exists():
        c = sqlite3.connect(DB_PATH)
        try:
            n = c.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        except sqlite3.OperationalError:
            n = 0
        finally:
            c.close()
        if n:
            return False
    log("本地还没有数据，正在从仓库里的 snapshot/ 还原（约 15 秒）…")
    import_(SNAPSHOT_DIR, DB_PATH, log=log)
    return True


def import_(snap_dir: Path | str | None = None, db_path: Path | str | None = None, force: bool = False,
            log=print) -> dict:
    """snapshot/ → 新数据库。目标数据库已有商品时默认拒绝（防止覆盖你本地的数据），force=True 会先备份再覆盖。"""
    from . import db as dbmod
    from .config import DB_PATH
    src = Path(snap_dir or SNAPSHOT_DIR)
    target = Path(db_path or DB_PATH)
    if not (src / "manifest.json").exists():
        raise FileNotFoundError(f"{src} 里没有快照（缺 manifest.json）")
    if target.exists():
        with sqlite3.connect(target) as c:
            try:
                n = c.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
        if n and not force:
            raise FileExistsError(f"{target} 里已经有 {n} 个商品；确定要用快照覆盖请加 --force（会先备份）")
        if n:
            backup = target.with_name(f"{target.stem}.before-import-{datetime.now():%Y%m%d-%H%M%S}{target.suffix}")
            target.replace(backup)
            log(f"原数据库已备份为 {backup.name}")
        for suffix in ("-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        target.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    old = dbmod.DB_PATH
    dbmod.DB_PATH = target   # 用项目自己的建表/补列逻辑建一个空库，保证表结构和本地版完全一致
    try:
        dbmod.init()
    finally:
        dbmod.DB_PATH = old
    import gc
    gc.collect()   # init() 里 with connect() 只提交不关闭连接，回收掉它，后面才能独占写入
    conn = sqlite3.connect(target, timeout=30)
    counts: dict[str, int] = {}
    try:
        conn.execute("PRAGMA synchronous=OFF")
        schema_cols = {}
        for p in sorted(src.rglob("*.jsonl")):
            with open(p, encoding="utf-8") as f:
                head = json.loads(f.readline())
                table, cols = head["table"], head["columns"]
                if table not in _TABLES:
                    continue
                if table not in schema_cols:
                    schema_cols[table] = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                keep = [i for i, c in enumerate(cols) if c in schema_cols[table]]  # 快照比代码新/旧时只导共有的列
                names = ", ".join(cols[i] for i in keep)
                sql = f"INSERT OR REPLACE INTO {table}({names}) VALUES({', '.join('?' * len(keep))})"
                rows = ([row[i] for i in keep] for row in map(json.loads, f) if row)
                before = conn.total_changes
                conn.executemany(sql, rows)
                counts[table] = counts.get(table, 0) + conn.total_changes - before
        conn.commit()
        conn.execute("ANALYZE")   # 建表时统计的是空表；导入后重新统计，查询才会选对索引
    finally:
        conn.close()
    log(f"已从快照还原到 {target}：" + "，".join(f"{t} {n}" for t, n in counts.items()))
    return counts
