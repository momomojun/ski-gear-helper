"""REI 适配器的联网冒烟测试（会真的访问 rei.com，不是 pytest 单元测试）。

用法：
    .venv/Scripts/python.exe tests/live_rei.py                          # 抓全部分类（约 5~10 分钟）
    .venv/Scripts/python.exe tests/live_rei.py --category jacket        # 只抓一个分类
    .venv/Scripts/python.exe tests/live_rei.py --category skin,bag,suit # 逗号分隔多个

分类配置的来源：skideals.config.load_retailers()（retailers.toml + config/categories.toml 的 [rei.<分类>]），
和调度器（crawl.category_configs）一样合并：零售商级别的键 + 分类表 + category，按 CATEGORIES 顺序。
categories.toml 里还没有 REI 的分类时，用适配器内置的 DEFAULT_CATEGORY_CFG 模拟那张表。
同一个商品出现在两个分类时，调度器只保留靠前的分类——这里最后会列出这种重叠。
"""
from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skideals.adapters.rei import DEFAULT_CATEGORY_CFG, ReiAdapter  # noqa: E402
from skideals.net import BlockedError, FetchError, Fetcher  # noqa: E402

RETAILER = {"id": "rei", "name": "REI", "country": "US", "currency": "USD", "url": "https://www.rei.com",
            "adapter": "rei", "detail_mode": "discounted"}


def simulated_table(cat: str) -> dict:
    """DEFAULT_CATEGORY_CFG 写成 categories.toml 里 [rei.<cat>] 的样子（显式写全路径键，防止继承双板配置）。"""
    c = dict(DEFAULT_CATEGORY_CFG[cat])
    c.setdefault("category_paths", [])
    c.setdefault("gender_paths", {})
    return c


def category_cfgs() -> tuple[str, list[tuple[str, dict]]]:
    try:
        from skideals.categories import CATEGORIES
        order = [c.id for c in CATEGORIES]
    except Exception:  # noqa: BLE001
        order = list(DEFAULT_CATEGORY_CFG)
    reg, source = None, "DEFAULT_CATEGORY_CFG (simulated categories.toml)"
    try:
        from skideals.config import load_retailers
        reg = next((r for r in load_retailers() if r.get("id") == "rei"), None)
    except Exception as e:  # noqa: BLE001
        print("(could not load the registry:", e, ")")
    if reg is None:
        reg = dict(RETAILER)
    by_cat = dict(reg.get("by_category") or {"ski": {}})
    if set(by_cat) - {"ski"}:
        source = "config/retailers.toml + config/categories.toml"
    else:
        by_cat.update({cat: simulated_table(cat) for cat in DEFAULT_CATEGORY_CFG if cat != "ski"})
    base = {k: v for k, v in reg.items() if k != "by_category"}
    out = []
    for cat in sorted(by_cat, key=lambda c: order.index(c) if c in order else 99):   # 和 crawl.category_configs 一样
        ccfg = by_cat[cat] or {}
        if ccfg.get("enabled", True) is False:
            continue
        out.append((cat, {**base, **ccfg, "category": cat}))
    return source, out


def sizes_summary(p, limit: int = 8) -> str:
    if not p.sizes:
        return "-"
    labels = [str(s.cm) if s.cm else s.label for s in p.sizes]
    shown = "/".join(labels[:limit]) + ("/..." if len(labels) > limit else "")
    avail = sum(1 for s in p.sizes if s.available)
    prices = sorted({s.price for s in p.sizes if s.price is not None})
    txt = f"{shown} ({avail}/{len(p.sizes)} avail)"
    if len(prices) > 1:
        txt += f" ${prices[0]:.2f}-{prices[-1]:.2f}"
    return txt + (" [api]" if p.extra.get("detail") else "")


def samples(products, n: int = 3):
    out = []
    disc = sorted((p for p in products if p.compare_at), key=lambda p: p.price / p.compare_at)
    if disc:
        out.append(disc[0])
    for p in products:
        if len(out) >= n:
            break
        if p not in out and p.gender_hint not in {q.gender_hint for q in out}:
            out.append(p)
    for p in products:
        if len(out) >= n:
            break
        if p not in out:
            out.append(p)
    return out


def run_category(cat: str, cfg: dict, fetcher: Fetcher) -> dict:
    adapter = ReiAdapter(cfg, fetcher, log=lambda m: print("   ", m))
    t0, r0 = time.time(), fetcher.request_count
    print(f"\n=== {cat} " + "=" * (90 - len(cat)))
    try:
        products = list(adapter.fetch())
        status = "ok"
    except BlockedError as e:
        print("    BLOCKED by REI anti-bot:", e)
        products, status = [], "BLOCKED"
    except FetchError as e:
        print("    FAILED:", e)
        products, status = [], "failed"
    dt, reqs = time.time() - t0, fetcher.request_count - r0
    n = len(products) or 1
    st = getattr(adapter, "stats", {})
    with_sizes = sum(1 for p in products if p.sizes)
    disc = [p for p in products if p.compare_at]
    genders = Counter(p.gender_hint or "None" for p in products)
    ids = Counter(p.external_id for p in products)
    print(f"    {len(products)} products | {dt:.0f}s | {reqs} requests (listing {st.get('listing_requests')}, "
          f"detail {st.get('detail_requests')}) | REI listed {st.get('listed')}, excluded {st.get('excluded')}, "
          f"skipped {st.get('skipped')}, detail failures {st.get('detail_failed')}")
    print(f"    sizes {100 * with_sizes / n:.0f}% | discounted {len(disc)} ({100 * len(disc) / n:.0f}%) | "
          f"gender {dict(genders)} | categories {dict(Counter(p.category for p in products))}")
    bad = {"dup": [i for i, c in ids.items() if c > 1], "no price": [p.external_id for p in products if p.price is None],
           "no image": [p.external_id for p in products if not p.image],
           "cmp<=price": [p.external_id for p in products if p.compare_at and p.compare_at <= p.price]}
    if any(bad.values()):
        print("    CHECKS:", {k: v[:5] for k, v in bad.items() if v})
    if st.get("skipped_titles"):
        print("    skipped by category filter:", st["skipped_titles"][:8])
    for p in samples(products):
        cmp_ = f"${p.compare_at:.2f}" if p.compare_at else "-"
        print(f"    - {p.title[:58]:58} ${p.price:>7.2f} was {cmp_:>8} | {p.gender_hint or '-':6} | {sizes_summary(p)}")
        print(f"      {p.url}")
    return {"cat": cat, "status": status, "n": len(products), "secs": dt, "reqs": reqs,
            "sizes": 100 * with_sizes / n, "disc": 100 * len(disc) / n, "genders": dict(genders),
            "ids": set(ids), "detail": st.get("detail_requests") or 0}


def main() -> None:
    wanted = None
    if "--category" in sys.argv:
        wanted = set(sys.argv[sys.argv.index("--category") + 1].split(","))
    source, cfgs = category_cfgs()
    cfgs = [(c, cfg) for c, cfg in cfgs if not wanted or c in wanted]
    print(f"category config source: {source}; running {[c for c, _ in cfgs]}")
    fetcher = Fetcher()
    t0 = time.time()
    rows = []
    for cat, cfg in cfgs:
        rows.append(run_category(cat, cfg, fetcher))
        if rows[-1]["status"] == "BLOCKED":
            print("\nStopping: REI blocked us.")
            break

    print("\n" + "=" * 100)
    print(f"{'category':10} {'status':8} {'items':>6} {'secs':>5} {'reqs':>5} {'detail':>6} {'sizes%':>6} {'disc%':>6}  gender")
    for r in rows:
        print(f"{r['cat']:10} {r['status']:8} {r['n']:>6} {r['secs']:>5.0f} {r['reqs']:>5} {r['detail']:>6} "
              f"{r['sizes']:>6.0f} {r['disc']:>6.0f}  {r['genders']}")
    print(f"TOTAL: {sum(r['n'] for r in rows)} products, {fetcher.request_count} requests "
          f"(robots.txt not counted), {time.time() - t0:.0f}s")
    seen: dict[str, str] = {}
    overlaps = Counter()
    for r in rows:
        for pid in r["ids"]:
            if pid in seen:
                overlaps[f"{seen[pid]}+{r['cat']}"] += 1
            else:
                seen[pid] = r["cat"]
    print("same product in two categories:", dict(overlaps) or "none")


if __name__ == "__main__":
    main()
