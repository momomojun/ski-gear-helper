"""双板规格补全：列表数据里没有腰宽的型号，去商品详情页的规格表里读（“Waist 96mm”“131/96/117”“Dimensions 128-96-112”）。

为什么需要：分类页 / Shopify 的商品数据里常常只有长度，没有尺寸；而“搭配固定器”要按腰宽挑刹车宽度、
找折扣页的“腰宽”筛选也要用到。详情页的规格表（很多店的规格是商品页模板渲染的，不在商品描述里）一般都有。

结果存在数据库 spec_cache 表（按“品牌 | 型号 token”，不分性别 / 含不含固定器——同一型号的雪板尺寸一样），
目录生成时自动补上；读过的型号不会重复请求（读不到的 30 天后再试）。

用法：python -m skideals specs        （对所有缺腰宽的双板型号最多试 4 个网站的商品页）
"""
from __future__ import annotations

import html as _html
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from . import db
from .net import BlockedError, Fetcher, FetchError, decode_html
from .normalize import parse_key

# 腰宽的写法（按可靠程度排列）：明确写着 waist / underfoot 的最可靠，其次是“尖-腰-尾”三段尺寸
_WAIST = [
    # “Waist 96mm”“Waist Width (mm): 96”；Peter Glenn 写成“keyfeature_Waist Width: 67”（前面是下划线，不能用 \b）
    re.compile(r"(?<![a-z])(?:waist|underfoot)(?:\s*width)?\s*(?:\(mm\))?\s*[:：\-–]?\s*(?P<w>\d{2,3})(?:\.\d)?\s*(?:mm)?\b", re.I),
    # “96mm waist”“74-mm waist”（Sun & Ski）“A 112-ish waist width”（evo）
    re.compile(r"\b(?P<w>\d{2,3})(?:\.\d)?\s*-?\s*(?:mm|ish)\s+(?:waist|underfoot)\b", re.I),
    re.compile(r"(?:ウエスト|センター|腰宽)\s*[:：]?\s*(?P<w>\d{2,3})"),
    # Black Crows 官网：“specs and technical details width 122 mm”“High floatation with 122mm at the waist”
    re.compile(r"\b(?P<w>\d{2,3})\s*mm\s+(?:at|under)\s+(?:the\s+)?(?:waist|foot)\b", re.I),
    re.compile(r"(?:specs?|technical details?|specifications?)\b.{0,40}?\bwidth\s*[:：]?\s*(?P<w>\d{2,3})\s*mm\b", re.I),
]
_RANGE_AFTER = re.compile(r"\s*(?:mm)?\s*[-–~]\s*(\d{2,3})(?!\d)")
_RANGE_BEFORE = re.compile(r"(\d{2,3})\s*(?:mm)?\s*[-–~]\s*$")


def _bucket(blob: str, m: re.Match) -> bool:
    """“Waist Width (mm) 60 - 79”这种是网站的腰宽分档（筛选项），不是这块板的腰宽（Sun & Ski 的每个商品页都有）。
    跨度小的（“Waist 98-100”，不同长度腰宽略有不同）不算分档，取第一个数。"""
    s, e = m.span("w")
    w = int(m.group("w"))
    after = _RANGE_AFTER.match(blob, e)
    before = _RANGE_BEFORE.search(blob[max(0, s - 14): s])
    return bool(after and int(after.group(1)) - w >= 8 or before and w - int(before.group(1)) >= 8)
# 三段尺寸：131/96/117、128-96-112、140 x 106 x 128；要满足 尖 > 腰 < 尾
_DIMS = re.compile(r"(?<![\d.])(1\d{2}|9\d)(?:\.\d)?\s*(?:mm)?\s*[-/x×|]\s*(\d{2,3})(?:\.\d)?\s*(?:mm)?\s*[-/x×|]\s*"
                   r"(1\d{2}|9\d)(?:\.\d)?(?![\d.])", re.I)
_DIMS_CONTEXT = re.compile(r"dimension|sidecut|tip|tail|shape|geometry|width|measurement|size|ラディウス|サイズ|尺寸", re.I)


def _text(page: str) -> str:
    page = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    page = re.sub(r"<[^>]+>", " ", page)
    return re.sub(r"\s+", " ", _html.unescape(page))


def waist_from_page(page: str) -> int | None:
    """从商品页里找腰宽（60–145mm）。先找明确写着 waist 的，再找带尺寸上下文的“尖-腰-尾”。"""
    text = _text(page)
    # JSON-LD / 页面脚本里的规格（有的店把规格放在脚本数据里）也看一眼
    scripts = " ".join(re.findall(r"<script[^>]*>(.*?)</script>", page, flags=re.S | re.I))[:200000]
    for blob in (text, re.sub(r"\\[nrt]|\\u00a0", " ", scripts)):
        for rx in _WAIST:
            for m in rx.finditer(blob):
                v = int(m.group("w"))
                if 60 <= v <= 145 and not _bucket(blob, m):
                    return v
    for m in _DIMS.finditer(text):
        tip, waist, tail = (int(x) for x in m.groups())
        ctx = text[max(0, m.start() - 120): m.start()]
        if 60 <= waist <= 145 and tip > waist < tail and _DIMS_CONTEXT.search(ctx):
            return waist
    return None


# 先试规格表最全的网站：品牌官网、专业店，再到综合零售商
# 实测（2026-09）商品页规格表里有尺寸的：Skis.com、Ski Essentials、Utah Ski Gear、Christy、品牌官网；evo / Ski Depot /
# The Ski Monster 的很多商品页没有尺寸，放在后面
_PREFER = ["skiscom", "skiessentials", "utahskigear", "christy", "blackcrows", "armada", "dps", "moment", "4frnt", "jskis",
           "icelantic", "liberty", "peterglenn", "rei", "sunandski", "evo", "alpineshop", "starthaus", "cripplecreek",
           "aspenskiandboard", "coloradoskishop", "skidepot", "theskimonster", "sportsbasement", "gearx", "jans"]


def cache_key(model_key: str) -> str:
    pk = parse_key(model_key)
    return f"{pk['brand']}|{' '.join(pk['tokens'])}"


def load_cache(conn) -> dict[str, int]:
    return {r["key"]: r["waist"] for r in conn.execute("SELECT key, waist FROM spec_cache WHERE waist IS NOT NULL")}


def backfill(log=print, max_tries: int = 4, workers: int = 8) -> dict:
    from . import catalog
    fams = catalog.build("ski", force=True)["families"]
    with db.connect() as conn:
        done = {r["key"]: (r["waist"], r["fetched_at"]) for r in conn.execute("SELECT key, waist, fetched_at FROM spec_cache")}
    retry_before = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    todo: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for f in fams:
        if f.get("w"):
            continue
        key = cache_key(f["k"])
        if key in done and (done[key][0] or done[key][1] > retry_before):
            continue
        for o in f["o"]:
            if o.get("u"):
                todo[key].append((o["r"], o["u"]))
    # 每个型号最多试 max_tries 个不同网站的商品页（优先规格表全的网站）
    order = {r: i for i, r in enumerate(_PREFER)}
    plans = {}
    for key, cands in todo.items():
        uniq, seen = [], set()
        for r, u in sorted(cands, key=lambda x: order.get(x[0], 99)):
            if r not in seen:
                seen.add(r)
                uniq.append((r, u))
        plans[key] = uniq[:max_tries]
    log(f"缺腰宽的型号 {len(plans)} 个，开始读商品页规格表……")

    fetcher = Fetcher(min_interval=2.0, retries=1, timeout=25)
    found: dict[str, tuple[int | None, str | None]] = {}
    blocked: set[str] = set()  # 商品页被反爬拦截的网站（如 REI），本次不再请求
    # 按网站分组并行（同一网站内部串行、限速）：每一轮每个型号试一个网站，读到了就不再试别的
    for attempt in range(max_tries):
        by_host: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for key, plan in plans.items():
            if key in found and found[key][0]:
                continue
            if attempt < len(plan):
                by_host[urlsplit(plan[attempt][1]).netloc].append((key, plan[attempt][1]))

        def run(items):
            out = []
            for key, url in items:
                w = None
                host = urlsplit(url).netloc
                if host not in blocked:
                    try:
                        w = waist_from_page(decode_html(fetcher.get(url)))
                    except BlockedError:
                        blocked.add(host)
                        log(f"  {host} 的商品页被反爬拦截，本次跳过这个网站")
                    except FetchError:
                        pass
                out.append((key, w, url))
            return out

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for res in ex.map(run, by_host.values()):
                for key, w, url in res:
                    if w or key not in found:
                        found[key] = (w, url)
        log(f"第 {attempt + 1} 轮：已读到 {sum(1 for w, _ in found.values() if w)} / {len(plans)} 个型号的腰宽")

    now = db.now_iso()
    with db.connect() as conn:
        for key, (w, url) in found.items():
            conn.execute("INSERT OR REPLACE INTO spec_cache(key, waist, url, fetched_at) VALUES(?, ?, ?, ?)", (key, w, url, now))
    ok = sum(1 for w, _ in found.values() if w)
    log(f"完成：{ok} 个型号补上了腰宽，{len(found) - ok} 个商品页里没有尺寸（{fetcher.request_count} 次请求）")
    return {"models": len(plans), "found": ok, "requests": fetcher.request_count}
