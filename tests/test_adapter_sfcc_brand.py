"""SFCC 品牌官网（Rossignol / Dynastar-Lange / Giro）：分类页卡片解析、翻页、性别。不联网。
页面片段按真实结构手写成最小样本。"""
import html
import json

from skideals.adapters.sfcc_brand import (DynastarLangeAdapter, GiroAdapter, RossignolAdapter, brand_name,
                                          parse_brand_tiles)

QUIET = lambda m: None  # noqa: E731


def _data(d: dict) -> str:
    return html.escape(json.dumps(d), quote=True)


def ross_tile(pid, name, price, lst=None, sizes=(), gender="Men", season="Fall Winter 26/27", brand="ROSSIGNOL"):
    gtm = {"item_id": pid, "item_name": name, "currency": "USD", "price": price, "item_brand": brand,
           "item_stock": "in stock", "item_category2": gender, "item_category3": "Skis & Snowboard equipment",
           "item_category4": "Alpine skis", "item_category5": season}
    del_html = (f'<del class="reduced-price-plp"><span class="strike-through list"><span class="value" content="{lst}">'
                f'${lst}</span></span></del>') if lst else ""
    pct = (f'<span class="sales discount-percentage"><span class="value" content="40">Save 40%</span></span>'
           if lst else "")
    size_html = "".join(f'<a class="size-variation {"" if ok else "line-through"}" href="/us-en/x.html?size={s}" '
                        f'data-pid="{pid}{s}">{s}</a>' for s, ok in sizes)
    return f"""
<div class="product js-gtm-product" data-pid="{pid}">
  <div class="product-tile plp-product-tile" data-product-data="{_data(gtm)}">
    <div class="image-container"><a href="/us-en/{pid.lower()}.html"><picture>
      <img class='tile-image ski-tile-image' src="https://www.rossignol.com/dw/image/{pid}.jpg?sh=300"/></picture></a></div>
    <h2 class="tile-header"><a class="link" href="/us-en/{pid.lower()}.html">{name}</a></h2>
    <div class="price"><span class="d-flex display-custom-prices">
      <span class="sales salesPrice"><span class="value" content="{price}">${price}</span>{del_html}</span>{pct}
    </span></div>
    <div class="product-variations"><div class="sizes-variations">{size_html}</div></div>
  </div>
</div>"""


ROSS_PAGE = "<html><body>" + "".join([
    ross_tile("RAOPR02000", "Rossignol Men Ski's FORZA 70' TI KONECT", 1099.95,
              sizes=[("163", True), ("173", False)]),
    ross_tile("RAPHE01000", "HERO MASTER LT R22 ALPINE SKIS", 1429.95, gender="Unisex",
              season="Fall Winter 27/28", sizes=[("173", True)]),
    ross_tile("RKHW502000", "Women's Experience 80 Skis", 559.95, lst=799.95, gender="Women", season="Fall Winter 25/26",
              sizes=[("150", False), ("158", True)]),
    ross_tile("RBP3140000", "Men's Alltrack 110 Ski Boots", 699.95, sizes=[("26.5", True)]),   # 雪鞋：双板分类不要
]) + "</body></html>"


class FakeFetcher:
    def __init__(self, pages):
        self.pages, self.urls = pages, []

    def get_html(self, url):
        self.urls.append(url)
        return self.pages[url]


def test_rossignol_tiles():
    tiles = {t["pid"]: t for t in parse_brand_tiles(ROSS_PAGE)}
    t = tiles["RKHW502000"]
    assert (t["sale"], t["list"]) == (559.95, 799.95)         # 跳过 “Save 40%” 那个 .sales
    assert t["sizes"] == [("150", False), ("158", True)]      # line-through = 缺货
    assert (t["gender"], t["brand"], t["year"], t["season"]) == ("women", "ROSSIGNOL", 2026, "25/26")
    assert t["href"] == "/us-en/rkhw502000.html" and t["image"].endswith("RKHW502000.jpg?sh=300")
    assert tiles["RAOPR02000"]["list"] is None


def test_rossignol_listing():
    url = ("https://www.rossignol.com/us-en/sports/alpine-ski/skis?prefn1=marketOnline&prefv1=true"
           "&start=0&sz=100")
    f = FakeFetcher({url: ROSS_PAGE})
    cfg = {"id": "rossignol", "url": "https://www.rossignol.com/us-en", "name": "Rossignol", "category": "ski"}
    ps = {p.external_id: p for p in RossignolAdapter(cfg, f, QUIET).fetch()}
    assert f.urls == [url]                                    # 内置默认分类页；4 张卡片 < 100 → 不再翻页
    assert set(ps) == {"RAOPR02000", "RAPHE01000", "RKHW502000"}
    forza = ps["RAOPR02000"]
    assert forza.title == "Rossignol Men Ski's FORZA 70' TI KONECT 2027"
    assert forza.url == "https://www.rossignol.com/us-en/raopr02000.html"
    assert (forza.vendor, forza.price, forza.compare_at, forza.currency) == ("Rossignol", 1099.95, None, "USD")
    assert [(s.label, s.cm, s.available) for s in forza.sizes] == [("163", 163, True), ("173", 173, False)]
    assert forza.gender_hint == "men" and forza.available is True
    assert ps["RAPHE01000"].title == "Rossignol HERO MASTER LT R22 ALPINE SKIS"   # 下一季（27/28）不加年份
    w = ps["RKHW502000"]
    assert (w.price, w.compare_at, w.gender_hint) == (559.95, 799.95, "women")
    assert w.sizes[1].compare_at == 799.95


def test_dynastar_page_gender_wins_and_pagination():
    base = "https://www.dynastar-lange.com/us-en/ski-boots/men?prefn1=marketOnline&prefv1=true"
    tiles = [ross_tile(f"LBP70{i:02d}000", f"Lange Code {i} Ski Boots", 849.95, gender="Unisex", brand="LANGE",
                       sizes=[("26/26.5", True)]) for i in range(3)]
    f = FakeFetcher({f"{base}&start=0&sz=2": "".join(tiles[:2]), f"{base}&start=2&sz=2": tiles[2]})
    cfg = {"id": "dynastar-lange", "url": "https://www.dynastar-lange.com/us-en", "name": "Dynastar",
           "category": "boot", "page_size": 2, "categories": [{"path": "/ski-boots/men", "gender": "men"}]}
    ps = list(DynastarLangeAdapter(cfg, f, QUIET).fetch())
    assert len(f.urls) == 2 and len(ps) == 3                  # 第 2 页只有 1 张 < 2 → 停
    assert {p.gender_hint for p in ps} == {"men"}             # 分类页的性别优先于 item_category2 的 Unisex
    assert {p.vendor for p in ps} == {"Lange"} and ps[0].sizes[0].label == "26/26.5"


def giro_tile(pid, name, price, lst=None, swatches=(), gender="Unisex"):
    gtm = {"item_id": "GR-1", "item_name": name, "item_brand": "Giro", "item_category": "Snow",
           "item_category2": gender, "item_category3": "Helmets", "item_category4": "Half Shell", "price": price}
    del_html = (f'<del><span class="strike-through list"><span class="value" content="{lst}">${lst}</span></span></del>'
                if lst else "")
    sw = "".join(f'<span class="swatch" data-color-id="{i}" data-color-list-price="{"$" + str(lp) if lp else ""}" '
                 f'data-color-sale-price="${sp}"></span>' for i, (sp, lp) in enumerate(swatches))
    href = f"/product/{name.lower().replace(' ', '-')}/{pid}.html?dwvar_{pid}_color=0208&cgid=snow-helmets"
    return f"""
<div class="product" data-pid="{pid}" data-currency="USD" data-gtmdata="{_data(gtm)}">
  <div class="product-tile always-show" data-pid="{pid}">
    <img class="product-tile-image tile-image lazyload" src="data:image/gif;base64,R0lGOD"
         data-src="https://vault.widen.net/content/{pid}?w=400&amp;h=400">
    <div class="pdp-link"><a class="link" href="{href}">{name}</a></div>
    <div class="price"><span>{del_html}<span class="sales"><span class="value" content="{price}"></span>${price}</span></span></div>
    <div class="color-swatches">{sw}</div>
  </div>
</div>"""


def test_giro_color_groups_and_swatch_prices():
    page = "".join([
        giro_tile("2000049", "Jackson Mips Helmet", 219.95, swatches=[(219.95, None), (140.23, 219.95)]),
        giro_tile("VG-2000049-0520", "Jackson Mips Helmet", 219.95),        # 颜色组卡片：并到款号 2000049
        giro_tile("VG-2000052-1247", "Neo Mips Helmet", 105.38, lst=164.95),  # 只有颜色组卡片也能用
        giro_tile("2000099", "Avera Mips Helmet", 189.95, gender="Women"),
    ])
    url = "https://www.giro.com/snow/helmets/?start=0&sz=60"
    f = FakeFetcher({url: page})
    cfg = {"id": "giro", "url": "https://www.giro.com", "name": "Giro", "category": "helmet",
           "categories": [{"path": "/snow/helmets/"}]}
    ps = {p.external_id: p for p in GiroAdapter(cfg, f, QUIET).fetch()}
    assert f.urls == [url]                                    # sz 放在最后（robots 禁止 /*sz=*=*）
    assert set(ps) == {"2000049", "2000052", "2000099"}
    j = ps["2000049"]
    assert (j.title, j.price, j.compare_at, j.vendor) == ("Giro Jackson Mips Helmet", 140.23, 219.95, "Giro")
    assert j.url == "https://www.giro.com/product/jackson-mips-helmet/2000049.html"   # 去掉 dwvar_ 参数
    assert j.image == "https://vault.widen.net/content/2000049?w=400&h=400"
    assert j.sizes == [] and j.available is None and j.gender_hint == "unisex"
    assert (ps["2000052"].price, ps["2000052"].compare_at) == (105.38, 164.95)
    assert ps["2000099"].gender_hint == "women"


def test_brand_name():
    assert brand_name("ROSSIGNOL", ["Rossignol", "Look"]) == "Rossignol"
    assert brand_name("LOOK", ["Rossignol", "Look"]) == "Look"
    assert brand_name("KERMA", ["Dynastar"]) == "Kerma"
    assert brand_name(None, ["Giro"]) is None
