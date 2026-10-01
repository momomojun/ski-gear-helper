"""滑雪装备分类体系：每个分类的名称、识别关键词、要从型号名里去掉的词、特征标签、尺码规则、日本/中国搜索词。

设计原则：
- **分类按证据判定**（见 assign）：标题关键词、网站自己的分类/标签、选项名、网址都算证据；
  没有任何证据时才相信“从哪个分类页抓来的”，而且混装的分类页里没有证据的商品不要。
  商品可以被改判到别的分类（Sports Basement 头盔页里的雪镜 → 雪镜），零件/配件归“其他配件”。
- 关键词同时支持英文 / 日文 / 中文（日文用于价格.com 比价，中文用于手动记录）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class Category:
    id: str
    name: str                     # 中文名
    group: str                    # 页面上的分组
    sizing: str                   # length（厘米长度）/ mondo（雪鞋码）/ apparel（XS~XXL）/ one（均码为主）
    gendered: bool = True         # 是否区分男女款
    strong: str = ""              # 强关键词（正则）：出现就基本能确定是这个分类
    strip: tuple[str, ...] = ()   # 型号名里要去掉的品类词（不影响“同款”判断）
    jp: str = ""                  # 日文搜索词（价格.com）
    cn: str = ""                  # 中文搜索词（淘宝/京东）
    kakaku: str | None = None     # 价格.com 分类代码（没有就不限定分类）


_C = Category
CATEGORIES: list[Category] = [
    # ---- 板 / 固定器 / 鞋 / 杖
    _C("ski", "双板", "板·鞋·固定器", "length", strong=r"(?<!for )\bskis\b|スキー板|双板",   # “Vises For Skis” 是修板工具
       strip=("ski", "skis", "alpine", "downhill"), jp="スキー板", cn="双板 滑雪板", kakaku="0009_0003_0024"),
    _C("binding", "固定器", "板·鞋·固定器", "brake", gendered=False,
       strong=r"\bbindings?\b|ビンディング|固定器",
       strip=("binding", "bindings", "ski", "alpine"), jp="スキー ビンディング", cn="双板 固定器"),
    _C("boot", "雪鞋", "板·鞋·固定器", "mondo", strong=r"\bskis? boots?\b|スキーブーツ|雪鞋|滑雪鞋",
       strip=("boot", "boots", "ski", "alpine"), jp="スキーブーツ", cn="双板 雪鞋"),
    _C("pole", "雪杖", "板·鞋·固定器", "length", gendered=False,
       strong=r"\bpoles?\b|ストック|雪杖", strip=("pole", "poles", "ski"), jp="スキーポール ストック", cn="滑雪杖"),
    # ---- 防护
    # 很多头盔/雪镜标题不写品类词（"Smith Mission MIPS"、"Squad + Bonus Lens"），MIPS / 面罩(visor) / OTG / 低鼻梁版 / 附送镜片 也算证据
    _C("helmet", "头盔", "防护", "apparel", gendered=False, strong=r"\bhelmets?\b|\bmips\b|ヘルメット|头盔",
       strip=("helmet", "helmets", "ski", "snow", "snowboard"), jp="スキー ヘルメット", cn="滑雪头盔"),
    _C("goggle", "雪镜", "防护", "one", gendered=False,
       strong=r"\bgoggles?\b|\botg\b|low[- ]bridge|bonus lens|ゴーグル|雪镜",
       strip=("goggle", "goggles", "ski", "snow", "snowboard"), jp="スキー ゴーグル", cn="滑雪镜"),
    _C("protection", "护具", "防护", "apparel",
       # 注意不能有单独的 protector：“Lens Protector”雪镜、Tyrolia “Protector”固定器、“Face Protector”护脸都会被误判
       strong=r"back protector|spine protector|impact shorts?|padded shorts?|crash (?:pants|shorts)|wrist guards?|"
              r"knee (?:pads?|guards?)|elbow pads?|back pads?|body armou?r|protective (?:shorts|vest|jacket)|"
              r"プロテクター|护具|护臀|护膝|护腕",
       strip=("protector",), jp="スキー プロテクター", cn="滑雪护具"),
    # ---- 服装
    _C("jacket", "雪服（外套）", "服装", "apparel",
       strong=r"\bjackets?\b|\banorak\b|\bparka\b|ジャケット|雪服|滑雪服",
       strip=("jacket", "jackets", "ski", "snow"), jp="スキーウェア ジャケット", cn="滑雪服 外套"),
    _C("pants", "雪裤 / 背带裤", "服装", "apparel",
       strong=r"\bpants?\b|\bbibs?\b|\btrousers?\b|\bovertrousers?\b|パンツ|ビブ|雪裤|背带裤",
       strip=("pant", "pants", "trouser", "trousers", "ski", "snow"), jp="スキーウェア パンツ", cn="滑雪裤"),
    _C("suit", "连体滑雪服", "服装", "apparel",
       strong=r"one[- ]piece|\bonesie\b|snow ?suit|ski suit|jumpsuit|coverall|\bbunting\b|ワンピース|つなぎ|连体",
       strip=("suit", "ski", "snow", "snowsuit"), jp="スキーウェア ワンピース", cn="连体滑雪服"),
    _C("midlayer", "中间层（抓绒/薄羽绒）", "服装", "apparel",
       strong=r"\bfleece\b|\bvest\b|\bhoodie\b|\bhoody\b|mid[- ]?layer|\bpullover\b|\b1/4[- ]zip\b|quarter[- ]zip|"
              r"half[- ]zip|\bsweater\b|フリース|ミドルレイヤー|抓绒|中间层",
       strip=("midlayer",), jp="ミドルレイヤー フリース", cn="滑雪 抓绒 中间层"),
    _C("baselayer", "保暖内衣（排汗层）", "服装", "apparel",
       strong=r"base ?layer|\bthermals?\b|long underwear|\blong johns\b|\bleggings?\b|\btights?\b|union suit|"
              r"ベースレイヤー|アンダーウェア|保暖内衣|排汗",
       strip=("baselayer", "base", "layer"), jp="スキー ベースレイヤー", cn="滑雪 速干 保暖内衣"),
    _C("glove", "手套", "服装", "apparel",
       strong=r"\bgloves?\b|\bmitts?\b|\bmittens?\b|\btrigger\b|\blobster\b|3[- ]finger|グローブ|ミトン|手套",
       strip=("glove", "gloves", "ski", "snow"), jp="スキー グローブ", cn="滑雪手套"),
    _C("sock", "滑雪袜", "服装", "apparel", strong=r"\bsocks?\b|ソックス|袜",
       strip=("sock", "socks", "ski"), jp="スキー ソックス", cn="滑雪袜"),
    _C("facewear", "护脸 / 围脖 / 头套", "服装", "one",
       # 不用品牌名 buff 当关键词：Buff 也卖头带、毛线帽
       # tube 要带限定词：“Neck Tube”“Fleece Tube”是脖套，“Heel Tube”（固定器零件）、“Glue Tube”不是
       strong=r"balaclava|neck ?gait[eo]r|neck ?warmer|face ?warmers?|face ?mask|"
              r"\b(?:neck|face|fleece|polar|merino|ecostretch|convertible|thermal|heavyweight|lightweight|midweight|knit|"
              r"wool|cozy|original|multifunctional) tubes?\b|"
              r"\bgaiter\b|\bhood\b|"
              r"バラクラバ|ネックウォーマー|フェイスマスク|面罩|护脸|头套|围脖",
       strip=(), jp="スキー フェイスマスク ネックウォーマー", cn="滑雪 护脸 面罩"),
    _C("hat", "帽子", "服装", "one", strong=r"\bbeanies?\b|\bhats?\b|\btoque\b|\bheadbands?\b|\bearbands?\b|"
                                           r"ear ?warmers?|ビーニー|ニット帽|帽子",
       strip=("beanie", "hat"), jp="ビーニー ニット帽", cn="滑雪帽"),
    # ---- 背包 / 雪崩 / 登山 / 保养 / 配件
    _C("backpack", "滑雪背包", "背包·安全·其他", "one", gendered=False,
       strong=r"\bbackpacks?\b|\bdaypack\b|airbag|avalanche pack\b|\bpack\b\s*\d{1,2}\s?l\b|\b\d{1,2}\s?l\b.*\bpack\b|"
              r"バックパック|ザック|背包",
       strip=("backpack", "pack"), jp="スキー バックパック", cn="滑雪背包"),
    _C("bag", "雪板包 / 雪鞋包", "背包·安全·其他", "one", gendered=False,
       strong=r"ski bags?|boot bags?|\bboot\b[\w\s]{0,15}\bpack\b|helmet bags?|boot (?:and|&) helmet (?:bags?|packs?)|"
              r"ski case|ski sleeve|"
              r"ski tube|double ski|ski roller|gear bag|"
              r"スキーケース|ブーツケース|雪板包|雪鞋包",
       strip=("bag",), jp="スキーケース", cn="雪板包"),
    _C("avalanche", "雪崩安全装备", "背包·安全·其他", "one", gendered=False,
       strong=r"\bbeacons?\b|\btransceivers?\b|\bavalanche\b|\bprobes?\b|\bshovels?\b|ビーコン|プローブ|ショベル|雪崩",
       strip=(), jp="雪崩 ビーコン", cn="雪崩 信标"),
    _C("skin", "止滑带（登山皮）", "背包·安全·其他", "one", gendered=False,
       strong=r"climbing skins?|\bskins?\b|シール|止滑带|登山皮", strip=("skin", "skins", "climbing"),
       jp="スキー シール クライミングスキン", cn="登山 止滑带"),
    _C("tuning", "打蜡 / 修板工具", "背包·安全·其他", "one", gendered=False,
       # stone 要带限定词：否则 “Crestone” 雪鞋、“Keystone” 雪裤会被当成修板工具
       strong=r"\bwax\b|\btuning\b|\bscraper\b|edge tuner|file guide|wax iron|\bbrush\b|\bvises?\b|"
              r"\b(?:diamond|gummy|arkansas|ceramic|sharpening) stones?\b|ワックス|チューン|打蜡",
       strip=(), jp="スキー ワックス", cn="雪板 打蜡"),
    _C("accessory", "其他配件", "背包·安全·其他", "one", gendered=False,
       strong=r"\bstraps?\b|\blocks?\b|\bdryers?\b|boot heater|\b(?:hand|toe|foot|body|pocket|boot) warmers?\b|"
              r"replacement lens|spare lens|\blens\b|"
              r"goggle case|\bleash\b|\bcarrier\b|\btether\b|pole baskets?|\bheated\b|\bbattery\b|boot gloves?|"
              r"boot covers?|boot horn",
       strip=(), jp="スキー 小物", cn="滑雪 配件"),
]
BY_ID: dict[str, Category] = {c.id: c for c in CATEGORIES}
GROUPS = ["板·鞋·固定器", "防护", "服装", "背包·安全·其他"]

# 标题识别顺序：越具体越靠前（boot bag 先于 boot、ski boots 先于 skis、airbag 背包先于雪崩装备）
_ORDER = ["bag", "backpack", "avalanche", "skin", "tuning", "protection", "goggle", "facewear", "helmet", "binding",
          "boot", "pole", "sock", "glove", "hat", "suit", "baselayer", "midlayer", "pants", "jacket", "accessory", "ski"]
_STRONG = {cid: re.compile(BY_ID[cid].strong, re.I) for cid in _ORDER if BY_ID[cid].strong}
# 带固定器的套装是“双板”，不是“固定器”
# 写法很多："Skis + Strive 13 Bindings"、"QST 92 w/ M10 Bindings"、"Absolut Joy + Protector 10 GW Skis"、"Ski System"
# “+” 要前面有空格：“Protector+ Attack 14 GW”“Speed 120 HV+ Ski Boots” 里的 + 是型号名的一部分
_SKI_PACKAGE = re.compile(r"\bskis?\b.{0,40}(?:\s\+|\bw/|\bwith\b|&).{0,40}\bbindings?\b|\bsystem skis?\b|\bski system\b|"
                          r"\bsystem bindings?\b.{0,40}\bskis?\b|\(\s*[^)]*\bsystem bindings?\s*\)|"   # “(M10 GW System Binding)”
                          r"(?:\s&|\s\|)\s*[\w .\-]{0,30}\bbindings?\b|\band\s+(?:marker|look|salomon|tyrolia)\b.{0,20}\bbindings?\b|"
                          r"\bskis? (?:and|&) bindings?|(?:\s\+|\bw/|\bwith\b)\s*[\w .\-]{0,30}\bbindings?\b|"
                          r"\s\+.{0,40}\bskis?\b(?!\s*boots?)|"
                          # “Skis w M 12 GW Bindings”（w 后面没斜杠）、“2024+ Marker TCX Bindings”、“152cm+ ELW 10 Demo Bindings”
                          r"\bskis?\s+w\s+[\w ./\-]{0,40}\bbindings?\b|(?:\d|cm)\+\s*[\w ./\-]{0,30}\bbindings?\b|"
                          # “w/ 固定器型号”：Volkl Mantra 88 W/Marker Griffon 13、QST 92 w/ M10 GW、Joy + Protector 10
                          r"(?:\s\+|\bw/)\s*(?:marker|look|salomon|tyrolia|head|atomic|rossignol|fischer|elan|v[oö]lkl|nordica|"
                          r"k2|line)?\s*(?:griffon|squire|jester|pivot|strive|stage|warden|attack|spx|nx|xpress|prd|protector|"
                          r"m1[0-3]|m[5-9]|el|rs|prw|mbs|vist|tp2|xcell)\s*\d{1,2}\b", re.I)
_GARMENT = re.compile(r"\b(?:jackets?|parkas?|anoraks?|pants?|bibs?|vests?|hoodies?|pullovers?|shirts?|sweaters?|fleece|"
                      r"base ?layers?|gloves?|mitts?|mittens?|socks?|beanies?|hats?|helmets?|goggles?)\b", re.I)
# 这两类分类页里互相出现属于正常，不改分类（雪服页里的“保暖外套”、中间层页里的轻薄羽绒服…）
_COMPATIBLE = {
    "jacket": {"midlayer", "suit"}, "midlayer": {"jacket", "baselayer", "pants"},  # 抓绒裤、羽绒裤属于中间层
    "baselayer": {"midlayer", "pants", "sock", "suit", "facewear"},  # 连体保暖内衣、带头套的保暖内衣
    "pants": {"suit", "baselayer"}, "suit": {"jacket", "pants", "midlayer"}, "facewear": {"baselayer", "midlayer"},
    "protection": {"baselayer", "pants"}, "skin": {"tuning"},        # 护臀裤、止滑带专用蜡
}


def classify(title: str, product_type: str | None = None) -> str | None:
    """只凭强关键词判断分类；判断不了返回 None。"""
    text = f"{title} | {product_type or ''}"
    if re.search(r"binding not included|\bw/o bindings?\b|without bindings?|\bski only\b", text, re.I) and \
            re.search(r"\bskis?\b", text, re.I):
        return "ski"   # “Fischer RC4 … (SKI ONLY - BINDING NOT INCLUDED)” 是板身
    # 套装标题里不会出现衣服：“Fire + Ice Saelly2 Insulated Ski Jacket”（品牌名 Bogner Fire + Ice）不是套装
    if _SKI_PACKAGE.search(text) and not _STRONG["bag"].search(text) and not _GARMENT.search(text) and \
            not re.search(r"\b(?:tools?|vises?|locks?|straps?|carriers?|racks?|covers?|wax|kits?)\b", text, re.I):
        return "ski"
    if re.search(r"\bbeanies?\b|\bheadbands?\b|\bearbands?\b", text, re.I):
        return "hat"   # “Ski Goggles Fox Fur Pom Beanie”是印着雪镜图案的毛线帽
    garment = _PRODUCT_NOUN.search(text)
    for cid in _ORDER:
        if garment and cid in ("skin", "avalanche"):
            continue   # “Pepper Skins Crewneck Top”（保暖内衣系列名）、“Beacon Vest”（马甲）
        rx = _STRONG.get(cid)
        if rx and rx.search(text):
            return cid
    return None


# ------------------------------------------------------------------ 最终分类判定（多信号）
# 为什么不能只“相信分类页”：零售商的分类页经常是混装的——Sports Basement 的“头盔”页其实是“头盔 + 雪镜”，
# “雪杖”页其实是“雪杖 + 全部配件”（暖手贴、雪镜套、雪橇…）；有的店雪杖页里混着整套雪板；而且很多标题不写品类词
# （“Smith 4D MAG”“Armada Legion”）。所以按证据强弱依次看：
#   不属于滑雪 → 零件/配件 → 标题 → 网站自己的分类（商品类型 / 结构化标签 / 分类路径）→ 选项名 → 网址 → 长度
# 都没有证据时才相信分类页；混装的分类页（很多商品的证据指向别的分类）里没有证据的商品直接不要（见 decide_batch）。

# 不是滑雪装备（出现在任何分类页里都不要）
# 注意别误伤：Jones “MTN Surf” 雪服、TNF “Salty Dog” 帽子、“Dog Days” 毛衣、Sidecut 的“(Sticker)”打磨片
_NOT_OURS = re.compile(
    r"sunglass|\bbike\b|bicycl|\bcycling\b|\bmtb\b|snowshoe|\bsleds?\b|\bsaucers?\b|snow ?tubes?\b|\btents?\b|"
    r"sleeping bags?|(?:trekking|hiking|walking|running)(?:\W+\w+){0,2}\W+poles?|nordic walking|\bfishing\b|"
    r"\bgolf\b|\bswim|"
    r"board ?shorts|surfboards?|wetsuits?|rash ?guards?|skateboard|\bsplitboard|gift ?cards?|"
    r"e-?gift|sticker (?:pack|sheet)s?|\bdecals?\b|\bposters?\b|\bkeychains?\b|\bmugs?\b|bottle opener|"
    # 注意：Columbia “Cirque Bowl”、Oakley “Bowls” 是雪服系列名（bowl 是雪场地形），“Ornament Fleece”是花纹名
    r"salad plate|dinner plate|(?:pasta|salad|cereal|serving) bowls?|(?:christmas|holiday|tree) ornaments?|coasters?|"
    r"\bpillows?\b|throw blankets?|pint glass|"
    r"\b(?:dog|pet) (?:collar|leash|harness|coat|jacket|bowl|toy|bed)s?\b", re.I)
_SNOWBOARD_HARDGOODS = re.compile(r"\bsnowboard (?:boots?|bindings?)\b", re.I)   # “Ski & Snowboard Boot Bag” 不算
# rain 后面要紧跟衣物词才是雨衣：Volcom “Rain GORE-TEX Bib” 是滑雪背带裤（Rain 是系列名）
_RAIN = re.compile(r"\brain(?:breaker|coat|wear|jacket)s?\b|"
                   r"\brain (?:jacket|shell|pant|bib|suit|hoodie|hoody|coat|poncho|parka|anorak)s?\b", re.I)
# 越野滑雪（Nordic / XC）的板、鞋、固定器、杖、蜡不是高山装备
_XC = re.compile(r"cross[- ]?country|\bxc\b|\bnordic\b|kick wax|klister|grip wax|\bnnn\b|\bsns\b|prolink|turnamic|"
                 r"\bskate ski|\bclassic ski", re.I)
_HARDGOODS = {"ski", "binding", "boot", "pole", "skin", "tuning"}

# 零件 / 配件：任何分类页里出现都归“其他配件”
_ACCESSORY_OVERRIDE = re.compile(
    r"\bdryers?\b|boot heaters?|heated insoles?|replacement batter(?:y|ies)|batter(?:y|ies) (?:packs?|charger)|"
    r"battery chargers?|charging cables?|usb cables?|boot gloves?|boot covers?|boot horn|heat packs?|"
    r"\b(?:hand|toe|foot|body|pocket|adhesive|boot)\s+warmers?\b|\bski locks?\b|\bcable locks?\b|\bski straps?\b|"
    r"\bvoile straps?\b|carry[- ]straps?|\bcarriers?\b|\bski leash(?:es)?\b|\btethers?\b|\binsoles?\b|\bfootbeds?\b|"
    r"\bstomp pads?\b|lens (?:case|cover|cloth|cleaner)|anti-?fog|no[- ]fog|fog (?:cloth|wipes?|spray)|cleaning cloth|"
    r"trigger test|test tool|boot masque|space warmers?|"
    r"airbag (?:canister|cylinder|cartridge)s?|"
    r"\b(?:canister|cylinder)s?\b", re.I)
# 电池 / 充电器 / 线缆：只有标题里没有“正经商品”（衣服、手套、袜子、头盔、固定器…）时才算配件——
# “Obermeyer Charger Jacket”“Dakine Charger Helmet”是产品线名，“Heated Socks + Batteries”是袜子
_POWER_PART = re.compile(r"\bbatter(?:y|ies)\b|\b(?:re)?chargers?\b|\bcables?\b|\bcords?\b", re.I)
_PRODUCT_NOUN = re.compile(r"\b(?:jackets?|parkas?|anoraks?|pants|bibs?|vests?|hoodies?|pullovers?|shirts?|tops?|sweaters?|"
                           r"fleece|base ?layers?|crews?|tees?|t-shirts?|henleys?|zips?|bottoms?|leggings?|tights?|jerseys?|"
                           r"suits?|gloves?|mitts?|mittens?|liners?|socks?|helmets?|mips|beanies?|hats?|"
                           r"bindings?|boots?)\b", re.I)
# 雪镜套 / 雪镜盒：只有 goggle 不是商品主体时才算（"Goggles with gogglesoc" 是附送雪镜套的雪镜）
_GOGGLE_ACC = re.compile(r"goggle (?:case|cover|bag|soc|sock|strap)s?\b|\bgogglesoc\b|\bsocs?\b", re.I)
_GOGGLE_NOUN = re.compile(r"\bgoggles?\b(?!\s*(?:case|cover|bag|soc|sock|strap|replacement|lens|lense)s?\b)", re.I)
_BOOT_PARTS = re.compile(r"\bstraps?\b|buckles?|\bliners?\b|insoles?|footbeds?|\bheaters?\b|\bdryers?\b|\bboot bags?\b|"
                         r"spoilers?|toe ?caps?|cat ?tracks?|replacement|\b(?:out)?soles?\b|\blaces?\b|\bcuffs?\b|"
                         r"\bboosters?\b|\bparts?\b|traction|protection|\bcovers?\b", re.I)
# 标题以零件词结尾的一定是零件（“Bootcap 2.5 Ski Boot Insulated Toe Cover”“… Ski Boot Sole Protection”）
_PART_TAIL = re.compile(r"\b(?:covers?|soles?|straps?|liners?|protection|traction|toe caps?|buckles?|laces?|parts?|"
                        r"spoilers?|boosters?|kits?)\s*(?:\([^)]*\))?\s*$", re.I)
# 单板固定器品牌（它们不做双板固定器）
_SNOWBOARD_BINDING_BRANDS = re.compile(r"^(?:burton|union|ride|rome|flow|bent metal|now|drake|nitro|spark r&d|karakoram|"
                                       r"salomon snowboard)\b", re.I)
# 背包 / 雪崩装备的零件：气瓶补充套件、水袋咬嘴、气囊触发测试工具……
_PACK_PARTS = re.compile(r"refill|bite valve|hydration (?:tube|hose|reservoir|bladder)|trigger (?:test|tool)|test tool|"
                         r"reset tool|canister cap|\breplacement\b|rain ?covers?|\bhip ?belt\b|shoulder straps?", re.I)
# 止滑带的附件：专用蜡、胶、裁剪刀、尾部卡扣/绑带、头部环、收纳袋、防粘网……
_SKIN_PARTS = re.compile(r"\bwax\b|adhesive|\bglue\b|trim(?:ming)? tool|cutter|tail (?:clips?|straps?|kits?|hooks?|fix)|"
                         r"tip (?:loops?|kits?|clips?|attachments?)|\bbags?\b|savers?|cheat ?sheets?|waterproofing|glop|"
                         r"\bclips?\b|\bhooks?\b|stretchers?|buckles?|\bplugs?\b|\bcables?\b", re.I)
_SKIN_NOUN = re.compile(r"\bskins?\b(?!\s*(?:wax|bags?|savers?|tail|tip|glue|adhesive|clips?|straps?|hooks?|stretchers?|"
                        r"waterproofing|cheat|care|kit)\b)", re.I)
_BOOT_NOUN = re.compile(r"\bboots?\b(?!\s*(?:straps?|buckles?|liners?|bags?|heaters?|dryers?|covers?|gloves?|horns?|"
                        r"soles?|laces?|spoilers?|parts?|boosters?|packs?|warmers?|trees?|carriers?)\b)", re.I)
_BINDING_PARTS = re.compile(r"replacement|\bparts?\b|\bplates?\b|adapters?|\bscrews?\b|"
                            r"heel ?pieces?|toe ?pieces?|\bshims?\b|risers?|spacers?|\binserts?\b|leash(?:es)?|"
                            r"crampons?|\bretainer|\bspring\b|\bpedal\b|\bafd\b|anti-?friction|"
                            r"\bjigs?\b|\bplugs?\b|\bdrill|\btaps?\b|templates?|mounting|\bglue\b|epoxy|\bbuddy\b|"
                            r"hardware|\bcovers?\b|comp kit|\bheels?\b",
                            re.I)  # 安装工具、固定器套、单独卖的后跟件
_POLE_PART_WORDS = r"baskets?|tips?|straps?|grips?|attachments?|ferrules?|parts?|collars?"
_POLE_PARTS = re.compile(rf"\b(?:{_POLE_PART_WORDS}|replacement)\b", re.I)
_POLE_NOUN = re.compile(rf"\bpoles?\b(?!\s*(?:{_POLE_PART_WORDS})\b)", re.I)   # “Pole Baskets”里的 pole 不算
_HELMET_PARTS = re.compile(r"chin ?bars?|\baudio\b|\bheadsets?\b|\bspeakers?\b|ear ?pads?|ear ?pieces?|\breplacement\b|"
                           r"helmet (?:cover|liner|lens|visor|hood|hoodz)s?\b|liner kit|fit kit|goggle clip", re.I)
_HELMET_NOUN = re.compile(r"\bhelmets?\b(?!\s*(?:covers?|liners?|lens(?:es)?|visors?|pads?|bags?|audio|speakers?|mounts?|"
                          r"hoods?|hoodz)\b)"
                          r"|\bmips\b", re.I)   # “Helmet Cover”里的 helmet 不算
# 只做一类配件的品牌（标题可能只写款式名，比如 Gogglesoc 的 “Protect Our Winters”）
_BRAND_CATEGORY = {"gogglesoc": "accessory"}
_GOGGLE_PARTS = re.compile(r"repl(?:acement)?\.? lens|spare lens|\brep lens|\blens only\b", re.I)
_LENS = re.compile(r"\blens(?:es)?\b", re.I)
_GOGGLE_CONTEXT = re.compile(r"bonus lens|\+|\bw/|\bwith\b|lenses included|\botg\b", re.I)   # 附送镜片的雪镜


def _part(source: str, t: str) -> bool:
    if _ACCESSORY_OVERRIDE.search(t):
        return True
    if _POWER_PART.search(t) and not _PRODUCT_NOUN.search(t):
        return True
    if _GOGGLE_ACC.search(t) and not _GOGGLE_NOUN.search(t):
        return True
    if (source == "binding" or _STRONG["binding"].search(t)) and _BINDING_PARTS.search(t):
        return True  # 垫板、螺丝、安装模具……
    # 刹车片：任何分类页里都算配件（“Power Brake2 Race (Attack 13)”括号里只是适配的固定器）；
    # 但“Pivot 15 GW Brake 95”“… Bindings 95mm Brake”是固定器写了刹车规格
    t_np = re.sub(r"\([^)]*\)", " ", t)
    if re.search(r"\bbrakes?(?:\d|\b)", t_np, re.I) and \
            not re.search(r"\bbindings?\b|\b(?:gw|mnc|grip ?walk|id)\b", t_np, re.I):
        return True
    if (source == "boot" or re.search(r"\bboots?\b", t, re.I)) and _BOOT_PARTS.search(t) and not _STRONG["bag"].search(t) \
            and (not _BOOT_NOUN.search(t) or _PART_TAIL.search(t)):
        return True  # 鞋带扣、魔术贴、内胆、鞋垫、鞋底、鞋头保暖套（“BOA Cuff Ski Boots”是雪鞋）
    if source in ("backpack", "avalanche") and _PACK_PARTS.search(t):
        return True  # 气瓶补充套件、水袋咬嘴、触发测试工具
    if (source == "skin" or re.search(r"\bskins?\b", t, re.I)) and _SKIN_PARTS.search(t) and not _SKIN_NOUN.search(t):
        return True  # 止滑带专用蜡、胶、裁剪刀、尾部卡扣
    if (source == "pole" or re.search(r"\bpoles?\b", t, re.I)) and _POLE_PARTS.search(t) and not _POLE_NOUN.search(t):
        return True  # 雪托、杖尖、腕带、握把
    if (source == "helmet" or re.search(r"\bhelmets?\b|\bvisors?\b", t, re.I)) and _HELMET_PARTS.search(t) and \
            not _HELMET_NOUN.search(t):
        return True  # 护颏、耳机、耳垫、头盔套、替换面罩
    # 替换镜片（“Goggles with Spare Lens”是附送备用镜片的雪镜，不算）
    # 单卖的镜片：“Oakley Flight Deck L Goggle Lens”“Smith I/O MAG Lens”（goggle 后面紧跟 lens，或者根本没有 goggle）
    if (_GOGGLE_PARTS.search(t) and not _GOGGLE_NOUN.search(t)) or \
            (source == "goggle" and _LENS.search(t) and not _GOGGLE_NOUN.search(t) and not _GOGGLE_CONTEXT.search(t)):
        return True  # 替换镜片
    return False


# 网站自己的分类名 / 标签 → 分类（整个词组要对得上，"Accessories" "Apparel" "Snow" 这种太泛的不算）
_TERMS: list[tuple[str | None, re.Pattern]] = [(c, re.compile(p, re.I)) for c, p in [
    (None, r"sunglass(?:es)?|bike helmets?|cycling|snowshoes?|sleds?|tents?|sleeping bags?|trekking poles?|swim(?:wear)?"),
    ("ski", r"(?:alpine |all[- ]mountain |freeride |powder |park |race |touring |backcountry )?skis|ski packages?|system skis"),
    ("binding", r"(?:ski |alpine |touring |at |alpine touring )?bindings"),
    ("boot", r"(?:ski|alpine|touring|at|alpine touring|alpine ski) boots"),
    ("pole", r"(?:ski |alpine |touring )?poles"),
    ("helmet", r"(?:ski |snow |snowsport |winter )?helmets?"),
    ("goggle", r"(?:ski |snow |snowsport |winter )?goggles?"),
    ("protection", r"(?:body )?protection|protective gear|impact shorts|padded shorts|wrist guards|back protectors?"),
    ("jacket", r"(?:ski |snow |insulated |shell |snowboard )?jackets|parkas?|anoraks?"),
    ("pants", r"(?:ski |snow |insulated |shell |snowboard )?(?:pants|bibs)|pants (?:&|and) bibs|bibs (?:&|and) pants"),
    ("suit", r"one[- ]pieces?|snow ?suits?|ski suits?|onesies?"),
    ("midlayer", r"mid[- ]?layers?|fleece(?:s| jackets| tops)?|vests|insulated tops?|hoodies|pullovers|sweaters"),
    ("baselayer", r"base ?layers?|thermals?|long underwear"),
    ("glove", r"gloves|mittens|mitts|gloves (?:&|and) mittens"),
    ("sock", r"(?:ski |snow )?socks"),
    ("facewear", r"balaclavas?|neck ?gaiters?|neck ?warmers?|face ?masks?|gaiters|neckwear|face ?wear"),
    ("hat", r"hats|beanies|headwear|headbands|earbands"),
    ("backpack", r"backpacks|(?:ski |avalanche |airbag |touring )?packs|daypacks"),
    ("bag", r"ski bags|boot bags|ski (?:&|and) boot bags|ski travel bags"),
    ("avalanche", r"avalanche(?: safety| gear| tools| equipment)?|beacons|transceivers|probes|shovels"),
    ("skin", r"(?:climbing )?skins"),
    ("tuning", r"wax(?:es)?|tuning|ski tuning|wax (?:&|and) tuning|tuning (?:&|and) wax|tune (?:&|and) wax"),
    ("accessory", r"insoles|footbeds|(?:hand |toe |hand (?:&|and) toe )?warmers|boot dryers?|dryers|ski locks|locks|"
                  r"ski straps|straps|leashes|carriers|goggle accessories|helmet accessories"),
]]
_TAG_KEY = re.compile(r"class|categ|type|dept|department", re.I)
_TERM_NOISE = re.compile(r"\b(?:men'?s|women'?s|womens|mens|kids'?|kid's|youth|junior|juniors|boys'?|girls'?|unisex|"
                         r"adult|toddler|all|shop|new|sale|clearance)\b|\d+", re.I)


def _term(value: str | None) -> str | None:
    """'BreadcrumbClass:Goggles' 的值、'00Goggles00'、'Shop/Ski/Helmets' 的最后一段、'SKI CUSHION SOCKS'… → 分类。
    返回 '__reject__' 表示明确不是滑雪装备（Sunglasses、Bike Helmets…）。"""
    if not value:
        return None
    v = _TERM_NOISE.sub(" ", str(value).replace("_", " ")).strip(" -&/,:")
    v = re.sub(r"\s+", " ", v).strip()
    if not v or len(v.split()) > 4:
        return None
    for cid, rx in _TERMS:
        if rx.fullmatch(v):
            return cid or "__reject__"
    return None


def _meta_vote(source: str, product_type: str | None, tags: list[str] | None) -> str | None:
    """网站自己的分类信息投票：商品类型（排除适配器填的“我们的分类名”）、结构化标签（key:value）、分类路径、纯标签。"""
    votes: dict[str, int] = {}
    src_names = {source, BY_ID[source].name} if source in BY_ID else {source}

    def add(c, w):
        if c:
            votes[c] = votes.get(c, 0) + w

    if product_type and product_type not in src_names:
        if re.search(r"accessor", product_type, re.I):   # "Ski Boot Accessories" 是配件，不是雪鞋；单独的 "Accessories" 太泛
            add("accessory" if len(product_type.split()) > 1 else None, 3)
        else:
            add(_term(product_type) or (classify(product_type) if len(product_type.split()) <= 4 else None), 3)
    for tag in tags or []:
        tag = str(tag)
        if len(tag) > 80:
            continue
        if ":" in tag:
            key, _, val = tag.partition(":")
            if _TAG_KEY.search(key):
                add(_term(val), 3)
        elif "/" in tag:            # 分类路径（Peter Glenn："Shop/Ski/Helmets"）
            add(_term(tag.rsplit("/", 1)[-1]), 2)
        else:
            add(_term(tag), 1)
    if not votes:
        return None
    if "__reject__" in votes and votes["__reject__"] >= max(votes.values()):
        return "__reject__"
    votes.pop("__reject__", None)
    best = max(votes.values())
    top = [c for c, n in votes.items() if n == best]
    if len(top) == 1:
        return top[0]
    return source if source in top else None   # 票数打平：包含本页分类就算本页，否则判断不了


def _meta_reject(product_type: str | None, tags: list[str] | None) -> bool:
    """网站自己的分类明确写着不是滑雪装备（商品类型 / 结构化标签 = Bike Helmets、Sunglasses…）：连标题证据也不看了。"""
    vals = [product_type] + [str(t).partition(":")[2] for t in (tags or [])
                             if ":" in str(t) and _TAG_KEY.search(str(t).partition(":")[0])]
    return any(_term(v) == "__reject__" for v in vals if v)


def _pick(source: str, found: str) -> str:
    return source if found == source or found in _COMPATIBLE.get(source, ()) else found


def assign(source: str, title: str, product_type: str | None = None, tags: list[str] | None = None,
           options: list[str] | None = None, url: str | None = None,
           lengths: list[int] | None = None, vendor: str | None = None) -> tuple[str | None, str]:
    """从“source 分类页”抓到的商品最终属于哪个分类。返回 (分类, 依据)；分类为 None 表示不要。
    依据：not-ours / xc / part / brand / title / meta / option / url / length / page（没有证据，相信分类页）。"""
    t = title or ""
    by_brand = _BRAND_CATEGORY.get(re.sub(r"[^a-z0-9]", "", (vendor or "").lower()))
    if by_brand and source != by_brand:
        return by_brand, "brand"
    if _NOT_OURS.search(t) or _meta_reject(product_type, tags) or \
            (_SNOWBOARD_HARDGOODS.search(t) and not re.search(r"\bski\b|\bbags?\b|\bpacks?\b|backpack", t, re.I)):
        return None, "not-ours"
    if source in ("jacket", "pants", "suit") and _RAIN.search(t):
        return None, "not-ours"   # 雨衣 / 雨裤不是雪服（Tactics 雪服页里的 “Cascadia 3L Rainbreaker Jacket”）
    if (source in _HARDGOODS or _STRONG["boot"].search(t) or _STRONG["binding"].search(t)) and \
            (_XC.search(t) or (source in _HARDGOODS and product_type and _XC.search(product_type))):  # 商品类型 “Nordic Ski Poles”
        return None, "xc"
    if (source == "binding" or _STRONG["binding"].search(t)) and \
            _SNOWBOARD_BINDING_BRANDS.search(re.sub(r"^\W+", "", (vendor or "") + " " + t if vendor else t)):
        return None, "not-ours"   # Burton / Union / Ride… 的固定器都是单板固定器
    if _part(source, t):
        return ("tuning" if source == "tuning" else "accessory"), "part"   # 打蜡页里的钻孔模具、螺丝塞还是修板工具
    # 硬货（雪杖 / 固定器 / 雪鞋）页里，网站明确标了“XX 配件”（“Pole Accessories”）、标题里又有零件词：是配件
    # （“Leki Trigger S Strap … Poles”）。服装不适用：服装网站把手套、帽子都归在 “Accessories” 大类下，手套内胆就是手套
    if source in ("pole", "binding", "boot") and product_type and re.search(r"\baccessor", product_type, re.I) and \
            len(product_type.split()) > 1 and \
            re.search(rf"\b(?:{_POLE_PART_WORDS}|straps?|liners?|buckles?|lens(?:es)?|brakes?|covers?|replacement)\b", t, re.I):
        return "accessory", "part"
    found = classify(t)
    if found:
        return _pick(source, found), "title"
    found = _meta_vote(source, product_type, tags)
    if found == "__reject__":
        return None, "not-ours"
    if found:
        return _pick(source, found), "meta"
    if options and any(_LENS.search(str(o)) for o in options):  # 雪镜的选项一般是 “Frame + Lens” / “Lens Color”
        return _pick(source, "goggle"), "option"
    if url:  # 网址只能“确认”本页分类（有的店网址是复制来的：雪鞋的网址写着 …-skis-2026），不能用来改判
        slug_words = re.sub(r"[-_/]+", " ", url.rsplit("/", 1)[-1].split("?")[0])
        found = classify(slug_words)
        if found and _pick(source, found) == source:
            return source, "url"
    if source == "pole" and lengths and max(lengths) > 140:  # 雪杖最长 ~135cm；更长的是雪板或越野雪杖，都不要
        return None, "length"
    return source, "page"


def decide_batch(source: str, results: list[tuple[str | None, str]], min_away: int = 5,
                 away_share: float = 0.2) -> tuple[list[str | None], bool]:
    """一个分类页（一批商品）的判定结果 → 最终分类列表。
    混装页（有证据的商品里 ≥20% 指向别的分类）里“没有证据、只能相信分类页”的商品不要；“其他配件”“打蜡”页本来就杂，不算。"""
    with_ev = [c for c, why in results if c is not None and why != "page"]
    away = sum(1 for c in with_ev if c != source)
    mixed = source not in ("accessory", "tuning") and away >= min_away and away >= away_share * len(with_ev)
    return [None if (why == "page" and mixed) else c for c, why in results], mixed


def is_wanted(category: str, title: str, product_type: str | None = None) -> bool:
    """这个商品是不是真的属于 category（只看标题/商品类型；完整判定见 assign）。"""
    return assign(category, title, product_type)[0] == category


# ------------------------------------------------------------------ 特征标签（筛选 & 卡片上显示）

@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    rx: re.Pattern
    cats: tuple[str, ...] = ()


def _f(key, label, pattern, *cats):
    return Feature(key, label, re.compile(pattern, re.I), tuple(cats))


_WEAR = ("jacket", "pants", "suit", "midlayer", "glove")
FEATURES: list[Feature] = [
    _f("goretex", "GORE-TEX", r"gore[- ]?tex|goretex|\bgtx\b", *_WEAR, "boot", "facewear"),
    _f("insulated", "保暖（有填充）", r"\binsulated\b|\bdown\b(?! to)|primaloft|thermore|\bpuffy\b|\bpuffer\b|"
                                    r"coreloft|thinsulate|synthetic insulation|\d{2,3}g (?:fill|insulation)",
       "jacket", "pants", "suit", "midlayer", "glove"),
    _f("shell", "硬壳（无填充）", r"\bshell\b|hardshell|3l\b|3-layer|2\.5l", "jacket", "pants", "suit"),
    _f("3in1", "三合一", r"3[- ]in[- ]1|three[- ]in[- ]one|\binterchange\b", "jacket", "glove"),
    _f("bib", "背带裤", r"\bbibs?\b|\bsuspenders?\b", "pants"),
    _f("softshell", "软壳", r"soft ?shell", "jacket", "pants", "midlayer"),
    _f("merino", "美利奴羊毛", r"merino|\bwool\b", "baselayer", "sock", "midlayer", "hat", "facewear", "glove"),
    _f("mitten", "连指手套", r"\bmitts?\b|\bmittens?\b|\blobster\b|3[- ]finger|\btrigger\b", "glove"),
    _f("leather", "皮革", r"leather", "glove"),
    _f("heated", "电加热", r"\bheated\b|\bheat\b.*\bbattery\b|\bbattery\b", "glove", "sock", "accessory", "jacket"),
    _f("mips", "MIPS 防旋转", r"\bmips\b|\bwavecel\b|\bspin\b|\bkoroyd\b", "helmet"),
    _f("audio", "耳机", r"\baudio\b|bluetooth|outdoor tech|aleck", "helmet"),
    _f("visor", "一体面罩", r"\bvisor\b", "helmet"),
    _f("asianfit", "亚洲版型（低鼻梁）", r"asian ?fit|low ?bridge|global ?fit|alternative ?fit|\baf\b|\blbf\b", "goggle", "helmet"),
    _f("photochromic", "变色镜片", r"photochromic|photo ?chromic|transitions|\bphoto\b|variable tint", "goggle"),
    _f("magnetic", "磁吸换片", r"\bmag\b|magnetic|\bmagna\b|quick ?change|\bqls\b", "goggle"),
    _f("bonuslens", "附送备用镜片", r"bonus lens|extra lens|spare lens|\+ ?(?:bonus )?lens|2 lenses|two lenses|with lens", "goggle"),
    _f("otg", "可戴眼镜(OTG)", r"\botg\b|over the glass", "goggle"),
    _f("gripwalk", "GripWalk 兼容", r"\bgw\b|grip ?walk", "binding", "boot"),
    _f("mnc", "多标准(MNC)", r"\bmnc\b|multi[- ]norm", "binding"),
    _f("touring", "登山/野外", r"\btour(?:ing)?\b|\btech\b|\bpin\b|kingpin|\bshift\b|duke pt|\bcast\b|\bzed\b|"
                              r"\bion\b|\bradical\b|rotation|\batk\b|\bplum\b|\bvipec\b|tecton|\bmtn\b|freeride pro|walk mode|"
                              r"\bhike\b|backcountry|\bskimo\b", "binding", "boot"),
    _f("demo", "可调试滑款", r"\bdemo\b|\brental\b", "binding"),
    _f("carbon", "碳纤维", r"carbon", "pole"),
    _f("adjustable", "可调节长度", r"adjustable|telescop|\bvario\b|\btwist\b", "pole"),
    _f("airbag", "气囊包", r"airbag|avabag|jetforce|\bras\b", "backpack"),
    _f("beacon", "信标", r"beacon|transceiver", "avalanche"),
    _f("shovel", "雪铲", r"shovel", "avalanche"),
    _f("probe", "探杆", r"\bprobe\b", "avalanche"),
    _f("kit", "套装", r"\bkit\b|\bpackage\b|\bset\b", "avalanche", "tuning", "baselayer"),
    _f("top", "上衣", r"\btop\b|\bcrew\b|\bshirt\b|\bhoody\b|\bhoodie\b|\bzip\b|\bneck\b|long ?sleeve|\bl/s\b", "baselayer"),
    _f("bottom", "下装", r"\bbottoms?\b|\bpants?\b|\bleggings?\b|\btights?\b|\bjohns\b", "baselayer"),
]


# 这些特征可以从商品描述里识别（材料类，描述里写了基本就是真的）；其余只看标题，避免描述里的“适合登山”之类误判
BODY_OK = {"goretex", "insulated", "shell", "softshell", "merino", "leather", "carbon", "mips"}


def features(category: str, text: str, body: str = "") -> list[str]:
    out = []
    for f in FEATURES:
        if f.cats and category not in f.cats:
            continue
        if f.rx.search(text) or (body and f.key in BODY_OK and f.rx.search(body)):
            out.append(f.key)
    return out


FEATURE_LABELS = {f.key: f.label for f in FEATURES}


# ------------------------------------------------------------------ 数值规格（固定器 DIN / 刹车宽度、雪鞋硬度 / 楦宽、背包容量）

_DIN_RANGE = re.compile(r"(?:din\s*)?(?<![\d.])(\d{1,2}(?:\.\d)?)\s*[-–~to]+\s*(\d{1,2}(?:\.\d)?)(?![\d.])", re.I)
_DIN_WORD = re.compile(r"\bdin\s*(\d{1,2})\b", re.I)
_BRAKE = re.compile(r"(?<![\d.])(7[0-9]|8[0-9]|9[0-9]|1[0-3][0-9])\s?(?:mm)?\s*(?:brakes?|stoppers?)|"
                    r"(?:brakes?|stoppers?)\s*[:\-]?\s*(7[0-9]|8[0-9]|9[0-9]|1[0-3][0-9])\s?mm|\bb(7\d|8\d|9\d|1[0-3]\d)\b",
                    re.I)
# 常见固定器型号名里的数字就是最大 DIN（Griffon 13、Pivot 15、Strive 14、Attack 14、Warden 13…）
_BINDING_MAX = re.compile(r"\b(?:griffon|squire|jester|duke|baron|kingpin|tour f|xcomp|xcell|pivot|spx|nx|rockerace|"
                          r"strive|warden|stage|shift|attack|aaattack|protector|prd|pr|evo|m|l|i|mi|x|v?motion|"
                          r"rmotion|ipt|emx|elx|el|rs|rsw|fks|konect|compact|team|jr|kid|free ten|freeten|sth2?|z)"
                          r"\s?(\d{1,2})(?:\.\d)?\b", re.I)
_FLEX = re.compile(r"(?<![\d.])(50|60|65|70|75|80|85|90|95|100|105|110|115|120|125|130|140|150)(?:\s?(?:flex|s|w|mv|lv|hv|"
                   r"gw|boa|\+|\b))", re.I)
_LAST = re.compile(r"(?<![\d.])(9[5-9]|10[0-6])(?:\.\d)?\s?mm(?:\s*last)?|\b(lv|mv|hv)\b", re.I)
_VOLUME = re.compile(r"(?<![\d.])(\d{1,2})\s?(?:l|liters?|litres?)\b", re.I)


def specs(category: str, text: str) -> dict:
    t = text
    out: dict = {}
    if category == "binding":
        m = _DIN_RANGE.search(t)
        if m and 0.5 <= float(m.group(1)) < float(m.group(2)) <= 20:
            out["din"] = [float(m.group(1)), float(m.group(2))]
        else:
            m = _BINDING_MAX.search(t) or _DIN_WORD.search(t)
            if m:
                mx = int(m.groups()[-1])
                if 4 <= mx <= 20:
                    out["din_max"] = mx
        if "din" in out:
            out["din_max"] = out["din"][1]
        elif "din_max" not in out:  # 兜底：标题里最后一个 4~20 的独立数字（"Salomon Shift MNC 13"、"Duke PT 16"）
            nums = [int(x) for x in re.findall(r"(?<![\d.])(\d{1,2})(?![\d.]|\s?mm)", t) if 4 <= int(x) <= 20]
            if nums:
                out["din_max"] = nums[-1]
        brakes = sorted({int(next(g for g in m.groups() if g)) for m in _BRAKE.finditer(t)})
        if brakes:
            out["brakes"] = brakes
    elif category == "boot":
        m = _FLEX.search(t)
        if m:
            out["flex"] = int(m.group(1))
        m = _LAST.search(t)
        if m:
            out["last"] = m.group(1) and int(m.group(1)) or {"lv": "窄楦 LV", "mv": "中楦 MV", "hv": "宽楦 HV"}[m.group(2).lower()]
    elif category == "backpack":
        m = _VOLUME.search(t)
        if m and 5 <= int(m.group(1)) <= 70:
            out["volume"] = int(m.group(1))
    return out


# ------------------------------------------------------------------ 尺码规范化

_APPAREL = [("4XL", r"^(?:4xl|xxxxl|4x)$"), ("3XL", r"^(?:3xl|xxxl|3x|xxx-?large)$"), ("XXL", r"^(?:2xl|xxl|2x|xx-?large)$"),
            ("XL", r"^(?:xl|x-?large|extra large)$"), ("L", r"^(?:l|lg|lrg|large)$"), ("M", r"^(?:m|md|med|medium)$"),
            ("S", r"^(?:s|sm|small)$"), ("XS", r"^(?:xs|x-?small|extra small)$"), ("XXS", r"^(?:xxs|2xs)$")]
_APPAREL_RX = [(k, re.compile(p, re.I)) for k, p in _APPAREL]


def _apparel_key(tok: str) -> str | None:
    """单个字母码 → 规范写法；'m-l' / 's/m' 两码合一 → 'M/L'；青少年码 'ym' → 'M'。"""
    tok = tok.strip()
    dual = re.fullmatch(r"([a-z0-9]+)\s?[/-]\s?([a-z0-9]+)", tok)
    toks = [dual.group(1), dual.group(2)] if dual else [re.sub(r"^y(?=x{0,2}[sml]$|xl$)", "", tok)]
    keys = [next((k for k, rx in _APPAREL_RX if rx.match(t)), None) for t in toks]
    return "/".join(keys) if all(keys) else None


@lru_cache(maxsize=65536)
def norm_size(category: str, label: str | None) -> str | None:
    """把各网站的尺码写法统一：雪鞋 → '26.5'；服装 → 'M'；雪板/雪杖 → 厘米数。返回 None 表示识别不了
    （通常是颜色/镜片名被网站当成了“尺码”选项，这种不参与尺码筛选）。"""
    if not label:
        return None
    s = str(label).strip()
    cat = BY_ID.get(category)
    kind = cat.sizing if cat else "one"
    if kind == "length":
        m = re.search(r"(?<![\d.])(\d{2,3})\s?cm", s, re.I) or re.fullmatch(r"\s*(\d{2,3})\s*", s)
        if m and 60 <= int(m.group(1)) <= 215:
            return m.group(1)
        m = re.search(r"(?<![\d.])(\d{2})(?:\.\d)?\s?(?:\"|in\b|inch)", s, re.I)  # 雪杖常用英寸：46" ≈ 117cm
        if m and 30 <= int(m.group(1)) <= 60:
            return str(round(int(m.group(1)) * 2.54 / 5) * 5)
        return None
    if kind == "mondo":
        m = re.search(r"(?<![\d.])(1[5-9]|2\d|3[0-3])(\.5)?(?![\d])", s)
        return f"{m.group(1)}{m.group(2) or ''}" if m else None
    if kind == "brake":  # 固定器的“尺码”其实是刹车宽度
        m = re.search(r"(?<![\d.])(7\d|8\d|9\d|1[0-3]\d)\s?(?:mm)?(?![\d.])", s, re.I)
        return f"{m.group(1)}mm" if m else None
    if kind in ("apparel", "one"):
        low = re.sub(r"\s+", " ", s.lower()).strip()
        if re.search(r"one ?size|\bos\b|\bo/s\b|^one$|^none$|unisize|\bosf[am]\b|均码|フリー", low):
            return "均码"
        # 括号里写了字母码的以它为准："37-38 (S)"、"Youth (M)"；否则去掉括号内容（"Small (7)" → small）
        for inner in re.findall(r"[(\[]([^)\]]*)[)\]]", low):
            k = _apparel_key(inner)
            if k:
                return k
        low = re.sub(r"[(\[][^)\]]*[)\]]", " ", low)
        for a, b in (("xxx-large", "3xl"), ("xx-large", "xxl"), ("x-large", "xl"), ("extra large", "xl"),
                     ("xx large", "xxl"), ("x large", "xl"), ("xx-small", "xxs"), ("x-small", "xs"),
                     ("extra small", "xs"), ("xx small", "xxs"), ("x small", "xs")):
            low = low.replace(a, b)
        low = re.sub(r"\b(?:youth|kids?|kid's|juniors?|jr|boys?|girls?|toddlers?|women's|womens|men's|mens|adult|"
                     r"unisex|regular|reg|tall|short|long|size)\b", " ", low)
        low = re.sub(r"\s*/\s*", "/", re.sub(r"\s+", " ", low)).strip(" -,.")
        if not low:
            return None
        parts = [p for p in re.split(r"[\s,;]+", low) if p]
        k = _apparel_key(parts[0])
        if k:
            return k
        if kind == "one":  # 雪镜、背包等：只认字母码/均码（数字、颜色名多半是镜片/容量，不是尺码）
            return None
        if m := re.fullmatch(r"([0-4])x", low):  # 女款加大码 1X / 0X（2X/3X 已在上面当作 XXL/3XL）
            return f"{m.group(1)}X"
        if m := re.fullmatch(r"([1-6]) ?t", low):  # 幼儿码 2T~6T
            return f"{m.group(1)}T"
        m = re.fullmatch(r"(\d{1,2}) ?m?(?:o|os|onths?)? ?[-–/] ?(\d{1,2}) ?(?:m|mo|mos|months?)", low)
        if m and int(m.group(2)) <= 36:  # 婴儿 6-12M
            return f"{m.group(1)}-{m.group(2)}M"
        m = re.fullmatch(r"(\d{1,2}) ?(?:m|mo|mos|months?)", low)
        if m and int(m.group(1)) <= 36:  # 婴儿 18M
            return f"{m.group(1)}M"
        if m := re.fullmatch(r"(\d{1,2}(?:\.5)?) ?[rls]?", low):  # 女款数字码 4/6/8、手套 7/8/9、腰围 32、"8L"（8 号加长）
            return m.group(1)
        if m := re.fullmatch(r"(\d{2}) ?[-–] ?(\d{2}) ?(?:cm)?", low):  # 头盔头围 55-59cm；袜子欧码 39-42
            a, b = int(m.group(1)), int(m.group(2))
            return f"{a}-{b}cm" if category == "helmet" and 48 <= a < b <= 68 else f"{a}-{b}"
        if m := re.fullmatch(r"(\d{1,2}(?:\.5)?) ?[-–/] ?(\d{1,2}(?:\.5)?)", low):  # 袜子鞋码 8-9、儿童 10/12
            return f"{m.group(1)}-{m.group(2)}"
        return None
    return None


APPAREL_ORDER = ["XXS", "XS", "S", "M", "L", "XL", "XXL", "3XL", "4XL", "均码"]
