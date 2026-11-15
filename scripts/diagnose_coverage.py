"""只读覆盖诊断：固定已完成轮次为基线，当前库存另列，不联网、不修改数据库。

运行：.venv/Scripts/python.exe scripts/diagnose_coverage.py
输出默认留在被 Git 忽略的 data/diagnostics/，不含关注、账号或环境变量。
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from skideals.config import DB_PATH, load_retailers
from skideals.sections import SKI_CATEGORIES


def diagnosis(status: str | None, n: int | None, message: str = "") -> str:
    """空结果不自动认定为真实无货或解析失败；有明确证据才细分原因。"""
    if not status:
        return "未验证"
    if status == "robots":
        return "robots限制"
    if status == "blocked":
        return "反爬拦截"
    if status == "error":
        if re.search(r"\b404\b", message):
            return "入口404"
        if "JSONDecodeError" in message or "parse" in message.lower():
            return "解析错误"
        return "其他错误"
    if status == "partial":
        return "部分覆盖"
    if status == "empty":
        return "零结果待核查"
    if status == "ok":
        return "零结果待核查" if not n else "有产出（未核验完整性）"
    return "其他状态"


def full_ski_run(run: dict) -> bool:
    s = run["summary"]
    if run["status"] != "done" or s.get("retailer_ids") or not s.get("results"):
        return False
    if "categories" in s:
        return s["categories"] is None or set(SKI_CATEGORIES) <= set(s["categories"])
    observed = {c["cat"] for r in s["results"] for c in r.get("cats", [])}
    return set(SKI_CATEGORIES) <= observed


def run_pairs(run: dict | None) -> dict:
    return {(r["id"], c["cat"]): c for r in (run or {}).get("summary", {}).get("results", [])
            for c in r.get("cats", [])}


def collect(db_path: Path, log_path: Path) -> dict:
    cfgs = load_retailers()
    # 显式只读 + 同一读事务，后台写入不会令本次报告的 SQL 查询互相不一致。
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        retailers = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM retailers")}
        runs = []
        for r in conn.execute("SELECT * FROM crawl_runs ORDER BY id DESC"):
            r = dict(r)
            r["summary"] = json.loads(r.pop("summary_json") or "{}")
            runs.append(r)
        inventory = {(r["retailer_id"], r["category"]): dict(r) for r in conn.execute("""
            SELECT retailer_id, category, COUNT(*) stored, SUM(active=1) active,
                   SUM(active=1 AND available=1) available,
                   SUM(active=1 AND (sizes_json IS NULL OR sizes_json='' OR sizes_json='[]')) no_sizes,
                   MAX(CASE WHEN active=1 THEN last_seen END) newest_seen,
                   MIN(CASE WHEN active=1 THEN last_seen END) oldest_seen
            FROM listings GROUP BY retailer_id, category""")}
        latest_seen = conn.execute("SELECT MAX(last_seen) FROM listings").fetchone()[0]
        cutoff = (datetime.fromisoformat(latest_seen) - timedelta(days=7)).isoformat() if latest_seen else ""
        old = {(r[0], r[1]): r[2] for r in conn.execute("""
            SELECT retailer_id,category,COUNT(*) FROM listings
            WHERE active=1 AND last_seen < ? GROUP BY retailer_id,category""", (cutoff,))}
        conn.rollback()

    baseline = next((r for r in runs if full_ski_run(r)), None)
    bp = run_pairs(baseline)
    successes = {}
    for run in runs:  # 已完成 run 的成功产出；不信任零产出的 ok。
        if run["status"] == "done":
            for key, c in run_pairs(run).items():
                if c.get("status") == "ok" and c.get("n", 0) > 0:
                    successes.setdefault(key, run["finished_at"])

    # 日志与数据库不是原子快照；按站保留证据与行号，不推断为每个分类的根因。
    evidence = defaultdict(list)
    if log_path.exists():
        with log_path.open(encoding="utf-8", errors="replace") as f:
            for num, line in enumerate(f, 1):
                m = re.search(r"\[([a-z0-9_-]+)(?:/[^\]]*)?\]", line)
                if m and re.search(r"失败|出错|anti-bot|disallow|\b404\b|without sizes|max_detail", line, re.I):
                    evidence[m[1]].append({"line": num, "text": line.strip()})
                    evidence[m[1]] = evidence[m[1]][-6:]

    rows = []
    for cfg in cfgs:
        rid = cfg["id"]
        retailer = retailers.get(rid, {})
        current = json.loads(retailer.get("cats_json") or "{}")
        cats = cfg["by_category"]
        # 保留已停用配置、数据库残留分类和旧基线分类，避免静默消失。
        keys = set(cats) | set(current) | {cat for r, cat in bp if r == rid} | {cat for r, cat in inventory if r == rid}
        for cat in sorted(keys):
            enabled = bool(cfg["enabled"] and cat in cats and cats[cat].get("enabled", True))
            b = bp.get((rid, cat), {})
            cur = current.get(cat, {})
            inv = inventory.get((rid, cat), {})
            last_success = successes.get((rid, cat))
            if cur.get("status") == "ok" and cur.get("n", 0) > 0:
                last_success = max(last_success or "", cur.get("at") or "") or None
            merged = {**cfg, **cats.get(cat, {})}
            adapter = cfg.get("adapter", "shopify")
            coverage_risk = "未审核完整性"
            if adapter in ("schema_org", "outdoor_brand"):
                key = "max_items_per_cat" if adapter == "schema_org" else "max_items"
                coverage_risk = f"详情链接上限 {merged.get(key, 40)}；无通用翻页，新版保守标 partial；历史状态需重新抓取验证"
            elif adapter == "backcountry":
                coverage_risk = f"页数上限 {merged.get('max_pages', 5)}；分页终点未验证，新版保守标 partial；历史状态需重新抓取验证"
            rows.append({
                "retailer": rid, "name": cfg["name"], "adapter": adapter,
                "country": cfg["country"], "category": cat, "enabled": enabled,
                "configured_sources_and_budgets": {k: v for k, v in merged.items() if k in
                    ("url", "pages", "categories", "path", "collections", "max_pages", "max_items", "max_items_per_cat", "max_detail_pages")},
                "coverage_risk": coverage_risk,
                "scope": "滑雪（含共享分类）" if cat in SKI_CATEGORIES else "户外或其他",
                "baseline": b, "baseline_diagnosis": diagnosis(b.get("status"), b.get("n"), b.get("msg", "")),
                "current": cur, "current_diagnosis": diagnosis(cur.get("status"), cur.get("n"), cur.get("msg", "")),
                "last_recorded_success_with_output": last_success,
                "active": inv.get("active", 0), "available": inv.get("available", 0),
                "stored": inv.get("stored", 0), "no_sizes": inv.get("no_sizes", 0),
                "older_than_7_days": old.get((rid, cat), 0),
                "oldest_seen": inv.get("oldest_seen"), "newest_seen": inv.get("newest_seen"),
            })
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "configured_retailers": len(cfgs), "enabled_retailers": sum(c["enabled"] for c in cfgs),
        "baseline": baseline, "unfinished_runs": [{k: r[k] for k in ("id", "started_at", "status")} for r in runs if r["status"] == "running"],
        "latest_listing_timestamp": latest_seen, "age_cutoff": cutoff,
        "rows": rows, "log_evidence_by_retailer": dict(evidence),
        "database_only_retailers": sorted(set(retailers) - {c["id"] for c in cfgs}),
    }


def cell(value) -> str:
    return str(value if value is not None else "未知").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render(report: dict) -> tuple[str, str]:
    rows = report["rows"]
    enabled = [r for r in rows if r["enabled"]]
    ski = [r for r in enabled if r["category"] in SKI_CATEGORIES]
    baseline = report["baseline"]
    lines = ["# 第一阶段：数据覆盖诊断", "", f"生成时间（UTC）：{report['generated_at_utc']}", "",
             "## 统计口径", "",
             "本报告只读本地数据库、配置和日志，没有向商家发送请求，没有验证实时价格。",
             "数据库各查询使用同一只读事务；日志单独读取，只作为带时间与行号的站级证据。",
             "数据库时间戳未记录时区，以下原样保留，不冒充洛杉矶时间。",
             "当前状态来自各分类最近一次记录，可能跨轮次；活跃记录不代表当前仍有货。",
             "最后成功时间指历史记录中 status=ok 且有产出的时间，不证明解析正确或覆盖完整。",
             "基线 n 包含改判到其他分类的产出；当前状态 n 为本分类产出，二者不可直接计算增减。",
             "7 天旧记录以数据库最新商品时间为参照，仅作诊断阈值；无尺码只统计空尺码数组，非逐码质量审核。", ""]
    if baseline:
        lines += [f"固定基线：已完成滑雪全量任务 **#{baseline['id']}**，数据库原始时间 {baseline['started_at']} 至 {baseline['finished_at']}。",
                  f"该轮包含 {len(baseline['summary']['results'])} 个来源；下面分类分布按当前启用配置筛选，旧基线中的已停用项保留在明细。", ""]
    else:
        lines += ["没有可用的已完成滑雪全量任务，基线未知。", ""]
    lines += [f"数据库仍标记 running 的任务：{', '.join('#'+str(r['id']) for r in report['unfinished_runs']) or '无'}。历史残留不等于进程仍运行。",
              f"当前配置 {report['configured_retailers']} 家，启用 {report['enabled_retailers']} 家。",
              f"当前启用分类有活跃记录的来源：{len({r['retailer'] for r in enabled if r['active']})} 家；活跃记录 {sum(r['active'] for r in enabled):,} 条。",
              f"其中滑雪范围活跃记录 {sum(r['active'] for r in ski):,} 条；超过 7 天未见 {sum(r['older_than_7_days'] for r in ski):,} 条；空尺码记录 {sum(r['no_sizes'] for r in ski):,} 条。", "",
              "## 分类诊断分布（当前启用滑雪分类）", "", "| 分类 | 固定基线 | 当前观察 |", "|---|---:|---:|"]
    bc = Counter(r["baseline_diagnosis"] for r in ski)
    cc = Counter(r["current_diagnosis"] for r in ski)
    for k in sorted(set(bc) | set(cc)):
        lines.append(f"| {k} | {bc[k]} | {cc[k]} |")
    lines += ["", "零结果不能证明该站无货，也不能单凭零结果断言解析失败。当前实现没有足够证据确认“真实空目录”；成功且有商品也不能证明全量覆盖。", "",
              "## 修复优先级", "",
              "1. **P0：状态与保留机制。** 第二阶段代码已修复通用适配器吞异常、部分结果丢失和范围未验证时误下架的问题；历史记录不回填，重启后下一轮抓取验证新状态。关联：skideals/adapters/、skideals/crawl.py。",
              "2. **P0：优惠准确性。** schema_org 将 AggregateOffer.highPrice 当原价；需要原价证据，不能把报价区间变成折扣。关联：skideals/adapters/schema_org.py。",
              "3. **P1：入口与平台核对。** 优先排查 Corbetts、Fischer、Powder7 的 404；先验证一个滑雪分类，再扩展。404 是本地历史响应，不代表现在仍然如此。",
              "4. **P1：重点零结果与反爬。** 核对 Backcountry 的分类覆盖、Steep & Cheap、REI；品牌站按证据逐站处理。浏览器回退不保证能得到正确 JSON 或完整商品页。",
              "5. **P2：数量与新鲜度。** 完成分页、详情预算及逐码价格检查，再安排更新频率。清理残留 running 状态另属后续阶段。", "",
              "## 按站核查队列", "",
              "按明确故障/零结果分类数排序，仅用来安排检查，不是商家质量评分。详见 [分类明细](coverage-details.md) 与 [结构化报告](coverage.json)。", "",
              "| 来源 | 适配器 | 启用滑雪分类 | 当前明确故障 | 当前零结果 | 活跃记录 | 7天旧记录 |", "|---|---|---:|---:|---:|---:|---:|"]
    grouped = defaultdict(list)
    for r in ski:
        grouped[r["retailer"]].append(r)
    queue = []
    for rid, group in grouped.items():
        failures = sum(r["current"].get("status") in ("error", "blocked", "robots") for r in group)
        zeros = sum(r["current_diagnosis"] == "零结果待核查" for r in group)
        queue.append((failures + zeros, rid, group, failures, zeros))
    for _, rid, group, failures, zeros in sorted(queue, key=lambda x: (-x[0], x[1])):
        lines.append(f"| {rid} | {group[0]['adapter']} | {len(group)} | {failures} | {zeros} | {sum(r['active'] for r in group)} | {sum(r['older_than_7_days'] for r in group)} |")
    details = ["# 网站 × 分类明细", "", "统计口径见 coverage-report.md。所有当前启用、停用、残留分类均保留。基线与当前数量口径不同，不能直接比较。", ""]
    for rid in sorted({r["retailer"] for r in rows}):
        group = [r for r in rows if r["retailer"] == rid]
        details += [f"## {rid} · {group[0]['name']} ({group[0]['adapter']})", "",
                    "| 分类 | 启用 | 基线诊断/产出 | 当前诊断/产出 | 当前状态时间 | 最后记录成功产出 | 活跃/7天旧 |", "|---|---|---|---|---|---|---|"]
        for r in group:
            values = [r['category'], '是' if r['enabled'] else '否',
                      f"{r['baseline_diagnosis']} / {r['baseline'].get('n', '未知')}",
                      f"{r['current_diagnosis']} / {r['current'].get('n', '未知')}",
                      r['current'].get('at'), r['last_recorded_success_with_output'],
                      f"{r['active']} / {r['older_than_7_days']}"]
            details.append("| " + " | ".join(map(cell, values)) + " |")
        risks = sorted({r['coverage_risk'] for r in group})
        details += ["", "覆盖风险：" + "；".join(risks), "", "当前分类消息（原样保留，配置入口与预算见 JSON）：", ""]
        for r in group:
            if r['current'].get('msg'):
                details.append(f"- {r['category']}：{cell(r['current']['msg'])}")
        details += ["", "最近的站级日志证据（可能跨轮次，不自动归因到上述各分类）：", ""]
        for e in report['log_evidence_by_retailer'].get(rid, []):
            details.append(f"- crawl.log:{e['line']} — {cell(e['text'])}")
        details.append("")
    return "\n".join(lines) + "\n", "\n".join(details) + "\n"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", type=Path, default=DB_PATH)
    p.add_argument("--log", type=Path, default=ROOT / "data/crawl.log")
    p.add_argument("--out", type=Path, default=ROOT / "data/diagnostics")
    args = p.parse_args()
    report = collect(args.db, args.log)
    summary, details = render(report)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "coverage.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / "coverage-report.md").write_text(summary, encoding="utf-8")
    (args.out / "coverage-details.md").write_text(details, encoding="utf-8")
    print(f"Coverage report: {args.out.resolve()}")


if __name__ == "__main__":
    main()
