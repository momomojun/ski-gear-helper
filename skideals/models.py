"""核心数据结构。

RawProduct  —— 适配器（每个网站一个）从网站上抓下来的“原始商品”，尽量少做加工。
Listing     —— 经过 normalize.py 标准化后的商品：识别出品牌/型号/年份/性别/类型/是否含固定器等。
IntlOffer   —— 日本 / 中国的参考价（自动抓取或手动录入）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SizeOption:
    """一个尺码（雪板长度）选项。"""
    label: str                      # 网站原始写法，如 "170 cm"
    cm: int | None = None           # 解析出的长度（厘米）
    available: bool = True          # 是否有货
    price: float | None = None      # 这个尺码的价格（不同尺码可能不同价）
    compare_at: float | None = None # 这个尺码的原价


@dataclass
class RawProduct:
    retailer_id: str
    external_id: str                # 网站内稳定的商品 ID（Shopify product id / SKU / URL 路径）
    url: str                        # 商品页绝对地址
    title: str                      # 商品标题原文
    price: float | None = None      # 当前售价（多个尺码取有货的最低价）
    compare_at: float | None = None # 原价 / 划线价（没有打折时为 None）
    currency: str = "USD"
    vendor: str | None = None       # 品牌（网站给的）
    product_type: str | None = None
    tags: list[str] = field(default_factory=list)
    image: str | None = None
    sizes: list[SizeOption] = field(default_factory=list)
    available: bool | None = None   # 整体是否有货（None = 未知）
    gender_hint: str | None = None  # 网站分类给出的性别：men / women / unisex / kids
    type_hints: list[str] = field(default_factory=list)  # 网站分类给出的类型线索（如 "all-mountain"）
    body_text: str | None = None    # 商品描述（用于提取腰宽等参数）
    condition_hint: str | None = None  # new / used / demo / blem
    extra: dict = field(default_factory=dict)
    category: str = "ski"           # 分类 id，见 skideals/categories.py（ski / binding / boot / jacket / goggle …）


@dataclass
class Listing:
    raw: RawProduct
    brand: str | None
    model: str                      # 展示用型号名，如 "Bent 100"
    tokens: str                     # 型号的规范化 token（排序后），用于跨网站匹配
    year: int | None                # 雪季结束年份：2025/26 雪季 -> 2026
    gender: str                     # men / women / unisex / kids
    gender_src: str                 # title / collection / brand / default
    gclass: str                     # 分组用：a=成人(男+中性) w=女款 k=儿童
    ski_type: str | None            # all_mountain / frontside / race / freeride / park / touring
    type_src: str | None
    waist: int | None               # 腰宽 mm
    waist_src: str | None           # spec / name
    bindings: bool                  # 是否含固定器
    binding_name: str | None
    condition: str                  # new / used / demo / blem
    model_key: str                  # 同款分组键（以分类开头："ski|atomic|100 bent|a|f"）
    category: str = "ski"
    features: list[str] = field(default_factory=list)   # 特征标签：goretex / mips / asianfit / gripwalk …
    specs: dict = field(default_factory=dict)           # 数值规格：固定器 din/brakes、雪鞋 flex/last、背包 volume

    @property
    def min_cm(self) -> int | None:
        cms = [s.cm for s in self.raw.sizes if s.cm]
        return min(cms) if cms else None

    @property
    def max_cm(self) -> int | None:
        cms = [s.cm for s in self.raw.sizes if s.cm]
        return max(cms) if cms else None


@dataclass
class IntlOffer:
    country: str                    # JP / CN
    source: str                     # kakaku / rakuten / manual ...
    title: str
    price: float
    currency: str                   # JPY / CNY
    url: str | None = None
    shop: str | None = None
    year: int | None = None
    bindings: bool | None = None
    match_score: float = 0.0        # 0~1，与美国型号的匹配程度
    note: str | None = None
