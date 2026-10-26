"""Shoe evidence must survive storage, catalog, alerts and later maintenance."""
import json

from skideals import catalog, db, maintenance
from skideals.alerts import best_offer
from skideals.models import RawProduct, SizeOption
from skideals.normalize import normalize


def test_shoe_size_evidence_survives_full_price_pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "shoes.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(catalog.fx, "current", lambda: {"rates": {"USD": 1, "CNY": 7, "JPY": 150}})
    monkeypatch.setattr(catalog, "_CACHE", {})
    db.init()
    raw = RawProduct(retailer_id="rei", external_id="moab", title="Merrell Moab 3 Hiking Shoes - Men's",
                     vendor="Merrell", category="hiking_shoe", price=80, available=True,
                     url="https://www.rei.com/product/moab", gender_hint="men",
                     product_type="Men's Hiking Shoes", extra={"shoe_size_system": "US", "src_category": "hiking_shoe"},
                     sizes=[SizeOption("9 Regular", price=100, url="https://www.rei.com/product/regular"),
                            SizeOption("9 Wide", price=80, url="https://www.rei.com/product/wide"),
                            SizeOption("9.5 Wide", price=60, available=False),
                            SizeOption("10 Wide", price=None)])
    lst = normalize(raw)
    with db.connect() as conn:
        conn.execute("INSERT INTO retailers(id,name,enabled) VALUES('rei','REI',1)")
        db.save_listing(conn, lst, db.now_iso(), db.today())
        stored = conn.execute("SELECT * FROM listings").fetchone()
        assert json.loads(stored["meta"])["shoe_size_system"] == "US"
        assert json.loads(stored["sizes_json"])[1]["n"] == "US:men:9:wide"
        wide = best_offer(conn, lst.model_key, size_label="US:men:9:wide")
        assert wide["usd"] == 80 and wide["url"].endswith("/wide")
        for key in ("EU:men:9:wide", "US:women:9:wide", "US:men:9.5:wide", "US:men:10:wide"):
            assert best_offer(conn, lst.model_key, size_label=key) is None
    fam = catalog.build("hiking_shoe")["families"][0]
    assert fam["o"][0]["z"][1][:3] == ["US:men:9:wide", 1, 80]
    assert catalog.detail(lst.model_key)["offers"][0]["sizes"][1]["n"] == "US:men:9:wide"
    maintenance.renormalize(log=lambda _: None)
    assert catalog.build("hiking_shoe")["families"][0]["k"] == lst.model_key
    with db.connect() as conn:
        assert best_offer(conn, lst.model_key, size_label="US:men:9:wide")["usd"] == 80
