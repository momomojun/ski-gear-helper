"""固定器相关：套装 vs 分开买、给板身推荐能配的固定器。

常识（写进页面说明里）：
- 刹车宽度（brake width）要 ≥ 雪板腰宽，最多宽 15~20mm；太窄装不上，太宽刹车会刮雪/挂人
- DIN 值（脱落力）由雪具店按体重、身高、鞋底长度、水平设定；选固定器时让你的设定值落在范围中间，别贴近上限
- 登山板（touring）要配登山固定器 + 能行走的雪鞋；GripWalk 鞋底要配标 GW / MNC 的固定器
- 大部分店买“板 + 固定器”会免费安装（mount），单独买固定器再找店安装一般 $50–80
"""
from __future__ import annotations

from . import catalog
from .normalize import parse_key, split_tokens

DIN_CLASSES = [(0, 10, "入门/轻体重（DIN ≤ 10）"), (11, 13, "进阶（DIN 11–13）"), (14, 16, "高级/大体重（DIN 14–16）"),
               (17, 30, "专家/竞技（DIN ≥ 17）")]
_GENERIC = {"gw", "mnc", "id", "binding", "bindings", "with", "brake", "brakes", "b", "mm"}


def _best_new(fam: dict) -> dict | None:
    pool = [o for o in fam["o"] if o["cd"] == "new" and o["a"]]
    return min(pool, key=lambda o: o["p"]) if pool else None


def _fit_price(o: dict, waist: int | None) -> float | None:
    """这个报价里“刹车宽度装得上这块板（腰宽 ~ 腰宽+20mm）且有货”的最低价。
    报价没有尺码信息时只能用整体价；不知道腰宽时也用整体价。"""
    if o["cd"] != "new" or not o["a"]:
        return None
    if not waist or not o["z"]:
        return o["p"]
    fits = [z[2] for z in o["z"] if z[1] and str(z[0]).endswith("mm") and waist <= int(str(z[0])[:-2]) <= waist + 20]
    return min(fits, default=None)


def _best_fit(fam: dict, waist: int | None) -> tuple[float, dict] | None:
    best = None
    for o in fam["o"]:
        price = _fit_price(o, waist)
        if price and (best is None or price < best[0]):
            best = (price, o)
    return best


def match_binding(name: str | None, ski_brand: str | None = None, waist: int | None = None) -> list[dict]:
    """套装里写的固定器名（如 'strive 13 gw'）→ 固定器分类里的同款家族（可能多个：同名不同品牌，如 Atomic/Salomon Strive）。
    给了雪板腰宽时，价格只算刹车宽度装得上的尺码（同一款固定器，窄刹车和宽刹车常常价格不同、库存不同）。"""
    if not name:
        return []
    want = [t for t in split_tokens(name) if t not in _GENERIC]
    words = [t for t in want if not t.isdigit()]
    nums = [t for t in want if t.isdigit()]
    if not words:
        return []
    out = []
    for f in catalog.build("binding")["families"]:
        toks = set(parse_key(f["k"])["tokens"])
        brand_words = set(split_tokens(f["b"] or ""))  # 家族 token 里不含品牌名（"Marker Griffon 13" → griffon 13）
        need = [w for w in words if w not in brand_words]
        extra = toks - set(want) - _GENERIC
        # 名字要全部对上，而且对方不能多出一堆别的词（"M11" 不能配到 "SLR 11 GW m.bk"）
        if need and all(w in toks for w in need) and all(n in toks for n in nums) and len(extra) <= 1:
            best = _best_fit(f, waist)
            if best:
                out.append({"k": f["k"], "b": f["b"], "m": f["m"], "p": best[0], "r": best[1]["r"], "u": best[1]["u"],
                            "same_brand": bool(ski_brand and f["b"] and f["b"].lower() == ski_brand.lower())})
    return sorted(out, key=lambda x: (not x["same_brand"], x["p"]))


def package_analysis(key: str) -> dict | None:
    """含固定器的套装：和“板身 + 同款固定器分开买”比一比。"""
    pk = parse_key(key)
    if pk["category"] != "ski" or not pk["bindings"]:
        return None
    fam = catalog.family(key)
    if not fam:
        return None
    pack = _best_new(fam)
    flat_key = key[:-1] + "f"
    flat = catalog.family(flat_key)
    flat_best = _best_new(flat) if flat else None
    waist = fam.get("w") or (flat and flat.get("w"))
    bmatch = match_binding(fam.get("bn"), fam.get("b"), waist)
    out = {"binding_name": fam.get("bn"), "package": pack and {"p": pack["p"], "r": pack["r"], "u": pack["u"]},
           "flat": flat_best and {"k": flat_key, "p": flat_best["p"], "r": flat_best["r"]},
           "binding": bmatch[0] if bmatch else None}
    if pack and flat_best and bmatch:
        total = round(flat_best["p"] + bmatch[0]["p"], 2)
        out["separate_total"] = total
        out["diff"] = round(total - pack["p"], 2)  # >0：套装更便宜
    elif pack and not flat:
        out["note"] = "这个型号只有套装版（通常是带专用板座的系统板，固定器不能随便换）"
    return out


def suggestions(key: str, limit_per_class: int = 3) -> dict | None:
    """板身：推荐刹车宽度合适、有货、便宜的固定器，按 DIN 档位分组。"""
    pk = parse_key(key)
    if pk["category"] != "ski" or pk["bindings"]:
        return None
    fam = catalog.family(key)
    waist = fam and fam.get("w")
    if not waist:
        return {"waist": None, "groups": [], "note": "不知道这块板的腰宽，没法按刹车宽度筛选固定器"}
    touring_ski = fam.get("t") == "touring"
    kids_ski = pk["gclass"] == "k"
    groups = {label: [] for _, _, label in DIN_CLASSES}
    for f in catalog.build("binding")["families"]:
        sp = f.get("sp") or {}
        kids_binding = parse_key(f["k"])["gclass"] == "k"
        if kids_binding and not kids_ski:
            continue  # 成人板不推荐儿童固定器（DIN 太低、结构不同）
        brakes = [b for b in sp.get("brakes", []) if waist <= b <= waist + 20]
        if not brakes:
            continue
        is_touring = "touring" in (f.get("ft") or [])
        if touring_ski != is_touring and not ("mnc" in (f.get("ft") or []) and touring_ski):
            continue  # 登山板推荐登山/多标准固定器，高山板推荐高山固定器
        din = sp.get("din_max")
        if not din or (kids_ski and not kids_binding and din > 11) or (not kids_ski and din < 8):
            continue  # 儿童板：儿童固定器或 DIN 上限 ≤ 11 的成人入门款；成人板：DIN 上限 < 8 的多半是青少年款（Atomic Colt 7）
        best = _best_fit(f, waist)  # 只算“这个刹车宽度有货”的报价
        if not best:
            continue
        label = next(lb for lo, hi, lb in DIN_CLASSES if lo <= din <= hi)
        groups[label].append({"k": f["k"], "b": f["b"], "m": f["m"], "din": din, "brakes": brakes, "p": best[0],
                              "r": best[1]["r"], "u": best[1]["u"], "touring": is_touring})
    return {"waist": waist, "touring": touring_ski,
            "groups": [{"label": lb, "items": sorted(v, key=lambda x: x["p"])[:limit_per_class]}
                       for lb, v in groups.items() if v]}
