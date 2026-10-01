"""把各网站五花八门的商品标题，标准化成可以跨网站比较的结构。

例子：
  "Blizzard Black Pearl 88 Skis - Women's 2026"       (evo)
  "Blizzard Black Pearl 88 Women's Skis 2026"         (另一家)
都会得到：品牌=Blizzard  型号=Black Pearl 88  年份=2026  性别=女款
以及同一个分组键 model_key = "blizzard|88 black pearl|w|f"，这样就能把两家的价格放在一起比较。

分组键 = 品牌 | 型号 token（排序后） | 性别大类(a 成人 / w 女款 / k 儿童) | 固定器(f 不含 / b 含)
年份不进分组键：同一型号的不同年份放在同一张卡片里，每个报价单独标年份。
"""
from __future__ import annotations

import re
import unicodedata

from .models import Listing, RawProduct

# ---------------------------------------------------------------- 基础工具

_QUOTES = str.maketrans({"’": "'", "‘": "'", "´": "'", "`": "'", "–": "-", "—": "-", "‐": "-", "‑": "-"})


def fold(s: str | None) -> str:
    """全角→半角、去掉拉丁字母的变音符（ö→o）、统一引号、小写。

    注意：只对拉丁字母去变音符。日文假名的浊点也是“组合字符”，
    一起去掉会把 レディース 变成 レテイース、ブーツ 变成 フーツ，导致日文关键词全部失效。
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).translate(_QUOTES)
    s = s.replace("ø", "o").replace("Ø", "O").replace("æ", "ae").replace("ß", "ss")  # Norrøna / Fjällräven
    out = []
    for ch in s:
        if "À" <= ch <= "ɏ":
            out.append("".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)))
        else:
            out.append(ch)
    return "".join(out).lower()


def slug(s: str) -> str:
    """分组键里的品牌写法：只留字母数字（Arc'teryx / ARCTERYX / Arc teryx 都变成 arcteryx）。"""
    return re.sub(r"[^a-z0-9]+", "", fold(s))


# ---------------------------------------------------------------- 品牌

# (标准名, [别名...])；别名均为 fold 之后的形式。ambiguous=True 表示别名是普通英文单词，
# 只有出现在 vendor 字段或标题开头时才认定为品牌。
BRANDS: list[tuple[str, list[str], bool]] = [
    ("Atomic", ["atomic", "アトミック", "阿托米克"], False),
    ("Salomon", ["salomon", "サロモン", "萨洛蒙"], False),
    ("Rossignol", ["rossignol", "ロシニョール", "金鸡"], False),
    ("Head", ["head", "ヘッド", "海德"], True),
    ("Völkl", ["volkl", "voelkl", "フォルクル"], False),
    ("Fischer", ["fischer", "フィッシャー"], False),
    ("K2", ["k2", "k2 skis", "k2 sports", "ケーツー"], False),
    ("Nordica", ["nordica", "ノルディカ", "诺迪卡"], False),
    ("Blizzard", ["blizzard", "ブリザード"], False),
    ("Elan", ["elan", "エラン"], False),
    ("Dynastar", ["dynastar", "ディナスター"], False),
    ("Armada", ["armada", "アルマダ"], False),
    ("Line", ["line", "line skis", "ライン"], True),
    ("Faction", ["faction", "faction skis", "ファクション"], False),
    ("Black Crows", ["black crows", "blackcrows", "ブラッククロウズ"], False),
    ("DPS", ["dps", "dps skis"], False),
    ("Moment", ["moment", "moment skis"], True),
    ("4FRNT", ["4frnt"], False),
    ("J Skis", ["j skis", "jskis", "j ski"], False),
    ("Icelantic", ["icelantic"], False),
    ("Kästle", ["kastle", "kaestle", "ケスレー"], False),
    ("Stöckli", ["stockli", "stoeckli", "ストックリ"], False),
    ("Liberty", ["liberty", "liberty skis"], True),
    ("Black Diamond", ["black diamond"], True),
    ("Dynafit", ["dynafit", "ディナフィット"], False),
    ("Scott", ["scott"], True),
    ("Season", ["season", "season eqpt"], True),
    ("Ogasaka", ["ogasaka", "オガサカ"], False),
    ("ID one", ["id one", "idone"], False),
    ("Swallow", ["swallow", "スワロー"], True),
    ("Majesty", ["majesty"], True),
    ("Augment", ["augment"], True),
    ("Renoun", ["renoun"], False),
    ("ON3P", ["on3p"], False),
    ("Folsom", ["folsom"], True),
    ("Parlor", ["parlor"], True),
    ("Wagner", ["wagner", "wagner custom"], True),
    ("Shaggy's", ["shaggy's", "shaggys"], False),
    ("Romp", ["romp"], True),
    ("Coalition Snow", ["coalition snow"], False),
    ("Zag", ["zag"], True),
    ("Movement", ["movement"], True),
    ("Lib Tech", ["lib tech"], False),
    ("Prior", ["prior"], True),
    ("Sego", ["sego"], True),
    ("Kye", ["kye"], True),
    ("Hart", ["hart"], True),
    ("Vector Glide", ["vector glide", "ベクターグライド"], False),
    ("Blossom", ["blossom"], True),
    ("Nidecker", ["nidecker"], False),
    ("Volant", ["volant"], True),
    # ---- 雪鞋 / 固定器 / 雪杖
    ("Tecnica", ["tecnica", "テクニカ"], False), ("Lange", ["lange", "ラング"], False),
    ("Dalbello", ["dalbello", "ダルベロ"], False), ("Full Tilt", ["full tilt"], False), ("Roxa", ["roxa"], False),
    ("Scarpa", ["scarpa"], False), ("La Sportiva", ["la sportiva"], False), ("Apex", ["apex ski boots"], False),
    ("Marker", ["marker", "マーカー"], True), ("Look", ["look", "ルック"], True), ("Tyrolia", ["tyrolia", "チロリア"], False),
    ("Fritschi", ["fritschi"], False), ("ATK", ["atk"], True), ("Plum", ["plum"], True), ("CAST", ["cast touring"], False),
    ("Leki", ["leki", "レキ"], False), ("Komperdell", ["komperdell"], False), ("Kerma", ["kerma"], False),
    ("Swix", ["swix"], False), ("Toko", ["toko"], True), ("Holmenkol", ["holmenkol"], False),
    ("Demon", ["demon"], True), ("One Ball", ["one ball", "oneball", "one ball jay"], False), ("Purl", ["purl"], True),
    ("mountainFLOW", ["mountainflow", "mountain flow"], False), ("Hotronic", ["hotronic"], False),
    ("Therm-ic", ["therm-ic", "thermic"], False), ("Sidas", ["sidas"], False), ("Superfeet", ["superfeet"], False),
    ("Zipfit", ["zipfit"], False), ("Intuition", ["intuition"], True),
    # ---- 头盔 / 雪镜
    ("Smith", ["smith", "smith optics"], True), ("Giro", ["giro"], False), ("POC", ["poc"], False),
    ("Oakley", ["oakley", "オークリー"], False), ("Anon", ["anon"], True), ("Dragon", ["dragon", "dragon alliance"], True),
    ("Uvex", ["uvex"], False), ("Julbo", ["julbo"], False), ("Sweet Protection", ["sweet protection"], False),
    ("Bollé", ["bolle"], False), ("Pret", ["pret", "pret helmets"], True), ("Zeal", ["zeal", "zeal optics"], True),
    ("Spy", ["spy", "spy optic"], True), ("Electric", ["electric"], True), ("Glade", ["glade", "glade optics"], True),
    ("OutdoorMaster", ["outdoormaster", "outdoor master"], False), ("Wildhorn", ["wildhorn"], False),
    ("Retrospec", ["retrospec"], False), ("Kask", ["kask"], False),
    # ---- 服装
    ("Arc'teryx", ["arc'teryx", "arcteryx", "arc teryx", "アークテリクス"], False), ("Patagonia", ["patagonia", "パタゴニア"], False),
    ("The North Face", ["the north face", "north face", "tnf", "ノースフェイス"], False),
    ("Helly Hansen", ["helly hansen", "helly-hansen", "ヘリーハンセン"], False), ("Spyder", ["spyder"], False),
    ("Obermeyer", ["obermeyer"], False), ("Columbia", ["columbia", "columbia sportswear"], False),
    ("Burton", ["burton"], False), ("686", ["686"], False), ("Volcom", ["volcom"], False), ("Flylow", ["flylow"], False),
    ("Trew", ["trew", "trew gear"], False), ("Strafe", ["strafe", "strafe outerwear"], False),
    ("Norrøna", ["norrona"], False), ("Mammut", ["mammut", "マムート"], False), ("Rab", ["rab"], True),
    ("Outdoor Research", ["outdoor research"], False), ("Marmot", ["marmot"], False),
    ("Mountain Hardwear", ["mountain hardwear"], False), ("Picture Organic", ["picture organic", "picture organic clothing", "picture"], True),
    ("Kjus", ["kjus"], False), ("Bogner", ["bogner"], False), ("Moncler", ["moncler"], False),
    ("Perfect Moment", ["perfect moment"], False), ("Fera", ["fera"], True), ("Kari Traa", ["kari traa"], False),
    ("Smartwool", ["smartwool"], False), ("Icebreaker", ["icebreaker"], False), ("Ortovox", ["ortovox"], False),
    ("Dakine", ["dakine"], False), ("Hestra", ["hestra"], False), ("Kombi", ["kombi"], False), ("Gordini", ["gordini"], False),
    ("Swany", ["swany"], False), ("Seirus", ["seirus"], False), ("Oyuki", ["oyuki"], False), ("Stio", ["stio"], False),
    ("L.L.Bean", ["l.l.bean", "ll bean", "l l bean", "l.l. bean"], False), ("REI Co-op", ["rei co-op", "rei"], True),
    ("Airblaster", ["airblaster"], False), ("Roxy", ["roxy"], False), ("Quiksilver", ["quiksilver"], False),
    ("Holden", ["holden"], True), ("Jones", ["jones", "jones snowboards"], True), ("Houdini", ["houdini"], True),
    ("Haglöfs", ["haglofs"], False), ("Fjällräven", ["fjallraven"], False), ("Peak Performance", ["peak performance"], False),
    ("Descente", ["descente", "デサント"], False), ("Phenix", ["phenix", "フェニックス"], False), ("Goldwin", ["goldwin", "ゴールドウイン"], False),
    ("Canada Goose", ["canada goose"], False), ("Eddie Bauer", ["eddie bauer"], False), ("Free Country", ["free country"], False),
    ("Arctix", ["arctix"], False), ("Gerry", ["gerry"], True), ("Hot Chillys", ["hot chillys", "hot chilly's"], False),
    ("Terramar", ["terramar"], False), ("Minus33", ["minus33", "minus 33"], False), ("Darn Tough", ["darn tough"], False),
    ("Stance", ["stance"], True), ("Point6", ["point6", "point 6"], False), ("Farm to Feet", ["farm to feet"], False),
    ("Feetures", ["feetures"], False), ("Wigwam", ["wigwam"], False), ("Lorpen", ["lorpen"], False), ("Falke", ["falke"], False),
    ("Turtle Fur", ["turtle fur"], False), ("Airhole", ["airhole"], False), ("Blackstrap", ["blackstrap"], False),
    ("Buff", ["buff"], True), ("Coal", ["coal", "coal headwear"], True), ("Spacecraft", ["spacecraft"], True),
    ("Pistil", ["pistil"], True), ("Karbon", ["karbon"], True), ("Nils", ["nils"], True), ("Newland", ["newland"], True),
    ("Goldbergh", ["goldbergh"], False), ("Sportalm", ["sportalm"], False), ("Toni Sailer", ["toni sailer"], False),
    ("Poivre Blanc", ["poivre blanc"], False), ("Dale of Norway", ["dale of norway"], False),
    # ---- 背包 / 雪崩 / 止滑带
    ("Osprey", ["osprey"], False), ("Deuter", ["deuter"], False), ("Mystery Ranch", ["mystery ranch"], False),
    ("BCA", ["bca", "backcountry access"], False), ("Pieps", ["pieps"], False), ("Arva", ["arva"], True),
    ("G3", ["g3", "genuine guide gear"], False), ("Pomoca", ["pomoca"], False), ("Kohla", ["kohla"], False),
    ("Contour", ["contour"], True), ("Montana", ["montana"], True), ("Thule", ["thule"], False),
    # ---- 2026-09 补充：各网站实际出现、之前没收录的品牌（含常见的不同写法）
    ("Le Bent", ["le bent", "lebent", "le bent socks"], False), ("Kühl", ["kuhl", "kuehl"], False),
    ("Auclair", ["auclair"], False), ("Moon Boot", ["moon boot", "moonboot"], False),
    ("ThirtyTwo", ["thirtytwo", "thirty two"], False), ("Bogner Fire+Ice", ["fire + ice", "fire+ice", "bogner fire + ice",
                                                                            "bogner fire+ice", "fire and ice"], False),
    ("DC", ["dc shoes", "dc"], True), ("Screamer", ["screamer", "screamer hats"], True),
    ("Db", ["db equipment", "db bags", "douchebags"], False), ("YNIQ", ["yniq", "yniq eyewear"], False),
    ("Gogglesoc", ["gogglesoc"], False), ("Boulder Gear", ["boulder gear"], False), ("Revo", ["revo"], True),
    ("Red Bull Spect", ["red bull spect", "red bull spect eyewear"], False), ("Bern", ["bern"], True),
    ("Pit Viper", ["pit viper"], False),
    ("Mons Royale", ["mons royale"], False), ("Bula", ["bula"], True), ("Chaos", ["chaos"], True),
    ("Autumn", ["autumn headwear", "autumn"], True), ("Wintersteiger", ["wintersteiger"], False),
    ("Dominator", ["dominator", "dominator wax"], True), ("Crab Grab", ["crab grab"], False), ("Wend", ["wend", "wend waxworks"], True),
    ("Spiral", ["spiral wax", "spiral wax co"], False), ("SideCut", ["sidecut", "side cut"], True),
    ("Voile", ["voile"], True), ("Rottefella", ["rottefella"], False), ("22 Designs", ["22 designs", "twenty two designs"], False),
    ("Cotopaxi", ["cotopaxi"], False), ("Kamik", ["kamik"], False), ("Under Armour", ["under armour"], False),
    ("O'Neill", ["o'neill", "oneill"], False), ("Billabong", ["billabong"], False), ("Killtec", ["killtec"], False),
    ("Reima", ["reima"], True), ("Krimson Klover", ["krimson klover"], False), ("Skida", ["skida"], False),
    ("Halfdays", ["halfdays"], False), ("Wild Rye", ["wild rye"], False), ("Artilect", ["artilect"], False),
    ("Kulkea", ["kulkea"], False), ("Athalon", ["athalon"], False), ("Sportube", ["sportube"], False),
    ("Transpack", ["transpack"], False), ("Frauenschuh", ["frauenschuh"], False), ("Sease", ["sease"], False),
    ("SVST", ["svst"], False), ("Thermotech", ["thermotech"], False), ("ActionHeat", ["actionheat", "action heat"], False),
    ("Lenz", ["lenz"], False), ("DryGuy", ["dryguy", "dry guy"], False), ("Bridgedale", ["bridgedale"], False),
    ("Wooly Bully", ["wooly bully"], False), ("Polarmax", ["polarmax"], False), ("Howl", ["howl", "howl supply"], True),
    ("Harricana", ["harricana"], False), ("Diavolezza", ["diavolezza"], False), ("The Mountain Studio", ["the mountain studio"], False),
    ("Seniq", ["seniq"], False), ("Icelandic Design", ["icelandic design"], False), ("rh+", ["rh+", "rh plus"], False),
    ("Mountain Tek", ["mountain tek"], False), ("Turbine", ["turbine"], True), ("One Way", ["one way"], True),
    ("Fox River", ["fox river"], False), ("Canadian Hat", ["canadian hat"], False), ("Wells Lamont", ["wells lamont"], False),
    ("Hootie Hoo", ["hootie hoo"], False), ("L1", ["l1", "l1 outerwear"], True), ("Dirndl & Bua", ["dirndl & bua"], False),
    ("Mitchie's Matchings", ["mitchie's matchings", "mitchies matchings"], False), ("Eivy", ["eivy"], False),
    ("Performance Ski", ["performance ski"], False), ("Howler Brothers", ["howler brothers", "howler bros"], False),
    ("Alashan", ["alashan"], False), ("Beyond Medals", ["beyond medals"], False), ("Orage", ["orage"], True),
    ("WhiteSpace", ["whitespace", "white space"], False), ("KUU", ["kuu"], True), ("Vola", ["vola"], True),
    ("XTM", ["xtm"], True), ("Dahu", ["dahu"], True), ("Diaface", ["diaface"], False), ("Skea", ["skea"], True),
    ("Jetty", ["jetty"], True), ("RMU", ["rmu"], True), ("Alpina", ["alpina"], True), ("Powpow", ["powpow"], False),
    ("M.Miller", ["m.miller", "m miller"], False), ("Everest Designs", ["everest designs"], False),
    ("FP Movement", ["fp movement"], False), ("Phunkshun", ["phunkshun", "phunkshun wear"], False),
    ("Free Fly", ["free fly"], False), ("prAna", ["prana"], False), ("Jet Set", ["jet set"], True), ("Rhone", ["rhone"], True),
    ("45NRTH", ["45nrth"], False), ("Van Deer", ["van deer"], False), ("CAMP", ["c.a.m.p.", "camp usa"], False),
    ("Candide", ["candide"], True), ("Deathgrip", ["deathgrip", "deathgrip glove"], False), ("Gravity Grabber", ["gravity grabber"], False),
    ("Dénériaz", ["deneriaz"], False), ("Kappa", ["kappa"], True), ("Vissla", ["vissla"], False), ("Terracea", ["terracea"], False),
    ("Grabber", ["grabber", "grabber warmers"], True), ("Emsco", ["emsco"], False), ("Gabel", ["gabel"], True),
    ("Bliz", ["bliz"], True), ("CTR", ["ctr"], True), ("Dissent", ["dissent", "dissent labs"], True),
    ("EMS", ["ems", "eastern mountain sports"], True), ("Peter Glenn", ["peter glenn"], False),
    # 只在 Skis.com / Aspen 等网站出现、网站又不给品牌字段的小品牌
    ("Northern Ridge", ["northern ridge"], False), ("Neff", ["neff"], True), ("Outdoor Tech", ["outdoor tech"], False),
    ("Ski the East", ["ski the east"], False), ("Sessions", ["sessions"], True), ("CB Sports", ["cb sports"], False),
    ("Stoko", ["stoko"], False), ("The Bomb Hole", ["the bomb hole"], False), ("Diamant", ["diamant"], True),
    ("Masterfit", ["masterfit"], False), ("ActionGlow", ["actionglow"], False), ("Lucky Bums", ["lucky bums"], False),
    ("Merrell", ["merrell"], False), ("Bluehouse", ["bluehouse"], False), ("ASBCO", ["asbco"], False),
    ("Rossignol Hero", [], False),  # 占位，避免 "hero" 被误认
]
BRANDS = [b for b in BRANDS if b[1]]
_BRAND_BY_ALIAS = {alias: name for name, aliases, _ in BRANDS for alias in aliases}
_AMBIGUOUS = {name for name, _, amb in BRANDS if amb}
_ALIASES_LONGEST_FIRST = sorted(_BRAND_BY_ALIAS, key=len, reverse=True)
_PREFIX_NOISE = re.compile(r"^(?:(?:used|demo|new|pre-owned|ex-demo|blem|sale)\s+|20\d\d(?:/\d{2,4})?\s+)+")


def brand_aliases(brand: str) -> list[str]:
    for name, aliases, _ in BRANDS:
        if name == brand:
            return aliases
    return [fold(brand)]


def known_brand(name: str | None) -> str | None:
    """名字是不是品牌表里的品牌（'Marker' / 'K2 Skis Inc' → 规范名）；不认识就 None，不做任何猜测。"""
    v = fold(name).strip()
    if not v:
        return None
    if v in _BRAND_BY_ALIAS:
        return _BRAND_BY_ALIAS[v]
    return next((_BRAND_BY_ALIAS[a] for a in _ALIASES_LONGEST_FIRST if re.match(rf"{re.escape(a)}\b", v)), None)


# ---------------------------------------------------------------- 品牌名清洗 & “学到的品牌”
# 品牌表收不全（小众服装/配件品牌有几百个），没收录的品牌用网站给的 vendor。但各网站写法很乱：
# "AUCLAIR" / "Auclair"、"LE BENT" / "Lé Bent"、"Gravity Grabber®"、"DEATHGRIP GLOVE CO."、"Screamer Hats" / "Screamer"……
# clean_brand 负责单个名字的清洗；BrandRegistry 从全部网站的 vendor 里“学”品牌：同一品牌的不同写法合并成一个标准写法，
# 并且在标题没写 vendor 的网站（Skis.com、Christy…）上，用学到的品牌名从标题开头认品牌。

_COMPANY_SUFFIX = re.compile(r"(?:[\s,]+(?:inc|llc|l\.l\.c|ltd|limited|co|company|corp|corporation|gmbh|ag|s\.?a|s\.?r\.?l|"
                             r"s\.?p\.?a|oy|ab|a/s|usa|u\.s\.a|international|intl)\.?)+$", re.I)
_PLACEHOLDER = re.compile(r"^(?:default|vendor|unknown|n/?a|none|null|other|misc|various|brand|generic|-+|\.+)$", re.I)
_SMALL_WORDS = {"the", "and", "of", "for", "le", "la", "de", "du", "des", "von", "van", "der", "&"}
_GENERIC_SUFFIX = {"hats", "headwear", "eyewear", "optics", "shoes", "footwear", "apparel", "clothing", "outerwear", "gloves",
                   "glove", "bags", "equipment", "skis", "ski", "snowboards", "sports", "outdoor", "outdoors", "wax", "usa"}
# 学到的品牌里，这些词开头的不拿来从标题认品牌（太常见：“Kids Jacket”“Pro Glove”“EVO 9 GW” 固定器…）
_STOP_PREFIX = {"men", "mens", "women", "womens", "kids", "kid", "youth", "junior", "girls", "boys", "the", "new", "used", "sale",
                "ski", "snow", "winter", "alpine", "mountain", "pro", "classic", "original", "performance", "north", "one",
                "all", "unisex", "adult", "basic", "essential", "premium", "sport", "sports", "outdoor", "backcountry",
                "park", "powder", "level", "surface", "evo", "apex", "race", "team", "black", "white", "red", "blue"}


def clean_brand(name: str | None) -> str | None:
    """网站 vendor 字段 → 干净的品牌名：去掉 ®™、括号、公司后缀（Inc / LLC / Co. / SRL…），全大写 / 全小写改成首字母大写
    （3 个字母以内的词当缩写保留大写：DB、FK、BUA）。明显不是品牌的（Default、Unknown、N/A）返回 None。"""
    if not name:
        return None
    s = unicodedata.normalize("NFKC", str(name))
    s = re.sub(r"[®™©]", "", s)
    s = re.sub(r"\s*[(\[][^)\]]*[)\]]\s*", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" -,.·/|")
    s = _COMPANY_SUFFIX.sub("", s).strip(" -,.")
    if not s or _PLACEHOLDER.match(s):
        return None
    letters = re.sub(r"[^A-Za-z]", "", s)
    if letters and ((letters.isupper() and len(letters) > 4) or (letters.islower() and len(letters) > 3)):
        words = []
        for i, w in enumerate(s.split(" ")):
            lw = w.lower()
            if lw in _SMALL_WORDS:
                words.append(lw if i else lw.capitalize())
            elif letters.isupper() and len(re.sub(r"[^A-Za-z]", "", w)) <= 3:
                words.append(w)
            else:
                words.append(w[:1].upper() + w[1:].lower())
        s = " ".join(words)
    return s


def _case_score(v: str) -> int:
    """同一品牌多种写法时选哪个：有大有小（ThirtyTwo、prAna）> 首字母大写 > 全大写/全小写。"""
    letters = re.sub(r"[^A-Za-z]", "", v)
    if not letters or letters.isupper() or letters.islower():
        return 0
    return 2 if any(c.isupper() for c in letters[1:]) and not v.istitle() else 1


class BrandRegistry:
    """从各网站的 vendor 学到的品牌：slug → 标准写法、合并关系（"screamerhats" → "screamer"）、标题开头认品牌用的名字。"""

    def __init__(self, names: dict[str, int], stores: dict[str, str] | None = None):
        from collections import Counter, defaultdict
        variants: dict[str, Counter] = defaultdict(Counter)
        for raw_name, n in names.items():
            c = clean_brand(raw_name)
            if not c:
                continue
            c = known_brand(c) or c
            variants[slug(c)][c] += n
        self.display: dict[str, str] = {}
        for s, cnt in variants.items():
            known = next((v for v in cnt if known_brand(v) == v), None)
            self.display[s] = known or max(cnt, key=lambda v: (_case_score(v), cnt[v]))
        self.alias: dict[str, str] = {}
        for s, d in self.display.items():
            words = d.split()
            if len(words) >= 2 and words[-1].lower() in _GENERIC_SUFFIX:       # "Screamer Hats" → "Screamer"
                t = slug(" ".join(words[:-1]))
                if t in self.display and t != s:
                    self.alias[s] = t
            if len(words) >= 2 and words[0].lower() == "the":                   # "The Mountain Studio" ↔ "Mountain Studio"
                t = slug(" ".join(words[1:]))
                if t in self.display and t != s:
                    self.alias[t] = s
        total: Counter = Counter()
        for s, cnt in variants.items():
            total[self.alias.get(s, s)] += sum(cnt.values())
        self.prefixes = sorted(((fold(self.display[s]), self.display[s]) for s in self.display
                                if s not in self.alias and total[s] >= 2 and len(s) >= 3
                                and fold(self.display[s]).split()[0] not in _STOP_PREFIX),
                               key=lambda x: -len(x[0]))
        self.stores = stores or {}

    def canonical(self, name: str) -> str:
        s = slug(name)
        s = self.alias.get(s, s)
        return self.display.get(s, name)

    def from_title(self, folded_title: str) -> str | None:
        for pre, disp in self.prefixes:
            if folded_title.startswith(pre) and (len(folded_title) == len(pre) or not folded_title[len(pre)].isalnum()):
                return disp
        return None


_REGISTRY: BrandRegistry | None = None


def use_brand_registry(reg: BrandRegistry | None) -> None:
    """抓取 / 重新整理之前由调度器设置（见 maintenance.load_brand_registry）；没设置时只用品牌表。"""
    global _REGISTRY
    _REGISTRY = reg


def canonical_brand(name: str | None) -> str | None:
    """任意写法的品牌名 → 标准写法（品牌表 > 学到的品牌 > 清洗后的原名）。"""
    c = clean_brand(name)
    if not c:
        return None
    kb = known_brand(c)
    if kb:
        return kb
    return _REGISTRY.canonical(c) if _REGISTRY else c


def _same_as_store(name: str, store: str | None) -> bool:
    if not store:
        return False
    a, b = slug(name), slug(store)
    return a == b or (len(a) >= 4 and (a in b or b in a))


def detect_brand(vendor: str | None, title: str, store: str | None = None) -> str | None:
    v = fold(vendor).strip()
    if v:
        kb = known_brand(vendor)  # "k2 skis inc" / "line skis" / "atomic austria"
        if kb:
            return kb
    t = _PREFIX_NOISE.sub("", fold(title).strip())
    for alias in _ALIASES_LONGEST_FIRST:
        if re.match(rf"{re.escape(alias)}(?![a-z0-9])", t):
            return _BRAND_BY_ALIAS[alias]
    # 日文/中文标题：品牌可能在任意位置（"【スキー板】アトミック ATOMIC ..."）
    for alias in _ALIASES_LONGEST_FIRST:
        name = _BRAND_BY_ALIAS[alias]
        if name in _AMBIGUOUS and not re.search(r"[぀-ヿ一-鿿]", alias):
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", t):
            return name
    if vendor:
        name = canonical_brand(vendor)  # 未收录的小众品牌：用网站给的 vendor（清洗、统一写法）
        # vendor 写的是店名本身（有的店给自营商品、服务、二手货填店名）：只有标题也以店名开头时才算自有品牌（evo、EMS、Peter Glenn）
        if name and not (_same_as_store(name, store) and not t.startswith(fold(name))):
            return name
    if _REGISTRY:  # 网站没给 vendor：用学到的品牌名从标题开头认（"Boulder Gear Endurance Jacket"）
        return _REGISTRY.from_title(t)
    return None


# ---------------------------------------------------------------- 年份（雪季）

_YEAR_RANGE = re.compile(r"(?<![\d.])(20[12]\d)\s*[/\-–]\s*(?:20)?([123]\d)(?![\d.])")
_SHORT_RANGE = re.compile(r"(?<![\d.\-/])([12]\d)\s*[/\-–]\s*([123]\d)(?![\d.\-/])")
_YEAR = re.compile(r"(?<![\d.])(20[12]\d)(?![\d.])")


def extract_year(*texts: str | None) -> int | None:
    """返回雪季结束年份：'2025/2026'、'25-26'、'2026' 都返回 2026。"""
    for text in texts:
        if not text:
            continue
        t = fold(text)
        m = _YEAR_RANGE.search(t)
        if m and int(m.group(2)) == (int(m.group(1)) + 1) % 100:
            return 2000 + int(m.group(2))
        for m in _SHORT_RANGE.finditer(t):
            a, b = int(m.group(1)), int(m.group(2))
            if b == a + 1 and 15 <= a <= 35:
                return 2000 + b
        years = [int(y) for y in _YEAR.findall(t) if 2012 <= int(y) <= 2035]
        if years:
            return max(years)
    return None


# ---------------------------------------------------------------- 性别

_KIDS_STRONG = re.compile(
    r"\b(kids?|kid's|kids'|junior|jr|youth|boys?|boy's|boys'|girls?|girl's|girls'|child(?:ren)?|children's|"
    r"toddler|grom|juniors?)\b|ジュニア|キッズ|子供|こども|儿童|童款|青少年")
_KIDS_WEAK = re.compile(r"\b(team|shorty|mini|tweener)\b")
_WOMEN = re.compile(r"\b(women'?s?|womens|woman|ladies|lady|female|wmns|wms)\b|レディース|ウィメンズ|女款|女士|女子")
_MEN = re.compile(r"\b(men'?s|mens|man's|male)\b|メンズ|男款|男士|男子")
_UNISEX = re.compile(r"\bunisex\b|ユニセックス|男女|中性")

# 只有女款才有的型号线（按品牌）；只在网站没给性别时使用
WOMEN_LINES = {
    "Blizzard": ["black pearl", "sheeva"],
    "Nordica": ["santa ana", "wild belle"],
    "Salomon": ["qst lux", "lux", "stella"],
    "Völkl": ["secret", "kenja", "yumi", "flair"],
    "Head": ["joy"],
    "Armada": ["arw", "reliance", "victa"],
    "Line": ["pandora"],
    "Black Crows": ["birdie"],
    "Atomic": ["cloud"],
    "Rossignol": ["rallybird"],
    "Elan": ["wildcat", "insomnia"],
    "Stöckli": ["nela"],
    "Icelantic": ["maiden"],
    "K2": ["luv"],
}


def _gender_from_tags(tags: list[str] | None) -> str | None:
    """网站标签往往同时打 Men's 和 Women's（=中性），所以和标题分开判断。"""
    blob = " | ".join(fold(x) for x in (tags or []) if len(x) < 40)
    if not blob:
        return None
    kids, women = bool(_KIDS_STRONG.search(blob)), bool(_WOMEN.search(blob))
    men, unisex = bool(_MEN.search(blob)), bool(_UNISEX.search(blob))
    if unisex or (men and women):
        return "unisex"
    if kids and not (men or women):
        return "kids"
    if women:
        return "women"
    if men:
        return "men"
    return None


def detect_gender(title: str, tags: list[str] | None, hint: str | None, brand: str | None,
                  model_tokens: list[str], had_w_suffix: bool, max_cm: int | None) -> tuple[str, str, str]:
    """返回 (gender, 来源, gclass)。gender: men/women/unisex/kids；gclass（分组用）: a=成人 w=女款 k=儿童。

    优先级：标题里的明确字样 > 型号自带的女款后缀/女款型号线 > 网站标签 > 网站分类页 > 默认中性。
    """
    t = fold(title)
    if _KIDS_STRONG.search(t) or (_KIDS_WEAK.search(t) and max_cm and max_cm <= 160):
        return "kids", "title", "k"
    if _WOMEN.search(t) or had_w_suffix:
        return "women", "title", "w"
    model_str = " ".join(model_tokens)
    for line in WOMEN_LINES.get(brand or "", []):
        if re.search(rf"(?<![a-z]){re.escape(line)}(?![a-z])", model_str):
            return "women", "brand", "w"
    if brand == "Faction" and "x" in model_tokens:   # Faction 女款以 X 结尾：Dancer 2X
        return "women", "brand", "w"
    if _UNISEX.search(t):
        return "unisex", "title", "a"
    if _MEN.search(t):
        return "men", "title", "a"
    for src, g in (("tags", _gender_from_tags(tags)), ("collection", hint)):
        if g not in ("men", "women", "unisex", "kids"):
            continue
        if g == "kids" and max_cm and max_cm > 175:  # 儿童分类页里混进了成人板
            continue
        return g, src, {"women": "w", "kids": "k"}.get(g, "a")
    return "unisex", "default", "a"


# ---------------------------------------------------------------- 固定器 / 成色

_BINDING_MODELS = (r"marker|look|tyrolia|griffon|squire|jester|duke|baron|kingpin|pivot|spx|nx ?\d{1,2}|xpress|"
                   r"strive|warden|stage|shift|protector|prd|attack|aaattack|m ?1[0-3]|l ?10|vmotion|ipt|rmotion|"
                   r"emx|elx|el ?\d{1,2}|rs ?\d{1,2}|rsw|pr ?\d{1,2}|x ?1[0-6]|mi ?1[0-2]|i ?1[0-6]|fks|konect|"
                   r"quikclik|\d{1,2}(?:\.\d)? ?gw|gw|tcx|compact|m3 ?\d{1,2}|evo ?\d{1,2}|jr ?\d|team ?\d|kid ?\d|c ?5|c ?[57] gw")
_BINDING_SPLIT = re.compile(rf"\s(?:\+|w/|with|&|and)\s*(?=(?:[\w.\-]+\s){{0,4}}?(?:bindings?|{_BINDING_MODELS})\b)|\s\+\s",
                            re.I)
_WITH_BINDINGS = re.compile(r"\bbindings?\b|\bsystem\b|\bpackage\b|\bcombo\b|\bski set\b|ビンディング付|金具付|ビンディングセット|"
                            r"セットモデル|\+\s*ビンディング|含固定器|带固定器|固定器套装|套装")
_FLAT = re.compile(r"\bflat\b|ski only|skis only|\bno bindings?\b|without bindings?|板のみ|金具別売|ビンディング別売|"
                   r"ビンディングなし|単品|不含固定器|裸板|单板身")
_USED = re.compile(r"\bused\b|pre-?owned|second ?hand|\bconsign|中古|二手")
_DEMO = re.compile(r"\bdemo\b|ex-?demo|\brental\b|試乗|展示|试用|租赁")
_BLEM = re.compile(r"\bblem(?:ished)?\b|\bcosmetic(?:ally)?\b|b-grade|open[- ]?box|訳あり|瑕疵")


# 系统板的固定器型号直接接在板名后面、没有 “+ / w/ / with”（Ski Depot 的写法）：
# “Elan Wildcat 80 Ti Shift X EL 9.0”“Head Supershape e-Rally SW+ Prot.”“Nordica Santa Ana 80 FDT”
# 注意 “Stockli Laser SX 2027” 的 SX 是板名（后面是年份，不是 4.5 / 7.5 这种 DIN）
_SYSTEM_BINDING = re.compile(
    r"(?:\bshift ?x?\s+)?\b(?:el|elw|elx|emx)\s?\d{1,2}(?:\.\d)?(?:\s?gw)?\b|\bsw\+?\s*prot(?:ector)?\.?(?=\s|$)|"
    r"\bprot(?:ector)?\.?(?=\s*(?:\d|gw|$))|\bprd\s?\d{1,2}\b|\bxpress\s?\d{1,2}\b|\bnx\s?\d{1,2}\b|\bspx\s?\d{1,2}\b|"
    r"\bkid-?x\s?\d\b|\bm\s?1[0-2](?:\s?gw)?\b|\bfdt\b|\btcx\s?\d{1,2}\b|\bvmotion\s?\d{1,2}\b|\bquikclik\b|"
    r"\brs\s?\d{1,2}\b|\bslr\s?\d{1,2}\b|\bfs\s?\d{1,2}\s?gw\b|\bsx\s?\d\.\d\b|\b[lc]\s?[5-7]\s?gw\b|\btp2\b", re.I)


def detect_bindings(title: str, product_type: str | None = None, tags: list[str] | None = None) -> tuple[bool, str | None]:
    t = fold(title)
    if _FLAT.search(t):
        return False, None
    m = _BINDING_SPLIT.search(t)
    if m:
        part = t[m.end():].strip(" -+")
        part = re.split(r"\s+-\s+|\s*\|\s*|\(", part)[0]  # " - Kids' Skis 2026/27" 之类的尾巴不要
        part = re.sub(r"\b(bindings?|skis?|20\d\d(?:/\d{2,4})?|system|kids'?|youth|women'?s|men'?s|junior)\b", "", part)
        part = re.sub(r"\s+", " ", part).strip(" -+,/")
        return True, (part[:40] or None)
    m = _SYSTEM_BINDING.search(t)
    if m:
        return True, re.sub(r"\s+", " ", m.group(0)).strip(" .")[:40]
    extra = fold(product_type) + " " + " ".join(fold(x) for x in (tags or []))
    if _WITH_BINDINGS.search(t) or re.search(r"\bsystems?\b|with bindings|ski ?\+ ?binding", extra):
        return True, None
    return False, None


def detect_condition(title: str, hint: str | None = None, tags: list[str] | None = None, url: str = "") -> str:
    t = fold(title) + " " + " ".join(fold(x) for x in (tags or []) if len(x) < 40) + " " + fold(url.rsplit("/", 1)[-1])
    if _USED.search(t):
        return "used"
    if _DEMO.search(t):
        return "demo"
    if _BLEM.search(t):
        return "blem"
    return hint if hint in ("used", "demo", "blem") else "new"


# ---------------------------------------------------------------- 型号名 & 分组 token

# 注意：不能把单字母 "s"/"c"/"x" 当噪声——Atomic Redster S9 / X9 / G9 靠它们区分
_NOISE_WORDS = {
    "ski", "skis", "alpine", "downhill", "flat", "only", "new", "sale", "clearance", "closeout", "demo", "used",
    "blem", "rental", "the", "and", "with", "package", "combo", "set", "womens", "women", "woman", "ladies",
    "mens", "men", "unisex", "kids", "kid", "youth", "boys", "boy", "girls", "girl", "childrens",
    "children", "child", "toddler", "adult", "pair", "size", "cm", "model", "system",
}
_TOKEN_ALIASES = {"junior": ["jr"], "juniors": ["jr"], "cti": ["c", "ti"], "tis": ["ti"],
                  "mitten": ["mitt"], "mittens": ["mitt"], "mitts": ["mitt"], "bibs": ["bib"], "hoodie": ["hoody"],
                  "gtx": ["goretex"], "gore": ["goretex"], "tex": [], "goretex": ["goretex"],
                  "gripwalk": ["gw"]}
_GENDER_PHRASE = re.compile(
    r"\b(?:women'?s|womens|woman's|men'?s|mens|kids'?|kid's|boys'?|girls'?|youth|unisex|ladies|junior's)\b", re.I)
_SIZE_TEXT = re.compile(r"\b\d{2,3}\s?cm\b", re.I)
_BRACKETS = re.compile(r"[\(\[【（<＜][^\)\]】）>＞]*[\)\]】）>＞]")


def _remove_brand(text: str, brand: str | None, vendor: str | None) -> str:
    # 别名是去掉变音符的写法（volkl），再加上原写法（Völkl / 网站给的 vendor），大小写不敏感地删除
    names = set(brand_aliases(brand)) | {brand} if brand else set()
    if vendor:
        names.add(vendor.strip())
    out = text
    for name in sorted((n for n in names if n), key=len, reverse=True):
        out = re.sub(rf"(?<![\w]){re.escape(name)}(?![\w])", " ", out, flags=re.I)
    return out


def _category_strip(category: str) -> str:
    from .categories import BY_ID
    words = BY_ID[category].strip if category in BY_ID else ()
    return "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))


def split_tokens(text: str) -> list[str]:
    """'M-Free 99Ti' -> ['m','free','99','ti']；'e.V8' -> ['e','v','8']。"""
    t = fold(text)
    t = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", t)
    parts = re.split(r"[^a-z0-9]+", t)
    out: list[str] = []
    for p in parts:
        if not p:
            continue
        out.extend(_TOKEN_ALIASES.get(p, [p]))
    return out


# 非双板分类：标题里“品类词”之后通常是颜色/镜片/尺码（"Smith Vantage MIPS Helmet Matte Black"），截掉
# 注意只截“通用品类词”：Bib/Pant、Mitt/Glove、上衣 Crew/下装 Bottom、Pullover/Hoody 是不同商品，这些词必须保留
_CUT_AFTER = {
    "helmet": r"helmets?", "goggle": r"goggles?", "jacket": r"jackets?", "pants": r"pants?|trousers?",
    "suit": r"(?:one[- ]piece|snow ?suit|ski suit)", "glove": r"gloves?", "sock": r"socks?",
    "boot": r"(?:ski )?boots?", "binding": r"bindings?", "pole": r"poles?", "backpack": r"backpack",
}


# 颜色是款式（variant），不是型号：“Griffon 13 ID Black”和“Griffon 13 ID White”是同一个型号
_COLOR_WORDS = (r"black|white|red|green|blue|yellow|orange|pink|purple|violet|silver|gr[ae]y|gold|anthracite|charcoal|"
                r"navy|olive|teal|turquoise|brown|beige|burgundy|lime|neon|graphite|matte?|gloss(?:y)?|satin|metallic|camo")
_COLOR_TAIL = re.compile(rf"(?:[\s/,&+-]+(?:{_COLOR_WORDS}))+\s*$", re.I)
_COLOR_ANY = re.compile(rf"\b(?:{_COLOR_WORDS})\b", re.I)


def _strip_colors(head: str, anywhere: bool = False) -> str:
    """去掉颜色词。固定器：任何位置（型号名里从来不用颜色词）；其他装备：只去掉末尾的颜色词。至少保留一个词。"""
    out = _COLOR_ANY.sub(" ", head) if anywhere else _COLOR_TAIL.sub("", " " + head)
    out = re.sub(r"\s+", " ", out).strip(" -,/&+")
    return out if split_tokens(out) else head


def clean_model(title: str, brand: str | None, vendor: str | None,
                category: str = "ski") -> tuple[str, list[str], bool, str | None]:
    """返回 (展示型号, token 列表, 是否带独立的 'W' 女款后缀, 固定器名)。"""
    t = unicodedata.normalize("NFKC", title).translate(_QUOTES)
    bind_name = None
    if category == "ski":  # 只有双板需要把 “+ 固定器” 那一段切掉
        has_bind, bind_name = detect_bindings(t)
        m = _BINDING_SPLIT.search(t)
        if m and has_bind:
            t = t[:m.start()]
        elif has_bind and (m2 := _SYSTEM_BINDING.search(t)):
            t = t[:m2.start()]   # “Wildcat 80 Ti Shift X EL 9.0” → 型号只到 “Wildcat 80 Ti”
    t = re.sub(r"'s\b", "", t)
    segments = [s for s in re.split(r"\s+[-|/]\s+|\s*\|\s*", t) if s.strip()]
    head = segments[0] if segments else t
    # 第一段太短（比如 "Atomic - Bent 100"）时合并下一段
    if len(split_tokens(_remove_brand(head, brand, vendor))) == 0 and len(segments) > 1:
        head = head + " " + segments[1]
    head = _BRACKETS.sub(" ", head)
    head = _remove_brand(head, brand, vendor)
    if category in _CUT_AFTER:
        # 保留到品类词为止（品类词本身也去掉）；如果品类词在最前面（"Gloves Hestra ..."）就不截
        m = re.search(rf"\b(?:{_CUT_AFTER[category]})\b", head, re.I)
        if m and m.start() > 0 and split_tokens(head[:m.start()]):
            head = head[:m.start()]
    head = _YEAR_RANGE.sub(" ", head)
    head = re.sub(r"(?<![\d.])(20[12]\d)(?![\d.])", " ", head)
    head = re.sub(r"(?<![\d.])([12]\d)\s*[/\-–]\s*([123]\d)(?![\d.])", " ", head)
    head = _GENDER_PHRASE.sub(" ", head)
    # "Men's" 在前面去掉 's 之后变成了 "Men"，这里把单独的性别词也从展示名里去掉
    head = re.sub(r"\b(?:men|women|mens|womens|unisex|ladies|kids|youth|boys|girls|toddlers?|infants?)\b", " ", head,
                  flags=re.I)
    head = _SIZE_TEXT.sub(" ", head)
    if category == "ski":
        # 竞技板常把长度写进标题（"RC4 Worldcup GS 188"）：140~215 的独立数字当作长度去掉，否则每个长度会变成一个型号
        head = re.sub(r"(?<![\d.])(?:1[4-9]\d|20\d|21[0-5])(?![\d.])", " ", head)
        head = re.sub(r"\b(?:skis?|alpine|downhill|flat|system|package|w/o bindings?)\b", " ", head, flags=re.I)
    else:
        if category == "binding":  # 刹车宽度是尺码，不是型号（"90mm"、"95-105mm"、"B100"）
            head = re.sub(r"(?<![\d.])(?:\d{2,3}\s?[-–/]\s?)?\d{2,3}\s?mm\b|\bbrakes?\b|\bb\d{2,3}\b", " ", head,
                          flags=re.I)
        strip = _category_strip(category)
        if strip:
            head = re.sub(rf"\b(?:{strip})\b", " ", head, flags=re.I)
    head = re.sub(r"\b(?:used|demo|blem(?:ished)?|pre-owned|ex-demo|new|sale|clearance|open[- ]box)\b", " ", head,
                  flags=re.I)
    head = re.sub(r"\s+", " ", head).strip(" -,:;+/")

    if category not in ("ski", "tuning"):  # 打蜡用品的颜色是温度档（Toko Red / Blue 蜡是两种东西），不能去掉
        head = _strip_colors(head, anywhere=category == "binding")

    raw_tokens = split_tokens(head)
    had_w = False
    tokens = []
    for tok in raw_tokens:
        if tok == "w":
            had_w = True
            continue
        if tok in _NOISE_WORDS:
            continue
        tokens.append(tok)
    # 展示名：去掉末尾单独的 W（性别单独显示）
    display = re.sub(r"\s+W$", "", head).strip()
    display = re.sub(r"\bW\b(?=\s*$)", "", display).strip()
    if display.isupper() and len(display) > 4:
        display = " ".join(w if re.search(r"\d", w) or len(w) <= 3 else w.capitalize() for w in display.split())
    return display or head, tokens, had_w, bind_name


# ---------------------------------------------------------------- 腰宽 & 类型

_WAIST_PATTERNS = [
    re.compile(r"(\d{2,3})\s?mm\s+(?:waist|underfoot)", re.I),
    re.compile(r"waist(?:\s+width)?\s*(?:of|:|-|is)?\s*(\d{2,3})(?:\.\d)?\s?mm", re.I),
    re.compile(r"underfoot(?:\s+width)?\s*(?:of|:|-)?\s*(\d{2,3})", re.I),
    re.compile(r"ウエスト\s*[:：]?\s*(\d{2,3})|センター\s*[:：]?\s*(\d{2,3})"),
    re.compile(r"腰宽\s*[:：]?\s*(\d{2,3})"),
]
_DIMENSIONS = re.compile(r"(?<!\d)(1\d{2})\s*[-/x]\s*(\d{2,3})\s*[-/x]\s*(1\d{2})(?!\d)")


def extract_waist(body: str | None, tokens: list[str], gclass: str) -> tuple[int | None, str | None]:
    if body:
        for rx in _WAIST_PATTERNS:
            m = rx.search(body)
            if m:
                val = int(next(g for g in m.groups() if g))
                if 60 <= val <= 145:
                    return val, "spec"
        m = _DIMENSIONS.search(body)
        if m:
            tip, waist, tail = map(int, m.groups())
            if 60 <= waist <= 145 and tip > waist < tail:
                return waist, "spec"
    if gclass != "k":
        nums = [int(t) for t in tokens if t.isdigit() and 60 <= int(t) <= 140]
        if len(nums) == 1:
            return nums[0], "name"
    return None, None


SKI_TYPES = ["all_mountain", "frontside", "race", "freeride", "park", "touring"]
_TYPE_RULES = [  # 顺序 = 优先级（越具体越靠前）
    ("touring", re.compile(r"touring|backcountry|skimo|randonn|freetour|backland|mountaineering|telemark|\bmtn\b|zero ?g|"
                           r"ツアー|山スキー|バックカントリー|登山|野雪|skitour")),
    ("race", re.compile(r"\brac(?:e|ing)\b|\bfis\b|world ?cup|\bwc\b|\bgs\b|\bsl\b|slalom|giant slalom|レーシング|競技|竞技")),
    ("park", re.compile(r"\bpark\b|freestyle|twin ?tip|\bjib|\bpipe\b|フリースタイル|パーク|公园|自由式")),
    ("freeride", re.compile(r"powder|freeride|big mountain|\bpow\b|パウダー|フリーライド|粉雪|野雪")),
    ("frontside", re.compile(r"carv|frontside|on[- ]?piste|\bpiste\b|groomer|基礎|デモ|基础|刻滑|道内")),
    ("all_mountain", re.compile(r"all[- ]?mountain|all[- ]?terrain|resort ski|mid[- ]?fat|オールマウンテン|全山|全能")),
]


def detect_type(hints: list[str], product_type: str | None, tags: list[str] | None, title: str) -> tuple[str | None, str | None]:
    for src, texts in (("collection", hints), ("product_type", [product_type or ""]),
                       ("tags", tags or []), ("title", [title])):
        blob = " ".join(fold(x) for x in texts if x)
        if not blob.strip():
            continue
        for name, rx in _TYPE_RULES:
            if rx.search(blob):
                return name, src
    return None, None


def type_from_waist(waist: int | None) -> str | None:
    if not waist:
        return None
    if waist < 80:
        return "frontside"
    if waist < 106:
        return "all_mountain"
    return "freeride"


# ---------------------------------------------------------------- 组装

def make_key(brand: str | None, tokens: list[str], gclass: str, bindings: bool) -> str:
    """双板的分组键：ski | 品牌 | 型号token | 性别大类 | f(板身)/b(含固定器)。其他分类见 _normalize_gear。"""
    return f"ski|{slug(brand or 'unknown')}|{' '.join(sorted(set(tokens)))}|{gclass}|{'b' if bindings else 'f'}"


def parse_key(key: str) -> dict:
    """把分组键拆开：{'category', 'brand', 'tokens', 'gclass', 'bindings'}（兼容旧的无分类前缀的双板键）。"""
    parts = key.split("|")
    if len(parts) == 4 and parts[3] in ("f", "b"):  # 旧格式：brand|tokens|gclass|f
        parts = ["ski"] + parts
    out = {"category": parts[0], "brand": parts[1] if len(parts) > 1 else "",
           "tokens": parts[2].split() if len(parts) > 2 else [], "gclass": parts[3] if len(parts) > 3 else "a"}
    out["bindings"] = len(parts) > 4 and parts[4] == "b"
    return out


def _store_name(retailer_id: str | None) -> str | None:
    return _REGISTRY.stores.get(retailer_id) if (_REGISTRY and retailer_id) else None


def normalize(raw: RawProduct) -> Listing:
    if (raw.category or "ski") != "ski":
        return _normalize_gear(raw)
    brand = detect_brand(raw.vendor, raw.title, _store_name(raw.retailer_id))
    display, tokens, had_w, bind_name = clean_model(raw.title, brand, raw.vendor)
    has_bind, bind_name2 = detect_bindings(raw.title, raw.product_type, raw.tags)
    cms = [s.cm for s in raw.sizes if s.cm]
    max_cm = max(cms) if cms else None
    gender, gsrc, gclass = detect_gender(raw.title, raw.tags, raw.gender_hint, brand, tokens, had_w, max_cm)
    year = extract_year(raw.title, raw.extra.get("year_text"))
    waist, waist_src = extract_waist(raw.body_text, tokens, gclass)
    ski_type, type_src = detect_type(raw.type_hints, raw.product_type, raw.tags, raw.title)
    if not ski_type and gclass != "k":
        ski_type, type_src = type_from_waist(waist), ("waist" if waist else None)
    condition = detect_condition(raw.title, raw.condition_hint, raw.tags, raw.url)
    if not tokens:  # 标题只有品牌名之类的极端情况
        tokens = split_tokens(raw.title)[:6] or ["unknown"]
    specs_ = {}
    binding = bind_name or bind_name2
    if has_bind and binding:  # 套装里的固定器：记下它的最大 DIN，方便和单买的固定器对比
        from .categories import specs as cat_specs
        specs_ = {f"binding_{k}": v for k, v in cat_specs("binding", binding).items()}
    return Listing(
        raw=raw, brand=brand, model=display, tokens=" ".join(sorted(set(tokens))), year=year,
        gender=gender, gender_src=gsrc, gclass=gclass, ski_type=ski_type, type_src=type_src,
        waist=waist, waist_src=waist_src, bindings=has_bind, binding_name=binding,
        condition=condition, model_key=make_key(brand, tokens, gclass, has_bind), category="ski", specs=specs_,
    )


def _normalize_gear(raw: RawProduct) -> Listing:
    """双板以外的所有分类：雪服、雪镜、固定器、雪鞋……"""
    from .categories import BY_ID, features, norm_size, specs
    cat = raw.category
    cdef = BY_ID.get(cat)
    brand = detect_brand(raw.vendor, raw.title, _store_name(raw.retailer_id))
    display, tokens, had_w, _ = clean_model(raw.title, brand, raw.vendor, cat)
    if not tokens:
        tokens = split_tokens(raw.title)[:6] or ["unknown"]
    if not display.strip():  # 标题去掉品牌和品类词后什么都不剩（如 "Marker Bindings"）→ 用原标题
        display = raw.title.strip()[:80]
    if cdef is None or cdef.gendered:
        gender, gsrc, gclass = detect_gender(raw.title, raw.tags, raw.gender_hint, brand if cat == "boot" else None,
                                             tokens, had_w, None)
    else:  # 雪镜、头盔、固定器等基本不分男女：只认标题里明确的儿童/女款字样
        t = fold(raw.title)
        if _KIDS_STRONG.search(t):
            gender, gsrc, gclass = "kids", "title", "k"
        elif _WOMEN.search(t):
            gender, gsrc, gclass = "women", "title", "w"
        else:
            gender, gsrc, gclass = "unisex", "default", "a"
    text = " ".join([raw.title, raw.product_type or "", " ".join(t for t in raw.tags if len(t) < 60)])
    feats = features(cat, text, (raw.body_text or "")[:800])
    spec = specs(cat, raw.title + " " + (raw.body_text or "")[:2000])
    if cat == "binding":  # 固定器的尺码 = 刹车宽度，汇总进规格里
        brakes = sorted({int(n[:-2]) for n in (norm_size(cat, s.label) for s in raw.sizes) if n and n.endswith("mm")}
                        | set(spec.get("brakes", [])))
        if brakes:
            spec["brakes"] = brakes
    key = f"{cat}|{slug(brand or 'unknown')}|{' '.join(sorted(set(tokens)))}|{gclass}"
    return Listing(
        raw=raw, brand=brand, model=display, tokens=" ".join(sorted(set(tokens))),
        year=extract_year(raw.title, raw.extra.get("year_text")), gender=gender, gender_src=gsrc, gclass=gclass,
        ski_type=None, type_src=None, waist=None, waist_src=None, bindings=False, binding_name=None,
        condition=detect_condition(raw.title, raw.condition_hint, raw.tags, raw.url), model_key=key,
        category=cat, features=feats, specs=spec,
    )
