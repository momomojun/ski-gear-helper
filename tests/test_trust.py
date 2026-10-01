"""可信度检测里的纯规则部分（不联网）。"""
import pytest
from skideals.trust import _RISKY_TLDS, brand_impersonation, split_domain


def test_split_domain():
    assert split_domain("https://www.evo.com/collections/skis")[:2] == ("evo.com", "www.evo.com")
    assert split_domain("store.shopping.yahoo.co.jp")[0] == "yahoo.co.jp"
    assert split_domain("item.jd.com")[0] == "jd.com"


def test_brand_impersonation_flags_fake_outlets():
    assert brand_impersonation("atomic-outlet-sale.shop") == "Atomic"
    assert brand_impersonation("salomonskisusa.com") == "Salomon"
    assert brand_impersonation("rossignol-clearance.store") == "Rossignol"


def test_brand_impersonation_ignores_official_and_normal_shops():
    assert brand_impersonation("atomic.com") is None
    assert brand_impersonation("evo.com") is None
    assert brand_impersonation("coloradoskishop.com") is None
    assert brand_impersonation("skiessentials.com") is None


def test_risky_tlds():
    assert "shop" in _RISKY_TLDS and "top" in _RISKY_TLDS and "com" not in _RISKY_TLDS


def test_retailer_impersonation_and_history_gap():
    from skideals.trust import history_gap, retailer_impersonation
    assert retailer_impersonation("utahskigearonline.shop") is not None   # 真实案例
    assert retailer_impersonation("utahskigear.com") is None
    assert retailer_impersonation("evo.com") is None
    assert history_gap([2003, 2004, 2005, 2024, 2025, 2026]) == (2005, 2024)
    assert history_gap(list(range(2001, 2027))) is None


def test_compare_with_market_flags_half_price_clones(monkeypatch):
    from skideals import catalog, trust
    fam = {"k": "ski|atomic|100 bent|a|f", "b": "Atomic", "m": "Bent 100",
           "o": [{"p": 389.95, "cd": "new", "y": 2026}, {"p": 449.0, "cd": "new", "y": 2026}]}
    monkeypatch.setattr(catalog, "build", lambda category="ski", force=False: {"families": [fam] if category == "ski" else []})
    fake = [{"id": 1, "title": "Atomic Bent 100 Skis 2026", "vendor": "Atomic", "variants": [{"price": "119.00"}]},
            {"id": 2, "title": "Atomic Bent 100 Skis 2026", "vendor": "Atomic", "variants": [{"price": "129.00"}]}]
    r = trust.compare_with_market(fake)
    assert r["matched"] == 2 and r["too_cheap"] == 2
    normal = [{"id": 3, "title": "Atomic Bent 100 Skis 2026", "vendor": "Atomic", "variants": [{"price": "399.00"}]}]
    assert trust.compare_with_market(normal) == {"matched": 1, "too_cheap": 0, "examples": []}


def test_robots_parser_wildcards_and_crawl_delay():
    from skideals.net import Robots
    rb = Robots("User-agent: ClaudeBot\nCrawl-delay: 10\n\nUser-agent: *\nDisallow: */demandware.store*\n"
                "Disallow: /*search*\nAllow: /search-help$\nCrawl-delay: 2\n")
    assert not rb.allowed("/on/demandware.store/Sites-x/Search-UpdateGrid")
    assert not rb.allowed("/skis?search=1") and rb.allowed("/search-help") and rb.allowed("/skis/")
    assert rb.crawl_delay == 2.0


def test_brand_store_is_not_a_scam_signal():
    from skideals.trust import looks_like_brand_store as f
    assert f("spyder.com", {"top_vendor": "Spyder", "top_vendor_share": 0.97}, 31) == "Spyder"     # 官网清仓
    assert f("strafeouterwear.com", {"top_vendor": "Strafe", "top_vendor_share": 1.0}, 16) == "Strafe"
    assert f("spyder-sale-outlet.shop", {"top_vendor": "Spyder", "top_vendor_share": 1.0}, 0.3) is None  # 新域名
    assert f("bestskideals.com", {"top_vendor": "Spyder", "top_vendor_share": 1.0}, 8) is None           # 域名不是品牌
    assert f("ski.com", {"top_vendor": "Skida", "top_vendor_share": 1.0}, 20) is None


@pytest.mark.parametrize("domain,expected_real", [   # 2026-09 人工调查时发现的山寨域名
    ("strafeouterwear.us", "strafeouterwear.com"), ("flylowgear.us", "flylowgear.com"),
    ("campmorstore.com", "campmor.com"), ("utahskigearonline.shop", "utahskigear.com"),
])
def test_lookalike_domains_are_flagged(domain, expected_real):
    from skideals.trust import retailer_impersonation
    assert expected_real in (retailer_impersonation(domain) or "")


def test_real_domains_are_not_flagged():
    from skideals.trust import retailer_impersonation
    assert retailer_impersonation("campmor.com") is None and retailer_impersonation("strafeouterwear.com") is None
