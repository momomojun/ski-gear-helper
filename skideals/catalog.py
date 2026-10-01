"""把数据库里的商品按“同款”分组，算出原价(MSRP)、折扣、历史低价等，供网页使用。

一个 family（型号家族）= 同分类 + 同品牌 + 同型号 + 同性别大类（双板再加“含不含固定器”）；
不同年份、不同网站的报价都在里面。数据按分类分别构建、分别缓存（全品类几万个商品，一次全给浏览器太大）。
"""
from __future__ import annotations

import json
import threading
from collections import Counter, defaultdict
from datetime import date, datetime

from . import db, fx
from .categories import norm_size
from .config import load_retailers
from .normalize import parse_key

_GENDER_W = {"title": 3.0, "brand": 3.0, "tags": 2.0, "collection": 2.0, "default": 0.3}
_TYPE_W = {"collection": 3.0, "product_type": 2.0, "tags": 2.0, "title": 2.0, "waist": 1.0}
_CACHE: dict[str, dict] = {}
_LOCK = threading.Lock()


def _vote(pairs, weights, prefer=None):
    score: dict[str, float] = defaultdict(float)
    for val, src in pairs:
        if val:
            score[val] += weights.get(src or "", 1.0)
    if not score:
        return None
    best = max(score.values())
    tied = [v for v, s in score.items() if s == best]
    return prefer if prefer in tied else tied[0]


def _mode_max(values):
    vals = [round(v, 2) for v in values if v]
    if not vals:
        return None
    counts = Counter(vals)
    top = max(counts.values())
    return max(v for v, c in counts.items() if c == top)


def _stamp(conn, category: str) -> tuple:
    row = conn.execute("SELECT MAX(last_seen), COUNT(*), SUM(active) FROM listings WHERE category=?",
                       (category,)).fetchone()
    return tuple(row) + (db.today(),)


def _load(conn, category: str):
    rates = fx.current()["rates"]
    retailers = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM retailers")}
    rows = conn.execute("""SELECT l.* FROM listings l JOIN retailers r ON r.id = l.retailer_id
                           WHERE l.active = 1 AND r.enabled = 1 AND l.category = ?""", (category,)).fetchall()
    ids = [r["id"] for r in rows]
    hist = {}
    for i in range(0, len(ids), 900):  # SQLite 变量个数有上限，分批
        chunk = ids[i:i + 900]
        q = (f"SELECT listing_id, MIN(price) mn, MAX(price) mx, COUNT(*) n, MIN(day) d0 FROM price_history "
             f"WHERE listing_id IN ({','.join('?' * len(chunk))}) GROUP BY listing_id")
        hist.update({r["listing_id"]: r for r in conn.execute(q, chunk)})
    return rates, retailers, hist, rows


def _offer(row, rates, hist, retailers) -> dict:
    cur = row["currency"] or "USD"
    price = fx.to_usd(row["price"], cur, rates)
    cmp_ = fx.to_usd(row["compare_at"], cur, rates)
    cat = row["category"] or "ski"
    length_based = cat in ("ski", "pole")
    sizes = []
    for s in json.loads(row["sizes_json"] or "[]"):
        # 尺码按当前规则重新规范化（旧数据里存的 n 可能是旧规则算的）；认不出的（多半是颜色/镜片名）不参与尺码筛选
        n = s.get("cm") if length_based else norm_size(cat, s.get("l"))
        if n:
            sizes.append([n, 1 if s.get("a") else 0, fx.to_usd(s.get("p"), cur, rates) or price])
    h = hist.get(row["id"])
    o = {"id": row["id"], "r": row["retailer_id"], "p": price, "c": cmp_, "y": row["year"], "cd": row["condition"],
         "a": 1 if row["available"] else 0, "z": sizes, "u": row["url"]}
    if row["prev_price"] and row["price_changed_at"]:
        days = (datetime.now() - datetime.fromisoformat(row["price_changed_at"])).days
        if days <= 21:
            o["pp"] = fx.to_usd(row["prev_price"], cur, rates)
            o["pd"] = row["price_changed_at"][:10]
    if h:
        o["n"] = h["n"]
        tracked_days = (date.today() - date.fromisoformat(h["d0"])).days if h["d0"] else 0
        if tracked_days >= 3 and row["price"] is not None and row["price"] <= h["mn"] + 0.01 and h["mx"] > h["mn"]:
            o["lo"] = 1  # 当前价 = 我们记录到的最低价（而且之前更贵过）
    if retailers.get(row["retailer_id"], {}).get("tier") == "brand":
        o["br"] = 1
    return o


def _msrp_by_year(offers: list[dict]) -> dict:
    by_year: dict = defaultdict(list)
    for o in offers:
        if o["cd"] == "new":
            by_year[o["y"]].append(o)
    out = {}
    for y, os_ in by_year.items():
        brand = [o["c"] or o["p"] for o in os_ if o.get("br")]
        if brand:
            out[y] = max(brand)
            continue
        out[y] = _mode_max([o["c"] for o in os_ if o["c"]] + [o["p"] for o in os_ if not o["c"]])
    latest = max((y for y in out if y), default=None)
    if None not in out and latest:
        out[None] = out[latest]
    return out


def _merge_specs(rows) -> dict:
    """同一型号多家店的规格合并：刹车宽度取并集，其他（DIN、雪鞋硬度、楦宽、容量）取众数。"""
    lists: dict[str, list] = defaultdict(list)
    for r in rows:
        for k, v in (json.loads(r["specs"]) if r["specs"] else {}).items():
            lists[k].append(v)
    merged: dict = {}
    for k, vals in lists.items():
        if k in ("brakes", "binding_brakes"):
            merged[k] = sorted({b for v in vals for b in v})
        else:
            merged[k] = json.loads(Counter(json.dumps(v) for v in vals).most_common(1)[0][0])
    return merged


def build(category: str = "ski", force: bool = False) -> dict:
    """返回某个分类的 {"families": [...], "retailers": {...}}。结果按数据版本缓存。"""
    with db.connect() as conn:
        stamp = _stamp(conn, category)
        with _LOCK:
            c = _CACHE.get(category)
            if not force and c and c["stamp"] == stamp:
                return c["data"]
        rates, retailers, hist, rows = _load(conn, category)
        spec_waist = {}
        if category == "ski":   # 商品页规格表里补到的腰宽（skideals/specs.py）
            from .specs import load_cache
            spec_waist = load_cache(conn)

    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        groups[row["model_key"]].append(row)

    families = []
    for key, rs in groups.items():
        offers = [_offer(r, rates, hist, retailers) for r in rs]
        offers = [o for o in offers if o["p"]]
        if not offers:
            continue
        msrp = _msrp_by_year(offers)
        for o in offers:
            m = msrp.get(o["y"]) or msrp.get(None)
            if m and o["p"] and m > o["p"]:
                o["off"] = round((1 - o["p"] / m) * 100)
            if m and o["c"] and o["c"] > m * 1.08:
                o["inf"] = 1  # 这家标的“原价”明显高于其他家 → 原价虚高
        # 展示名：优先用 evo 的写法（最规整），否则取最常见的写法
        names = Counter(r["model"] for r in rs if r["model"])
        evo_name = next((r["model"] for r in rs if r["retailer_id"] == "evo" and r["model"]), None)
        image = next((r["image"] for r in rs if r["retailer_id"] == "evo" and r["image"]), None) or \
            next((r["image"] for r in rs if r["image"]), None)
        gclass = rs[0]["gclass"]
        gender = {"w": "women", "k": "kids"}.get(gclass) or \
            _vote([(r["gender"], r["gender_src"]) for r in rs if r["gender"] in ("men", "unisex")], _GENDER_W,
                  prefer="unisex") or "unisex"
        display = evo_name or (names.most_common(1)[0][0] if names else (rs[0]["title"] or rs[0]["tokens"] or "?"))
        brands = Counter(r["brand"] for r in rs if r["brand"])  # 同一个分组里品牌 slug 相同，取最常见的写法
        fam = {"k": key, "b": brands.most_common(1)[0][0] if brands else "", "m": display, "g": gender,
               "img": image, "msrp": {str(y): v for y, v in msrp.items() if v},
               "o": sorted(offers, key=lambda o: o["p"])}
        if category == "ski":
            # 描述里读到的尺寸（spec）比型号名里的数字（Soul 92）可靠，但单个网站的描述也会读错 / 写错
            # （REI 的 Soul 92 读出 125），所以加权投票：spec 2 票、型号名 1 票，平票时 spec 优先
            votes, spec = Counter(), Counter()
            for r in rs:
                if r["waist"]:
                    votes[r["waist"]] += 2 if r["waist_src"] == "spec" else 1
                    spec[r["waist"]] += r["waist_src"] == "spec"
            w = max(votes, key=lambda v: (votes[v], spec[v])) if votes else None
            if not w:
                from .specs import cache_key
                w = spec_waist.get(cache_key(key))
            fam.update(t=_vote([(r["ski_type"], r["type_src"]) for r in rs], _TYPE_W), w=w, bd=rs[0]["bindings"])
            if rs[0]["bindings"]:
                bn = Counter(r["binding_name"] for r in rs if r["binding_name"]).most_common(1)
                fam["bn"] = bn[0][0] if bn else None
        feats = Counter(f for r in rs for f in (json.loads(r["features"]) if r["features"] else []))
        if feats:
            fam["ft"] = sorted(feats)
        sp = _merge_specs(rs)
        if sp:
            fam["sp"] = sp
        families.append(fam)

    cfg = {c["id"]: c for c in load_retailers()}
    data = {
        "category": category,
        "generated_at": db.now_iso(),
        "families": families,
        "retailers": {rid: {"name": r["name"], "tier": r["tier"], "country": r["country"], "url": r["url"],
                            "status": r["last_status"], "n": r["n_active"], "last_ok": r["last_ok_at"],
                            # 只有“接受其他店价格匹配”的才给前端做比价提示
                            "price_match": cfg.get(rid, {}).get("price_match")
                            if cfg.get(rid, {}).get("price_match_ok", True) else None}
                      for rid, r in retailers.items() if r["enabled"]},
    }
    with _LOCK:
        _CACHE[category] = {"stamp": stamp, "data": data}
    return data


def categories_summary() -> list[dict]:
    """每个分类的型号数、报价数、有货报价数、网站数（给顶部分类栏用）。"""
    from .categories import CATEGORIES
    with db.connect() as conn:
        rows = {r["category"]: dict(r) for r in conn.execute(
            """SELECT l.category, COUNT(DISTINCT l.model_key) fams, COUNT(*) offers,
                      SUM(CASE WHEN l.available=1 AND l.condition='new' THEN 1 ELSE 0 END) live,
                      COUNT(DISTINCT l.retailer_id) shops
               FROM listings l JOIN retailers r ON r.id=l.retailer_id
               WHERE l.active=1 AND r.enabled=1 GROUP BY l.category""")}
    return [{"id": c.id, "name": c.name, "group": c.group, "sizing": c.sizing, "gendered": c.gendered,
             **{k: (rows.get(c.id) or {}).get(k, 0) or 0 for k in ("fams", "offers", "live", "shops")}}
            for c in CATEGORIES]


def family(key: str) -> dict | None:
    cat = parse_key(key)["category"]
    return next((f for f in build(cat)["families"] if f["k"] == key), None)


def siblings(key: str) -> list[dict]:
    """同品牌的“近亲”：同型号不同性别/含不含固定器，或型号 token 只差一个词（如 Ti / C 版本）。"""
    me = parse_key(key)
    mine = set(me["tokens"])
    out = []
    for f in build(me["category"])["families"]:
        if f["k"] == key:
            continue
        other = parse_key(f["k"])
        if other["brand"] != me["brand"]:
            continue
        ot = set(other["tokens"])
        if ot == mine or (len(mine ^ ot) == 1 and min(len(mine), len(ot)) >= 1 and (mine <= ot or ot <= mine)):
            best = min(f["o"], key=lambda o: o["p"])
            out.append({"k": f["k"], "m": f["m"], "g": f["g"], "bd": f.get("bd", 0), "p": best["p"],
                        "n": len({o["r"] for o in f["o"]})})
    return sorted(out, key=lambda x: x["p"])[:12]


def _step_series(points: list, today: str) -> list:
    """稀疏价格历史（只在变化时记录）→ 延伸到今天，前端画阶梯线。"""
    if points and points[-1][0] < today:
        points = points + [[today, points[-1][1], points[-1][2]]]
    return points


def detail(key: str) -> dict | None:
    fam = family(key)
    if fam is None:
        return None
    with db.connect() as conn:
        rows = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM listings WHERE model_key=? AND active=1", (key,))}
        ids = list(rows)
        hist: dict[int, list] = defaultdict(list)
        if ids:
            q = f"SELECT * FROM price_history WHERE listing_id IN ({','.join('?' * len(ids))}) ORDER BY day"
            for h in conn.execute(q, ids):
                hist[h["listing_id"]].append([h["day"], h["price"], h["available"]])
        watch = conn.execute("SELECT * FROM watchlist WHERE model_key=?", (key,)).fetchone()
    rates = fx.current()["rates"]
    today = db.today()
    offers = []
    for o in fam["o"]:
        r = rows.get(o["id"])
        if not r:
            continue
        cur = r["currency"] or "USD"
        sizes = [{"cm": s.get("cm"), "n": norm_size(r["category"] or "ski", s.get("l")), "label": s.get("l"),
                  "a": bool(s.get("a")),
                  "p": fx.to_usd(s.get("p"), cur, rates)} for s in json.loads(r["sizes_json"] or "[]")]
        points = [[d, fx.to_usd(p, cur, rates), a] for d, p, a in hist.get(o["id"], [])][-365:]
        offers.append({**o, "title": r["title"], "gender": r["gender"], "gender_src": r["gender_src"],
                       "binding_name": r["binding_name"], "first_seen": r["first_seen"], "last_seen": r["last_seen"],
                       "sizes": sizes, "currency": cur, "history": _step_series(points, today)})
    # 每个“有变化的日子”的全网最低价（全新、有货）：历史是稀疏的，价格沿用最近一次记录
    days = sorted({d for o in offers if o["cd"] == "new" for d, _, _ in o["history"]})
    daily = []
    for d in days:
        best = None
        for o in offers:
            if o["cd"] != "new":
                continue
            last = None
            for hd, p, a in o["history"]:
                if hd > d:
                    break
                last = (p, a)
            if last and last[0] and last[1] and (best is None or last[0] < best):
                best = last[0]
        if best is not None:
            daily.append([d, best])
    return {**fam, "category": parse_key(key)["category"], "offers": offers, "daily_min": daily,
            "siblings": siblings(key), "watch": dict(watch) if watch else None}
