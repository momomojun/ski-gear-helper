"""Outdoor shoe sizes and model discriminators; never convert via Mondo.

norm_size(label, *, shoe_size_system=None, shoe_gender=None, shoe_width=None) -> str | None
parse_shoe_size(label, *, size_system=None, gender=None, width=None) -> ShoeSize | None
norm_shoe_size(...) -> "SYSTEM:basis:value:width" | None
shoe_size_matches(left, right, *, left_context=None, right_context=None) -> bool

SYSTEM is US/EU/UK/JP or 未注明; basis is men/women/unisex/kids or 未注明.
Explicit label evidence wins over per-product context. Unknown systems stay unknown.
No US/EU/UK/JP conversion or male/female size conversion is attempted. Ambiguous
multi-size labels, ranges, Mondo and non-size option labels return None.
Widths are literal standards (D/2E/etc.) or narrow/regular/wide/xwide; these are
not converted into each other using assumed brand/gender size charts.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

FOOTWEAR = frozenset({"hiking_shoe", "trail_shoe"})
UNKNOWN = "未注明"
_SYSTEM = re.compile(r"(?<![a-z])(USA|US|EURO|EUR|EU|UK|JPN|JP)(?![a-z])|美码|欧码|英码|日码", re.I)
_SYSTEM_ALIASES = {"USA": "US", "EUR": "EU", "EURO": "EU", "JPN": "JP",
                   "美码": "US", "欧码": "EU", "英码": "UK", "日码": "JP"}
_BASIS = re.compile(r"\b(women(?:'?s)?|womens|wmns|men(?:'?s)?|mens|unisex|kids?|youth|junior)\b|男码|女码|童码", re.I)
_WIDTH = re.compile(r"\b(extra[ -]?wide|x[ -]?wide|wide|narrow|regular|standard|medium|[2-6]e|ee(?:ee)?|b|d)\b", re.I)
_WIDTH_ALIASES = {"extra wide": "xwide", "extrawide": "xwide", "x wide": "xwide",
                  "standard": "regular", "medium": "regular", "ee": "2e", "eeee": "4e"}


def _text(value) -> str:
    # Expand mixed fractions before NFKC joins the integer and numerator.
    s = str(value or "").replace("⅓", " 1/3").replace("⅔", " 2/3").replace("½", " 1/2")
    return unicodedata.normalize("NFKC", s).replace("’", "'").replace("⁄", "/").strip()


def _size_system(value: str | None) -> str:
    values = {_SYSTEM_ALIASES.get(m.group().upper(), m.group().upper()) for m in _SYSTEM.finditer(_text(value))}
    return next(iter(values)) if len(values) == 1 else UNKNOWN


def size_basis(value: str | None) -> str:
    values = set()
    for m in _BASIS.finditer(_text(value)):
        s = m.group().lower()
        values.add("women" if s.startswith(("women", "wmns")) or s == "女码" else
                   "men" if s.startswith("men") or s == "男码" else
                   "unisex" if s == "unisex" else "kids")
    return next(iter(values)) if len(values) == 1 else UNKNOWN


def shoe_width(value: str | None) -> str:
    """Only an explicit width value; a wide toe box in prose is not size width."""
    s = re.sub(r"[-\s]+", " ", _text(value).lower()).strip()
    s = re.sub(r"\s*(?:width|fit|楦宽|鞋宽)\s*", "", s).strip()
    if not _WIDTH.fullmatch(s):
        return UNKNOWN
    return _WIDTH_ALIASES.get(s, s)


@dataclass(frozen=True)
class ShoeSize:
    system: str
    gender: str
    value: str
    width: str = UNKNOWN

    @property
    def key(self) -> str:
        return f"{self.system}:{self.gender}:{self.value}:{self.width}"


def parse_shoe_size(label: str | None, *, size_system: str | None = None,
                    gender: str | None = None, width: str | None = None) -> ShoeSize | None:
    if not label:
        return None
    s = _text(label)
    # Canonical keys can round-trip through existing two-argument norm_size callers.
    parts = s.split(":")
    if len(parts) == 4 and parts[0] in {"US", "EU", "UK", "JP", UNKNOWN} and \
            parts[1] in {"men", "women", "unisex", "kids", UNKNOWN}:
        parsed = parse_shoe_size(parts[2], size_system=parts[0], gender=parts[1], width=parts[3])
        return parsed if parsed and parsed.key == s else None
    if re.search(r"\b(?:mondo(?:point)?|mp)\b|モンド", s, re.I):
        return None
    systems = list(_SYSTEM.finditer(s))
    if len(systems) > 1:
        return None
    system = _size_system(s) if systems else _size_system(size_system)
    basis = size_basis(s)
    explicit_basis = bool(_BASIS.search(s))
    if explicit_basis and basis == UNKNOWN:
        return None
    if not explicit_basis:
        basis = size_basis(gender)
    s = _SYSTEM.sub(" ", _BASIS.sub(" ", s))
    # M/W shorthand denotes a size basis only next to a number. A bare trailing M
    # is commonly medium width and is deliberately not read as men's sizing.
    short = re.search(r"\b([mw])\s*(?=\d)", s, re.I)
    if short:
        basis = "men" if short[1].lower() == "m" else "women"
        s = s[:short.start()] + s[short.end():]
    elif re.search(r"\d\s*w\b", s, re.I):
        # Trailing W is ambiguous: women versus wide. Require explicit context.
        return None
    width_matches = list(_WIDTH.finditer(s))
    widths = {shoe_width(m.group()) for m in width_matches}
    if len(widths) > 1:
        return None
    w = next(iter(widths)) if widths else shoe_width(width)
    s = _WIDTH.sub(" ", s)
    s = re.sub(r"\b(?:size|width|fit|adult)\b|码", " ", s, flags=re.I)
    # JP cm is kept in JP centimetres, with no conversion to Mondo or millimetres.
    if system == "JP":
        s = re.sub(r"(?<=\d)\s*cm\b", " ", s, flags=re.I)
    s = s.strip(" ()[]:")
    if width_matches:
        # Sources join an independently selected width as "9.5 / Wide".
        # Numeric slashes inside a size (8/9) remain unrecognized.
        s = s.strip(" /|,-")
    m = re.fullmatch(r"(\d{1,3}(?:\.\d+)?)\s*(?:\+?\s+([12])/([23]))?", s)
    if not m:
        return None
    if m[2] and ("." in m[1] or int(m[2]) >= int(m[3])):
        return None
    val = Fraction(m[1]) + (Fraction(int(m[2]), int(m[3])) if m[2] else 0)
    if val <= 0 or val > (350 if system == "JP" else 60 if system in {"EU", UNKNOWN} else 35):
        return None
    if val.denominator == 3:
        value = f"{val.numerator // 3} {val.numerator % 3}/3"
    else:
        # Decimal construction preserves the input exactly; no float rounding.
        value = f"{int(m[1])}.5" if m[2] else format(Decimal(m[1]), "f")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
    return ShoeSize(system, basis, value, w)


def norm_shoe_size(label: str | None, **context) -> str | None:
    size = parse_shoe_size(label, **context)
    return size.key if size else None


def norm_size(label: str | None, *, shoe_size_system: str | None = None,
              shoe_gender: str | None = None, shoe_width: str | None = None) -> str | None:
    """Public adapter/catalog interface; same keywords as categories.norm_size."""
    return norm_shoe_size(label, size_system=shoe_size_system, gender=shoe_gender, width=shoe_width)


def shoe_size_matches(left: str | None, right: str | None, *,
                      left_context: dict | None = None, right_context: dict | None = None) -> bool:
    a = parse_shoe_size(left, **(left_context or {}))
    b = parse_shoe_size(right, **(right_context or {}))
    return a is not None and b is not None and a.key == b.key


_GTX = re.compile(r"\bgore[- ]?tex\b|\bgoretex\b|\bgtx\b", re.I)
_NO_GTX = re.compile(r"\b(?:non|no|without)[ -]+(?:gore[- ]?tex|goretex|gtx)\b", re.I)
_CUT = re.compile(r"\b(mid|low|high)(?:[- ]cut)?\b", re.I)
_MODEL_WIDTH = re.compile(r"\b(?:extra[ -]?wide|x[ -]?wide|wide|narrow|regular[ -]width|standard[ -]width|[2-6]e|eeee|ee)\b", re.I)


def shoe_identity(title: str, width: str | None = None, evidence: str = "") -> tuple[str, dict]:
    """Return a title retaining generations/GTX/cut plus explicit model dimensions.

    Parentheses and dash-separated suffixes are significant on shoes. Do not pass
    the result through the apparel cut-after/bracket stripping heuristics.
    """
    title = _text(title)
    matches = list(_MODEL_WIDTH.finditer(title))
    widths = sorted({shoe_width(m.group()) for m in matches})
    declared = {shoe_width(part) for part in re.split(r"[,;+|]", str(width or ""))}
    declared.discard(UNKNOWN)
    w = "+".join(sorted(declared)) if declared else "+".join(widths) if widths else UNKNOWN
    clean = _MODEL_WIDTH.sub(" ", title)
    evidence = " ".join([title, evidence])
    gtx = not _NO_GTX.search(title) and bool(_GTX.search(_NO_GTX.sub(" ", evidence)))
    clean = _NO_GTX.sub(" ", clean)
    clean = _GTX.sub(" GTX ", clean)
    cuts = sorted({m[1].lower() for m in _CUT.finditer(evidence)})
    return clean, {"shoe_width": w, "shoe_membrane": "gtx" if gtx else "non-gtx",
                   "shoe_cut": "+".join(cuts) if cuts else UNKNOWN}
