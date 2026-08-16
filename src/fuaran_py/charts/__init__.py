"""Render-time ``Chart`` → ``Drawing`` lowering (the Python host of the S4 parity leg).

``Chart`` stays a **semantic** wire kind; this module is the bounded layout engine
that turns a resolved chart spec + data rows into a canonical ``Drawing`` subtree
(scales, ticks, axes, gridlines, legend, series geometry) — so a chart renders as
first-party inline SVG on every host, headless included, and a new chart type is a
lowering rule + fixtures rather than bespoke per-host drawing.

Lowered arms: ``Bar`` (grouped + stacked), ``Line``, ``Area`` (overlaid +
stacked bands, Phase 637), ``Scatter`` (linear numeric x-scale, point marks,
Phase 636), ``Pie`` (polar, cubic-approximated wedges, Phase 638). Data-bearing
shapes carry a derivation-based ``markId`` (Phase 642 — ``series|category``,
stable under row reorder) that renders as ``data-fuaran-mark``.

The layout math is deterministic (R2): a fixed pixel viewBox, a ``{1,2,5}·10ⁿ``
nice-tick rule, and round-half-up coordinate rounding to 2 dp, so the output
depends only on the spec + data (never on enumeration order or platform float
print). This is a **byte-for-byte port of the F# reference** ``Fuaran.UI.Charts.lower``;
the shared ``wire-format-fixtures/chart-lowering/*`` corpus certifies the parity.

Chrome + text ink is surface-relative (``currentColor`` + per-role opacity), never a
spec wire field; series (categorical data) colours stay hex. See
``docs/CHARTS-DRAWING-PRIMITIVE-DESIGN.md`` (S4, D8).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ..canonical import format_finite_double
from ..model import Arr, Node, Obj, Value

# ── Layout constants (the fixed canonical drawing space) ─────────────────────

_W = 640.0
_H = 400.0
_MARGIN_TOP = 64.0  # title + legend band
_MARGIN_RIGHT = 28.0
# Phase 879 — both of these are now the FLOOR of an autosized margin, not the
# margin itself: the left one is derived from the widest FORMATTED y tick, the
# bottom one from the drop a tilted (or vertical) category label needs. The
# plot rectangle is therefore NOT a module constant — it depends on the text
# the chart is going to print, so it is computed per lowering.
_MARGIN_BOTTOM = 56.0  # x-axis category labels + x-axis title
_MARGIN_LEFT = 64.0  # right-aligned y-axis tick labels

# Ceilings on the autosized margins, as a share of the canvas.
_MARGIN_LEFT_MAX_SHARE = 0.3
_MARGIN_BOTTOM_MAX_SHARE = 0.35
# Breathing room between an autosized margin's content and the canvas edge —
# also absorbs the few percent by which a real font differs from the table.
_AXIS_LABEL_PADDING = 6.0

# A fixed, deterministic categorical palette (series index → colour).
#
# Palette v2 (default chart style v2) — 8 slots, fixed assignment order.
# Validated on BOTH surfaces (light #fcfcfb, dark #1a1a19) against the OKLab
# gate set: lightness band, chroma floor, adjacent-pair CVD ΔE (protan +
# deutan, Machado 2009 at severity 1.0), adjacent-pair normal-vision ΔE. Every
# slot sits in the INTERSECTION of the two lightness bands (OKLCH L
# 0.48-0.67), which is what lets one hex set serve both themes. The
# ASSIGNMENT ORDER is load-bearing — the gates are measured over ADJACENT
# pairs, so re-ordering the tuple can drop a passing set below the floor. Do
# not cycle or sort it. Mirrors the F# `ChartStyle.defaults.Palette`.
_PALETTE = (
    "#1a86ac",  # loch blue
    "#bf831c",  # ochre
    "#a51574",  # magenta
    "#21a766",  # green
    "#6454e5",  # violet
    "#af153d",  # crimson
    "#21a2b2",  # teal
    "#d3241b",  # vermilion
)


def _colour_for(i: int) -> str:
    return _PALETTE[i % len(_PALETTE)]


# ── Surface-relative ink (theme-aware chart lowering, S4 / D8) ───────────────
_INK = "currentColor"
_AXIS_OPACITY = 0.8
_GRID_OPACITY = 0.12
_LABEL_OPACITY = 0.66

# The chart's own font stack — carried in the wire so a lowered chart is
# self-contained + legible on every host without host CSS.
_CHART_FONT = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif"

# Typography + label geometry (Phase 879 reads these).
_TICK_LABEL_GAP = 12.0  # y-axis spine → right edge of a tick label
_TICK_FONT_SIZE = 13.0  # tick / category / axis-title / legend text
_TEXT_LINE_HEIGHT_FACTOR = 1.2  # a line's height as a multiple of its font size
_CATEGORY_LABEL_OFFSET_Y = 20.0  # x-axis spine → category-label baseline
_AXIS_TITLE_BOTTOM_OFFSET = 12.0  # canvas bottom → x-axis title BASELINE
# The MAGNITUDE of the category-label tilt. Tilt is the DEFAULT state — it is
# for LEGIBILITY, not a crowding fallback — and escalates to the vertical arm,
# which packs one label per line height at any category count.
_LABEL_TILT_DEGREES = 30.0
_VERTICAL_TILT_DEGREES = 90.0
# Legend geometry. The pitch is PER ENTRY since Phase 879 (swatch offset + the
# entry's own measured name width + this gap), never a fixed stride.
_LEGEND_LABEL_OFFSET_X = 15.0
_LEGEND_ENTRY_GAP = 24.0


# ── Deterministic numeric helpers ────────────────────────────────────────────


def _r2(x: float) -> float:
    """Round-half-up to 2 dp — the single deterministic rule every host reproduces."""
    return math.floor(x * 100.0 + 0.5) / 100.0


# ── Deterministic text metrics (Phase 879) ───────────────────────────────────
#
# A byte-for-byte MIRROR of the F# reference table
# (``Fuaran.UI.Charts.TextMetrics``) — mirrored, never re-derived, because the
# margins, the legend pitch and the label rotations it decides are all pinned
# by the shared ``chart-lowering/*`` corpus.
#
# THE APPROXIMATION IS THE SPEC. No host measures text: this one has no font
# engine at all, and a browser's measurement depends on which member of the
# font stack actually resolved — either would make the lowering's output a
# function of the host, destroying the byte-identical cross-host property the
# corpus rests on. So the widths come from a FIXED table of per-character
# advance widths as a fraction of the font size (em), approximating a typical
# sans-serif. A real font differs by a few percent; ``_AXIS_LABEL_PADDING``
# absorbs it.
#
#   1. Five width classes; an unlisted character (including every non-ASCII
#      one) takes the DEFAULT, which is what makes the table total.
#   2. Width = font_size × Σ advance_em(ch), summed LEFT TO RIGHT (float
#      addition is not associative — the order is part of the spec), rounded
#      once at the end.
#   3. Line height = font_size × _TEXT_LINE_HEIGHT_FACTOR.
#   4. Truncation keeps the longest prefix that still fits with the ellipsis;
#      when nothing fits the result is a bare "…", never the empty string.

_THIN_EM = 0.28
_NARROW_EM = 0.33
_DEFAULT_EM = 0.55
_WIDE_EM = 0.7
_EXTRA_WIDE_EM = 0.9
_ELLIPSIS = "…"

_THIN_CHARS = " !',.:;Iijl|"
_NARROW_CHARS = '"()*-/\\[]{}frt'
_EXTRA_WIDE_CHARS = "%@MWm"


def _advance_em(ch: str) -> float:
    """One character's advance width as a fraction of the font size.

    Total: an unlisted character takes ``_DEFAULT_EM``, so no host enumerates
    Unicode."""
    if ch in _THIN_CHARS:
        return _THIN_EM
    if ch in _NARROW_CHARS:
        return _NARROW_EM
    if ch in _EXTRA_WIDE_CHARS:
        return _EXTRA_WIDE_EM
    if ch in ("J", "L"):
        return _DEFAULT_EM
    if "A" <= ch <= "Z":
        return _WIDE_EM
    if ch == "w":
        return _WIDE_EM
    return _DEFAULT_EM


def _advance_em_of(text: str) -> float:
    """A string's advance width in em — summed LEFT TO RIGHT (rule 2)."""
    acc = 0.0
    for ch in text:
        acc += _advance_em(ch)
    return acc


def _text_width(font_size: float, text: str) -> float:
    """The estimated rendered width of ``text`` at ``font_size``, rounded once."""
    return _r2(font_size * _advance_em_of(text))


def _text_line_height(font_size: float, line_height_factor: float) -> float:
    """The estimated line height at ``font_size`` (rule 3)."""
    return _r2(font_size * line_height_factor)


def text_fits_box(font_size: float, line_height_factor: float, max_width: float, max_height: float, text: str) -> bool:
    """Does ``text`` fit a box ``max_width`` × ``max_height`` at ``font_size``?

    The single predicate a data-label gate answers inside/outside/suppress
    with, so a label can never disagree with the margin that made room for
    it."""
    return _text_width(font_size, text) <= max_width and _text_line_height(font_size, line_height_factor) <= max_height


def _truncate_to_width(font_size: float, max_width: float, text: str) -> str:
    """Deterministic ellipsis truncation to ``max_width`` (rule 4).

    A string that already fits comes back unchanged, so a host that never hits
    a bound never sees a "…"."""
    if _text_width(font_size, text) <= max_width:
        return text
    budget = max_width - _text_width(font_size, _ELLIPSIS)
    if budget < 0.0:
        return _ELLIPSIS
    acc = 0.0
    take = 0
    for i, ch in enumerate(text):
        nxt = acc + _advance_em(ch)
        if _r2(font_size * nxt) > budget:
            break
        acc = nxt
        take = i + 1
    return text[:take] + _ELLIPSIS


def _nice_num(x: float, round_it: bool) -> float:
    """A "nice" ``{1,2,5}·10ⁿ`` number for the magnitude of ``x`` (axis ticks)."""
    if x <= 0.0:
        return 0.0
    exp = math.floor(math.log10(x))
    f = x / (10.0**exp)
    if round_it:
        if f < 1.5:
            nf = 1.0
        elif f < 3.0:
            nf = 2.0
        elif f < 7.0:
            nf = 5.0
        else:
            nf = 10.0
    elif f <= 1.0:
        nf = 1.0
    elif f <= 2.0:
        nf = 2.0
    elif f <= 5.0:
        nf = 5.0
    else:
        nf = 10.0
    return nf * (10.0**exp)


def _nice_domain(lo: float, hi: float) -> tuple[float, float, float, list[float]]:
    """A nice value domain + its TICK STEP + its tick values for ``[lo, hi]``.

    The step is returned because the axis's decimal precision derives from it
    (Phase 876) — precision follows the axis, not the data.
    """
    if hi == lo:
        hi = lo + 1.0
    target_ticks = 5.0
    rng = _nice_num(hi - lo, False)
    step = _nice_num(rng / (target_ticks - 1.0), True)
    nice_lo = math.floor(lo / step) * step
    nice_hi = math.ceil(hi / step) * step
    # Enumerate ticks by integer count (float accumulation would drift).
    count = int(round((nice_hi - nice_lo) / step))
    ticks = [_r2(nice_lo + float(i) * step) for i in range(count + 1)]
    return nice_lo, nice_hi, step, ticks


def _format_num(n: float) -> str:
    """Canonical number form for a label/measure — whole values drop the decimal.

    Mirrors the F# ``DrawingSvg.formatNum`` (and the canonical wire float form): a
    whole value renders as a plain integer, else the shortest round-trip layout.
    """
    if math.isnan(n) or math.isinf(n):
        return "0"
    if n == math.floor(n) and abs(n) < 1e15:
        return str(int(n))
    return format_finite_double(n)


# ── The canonical invariant number formatter (Phase 876) ─────────────────────
#
# A byte-for-byte port of the F# reference spec. The chart lowering does NOT
# inherit the locale-aware rendering other surfaces give ``Format``: a chart's
# ticks are part of a drawing whose bytes must be identical on every host, so
# the rendering here is locale-INVARIANT by definition — period decimal
# separator, comma thousands separator, no locale data anywhere.
#
#   1. Decimals come from the TICK STEP, never the data (``_dps_of_step``).
#   2. The base render is round-half-up on the magnitude at that precision,
#      grouped in threes, zero-padded to exactly d places, a leading ``-`` only
#      when the rounded magnitude is non-zero.
#   3. The ``Format`` arms layer meaning over that base; ``Date`` /
#      ``RelativeTime`` / ``Duration`` are not value-axis formats and fall
#      through to the base render.
#   4. Display-unit scaling divides BOTH the value and the step by 10**n.


def _dps_of_step(step: float) -> int:
    """Decimal places implied by a tick step: the smallest ``d <= 6`` for which
    ``step * 10**d`` is (within relative float tolerance) an integer."""
    s = abs(step)
    if not (s > 0.0) or math.isnan(s) or math.isinf(s):
        return 0
    scaled = s
    for d in range(6):
        if abs(scaled - math.floor(scaled + 0.5)) <= 1e-9 * max(1.0, scaled):
            return d
        scaled *= 10.0
    return 6


def _group_thousands(digits: str) -> str:
    """Group an integral digit string in threes from the right with ``,``."""
    n = len(digits)
    if n <= 3:
        return digits
    head = n % 3
    parts: list[str] = []
    if head > 0:
        parts.append(digits[:head])
    for i in range(head, n - 2, 3):
        parts.append(digits[i : i + 3])
    return ",".join(parts)


def _render_fixed(dps: int, v: float) -> str:
    """Render ``v`` with EXACTLY ``dps`` decimals — round-half-up on the
    magnitude, comma thousands separators, period decimal point, invariant."""
    if math.isnan(v) or math.isinf(v):
        return "0"
    d = 0 if dps < 0 else (6 if dps > 6 else dps)
    scale = 10.0**d
    units = math.floor(abs(v) * scale + 0.5)
    int_part = math.floor(units / scale)
    frac_part = units - int_part * scale
    int_str = _group_thousands(_format_num(float(int_part)))
    body = int_str
    if d > 0:
        raw = _format_num(float(frac_part))
        body = int_str + "." + "0" * max(0, d - len(raw)) + raw
    return "-" + body if v < 0.0 and units > 0 else body


# ISO-4217 code -> symbol, the invariant table. An unlisted code renders as the
# code itself — deterministic, and never a wrong symbol.
_CURRENCY_SYMBOLS: dict[str, str] = {
    "EUR": "€",
    "USD": "$",
    "GBP": "£",
    "JPY": "¥",
    "CNY": "¥",
    "CHF": "CHF",
    "AUD": "$",
    "CAD": "$",
    "NZD": "$",
    "HKD": "$",
    "SGD": "$",
    "INR": "₹",
    "KRW": "₩",
    "BRL": "R$",
    "RUB": "₽",
    "ZAR": "R",
    "SEK": "kr",
    "NOK": "kr",
    "DKK": "kr",
    "PLN": "zł",
    "CZK": "Kč",
    "HUF": "Ft",
    "TRY": "₺",
    "MXN": "$",
    "THB": "฿",
    "ILS": "₪",
}


def _currency_symbol(iso: str) -> str:
    return _CURRENCY_SYMBOLS.get(iso, iso)


def _format_unit_symbol(fmt: Mapping[str, object] | None) -> str:
    """The unit symbol a ``Format`` contributes to an axis-unit label."""
    if fmt is not None and fmt.get("$type") == "Currency":
        return _currency_symbol(str(fmt.get("isoCode", "")))
    return ""


def _format_value_scale(fmt: Mapping[str, object] | None) -> float:
    """The x100 a ``Format.Percent`` applies to BOTH the value and the step."""
    return 100.0 if fmt is not None and fmt.get("$type") == "Percent" else 1.0


def _format_value(
    fmt: Mapping[str, object] | None,
    divisor: float,
    drop_symbol: bool,
    step: float,
    v: float,
) -> str:
    """Render one value-axis number. ``divisor`` is the display unit (1.0 when
    no scaling applies); ``drop_symbol`` suppresses a currency symbol on the
    ticks because the axis-unit label already states it once."""
    pct = _format_value_scale(fmt)
    dv = v * pct / divisor
    ds = step * pct / divisor
    kind = None if fmt is None else fmt.get("$type")
    pinned = fmt.get("decimals") if fmt is not None and kind in ("Number", "Percent") else None
    dps = int(pinned) if isinstance(pinned, (int, float)) else _dps_of_step(ds)
    body = _render_fixed(dps, dv)
    if kind == "Percent":
        return body + "%"
    if kind == "Currency" and not drop_symbol:
        sym = _currency_symbol(str(fmt.get("isoCode", "")))  # type: ignore[union-attr]
        return "-" + sym + body[1:] if body.startswith("-") else sym + body
    return body


# ── Display units (Phase 876) ────────────────────────────────────────────────
#
# The operator's prefix table: thresholds sit at 1 + 3k and the selected
# threshold ``t`` for a magnitude of exponent ``e`` satisfies
# ``e - 1 <= t < e + 2``, giving the unit exponent ``n = t - 1``. Each unit
# covers three exponents — Thousands for e in {3,4,5}, Millions for {6,7,8} —
# which is why a 12-million axis and a 900-million axis both read in millions.

AXIS_UNIT_MODES = ("Words", "WordsWithSymbol", "SIAbbreviation", "CompactPerTick", "Off")
"""How a value axis states its display unit once scaling applies."""

DISPLAY_UNIT_MIN_EXPONENT = 6
"""The smallest unit exponent that triggers scaling at the shipped default — the
operator's ``unit > 3`` gate, so scaling begins at MILLIONS and a
thousands-range axis still reads ``12,500`` in full."""

_UNIT_WORDS = {3: "Thousands", 6: "Millions", 9: "Billions", 12: "Trillions", 15: "Quadrillions"}
_UNIT_SI = {3: "k", 6: "M", 9: "G", 12: "T", 15: "P"}
_UNIT_COMPACT = {3: "K", 6: "M", 9: "B", 12: "T", 15: "Q"}


def _unit_exponent_of(max_abs: float) -> int:
    if not (max_abs > 0.0) or math.isnan(max_abs) or math.isinf(max_abs):
        return 0
    e = int(math.floor(math.log10(max_abs) + 0.5))
    n = 3 * int(math.ceil((e - 2) / 3.0))
    return -15 if n < -15 else (15 if n > 15 else n)


def _resolve_display_unit(
    mode: str,
    min_exponent: int,
    fmt: Mapping[str, object] | None,
    max_abs: float,
) -> tuple[float, str, bool, str]:
    """(divisor, tick suffix, drop-symbol, axis unit label) for a value axis
    whose PRINTED magnitudes peak at ``max_abs`` (already through any x100)."""
    n = _unit_exponent_of(max_abs)
    threshold = 3 if mode == "CompactPerTick" else min_exponent
    words = _UNIT_WORDS.get(n, "")
    if mode == "Off" or n < 3 or n < threshold or words == "":
        return 1.0, "", False, ""
    symbol = _format_unit_symbol(fmt)
    divisor = 10.0**n
    if mode == "WordsWithSymbol":
        return divisor, "", symbol != "", (words if symbol == "" else words + " of " + symbol)
    if mode == "SIAbbreviation":
        return divisor, "", symbol != "", _UNIT_SI.get(n, "") + symbol
    if mode == "CompactPerTick":
        return divisor, _UNIT_COMPACT.get(n, ""), False, ""
    return divisor, "", False, words


# ── DrawStyle builders (untagged style objects; only Some fields emitted) ─────


def _static(value: Value) -> Obj:
    return Obj("Static", {"value": value})


def _style_fill(fill: str) -> Obj:
    return Obj(None, {"fill": _static(fill)})


def _style_stroke(stroke: str, width: float) -> Obj:
    return Obj(None, {"stroke": _static(stroke), "strokeWidth": _static(width)})


# A translucent categorical fill (Phase 637 — area bands). The gridlines stay
# legible through the band; the series' full-strength Polyline edge on top
# carries the categorical colour at full contrast. Dropped to a wash (default
# chart style v2): at 0.35 two overlaid bands read as a third colour and the
# chrome beneath them disappears.
_AREA_FILL_OPACITY = 0.12

# Mark-geometry constants (default chart style v2).
_BAR_MAX_THICKNESS = 28.0  # hard pixel ceiling on a single bar's thickness
_STACK_SEGMENT_GAP = 2.0  # geometric gap between consecutive stacked-bar segments
_WEDGE_GAP_DEGREES = 0.75  # geometric angular padding between pie wedges

# Length of the small OUTSIDE tick marks on both axes: y-axis marks run left
# from the spine, x-axis marks run down from it, so neither eats plot area.
_TICK_MARK_LENGTH = 5.0


def _style_fill_opacity(fill: str, opacity: float) -> Obj:
    return Obj(None, {"fill": _static(fill), "opacity": _static(opacity)})


def _with_mark(style: Obj, mark_id: str) -> Obj:
    """Phase 642 — stamp a derivation-based mark identity onto a data-bearing
    shape's style (`series-field|category-key`, or the series field alone for a
    one-shape-per-series mark). Chrome deliberately stays unstamped — its
    identity is structural, not data-borne."""
    return Obj(style.tag, {**style.fields, "markId": mark_id})


def _style_stroke_ink(opacity: float, width: float) -> Obj:
    """Surface-relative structural stroke (``currentColor`` at a per-role opacity)."""
    return Obj(None, {"stroke": _static(_INK), "strokeWidth": _static(width), "opacity": _static(opacity)})


def _text_style(opacity: float | None, anchor: str, size: float, emphasis: str, rotation: float | None = None) -> Obj:
    """Surface-relative text-label style: ``currentColor`` + optional per-role opacity.

    ``rotation`` (Phase 879) is the clockwise rotation in degrees about the
    label's own anchor point; omitted when ``None``, so an unrotated drawing is
    byte-unchanged."""
    fields: dict[str, Value] = {
        "fill": _static(_INK),
        "textAnchor": anchor,
        "fontSize": size,
        "emphasis": emphasis,
        "fontFamily": _CHART_FONT,
    }
    if opacity is not None:
        fields["opacity"] = _static(opacity)
    if rotation is not None:
        fields["rotation"] = rotation
    return Obj(None, fields)


def _literal(text: str) -> Value:
    # 0.2.0 — the bare JSON string IS the canonical TextSource.Literal form.
    return text


# ── Shape builders (tagged ``$type`` objects) ────────────────────────────────


def _line(x1: float, y1: float, x2: float, y2: float, style: Obj) -> Obj:
    return Obj("Line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "style": style})


def _rectangle(x: float, y: float, w: float, h: float, corner: float | None, style: Obj) -> Obj:
    fields: dict[str, Value] = {"x": x, "y": y, "width": w, "height": h, "style": style}
    if corner is not None:
        fields["cornerRadius"] = corner
    return Obj("Rectangle", fields)


def _label(x: float, y: float, text: Value, style: Obj) -> Obj:
    return Obj("Label", {"x": x, "y": y, "text": text, "style": style})


def _polyline(points: list[tuple[float, float]], style: Obj) -> Obj:
    pts = Arr([Obj(None, {"x": px, "y": py}) for px, py in points])
    return Obj("Polyline", {"points": pts, "style": style})


def _polygon(points: list[tuple[float, float]], style: Obj) -> Obj:
    pts = Arr([Obj(None, {"x": px, "y": py}) for px, py in points])
    return Obj("Polygon", {"points": pts, "style": style})


def _circle(cx: float, cy: float, r: float, style: Obj) -> Obj:
    return Obj("Circle", {"cx": cx, "cy": cy, "r": r, "style": style})


def _curve(commands: list[Obj], style: Obj) -> Obj:
    return Obj("Curve", {"commands": Arr(list(commands)), "style": style})


def _pt(x: float, y: float) -> Obj:
    return Obj(None, {"x": x, "y": y})


# ── The chart spec (the neutral cross-host lowering input) ────────────────────


@dataclass(frozen=True)
class ChartSpec:
    """The resolved chart layout inputs — the neutral lowering contract.

    Mirrors the F# ``ChartSpec`` fields the lowering reads: ``kind`` (``Bar``
    grouped + stacked, ``Line``, ``Area`` overlaid + stacked, ``Scatter``,
    ``Pie`` are lowered; ``Heatmap`` produces an empty drawing), the ``x_field``
    category (Scatter: numeric) column, the ``y_fields`` series columns, an
    optional literal ``title``, and ``stacked`` (Bar/Area geometry only).
    """

    kind: str
    x_field: str
    y_fields: tuple[str, ...]
    title: str | None = None
    stacked: bool = field(default=False)
    # Phase 876 — the VALUE axis's number format, reusing the existing
    # ``Format`` vocabulary, carried as its canonical wire mapping
    # (``{"$type": "Currency", "isoCode": "GBP"}``). A WIRE field: a semantic
    # declaration, not an appearance.
    value_format: Mapping[str, object] | None = None
    # Phase 876 — the axis-unit mode + gate. NOT wire fields: the chart style is
    # a lowering parameter, so a display-unit convention is the host's choice.
    axis_unit_mode: str = "Words"
    display_unit_min_exponent: int = DISPLAY_UNIT_MIN_EXPONENT


LOWERED_KINDS = frozenset({"Bar", "Line", "Area", "Scatter", "Pie"})
"""The ``ChartKind``s this module lowers to a real ``Drawing`` (the render
dispatch consults THIS, so the first-party render branch and the lowering's
arm set can never drift apart). ``Heatmap`` stays a placeholder."""


# ── Row field extraction ─────────────────────────────────────────────────────


def _row_get(row: Mapping[str, object], field_name: str) -> object:
    return row.get(field_name)


def _numeric_of(row: Mapping[str, object], field_name: str) -> float:
    v = _row_get(row, field_name)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        f = float(v)
        # Non-finite guard (Phase 640): NaN/Infinity would poison every domain
        # computation and emit NaN geometry into the SVG. Wire-carried data can
        # never be non-finite (the canonical-float codec rejects it), so this
        # covers only host-side rows — coerced to the same 0.0 the non-numeric
        # posture uses, deterministically.
        if math.isnan(f) or math.isinf(f):
            return 0.0
        return f
    return 0.0


def _string_of(row: Mapping[str, object], field_name: str) -> str:
    v = _row_get(row, field_name)
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _format_num(float(v))
    if v is None:
        return ""
    return str(v)


def _capitalise(s: str) -> str:
    if len(s) == 0:
        return s
    return s[0].upper() + s[1:]


# ── The lowering ─────────────────────────────────────────────────────────────


def lower(spec: ChartSpec, rows: Sequence[Mapping[str, object]]) -> Obj:  # noqa: C901, PLR0912, PLR0915
    """Lower a resolved ``ChartSpec`` + data rows to a canonical ``Drawing`` kind.

    Returns the ``Drawing`` kind object (``$type = "Drawing"``); wrap it in a node
    with :func:`lower_node`. Lowered arms: ``Bar`` (grouped + stacked), ``Line``,
    ``Area`` (overlaid + stacked), ``Scatter`` (linear numeric x), ``Pie``
    (polar, single-series); ``Heatmap`` produces an empty drawing. ``stacked``
    on a kind where stacking is meaningless is ignored.
    """
    rows = list(rows)
    categories = [_string_of(r, spec.x_field) for r in rows]
    n = len(rows)

    series = [[_numeric_of(r, yf) for r in rows] for yf in spec.y_fields]
    m = len(series)

    # Stacking applies to Bar + Area only (Phase 637). Values stack as-is by
    # plain cumulative sum per category — deterministic and total; a negative
    # value simply lowers the running sum.
    stacked = spec.stacked and spec.kind in ("Bar", "Area")

    def cums_for(i: int) -> list[float]:
        """Per-category running sums across the series, INCLUDING the leading 0
        baseline: length m+1 (the F# ``List.scan (+) 0.0``)."""
        out = [0.0]
        acc = 0.0
        for j in range(m):
            acc = acc + series[j][i]
            out.append(acc)
        return out

    if stacked:
        all_values = [v for i in range(n) for v in cums_for(i)] or [0.0]
    else:
        all_values = [v for s in series for v in s] or [0.0]
    data_min = min(all_values)
    data_max = max(all_values)
    # Bars + lines share a zero-anchored domain — deterministic + honest for
    # bars. Stacked domains come from the cumulative partial sums, so the axis
    # covers the stack totals, never a single series' range.
    nice_lo, nice_hi, y_step, ticks = _nice_domain(min(0.0, data_min), max(0.0, data_max))

    # ── Value-axis number formatting (Phase 876) ──
    # The declared meaning (``spec.value_format``) chooses the arms; the style
    # chooses whether a large magnitude is stated once as a display unit; the
    # tick STEP chooses the precision. The unit is resolved from the PRINTED
    # magnitude, so a Percent axis is measured after its x100.
    value_format = spec.value_format
    y_divisor, y_tick_suffix, y_drop_symbol, y_unit_label = _resolve_display_unit(
        spec.axis_unit_mode,
        spec.display_unit_min_exponent,
        value_format,
        max(abs(nice_lo), abs(nice_hi)) * _format_value_scale(value_format),
    )

    def y_tick_text(v: float) -> str:
        return _format_value(value_format, y_divisor, y_drop_symbol, y_step, v) + y_tick_suffix

    # ── Linear x-scale (Phase 636 — the Scatter arm's numeric x axis) ──
    # Scatter reads the x-field NUMERICALLY and plots on a linear x-domain (the
    # first non-band x-scale arm). The domain is NOT zero-anchored — a scatter's
    # x range carries no baseline semantics (the y domain stays zero-anchored
    # with the other arms, deliberately: one shared y-domain rule).
    is_scatter = spec.kind == "Scatter"
    x_values = [_numeric_of(r, spec.x_field) for r in rows] if is_scatter else []
    if is_scatter:
        if x_values:
            x_nice_lo, x_nice_hi, x_step, x_ticks = _nice_domain(min(x_values), max(x_values))
        else:
            x_nice_lo, x_nice_hi, x_step, x_ticks = _nice_domain(0.0, 1.0)
    else:
        x_nice_lo, x_nice_hi, x_step, x_ticks = 0.0, 1.0, 1.0, []

    # The Scatter arm's x IS a value axis, so its ticks take the same canonical
    # formatter (Phase 876). ``value_format`` is deliberately NOT applied to it:
    # one declared meaning cannot be true of two different measures, and there
    # is no second axis-unit slot to state an x display unit in.
    def x_tick_text(v: float) -> str:
        return _format_value(None, 1.0, False, x_step, v)

    tick_size = _TICK_FONT_SIZE
    title_size = 18.0

    # ── Text-metric layout (Phase 879) ───────────────────────────────────────
    #
    # ORDER IS LOAD-BEARING. The plot rectangle used to be four module
    # constants; it is now DERIVED from the text the chart prints — the widest
    # formatted y tick decides the left margin, and the category labels' tilt
    # decides the bottom one. So: the left margin, the band pitch that follows
    # from it, the tilt, and the bottom margin the tilt needs, in that order.

    line_height = _text_line_height(tick_size, _TEXT_LINE_HEIGHT_FACTOR)

    def widest_of(texts: Sequence[str]) -> float:
        acc = 0.0
        for t in texts:
            acc = max(acc, _text_width(tick_size, t))
        return acc

    # ── Left margin ──
    # The truncation budget is derived from the CEILING — a constant — so the
    # truncation that feeds the margin never depends on the margin it decides.
    left_ceiling = _MARGIN_LEFT_MAX_SHARE * _W
    tick_text_budget = max(0.0, left_ceiling - _TICK_LABEL_GAP - _AXIS_LABEL_PADDING)

    def y_tick_label_text(v: float) -> str:
        return _truncate_to_width(tick_size, tick_text_budget, y_tick_text(v))

    required_left = _TICK_LABEL_GAP + widest_of([y_tick_label_text(t) for t in ticks]) + _AXIS_LABEL_PADDING
    margin_left = _r2(max(_MARGIN_LEFT, min(left_ceiling, required_left)))

    plot_x0 = margin_left
    plot_x1 = _W - _MARGIN_RIGHT
    plot_w = plot_x1 - plot_x0

    band_w = plot_w / float(n) if n > 0 else plot_w

    def centre_x(i: int) -> float:
        return _r2(plot_x0 + band_w * (float(i) + 0.5))

    # ── Category-label tilt + its vertical escalation ──
    # Only the BAND arms label categories: Scatter labels numeric x ticks (short
    # by construction, left horizontal) and Pie has no x axis. Both must
    # therefore contribute NO drop, or their bottom margin — and with it the
    # pie's centre — would move for a decision they never take.
    draws_category_labels = not is_scatter and spec.kind != "Pie"

    # A rotated label's footprint ALONG the axis is w·cos θ + h·sin θ. Escalate
    # when the widest label's footprint at the tilt no longer fits the band
    # pitch. At 90° the width term vanishes, so the vertical arm packs one label
    # per line height at any count — which is why it is terminal.
    def along_axis_footprint(deg: float, w: float) -> float:
        return w * math.cos(math.radians(deg)) + line_height * math.sin(math.radians(deg))

    if not draws_category_labels or n == 0 or _LABEL_TILT_DEGREES <= 0.0:
        # A zero tilt is a host opting out; honour it literally rather than
        # escalating it to vertical.
        tilt_degrees = 0.0
    elif along_axis_footprint(_LABEL_TILT_DEGREES, widest_of(categories)) > band_w:
        tilt_degrees = _VERTICAL_TILT_DEGREES
    else:
        tilt_degrees = _LABEL_TILT_DEGREES

    # ── Bottom margin ──
    # Below the plot, top to bottom: the label offset, the tilted label's drop
    # (w·sin θ), the padding, the x-axis title's own LINE (its offset measures
    # to its BASELINE, so the glyphs above it need reserving separately), and
    # that offset. Same ceiling-then-truncate posture as the left margin.
    sin_tilt = math.sin(math.radians(tilt_degrees))
    bottom_ceiling = _MARGIN_BOTTOM_MAX_SHARE * _H
    drop_ceiling = max(
        0.0,
        bottom_ceiling - _CATEGORY_LABEL_OFFSET_Y - _AXIS_LABEL_PADDING - line_height - _AXIS_TITLE_BOTTOM_OFFSET,
    )
    category_text_budget = drop_ceiling / sin_tilt if sin_tilt > 0.0 else math.inf
    category_texts = (
        [_truncate_to_width(tick_size, category_text_budget, c) for c in categories] if draws_category_labels else []
    )
    required_bottom = (
        _CATEGORY_LABEL_OFFSET_Y
        + sin_tilt * widest_of(category_texts)
        + _AXIS_LABEL_PADDING
        + line_height
        + _AXIS_TITLE_BOTTOM_OFFSET
    )
    margin_bottom = _r2(max(_MARGIN_BOTTOM, min(bottom_ceiling, required_bottom)))

    plot_y0 = _MARGIN_TOP
    plot_y1 = _H - margin_bottom
    plot_h = plot_y1 - plot_y0

    def y_scale(v: float) -> float:
        return _r2(plot_y1 - (v - nice_lo) / (nice_hi - nice_lo) * plot_h)

    def x_scale(v: float) -> float:
        return _r2(plot_x0 + (v - x_nice_lo) / (x_nice_hi - x_nice_lo) * plot_w)

    # ── Cartesian chrome (painter's order pieces) ──
    grid_style = _style_stroke_ink(_GRID_OPACITY, 1.0)
    axis_stroke_style = _style_stroke_ink(_AXIS_OPACITY, 1.0)
    gridlines: list[Value] = [_line(_r2(plot_x0), y_scale(t), _r2(plot_x1), y_scale(t), grid_style) for t in ticks]

    # Vertical gridlines — the Scatter arm only. A linear x-scale has readable
    # x positions, so a reader traces a point back to an x value the same way
    # the horizontal grid lets them trace a y value. A BAND x-axis has no such
    # positions to trace (a category is a label, not a magnitude), so a
    # vertical rule there would be decoration.
    x_gridlines: list[Value] = (
        [_line(x_scale(t), _r2(plot_y0), x_scale(t), _r2(plot_y1), grid_style) for t in x_ticks] if is_scatter else []
    )

    # Zero baseline — only when the domain CROSSES zero, where the sign of a
    # value is a reading of the chart and the zero line is what the reader
    # measures against. Drawn at axis strength, over the ordinary gridline it
    # shares a y with; when the domain does not cross zero the axis spine
    # already IS the baseline and a second rule at the same strength would be
    # noise.
    zero_line: list[Value] = (
        [_line(_r2(plot_x0), y_scale(0.0), _r2(plot_x1), y_scale(0.0), axis_stroke_style)]
        if nice_lo < 0.0 < nice_hi
        else []
    )

    axes: list[Value] = [
        _line(_r2(plot_x0), _r2(plot_y0), _r2(plot_x0), _r2(plot_y1), axis_stroke_style),
        _line(_r2(plot_x0), _r2(plot_y1), _r2(plot_x1), _r2(plot_y1), axis_stroke_style),
    ]

    # Outside tick marks — outside the plot on both axes, so the plot area
    # stays ink-free and the marks tie each label to its position. y marks
    # come first, then x marks. Suppressed entirely when `_TICK_MARK_LENGTH`
    # is not positive.
    if _TICK_MARK_LENGTH <= 0.0:
        tick_marks: list[Value] = []
    else:
        y_marks: list[Value] = [
            _line(_r2(plot_x0 - _TICK_MARK_LENGTH), y_scale(t), _r2(plot_x0), y_scale(t), axis_stroke_style)
            for t in ticks
        ]

        def _x_mark(x: float) -> Value:
            return _line(x, _r2(plot_y1), x, _r2(plot_y1 + _TICK_MARK_LENGTH), axis_stroke_style)

        x_marks: list[Value] = (
            [_x_mark(x_scale(t)) for t in x_ticks] if is_scatter else [_x_mark(centre_x(i)) for i in range(n)]
        )
        tick_marks = y_marks + x_marks

    # y-axis tick labels — right-anchored (End) in the left margin. The text is
    # the margin-bounded one (Phase 879): whatever the margin was sized for is
    # exactly what gets drawn.
    y_tick_labels: list[Value] = [
        _label(
            _r2(plot_x0 - _TICK_LABEL_GAP),
            _r2(y_scale(t) + 4.0),
            _literal(y_tick_label_text(t)),
            _text_style(_LABEL_OPACITY, "End", tick_size, "Normal"),
        )
        for t in ticks
    ]

    # x-axis labels — band arms label each category under its band centre;
    # Scatter labels its numeric x-ticks along the linear axis (Phase 636).
    #
    # A tilted category label is End-anchored at the band centre and rotated
    # NEGATIVELY (counter-clockwise, against ``rotation``'s clockwise
    # convention): the anchor is the pivot, so the text ENDS under the band's
    # tick and runs back down-and-left, reading up-to-the-right into it. The
    # opposite sign would swing the same text up into the plot area. At 90° this
    # degenerates to reading bottom-up. Scatter's numeric ticks stay horizontal
    # + Middle — short by construction, and centred on their value.
    if is_scatter:
        x_labels: list[Value] = [
            _label(
                x_scale(t),
                _r2(plot_y1 + _CATEGORY_LABEL_OFFSET_Y),
                _literal(x_tick_text(t)),
                _text_style(_LABEL_OPACITY, "Middle", tick_size, "Normal"),
            )
            for t in x_ticks
        ]
    else:
        x_labels = [
            _label(
                centre_x(i),
                _r2(plot_y1 + _CATEGORY_LABEL_OFFSET_Y),
                _literal(c),
                _text_style(_LABEL_OPACITY, "End", tick_size, "Normal", _r2(-tilt_degrees))
                if tilt_degrees > 0.0
                else _text_style(_LABEL_OPACITY, "Middle", tick_size, "Normal"),
            )
            for i, c in enumerate(category_texts)
        ]

    # ── Axis titles (a name on both axes) ──
    axis_titles: list[Value] = [
        _label(
            _r2((plot_x0 + plot_x1) / 2.0),
            _r2(_H - _AXIS_TITLE_BOTTOM_OFFSET),
            _literal(_capitalise(spec.x_field)),
            _text_style(None, "Middle", tick_size, "Normal"),
        ),
        _label(
            _r2(8.0),
            _r2(plot_y0 - 12.0),
            # The top-left slot states the value axis's DISPLAY UNIT once when
            # scaling applies, and otherwise keeps the horizontal "Value" hint.
            _literal("Value" if y_unit_label == "" else y_unit_label),
            _text_style(None, "Start", tick_size, "Normal"),
        ),
    ]

    # ── Series geometry ──
    series_shapes: list[Value] = []
    if spec.kind in ("Bar", "Column") and stacked:
        # One capped bar per category, centred in its band; series stack as
        # segments between consecutive cumulative sums (Phase 637), each
        # shortened by `_STACK_SEGMENT_GAP` on the side facing the next
        # segment so the boundaries read as gaps rather than colour changes.
        group_w = band_w * 0.7
        bw = _r2(min(group_w * 0.9, _BAR_MAX_THICKNESS))
        for i in range(n):
            bx = _r2(plot_x0 + band_w * float(i) + (band_w - bw) / 2.0)
            cums = cums_for(i)
            for j in range(m):
                y0 = y_scale(cums[j])
                y1 = y_scale(cums[j + 1])
                # The gap comes off the far side from the baseline, and only
                # where another segment follows — so the stack's outer tip
                # keeps its full height and the total stays honest. `max 0`
                # covers a segment thinner than the gap.
                gap = _STACK_SEGMENT_GAP if j < m - 1 else 0.0
                top = _r2(min(y0, y1) + (gap if y1 < y0 else 0.0))
                hgt = _r2(max(0.0, abs(y1 - y0) - gap))
                mark = f"{spec.y_fields[j]}|{categories[i]}"
                series_shapes.append(_rectangle(bx, top, bw, hgt, None, _with_mark(_style_fill(_colour_for(j)), mark)))
    elif spec.kind in ("Bar", "Column"):
        group_w = band_w * 0.7
        sub_w = group_w / float(m) if m > 0 else group_w
        bw = _r2(min(sub_w * 0.9, _BAR_MAX_THICKNESS))
        base_y = y_scale(0.0)
        for j in range(m):
            colour = _colour_for(j)
            values = series[j]
            for i in range(n):
                v = values[i]
                # Centre the (possibly capped) bar in its own sub-slot, so a
                # cap takes air off BOTH sides and the group stays symmetric
                # about the band centre.
                slot_x = plot_x0 + band_w * float(i) + (band_w - group_w) / 2.0 + float(j) * sub_w
                bx = _r2(slot_x + (sub_w - bw) / 2.0)
                vy = y_scale(v)
                top = min(vy, base_y)
                hgt = _r2(abs(vy - base_y))
                mark = f"{spec.y_fields[j]}|{categories[i]}"
                series_shapes.append(_rectangle(bx, top, bw, hgt, None, _with_mark(_style_fill(colour), mark)))
    elif spec.kind == "Area" and stacked and n > 0:
        # Cumulative bands, bottom band first (painter's order): band j fills
        # between boundary j (below) and boundary j+1 (above); its upper
        # boundary carries the full-strength series edge (Phase 637).
        cum_rows = [cums_for(i) for i in range(n)]
        for j in range(m):
            colour = _colour_for(j)
            yf = spec.y_fields[j]
            upper = [(centre_x(i), y_scale(cum_rows[i][j + 1])) for i in range(n)]
            lower_pts = [(centre_x(i), y_scale(cum_rows[i][j])) for i in range(n - 1, -1, -1)]
            series_shapes.append(
                _polygon(upper + lower_pts, _with_mark(_style_fill_opacity(colour, _AREA_FILL_OPACITY), yf))
            )
            series_shapes.append(_polyline(upper, _with_mark(_style_stroke(colour, 2.0), yf)))
    elif spec.kind == "Area" and n > 0:
        # Overlaid baseline-closed bands in palette order (painter's order:
        # later series draw over earlier); the translucent fill keeps the
        # overlap legible, the Polyline edge keeps each series distinct.
        base_y = y_scale(0.0)
        for j in range(m):
            colour = _colour_for(j)
            values = series[j]
            yf = spec.y_fields[j]
            points = [(centre_x(i), y_scale(values[i])) for i in range(n)]
            band = [(centre_x(0), base_y), *points, (centre_x(n - 1), base_y)]
            series_shapes.append(_polygon(band, _with_mark(_style_fill_opacity(colour, _AREA_FILL_OPACITY), yf)))
            series_shapes.append(_polyline(points, _with_mark(_style_stroke(colour, 2.0), yf)))
    elif spec.kind == "Line":
        for j in range(m):
            colour = _colour_for(j)
            values = series[j]
            points = [(centre_x(i), y_scale(values[i])) for i in range(n)]
            series_shapes.append(_polyline(points, _with_mark(_style_stroke(colour, 2.0), spec.y_fields[j])))
    elif spec.kind == "Scatter":
        # Fixed-radius point marks per datum (Phase 636). A non-numeric x/y
        # cell reads 0.0 (`_numeric_of`'s posture, shared with the other arms)
        # — grounded validation makes that loud upstream, not here.
        for j in range(m):
            colour = _colour_for(j)
            values = series[j]
            yf = spec.y_fields[j]
            for i in range(n):
                series_shapes.append(
                    _circle(
                        x_scale(x_values[i]),
                        y_scale(values[i]),
                        4.0,
                        _with_mark(_style_fill(colour), f"{yf}|{_format_num(x_values[i])}"),
                    )
                )

    # ── Legend (only when >1 series) — a swatch + series name per series ──
    #
    # The pitch is PER ENTRY since Phase 879: an entry occupies its
    # swatch-to-label offset, its own measured name width, and the inter-entry
    # gap, so entries lay out cumulatively rather than on a fixed stride. A long
    # series name now pushes its neighbour along instead of being overwritten by
    # it. Legend POSITION and OVERFLOW are deliberately unchanged — they are one
    # problem and land together in a later phase.
    legend: list[Value] = []
    if m > 1:
        lx_acc = plot_x0
        for j in range(m):
            colour = _colour_for(j)
            name = spec.y_fields[j]
            # The label offsets from the ROUNDED swatch x, exactly as the
            # reference does — rounding the sum instead can differ in the last
            # 2 dp.
            sx = _r2(lx_acc)
            legend.append(_rectangle(sx, 34.0, 10.0, 10.0, 2.0, _style_fill(colour)))
            legend.append(
                _label(
                    _r2(sx + _LEGEND_LABEL_OFFSET_X),
                    43.0,
                    _literal(name),
                    _text_style(_LABEL_OPACITY, "Start", tick_size, "Normal"),
                )
            )
            lx_acc += _LEGEND_LABEL_OFFSET_X + _text_width(tick_size, name) + _LEGEND_ENTRY_GAP

    # ── Visible title (a Label — bigger + emphasised) ──
    title_shapes: list[Value] = []
    if spec.title is not None:
        title_shapes.append(
            _label(_r2(plot_x0), 22.0, _literal(spec.title), _text_style(None, "Start", title_size, "Loud"))
        )

    # Pie is polar — no axes/gridlines/tick chrome; every other arm assembles
    # the shared cartesian chrome in painter's order: gridlines (h then v),
    # the zero baseline, axes, tick marks, y-tick + x labels, axis titles,
    # series, legend, chart title.
    if spec.kind == "Pie":
        shapes: list[Value] = (
            _pie_shapes(spec, series, categories, n, m, plot_x0, plot_x1, plot_y0, plot_y1) + title_shapes
        )
    else:
        shapes = (
            gridlines
            + x_gridlines
            + zero_line
            + axes
            + tick_marks
            + y_tick_labels
            + x_labels
            + axis_titles
            + series_shapes
            + legend
            + title_shapes
        )

    kind_fields: dict[str, Value] = {
        "viewBox": Obj(None, {"minX": 0.0, "minY": 0.0, "width": _W, "height": _H}),
        "shapes": Arr(shapes),
        "style": Obj(None, {}),
    }
    if spec.title is not None:
        kind_fields["title"] = _literal(spec.title)
    return Obj("Drawing", kind_fields)


def _pie_shapes(  # noqa: PLR0914
    spec: ChartSpec,
    series: list[list[float]],
    categories: list[str],
    n: int,
    m: int,
    plot_x0: float,
    plot_x1: float,
    plot_y0: float,
    plot_y1: float,
) -> list[Value]:
    """The Pie arm (Phase 638) — polar, cubic-approximated wedges.

    Bounded v1: exactly ONE series (multi-series pie is a grounded-validation
    refusal upstream, never a silent first-series truncation) and non-negative
    values (any negative refuses the geometry). Zero-value categories draw no
    wedge but keep their legend row. Wedges start at 12 o'clock and sweep
    clockwise; arcs are the standard <=90-degree-segment cubic-Bezier
    approximation (the closed `CurveCommand` vocabulary has no arc case,
    deliberately). A lone 100% category degenerates to a `Circle`. Category
    share reads in the legend ("name (NN%)")."""
    tick_size = _TICK_FONT_SIZE
    values = series[0] if m == 1 else []
    refused = m != 1 or any(v < 0.0 for v in values)
    total = sum(values)
    if refused or total <= 0.0:
        return []

    cx = _r2((plot_x0 + plot_x1) / 2.0)
    cy = _r2((plot_y0 + plot_y1) / 2.0)
    radius = 130.0

    def pt(a: float) -> Obj:
        return _pt(_r2(cx + radius * math.cos(a)), _r2(cy + radius * math.sin(a)))

    def arc_cubics(a0: float, a1: float) -> list[Obj]:
        segments = max(1, int(math.ceil((a1 - a0) / (math.pi / 2.0) - 1e-9)))
        out: list[Obj] = []
        for s in range(segments):
            t0 = a0 + (a1 - a0) * float(s) / float(segments)
            t1 = a0 + (a1 - a0) * float(s + 1) / float(segments)
            k = 4.0 / 3.0 * math.tan((t1 - t0) / 4.0)
            c1x = _r2(cx + radius * (math.cos(t0) - k * math.sin(t0)))
            c1y = _r2(cy + radius * (math.sin(t0) + k * math.cos(t0)))
            c2x = _r2(cx + radius * (math.cos(t1) + k * math.sin(t1)))
            c2y = _r2(cy + radius * (math.sin(t1) - k * math.cos(t1)))
            c1 = _pt(c1x, c1y)
            c2 = _pt(c2x, c2y)
            out.append(Obj("CubicTo", {"control1": c1, "control2": c2, "to": pt(t1)}))
        return out

    fractions = [v / total for v in values]
    starts = [0.0]
    acc = 0.0
    for f in fractions:
        acc = acc + f
        starts.append(acc)
    top = -math.pi / 2.0

    # Half the angular padding comes off each end of every wedge, so the
    # separation is a sliver of absent ink — no surface colour is needed and
    # the result is theme-invariant, which a stroked wedge border could not be.
    half_gap = _WEDGE_GAP_DEGREES * math.pi / 360.0

    yf = spec.y_fields[0]
    segs: list[Value] = []
    for i in range(n):
        f = fractions[i]
        if f > 0.0:
            colour = _colour_for(i)
            mark_style = _with_mark(_style_fill(colour), f"{yf}|{categories[i]}")
            if f >= 1.0 - 1e-9:
                # A lone 100% category is a circle — there is no neighbour to
                # separate from, so no padding.
                segs.append(_circle(cx, cy, radius, mark_style))
            else:
                a0 = top + 2.0 * math.pi * starts[i] + half_gap
                a1 = top + 2.0 * math.pi * starts[i + 1] - half_gap
                # A wedge narrower than the padding is DROPPED rather than
                # drawn inverted — the alternative is a sliver sweeping the
                # wrong way round the circle, which is a wrong picture, not a
                # small one.
                if a1 > a0:
                    cmds = [
                        Obj("MoveTo", {"to": _pt(cx, cy)}),
                        Obj("LineTo", {"to": pt(a0)}),
                        *arc_cubics(a0, a1),
                        Obj("Close", {}),
                    ]
                    segs.append(_curve(cmds, mark_style))

    # Vertical category legend on the right — categories take the palette
    # roles a cartesian chart gives its series.
    pie_legend: list[Value] = []
    for i in range(n):
        ly = 70.0 + 20.0 * float(i)
        pie_legend.append(_rectangle(_r2(_W - 168.0), _r2(ly), 10.0, 10.0, 2.0, _style_fill(_colour_for(i))))
        # Routed through the canonical formatter (Phase 876) — one rounding +
        # rendering rule for every number this module prints. A share is a whole
        # percent here, so the shipped ``NN%`` shape is unchanged.
        pct = _format_value(None, 1.0, False, 1.0, fractions[i] * 100.0)
        pie_legend.append(
            _label(
                _r2(_W - 153.0),
                _r2(ly + 9.0),
                _literal(f"{categories[i]} ({pct}%)"),
                _text_style(_LABEL_OPACITY, "Start", tick_size, "Normal"),
            )
        )

    return segs + pie_legend


def lower_node(node_id: str, spec: ChartSpec, rows: Sequence[Mapping[str, object]]) -> Node:
    """Lower + wrap the ``Drawing`` kind in a node envelope (id + kind)."""
    return Node(id=node_id, kind=lower(spec, rows))
