"""本地网页服务（FastAPI）。只监听 127.0.0.1，外网访问不到。

安全：
- TrustedHostMiddleware：只接受 Host=127.0.0.1/localhost，防 DNS rebinding
- 所有写操作（POST/PUT/DELETE）必须带自定义请求头 X-SkiDeals —— 别的网站无法跨域伪造这个头，防 CSRF
"""
from __future__ import annotations

import json
import threading
import time
import webbrowser
from datetime import datetime, timedelta

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import alerts, catalog, crawl, db, fx, trust
from .config import HOST, PORT, WEB_DIR, load_retailers
from .intl import service as intl

app = FastAPI(title="SkiDeals 双板比价", docs_url="/api/docs", redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=2000)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    if request.method in ("POST", "PUT", "DELETE", "PATCH") and request.headers.get("x-skideals") != "1":
        return JSONResponse({"detail": "missing X-SkiDeals header"}, status_code=403)
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"  # 页面文件每次都向服务器确认（ETag），更新代码后立即生效
    return resp


# ------------------------------------------------------------------ 数据

@app.get("/api/categories")
def api_categories():
    from .categories import APPAREL_ORDER, FEATURE_LABELS, GROUPS
    return {"categories": catalog.categories_summary(), "groups": GROUPS, "features": FEATURE_LABELS,
            "size_order": APPAREL_ORDER}


@app.get("/api/catalog")
def api_catalog(cat: str = "ski"):
    from .categories import BY_ID
    if cat not in BY_ID:
        raise HTTPException(404, "没有这个分类")
    return catalog.build(cat)


@app.get("/api/model")
def api_model(key: str):
    d = catalog.detail(key)
    if d is None:
        raise HTTPException(404, "型号不存在或已下架")
    if d.get("category") == "ski":  # 固定器：套装 vs 分开买；板身推荐可配的固定器
        from . import bindings
        d["package"] = bindings.package_analysis(key)
        d["binding_suggest"] = bindings.suggestions(key)
    return d


@app.get("/api/meta")
def api_meta():
    with db.connect() as conn:
        last = conn.execute("SELECT * FROM crawl_runs WHERE status='done' ORDER BY id DESC LIMIT 1").fetchone()
        unseen = conn.execute("SELECT COUNT(*) FROM alerts WHERE seen=0").fetchone()[0]
        n_watch = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        settings = db.get_settings(conn)
    return {
        "last_crawl": last["finished_at"] if last else None,
        "crawl_running": crawl.STATE["running"],
        "unseen_alerts": unseen,
        "n_watch": n_watch,
        "settings": settings,
        "fx": fx.current(),
        "season": crawl.current_season(),
        "today": db.today(),
    }


# ------------------------------------------------------------------ 抓取

@app.post("/api/crawl")
def api_crawl(body: dict = Body(default={})):
    return {"started": crawl.start_background(body.get("ids") or None, body.get("categories") or None)}


@app.get("/api/crawl/status")
def api_crawl_status():
    return crawl.STATE


# ------------------------------------------------------------------ 国际比价

@app.get("/api/intl")
def api_intl(key: str):
    return intl.get(key)


@app.post("/api/intl/refresh")
def api_intl_refresh(body: dict = Body(...)):
    try:
        return intl.refresh_jp(body["key"])
    except KeyError:
        raise HTTPException(404, "型号不存在")


@app.post("/api/intl/manual")
def api_intl_manual(body: dict = Body(...)):
    try:
        price = float(body["price"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "请填写价格")
    if price <= 0 or body.get("currency") not in ("CNY", "JPY", "USD"):
        raise HTTPException(400, "价格或币种不正确")
    if body.get("country") not in ("CN", "JP"):
        raise HTTPException(400, "国家只能是 CN 或 JP")
    url = (body.get("url") or "").strip() or None
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(400, "链接需要以 http(s):// 开头")
    year = body.get("year")
    intl.add_manual(body["key"], body["country"], price, body["currency"], url=url,
                    title=(body.get("title") or "").strip()[:200] or None,
                    shop=(body.get("shop") or "").strip()[:60] or None,
                    year=int(year) if year else None, bindings=body.get("bindings"),
                    note=(body.get("note") or "").strip()[:200] or None)
    return intl.get(body["key"])


@app.delete("/api/intl/entry/{entry_id}")
def api_intl_delete(entry_id: int, key: str):
    intl.delete_entry(entry_id)
    return intl.get(key)


# ------------------------------------------------------------------ 关注 & 提醒

@app.get("/api/watchlist")
def api_watchlist():
    out = []
    with db.connect() as conn:
        for w in conn.execute("SELECT * FROM watchlist ORDER BY created_at DESC").fetchall():
            fam = catalog.family(w["model_key"])
            best = alerts.best_offer(conn, w["model_key"], w["year"], w["length_cm"], w["size_label"])
            out.append({**dict(w), "family": fam and {k: fam.get(k) for k in ("k", "b", "m", "g", "img", "t", "w")},
                        "best": best, "gone": fam is None})
    return out


@app.post("/api/watchlist")
def api_watch_add(body: dict = Body(...)):
    key = body.get("key")
    fam = catalog.family(key) if key else None
    if fam is None:
        raise HTTPException(404, "型号不存在")

    def num(v, cast=float):
        return cast(v) if v not in (None, "") else None

    with db.connect() as conn:
        size_label = (body.get("size_label") or "").strip()[:12] or None
        best = alerts.best_offer(conn, key, num(body.get("year"), int), num(body.get("length_cm"), int), size_label)
        conn.execute("""INSERT INTO watchlist(model_key, label, year, length_cm, target_price, created_at, last_best, note,
                                              size_label)
                        VALUES(?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(model_key) DO UPDATE SET year=excluded.year, length_cm=excluded.length_cm,
                          target_price=excluded.target_price, note=excluded.note, size_label=excluded.size_label""",
                     (key, f"{fam['b']} {fam['m']}", num(body.get("year"), int), num(body.get("length_cm"), int),
                      num(body.get("target_price")), db.now_iso(), best["usd"] if best else None,
                      (body.get("note") or "")[:200] or None, size_label))
    return {"ok": True}


@app.delete("/api/watchlist")
def api_watch_del(key: str):
    with db.connect() as conn:
        conn.execute("DELETE FROM watchlist WHERE model_key=?", (key,))
    return {"ok": True}


@app.get("/api/alerts")
def api_alerts():
    with db.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT 100")]


@app.post("/api/alerts/seen")
def api_alerts_seen():
    with db.connect() as conn:
        conn.execute("UPDATE alerts SET seen=1 WHERE seen=0")
    return {"ok": True}


# ------------------------------------------------------------------ 网站可信度

@app.post("/api/trust/check")
def api_trust_check(body: dict = Body(...)):
    url = (body.get("url") or "").strip()
    if not url or len(url) > 300 or " " in url:
        raise HTTPException(400, "请输入网址，例如 https://www.example.com")
    try:
        return trust.check_site(url)
    except Exception as e:
        raise HTTPException(500, f"检测失败：{e}")


@app.get("/api/trust/retailers")
def api_trust_retailers():
    cfgs = load_retailers()
    with db.connect() as conn:
        status = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM retailers")}
    out = []
    for c in cfgs:
        domain = trust.split_domain(c["url"])[0]
        st = status.get(c["id"], {})
        rep = trust.cached(domain)
        out.append({"id": c["id"], "name": c["name"], "url": c["url"], "tier": c["tier"], "country": c["country"],
                    "cats": json.loads(st["cats_json"]) if st.get("cats_json") else {},
                    "cat_config": sorted(k for k, v in c.get("by_category", {}).items() if v.get("enabled", True) is not False),
                    "enabled": c.get("enabled", True), "adapter": c.get("adapter", "shopify"),
                    "notes": c.get("notes"), "price_match": c.get("price_match"),
                    "dd": trust.due_diligence(domain),
                    "status": st.get("last_status"), "msg": st.get("last_msg"),
                    "last_ok": st.get("last_ok_at"), "n": st.get("n_active"),
                    "meta": json.loads(st["meta_json"]) if st.get("meta_json") else None,
                    "report": rep})
    return out


@app.post("/api/trust/recheck-all")
def api_trust_recheck():
    threading.Thread(target=trust.check_all_retailers, daemon=True).start()
    return {"started": True}


# ------------------------------------------------------------------ 设置

@app.get("/api/settings")
def api_settings():
    return db.get_settings()


@app.put("/api/settings")
def api_settings_put(body: dict = Body(...)):
    with db.connect() as conn:
        db.put_settings(conn, body)
    return db.get_settings()


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


# ------------------------------------------------------------------ 后台任务

def _auto_crawl_loop():
    """网页开着时自动更新：距离上次成功抓取超过 N 小时（默认 24）就自动抓一次。"""
    time.sleep(20)
    while True:
        try:
            hours = float(db.get_settings().get("auto_crawl_hours") or 0)
            if hours > 0 and not crawl.STATE["running"]:
                with db.connect() as conn:
                    row = conn.execute("SELECT finished_at FROM crawl_runs WHERE status='done' "
                                       "ORDER BY id DESC LIMIT 1").fetchone()
                last = datetime.fromisoformat(row["finished_at"]) if row else None
                if last is None or datetime.now() - last > timedelta(hours=hours):
                    crawl.start_background()
        except Exception as e:
            print("自动更新检查失败：", e)
        time.sleep(1800)


def _auto_trust_check():
    """首次运行 / 超过 30 天：在后台重新核验注册表里的网站。"""
    time.sleep(5)
    for c in load_retailers():
        if not c.get("enabled", True):
            continue
        rep = trust.cached(trust.split_domain(c["url"])[0])
        if rep and datetime.fromisoformat(rep["checked_at"]) > datetime.now() - timedelta(days=30):
            continue
        try:
            trust.check_site(c["url"])
        except Exception as e:
            print(f"核验 {c['id']} 失败：{e}")


def _already_running(port: int) -> bool:
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/meta", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def serve(open_browser: bool = False, port: int | None = None) -> None:
    import uvicorn
    port = port or PORT
    url = f"http://{HOST}:{port}/"
    if _already_running(port):  # 重复双击 start.bat：不再报“端口被占用”，直接打开已有的页面
        print(f"滑雪装备小帮手已经在运行：{url}" + ("（已为你打开浏览器）" if open_browser else ""))
        if open_browser:
            webbrowser.open(url)
        return
    db.init()
    with db.connect() as conn:
        db.sync_retailers(conn, load_retailers())
    threading.Thread(target=_auto_crawl_loop, daemon=True).start()
    threading.Thread(target=_auto_trust_check, daemon=True).start()
    print(f"\n  ❄  滑雪装备小帮手已启动：{url}\n     关闭这个窗口即可退出。\n", flush=True)
    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=HOST, port=port, log_level="warning")
