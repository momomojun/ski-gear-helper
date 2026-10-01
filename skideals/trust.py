"""网站可信度核验 —— 用可验证的客观证据给网站打分，并逐条解释原因。

检查项（每项都会显示 ✓/⚠/✗ 和说明）：
  1. 域名注册时间（RDAP / WHOIS）      —— 骗子网站几乎都是几个月内新注册的
  2. 网站历史（Wayback Machine 最早存档）—— 老牌商店十几年前就有存档
  3. HTTPS 证书是否有效、是否匹配域名
  4. 网站上有没有实体地址、电话、退货/运费/隐私政策
  5. Shopify 店铺的注册地（/meta.json）  —— 自称美国店却注册在别国要警惕
  6. 价格异常：几乎全场 5 折以下        —— 假“清仓/outlet”站的典型特征
  7. 域名里冒用品牌名 + outlet/sale 等词、可疑后缀（.shop/.top/.xyz…）
  8. 是否在本工具人工核验过的零售商名单里 / 是否为知名电商平台
最后给出 0~100 分和等级：可信 / 基本可信 / 谨慎 / 高风险。
"""
from __future__ import annotations

import json
import re
import socket
import ssl
import statistics
from datetime import datetime, timezone
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from . import db
from .config import CONFIG_DIR, load_retailers
from .net import BlockedError, Fetcher, FetchError, decode_html
from .normalize import BRANDS, fold

# 常见的两级公共后缀（够本工具用的小型 PSL）
_TWO_LEVEL_SUFFIX = {"co.jp", "ne.jp", "or.jp", "ac.jp", "com.cn", "net.cn", "org.cn", "co.uk", "org.uk",
                     "com.au", "net.au", "co.nz", "com.hk", "com.tw", "co.kr", "com.sg", "com.br", "com.mx"}
_RISKY_TLDS = {"shop", "store", "top", "xyz", "online", "site", "club", "vip", "buzz", "icu", "cfd", "sbs",
               "click", "live", "cyou", "rest", "bond", "fun", "space", "website", "pw", "tk", "ml", "ga", "cf"}
_DEAL_WORDS = re.compile(r"outlet|sale|cheap|discount|clearance|deal|official|factory|store|shop|mall|warehouse|"
                         r"online|usa|us|jp|cn|2024|2025|2026|2027|off")
KNOWN_PLATFORMS = {
    "amazon.com": "Amazon", "amazon.co.jp": "Amazon Japan", "ebay.com": "eBay", "walmart.com": "Walmart",
    "rakuten.co.jp": "楽天市場", "kakaku.com": "価格.com", "yahoo.co.jp": "Yahoo! JAPAN",
    "jd.com": "京东", "tmall.com": "天猫", "taobao.com": "淘宝", "smzdm.com": "什么值得买", "goofish.com": "闲鱼",
}
# 品牌官方域名（域名里出现品牌名时，只有这些是官方的）
OFFICIAL_BRAND_DOMAINS = {
    "atomic.com", "salomon.com", "rossignol.com", "head.com", "volkl.com", "fischersports.com", "k2snow.com",
    "nordica.com", "blizzardsports.com", "blizzard-tecnica.com", "elansports.com", "elanskis.com",
    "dynastar-lange.com", "dynastar.com", "armadasnow.com", "armadaskis.com", "lineskis.com", "factionskis.com",
    "black-crows.com", "dpsskis.com", "momentskis.com", "4frnt.com", "jskis.com", "icelanticskis.com",
    "kaestle.com", "kastle.com", "stockli.com", "libertyskis.com", "blackdiamondequipment.com", "dynafit.com",
    "scott-sports.com", "burton.com",
}
LEVELS = [(75, "trusted", "可信"), (55, "ok", "基本可信"), (35, "caution", "谨慎"), (0, "danger", "高风险")]


# ------------------------------------------------------------------ 工具

def split_domain(url_or_domain: str) -> tuple[str, str, str]:
    """返回 (可注册域名, 主机名, 规范化 URL)。"""
    s = url_or_domain.strip()
    if not re.match(r"https?://", s, re.I):
        s = "https://" + s
    host = (urlsplit(s).hostname or "").lower().rstrip(".")
    labels = host.split(".")
    n = 3 if ".".join(labels[-2:]) in _TWO_LEVEL_SUFFIX else 2
    domain = ".".join(labels[-n:]) if len(labels) >= n else host
    return domain, host, f"https://{host}/"


def _sig(sid, label, status, points, detail):
    return {"id": sid, "label": label, "status": status, "points": points, "detail": detail}


def _years_since(dt: datetime) -> float:
    return (datetime.now(timezone.utc) - dt).days / 365.25


def _parse_date(s: str) -> datetime | None:
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%d-%b-%Y", "%Y%m%d%H%M%S"):
        try:
            dt = datetime.strptime(s[:len(datetime.now().strftime(fmt)) + 6].strip(), fmt) if "%z" in fmt \
                else datetime.strptime(s[:26].strip(), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    return None


# ------------------------------------------------------------------ 各项证据

def domain_created(fetcher: Fetcher, domain: str) -> tuple[datetime | None, str | None, str]:
    """域名注册日期。先查 RDAP（结构化），不支持的后缀（如 .jp）再用 WHOIS。返回 (日期, 注册商, 来源)。"""
    try:
        d = fetcher.get_json(f"https://rdap.org/domain/{domain}", check_robots=False)
        created = next((e["eventDate"] for e in d.get("events", []) if e.get("eventAction") == "registration"), None)
        registrar = None
        for ent in d.get("entities", []):
            if "registrar" in ent.get("roles", []):
                vcard = ent.get("vcardArray", [None, []])[1]
                registrar = next((v[3] for v in vcard if v and v[0] == "fn"), None)
        if created:
            return _parse_date(created), registrar, "RDAP"
    except Exception:
        pass
    try:
        text = _whois(domain)
        m = re.search(r"(?:Creation Date|Created On|Registration Time|Registered on|\[登録年月日\]|"
                      r"\[Created on\]|created)\s*[:：]?\s*([^\r\n]+)", text, re.I)
        reg = re.search(r"Registrar:\s*([^\r\n]+)", text)
        if m:
            return _parse_date(m.group(1)), (reg.group(1).strip() if reg else None), "WHOIS"
    except Exception:
        pass
    return None, None, ""


def _whois(domain: str, server: str | None = None, depth: int = 0) -> str:
    server = server or "whois.iana.org"
    with socket.create_connection((server, 43), timeout=10) as s:
        q = domain + ("/e" if server == "whois.jprs.jp" else "")
        s.sendall((q + "\r\n").encode())
        chunks = []
        while True:
            b = s.recv(4096)
            if not b:
                break
            chunks.append(b)
    raw = b"".join(chunks)
    text = raw.decode("utf-8", errors="replace")
    m = re.search(r"(?:refer|whois):\s*(\S+)", text, re.I)
    if depth < 2 and m and server == "whois.iana.org":
        return _whois(domain, m.group(1).strip(), depth + 1)
    return text


def wayback_years(fetcher: Fetcher, domain: str) -> list[int]:
    """互联网档案馆里有存档的年份（每年取一条）。用来看网站历史是否连续。"""
    try:
        rows = fetcher.get_json("https://web.archive.org/cdx/search/cdx",
                                params={"url": domain, "output": "json", "fl": "timestamp",
                                        "collapse": "timestamp:4", "limit": 60},
                                check_robots=False)
        return sorted({int(r[0][:4]) for r in rows[1:] if r and r[0][:4].isdigit()})
    except Exception:
        return []


def history_gap(years: list[int]) -> tuple[int, int] | None:
    """最近几年很活跃、之前却空白好几年 → 可能是骗子买下的过期老域名（继承“年龄”）。返回 (空白开始, 空白结束)。"""
    if len(years) < 2:
        return None
    now = datetime.now().year
    worst = None
    for a, b in zip(years, years[1:]):
        if b - a >= 4 and b >= now - 3 and (worst is None or b - a > worst[1] - worst[0]):
            worst = (a, b)
    return worst


# 人工尽职调查（config/due_diligence.json，按域名索引）
def _load_dd() -> dict:
    try:
        data = json.load(open(CONFIG_DIR / "due_diligence.json", encoding="utf-8"))
        return {e.get("domain", "").replace("www.", ""): e for e in data.get("retailers", {}).values()}
    except (OSError, ValueError):
        return {}


_DD = _load_dd()
_DD_MTIME = [0.0]


def due_diligence(domain: str) -> dict | None:
    """按域名取人工调查记录；文件改过就重新读（补充调查结果后不用重启网页服务）。"""
    global _DD
    try:
        mtime = (CONFIG_DIR / "due_diligence.json").stat().st_mtime
    except OSError:
        mtime = 0.0
    if mtime != _DD_MTIME[0]:
        _DD, _DD_MTIME[0] = _load_dd(), mtime
    return _DD.get(domain)


def tls_info(host: str) -> dict:
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=10) as sock, ctx.wrap_socket(sock, server_hostname=host) as s:
            cert = s.getpeercert()
        issuer = dict(x[0] for x in cert.get("issuer", []))
        subject = dict(x[0] for x in cert.get("subject", []))
        return {"ok": True, "issuer": issuer.get("organizationName") or issuer.get("commonName"),
                "org": subject.get("organizationName"), "not_before": cert.get("notBefore"),
                "not_after": cert.get("notAfter")}
    except ssl.SSLCertVerificationError as e:
        return {"ok": False, "error": f"证书校验失败：{e.verify_message}"}
    except Exception as e:
        return {"ok": None, "error": str(e)[:120]}


_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\b[2-9]\d{2}\)?[\s.-]\d{3}[\s.-]\d{4}\b|\b1-8\d{2}-\d{3}-\d{4}\b|"
                    r"\b0\d{1,4}-\d{1,4}-\d{3,4}\b|\b400-?\d{3}-?\d{4}\b")
_ADDRESS = re.compile(r"\b\d{1,6}\s+(?:[A-Z0-9][\w.'-]*\s+){0,5}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|"
                      r"Drive|Dr|Way|Lane|Ln|Highway|Hwy|Parkway|Pkwy|Place|Pl|Court|Ct|Circle|Suite|Route|Rte)\b"
                      r"[\s.,#\w-]{0,60}?\b(?:A[LKZR]|C[AOT]|D[EC]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|"
                      r"O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])\b,?\s+\d{5}")
_JP_ADDRESS = re.compile(r"〒?\d{3}-\d{4}\s*\S{2,4}[都道府県]")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_POLICY = {"returns": re.compile(r"return|refund|exchange|返品|退货"),
           "shipping": re.compile(r"shipping|delivery|送料|配送|运费"),
           "privacy": re.compile(r"privacy|個人情報|隐私"),
           "terms": re.compile(r"terms|conditions|特定商取引|利用規約|条款")}


def page_evidence(fetcher: Fetcher, url: str) -> dict:
    """抓首页（必要时再抓联系我们/关于我们页），找联系方式、政策链接、平台特征。"""
    ev = {"reachable": False, "title": None, "final_url": None, "platform": None, "phones": [], "emails": [],
          "address": None, "policies": {}, "blocked": False}
    try:
        resp = fetcher.get(url)
    except BlockedError:
        ev["blocked"] = True
        return ev
    except FetchError as e:
        ev["error"] = str(e)[:120]
        return ev
    html = decode_html(resp)
    ev.update(reachable=True, final_url=str(resp.url))
    doc = HTMLParser(html)
    t = doc.css_first("title")
    ev["title"] = t.text(strip=True)[:120] if t else None
    low = html[:300000]
    if "cdn.shopify.com" in low or "Shopify.shop" in low:
        ev["platform"] = "Shopify"
    elif "demandware" in low:
        ev["platform"] = "Salesforce Commerce Cloud"
    elif "bigcommerce" in low.lower():
        ev["platform"] = "BigCommerce"
    elif "Magento" in low or "mage/cookies" in low:
        ev["platform"] = "Magento"

    candidates = []
    for a in doc.css("a[href]"):
        text = (a.text(strip=True) or "")[:60]
        href = a.attributes.get("href") or ""
        blob = f"{text} {href}".lower()
        for k, rx in _POLICY.items():
            if k not in ev["policies"] and rx.search(blob):
                ev["policies"][k] = urljoin(ev["final_url"], href)
        if re.search(r"contact|about|store-?locat|locations|会社概要|店舗|联系我们|关于", blob):
            candidates.append(urljoin(ev["final_url"], href))

    def harvest(text: str):
        body = re.sub(r"\s+", " ", text)
        ev["phones"] = sorted(set(ev["phones"]) | set(m.strip() for m in _PHONE.findall(body)))[:5]
        ev["emails"] = sorted(set(ev["emails"]) | {e for e in _EMAIL.findall(body)
                                                  if not e.lower().endswith((".png", ".jpg", ".webp", ".gif"))})[:5]
        if not ev["address"]:
            m = _ADDRESS.search(body) or _JP_ADDRESS.search(body)
            if m:
                ev["address"] = m.group(0)[:120]

    harvest(doc.body.text(separator=" ") if doc.body else html)
    host = urlsplit(ev["final_url"]).netloc
    tried = 0
    for c in candidates:
        if ev["address"] and ev["phones"]:
            break
        if urlsplit(c).netloc != host or tried >= 2:
            continue
        tried += 1
        try:
            sub = HTMLParser(fetcher.get_html(c))
            harvest(sub.body.text(separator=" ") if sub.body else "")
        except Exception:
            continue
    return ev


def compare_with_market(prods: list[dict]) -> dict:
    """把可疑网站的商品和本工具数据库里的正规零售商价格对比：同款却便宜一半以上 = 危险信号。"""
    from . import catalog
    from .categories import classify
    from .models import RawProduct
    from .normalize import normalize
    fams_by_cat: dict[str, dict] = {}
    matched, cheap, examples = 0, 0, []
    for p in prods:
        v = (p.get("variants") or [{}])[0]
        try:
            price = float(v.get("price") or 0)
        except ValueError:
            continue
        if price <= 0:
            continue
        cat = classify(p.get("title", ""), p.get("product_type")) or "ski"
        if cat not in fams_by_cat:
            fams_by_cat[cat] = {f["k"]: f for f in catalog.build(cat)["families"]}
        lst = normalize(RawProduct(retailer_id="check", external_id=str(p.get("id")), url="", title=p.get("title", ""),
                                   vendor=p.get("vendor"), product_type=p.get("product_type"), category=cat))
        fam = fams_by_cat[cat].get(lst.model_key)
        if not fam or not lst.brand:
            continue
        legit = [o["p"] for o in fam["o"] if o["cd"] == "new" and (not lst.year or o["y"] in (lst.year, None))]
        if not legit:
            continue
        matched += 1
        low = min(legit)
        if price < 0.5 * low:
            cheap += 1
            if len(examples) < 3:
                examples.append(f"{fam['b']} {fam['m']}：${price:.0f}（正规店最低 ${low:.0f}）")
    return {"matched": matched, "too_cheap": cheap, "examples": examples}


def shopify_evidence(fetcher: Fetcher, base: str, compare: bool = False) -> dict | None:
    try:
        meta = fetcher.get_json(urljoin(base, "/meta.json"))
    except Exception:
        return None
    out = {k: meta.get(k) for k in ("name", "city", "province", "country", "currency", "published_products_count")}
    out["ships_to"] = len(meta.get("ships_to_countries") or [])
    try:
        prods = fetcher.get_json(urljoin(base, "/products.json"), params={"limit": 250}).get("products", [])
        if compare:
            out["market"] = compare_with_market(prods)
        discounts = []
        for p in prods:
            for v in p.get("variants", [])[:1]:
                try:
                    price, cmp_ = float(v.get("price") or 0), float(v.get("compare_at_price") or 0)
                except ValueError:
                    continue
                if price > 0 and cmp_ > price:
                    discounts.append(1 - price / cmp_)
        out["n_products_sampled"] = len(prods)
        vendors = [str(p.get("vendor") or "").strip() for p in prods if p.get("vendor")]
        if vendors:
            top = max(set(vendors), key=vendors.count)
            out["top_vendor"], out["top_vendor_share"] = top, round(vendors.count(top) / len(prods), 2)
        out["n_discounted"] = len(discounts)
        out["median_discount"] = round(statistics.median(discounts) * 100) if discounts else 0
        out["share_discounted"] = round(len(discounts) / len(prods) * 100) if prods else 0
    except Exception:
        pass
    return out


_BRAND_DOMAIN_SUFFIX = {"", "gear", "outerwear", "apparel", "usa", "us", "america", "official", "wear", "clothing", "sports",
                        "co", "inc", "store", "shop", "ski", "skis", "outdoor", "outdoors"}


def looks_like_brand_store(domain: str, shop: dict | None, age_years: float | None) -> str | None:
    """陌生网站是不是某品牌自己的官网：抽查的商品 ≥80% 是同一品牌、域名就是品牌名（或品牌名 + gear/outerwear 之类的后缀）、
    域名注册 ≥5 年（骗子网站几乎都是新注册的）。是的话返回品牌名。
    官网清仓价比零售店便宜一半很常见（Spyder 官网），不能当成骗局信号。"""
    if not shop or not shop.get("top_vendor") or (shop.get("top_vendor_share") or 0) < 0.8 or (age_years or 0) < 5:
        return None
    name = re.sub(r"[^a-z0-9]", "", domain.split(".")[0].lower())
    v = re.sub(r"[^a-z0-9]", "", fold(shop["top_vendor"]))
    if not v or len(v) < 3:
        return None
    rest_v = v[len(name):] if v.startswith(name) else None      # vendor 比域名长：“Spyder Active Sports” vs spyder.com
    if (name.startswith(v) and name[len(v):] in _BRAND_DOMAIN_SUFFIX) or             (len(name) >= 4 and rest_v is not None and
             re.fullmatch(r"(?:active)?(?:sports?|apparel|inc|usa|outerwear|clothing|gear|skis?|co|company|designs?)?", rest_v)):
        return shop["top_vendor"]
    return None


def brand_impersonation(domain: str) -> str | None:
    if domain in OFFICIAL_BRAND_DOMAINS:
        return None
    name = domain.split(".")[0]
    flat = re.sub(r"[^a-z0-9]", "", name)
    for brand, aliases, ambiguous in BRANDS:
        for a in aliases:
            a2 = re.sub(r"[^a-z0-9]", "", fold(a))
            if len(a2) < 4 or not a2.isascii() or ambiguous:
                continue
            if a2 in flat and flat != a2 and _DEAL_WORDS.search(flat.replace(a2, " ")):
                return brand
    return None


def retailer_impersonation(domain: str) -> str | None:
    """冒充已知零售商：如 utahskigearonline.shop 冒充 utahskigear.com（Utah Ski Gear 官网自己发过警告）。"""
    flat = re.sub(r"[^a-z0-9]", "", domain.split(".")[0])
    for r in load_retailers():
        real = split_domain(r["url"])[0]
        label = re.sub(r"[^a-z0-9]", "", real.split(".")[0])
        if len(label) >= 6 and label in flat and domain != real:
            return f"{r['name']}（{real}）"
    return None


# ------------------------------------------------------------------ 汇总打分

def _registry_entry(domain: str) -> dict | None:
    for r in load_retailers():
        if split_domain(r["url"])[0] == domain:
            return r
    return None


def check_site(url: str, save: bool = True) -> dict:
    domain, host, base = split_domain(url)
    fetcher = Fetcher(min_interval=0.6, retries=1, timeout=20)
    signals: list[dict] = []
    facts: dict = {}

    reg = _registry_entry(domain)
    platform_name = KNOWN_PLATFORMS.get(domain)
    if reg:
        pts = {"A": 15, "B": 10, "brand": 15}.get(reg.get("tier"), 8)
        tier_txt = {"A": "大型/老牌零售商", "B": "专业雪具店", "brand": "品牌官网"}.get(reg.get("tier"), "")
        signals.append(_sig("registry", "人工核验名单", "pass", pts, f"已收录：{reg['name']}（{tier_txt}）"))
    elif platform_name:
        signals.append(_sig("registry", "知名平台", "pass", 15,
                            f"{platform_name} 是知名电商平台；注意平台上的第三方卖家需要单独看评价"))

    created, registrar, src = domain_created(fetcher, domain)
    if created:
        age = _years_since(created)
        facts.update(domain_created=created.date().isoformat(), domain_age_years=round(age, 1), registrar=registrar)
        if age >= 10:
            signals.append(_sig("age", "域名年龄", "pass", 20, f"注册于 {created.year} 年（{age:.0f} 年），老牌网站"))
        elif age >= 5:
            signals.append(_sig("age", "域名年龄", "pass", 14, f"注册于 {created.year} 年（{age:.0f} 年）"))
        elif age >= 2:
            signals.append(_sig("age", "域名年龄", "pass", 6, f"注册于 {created.date()}（{age:.1f} 年）"))
        elif age >= 1:
            signals.append(_sig("age", "域名年龄", "warn", -5, f"注册于 {created.date()}，只有 {age:.1f} 年"))
        elif age >= 0.25:
            signals.append(_sig("age", "域名年龄", "fail", -25, f"注册于 {created.date()}，不到 1 年 —— 新网站要特别小心"))
        else:
            signals.append(_sig("age", "域名年龄", "fail", -35, f"注册于 {created.date()}，不到 3 个月 —— 骗子网站典型特征"))
    else:
        signals.append(_sig("age", "域名年龄", "info", 0, "查不到注册日期（部分国家域名不公开）"))

    years = wayback_years(fetcher, domain)
    if years:
        first_year = years[0]
        age_h = datetime.now().year - first_year
        facts["wayback_first"] = str(first_year)
        facts["wayback_years"] = years
        if age_h >= 10:
            signals.append(_sig("history", "网站历史", "pass", 8, f"互联网档案馆最早存档：{first_year} 年"))
        elif age_h >= 3:
            signals.append(_sig("history", "网站历史", "pass", 4, f"最早存档：{first_year} 年"))
        elif age_h >= 1:
            signals.append(_sig("history", "网站历史", "info", 0, f"最早存档：{first_year} 年"))
        else:
            signals.append(_sig("history", "网站历史", "warn", -8, f"最早存档：{first_year} 年，历史很短"))
        gap = history_gap(years)
        if gap:
            signals.append(_sig("gap", "历史中断", "warn", -12,
                                f"{gap[0]}–{gap[1]} 年之间没有任何存档，最近才重新活跃 —— 可能是被转手的旧域名"
                                "（骗子会买过期老域名来冒充“老店”），请核对网站内容是否一直是同一家店"))
    else:
        signals.append(_sig("history", "网站历史", "warn", -6, "互联网档案馆没有这个网站的存档（或查询失败）"))

    dd = due_diligence(domain)
    if dd:
        facts["due_diligence"] = dd
        if dd.get("verdict") == "caution":
            signals.append(_sig("dd", "人工调查", "warn", -15, f"需谨慎：{dd.get('verdict_reason', '')}"))
        else:
            parts = [x for x in (dd.get("hq"), f"成立于 {dd['founded']}" if dd.get("founded") and
                                 dd["founded"] != "unknown" else None) if x and x != "unknown"]
            signals.append(_sig("dd", "人工调查", "pass", 0, "；".join(parts)[:140] or dd.get("verdict_reason", "")))

    tls = tls_info(host)
    facts["tls"] = tls
    if tls.get("ok"):
        org = f"，证书单位：{tls['org']}" if tls.get("org") else ""
        signals.append(_sig("tls", "HTTPS 证书", "pass", 3, f"证书有效（签发：{tls.get('issuer')}{org}）"))
    elif tls.get("ok") is False:
        signals.append(_sig("tls", "HTTPS 证书", "fail", -30, tls.get("error", "证书无效")))
    else:
        signals.append(_sig("tls", "HTTPS 证书", "warn", -5, f"无法建立 HTTPS 连接：{tls.get('error')}"))

    ev = page_evidence(fetcher, base)
    facts["page"] = ev
    if ev.get("blocked"):
        signals.append(_sig("contact", "联系方式", "info", 0, "网站有反爬保护，无法自动读取页面（不代表不可信）"))
    elif not ev.get("reachable"):
        signals.append(_sig("contact", "网站可访问", "fail", -20, f"首页打不开：{ev.get('error', '')}"))
    else:
        got = [x for x, ok in (("地址", ev["address"]), ("电话", ev["phones"])) if ok]
        if len(got) == 2:
            signals.append(_sig("contact", "联系方式", "pass", 9,
                                f"有实体地址和电话（{ev['address'] or ''} / {ev['phones'][0]}）"))
        elif got:
            signals.append(_sig("contact", "联系方式", "pass", 4, f"找到{got[0]}：{ev['address'] or ev['phones'][0]}"))
        elif ev["emails"]:
            signals.append(_sig("contact", "联系方式", "warn", -6, f"只找到邮箱（{ev['emails'][0]}），没找到地址和电话"))
        else:
            signals.append(_sig("contact", "联系方式", "warn", -8, "首页和联系页都没找到地址、电话"))
        pol = ev["policies"]
        names = {"returns": "退货", "shipping": "运费/配送", "privacy": "隐私", "terms": "条款"}
        have = [names[k] for k in names if k in pol]
        if "returns" in pol and "shipping" in pol:
            signals.append(_sig("policy", "购物政策", "pass", 4, "有" + "、".join(have) + "政策页面"))
        else:
            missing = [names[k] for k in ("returns", "shipping") if k not in pol]
            signals.append(_sig("policy", "购物政策", "warn", -6, "没找到" + "、".join(missing) + "政策链接"))
        if ev.get("final_url") and split_domain(ev["final_url"])[0] != domain:
            signals.append(_sig("redirect", "跳转", "warn", -5, f"首页跳转到了另一个域名：{ev['final_url']}"))

    shop = shopify_evidence(fetcher, ev.get("final_url") or base, compare=not reg) \
        if (ev.get("platform") == "Shopify" or ev.get("blocked")) else None
    mk = (shop or {}).get("market") or {}
    too_cheap = mk.get("too_cheap", 0) >= 2 or (mk.get("matched", 0) >= 4 and mk.get("too_cheap", 0) / mk["matched"] >= 0.3)
    own_brand = looks_like_brand_store(domain, shop, facts.get("domain_age_years")) if too_cheap else None
    if too_cheap and own_brand:
        signals.append(_sig("market", "价格低于零售店", "info", 0,
                            f"{mk['too_cheap']} 款比零售店最低价便宜一半以上，但这看起来是 {own_brand} 自己的官网"
                            f"（域名注册 {facts.get('domain_age_years')} 年、商品几乎都是 {own_brand}）：官网清仓价低于零售店很常见，"
                            "不算骗局信号；下单前仍请确认域名就是品牌官网"))
    elif too_cheap:
        signals.append(_sig("market", "价格远低于市场", "fail", -30,
                            f"{mk['too_cheap']} 款商品比正规零售商最低价还便宜一半以上（例：{'；'.join(mk['examples'])}）"
                            " —— 正规店不可能这样卖，典型骗局"))
    elif mk.get("matched"):
        signals.append(_sig("market", "与市场价对比", "pass", 3,
                            f"{mk['matched']} 款能和正规零售商的同款对上，价格在正常范围内"))
    if shop:
        facts["shopify"] = shop
        where = ", ".join(x for x in (shop.get("city"), shop.get("province"), shop.get("country")) if x)
        signals.append(_sig("shopify", "店铺注册地", "info", 0, f"Shopify 店铺登记地：{where}（币种 {shop.get('currency')}）"))
        md, share, n = shop.get("median_discount", 0), shop.get("share_discounted", 0), shop.get("n_discounted", 0)
        if n >= 15 and share >= 60 and md >= 50:
            signals.append(_sig("prices", "价格异常", "fail", -30,
                                f"抽查 {shop.get('n_products_sampled')} 件商品，{share}% 在打折，折扣中位数 {md}% —— "
                                "几乎全场半价以下，是假冒折扣站的典型特征"))
        elif n >= 15 and md >= 40:
            signals.append(_sig("prices", "价格", "warn", -8, f"折扣商品的折扣中位数 {md}%，偏高，留意是否过季清仓"))
        elif shop.get("n_products_sampled"):
            signals.append(_sig("prices", "价格", "pass", 3, f"抽查 {shop.get('n_products_sampled')} 件，折扣正常"
                                                             f"（打折占比 {share}%，中位数 {md}%）"))

    tld = domain.rsplit(".", 1)[-1]
    if tld in _RISKY_TLDS:
        signals.append(_sig("tld", "域名后缀", "warn", -10, f".{tld} 后缀注册便宜，被大量骗子网站使用"))
    imp = brand_impersonation(domain)
    if imp:
        signals.append(_sig("brand", "冒用品牌", "fail", -40,
                            f"域名包含品牌名“{imp}”但不是 {imp} 官网 —— 典型的假冒“官方折扣店”"))
    fake_shop = retailer_impersonation(domain)
    if fake_shop and not reg:
        signals.append(_sig("brand", "冒充零售商", "fail", -40,
                            f"域名和已知零售商 {fake_shop} 很像但不是它 —— 典型的仿冒网站"))

    score = max(0, min(100, 50 + sum(s["points"] for s in signals)))
    fatal = any(s["status"] == "fail" and s["id"] in ("brand", "tls") for s in signals) or \
        any(s["id"] == "age" and s["points"] <= -35 for s in signals)
    if fatal:
        score = min(score, 30)
    level, label = next((lv, lb) for th, lv, lb in LEVELS if score >= th)
    # 人工调查结论是“需谨慎”（授权运营商破产、清库存、投诉多…）：网站是真的，但总评级最高“基本可信”
    if dd and dd.get("verdict") == "caution" and level == "trusted":
        level, label = "ok", "基本可信（人工调查：需谨慎）"
    report = {
        "domain": domain, "host": host, "url": base, "checked_at": db.now_iso(), "score": score,
        "level": level, "level_label": label, "signals": signals, "facts": facts,
        "registry": {"id": reg["id"], "name": reg["name"], "tier": reg.get("tier")} if reg else None,
        "links": [
            {"name": "Trustpilot 评价", "url": f"https://www.trustpilot.com/review/{domain}"},
            {"name": "BBB 商业信用", "url": f"https://www.bbb.org/search?find_text={domain}"},
            {"name": "ScamAdviser", "url": f"https://www.scamadviser.com/check-website/{domain}"},
            {"name": "Google 安全浏览", "url": f"https://transparencyreport.google.com/safe-browsing/search?url={domain}"},
            {"name": "Reddit 讨论", "url": f"https://www.reddit.com/search/?q=%22{domain}%22"},
        ],
    }
    if save:
        with db.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO site_checks(domain, checked_at, score, level, report_json) "
                         "VALUES(?,?,?,?,?)", (domain, report["checked_at"], score, level,
                                               json.dumps(report, ensure_ascii=False)))
    return report


def cached(domain: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute("SELECT report_json FROM site_checks WHERE domain=?", (domain,)).fetchone()
    return json.loads(row["report_json"]) if row else None


def check_all_retailers(verbose: bool = False) -> list[dict]:
    out = []
    for r in load_retailers():
        if not r.get("enabled", True):
            continue
        try:
            rep = check_site(r["url"])
            out.append(rep)
            if verbose:
                print(f"{r['id']:18} {rep['score']:3}  {rep['level_label']}", flush=True)
        except Exception as e:
            if verbose:
                print(f"{r['id']:18} 失败：{e}", flush=True)
    return out


def format_report(rep: dict) -> str:
    icon = {"pass": "✓", "warn": "⚠", "fail": "✗", "info": "·"}
    lines = [f"{rep['domain']}  →  {rep['score']} 分  【{rep['level_label']}】", ""]
    for s in rep["signals"]:
        pts = f"{s['points']:+d}" if s["points"] else "  "
        lines.append(f"  {icon[s['status']]} {pts:>4}  {s['label']}：{s['detail']}")
    lines += ["", "人工复核：" + "  ".join(f"{l['name']} {l['url']}" for l in rep["links"][:3])]
    return "\n".join(lines)
