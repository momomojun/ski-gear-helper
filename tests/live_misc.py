"""在线冒烟测试：Peter Glenn / Sun & Ski / Skis.com（以及已停用的 Buckman's）这些非 Shopify 适配器，按分类跑。

用法（在项目根目录）：
    .venv/Scripts/python.exe tests/live_misc.py                          # 默认站点 × 各自支持的全部分类
    .venv/Scripts/python.exe tests/live_misc.py --category jacket        # 只跑某个分类（逗号分隔可多个）
    .venv/Scripts/python.exe tests/live_misc.py --category ski,boot peterglenn skiscom
    .venv/Scripts/python.exe tests/live_misc.py --max-detail 3           # 统一限制每个分类的详情页数量（调试用）
    .venv/Scripts/python.exe tests/live_misc.py --dump out.json          # 另把全部商品写成 JSON

站点之间并行（不同域名互不影响），同一站点的分类顺序执行、共用一个 Fetcher（按 registry 的 min_interval 限速）。
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skideals.adapters import peterglenn, sunandski  # noqa: E402
from skideals.adapters.buckmans import BuckmansAdapter  # noqa: E402
from skideals.adapters.skiscom import SkisComAdapter  # noqa: E402
from skideals.net import Fetcher  # noqa: E402

SITES = {
    "peterglenn": (peterglenn.PeterGlennAdapter, list(peterglenn.DEFAULT_CATEGORIES),
                   {"id": "peterglenn", "name": "Peter Glenn", "url": "https://peterglenn.com", "min_interval": 3.0}),
    "sunandski": (sunandski.SunAndSkiAdapter, list(sunandski.DEFAULT_CATEGORIES),
                  {"id": "sunandski", "name": "Sun & Ski Sports", "url": "https://www.sunandski.com"}),
    "skiscom": (SkisComAdapter, list(SkisComAdapter.DEFAULT_CATEGORIES),
                {"id": "skiscom", "name": "Skis.com", "url": "https://skis.com", "min_interval": 2.0}),
    "buckmans": (BuckmansAdapter, list(BuckmansAdapter.DEFAULT_CATEGORIES),   # 已停用，只在显式指定时跑
                 {"id": "buckmans", "name": "Buckman's", "url": "https://buckmans.com", "min_interval": 2.0}),
}
DEFAULT_SITES = ["peterglenn", "sunandski", "skiscom"]


def run_site(site: str, cats: list[str], max_detail: int | None):
    cls, supported, base_cfg = SITES[site]
    fetcher = Fetcher(min_interval=float(base_cfg.get("min_interval", 1.5)))
    results = []
    for cat in cats:
        if cat not in supported:
            results.append((cat, [], 0.0, 0, "not supported by this site"))
            continue
        cfg = {**base_cfg, "category": cat}  # 详情页预算默认用适配器里的 DEFAULT_DETAIL_PAGES（= 建议的 categories.toml）
        if max_detail is not None:
            cfg["max_detail_pages"] = max_detail
        t0, r0 = time.time(), fetcher.request_count
        items, err = [], None
        try:
            items = list(cls(cfg, fetcher, log=lambda m: print("   ", m, flush=True)).fetch())
        except Exception as e:  # 报告而不是崩溃
            err = f"{type(e).__name__}: {e}"
        results.append((cat, items, time.time() - t0, fetcher.request_count - r0, err))
    return site, results


def sizes_summary(p) -> str:
    if not p.sizes:
        return "-"
    labels = [(str(s.cm) if s.cm else s.label) + ("" if s.available else "x") for s in p.sizes]
    return f"{'/'.join(labels)[:70]} ({sum(s.available for s in p.sizes)}/{len(p.sizes)} avail)"


def pct(n, d) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"


def report_category(cat, items, secs, reqs, err):
    n = len(items)
    head = f"  [{cat}] products={n} runtime={secs:.0f}s requests={reqs}"
    if err:
        print(head + f"  ERROR={err}")
        return
    if not n:
        print(head)
        return
    disc = [p for p in items if p.compare_at]
    with_sizes = [p for p in items if p.sizes]
    print(head + f"  sizes={pct(len(with_sizes), n)} discounted={pct(len(disc), n)}"
          f" (disc.w/sizes {sum(1 for p in disc if p.sizes)}/{len(disc)})")
    print("      gender:", dict(Counter(p.gender_hint for p in items).most_common()),
          " condition:", dict(Counter(p.condition_hint for p in items).most_common()))
    bad = [p for p in items if p.category != cat]
    dup = [k for k, v in Counter(p.external_id for p in items).items() if v > 1]
    if bad or dup:
        print(f"      !! wrong category: {len(bad)}  duplicate ids: {dup[:5]}")
    picks = ([p for p in disc if p.sizes][:1] + [p for p in items if not p.compare_at][:1])
    rest = [p for p in items if p not in picks]
    picks += rest[:: max(1, len(rest) // max(1, 3 - len(picks)))][: 3 - len(picks)]
    for p in picks[:3]:
        cmp_ = f"{p.compare_at:.2f}" if p.compare_at else "-"
        price = f"{p.price:.2f}" if p.price is not None else "None"
        print(f"      - {p.title[:90]} | {price} / {cmp_} | {p.gender_hint} | sizes {sizes_summary(p)}\n        {p.url}")


def main():
    args = sys.argv[1:]

    def opt(name):
        if name in args:
            i = args.index(name)
            val = args[i + 1]
            del args[i:i + 2]
            return val
        return None

    max_detail = opt("--max-detail")
    max_detail = int(max_detail) if max_detail is not None else None
    dump = opt("--dump")
    cat_arg = opt("--category")
    sites = args or DEFAULT_SITES
    t0 = time.time()

    def cats_for(site):
        supported = SITES[site][1]
        return supported if not cat_arg or cat_arg == "all" else [c.strip() for c in cat_arg.split(",") if c.strip()]

    with ThreadPoolExecutor(max_workers=len(sites)) as ex:
        results = list(ex.map(lambda s: run_site(s, cats_for(s), max_detail), sites))

    for site, rows in results:
        print("=" * 110)
        tot_n = sum(len(r[1]) for r in rows)
        tot_req = sum(r[3] for r in rows)
        tot_t = sum(r[2] for r in rows)
        print(f"{SITES[site][2]['name']} ({site})  categories={len(rows)}  products={tot_n}  "
              f"requests={tot_req}  runtime={tot_t:.0f}s")
        for row in rows:
            report_category(*row)
    if dump:
        with open(dump, "w", encoding="utf-8") as fh:
            json.dump({site: {cat: [asdict(p) for p in items] for cat, items, *_ in rows} for site, rows in results},
                      fh, ensure_ascii=False, indent=1)
        print(f"dumped products to {dump}")
    print("=" * 110)
    print(f"total wall time {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
