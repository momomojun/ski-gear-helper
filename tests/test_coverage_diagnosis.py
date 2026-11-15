import json
import sqlite3

from scripts import diagnose_coverage as coverage


def test_zero_is_unverified_not_proof_of_empty_or_parser_failure():
    assert coverage.diagnosis('ok', 0) == '零结果待核查'
    assert coverage.diagnosis(None, None) == '未验证'
    assert coverage.diagnosis('ok', 5) == '有产出（未核验完整性）'


def test_classify_explicit_errors_and_partial():
    assert coverage.diagnosis('error', 0, 'FetchError: 404 https://example.com') == '入口404'
    assert coverage.diagnosis('blocked', 0) == '反爬拦截'
    assert coverage.diagnosis('robots', 0) == 'robots限制'
    assert coverage.diagnosis('partial', 4) == '部分覆盖'


def test_baseline_excludes_partial_site_and_running_updates():
    run = {'status': 'done', 'summary': {'results': [{'id': 'shop'}], 'categories': list(coverage.SKI_CATEGORIES)}}
    assert coverage.full_ski_run(run)
    assert not coverage.full_ski_run({**run, 'status': 'running'})
    assert not coverage.full_ski_run({'status': 'done', 'summary': {**run['summary'], 'retailer_ids': ['shop']}})
    assert not coverage.full_ski_run({'status': 'done', 'summary': {**run['summary'], 'categories': ['ski']}})


def test_collect_keeps_baseline_current_and_inventory_separate(tmp_path, monkeypatch):
    path = tmp_path / 'test.db'
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE retailers(id TEXT, cats_json TEXT);
            CREATE TABLE crawl_runs(id INTEGER, started_at TEXT, finished_at TEXT, status TEXT, summary_json TEXT);
            CREATE TABLE listings(retailer_id TEXT, category TEXT, active INTEGER, available INTEGER, sizes_json TEXT, last_seen TEXT);
        ''')
        conn.execute('INSERT INTO retailers VALUES (?,?)', ('shop', json.dumps({'ski': {'status': 'blocked', 'n': 0, 'at': '2026-11-14T12:00:00'}})))
        summary = {'categories': None, 'results': [{'id': 'shop', 'cats': [{'cat': 'ski', 'status': 'ok', 'n': 3}]}]}
        conn.execute('INSERT INTO crawl_runs VALUES (1,?,?,?,?)', ('2026-11-01T10:00:00', '2026-11-01T11:00:00', 'done', json.dumps(summary)))
        conn.execute("INSERT INTO crawl_runs VALUES (2,'2026-11-14T11:00:00',NULL,'running',NULL)")
        conn.executemany('INSERT INTO listings VALUES (?,?,?,?,?,?)', [('shop', 'ski', 1, 1, '[]', '2026-11-01T11:00:00'), ('shop', 'boot', 1, 1, '[]', '2026-11-14T12:00:00')])
    before = path.read_bytes()
    monkeypatch.setattr(coverage, 'load_retailers', lambda: [{'id': 'shop', 'name': 'Shop', 'enabled': True, 'country': 'US', 'by_category': {'ski': {}, 'boot': {'enabled': False}}}])
    report = coverage.collect(path, tmp_path / 'missing.log')
    row = next(r for r in report['rows'] if r['category'] == 'ski')
    assert report['baseline']['id'] == 1
    assert row['baseline']['n'] == 3
    assert row['current_diagnosis'] == '反爬拦截'
    assert row['active'] == row['older_than_7_days'] == 1
    assert row['last_recorded_success_with_output'] == '2026-11-01T11:00:00'
    assert not next(r for r in report['rows'] if r['category'] == 'boot')['enabled']
    assert path.read_bytes() == before
    summary_text, details = coverage.render(report)
    assert '#1' in summary_text and '反爬拦截' in details
