import json
import sqlite3

from skideals.sections import SKI_CATEGORIES, OUTDOOR_CATEGORIES, category_sections, last_full_update, sections_summary


def test_ski_is_first_and_default_with_distinct_outdoor_footwear():
    ski, outdoor = sections_summary()
    assert ski["id"] == "ski" and ski["default"] == "ski"
    assert outdoor["id"] == "outdoor" and outdoor["default"] == "hiking_shoe"
    assert len(SKI_CATEGORIES) == 22
    assert "boot" in ski["categories"] and "boot" not in outdoor["categories"]
    assert "trail_shoe" in outdoor["categories"] and "trail_shoe" not in ski["categories"]
    assert category_sections("midlayer") == ["ski", "outdoor"]
    assert category_sections("outdoor_jacket") == ["outdoor"]


def test_outdoor_refresh_does_not_reset_ski_full_update_time():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE crawl_runs(id INTEGER PRIMARY KEY,finished_at TEXT,status TEXT,summary_json TEXT)")
    conn.execute("INSERT INTO crawl_runs VALUES(1, '2026-10-16T12:00:00','done',?)",
                 (json.dumps({"categories": list(SKI_CATEGORIES)}),))
    conn.execute("INSERT INTO crawl_runs VALUES(2, '2026-10-18T12:00:00','done',?)",
                 (json.dumps({"categories": list(OUTDOOR_CATEGORIES)}),))
    assert last_full_update(conn, "ski") == "2026-10-16T12:00:00"
    assert last_full_update(conn, "outdoor") == "2026-10-18T12:00:00"
    conn.execute("INSERT INTO crawl_runs VALUES(4,'2026-10-18T14:00:00','done',?)",
                 (json.dumps({"categories": None, "retailer_ids": ["rei"]}),))
    assert last_full_update(conn, "ski") == "2026-10-16T12:00:00"
    conn.execute("INSERT INTO crawl_runs VALUES(5,'2026-10-18T13:00:00','done',?)",
                 (json.dumps({"results": [{"cats": [{"cat": c} for c in SKI_CATEGORIES]}]}),))
    assert last_full_update(conn, "ski") == "2026-10-18T13:00:00"
    assert last_full_update(conn, "outdoor") == "2026-10-18T12:00:00"


def test_api_refresh_defaults_to_ski_and_accepts_explicit_outdoor(monkeypatch):
    from skideals import server
    calls = []
    monkeypatch.setattr(server.crawl, "start_background", lambda ids, cats: calls.append((ids, cats)) or True)
    assert server.api_crawl({}) == {"started": True}
    assert calls[-1] == (None, list(SKI_CATEGORIES))
    assert server.api_crawl({"categories": list(OUTDOOR_CATEGORIES)}) == {"started": True}
    assert calls[-1] == (None, list(OUTDOOR_CATEGORIES))
