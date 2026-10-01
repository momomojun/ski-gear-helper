"""数据质量自检：每次抓取后跑一遍，列出可能分错类、品牌有问题、价格异常的商品。

用法：python -m skideals audit [分类…]      （结果同时写到 data/audit.txt）

检查项：
1. 分类可疑：标题里的强关键词指向别的分类（而且不是兼容分类），例如“头盔”分类里标题写着 Goggles
2. 品牌：没有品牌、品牌名全大写/全小写、同一个品牌多种写法（按字母数字归一后相同）
3. 价格异常：远低于同分类中位数（硬货 < 中位数的 12%，服装 < 8%）——常见原因是配件/零件混进来了
4. 每个分类每家网站的商品数（一眼看出哪家网站某个分类抓漏了或抓多了）
"""
from __future__ import annotations

import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from . import db
from .categories import BY_ID, CATEGORIES, assign, classify
from .normalize import slug

_HARD = {"ski", "binding", "boot", "pole", "helmet", "goggle", "backpack", "avalanche", "skin"}


def run(categories: list[str] | None = None, out_path: Path | None = None, limit: int = 12) -> str:
    cats = categories or [c.id for c in CATEGORIES]
    lines: list[str] = []
    with db.connect() as conn:
        rows = conn.execute("SELECT l.retailer_id, l.category, l.title, l.brand, l.vendor, l.price, l.meta FROM listings l "
                            "JOIN retailers r ON r.id = l.retailer_id WHERE l.active = 1 AND r.enabled = 1").fetchall()
    by_cat: dict[str, list] = defaultdict(list)
    for r in rows:
        by_cat[r["category"] or "ski"].append(r)

    lines.append(f"== 总览：{len(rows)} 个在售商品")
    brand_variants: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r["brand"]:
            brand_variants[slug(r["brand"])][r["brand"]] += 1
    multi = {s: v for s, v in brand_variants.items() if len(v) > 1}
    no_brand = Counter(r["category"] for r in rows if not r["brand"])
    lines.append(f"   没有品牌：{sum(no_brand.values())} 个（" + "，".join(f"{BY_ID[c].name.split('（')[0]} {n}"
                                                             for c, n in no_brand.most_common(8)) + "）")
    lines.append(f"   同一品牌多种写法：{len(multi)} 组" + ("：" + "；".join(" / ".join(v) for v in list(multi.values())[:10])
                                                    if multi else ""))
    odd = sorted({r["brand"] for r in rows if r["brand"] and len(re.sub(r"[^A-Za-z]", "", r["brand"])) > 4
                  and (r["brand"].isupper() or r["brand"].islower())})
    lines.append(f"   全大写/全小写的品牌名：{len(odd)} 个" + (("：" + "、".join(odd[:20])) if odd else ""))

    for cid in cats:
        items = by_cat.get(cid, [])
        if not items:
            continue
        name = BY_ID[cid].name
        lines.append(f"\n== {name}（{cid}）：{len(items)} 个")
        per_site = Counter(r["retailer_id"] for r in items)
        lines.append("   各网站：" + " · ".join(f"{k} {n}" for k, n in per_site.most_common()))
        wrong = []
        for r in items:
            if cid == "ski":   # 双板页用严格排除表，这里只看标题是不是明显属于别的分类（套装算双板）
                found = classify(r["title"])
                found = None if found in (None, "ski") else found
            else:              # 其他分类：用和抓取时同样的判定（按来源分类页；零件、兼容分类都考虑在内）
                m = json.loads(r["meta"]) if r["meta"] else {}
                found = assign(m.get("src") or cid, r["title"], m.get("pt"), m.get("tags"), m.get("opts"),
                               vendor=r["vendor"])[0]
                found = None if found == cid else (found or "不要")
            if found:
                wrong.append((found, r))
        if wrong:
            lines.append(f"   标题指向别的分类：{len(wrong)} 个")
            for found, r in wrong[:limit]:
                lines.append(f"      → {found:10} {r['retailer_id']:16} {r['title'][:80]}")
        prices = [r["price"] for r in items if r["price"]]
        if len(prices) >= 20:
            med = statistics.median(prices)
            floor = med * (0.12 if cid in _HARD else 0.08)
            cheap = sorted((r for r in items if r["price"] and r["price"] < floor), key=lambda r: r["price"])
            if cheap:
                lines.append(f"   价格远低于中位数 ${med:.0f}：{len(cheap)} 个")
                for r in cheap[:limit]:
                    lines.append(f"      ${r['price']:<8} {r['retailer_id']:16} {r['title'][:80]}")
    text = "\n".join(lines)
    out = out_path or (db.DB_PATH.parent / "audit.txt")
    try:
        out.write_text(text, encoding="utf-8")
    except OSError:
        pass
    return text
