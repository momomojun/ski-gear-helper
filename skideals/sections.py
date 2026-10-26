"""滑雪主站与独立户外专区。共享商品沿用同一分类、分组键和价格历史。"""
import json
SKI_CATEGORIES = (
    "ski", "binding", "boot", "pole", "helmet", "goggle", "protection", "jacket", "pants", "suit",
    "midlayer", "baselayer", "glove", "sock", "facewear", "hat", "backpack", "bag", "avalanche", "skin", "tuning", "accessory",
)
OUTDOOR_CATEGORIES = (
    "hiking_shoe", "trail_shoe", "outdoor_jacket", "outdoor_pants", "outdoor_top",
    "midlayer", "baselayer", "glove", "sock", "hat", "facewear",
)
SHARED_LABELS = {"midlayer": "抓绒 / 保暖层", "baselayer": "保暖内衣", "sock": "袜子", "hat": "帽子", "facewear": "围脖 / 护脸"}


def category_sections(category: str) -> list[str]:
    return [section for section, cats in (("ski", SKI_CATEGORIES), ("outdoor", OUTDOOR_CATEGORIES)) if category in cats]


def sections_summary() -> list[dict]:
    from .categories import BY_ID
    return [
        {"id": "ski", "name": "滑雪", "default": "ski", "categories": list(SKI_CATEGORIES), "labels": {}},
        {"id": "outdoor", "name": "户外", "default": "hiking_shoe",
         "categories": [c for c in OUTDOOR_CATEGORIES if c in BY_ID], "labels": SHARED_LABELS},
    ]


def last_full_update(conn, section: str = "ski") -> str | None:
    """局部户外更新不能推迟滑雪自动更新；兼容未记录请求分类的旧抓取日志。"""
    required = set(SKI_CATEGORIES if section == "ski" else OUTDOOR_CATEGORIES)
    for row in conn.execute("SELECT finished_at, summary_json FROM crawl_runs WHERE status='done' ORDER BY id DESC LIMIT 100"):
        try:
            summary = json.loads(row["summary_json"] or "{}")
        except (ValueError, TypeError):
            continue
        if summary.get("retailer_ids"):
            continue  # 指定几家商店的局部更新不代表专区整体更新。
        if "categories" in summary:
            requested = summary["categories"]
            if requested is None or required <= set(requested):
                return row["finished_at"]
        else:
            observed = {c["cat"] for result in summary.get("results", []) for c in result.get("cats", [])}
            if required <= observed:
                return row["finished_at"]
    return None
