"""Text measurement and font-size fitting.

Measures with the same font files and the same greedy word-wrap the official
renderer uses (`renderer/converter/wrap.py` + cr-renderer's FontManager), so a
box that fits here fits in the rendered PNG. Falls back to a glyph-width
estimate if the renderer stack is unavailable, so the engine never crashes.

cr-renderer draws text top-aligned, one line per `font_size * line_height`,
and casts `font_size` to int — so every size returned here is an int.
"""

import math
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple

_FONTS_DIR = Path("brand_kit/fonts")
_DEFAULT_LINE_HEIGHT = 1.2

try:  # the renderer is vendored next to the engine; import its measurement
    import skia  # type: ignore
    from renderer.converter.brand_fonts import load_brand_font_manager
    from renderer.converter.wrap import wrap_text as _wrap_text

    _font_manager = load_brand_font_manager(str(_FONTS_DIR)) if _FONTS_DIR.is_dir() else None
except Exception:  # pragma: no cover - only on a broken install
    skia = None
    _font_manager = None
    _wrap_text = None


def _style(el: dict) -> Tuple[str, str]:
    bold = (el.get("font_weight") or "regular").lower() == "bold"
    italic = (el.get("font_style") or "normal").lower() == "italic"
    weight = "bold" if bold else "regular"
    if bold and italic:
        return weight, "bolditalic"
    if bold:
        return weight, "bold"
    if italic:
        return weight, "italic"
    return weight, "regular"


@lru_cache(maxsize=64)
def _typeface(family: str, weight: str, style: str):
    ttf = _font_manager.lookup(family, weight, style)
    return skia.Typeface.MakeFromData(ttf)


def _text_key(el: dict, font_size: int, content: str) -> tuple:
    weight, style = _style(el)
    return (content, el.get("font_family", ""), weight, style, int(font_size),
            float(el.get("letter_spacing", 0) or 0))


@lru_cache(maxsize=200_000)
def _char_widths_cached(key: tuple) -> Tuple[float, ...]:
    content, family, weight, style, font_size, spacing = key
    if _font_manager is None or skia is None:
        avg = 0.55 if "Serif" not in family else 0.52
        return tuple(font_size * avg + spacing for _ in content)
    try:
        font = skia.Font(_typeface(family, weight, style), font_size)
        glyphs = font.textToGlyphs(content)
        return tuple(w + spacing for w in font.getWidths(glyphs))
    except Exception:
        return tuple(font_size * 0.55 + spacing for _ in content)


def char_widths(el: dict, font_size: int, content: str) -> List[float]:
    """Per-character advance widths at `font_size`, letter spacing included.
    Memoised: the layout re-measures the same strings hundreds of times while
    it shrinks and re-packs."""
    if not content:
        return []
    return list(_char_widths_cached(_text_key(el, max(1, int(font_size)), content)))


def _wrap(content: str, widths: List[float], max_width: float) -> str:
    if _wrap_text is not None:
        return _wrap_text(content, widths, max_width)
    # Minimal fallback mirroring the renderer's greedy space-only wrap.
    chars = list(content)
    line_w, last_space = 0.0, None
    for i, c in enumerate(chars):
        if c == "\n":
            line_w, last_space = 0.0, None
            continue
        w = widths[i] if i < len(widths) else 0.0
        if line_w + w > max_width and last_space is not None:
            chars[last_space] = "\n"
            line_w = sum(widths[j] for j in range(last_space + 1, i + 1) if chars[j] != "\n")
            last_space = None
        else:
            line_w += w
        if c == " ":
            last_space = i
    return "".join(chars)


class TextMetrics:
    __slots__ = ("lines", "max_line_width", "longest_word", "height", "font_size")

    def __init__(self, lines: int, max_line_width: float, longest_word: float, height: float, font_size: int):
        self.lines = lines
        self.max_line_width = max_line_width
        self.longest_word = longest_word
        self.height = height
        self.font_size = font_size


def measure(el: dict, font_size: int, width: float) -> TextMetrics:
    """Wrap `el`'s content into `width` at `font_size` exactly as the renderer will."""
    content = str(el.get("content", ""))
    lh = float(el.get("line_height", _DEFAULT_LINE_HEIGHT) or _DEFAULT_LINE_HEIGHT)
    font_size = max(1, int(font_size))
    return _measure_cached(_text_key(el, font_size, content), lh, round(float(width), 2))


@lru_cache(maxsize=200_000)
def _measure_cached(key: tuple, lh: float, width: float) -> TextMetrics:
    content, font_size = key[0], key[4]
    widths = list(_char_widths_cached(key))
    wrapped = _wrap(content, widths, width)
    line_widths, cur = [], 0.0
    word_widths, word = [], 0.0
    for i, c in enumerate(wrapped):
        w = widths[i] if i < len(widths) else 0.0
        if c == "\n":
            line_widths.append(cur)
            cur = 0.0
            word_widths.append(word)
            word = 0.0
            continue
        cur += w
        if c == " ":
            word_widths.append(word)
            word = 0.0
        else:
            word += w
    line_widths.append(cur)
    word_widths.append(word)
    lines = len(line_widths) if content else 1
    return TextMetrics(
        lines=lines,
        max_line_width=max(line_widths) if line_widths else 0.0,
        longest_word=max(word_widths) if word_widths else 0.0,
        height=lines * font_size * lh,
        font_size=font_size,
    )


def fits(el: dict, font_size: int, width: float, height: Optional[float]) -> Tuple[bool, TextMetrics]:
    m = measure(el, font_size, width)
    ok = m.longest_word <= width + 0.5 and m.max_line_width <= width + 0.5
    if height is not None:
        ok = ok and m.height <= height + 0.5
    # `max_lines` is deliberately ignored: the renderer wraps within the box
    # regardless of it (schema note), so matching the renderer is what counts.
    return ok, m


def fit_font_size(
    el: dict, width: float, height: Optional[float], start: float, floor: float
) -> Tuple[int, TextMetrics]:
    """Largest int font size in [floor, start] whose wrapped text fits.

    If even `floor` does not fit, returns `floor` with its metrics — the
    caller decides whether to widen the box or accept overflow.
    """
    lo, hi = max(1, int(math.floor(floor))), max(1, int(math.floor(start)))
    if hi < lo:
        hi = lo
    ok_hi, m_hi = fits(el, hi, width, height)
    if ok_hi:
        return hi, m_hi
    ok_lo, m_lo = fits(el, lo, width, height)
    if not ok_lo:
        return lo, m_lo
    # binary search the boundary between lo (fits) and hi (doesn't)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        ok, m = fits(el, mid, width, height)
        if ok:
            lo, m_lo = mid, m
        else:
            hi = mid
    return lo, m_lo


def natural_width(el: dict, font_size: int) -> float:
    """Width of the content on a single line (no wrapping)."""
    content = str(el.get("content", ""))
    return sum(char_widths(el, int(font_size), content))


def min_width_for(el: dict, font_size: int) -> float:
    """Narrowest box that wraps without any word overflowing."""
    content = str(el.get("content", ""))
    widths = char_widths(el, int(font_size), content)
    longest, cur = 0.0, 0.0
    for i, c in enumerate(content):
        if c in " \n":
            longest, cur = max(longest, cur), 0.0
        else:
            cur += widths[i]
    return max(longest, cur)
