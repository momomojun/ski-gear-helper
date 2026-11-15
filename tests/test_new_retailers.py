"""验证第一、第二、第三梯队新接入网站的配置与适配器完整性。"""
import pytest
from skideals.config import load_retailers
from skideals.adapters import get_adapter, ADAPTERS
from skideals.net import Fetcher

NEW_RETAILERS = [
    # 第一梯队：滑雪大牌硬件
    "salomon", "blizzard-tecnica", "nordica", "head", "elan", "fischer",
    # 第二梯队：顶级户外/高山/越野/背包
    "mammut", "hoka", "osprey", "norrona", "merrell", "altra", "gregory", "bca",
    # 第三梯队：特卖与欧洲名站
    "sierra", "curated", "ekosport", "glisshop", "sportconrad",
]

# 品牌展示型无自营电商或防爬严格站
DISABLED_NEW_RETAILERS = {"blizzard-tecnica", "nordica", "sierra", "mammut", "hoka", "curated", "ekosport"}


def test_new_retailers_exist_and_enabled():
    retailers = {r["id"]: r for r in load_retailers()}
    for rid in NEW_RETAILERS:
        assert rid in retailers, f"Retailer {rid} not found in load_retailers()"
        cfg = retailers[rid]
        if rid in DISABLED_NEW_RETAILERS:
            assert cfg.get("enabled") is False
        else:
            assert cfg.get("enabled", True) is True, f"Retailer {rid} should be enabled"
        assert cfg.get("url", "").startswith("https://"), f"Retailer {rid} invalid URL"
        assert cfg.get("adapter") in ADAPTERS, f"Retailer {rid} invalid adapter {cfg.get('adapter')}"
        assert cfg.get("currency") in ("USD", "EUR", "CAD", "JPY", "CNY", "GBP")


def test_new_retailers_categories_defined():
    retailers = {r["id"]: r for r in load_retailers()}
    for rid in NEW_RETAILERS:
        by_cat = retailers[rid].get("by_category", {})
        # 至少定义了一个可用或禁用的分类
        assert len(by_cat) > 0, f"Retailer {rid} has empty category map"


def test_new_retailers_adapters_instantiate():
    retailers = {r["id"]: r for r in load_retailers()}
    fetcher = Fetcher()
    for rid in NEW_RETAILERS:
        cfg = retailers[rid]
        adapter = get_adapter(cfg, fetcher, log=lambda *args: None)
        assert adapter is not None
        assert adapter.rid == rid
        assert adapter.base_url.rstrip("/") == cfg["url"].rstrip("/")
