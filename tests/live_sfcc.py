"""实网测试：用真实的 Fetcher 跑 SFCC 适配器（Christy Sports 全部分类 / The House 双板），按分类打印统计和样例。

用法（在项目根目录）：
    .venv/Scripts/python.exe tests/live_sfcc.py                          # Christy，全部已配置的分类
    .venv/Scripts/python.exe tests/live_sfcc.py --category jacket        # 只跑某些分类（可重复，或逗号分隔）
    .venv/Scripts/python.exe tests/live_sfcc.py thehouse                 # The House（只支持双板；默认停用的网站）
    .venv/Scripts/python.exe tests/live_sfcc.py --details 5 --verbose --dump out.json

每个分类的 cfg 和调度器的合并方式一样：config/retailers.toml 里该零售商的配置（双板配置就在这里）
+ config/categories.toml 里的 [<零售商id>.<分类>] 表 + category。配置文件只读不写；
categories.toml 不存在（或没有这个零售商的表）时，改用适配器内置的默认表（CHRISTY_CATEGORY_DEFAULTS）。
不是 pytest 用例（文件名不以 test_ 开头），不会被自动收集；会真的访问网站，请勿频繁运行。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
from collections import Counter
from dataclasses import asdict

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from skideals.adapters.sfcc import ChristySportsAdapter, TheHouseAdapter  # noqa: E402
from skideals.net import BlockedError, Fetcher  # noqa: E402

# 配置文件里找不到这个零售商时用的兜底配置
SITES = {
    "christy": (ChristySportsAdapter, {"id": "christy", "name": "Christy Sports",
                                       "url": "https://www.christysports.com", "page_size": 150}),
    "thehouse": (TheHouseAdapter, {"id": "thehouse", "name": "The House",
                                   "url": "https://www.the-house.com", "listing_source": "algolia"}),
}
REQUEST_BUDGET = 400   # 整站（全部分类）一次抓取的请求数上限目标


def _read_toml(name: str) -> dict:
    path = os.path.join(ROOT, "config", name)
    if not os.path.exists(path):
        return {}
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def load_site(key: str) -> tuple[dict, dict, str]:
    """返回 (零售商级配置, {分类: [<id>.<分类>] 表}, 配置来源说明)。"""
    retailer = next((r for r in _read_toml("retailers.toml").get("retailers", []) if r.get("id") == key), None)
    tables = {k: v for k, v in (_read_toml("categories.toml").get(key) or {}).items() if isinstance(v, dict)}
    src = ("config/retailers.toml" if retailer else "built-in retailer cfg") + \
          (" + config/categories.toml" if tables else " + built-in category defaults")
    return retailer or SITES[key][1], tables, src


def category_cfg(cls, retailer: dict, tables: dict, cat: str) -> dict:
    """和调度器一样合并：零售商级 + [<id>.<分类>] + category。没有配置文件时用内置默认表。"""
    table = tables.get(cat)
    if table is None and not tables and cat != "ski":
        table = cls.CATEGORY_DEFAULTS.get(cat, {})
    return {**retailer, **(table or {}), "category": cat}


def pct(a: int, b: int) -> str:
    return f"{100.0 * a / b:.0f}%" if b else "-"


def money(x) -> str:
    return f"${x:,.2f}" if x is not None else "?"


def size_summary(p, limit: int = 8) -> str:
    if not p.sizes:
        return "-"
    parts = [f"{s.cm or s.label}{'' if s.available else '(x)'}" for s in p.sizes]
    return " ".join(parts[:limit]) + (" …" if len(parts) > limit else "")


def pick_samples(items: list, n: int = 3) -> list:
    """样例：优先“打折且有尺码”的，再补一个不同性别的、一个原价的。"""
    groups = [[p for p in items if p.compare_at and p.sizes],
              [p for p in items if p.gender_hint in ("women", "kids")],
              [p for p in items if not p.compare_at], items]
    out, seen = [], set()
    for g, quota in zip(groups, (1, 1, 1, n)):
        k = 0
        for p in g:
            if len(out) >= n or k >= quota:
                break
            if p.external_id not in seen:
                out.append(p)
                seen.add(p.external_id)
                k += 1
    return out


def run_category(cls, cfg: dict, cat: str, fetcher: Fetcher, details: int | None, verbose: bool) -> dict:
    cfg = dict(cfg)
    if details is not None:
        cfg["max_detail_pages"] = details
    adapter = cls(cfg, fetcher, log=(print if verbose else lambda s: None))
    r0, t0 = fetcher.request_count, time.time()
    try:
        items = list(adapter.fetch())
    except BlockedError as e:
        print(f"\n--- {cat}: BLOCKED by the site's anti-bot protection: {e}")
        return {"cat": cat, "n": 0, "sec": time.time() - t0, "req": fetcher.request_count - r0, "blocked": True,
                "items": []}
    sec, req = time.time() - t0, fetcher.request_count - r0
    st = adapter.stats
    n = len(items)
    disc = [p for p in items if p.compare_at]
    sized = [p for p in items if p.sizes]
    genders = dict(Counter(p.gender_hint for p in items).most_common())
    print(f"\n--- {cat}: {n} products | {sec:.1f} s | {req} requests (listing {st['list_requests']}, "
          f"product pages {st['detail_requests']}, max {adapter._detail_limit()})")
    print(f"    sizes {len(sized)} ({pct(len(sized), n)}); discounted {len(disc)} ({pct(len(disc), n)}); "
          f"discounted with sizes {sum(1 for p in disc if p.sizes)}/{len(disc)}")
    print(f"    gender {genders}; condition {dict(Counter(p.condition_hint for p in items))}; "
          f"skipped {st['skipped']}")
    missing = [k for k, v in (("vendor", sum(1 for p in items if not p.vendor)),
                              ("image", sum(1 for p in items if not p.image)),
                              ("price", sum(1 for p in items if not p.price))) if v]
    if missing:
        print(f"    missing fields: {missing}")
    if any(p.category != cat for p in items):
        print(f"    !! {sum(1 for p in items if p.category != cat)} products have the wrong category field")
    if st["blocked"]:
        print(f"    !! BLOCKED during the crawl (partial results): {st['errors'][-1]}")
    elif st["errors"]:
        print(f"    errors: {st['errors'][:3]}")
    for p in pick_samples(items):
        was = f" (was {money(p.compare_at)})" if p.compare_at else ""
        print(f"    - {p.title} | {money(p.price)}{was} | sizes: {size_summary(p)} | "
              f"gender={p.gender_hint} | cond={p.condition_hint}")
        print(f"      {p.url}")
    if verbose:
        for t in st["skipped_titles"]:
            print(f"      skip: {t}")
    return {"cat": cat, "n": n, "sec": sec, "req": req, "sized": pct(len(sized), n), "disc": pct(len(disc), n),
            "genders": genders, "items": items, "blocked": bool(st["blocked"])}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sites", nargs="*", help=f"any of {', '.join(SITES)} (default: christy)")
    ap.add_argument("--category", action="append", default=[],
                    help="category id(s) to crawl, repeatable or comma-separated (default: all configured)")
    ap.add_argument("--details", type=int, default=None, help="override max_detail_pages for every category")
    ap.add_argument("--verbose", action="store_true", help="adapter logs + skipped titles")
    ap.add_argument("--dump", help="write all RawProducts as JSON to this path")
    args = ap.parse_args()
    sites = args.sites or ["christy"]
    unknown = [s for s in sites if s not in SITES]
    if unknown:
        ap.error(f"unknown site(s): {unknown}; choose from {list(SITES)}")
    wanted = [c.strip() for arg in args.category for c in arg.split(",") if c.strip()]
    dump: dict = {}
    for key in sites:
        cls = SITES[key][0]
        retailer, tables, src = load_site(key)
        configured = (["ski"] + [c for c in tables if c != "ski"]) if tables else list(cls.CATEGORY_DEFAULTS)
        cats = wanted or configured
        print(f"\n=== {retailer.get('name', key)} ({retailer['url']}) — config: {src}\n    categories: {', '.join(cats)} ===")
        fetcher = Fetcher()
        rows = [run_category(cls, category_cfg(cls, retailer, tables, cat), cat, fetcher, args.details, args.verbose)
                for cat in cats]
        print(f"\n{'category':<11} {'count':>6} {'time s':>7} {'req':>5} {'sizes':>6} {'disc':>6}  gender")
        for r in rows:
            print(f"{r['cat']:<11} {r['n']:>6} {r['sec']:>7.1f} {r['req']:>5} {r.get('sized', '-'):>6} "
                  f"{r.get('disc', '-'):>6}  {'BLOCKED' if r['blocked'] else r.get('genders')}")
        total_req = sum(r["req"] for r in rows)
        print(f"{'TOTAL':<11} {sum(r['n'] for r in rows):>6} {sum(r['sec'] for r in rows):>7.1f} {total_req:>5}"
              f"   (budget {REQUEST_BUDGET}{' — OVER!' if total_req > REQUEST_BUDGET else ''}; "
              f"robots.txt not counted)")
        dump[key] = {r["cat"]: [asdict(p) for p in r["items"]] for r in rows}
    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as fh:
            json.dump(dump, fh, ensure_ascii=False, indent=1)
        print(f"\nwrote {args.dump}")


if __name__ == "__main__":
    main()
