"""适配器注册表：retailers.toml 里的 adapter 字段 → (模块, 类名)。按需导入，某个适配器出错不影响其他网站。"""
from __future__ import annotations

import importlib

ADAPTERS: dict[str, tuple[str, str]] = {
    "shopify": ("shopify", "ShopifyAdapter"),
    "rei": ("rei", "ReiAdapter"),
    "christy": ("sfcc", "ChristySportsAdapter"),
    "thehouse": ("sfcc", "TheHouseAdapter"),
    "peterglenn": ("peterglenn", "PeterGlennAdapter"),
    "sunandski": ("sunandski", "SunAndSkiAdapter"),
    "buckmans": ("buckmans", "BuckmansAdapter"),
    "skiscom": ("skiscom", "SkisComAdapter"),
    "zappos": ("zappos", "ZapposAdapter"),
    "marmot": ("marmot", "MarmotAdapter"),
    "tactics": ("tactics", "TacticsAdapter"),
}


def get_adapter(cfg: dict, fetcher, log=print):
    name = cfg.get("adapter", "shopify")
    if name not in ADAPTERS:
        raise ValueError(f"未知的 adapter: {name}")
    module, cls = ADAPTERS[name]
    mod = importlib.import_module(f".{module}", __name__)
    return getattr(mod, cls)(cfg, fetcher, log)
