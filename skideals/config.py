"""路径与全局设置。"""
from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "skideals.db"
CONFIG_DIR = ROOT / "config"
WEB_DIR = ROOT / "web"

HOST = "127.0.0.1"
PORT = 8765

# 默认设置（可在网页「设置」里修改，保存在数据库 settings 表）
DEFAULT_SETTINGS = {
    "sales_tax_pct": 6.25,        # 美国销售税（马萨诸塞州 6.25%），只用于估算到手价
    "notify_desktop": True,       # 降价时弹 Windows 通知
    "intl_cache_days": 3,         # 日本价格缓存天数
    "crawl_workers": 6,           # 同时抓取的网站数（同一网站内部始终串行、限速）
    "auto_crawl_hours": 24,       # 网页开着时，距上次抓取超过这么多小时就自动更新（0 = 关闭）
}


def load_retailers() -> list[dict]:
    """读取零售商注册表，并合并 config/categories.toml 里每家店“双板以外各分类”的抓取配置。

    retailers.toml   —— 零售商本身（名称、网址、可信等级…）+ 双板的抓取配置（历史原因写在零售商级别）
    categories.toml  —— [<零售商id>.<分类>] 表：这家店这个分类从哪抓（大多由 `python -m skideals discover` 生成）
                        写 [<零售商id>.ski] enabled = false 可以只抓装备、不抓双板
    """
    with open(CONFIG_DIR / "retailers.toml", "rb") as f:
        data = tomllib.load(f)
    gear: dict = {}
    if (CONFIG_DIR / "categories.toml").exists():
        with open(CONFIG_DIR / "categories.toml", "rb") as f:
            gear = tomllib.load(f)
    out = []
    for r in data.get("retailers", []):
        r.setdefault("enabled", True)
        r.setdefault("country", "US")
        r.setdefault("currency", "USD")
        r.setdefault("tier", "B")
        cats = {"ski": {}}  # 注意：键名不能叫 categories —— 有些适配器自己用 cfg["categories"] 表示分类页路径
        cats.update(r.pop("by_category", {}) or {})
        for cat, ccfg in (gear.get(r["id"]) or {}).items():
            cats[cat] = {**cats.get(cat, {}), **ccfg}
        if r.get("adapter", "shopify") == "shopify":
            for cat, ccfg in cats.items():  # Shopify：每个分类必须有自己的 hints，否则会沿用双板的 hints 去多抓双板分类
                if cat != "ski":
                    ccfg.setdefault("hints", {})
        r["by_category"] = cats
        out.append(r)
    return out
