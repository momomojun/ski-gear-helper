"""HTTP request and browser fallback contracts, without network or real sleeps."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from skideals import net


URL = 'https://shop.test/products'


def response(status=200, text='<html>商品</html>'):
    return SimpleNamespace(status_code=status, text=text, content=text.encode(),
                           url=URL, headers={'content-type': 'text/html; charset=utf-8'})


@pytest.fixture
def client(monkeypatch):
    session = Mock()
    session.get.return_value = response()
    monkeypatch.setattr(net.creq, 'Session', Mock(return_value=session))
    monkeypatch.setattr(net.time, 'sleep', Mock())
    monkeypatch.setattr(net.time, 'monotonic', lambda: 100.0)
    monkeypatch.setattr(net, 'is_browser_available', Mock(return_value=True))
    monkeypatch.setattr(net, 'fetch_with_browser', Mock(return_value=('<html>商品</html>', {'clearance': 'synthetic'})))
    return net.Fetcher(min_interval=0, retries=0, timeout=7), session


def test_html_navigation_headers_preserve_caller_overrides(client):
    fetcher, session = client
    headers = {'Sec-Fetch-Site': 'same-site', 'X-Test': 'value'}
    fetcher.get(URL, headers=headers, check_robots=False)
    sent = session.get.call_args.kwargs['headers']
    assert sent['Sec-Fetch-Dest'] == 'document'
    assert sent['Sec-Fetch-Mode'] == 'navigate'
    assert sent['Sec-Fetch-Site'] == 'same-site'
    assert sent['Upgrade-Insecure-Requests'] == '1'
    assert sent['Sec-Fetch-User'] == '?1'
    assert sent['X-Test'] == 'value'
    assert headers == {'Sec-Fetch-Site': 'same-site', 'X-Test': 'value'}


def test_json_headers_remove_navigation_upgrade_without_mutating_input(client):
    fetcher, session = client
    session.get.return_value.json = lambda: {'products': []}
    headers = {'Upgrade-Insecure-Requests': '1'}
    assert fetcher.get_json(URL, headers=headers, check_robots=False) == {'products': []}
    sent = session.get.call_args.kwargs['headers']
    assert sent['Sec-Fetch-Dest'] == 'empty'
    assert sent['Sec-Fetch-Mode'] == 'cors'
    assert sent['Sec-Fetch-Site'] == 'same-origin'
    assert 'Upgrade-Insecure-Requests' not in sent
    assert headers == {'Upgrade-Insecure-Requests': '1'}


@pytest.mark.parametrize('enabled,available', [(False, True), (True, False)])
def test_blocked_page_does_not_launch_unavailable_or_disabled_browser(client, enabled, available):
    fetcher, session = client
    fetcher.browser_fallback = enabled
    net.is_browser_available.return_value = available
    session.get.return_value = response(403, 'cf-chl challenge')
    with pytest.raises(net.BlockedError) as exc:
        fetcher.get(URL, check_robots=False)
    assert exc.value.url == URL and exc.value.status == 403
    net.fetch_with_browser.assert_not_called()
    assert fetcher.request_count == 1


@pytest.mark.parametrize('result', ['success', 'challenge', 'exception'])
def test_browser_result_is_validated_and_errors_keep_original_status(client, result):
    fetcher, session = client
    session.get.return_value = response(403, 'cf-chl challenge')
    if result == 'exception':
        net.fetch_with_browser.side_effect = RuntimeError('browser failed')
    elif result == 'challenge':
        net.fetch_with_browser.return_value = ('<html>px-captcha</html>', {})
    if result == 'success':
        assert fetcher.get_html(URL, check_robots=False) == '<html>商品</html>'
        session.cookies.set.assert_called_once_with('clearance', 'synthetic', domain='shop.test')
    else:
        with pytest.raises(net.BlockedError) as exc:
            fetcher.get(URL, check_robots=False)
        assert exc.value.status == 403
    net.fetch_with_browser.assert_called_once_with(URL, timeout=7)


def test_browser_json_response_supports_json_decoding(client):
    fetcher, session = client
    session.get.return_value = response(403, 'cf-chl challenge')
    net.fetch_with_browser.return_value = (json.dumps({'price': 120}), {})
    assert fetcher.get_json(URL, check_robots=False) == {'price': 120}


@pytest.mark.parametrize('status', [202, 401, 403])
@pytest.mark.parametrize('interval', [0, 4])
def test_blocked_robots_allows_page_with_conservative_cached_delay(client, status, interval):
    fetcher, session = client
    fetcher.min_interval = interval
    session.get.side_effect = [response(status, 'denied'), response(), response()]
    fetcher.get(URL)
    fetcher.get(URL)
    assert session.get.call_args_list[0].args == ('https://shop.test/robots.txt',)
    assert session.get.call_count == 3
    assert fetcher._state('shop.test')['robots'].crawl_delay == max(interval, 2.5)
    assert net.time.sleep.call_count == 2
    assert all(call.args[0] >= max(interval, 2.5) - 0.1 for call in net.time.sleep.call_args_list)


def test_robots_disallow_prevents_page_and_browser_requests(client):
    fetcher, session = client
    session.get.return_value = response(200, 'User-agent: *\nDisallow: /products')
    with pytest.raises(net.RobotsDisallowed):
        fetcher.get(URL)
    assert session.get.call_count == 1
    net.fetch_with_browser.assert_not_called()
