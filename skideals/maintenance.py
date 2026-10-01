"""维护工具：标准化 / 分类规则改进后，不用重新抓网页，直接用新规则重算库里已有商品的品牌 / 型号 / 分组键 / 分类。

用法：python -m skideals renormalize
（性别、类型、腰宽这些依赖抓取时的分类页/详情页信息，数据库里没有原始材料，保持不变。
 下次正常抓取时，所有字段都会按新规则完整重算。）
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from . import db
from .config import load_retailers
from .normalize import (_TOKEN_ALIASES, BrandRegistry, canonical_brand, clean_model, detect_bindings, detect_brand,
                        known_brand, make_key,
                        slug, split_tokens, use_brand_registry)


def load_brand_registry() -> BrandRegistry:
    """从数据库里全部网站给过的 vendor（和已识别的品牌）学品牌写法；店名用来识别“vendor 填的是店名”的情况。"""
    names: Counter = Counter()
    with db.connect() as conn:
        for col in ("vendor", "brand"):
            for r in conn.execute(f"SELECT {col} AS n, COUNT(*) AS c FROM listings WHERE {col} IS NOT NULL GROUP BY {col}"):
                names[r["n"]] += r["c"]
    # 只有多品牌零售商需要防“vendor 填的是店名”；品牌官网的 vendor 本来就是品牌（Aztech、Ibex…）
    stores = {r["id"]: r["name"] for r in load_retailers() if not r.get("brand")}
    return BrandRegistry(dict(names), stores)


def _fix_brand(brand: str | None, title: str, store_brand: str | None) -> str | None:
    """品牌官网转卖的别家商品（Black Crows 官网的 "Marker Griffon 13 ID"）：标题开头是另一个品牌就用它。"""
    if not store_brand:
        return brand
    own = known_brand(store_brand) or store_brand
    if (known_brand(brand) or brand) != own:
        return brand
    in_title = known_brand(title)
    return in_title if in_title and in_title != own else brand


def _reassign(conn, log) -> Counter:
    """存了原始分类信息（meta）的商品：按“来源分类页”分批，用新的分类规则重新判定（含混装页判断）。"""
    from .categories import assign, decide_batch
    groups: dict[tuple, list] = defaultdict(list)
    for r in conn.execute("SELECT id, retailer_id, title, url, vendor, category, sizes_json, meta FROM listings "
                          "WHERE active=1 AND meta IS NOT NULL"):
        m = json.loads(r["meta"])
        if m.get("src") and m["src"] != "ski":
            groups[(r["retailer_id"], m["src"])].append((r, m))
    moved: Counter = Counter()
    for (rid, src), rows in groups.items():
        results = [assign(src, r["title"], m.get("pt"), m.get("tags"), m.get("opts"), r["url"],
                          [s["cm"] for s in json.loads(r["sizes_json"] or "[]") if s.get("cm")], r["vendor"]) for r, m in rows]
        finals, _ = decide_batch(src, results)
        for (r, _m), final in zip(rows, finals):
            if final is None:
                conn.execute("UPDATE listings SET active=0 WHERE id=?", (r["id"],))
                moved[f"{r['category']}→不要"] += 1
            elif final != r["category"]:
                _move(conn, r["id"], final)   # 分组键在 renormalize 主循环里统一重算
                moved[f"{r['category']}→{final}"] += 1
    return moved


def _move(conn, lid: int, cat: str) -> None:
    """商品改判到别的分类：和分类有关的字段按新分类重算（规格、特征、不分男女的分类只认标题里的儿童/女款…）。"""
    from .categories import BY_ID, features, norm_size, specs
    from .normalize import _KIDS_STRONG, _WOMEN, detect_bindings, fold
    r = conn.execute("SELECT title, sizes_json, gender, gender_src, gclass FROM listings WHERE id=?", (lid,)).fetchone()
    title = r["title"] or ""
    sp = specs(cat, title)
    if cat == "binding":
        brakes = {int(n[:-2]) for n in (norm_size(cat, s.get("l")) for s in json.loads(r["sizes_json"] or "[]"))
                  if n and n.endswith("mm")} | set(sp.get("brakes", []))
        if brakes:
            sp["brakes"] = sorted(brakes)
    gender, gsrc, gclass = r["gender"], r["gender_src"], r["gclass"]
    cdef = BY_ID.get(cat)
    if cdef is not None and not cdef.gendered:
        t = fold(title)
        gender, gsrc, gclass = (("kids", "title", "k") if _KIDS_STRONG.search(t) else
                                ("women", "title", "w") if _WOMEN.search(t) else ("unisex", "default", "a"))
    has_bind, bind_name = detect_bindings(title) if cat == "ski" else (False, None)
    ft = features(cat, title)
    conn.execute("UPDATE listings SET category=?, features=?, specs=?, gender=?, gender_src=?, gclass=?, bindings=?, "
                 "binding_name=?, ski_type=CASE WHEN ?='ski' THEN ski_type END, waist=CASE WHEN ?='ski' THEN waist END "
                 "WHERE id=?", (cat, json.dumps(ft) if ft else None, json.dumps(sp) if sp else None, gender, gsrc, gclass,
                                int(has_bind), bind_name, cat, cat, lid))


def _drop_bad_skis(conn) -> int:
    """双板分类：十年以上的老板子、标题明显不是雪板的（品牌官网双板页里的棒球帽）→ 下架。"""
    from .adapters.base import NOT_SKI_RE
    from .crawl import OLD_SKI_YEARS, OLD_YEAR_RE, current_season
    n = conn.execute("UPDATE listings SET active=0 WHERE active=1 AND category='ski' AND year IS NOT NULL AND year <= ?",
                     (current_season() - OLD_SKI_YEARS,)).rowcount
    import re
    from .categories import classify
    for r in conn.execute("SELECT id, title FROM listings WHERE active=1 AND category='ski'").fetchall():
        title = r["title"] or ""
        if NOT_SKI_RE.search(title) or OLD_YEAR_RE.search(title):
            conn.execute("UPDATE listings SET active=0 WHERE id=?", (r["id"],))
            n += 1
        elif classify(title) == "binding" and not re.search(r"\bskis?\b", re.sub(r"\bski bindings?\b", " ", title, flags=re.I),
                                                              re.I):
            _move(conn, r["id"], "binding")   # 双板页里单卖的固定器（“Marker F10 Tour Ski Bindings”）
    return n


def renormalize(log=print) -> dict:
    reg = load_brand_registry()
    use_brand_registry(reg)
    brands = {r["id"]: r.get("brand") for r in load_retailers()}
    changed: Counter = Counter()
    with db.connect() as conn:
        moved = _reassign(conn, log)
        dropped = _drop_bad_skis(conn)
        if dropped:
            log(f"双板：下架 {dropped} 个（十年以上的老款 / 不是雪板）")
        rows = conn.execute("SELECT id, retailer_id, title, vendor, brand, model, tokens, gclass, bindings, binding_name, "
                            "category, model_key, meta FROM listings").fetchall()
        for r in rows:
            cat = r["category"] or "ski"
            title = r["title"] or ""
            store = reg.stores.get(r["retailer_id"])
            if r["vendor"] is not None:  # 新数据存了网站原始 vendor：和抓取时走完全一样的流程
                brand = detect_brand(r["vendor"], title, store)
                display, tokens, _, _ = clean_model(title, brand, r["vendor"], cat)
            else:
                # 旧数据没存 vendor（抓取时会把 vendor 名从标题里删掉，比如 "Boulder Gear"、"Darn Tough Vermont"），
                # 重算可能多出这些词 → 只接受“只删词”的变化（去颜色、去刹车宽度、改正品牌），其余等下次抓取再更新
                brand = detect_brand(None, title, store) or canonical_brand(
                    _fix_brand(r["brand"], title, brands.get(r["retailer_id"])))
                display, tokens, _, _ = clean_model(title, brand, brand, cat)
                old = {a for t in (r["tokens"] or "").split() for a in _TOKEN_ALIASES.get(t, [t])}  # 旧 token 也套用新别名
                if not tokens or not set(tokens) <= old:
                    tokens = (r["tokens"] or "").split()
                    display = r["model"] or display
            if not tokens:
                tokens = split_tokens(title)[:6] or ["unknown"]
            if not display.strip():
                display = title.strip()[:80]
            tok = " ".join(sorted(set(tokens)))
            bindings, bname = bool(r["bindings"]), r["binding_name"]
            if cat == "ski":
                # 套装识别也按新规则重算（“Wildcat 80 Ti Shift X EL 9.0” 这种没写 + / with 的系统板）
                m = json.loads(r["meta"]) if r["meta"] else {}
                has_bind, name = detect_bindings(title, m.get("pt"), m.get("tags"))
                if has_bind != bindings:
                    bindings, bname = has_bind, name
                key = make_key(brand, tokens, r["gclass"], bindings)
            else:
                key = f"{cat}|{slug(brand or 'unknown')}|{tok}|{r['gclass']}"
            if (key, brand, display, tok, bindings) != (r["model_key"], r["brand"], r["model"], r["tokens"], bool(r["bindings"])):
                conn.execute("UPDATE listings SET brand=?, model=?, tokens=?, model_key=?, bindings=?, binding_name=? WHERE id=?",
                             (brand, display, tok, key, int(bindings), bname, r["id"]))
                changed[cat] += 1
    total = sum(changed.values())
    if moved:
        log("分类重新判定：" + " · ".join(f"{k} {n}" for k, n in moved.most_common()))
    log(f"重新整理 {len(rows)} 个商品，更新了 {total} 个：" +
        (" · ".join(f"{c} {n}" for c, n in changed.most_common()) or "无变化"))
    return {"total": len(rows), "changed": total, "by_category": dict(changed), "moved": dict(moved)}
