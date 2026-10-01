"""抓取调度：并行抓多个网站（每个网站内部串行 + 限速），标准化后写入数据库。

抓完后：更新汇率、检查关注列表并生成降价提醒。
网页上的「更新数据」按钮和命令行 `python -m skideals crawl` 都走这里。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from . import db
from .adapters import get_adapter
from .config import DATA_DIR, load_retailers
from .net import BlockedError, Fetcher, RobotsDisallowed
from .normalize import normalize

# 当前抓取进度（网页轮询 /api/crawl/status 读取）
STATE: dict = {"running": False, "started_at": None, "finished_at": None, "retailers": {}, "log": []}
_LOCK = threading.Lock()


def _log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with _LOCK:
        STATE["log"] = (STATE["log"] + [line])[-200:]
        try:  # 计划任务在后台运行时没有控制台，日志写进 data/crawl.log 方便排查
            with open(DATA_DIR / "crawl.log", "a", encoding="utf-8") as fh:
                print(time.strftime("%Y-%m-%d ") + line, file=fh)
        except OSError:
            pass


def _set(rid: str, **kw) -> None:
    with _LOCK:
        STATE["retailers"].setdefault(rid, {}).update(kw)


OLD_SKI_YEARS = 10   # 早于“当前雪季 − 10 年”的双板不收
# 年份识别（normalize.extract_year）只认 2012 年以后；标题里写着 1980–2011 年的也是老板子（“K2 T9 Skye Skis 2003”）
OLD_YEAR_RE = re.compile(r"(?<![\w.])(?:19[89]\d|200\d|201[01])(?![\w.])")   # 独立的数字：“K2 K2000 AT99S”不算


def current_season() -> int:
    """当前雪季的结束年份：2026 年 9 月 → 2026/27 雪季 → 2027。"""
    now = datetime.now()
    return now.year + 1 if now.month >= 7 else now.year


def category_configs(cfg: dict) -> list[tuple[str, dict]]:
    """把一个零售商的配置拆成“每个分类一次抓取”的配置：零售商级别的键 + [retailers.categories.<分类>] 里的键。
    没写 categories 的老配置 = 只有双板。"""
    from .categories import CATEGORIES
    base = {k: v for k, v in cfg.items() if k != "by_category"}
    cats = cfg.get("by_category")
    if not cats:
        return [("ski", {**base, "category": "ski"})]
    order = [c.id for c in CATEGORIES]  # 固定顺序：同一个商品出现在两个分类时，归到靠前的分类（雪服优先于中间层…）
    out = []
    for cat in sorted(cats, key=lambda c: order.index(c) if c in order else 99):
        ccfg = cats[cat] or {}
        if ccfg.get("enabled", True) is False:
            continue
        out.append((cat, {**base, **ccfg, "category": cat}))
    return out


def _cat_name(cat: str) -> str:
    from .categories import BY_ID
    return BY_ID[cat].name.split("（")[0] if cat in BY_ID else cat


def _write(rid: str, category: str, items: list, started: str, complete: bool) -> int:
    """一次性写入一个网站一个分类的全部结果（一个很短的事务）。

    为什么不边抓边写：有些适配器在两个商品之间还要联网抓商品页，如果边抓边写，
    写事务会一直开着，其他网站的线程拿不到写锁，就会报 “database is locked”。
    """
    day = db.today()
    for attempt in range(4):
        try:
            with db.connect() as conn:
                for lst in items:
                    db.save_listing(conn, lst, started, day)
                return db.deactivate_missing(conn, rid, started, category) if complete and items else 0
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or attempt == 3:
                raise
            time.sleep(2 + attempt * 3)
    return 0


def _decide_categories(rid: str, cat: str, raws: list) -> tuple[list[str | None], int]:
    """这个分类页抓到的每个商品最终属于哪个分类（categories.assign + decide_batch），返回 (分类列表, 改判数)。
    双板页沿用适配器里严格的 NOT_SKI_RE 过滤，不再改判。"""
    from collections import Counter
    from .categories import assign, classify, decide_batch
    if cat == "ski":
        # 双板页里单卖的固定器（“Marker F10 Tour Ski Bindings”）改判到固定器；套装（标题里还有 skis）仍是双板
        finals = ["binding" if classify(r.title) == "binding" and
                  not re.search(r"\bskis?\b", re.sub(r"\bski bindings?\b", " ", r.title, flags=re.I), re.I) else "ski"
                  for r in raws]
        return finals, finals.count("binding")
    results = [assign(cat, r.title, r.product_type, r.tags, r.extra.get("options"), r.url,
                      [s.cm for s in r.sizes if s.cm], r.vendor) for r in raws]
    finals, mixed = decide_batch(cat, results)
    stats = Counter()
    for (c, why), final in zip(results, finals):
        if final is None:
            stats["不要：" + ("混装页里没有证据" if c else why)] += 1
        elif final != cat:
            stats[f"→{final}"] += 1
    if stats or mixed:
        _log(f"[{rid}/{_cat_name(cat)}] 分类判定{'（混装页）' if mixed else ''}：" +
             "，".join(f"{k} {n}" for k, n in stats.most_common()))
    return finals, sum(1 for f in finals if f and f != cat)


def crawl_category(cfg: dict, cat: str, fetcher: Fetcher, seen: set | None = None, started: str | None = None) -> dict:
    """started = 这个网站这一轮抓取的开始时间（所有分类共用）：前面分类页里已经收过、改判到本分类的商品
    last_seen 也是这一轮，不会被当成下架。"""
    rid = cfg["id"]
    started = started or db.now_iso()
    items: list = []
    t0 = time.time()
    oldest_soldout_year = current_season() - 1
    adapter = None
    kind, msg = "ok", ""
    try:
        adapter = get_adapter(cfg, fetcher, _log)
        raws = []
        for raw in adapter.fetch():
            if raw.price is None:
                continue
            if seen is not None and raw.external_id in seen:  # 已经在前面的分类里收过（比如保暖雪服也在中间层分类页里）
                continue
            raws.append(raw)
        finals, moved = _decide_categories(rid, cat, raws)
        for raw, final in zip(raws, finals):
            if final is None:
                continue
            raw.extra["src_category"] = cat
            raw.category = final
            # 双板以外的售罄商品不入库：服装大多没写年份，没法判断是不是早已下市的老款，留着只会让数据库膨胀
            if final != "ski" and raw.available is False:
                continue
            lst = normalize(raw)
            # 早已售罄的老款（比如 evo 还挂着 2010 年的板子）没有比价意义，不入库
            if raw.available is False and lst.year and lst.year < oldest_soldout_year:
                continue
            # 十年以上的老板子（evo 还在卖 2003 年的 $19.9 旧板）：固定器早已不在厂商的安全认证期内，没有比价意义
            if final == "ski" and ((lst.year and lst.year <= current_season() - OLD_SKI_YEARS) or OLD_YEAR_RE.search(raw.title)):
                continue
            items.append(lst)
            if seen is not None:
                seen.add(raw.external_id)
        gone = _write(rid, cat, items, started, True)
        kept = sum(1 for lst in items if lst.category == cat)
        msg = f"{kept} 个" + (f"（另有 {moved} 个改判到别的分类）" if moved else "") + (f"，{gone} 个已下架" if gone else "")
    except (BlockedError, RobotsDisallowed) as e:
        kind = "blocked" if isinstance(e, BlockedError) else "robots"
        msg = ("被网站反爬拦截" if kind == "blocked" else "robots.txt 不允许") + f"：{e.url[:80]}"
    except Exception as e:
        kind = "error"
        msg = f"{type(e).__name__}: {str(e)[:200]}"
        _log(f"[{rid}/{cat}] 出错：{msg}\n{traceback.format_exc(limit=3)}")
    if kind != "ok":  # 失败：已经抓到的部分照样保存（但不把没抓到的标记为下架）
        try:
            _write(rid, cat, items, started, False)
        except Exception as e2:
            _log(f"[{rid}/{cat}] 保存失败：{e2}")
    try:
        with db.connect() as conn:
            db.set_category_status(conn, rid, cat, kind, sum(1 for lst in items if lst.category == cat), msg)
    except sqlite3.OperationalError:
        pass
    _log(f"[{rid}/{_cat_name(cat)}] {'完成' if kind == 'ok' else '失败'}：{msg}（{time.time() - t0:.0f}s）")
    return {"cat": cat, "status": kind, "n": len(items), "msg": msg,
            "meta": getattr(adapter, "store_meta", None) or None}


def crawl_one(cfg: dict, fetcher: Fetcher, categories: list[str] | None = None) -> dict:
    rid = cfg["id"]
    _set(rid, status="running", n=0, msg="")
    own_fetcher = None
    if cfg.get("min_interval"):  # 这个网站单独设置了更长的请求间隔
        fetcher = own_fetcher = Fetcher(min_interval=float(cfg["min_interval"]))
    results, total = [], 0
    seen: set = set()
    run_started = db.now_iso()
    for cat, ccfg in category_configs(cfg):
        if categories and cat not in categories:
            continue
        _set(rid, msg=f"正在抓：{_cat_name(cat)}")
        r = crawl_category(ccfg, cat, fetcher, seen, run_started)
        results.append(r)
        total += r["n"]
        _set(rid, n=total)
    oks = [r for r in results if r["status"] == "ok"]
    if not results:
        status = "ok"
    elif len(oks) == len(results):
        status = "ok"
    else:
        status = "partial" if oks else results[0]["status"]
    msg = " · ".join(f"{_cat_name(r['cat'])} {r['n']}" + ("" if r["status"] == "ok" else "（失败）") for r in results)
    meta = next((r["meta"] for r in results if r.get("meta")), None)
    try:
        with db.connect() as conn:
            db.set_retailer_status(conn, rid, status, msg[:500], meta, ok=bool(oks))
    except sqlite3.OperationalError as e:
        _log(f"[{rid}] 状态保存失败：{e}")
    _set(rid, status=status, n=total, msg=msg)
    _log(f"[{rid}] 全部分类完成：{msg}")
    return {"id": rid, "status": status, "n": total, "requests": own_fetcher.request_count if own_fetcher else 0,
            "cats": [{k: v for k, v in r.items() if k != "meta"} for r in results]}


def run_crawl(retailer_ids: list[str] | None = None, categories: list[str] | None = None) -> dict:
    with _LOCK:
        if STATE["running"]:
            return {"status": "already_running"}
        STATE.update(running=True, started_at=db.now_iso(), finished_at=None, retailers={}, log=[])
    try:
        db.init()
        cfgs = load_retailers()
        with db.connect() as conn:
            db.sync_retailers(conn, cfgs)
            settings = db.get_settings(conn)
            run_id = conn.execute("INSERT INTO crawl_runs(started_at, status) VALUES(?, 'running')",
                                  (STATE["started_at"],)).lastrowid
        todo = [c for c in cfgs if c.get("enabled", True) and (not retailer_ids or c["id"] in retailer_ids)]
        for c in todo:
            _set(c["id"], status="queued", n=0, name=c["name"])
        # 品牌写法统一：从已有数据里全部网站给过的 vendor 学品牌（见 normalize.BrandRegistry）
        from .maintenance import load_brand_registry
        from .normalize import use_brand_registry
        use_brand_registry(load_brand_registry())
        fetcher = Fetcher(min_interval=1.5)
        with ThreadPoolExecutor(max_workers=int(settings.get("crawl_workers", 6))) as ex:
            results = list(ex.map(lambda c: crawl_one(c, fetcher, categories), todo))

        # 抓完之后的收尾工作
        try:
            from . import fx
            fx.refresh()
        except Exception as e:
            _log(f"汇率更新失败：{e}")
        try:
            from . import alerts
            n_alerts = alerts.check_watchlist()
            if n_alerts:
                _log(f"生成 {n_alerts} 条降价提醒")
        except Exception as e:
            _log(f"关注列表检查失败：{e}")

        n_requests = fetcher.request_count + sum(r.get("requests", 0) for r in results)  # 加上单独限速网站自己的请求数
        summary = {"results": results, "requests": n_requests}
        with db.connect() as conn:
            conn.execute("UPDATE crawl_runs SET finished_at=?, status='done', summary_json=? WHERE id=?",
                         (db.now_iso(), json.dumps(summary, ensure_ascii=False), run_id))
        _log(f"全部完成：{sum(r['n'] for r in results)} 个商品，{n_requests} 次请求")
        return summary
    finally:
        with _LOCK:
            STATE.update(running=False, finished_at=db.now_iso())


def start_background(retailer_ids: list[str] | None = None, categories: list[str] | None = None) -> bool:
    if STATE["running"]:
        return False
    threading.Thread(target=run_crawl, args=(retailer_ids, categories), daemon=True).start()
    return True
