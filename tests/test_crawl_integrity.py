"""抓取失败/截断不能丢弃已取得的商品，或误下架旧商品。全部使用临时库和假页面。"""
import json

import pytest

from skideals import crawl, db
from skideals.adapters.base import Adapter
from skideals.adapters.schema_org import SchemaOrgAdapter
from skideals.adapters.outdoor_brand import OutdoorBrandAdapter
from skideals.adapters.backcountry import BackcountryAdapter
from skideals.models import RawProduct
from skideals.net import BlockedError, FetchError, RobotsDisallowed
from skideals.normalize import normalize


CFG = {'id': 'shop', 'name': 'Shop', 'url': 'https://shop.test', 'category': 'ski', 'categories': ['/skis'], 'pages': ['/skis']}


def product(pid='new'):
    return RawProduct(retailer_id='shop', external_id=pid, title=f'Atomic Bent 100 Skis {pid}',
                      price=300, available=True, url=f'https://shop.test/{pid}.html', category='ski')


def html(pid='new'):
    return '<script type="application/ld+json">' + json.dumps({
        '@type': 'Product', 'sku': pid, 'name': f'Atomic Bent 100 Skis {pid}',
        'url': f'https://shop.test/{pid}.html', 'offers': {'@type': 'Offer', 'price': 300}
    }) + '</script>'


class Fetcher:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get_html(self, url, **kwargs):
        self.calls.append((url, kwargs.get('params')))
        result = self.pages[url]
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'crawl.db')
    monkeypatch.setattr(db, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(crawl, '_log', lambda _: None)
    db.init()
    with db.connect() as conn:
        conn.execute("INSERT INTO retailers(id,name,enabled) VALUES('shop','Shop',1)")
        db.save_listing(conn, normalize(product('old')), '2020-01-01T00:00:00', '2020-01-01')


def stored():
    with db.connect() as conn:
        return {r['external_id']: r['active'] for r in conn.execute('SELECT external_id,active FROM listings')}


def run_adapter(monkeypatch, adapter):
    monkeypatch.setattr(crawl, 'get_adapter', lambda *args: adapter)
    return crawl.crawl_category(CFG, 'ski', None)


@pytest.mark.parametrize('error,status', [
    (BlockedError('https://shop.test/skis', 403), 'blocked'),
    (RobotsDisallowed('https://shop.test/skis'), 'robots'),
    (FetchError('https://shop.test/skis', 404), 'error'),
    (ValueError('malformed product data'), 'error'),
])
@pytest.mark.parametrize('has_output', [False, True])
def test_generator_failure_preserves_prior_output_and_old_stock(isolated_db, monkeypatch, error, status, has_output):
    class Broken(Adapter):
        def fetch(self):
            if has_output:
                yield product()
            raise error
    result = run_adapter(monkeypatch, Broken(CFG, None))
    assert result['status'] == ('partial' if has_output else status)
    assert stored() == ({'old': 1, 'new': 1} if has_output else {'old': 1})
    with db.connect() as conn:
        cat = json.loads(conn.execute('SELECT cats_json FROM retailers').fetchone()[0])['ski']
        assert cat['status'] == result['status']
        assert cat['n'] == int(has_output)


@pytest.mark.parametrize('mode,status,old_active', [('complete', 'ok', 0), ('partial', 'partial', 1), ('empty', 'empty', 1)])
def test_complete_partial_and_empty_have_distinct_deactivation_rules(isolated_db, monkeypatch, mode, status, old_active):
    class Result(Adapter):
        def fetch(self):
            if mode == 'partial':
                self.mark_incomplete('test budget')
            if mode != 'empty':
                yield product()
    result = run_adapter(monkeypatch, Result(CFG, None))
    assert result['status'] == status
    assert stored()['old'] == old_active


@pytest.mark.parametrize('adapter_cls', [SchemaOrgAdapter, OutdoorBrandAdapter, BackcountryAdapter])
def test_adapter_request_failure_is_visible_to_scheduler(isolated_db, monkeypatch, adapter_cls):
    adapter = adapter_cls(CFG, Fetcher({'https://shop.test/skis': FetchError('https://shop.test/skis', 404)}), log=lambda _: None)
    result = run_adapter(monkeypatch, adapter)
    assert result['status'] == 'error'
    assert '404' in result['msg']
    assert stored() == {'old': 1}


@pytest.mark.parametrize('adapter_cls', [SchemaOrgAdapter, OutdoorBrandAdapter, BackcountryAdapter])
def test_adapter_continues_other_entry_after_block(isolated_db, monkeypatch, adapter_cls):
    cfg = {**CFG, 'pages': ['/bad', '/good'], 'categories': ['/bad', '/good']}
    adapter = adapter_cls(cfg, Fetcher({'https://shop.test/bad': BlockedError('https://shop.test/bad', 403),
                                       'https://shop.test/good': html()}), log=lambda _: None)
    result = run_adapter(monkeypatch, adapter)
    assert result['status'] == 'partial'
    assert stored() == {'old': 1, 'new': 1}
    assert '反爬' in result['msg']


@pytest.mark.parametrize('adapter_cls', [SchemaOrgAdapter, OutdoorBrandAdapter])
def test_detail_failure_keeps_other_detail(isolated_db, monkeypatch, adapter_cls):
    pages = {'https://shop.test/skis': '<a href="/a.html">A</a><a href="/b.html">B</a>',
             'https://shop.test/a.html': FetchError('https://shop.test/a.html', 404),
             'https://shop.test/b.html': html()}
    result = run_adapter(monkeypatch, adapter_cls(CFG, Fetcher(pages), log=lambda _: None))
    assert result['status'] == 'partial' and '404' in result['msg']
    assert stored() == {'old': 1, 'new': 1}


@pytest.mark.parametrize('adapter_cls', [SchemaOrgAdapter, OutdoorBrandAdapter])
def test_detail_budget_is_deterministic_and_preserves_old_stock(isolated_db, monkeypatch, adapter_cls):
    pages = {'https://shop.test/skis': '<a href="/b.html">B</a><a href="/a.html">A</a>',
             'https://shop.test/a.html': html('a')}
    fetcher = Fetcher(pages)
    adapter = adapter_cls({**CFG, 'max_items': 1, 'max_items_per_cat': 1}, fetcher, log=lambda _: None)
    result = run_adapter(monkeypatch, adapter)
    assert result['status'] == 'partial' and '预算 1' in result['msg']
    assert len(fetcher.calls) == 2
    assert stored() == {'old': 1, 'a': 1}


def test_backcountry_page_cap_preserves_old_stock(isolated_db, monkeypatch):
    fetcher = Fetcher({'https://shop.test/skis': ''.join(html(str(i)) for i in range(12))})
    result = run_adapter(monkeypatch, BackcountryAdapter({**CFG, 'max_pages': 1}, fetcher, log=lambda _: None))
    assert result['status'] == 'partial' and '页预算 1' in result['msg']
    assert stored()['old'] == 1 and len(stored()) == 13


def test_retailer_partial_aggregation_after_first_category_failed(isolated_db, monkeypatch):
    answers = iter([{'cat': 'ski', 'status': 'error', 'n': 0, 'msg': '404', 'meta': None},
                    {'cat': 'boot', 'status': 'partial', 'n': 1, 'msg': 'budget', 'meta': None}])
    monkeypatch.setattr(crawl, 'crawl_category', lambda *args: next(answers))
    monkeypatch.setattr(crawl, '_set', lambda *args, **kwargs: None)
    result = crawl.crawl_one({**CFG, 'by_category': {'ski': {}, 'boot': {}}}, None)
    assert result['status'] == 'partial'
    with db.connect() as conn:
        assert conn.execute('SELECT last_status FROM retailers').fetchone()[0] == 'partial'
