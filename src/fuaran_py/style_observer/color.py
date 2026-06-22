"""Colour + font primitives — Python port of ``Fuaran.UI.StyleObserver`` Color/FontRole.

``Rgba`` channels are 0–255; alpha is 0–1 — matching the browser's
``getComputedStyle`` convention so a Pyodide read-back is a direct field fill.
Compositing + WCAG luminance operate on these units directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class Rgba:
    """A resolved colour. ``r``/``g``/``b`` are 0–255; ``a`` (alpha) is 0–1."""

    r: float
    g: float
    b: float
    a: float


BLACK = Rgba(0.0, 0.0, 0.0, 1.0)
WHITE = Rgba(255.0, 255.0, 255.0, 1.0)
TRANSPARENT = Rgba(0.0, 0.0, 0.0, 0.0)


def rgb(r: float, g: float, b: float) -> Rgba:
    """Construct an opaque colour from 0–255 channels."""
    return Rgba(r, g, b, 1.0)


def rgba(r: float, g: float, b: float, a: float) -> Rgba:
    """Construct a colour with explicit alpha (0–1)."""
    return Rgba(r, g, b, a)


def is_opaque(c: Rgba) -> bool:
    """``True`` when the colour is fully opaque (alpha ≥ 1) — the composite walk stops here."""
    return c.a >= 1.0


def same_rgb(a: Rgba, b: Rgba) -> bool:
    """RGB-equal after rounding channels to the nearest integer (alpha ignored)."""
    return round(a.r) == round(b.r) and round(a.g) == round(b.g) and round(a.b) == round(b.b)


def try_parse_hex(raw: str) -> Rgba | None:
    """Parse a CSS hex colour (``#rgb`` / ``#rrggbb`` / ``#rrggbbaa``), or ``None`` if malformed."""
    s = raw.strip().lstrip("#")

    def hex2(i: int) -> int | None:
        try:
            return int(s[i : i + 2], 16)
        except ValueError:
            return None

    def hex1(i: int) -> int | None:
        try:
            v = int(s[i], 16)
        except ValueError:
            return None
        return v * 16 + v

    if len(s) == 3:
        r, g, b = hex1(0), hex1(1), hex1(2)
        return rgb(r, g, b) if r is not None and g is not None and b is not None else None
    if len(s) == 6:
        r, g, b = hex2(0), hex2(2), hex2(4)
        return rgb(r, g, b) if r is not None and g is not None and b is not None else None
    if len(s) == 8:
        r, g, b, a = hex2(0), hex2(2), hex2(4), hex2(6)
        if r is not None and g is not None and b is not None and a is not None:
            return rgba(r, g, b, a / 255.0)
        return None
    return None


def encode_rgba(c: Rgba) -> str:
    """Encode as compact JSON — ``{"r":R,"g":G,"b":B,"a":A}`` (2-decimal, invariant)."""
    return f'{{"r":{c.r:.2f},"g":{c.g:.2f},"b":{c.b:.2f},"a":{c.a:.2f}}}'


class FontRole(StrEnum):
    """Coarse font-family classification — the wire string is the value."""

    SANS_SERIF = "SansSerif"
    SERIF = "Serif"
    MONOSPACE = "Monospace"
    UNKNOWN = "Unknown"
