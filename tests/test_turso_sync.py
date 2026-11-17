"""Sync boundaries and recovery using only in-memory SQLite databases."""
import sqlite3
from unittest.mock import Mock

import pytest

from skideals import turso


@pytest.fixture
def databases():
    with sqlite3.connect(':memory:') as local, sqlite3.connect(':memory:') as remote:
        for conn in (local, remote):
            conn.execute('CREATE TABLE prices (sku TEXT, day TEXT, price REAL, last_seen TEXT, PRIMARY KEY(day, sku))')
        local.executemany('INSERT INTO prices VALUES (?, ?, ?, ?)', [
            ('滑雪板', '2026-11-16', 100, 'first'),
            ('滑雪板', '2026-11-17', 90, 'first'),
            ('other', '2026-11-17', 80, 'first'),
        ])
        turso._ensure_state(local)
        local.commit()
        yield local, remote


def test_composite_key_incremental_changes_and_full_refresh(databases):
    local, remote = databases
    assert turso.push_table(remote, local, 'prices', ignore=('last_seen',)) == 3
    local.execute("UPDATE prices SET last_seen='later'")
    assert turso.push_table(remote, local, 'prices', ignore=('last_seen',)) == 0
    local.execute("UPDATE prices SET price=75 WHERE sku='滑雪板' AND day='2026-11-17'")
    assert turso.push_table(remote, local, 'prices', ignore=('last_seen',)) == 1
    assert remote.execute('SELECT day, price FROM prices WHERE sku=? ORDER BY day', ('滑雪板',)).fetchall() == [
        ('2026-11-16', 100), ('2026-11-17', 75)]
    assert turso.push_table(remote, local, 'prices', ignore=('last_seen',), full=True) == 3
    assert remote.execute('SELECT COUNT(*) FROM prices').fetchone()[0] == 3


@pytest.mark.parametrize('stage', ['execute', 'commit'])
def test_failed_chunk_is_not_checkpointed_and_retry_only_sends_remaining(databases, monkeypatch, stage):
    local, remote = databases
    monkeypatch.setattr(turso, 'MAX_PARAMS', 4)  # One row per chunk.
    failing = Mock(wraps=remote)
    if stage == 'execute':
        # Forward actual SQL for the successful first chunk.
        calls = 0

        def execute(sql, params):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('connection lost')
            return remote.execute(sql, params)

        failing.execute.side_effect = execute
    else:
        calls = 0

        def commit():
            nonlocal calls
            calls += 1
            if calls == 2:
                remote.rollback()
                raise OSError('connection lost')
            remote.commit()

        failing.commit.side_effect = commit
    with pytest.raises(OSError, match='connection lost'):
        turso.push_table(failing, local, 'prices')
    assert local.execute('SELECT COUNT(*) FROM turso_sync').fetchone()[0] == 1
    assert remote.execute('SELECT COUNT(*) FROM prices').fetchone()[0] == 1
    assert turso.push_table(remote, local, 'prices') == 2
    assert remote.execute('SELECT COUNT(*) FROM prices').fetchone()[0] == 3
    assert turso.push_table(remote, local, 'prices') == 0


def test_chunks_byte_boundary_and_single_oversized_row(monkeypatch):
    monkeypatch.setattr(turso, 'MAX_BYTES', 10)
    rows = [('12345',), (b'12345',), ('x',), ('oversized row',), ('end',)]
    assert list(turso._chunks(rows, ['value'])) == [rows[:2], rows[2:3], rows[3:4], rows[4:]]
    assert list(turso._chunks([], ['value'])) == []


def test_primary_key_only_upsert_is_idempotent():
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE keys (id TEXT PRIMARY KEY)')
        sql = turso.upsert_sql('keys', ['id'], ['id'], 2)
        conn.execute(sql, ['a', 'b'])
        conn.execute(sql, ['a', 'b'])
        assert conn.execute('SELECT id FROM keys ORDER BY id').fetchall() == [('a',), ('b',)]


@pytest.mark.parametrize('url,token,expected', [('', '', False), ('libsql://test', '', False),
                                               ('', 'synthetic', False), ('libsql://test', 'synthetic', True)])
def test_configured_requires_both_environment_values(monkeypatch, url, token, expected):
    monkeypatch.setenv('TURSO_DATABASE_URL', url)
    monkeypatch.setenv('TURSO_AUTH_TOKEN', token)
    assert turso.configured() is expected
