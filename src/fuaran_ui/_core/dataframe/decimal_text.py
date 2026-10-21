"""Exact-decimal text — the carrier of a ``decimal`` cell, and the arithmetic over it.

A ``decimal`` cell is CANONICAL TEXT, never a host decimal or float: two texts denote one number
exactly when their canonical forms are one string, so equality, grouping and distinctness over
decimal cells are string equality.

THE GRAMMAR READ is ``-?[0-9]+(\\.[0-9]+)?`` over ASCII digits only: an optional minus, at least
one integer digit, and a fraction of at least one digit where a point is written. Leading zeros and
trailing fraction zeros are read and normalised away. A leading ``+``, a bare point (``.5``,
``5.``), an exponent, a separator, white space and any non-ASCII digit are refused.

THE CANONICAL FORM WRITTEN has no leading zero on the integer part (a lone ``0`` stands for none),
no trailing zero on the fraction, no point where the fraction is zero, and no sign on zero.

Arithmetic: ``add`` / ``sub`` / ``mul`` / ``rem`` / ``negate`` / ``abs`` are closed over finite
decimals and exact. A quotient is not (a third has no finite expansion), and neither is a value
brought to fewer places, so the ONE rounding kernel — :func:`quantize` and :func:`divide`, both
over ``_round_quotient`` — takes a rounding mode and a scale and rounds the EXACT value once,
never twice. Values are held as ``(negative, magnitude, scale)`` — the number
``±magnitude × 10^-scale`` — with the magnitude a Python ``int`` (arbitrary precision, so nothing
overflows), and every answer is rendered back to canonical text. The standard-library ``decimal``
module is deliberately not used: its contexts and rounding are a different specification.

``to_float`` is the one place this module rounds to a float, for the aggregates whose answer is a
float by declaration and for an explicit cast; ``of_float`` is the one place an approximation
enters a decimal — a float's shortest round-trip digits, laid out without an exponent.
"""

from __future__ import annotations

import math
import re

from ...canonical import format_finite_double

#: The largest scale a rounding may name. A quotient is computed to the scale it names, so the
#: bound is a resource limit rather than a statement about decimals.
MAX_SCALE = 1000

#: The seven rounding-mode spellings, the wire's and the only ones. Complete on purpose: the
#: vocabulary of the well-known set ships whole.
HALF_EVEN = "half-even"
HALF_UP = "half-up"
HALF_DOWN = "half-down"
UP = "up"
DOWN = "down"
CEILING = "ceiling"
FLOOR = "floor"

ROUNDING_MODES: tuple[str, ...] = (HALF_EVEN, HALF_UP, HALF_DOWN, UP, DOWN, CEILING, FLOOR)

ZERO = "0"

_GRAMMAR = re.compile(r"(-?)([0-9]+)(?:\.([0-9]+))?")

# CPython bounds int <-> str conversion of very long digit strings (a denial-of-service guard), and a
# decimal text has no such bound. Conversions past the chunk go through these two helpers, which
# split the digits so every piece stays inside the guard.
_CHUNK = 1000
_CHUNK_BOUND = 10**_CHUNK


def _int_of(digits: str) -> int:
    if len(digits) <= _CHUNK:
        return int(digits)
    half = len(digits) // 2
    low = digits[half:]
    return _int_of(digits[:half]) * 10 ** len(low) + _int_of(low)


def _str_of(n: int) -> str:
    """The decimal digits of a non-negative int."""
    if n < _CHUNK_BOUND:
        return str(n)
    # Split near the middle digit (log10(2) ≈ 0.30103 digits per bit), so each half recurses.
    width = int(n.bit_length() * 0.30103) // 2
    high, low = divmod(n, 10**width)
    return _str_of(high) + _str_of(low).rjust(width, "0")


def _parts(s: object) -> tuple[bool, int, int] | None:
    """``(negative, magnitude, scale)`` of decimal text — the number ``±magnitude × 10^-scale``,
    with trailing fraction zeros dropped — or ``None`` for text outside the grammar."""
    if not isinstance(s, str):
        return None
    m = _GRAMMAR.fullmatch(s)
    if m is None:
        return None
    sign, ip, fp = m.group(1), m.group(2), (m.group(3) or "").rstrip("0")
    magnitude = _int_of((ip + fp).lstrip("0") or "0")
    return (sign == "-" and magnitude != 0, magnitude, len(fp))


def _render(negative: bool, magnitude: int, scale: int) -> str:  # noqa: FBT001
    """The canonical text of ``±magnitude × 10^-scale``."""
    if magnitude == 0:
        return ZERO
    digits = _str_of(magnitude)
    if scale <= 0:
        text = digits + "0" * (-scale)
    else:
        padded = digits.rjust(scale + 1, "0")
        ip, fp = padded[:-scale], padded[-scale:].rstrip("0")
        text = ip + ("." + fp if fp else "")
    return ("-" if negative else "") + text


def try_canonical(s: object) -> str | None:
    """The canonical form of ``s``, or ``None`` where ``s`` is not decimal text."""
    p = _parts(s)
    return None if p is None else _render(*p)


def is_canonical(s: object) -> bool:
    """True where ``s`` is decimal text already in canonical form."""
    return isinstance(s, str) and try_canonical(s) == s


def is_zero(s: str) -> bool:
    p = _parts(s)
    return p is not None and p[1] == 0


def _aligned(a: tuple[bool, int, int], b: tuple[bool, int, int]) -> tuple[int, int, int]:
    """Both signed values at their common scale: ``(scale, a, b)``."""
    scale = max(a[2], b[2])
    va = a[1] * 10 ** (scale - a[2])
    vb = b[1] * 10 ** (scale - b[2])
    return scale, -va if a[0] else va, -vb if b[0] else vb


def _of_signed(value: int, scale: int) -> str:
    return _render(value < 0, abs(value), scale)


def compare(a: str, b: str) -> int | None:
    """The numeric order of two decimal texts as ``-1`` / ``0`` / ``1``, or ``None`` where either is
    not decimal text. Canonical or not: ``1.50`` and ``1.5`` compare equal."""
    pa, pb = _parts(a), _parts(b)
    if pa is None or pb is None:
        return None
    _, va, vb = _aligned(pa, pb)
    return (va > vb) - (va < vb)


def add(a: str, b: str) -> str | None:
    """The exact sum, in canonical form; ``None`` where either operand is not decimal text."""
    pa, pb = _parts(a), _parts(b)
    if pa is None or pb is None:
        return None
    scale, va, vb = _aligned(pa, pb)
    return _of_signed(va + vb, scale)


def negate(a: str) -> str | None:
    p = _parts(a)
    return None if p is None else _render(not p[0], p[1], p[2])


def abs_(a: str) -> str | None:
    p = _parts(a)
    return None if p is None else _render(False, p[1], p[2])


def sub(a: str, b: str) -> str | None:
    nb = negate(b)
    return None if nb is None else add(a, nb)


def mul(a: str, b: str) -> str | None:
    pa, pb = _parts(a), _parts(b)
    if pa is None or pb is None:
        return None
    return _render(pa[0] != pb[0], pa[1] * pb[1], pa[2] + pb[2])


def rem(a: str, b: str) -> str | None:
    """The remainder of a TRUNCATING division, with the dividend's sign — the integer ``mod``'s
    convention — and exact. ``None`` for a zero divisor or an operand that is not decimal text."""
    pa, pb = _parts(a), _parts(b)
    if pa is None or pb is None or pb[1] == 0:
        return None
    scale = max(pa[2], pb[2])
    ma = pa[1] * 10 ** (scale - pa[2])
    mb = pb[1] * 10 ** (scale - pb[2])
    return _render(pa[0], ma % mb, scale)


def _round_quotient(mode: str, negative: bool, numerator: int, denominator: int) -> int:  # noqa: FBT001
    """``numerator / denominator`` (magnitudes, the denominator non-zero) rounded to an integer under
    ``mode``, where ``negative`` is the sign of the exact quotient. The remainder decides the last
    digit: twice the remainder against the divisor for the three half-way modes, its being non-zero
    for the four directed ones. One rounding, of the exact value."""
    q, r = divmod(numerator, denominator)
    if r == 0:
        return q
    twice = 2 * r
    half = (twice > denominator) - (twice < denominator)
    bump = q + 1
    if mode == DOWN:
        return q
    if mode == UP:
        return bump
    if mode == CEILING:
        return q if negative else bump
    if mode == FLOOR:
        return bump if negative else q
    if mode == HALF_UP:
        return bump if half >= 0 else q
    if mode == HALF_DOWN:
        return bump if half > 0 else q
    if mode == HALF_EVEN:
        if half > 0:
            return bump
        if half < 0:
            return q
        return q if q % 2 == 0 else bump
    raise ValueError(f"unknown rounding mode {mode!r}")


def in_scale(scale: int) -> bool:
    return 0 <= scale <= MAX_SCALE


def quantize(mode: str, scale: int, text: str) -> str | None:
    """``text`` brought to ``scale`` places under ``mode`` — exact (the value itself) where it already
    has no more places. ``None`` where the text is not decimal text or the scale is outside
    ``0 .. MAX_SCALE``."""
    p = _parts(text)
    if p is None or not in_scale(scale):
        return None
    negative, magnitude, s = p
    if s <= scale:
        return _render(negative, magnitude, s)
    return _render(negative, _round_quotient(mode, negative, magnitude, 10 ** (s - scale)), scale)


def divide(mode: str, scale: int, dividend: str, divisor: str) -> str | None:
    """``dividend / divisor`` correctly rounded to ``scale`` places under ``mode``. ``None`` where an
    operand is not decimal text, the scale is outside ``0 .. MAX_SCALE``, or the divisor is zero —
    a caller tells the last apart with :func:`is_zero` first."""
    pa, pb = _parts(dividend), _parts(divisor)
    if pa is None or pb is None or not in_scale(scale) or pb[1] == 0:
        return None
    na, da, sa = pa
    nb, db, sb = pb
    # dividend / divisor = (da / db) * 10^(sb - sa); scaled by 10^scale, the integer to round is
    # da * 10^e / db with e = sb - sa + scale.
    e = sb - sa + scale
    numerator = da * 10**e if e >= 0 else da
    denominator = db if e >= 0 else db * 10 ** (-e)
    negative = na != nb and da != 0
    return _render(negative, _round_quotient(mode, negative, numerator, denominator), scale)


def to_float(text: str) -> float | None:
    """The nearest ``float`` to a decimal text, or ``None`` where it is not decimal text or its
    magnitude is past the float range (an infinity is not the nearest float to any decimal)."""
    canonical = try_canonical(text)
    if canonical is None:
        return None
    f = float(canonical)  # CPython's string-to-float is correctly rounded.
    return None if math.isinf(f) else f


def of_float(f: float) -> str | None:
    """The decimal text of a FINITE float — its shortest round-trip digits in the shared cross-host
    float layout, laid out without an exponent. ``None`` for NaN and the infinities."""
    if math.isnan(f) or math.isinf(f):
        return None
    text = format_finite_double(f)
    e = text.find("E")
    if e < 0:
        return try_canonical(text)
    p = _parts(text[:e])
    if p is None:
        return None
    negative, magnitude, s = p
    scale = s - int(text[e + 1 :])
    if scale >= 0:
        return _render(negative, magnitude, scale)
    return _render(negative, magnitude * 10 ** (-scale), 0)


def truncate_to_int(text: str) -> int | None:
    """The integer part of a decimal, truncated toward zero, or ``None`` for text outside the
    grammar. The caller range-checks it."""
    p = _parts(text)
    if p is None:
        return None
    negative, magnitude, scale = p
    whole = magnitude // 10**scale
    return -whole if negative else whole
