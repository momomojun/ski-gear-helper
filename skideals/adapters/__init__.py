"""适配器注册表：retailers.toml 里的 adapter 字段 → (模块, 类名)。按需导入，某个适配器出错不影响其他网站。"""
from __future__ import annotations

import importlib

ADAPTERS: dict[str, tuple[str, str]] = {
    "shopify": ("shopify", "ShopifyAdapter"),
    "scheels": ("scheels", "ScheelsAdapter"),
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
    "runningwarehouse": ("runningwarehouse", "RunningWarehouseAdapter"),
    "topo": ("topo", "TopoAdapter"),
    "danner": ("danner", "DannerAdapter"),
    "lightspeed": ("lightspeed", "LightspeedAdapter"),     # Lightspeed eCom 平台通用：Bentgate、Wasatch Touring
    "woocommerce": ("woocommerce", "WooCommerceAdapter"),  # WooCommerce Store API 通用：Mountain Shop
    "skimo": ("skimo", "SkimoAdapter"),
    "kuhl": ("kuhl", "KuhlAdapter"),
    "poc": ("poc", "PocAdapter"),
    "dynafit": ("dynafit", "DynafitAdapter"),
    "k2sports": ("k2sports", "K2SportsAdapter"),        # K2 Sports 平台：K2、Line、Völkl、Marker、Dalbello（robots 要求 20 秒间隔）
    "rossignol": ("sfcc_brand", "RossignolAdapter"),
    "dynastarlange": ("sfcc_brand", "DynastarLangeAdapter"),
    "giro": ("sfcc_brand", "GiroAdapter"),
}


def get_adapter(cfg: dict, fetcher, log=print):
    name = cfg.get("adapter", "shopify")
    if name not in ADAPTERS:
        raise ValueError(f"未知的 adapter: {name}")
    module, cls = ADAPTERS[name]
    mod = importlib.import_module(f".{module}", __name__)
    return getattr(mod, cls)(cfg, fetcher, log)
