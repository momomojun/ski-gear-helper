"""2026-10-25 自查发现的问题：每条都用真实标题做回归。"""
import json

from skideals import bindings, crawl, db, snapshot
from skideals.categories import assign, classify
from skideals.models import RawProduct, SizeOption
from skideals.normalize import normalize


# ---- 户外专区不能把滑雪分类页里的衣服挪走

def test_bare_tee_on_ski_page_stays_baselayer():
    assert classify("Merino Wool Long Sleeve Tee") != "outdoor_top"
    assert assign("baselayer", "Black Crows Merino Wool Long Sleeve Tee")[0] == "baselayer"


def test_shirt_tag_on_ski_page_does_not_move_item():
    # 标题写着 Jacket，网站标签里有 Shirts：留在雪服/中间层，不进户外上衣
    assert assign("jacket", "Pfeifer Corduroy Down Jacket", tags=["Shirts"])[0] != "outdoor_top"
    assert assign("midlayer", "Patagonia R1 Air Crew", tags=["Shirts", "Fleece"])[0] == "midlayer"


def test_explicit_outdoor_titles_still_move():
    # 雪服页里的雨衣：不当雪服（旧规则直接不要；网站标了 Rain Jackets 的挪去户外外套）
    assert assign("jacket", "Patagonia Women's Torrentshell 3L Rain Jacket")[0] != "jacket"
    assert assign("jacket", "Torrentshell Jacket", product_type="Rain Jackets")[0] == "outdoor_jacket"
    assert classify("Quick-Dry Shirt") == "outdoor_top"
    # 户外分类页里单独的 Shirt 照样算户外上衣
    assert assign("outdoor_top", "Capilene Cool Daily Shirt")[0] == "outdoor_top"


def test_ski_boot_bags_are_bags_not_shoe_parts():
    for t in ("Dakine Boot Locker Bag 69L", "Burton Gig Boot Tote Bag", "Rossignol Hero Boot Pro Bag"):
        assert assign("bag", t)[0] == "bag", t


# ---- 鞋：重算不能丢描述里的特征；这次没抓到详情时沿用上次的码制

def test_renormalize_keeps_description_features_for_shoes(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "s.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init()
    raw = RawProduct(retailer_id="rei", external_id="x", title="Oboz Sawtooth X Low Hiking Shoes - Men's",
                     vendor="Oboz", category="hiking_shoe", price=100, available=True, url="https://x.test/x",
                     body_text="Vibram outsole, full-grain leather upper", extra={"src_category": "hiking_shoe"},
                     sizes=[SizeOption("9")])
    lst = normalize(raw)
    assert "vibram" in lst.features
    with db.connect() as conn:
        conn.execute("INSERT INTO retailers(id,name,enabled) VALUES('rei','REI',1)")
        db.save_listing(conn, lst, db.now_iso(), db.today())
    from skideals import maintenance
    maintenance.renormalize(log=lambda *a: None)
    with db.connect() as conn:
        row = conn.execute("SELECT features, model_key FROM listings").fetchone()
    assert "vibram" in json.loads(row["features"])
    assert row["model_key"] == lst.model_key


def test_shoe_size_system_carried_when_detail_not_fetched(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "c.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init()
    with db.connect() as conn:
        conn.execute("INSERT INTO listings(retailer_id, external_id, category, meta) VALUES('rei','m1','hiking_shoe',?)",
                     (json.dumps({"shoe_size_system": "US", "shoe_width": "wide"}),))
    no_detail = RawProduct(retailer_id="rei", external_id="m1", title="Moab 3", url="https://x.test/m1", category="hiking_shoe")
    cache: dict = {}
    crawl._carry_shoe_context("rei", no_detail, cache)
    assert no_detail.extra == {"shoe_size_system": "US", "shoe_width": "wide"}
    fresh = RawProduct(retailer_id="rei", external_id="m1", title="Moab 3", url="https://x.test/m1", category="hiking_shoe",
                       extra={"shoe_size_system": "US"})   # 这次抓到了详情、楦宽混合 → 不补回旧楦宽
    crawl._carry_shoe_context("rei", fresh, cache)
    assert "shoe_width" not in fresh.extra


def test_gearx_colourway_and_wp_alias_group_with_rei():
    def key(title, vendor):
        return normalize(RawProduct(retailer_id="x", external_id=title, url="https://x.test", title=title, vendor=vendor,
                                    category="hiking_shoe", sizes=[SizeOption("9")])).model_key
    rei = key("KEEN Targhee IV Waterproof Hiking Shoes - Women's", "KEEN")
    assert key("Keen - Women's Targhee IV WP - Alloy/Granite Green", "Keen") == rei
    assert key("Keen - Women's Targhee IV WP - Black/Black", "Keen") == rei


# ---- 固定器推荐：尺码价格未知（None）不能让详情页报错

def test_binding_fit_price_ignores_unknown_size_prices():
    offer = {"cd": "new", "a": 1, "p": 300, "z": [["90mm", 1, None], ["100mm", 1, 280.0]]}
    assert bindings._fit_price(offer, 85) == 280.0
    assert bindings._fit_price({**offer, "z": [["90mm", 1, None]]}, 85) is None
    assert bindings._best_new({"o": [{"cd": "new", "a": 1, "p": None}, {"cd": "new", "a": 1, "p": 5}]})["p"] == 5


# ---- 数据快照：导出 → 还原 完全一致，个人数据不导出

def test_snapshot_round_trip_excludes_private_tables(tmp_path, monkeypatch):
    src = tmp_path / "src.db"
    monkeypatch.setattr(db, "DB_PATH", src)
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init()
    with db.connect() as conn:
        conn.execute("INSERT INTO retailers(id,name,enabled) VALUES('evo','evo',1)")
        conn.execute("INSERT INTO listings(retailer_id, external_id, title, price, last_seen, category) "
                     "VALUES('evo','a','Atomic Bent 100 – 中文 ✓',499.95,'2026-10-20T10:00:00','ski')")
        lid = conn.execute("SELECT id FROM listings").fetchone()[0]
        conn.execute("INSERT INTO price_history(listing_id, day, price) VALUES(?,?,?)", (lid, "2026-10-19", 549.95))
        conn.execute("INSERT INTO watchlist(model_key, label) VALUES('k','私人关注')")
        conn.execute("INSERT INTO settings(key, value) VALUES('notify_desktop','true')")
    out = tmp_path / "snap"
    manifest = snapshot.export(src, out, log=lambda *a: None)
    assert manifest["data_day"] == "2026-10-20"
    assert (out / "listings" / "evo.jsonl").exists() and (out / "price_history" / "evo.jsonl").exists()
    text = "".join(p.read_text(encoding="utf-8") for p in out.rglob("*.jsonl"))
    assert "私人关注" not in text and "notify_desktop" not in text
    dst = tmp_path / "dst.db"
    snapshot.import_(out, dst, log=lambda *a: None)
    import sqlite3
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    for t in ("listings", "price_history", "retailers"):
        assert a.execute(f"SELECT * FROM {t}").fetchall() == b.execute(f"SELECT * FROM {t}").fetchall()
    assert b.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0] == 0
    # 已有数据的库默认拒绝覆盖
    try:
        snapshot.import_(out, dst, log=lambda *a: None)
        raise AssertionError("should refuse")
    except FileExistsError:
        pass
