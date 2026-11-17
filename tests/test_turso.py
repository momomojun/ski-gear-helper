"""push-turso：用 libsql 打开一个本地文件当“云端”，测增量推送（不联网、不占 Turso 额度）。"""
import sqlite3

import pytest

libsql = pytest.importorskip("libsql")

from skideals import db, turso  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "local.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init()
    remote_path = str(tmp_path / "remote.db")
    monkeypatch.setattr(turso, "connect_remote", lambda: libsql.connect(remote_path))
    with db.connect() as conn:
        conn.execute("INSERT INTO retailers(id, name, enabled) VALUES ('evo', 'evo', 1)")
        for i in range(1, 301):
            conn.execute("INSERT INTO listings(id, retailer_id, external_id, title, price, last_seen, category, meta) "
                         "VALUES (?, 'evo', ?, ?, ?, '2026-11-12T10:00:00', 'ski', ?)",
                         (i, f"e{i}", f"Ski {i} – 中文 ✓", 100.0 + i, '{"src": "ski"}'))
            conn.execute("INSERT INTO price_history(listing_id, day, price) VALUES (?, '2026-11-12', ?)", (i, 100.0 + i))
        conn.execute("INSERT INTO watchlist(model_key, label) VALUES ('k', '私人关注')")
    return tmp_path / "local.db", remote_path


def _remote(path):
    return sqlite3.connect(path)


def test_first_push_uploads_everything_but_not_private_tables(env):
    local, remote = env
    counts = turso.push(db_path=local, log=lambda *a: None)
    assert counts["listings"] == 300 and counts["price_history"] == 300 and counts["retailers"] == 1
    r = _remote(remote)
    assert r.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 300
    assert r.execute("SELECT title FROM listings WHERE id=7").fetchone()[0] == "Ski 7 – 中文 ✓"
    assert r.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 0   # 服务器负责的表：只建表不推数据
    assert r.execute("SELECT value FROM sync_meta WHERE key='data_day'").fetchone()[0] == "2026-11-12"


def test_incremental_push_skips_last_seen_only_changes(env):
    local, remote = env
    turso.push(db_path=local, log=lambda *a: None)
    with sqlite3.connect(local) as c:
        c.execute("UPDATE listings SET last_seen='2026-11-13T10:00:00'")          # 每天都会变，服务器用不到
        c.execute("UPDATE listings SET price=1.0, last_seen='2026-11-13T10:00:00' WHERE id IN (3, 4)")
        c.execute("INSERT INTO price_history(listing_id, day, price) VALUES (3, '2026-11-13', 1.0)")
    counts = turso.push(db_path=local, log=lambda *a: None)
    assert counts["listings"] == 2 and counts["price_history"] == 1 and counts["retailers"] == 0
    r = _remote(remote)
    assert r.execute("SELECT price FROM listings WHERE id=3").fetchone()[0] == 1.0
    assert r.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == 301
    # 没推送的行，云端的 last_seen 还是旧的（预期行为）；有变化的行带上了新的
    assert r.execute("SELECT last_seen FROM listings WHERE id=3").fetchone()[0] == "2026-11-13T10:00:00"


def test_full_push_is_idempotent(env):
    local, remote = env
    turso.push(db_path=local, log=lambda *a: None)
    turso.push(full=True, db_path=local, log=lambda *a: None)
    assert _remote(remote).execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 300


def test_chunks_respect_param_limit():
    rows = [tuple(range(36))] * 1000
    chunks = list(turso._chunks(rows, [f"c{i}" for i in range(36)]))
    assert sum(len(c) for c in chunks) == 1000
    assert all(len(c) * 36 <= turso.MAX_PARAMS for c in chunks)
