"""适配器基类：每个零售网站（或每种电商平台）一个子类，负责把网站上的双板商品抓成 RawProduct。"""
from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Callable

from ..models import RawProduct, SizeOption
from ..net import Fetcher

# 标题里出现这些词的一般不是“单独的双板雪板”，直接跳过
# 注意词边界：\bnordic\b 不能写成 nordic（会误伤品牌 Nordica）；leash 会误伤 Nordica Unleashed
NOT_SKI_RE = re.compile(
    r"cross[- ]?country|\bxc\b|\bnordic\b|skate ski|classic ski|fish ?scale|waxless|skin ski|"
    r"snow ?blades?|ski ?boards?\b|skiblades?|splitboard|snowboard|"
    r"\bboots?\b|\bpoles?\b|\bbags?\b|\bskins?\b|\bstraps?\b|\bwax\b|\blocks?\b|\bleash(?:es)?\b|\bracks?\b|"
    r"\bcarriers?\b|tuning|jacket|\bpants?\b|\bbibs?\b|\bgloves?\b|\bmitts?\b|mittens?|goggles?|helmets?|"
    r"\bsocks?\b|hoodie|\btee\b|t-shirt|beanie|sticker|gift card|\bbrakes?\b|crampons?|ski tote|"
    r"\bsleeves?\b|protector pad|poster|keychain|\bcaps?\b|\btrucker\b|snapback|\bhats?\b|"  # 品牌官网双板页里的棒球帽
    r"salad plate|dinner plate|(?:pasta|salad|cereal|serving) bowls?|\bmugs?\b|(?:christmas|holiday|tree) ornaments?|"
    r"coasters?|\bpillows?\b|blankets?|\bglassware\b|pint glass|decorative|wall decor",  # 家居礼品 / 装饰用老木板
    re.I,
)


def parse_cm(label: str | None) -> int | None:
    """从 '170 cm' / '177cm' / '163' / '170cm / Black' 里提取雪板长度。"""
    if not label:
        return None
    m = re.search(r"(?<![\d.])(\d{2,3})(?:\.\d)?\s*(?:cm)?(?![\d.])", str(label), re.I)
    if not m:
        return None
    val = int(m.group(1))
    return val if 60 <= val <= 215 else None


def to_float(x) -> float | None:
    if x in (None, "", "0", 0, "0.00"):
        return None
    try:
        return round(float(str(x).replace(",", "").replace("$", "").strip()), 2)
    except ValueError:
        return None


class Adapter:
    """子类实现 fetch()，逐个 yield RawProduct。

    约定：
    - 只返回“高山双板雪板”（含登山板/竞技板；不要越野板 XC、雪鞋、雪杖、配件、带雪鞋的套装）
    - price = 当前售价；compare_at = 原价（没打折就 None）
    - sizes 尽量给出每个长度的有货情况
    - gender_hint 来自网站自己的分类（男款 / 女款 / 儿童分类页）
    """

    platform = "base"

    def __init__(self, cfg: dict, fetcher: Fetcher, log: Callable[[str], None] = print):
        self.cfg = cfg
        self.fetcher = fetcher
        self.log = log

    @property
    def rid(self) -> str:
        return self.cfg["id"]

    @property
    def base_url(self) -> str:
        return self.cfg["url"].rstrip("/")

    @property
    def category(self) -> str:
        """这次抓取的分类（由调度器根据 retailers.toml 里的 [retailers.categories.<分类>] 设置）。"""
        return self.cfg.get("category", "ski")

    def wanted(self, title: str, product_type: str | None = None) -> bool:
        """抓取时的初筛：这个商品要不要（最终属于哪个分类由调度器用 categories.assign 统一判定）。
        双板沿用严格的 NOT_SKI_RE；其他分类只剔除明确不是滑雪装备的东西（太阳镜、自行车头盔、越野装备…），
        属于别的分类的（手套页里的帽子）保留下来，由调度器改判到正确的分类。"""
        from ..categories import assign
        if self.category == "ski":  # 双板套装写法太多（“+ M10 Bindings”…），分类纠错容易误伤，只用 NOT_SKI_RE
            return not NOT_SKI_RE.search(title)
        return assign(self.category, title, product_type)[0] is not None

    def size_cm(self, label) -> int | None:
        """只有双板/雪杖的尺码是厘米长度；其他分类的尺码（S/M/L、26.5、100mm 刹车）原样保留在 label 里。"""
        return parse_cm(label) if self.category in ("ski", "pole") else None

    def fetch(self) -> Iterator[RawProduct]:
        raise NotImplementedError


__all__ = ["Adapter", "RawProduct", "SizeOption", "NOT_SKI_RE", "parse_cm", "to_float"]
