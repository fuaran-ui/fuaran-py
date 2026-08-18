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
from collections.abc import Callable, Mapping, Sequence
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
# The MAGNITUDE of the MIDDLE RUNG of the category-label angle ladder. The ladder
# is fit-driven and UNIFORM per axis: flat while every label fits its band, all at
# this angle when any does not, all vertical when this angle no longer packs
# either. (Phase 879 read the tilt as the resting state; Phase 903's correction
# makes it the middle rung.) A zero angle opts out of rotation entirely — flat at
# every label length, never escalated instead. The vertical rung takes one line
# height per label whatever its length, so it packs at any category count.
_LABEL_TILT_DEGREES = 30.0
_VERTICAL_TILT_DEGREES = 90.0
# Phase 878 — the subtitle + the rotated y-axis title.
# The subtitle sits deliberately BELOW the 18.0 title size: it is a qualifier on
# the title, and a qualifier set at the same size competes with what it
# qualifies. Its baseline sits directly under the title's, sharing its x + anchor
# so the pair reads as one block.
_SUBTITLE_FONT_SIZE = 13.0
_SUBTITLE_BASELINE_Y = 38.0
# x of the ROTATED y-axis title's baseline, measured from the canvas LEFT EDGE —
# not from the autosized margin, so the title does not slide about as tick widths
# change. A rotated-by `-_Y_AXIS_TITLE_DEGREES` label's ascenders extend LEFT of
# its baseline, which is why this sits near the outer edge of the reserved band
# rather than at it.
_Y_AXIS_TITLE_OFFSET_X = 18.0
# The MAGNITUDE of the y-axis title's rotation. Emitted as `rotation =
# -_Y_AXIS_TITLE_DEGREES`: `rotation` is clockwise (SVG's convention), so the
# negative angle reads BOTTOM-UP — the conventional treatment, and the same sign
# convention `_VERTICAL_TILT_DEGREES` already uses.
_Y_AXIS_TITLE_DEGREES = 90.0
# Legend geometry (Phase 880 — ONE legend, four placements).
#
# Both shapes live here because both are reachable from any arm: a horizontal
# BAND (the ``Top`` / ``Bottom`` arms — Phase 879's per-entry pitch) and a
# vertical COLUMN (``Right``, the default — one row per entry, the plot
# shrinking by the column's width). The pie arm draws through exactly these
# constants too since Phase 880; its own pie-legend literals are retired into
# them at their own values, so no pie geometry was restyled by the unification.
_LEGEND_LABEL_OFFSET_X = 15.0
# BAND arms only. Horizontal padding after an entry's label, before the next
# entry's swatch. The pitch is PER ENTRY since Phase 879 (swatch offset + the
# entry's own measured name width + this gap), never a fixed stride.
_LEGEND_ENTRY_GAP = 24.0
# BAND arms only. Top y of a legend swatch in the TOP band, measured from the
# canvas top; the ``Bottom`` band mirrors from the canvas bottom via
# ``_LEGEND_LABEL_BASELINE_DY``, so it needs no second constant.
_LEGEND_SWATCH_Y = 34.0
_LEGEND_SWATCH_SIZE = 10.0
_LEGEND_SWATCH_CORNER_RADIUS = 2.0
# BAND arms only. Baseline y of a legend label in the TOP band.
_LEGEND_LABEL_BASELINE_Y = 43.0
# COLUMN arms only. Vertical pitch between legend rows.
_LEGEND_ROW_PITCH_Y = 20.0
# COLUMN arms (and the ``Bottom`` band). Baseline nudge from a legend row's TOP
# to its label's baseline — the relation that lets a row be placed by its top
# edge and still read as one line.
_LEGEND_LABEL_BASELINE_DY = 9.0
# COLUMN arms only. Gap between the plot's edge and the legend column's
# swatches. The column's own trailing clearance to the canvas edge is
# ``_MARGIN_RIGHT``, which is what it always was.
_LEGEND_COLUMN_GAP = 16.0
# Ceiling on the legend column's width, as a share of the canvas width. Same
# posture as the margin autosizes: a pathological series name is truncated with
# the deterministic ellipsis rather than allowed to eat the plot.
_LEGEND_COLUMN_MAX_SHARE = 0.3

LEGEND_POSITIONS = ("Top", "Right", "Bottom", "None")
"""Which edge the legend occupies — or ``None``, which suppresses it. A WIRE
vocabulary (``ChartSpec.legend_position``); the geometry that realises it is the
host's, above."""

_LEGEND_POSITION_DEFAULT = "Right"
"""The host default an absent ``ChartSpec.legend_position`` resolves to (Phase
880). A VERTICAL COLUMN, and that is a structural choice rather than a taste:
a band's width is the SUM of its entries, so it runs off a 640 px canvas once
the names are long enough or numerous enough — silently, with no refusal and no
ellipsis. A column's width is the MAX of its entries, bounded by
``_LEGEND_COLUMN_MAX_SHARE`` and truncated at it, and its height is one
``_LEGEND_ROW_PITCH_Y`` per entry into 400 px of canvas. Neither term grows
without limit, so the eight-slot palette legends itself by construction."""

DATA_LABEL_MODES = ("Off", "Ends")
"""Whether a chart writes its values onto the picture, and where (Phase 881). A
WIRE vocabulary (``ChartSpec.data_labels``); the type size, the offsets and the
fit rule that realise it are the host's, below.

THE SET IS TWO, AND THAT IS THE POINT. There is deliberately no all-points
value: a number on every interior point is the clutter this vocabulary exists to
avoid, so no shape of the API can request one. ``"Ends"`` names the selective
placements that read — a bar's cap, a line's last point — and the set is closed
there."""

_DATA_LABEL_MODE_DEFAULT = "Off"
"""What an absent ``ChartSpec.data_labels`` resolves to — and the shipped
default. The one place this differs from ``legend_position``, deliberately: a
legend is chrome an author opts OUT of, where a data label is ink an author opts
IN to. So an absent field lowers to the pre-881 picture byte-for-byte."""

# Phase 881 — the data-label geometry. NONE of these feeds a margin: a data
# label never makes the plot smaller, it either fits the room the picture
# already has or it is suppressed. That is what keeps ``Off`` byte-identical to
# the pre-881 layout rather than merely visually similar.
#
# The font size is one point BELOW the tick size, and a constant of its own: a
# tick sits OUTSIDE the plot in a column, where a data label sits INSIDE it
# competing with the mark it describes.
_DATA_LABEL_FONT_SIZE = 12.0
# Clearance between a bar's cap and the nearest ink of its label, in BOTH
# directions — one constant used twice, so the two placements are mirrors.
_DATA_LABEL_OFFSET_Y = 5.0
# Clearance a label keeps from the plot edge, and half the clearance it keeps
# from its neighbour's. Feeds the fit gate only.
_DATA_LABEL_PADDING = 2.0
# Gap from a line/area endpoint to the left edge of its label.
_DATA_LABEL_END_OFFSET_X = 6.0
# Rise from a line/area endpoint to its label's baseline — the nudge that takes
# the text off the line it belongs to.
_DATA_LABEL_END_NUDGE_Y = 5.0

X_SCALES = ("Category", "Temporal")
"""What a chart's x column MEANS (Phase 882) — discrete ``Category`` bands or
``Temporal`` dates on a continuous day-scale. A WIRE vocabulary
(``ChartSpec.x_scale``) that is DECLARED, never inferred, so the pre-emit
validator can ground it against the column type (FUARAN097) instead of the
lowering guessing from cell strings."""

_X_SCALE_DEFAULT = "Category"
"""What an absent ``ChartSpec.x_scale`` resolves to — and the shipped default, so
every pre-882 chart lowers byte-for-byte unchanged."""

_TARGET_TICK_COUNT = 5.0
"""The value axis's tick target (``_nice_domain``). The temporal ladder's ceiling
is this PLUS ONE (rule 3 below): a continuous step can be tuned to hit a target,
a calendar rung jumps by 2–3× and cannot, so rounding a rung down loses roughly
half the ticks."""


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
    target_ticks = _TARGET_TICK_COUNT
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
#   5. THE INTEGER PART IS RENDERED IN POSITIONAL NOTATION AT EVERY MAGNITUDE,
#      by an expansion this module owns — never by inheriting a host's default
#      float→string switch. Grouping walks decimal digits, so handing it an
#      exponent form corrupts it silently (``_group_thousands("1E+17")`` is
#      ``"1E,+17"``), and the hosts do not agree on WHEN that form appears: the
#      .NET ``"R"`` layout that ``format_finite_double`` mirrors (and that the
#      wire format pins) goes scientific once the leading-digit exponent passes
#      16, i.e. at 1e17, while JavaScript's ``Number.prototype.toString`` stays
#      positional until 1e21. So above 1e17 four hosts drew a grouped exponent
#      and one drew correct digits: the same chart, different bytes.
#      ``_expand_to_fixed`` re-lays any ``d[.ddd]E±NN`` mantissa/exponent pair
#      (JavaScript's lower-case ``e+NN`` included) as its digits zero-padded to
#      ``exp + 1`` places, and leaves an already-positional form untouched — so
#      every host groups the same digit string and nothing below 1e17 moves.
#      NOTE the threshold is 1e17, not the 1e15 in ``_format_num`` — that
#      constant bounds the exact integer fast path, not the notation switch.
#      The expansion is over the SHORTEST-ROUND-TRIP digits, the canonical
#      decimal identity of the float, not its exact binary value: 1e21 reads
#      ``1,000,000,000,000,000,000,000``, never
#      ``999,999,999,999,999,916,000``. Only the INTEGER part needs this — the
#      fraction is bounded by ``10**d <= 10**6`` by rule 1's cap.


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


def _expand_to_fixed(s: str) -> str:
    """Expand a canonical round-trip number form into POSITIONAL notation (rule 5).

    ``s`` is whatever the host's shortest-round-trip formatter produced for a
    non-negative INTEGER-valued float: positional at small magnitudes, and
    ``d[.ddd]E±NN`` — or JavaScript's lower-case ``e+NN`` — above whichever
    magnitude that host switches at. Total by construction: a form carrying no
    exponent is returned unchanged, as is the negative-exponent form an integer
    part cannot produce.
    """
    e_idx = s.find("E")
    if e_idx < 0:
        e_idx = s.find("e")
    if e_idx < 0:
        return s
    mant = s[:e_idx]
    try:
        exp = int(s[e_idx + 1 :])
    except ValueError:
        return s
    if exp < 0:
        return s
    dot = mant.find(".")
    digits = mant if dot < 0 else mant[:dot] + mant[dot + 1 :]
    # An integer-valued float's shortest round-trip always has at least as many
    # places as digits; the guard keeps the function total rather than
    # describing a reachable case.
    if len(digits) >= exp + 1:
        return digits
    return digits + "0" * (exp + 1 - len(digits))


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
    # Rule 5 — expand before grouping. ``_format_num`` alone would hand the
    # grouper an exponent form above the host's own switch magnitude.
    int_str = _group_thousands(_expand_to_fixed(_format_num(float(int_part))))
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


# ─── The temporal x-axis (Phase 882) ─────────────────────────────────────────
#
# NORMATIVE CROSS-HOST SPEC (R2), the same standing as the text metrics and the
# number formatter above: every conformant host reproduces this section exactly,
# and ``docs/CHARTS-DRAWING-PRIMITIVE-DESIGN.md`` §4h carries it as the
# language-neutral statement. The ``chart-lowering/*`` goldens pin it.
#
# FIVE RULES, and each one exists to remove a way two hosts could disagree.
#
#   1. THE UNIT IS THE DAY, and a date is an INTEGER: days since 1970-01-01 in
#      the PROLEPTIC GREGORIAN calendar. Nothing here reads a host date type, a
#      locale, a time zone, or a clock — the conversions are the fixed integer
#      algorithms below (Howard Hinnant's ``days_from_civil`` /
#      ``civil_from_days``, public domain), which are exact for every date they
#      admit and need no leap-year table. A timestamp cell's TIME-OF-DAY IS
#      DISCARDED: the value is its UTC date. That is the whole of the axis's
#      time-zone policy, and it is stated rather than inherited, because
#      inheriting it from a host would make the picture depend on where it was
#      drawn.
#
#      Integer division must TRUNCATE TOWARD ZERO (F#, Rust, Go and C all do;
#      JavaScript needs ``Math.trunc(a / b)``). PYTHON'S ``//`` FLOORS, which is
#      a DIFFERENT answer for the two negative-bias branches (``y - 399`` and
#      ``z - 146096``) — so every division in the two algorithms goes through
#      ``_trunc_div``. The algorithms bias their operands into the non-negative
#      range precisely so that truncation is the only convention they need.
#
#   2. THE DOMAIN IS THE DATA'S OWN EXTENT, UNEXPANDED — ``[min, max]``, so the
#      first and last points sit on the plot's edges. It is NOT snapped outward
#      to a tick boundary (the value axis's ``_nice_domain`` posture), because a
#      calendar boundary is a coarse thing to round to: nicing a 30-day domain
#      to whole months would add a month of empty plot at each end to make room
#      for ticks nobody asked for. The ticks come to the domain instead. A
#      degenerate domain (every row the same date, or no rows) becomes
#      ``[lo, lo+1]``, the same guard ``_nice_domain`` applies for the same
#      reason.
#
#   3. THE TICKS ARE CALENDAR-ALIGNED INSTANTS INSIDE THE DOMAIN, at a step
#      drawn from a FIXED LADDER — the ``{1,2,5}·10ⁿ`` rule's analogue for units
#      that are not decimal:
#
#        1, 2, 5, 10 DAYS · 1, 2, 3, 6 MONTHS · {1,2,5}·10ⁿ YEARS (n ≤ 6)
#
#      The chosen rung is the FIRST whose in-domain tick count fits the
#      ceiling; the coarsest rung is the fallback nothing else fits. Day rungs
#      step from the DOMAIN'S OWN START (a "nice" 2-day or 5-day boundary does
#      not exist — days are uniform, so the honest anchor is the first datum);
#      month rungs land on month starts where ``(month-1) % k == 0``, which
#      makes ``k = 3`` the calendar quarters and ``k = 6`` January and July;
#      year rungs land on the January 1 of years where ``year % k == 0``.
#
#      The ceiling is ``_TARGET_TICK_COUNT + 1`` (6 at the shipped default)
#      rather than the target itself — see that constant. Counts are computed
#      WITHOUT generating the ticks, so the ladder can be walked from its
#      densest rung on a millennium-wide domain without unbounded work.
#
#   4. THE FORMAT FOLLOWS THE STEP'S NOMINAL LENGTH, at the operator's
#      thresholds: ``> 365`` days ⇒ ``yyyy``, ``> 27`` ⇒ ``mmm yy``, else
#      ``dd mmm yy``. Nominal, not measured: a month is
#      ``365.2425 / 12 = 30.436875`` days and a year ``365.2425``, so the rung
#      decides the format and the DATA cannot. Measuring the actual tick gaps
#      instead would put the year rung's average at exactly 365.0 across a run
#      of non-leap years (1900–1903, say) and flip a decade chart from ``yyyy``
#      to ``mmm yy`` on a property of the calendar nobody was asking about. The
#      thresholds are calibrated for this: the 1-month rung clears 27 and the
#      6-month rung does not clear 365, so each threshold separates two
#      ADJACENT rungs.
#
#   5. THE MONTH NAMES ARE PART OF THE SPEC. English three-letter
#      abbreviations, invariant, never a locale lookup — an i18n date axis is a
#      different feature with its own vocabulary, and a chart whose golden bytes
#      changed with the host's culture would not be certifiable at all.

_MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
"""The English three-letter month abbreviations, in calendar order. INVARIANT —
part of the wire-visible spec (rule 5), never a locale lookup."""

TEMPORAL_UNITS = ("Days", "Months", "Years")
"""The calendar unit a tick step counts in."""


def _trunc_div(a: int, b: int) -> int:
    """Integer division TRUNCATING TOWARD ZERO — the convention rule 1 requires.

    Python's ``//`` floors, so ``-399 // 400`` is ``-1`` where the calendar
    algorithms need ``0``. Every division inside them goes through here, which is
    what makes this host's day numbers identical to the other four.
    """
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _trunc_mod(a: int, b: int) -> int:
    """The remainder that pairs with :func:`_trunc_div` — i.e. the sign of ``a``.

    Python's ``%`` takes the sign of the DIVISOR (``-1 % 12 == 11``) where the
    reference hosts' ``%`` takes the sign of the dividend (``-1``). Only the two
    call sites where the operand can be negative (a pre-epoch month index, a
    two-digit year) use this; a divisibility test (``x % k == 0``) is unaffected
    by the convention and is left as the plain operator.
    """
    return a - _trunc_div(a, b) * b


@dataclass(frozen=True)
class _TemporalStep:
    """One rung of the ladder: ``count`` of ``unit`` (rule 3)."""

    unit: str
    count: int


def _is_leap_year(y: int) -> bool:
    """Gregorian leap year (proleptic — the rule applies to every year the parser
    admits, with no historical exception)."""
    return (y % 4 == 0 and y % 100 != 0) or y % 400 == 0


def _days_in_month(y: int, m: int) -> int:
    """Days in a month — the one place the calendar's irregularity is written
    down, used by the PARSER only (the conversions below need no table)."""
    if m == 2:
        return 29 if _is_leap_year(y) else 28
    if m in (4, 6, 9, 11):
        return 30
    return 31


def _days_from_civil(year: int, month: int, day: int) -> int:
    """``(y, m, d)`` → days since 1970-01-01. Hinnant's ``days_from_civil``: exact
    for every proleptic-Gregorian date, no leap table, integer-only. Division
    truncates toward zero — the operands are biased so that is the only
    convention needed (rule 1)."""
    y = year - 1 if month <= 2 else year
    era = _trunc_div(y if y >= 0 else y - 399, 400)
    yoe = y - era * 400  # [0, 399]
    mp = month - 3 if month > 2 else month + 9  # March-based month
    doy = _trunc_div(153 * mp + 2, 5) + day - 1  # [0, 365]
    doe = yoe * 365 + _trunc_div(yoe, 4) - _trunc_div(yoe, 100) + doy  # [0, 146096]
    return era * 146097 + doe - 719468


def _civil_from_days(days: int) -> tuple[int, int, int]:
    """Days since 1970-01-01 → ``(y, m, d)``. Hinnant's ``civil_from_days``, the
    exact inverse of :func:`_days_from_civil`."""
    z = days + 719468
    era = _trunc_div(z if z >= 0 else z - 146096, 146097)
    doe = z - era * 146097  # [0, 146096]
    yoe = _trunc_div(doe - _trunc_div(doe, 1460) + _trunc_div(doe, 36524) - _trunc_div(doe, 146096), 365)  # [0, 399]
    y = yoe + era * 400
    doy = doe - (365 * yoe + _trunc_div(yoe, 4) - _trunc_div(yoe, 100))  # [0, 365]
    mp = _trunc_div(5 * doy + 2, 153)  # [0, 11], March-based
    d = doy - _trunc_div(153 * mp + 2, 5) + 1  # [1, 31]
    m = mp + 3 if mp < 10 else mp - 9  # [1, 12]
    return (y + 1 if m <= 2 else y), m, d


def _try_parse_day(text: str) -> int | None:
    """Parse a canonical ISO-8601 date to days since epoch — ``YYYY-MM-DD``,
    optionally followed by ``T…``, whose time-of-day is DISCARDED (rule 1).

    STRICT by shape and by calendar: four digits, two, two, both hyphens, a month
    in 1–12 and a day the month actually has. ``None`` for everything else,
    including a locale spelling ("15/01/2026") and a bare year — admitting either
    would be the string-sniffing this axis exists to avoid.
    """

    def digits(start: int, length: int) -> int | None:
        if start + length > len(text):
            return None
        acc = 0
        for k in range(start, start + length):
            c = text[k]
            if not ("0" <= c <= "9"):
                return None
            acc = acc * 10 + (ord(c) - ord("0"))
        return acc

    if len(text) < 10:
        return None
    if text[4] != "-" or text[7] != "-":
        return None
    if len(text) > 10 and text[10] != "T":
        return None
    y = digits(0, 4)
    m = digits(5, 2)
    d = digits(8, 2)
    if y is None or m is None or d is None:
        return None
    if not (1 <= m <= 12) or not (1 <= d <= _days_in_month(y, m)):
        return None
    return _days_from_civil(y, m, d)


def _day_of(text: str) -> int:
    """The day number a row's x cell carries, with an UNPARSEABLE cell reading as
    the epoch. That mirrors ``_numeric_of``'s posture for a non-numeric value-axis
    cell — the lowering stays total, and the grounding rule (FUARAN097) is what
    makes a non-date column loud, upstream, before any picture is drawn. Silence
    here is not the design; refusing here would be."""
    d = _try_parse_day(text)
    return 0 if d is None else d


def _nominal_days(step: _TemporalStep) -> float:
    """The step's NOMINAL length in days (rule 4) — a mean Gregorian month and
    year, so the FORMAT is a property of the rung rather than of the data."""
    if step.unit == "Days":
        return float(step.count)
    if step.unit == "Months":
        return float(step.count) * 30.436875  # 365.2425 / 12
    return float(step.count) * 365.2425


_TEMPORAL_LADDER: tuple[_TemporalStep, ...] = (
    _TemporalStep("Days", 1),
    _TemporalStep("Days", 2),
    _TemporalStep("Days", 5),
    _TemporalStep("Days", 10),
    _TemporalStep("Months", 1),
    _TemporalStep("Months", 2),
    _TemporalStep("Months", 3),
    _TemporalStep("Months", 6),
    _TemporalStep("Years", 1),
    _TemporalStep("Years", 2),
    _TemporalStep("Years", 5),
    _TemporalStep("Years", 10),
    _TemporalStep("Years", 20),
    _TemporalStep("Years", 50),
    _TemporalStep("Years", 100),
    _TemporalStep("Years", 200),
    _TemporalStep("Years", 500),
    _TemporalStep("Years", 1000),
    _TemporalStep("Years", 2000),
    _TemporalStep("Years", 5000),
    _TemporalStep("Years", 10000),
    _TemporalStep("Years", 20000),
    _TemporalStep("Years", 50000),
    _TemporalStep("Years", 100000),
    _TemporalStep("Years", 200000),
    _TemporalStep("Years", 500000),
    _TemporalStep("Years", 1000000),
    _TemporalStep("Years", 2000000),
    _TemporalStep("Years", 5000000),
)
"""The ladder, ascending (rule 3). Written out rather than generated: it is a
pinned vocabulary five hosts mirror, and an explicit list cannot drift on a
difference of opinion about exponentiation."""


def _ceil_to(k: int, i: int) -> int:
    """Round an index UP to the next multiple of ``k`` (both non-negative)."""
    return _trunc_div(i + k - 1, k) * k


def _month_window(k: int, lo: int, hi: int) -> tuple[int, int]:
    """The aligned window a month rung covers: ``(first aligned month index,
    count)`` over ``[lo, hi]``, in month-index space (``year·12 + month - 1``).
    Closed-form, so a count never generates a tick."""
    y0, m0, d0 = _civil_from_days(lo)
    # A `lo` past the 1st means `lo`'s own month start is outside the domain.
    first_idx = (y0 * 12 + m0 - 1) + (1 if d0 > 1 else 0)
    first = _ceil_to(k, first_idx)
    y1, m1, _ = _civil_from_days(hi)
    # `hi`'s own month start is always inside the domain (its day >= 1).
    last = _trunc_div(y1 * 12 + m1 - 1, k) * k
    if last < first:
        return first, 0
    return first, _trunc_div(last - first, k) + 1


def _year_window(k: int, lo: int, hi: int) -> tuple[int, int]:
    """The year rung's twin of :func:`_month_window`, in year space."""
    y0, m0, d0 = _civil_from_days(lo)
    first_year = y0 + (0 if (m0 == 1 and d0 == 1) else 1)
    first = _ceil_to(k, first_year)
    y1, _, _ = _civil_from_days(hi)
    last = _trunc_div(y1, k) * k
    if last < first:
        return first, 0
    return first, _trunc_div(last - first, k) + 1


def _tick_count(step: _TemporalStep, lo: int, hi: int) -> int:
    """How many ``step``-aligned ticks fall in ``[lo, hi]`` — closed-form, never by
    generation (rule 3), so walking the ladder is O(rungs) whatever the span."""
    if hi < lo:
        return 0
    if step.unit == "Days":
        return _trunc_div(hi - lo, step.count) + 1
    if step.unit == "Months":
        return _month_window(step.count, lo, hi)[1]
    return _year_window(step.count, lo, hi)[1]


def _temporal_ticks(step: _TemporalStep, lo: int, hi: int) -> list[int]:
    """The ``step``-aligned ticks in ``[lo, hi]``, ascending."""
    if hi < lo:
        return []
    if step.unit == "Days":
        return [lo + i * step.count for i in range(_trunc_div(hi - lo, step.count) + 1)]
    if step.unit == "Months":
        first, count = _month_window(step.count, lo, hi)
        out = []
        for i in range(count):
            idx = first + i * step.count
            out.append(_days_from_civil(_trunc_div(idx, 12), _trunc_mod(idx, 12) + 1, 1))
        return out
    first, count = _year_window(step.count, lo, hi)
    return [_days_from_civil(first + i * step.count, 1, 1) for i in range(count)]


def _choose_temporal_step(max_ticks: int, lo: int, hi: int) -> _TemporalStep:
    """The chosen rung: the FIRST whose in-domain tick count fits ``max_ticks``,
    else the coarsest (rule 3). Total — the ladder is never empty."""
    for s in _TEMPORAL_LADDER:
        if _tick_count(s, lo, hi) <= max_ticks:
            return s
    return _TEMPORAL_LADDER[-1]


def _temporal_domain(days: Sequence[int]) -> tuple[int, int]:
    """The domain: the data's own extent, unexpanded, with the degenerate guard
    (rule 2). No rows ⇒ ``[0, 1]`` — the epoch day and the one after it, which
    draws an axis rather than dividing by zero."""
    if not days:
        return 0, 1
    lo = min(days)
    hi = max(days)
    return (lo, lo + 1) if hi == lo else (lo, hi)


def _pad(width: int, v: int) -> str:
    s = str(v)
    if len(s) >= width:
        return s
    return "0" * (width - len(s)) + s


def _temporal_label(step: _TemporalStep, day: int) -> str:
    """The tick label for ``day`` under ``step`` — the granularity-adaptive format
    (rule 4). ``yyyy`` past a year, ``mmm yy`` past 27 days, else ``dd mmm yy``."""
    y, m, d = _civil_from_days(day)
    nominal = _nominal_days(step)
    if nominal > 365.0:
        return _pad(4, y)
    yy = _pad(2, _trunc_mod(y, 100))
    mmm = _MONTH_NAMES[m - 1]
    if nominal > 27.0:
        return mmm + " " + yy
    return _pad(2, d) + " " + mmm + " " + yy


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


# Phase 883 — the separator between the three parts of a hover readout. A middle
# dot with spaces of its own: not a character a series or category name is likely
# to contain (a hyphen, a slash and a comma all are), and it reads as a separator
# rather than as punctuation belonging to either side.
_TIP_SEPARATOR = " · "


def _with_tip(style: Obj, text: str) -> Obj:
    """Phase 883 — stamp the hover readout onto a data-bearing shape's style.

    An EMPTY readout is dropped rather than encoded: an empty SVG ``<title>``
    suppresses the native tooltip AND overrides the element's accessible name
    with nothing, which is worse than having no title at all.
    """
    if text == "":
        return style
    return Obj(style.tag, {**style.fields, "tip": text})


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
    # Phase 878 — the axis names + the subtitle. WIRE fields, on the same side
    # of the D8 line as ``title``: what an axis is CALLED is the author's
    # meaning, where and how it is drawn stays the host's. All three are
    # optional, and the axis titles are DEFAULT-ON — an absent one falls back to
    # the capitalised field name, so an axis is never nameless.
    x_title: str | None = None
    y_title: str | None = None
    subtitle: str | None = None
    # Phase 880 — WHERE the legend sits, and whether it sits anywhere at all.
    # A WIRE field for the same reason the titles above are (D8): the edge an
    # author wants the legend on is their meaning; the column widths and pitches
    # that realise it are the host's, above.
    #
    # Absent means "the host default" (``_LEGEND_POSITION_DEFAULT``, which is
    # ``Right``) — NOT "no legend"; suppression is the explicit ``"None"``. So
    # absence stays the ordinary shape and is omitted on the wire, and an author
    # who wants no legend has to say so.
    legend_position: str | None = None
    # Phase 881 — whether the values are written onto the picture. A WIRE field
    # in the same way: whether a reader is meant to read the NUMBERS or the
    # shape is the author's meaning; the type size, the offsets and the fit rule
    # that decide whether a given label draws are the host's, above.
    #
    # Absent means ``"Off"``, which is also the default, so an absent field
    # lowers to the pre-881 picture byte-for-byte. ``"Ends"`` is the only other
    # value there is.
    data_labels: str | None = None
    # Phase 882 — what the x column MEANS: discrete ``Category`` bands or
    # ``Temporal`` dates on a continuous day-scale. A WIRE field, and a DECLARED
    # one: what a column is is the author's meaning, and inferring it (from the
    # column type, or worse from the cell strings) would make one wire tree draw
    # a band axis or a temporal one depending on where its rows came from.
    #
    # Absent means ``"Category"``, which is also the default, so an absent field
    # lowers to the pre-882 picture byte-for-byte. ``"Temporal"`` is the only
    # other value there is, and the pre-emit validator grounds it against the
    # column type where the schema is statically known (FUARAN097).
    x_scale: str | None = None
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


# ── The accessible summary (Phase 921) ───────────────────────────────────────
#
# NORMATIVE CROSS-HOST SPEC, ported verbatim from the F# reference and pinned by
# the ``chart-lowering/*`` goldens; ``docs/CHARTS-DRAWING-PRIMITIVE-DESIGN.md``
# §4i carries the language-neutral statement.
#
# The drawing root is ``role="img"``, which presents the chart as ONE graphic and
# does not traverse into it — so the per-mark ``<title>``s are never announced.
# Operator decision 2026-08-18: the root keeps that role, and the lowering
# generates a deterministic summary as the drawing's ``description``, which the
# SVG builder wires to the root's ``aria-label``. The title is NOT part of it:
# it is a text source whose bound/i18n arms resolve only at render time, so the
# builder composes it in front instead.

#: The clause separator + terminator. Periods, not commas: a screen reader pauses
#: at a sentence boundary.
_SUMMARY_CLAUSE_SEPARATOR = ". "

#: At most this many series are NAMED before the summary folds the rest into a
#: count — a legibility bound, not a technical one.
_SUMMARY_MAX_SERIES_NAMED = 4

#: The per-NAME character cap (a series field, a category label) — untrusted
#: strings straight off the data feed.
_SUMMARY_MAX_NAME_CHARS = 32

#: The whole summary's character cap.
_SUMMARY_MAX_CHARS = 320


def _clamp_text(max_chars: int, s: str) -> str:
    """Truncate to at most ``max_chars``, marking the cut with the ellipsis.

    The cap counts UTF-16 code units, which is what the F#/TypeScript hosts
    count natively — so a non-BMP character costs two here as it does there, and
    the cut lands in the same place on every host. A cut never splits a surrogate
    pair.
    """
    units = s.encode("utf-16-le")
    if len(units) <= max_chars * 2:
        return s
    cut = max_chars - 1
    prev = int.from_bytes(units[(cut - 1) * 2 : cut * 2], "little")
    if cut > 0 and 0xD800 <= prev <= 0xDBFF:
        cut -= 1
    return units[: cut * 2].decode("utf-16-le") + _ELLIPSIS


def _summary_kind_words(kind: str, stacked: bool) -> str:
    """The chart's kind in words. ``stacked`` earns a word only on the two arms
    where it changes the geometry — the same rule the lowering itself applies."""
    if kind == "Bar":
        return "Stacked bar chart" if stacked else "Bar chart"
    if kind == "Line":
        return "Line chart"
    if kind == "Area":
        return "Stacked area chart" if stacked else "Area chart"
    if kind == "Scatter":
        return "Scatter chart"
    if kind == "Pie":
        return "Pie chart"
    return "Heatmap chart"


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

    # ── Hover readout (Phase 883) ────────────────────────────────────────────
    #
    # THE TIP IS WHERE FULL PRECISION LIVES. A printed data label (Phase 881)
    # goes through ``y_tick_text`` — the axis's own formatter, step precision
    # and display unit — and reads ROUGHLY WHERE. The tip answers the other
    # question, WHAT EXACTLY IS THIS, so it takes the opposite three decisions:
    # UNSCALED by the display unit (a tooltip has no unit slot beside it), the
    # DATUM's own precision rather than the tick step's (an author's EXPLICIT
    # ``Number``/``Percent`` precision still wins — a declared precision is a
    # statement about the data, not the axis), and the currency symbol KEPT
    # (the ticks drop it because the axis-unit label states it once).
    #
    # Passing ``v`` as the step is what selects the datum's own precision:
    # ``_format_value`` derives its decimals from the step when no explicit
    # precision is declared, so step = value gives the fewest decimals that
    # reproduce the value exactly.
    def tip_value_text(v: float) -> str:
        return _format_value(value_format, 1.0, False, v, v)

    def datum_tip(style: Obj, series_field: str, category_key: str, v: float) -> Obj:
        """The readout for a PER-DATUM mark (bar, stack segment, wedge, scatter
        point): "Series · Category · value". Both leading parts are untrusted
        strings straight off the data feed — the renderer's XML escape is what
        makes that safe. The series name is the FIELD name, matching the legend
        and the mark id rather than the capitalised axis title."""
        return _with_tip(style, f"{series_field}{_TIP_SEPARATOR}{category_key}{_TIP_SEPARATOR}{tip_value_text(v)}")

    def series_tip(style: Obj, series_field: str) -> Obj:
        """The readout for a SERIES-LEVEL mark (a line, an area band or its
        edge). THE TIP'S GRANULARITY FOLLOWS THE MARK'S IDENTITY GRANULARITY —
        one element IS the whole series, and SVG resolves a tooltip per
        ELEMENT, so a single ``<title>`` cannot honestly report one point's
        value: whichever was chosen would show for a hover anywhere along the
        line."""
        return _with_tip(style, series_field)

    # ── Linear x-scale (Phase 636 — the Scatter arm's numeric x axis) ──
    # Scatter reads the x-field NUMERICALLY and plots on a linear x-domain (the
    # first non-band x-scale arm). The domain is NOT zero-anchored — a scatter's
    # x range carries no baseline semantics (the y domain stays zero-anchored
    # with the other arms, deliberately: one shared y-domain rule).
    is_scatter = spec.kind == "Scatter"

    # ── Temporal x-scale (Phase 882 — the SECOND non-band x-scale) ──
    #
    # DECLARED, never inferred. ``x_scale == "Temporal"`` is the author saying
    # "this column is dates"; the language then GROUNDS that claim against the
    # statically-known column type (FUARAN097) wherever it can. Inference was the
    # alternative and is wrong twice over: the schema is statically known only
    # for an embedded table with an EMPTY pipeline (FUARAN086's window), so an
    # inferred axis would make the same tree draw a band axis or a temporal one
    # depending on where its rows came from — a picture that depends on data
    # PROVENANCE — and sniffing the cell strings for an ISO-8601 shape is the
    # guess-dressed-as-a-rule §4e refused. Absent is ``"Category"``, which is
    # every pre-882 chart, byte-for-byte.
    #
    # Pie is excluded because it HAS no x axis: a temporal declaration there is
    # dead intent the polar arm cannot honour, and neutralising it here keeps the
    # pie geometry free of a scale it never reads.
    is_temporal = (spec.x_scale or _X_SCALE_DEFAULT) == "Temporal" and spec.kind != "Pie"

    # Each row's x as a DAY NUMBER, read off the same string projection the band
    # arms label with — which is exactly the canonical ISO-8601 form a date /
    # timestamp cell carries through the row bridge. So the mark identity keeps
    # the day number while the geometry uses the same integer, and neither has to
    # be derived from the other.
    day_values = [_day_of(c) for c in categories] if is_temporal else []

    # The x axis is CONTINUOUS (Phase 903's split) on exactly two arms: the
    # Scatter arm's numeric x and a temporal x. Everything keyed off this — tick
    # marks AT the value, vertical gridlines, marks placed by value rather than
    # by band index — follows from that one property rather than from a list of
    # kinds.
    is_continuous_x = is_scatter or is_temporal

    if is_temporal:
        x_values = [float(d) for d in day_values]
    elif is_scatter:
        x_values = [_numeric_of(r, spec.x_field) for r in rows]
    else:
        x_values = []

    # The chosen calendar rung, on a temporal axis only. ONE value decides both
    # the tick positions and the label format, so the two cannot disagree about
    # the axis's granularity.
    temporal_step: _TemporalStep | None = None
    if is_temporal:
        temporal_lo, temporal_hi = _temporal_domain(day_values)
        temporal_step = _choose_temporal_step(int(_TARGET_TICK_COUNT) + 1, temporal_lo, temporal_hi)

    if temporal_step is not None:
        # The domain is the data's own extent (rule 2) — deliberately NOT nice-d
        # outward — and the ticks are the calendar-aligned instants inside it.
        # ``x_step`` carries the rung's NOMINAL length, which is what the label
        # format reads.
        x_nice_lo, x_nice_hi = float(temporal_lo), float(temporal_hi)
        x_step = _nominal_days(temporal_step)
        x_ticks = [float(t) for t in _temporal_ticks(temporal_step, temporal_lo, temporal_hi)]
    elif is_scatter:
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
    #
    # A TEMPORAL tick takes the calendar label instead (Phase 882) — the same
    # one-formatter-per-axis discipline over a different vocabulary: the number
    # formatter has nothing true to say about a date.
    def x_tick_text(v: float) -> str:
        if temporal_step is not None:
            return _temporal_label(temporal_step, int(v))
        return _format_value(None, 1.0, False, x_step, v)

    tick_size = _TICK_FONT_SIZE
    title_size = 18.0
    subtitle_size = _SUBTITLE_FONT_SIZE

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

    # ── Axis names + subtitle (Phase 878) ────────────────────────────────────
    #
    # Resolved HERE, before any margin, because both margins have to reserve a
    # line for text whose presence is decided by these three fields — the left
    # margin for the rotated y-axis title, the top margin for the subtitle. The
    # same dependency Phase 879 established when the bottom margin started
    # reserving the x-axis title's line.

    def axis_title_of(declared: str | None, fallback_field: str) -> str | None:
        """An axis title: the author's own text when declared, else the
        capitalised field name — which is exactly what the x axis has always
        drawn, now stated once and applied to both axes. ``None`` only where
        there is no honest fallback: an empty field name, or a y axis carrying
        no series at all."""
        if declared is not None:
            return declared
        if fallback_field == "":
            return None
        return _capitalise(fallback_field)

    # Phase 882 wires §4e's date-axis rule: a SELF-EVIDENT DATE AXIS SUPPRESSES
    # ITS DEFAULT TITLE — an axis reading "Jan Feb Mar" does not need the word
    # "Date" beneath it. Two boundaries, both stated when the rule was written
    # down and both kept: it applies to the FALLBACK only (an explicit ``x_title``
    # is the author overriding the default and always draws), and it suppresses
    # the TITLE, never the axis. The declaration is what made it wirable —
    # nothing before 882 could tell a date column from a string one, which is why
    # 878 recorded the rule instead of shipping it.
    x_title = None if (is_temporal and spec.x_title is None) else axis_title_of(spec.x_title, spec.x_field)

    # The y fallback is the capitalised FIRST y-field. It is the honest answer
    # to "what is on this axis", where the retired "Value" literal named neither
    # the measure nor its unit — and it makes ONE rule cover both axes rather
    # than a rule for x and a constant for y. The multi-series chart is the case
    # it serves least well; there the legend already names every series, and an
    # author plotting genuinely different measures should declare ``y_title``,
    # which is precisely why the field exists.
    y_title = axis_title_of(spec.y_title, spec.y_fields[0] if spec.y_fields else "")

    # ── Top margin ──
    # A subtitle takes one line under the visible title, and EVERYTHING below it
    # in the top band moves down by exactly that line: the legend row, the
    # display-unit slot, and the plot itself (so on the Pie arm the wedge centre
    # moves too). Reserved only when a subtitle is present, so a chart without
    # one keeps the pre-878 layout byte-for-byte.
    subtitle_band = _text_line_height(subtitle_size, _TEXT_LINE_HEIGHT_FACTOR) if spec.subtitle is not None else 0.0
    margin_top = _r2(_MARGIN_TOP + subtitle_band)

    def bound_text(font_size: float, extent: float, t: str) -> str:
        """Bound a title to the extent it runs along."""
        return _truncate_to_width(font_size, extent, t)

    # ── Left margin ──
    # The truncation budget is derived from the CEILING — a constant — so the
    # truncation that feeds the margin never depends on the margin it decides.
    left_ceiling = _MARGIN_LEFT_MAX_SHARE * _W

    # Phase 878 — the rotated y-axis title occupies one LINE of the left margin,
    # outboard of the tick column. Only its line height (plus the padding beside
    # it) is reserved here: the title is rotated, so its LENGTH runs vertically
    # and is bounded against the plot height further down. That is what keeps
    # this acyclic — exactly the shape Phase 879 gave the x-axis title's line in
    # the bottom margin.
    y_title_band = line_height + _AXIS_LABEL_PADDING if y_title is not None else 0.0

    tick_text_budget = max(0.0, left_ceiling - _TICK_LABEL_GAP - _AXIS_LABEL_PADDING - y_title_band)

    def y_tick_label_text(v: float) -> str:
        return _truncate_to_width(tick_size, tick_text_budget, y_tick_text(v))

    required_left = (
        _TICK_LABEL_GAP + widest_of([y_tick_label_text(t) for t in ticks]) + _AXIS_LABEL_PADDING + y_title_band
    )
    margin_left = _r2(max(_MARGIN_LEFT, min(left_ceiling, required_left)))

    plot_x0 = margin_left

    # ── Legend placement (Phase 880; BAND overflow fallback 2026-08-18) ──────
    #
    # ONE legend with four placements, resolved HERE — AFTER the left margin,
    # whose ``plot_x0`` is where a band packs FROM, and before the plot's right
    # edge, because a ``Right`` legend's column width is an INPUT to the plot
    # rectangle and a ``Bottom`` legend's band is an input to the bottom margin.
    # The same acyclicity discipline the text metrics established: everything
    # the layout reads is computed before the layout that reads it. Phase 880
    # resolved this block above ALL the margins; the overflow rule moved it
    # below the LEFT one, because that is where the band's available width
    # comes from. Nothing between the two reads the legend, so the block moved
    # whole.
    #
    # The pie arm's shares are resolved here for the same reason: its legend
    # labels carry them ("name (NN%)"), so they are layout input, not output.
    is_pie = spec.kind == "Pie"
    pie_values = series[0] if is_pie and m == 1 else []
    pie_total = sum(pie_values)
    # The Phase-638 bounded-v1 guard, unchanged and merely lifted: exactly one
    # series, no negative value, a positive total. A refused pie draws no
    # geometry AND no legend — a legend for a picture that was refused would be
    # a claim about data the drawing declined to show.
    pie_refused = is_pie and (m != 1 or any(v < 0.0 for v in pie_values) or pie_total <= 0.0)
    pie_fractions = [v / pie_total for v in pie_values] if is_pie and not pie_refused else []

    # The legend's rows in draw order — ``(colour, label)``. TWO sources, ONE
    # shape, which is what Phase 880 unified: the cartesian arms legend their
    # SERIES and only when there is more than one (with a single series the
    # title already names it — the pre-880 rule, preserved exactly), while the
    # pie arm legends its CATEGORIES, which is why a single-series pie legends
    # and a single-series bar does not. Before this phase these were two
    # separate emitters with two separate constant sets, and only one of them
    # could honour a position.
    legend_entries: list[tuple[str, str]] = []
    if is_pie:
        # Routed through the canonical formatter (Phase 876) — one rounding +
        # rendering rule for every number this module prints. A share is a whole
        # percent, so the shipped ``NN%`` shape is unchanged.
        legend_entries = [
            (_colour_for(i), f"{categories[i]} ({_format_value(None, 1.0, False, 1.0, f * 100.0)}%)")
            for i, f in enumerate(pie_fractions)
        ]
    elif m > 1:
        legend_entries = [(_colour_for(j), spec.y_fields[j]) for j in range(m)]

    # The placement the author ASKED FOR: their explicit spec value where there
    # is one, else the host default. With no entries at all the answer is
    # ``None`` whatever either of them said — so an explicit position on a
    # single-series chart still draws nothing and, more to the point, reserves
    # no space.
    if not legend_entries:
        requested_pos = "None"
    elif spec.legend_position is not None:
        requested_pos = spec.legend_position
    else:
        requested_pos = _LEGEND_POSITION_DEFAULT

    def band_entry_width(t: str) -> float:
        """A BAND entry's PITCH: the swatch's label offset, the label's own
        natural width, and the gap before the next entry. Read by the overflow
        predicate AND by the band emitter far below — one expression, so the
        rule can never decide against geometry the drawing does not use. The
        name is the untruncated one, because a band never truncates."""
        return _LEGEND_LABEL_OFFSET_X + _text_width(tick_size, t) + _LEGEND_ENTRY_GAP

    # The width a BAND has to pack into: from the plot's left edge, where the
    # band starts, to the plot's right edge — which on a band arm is the canvas
    # less the right margin, since a band reserves no column and
    # ``legend_column_w`` is 0 there by construction. So the term is not
    # circular, and it is the PLOT's width rather than
    # canvas-minus-declared-margins: the band packs from ``plot_x0``, the
    # AUTOSIZED left margin, not from ``_MARGIN_LEFT``.
    band_available_w = _W - _MARGIN_RIGHT - plot_x0

    # **The BAND overflow rule (operator decision, 2026-08-18).** An explicit
    # ``Top`` or ``Bottom`` legend whose entries do not pack into one band row
    # FALLS BACK TO THE RIGHT-HAND COLUMN. A band's width is the SUM of its
    # entries, so it runs off the canvas once the names are long enough or
    # numerous enough — and truncating any one name cannot fix a sum, which is
    # why Phase 879's per-entry natural pitch and Phase 880's repositioning both
    # left it standing.
    #
    # The column never loses information, never grows the band unboundedly, and
    # reuses layout that already shipped. Two alternatives were considered and
    # DECLINED: a second row grows the reserved band and moves the plot
    # rectangle with the entry COUNT (chrome sliding under a data refresh); a
    # refusal loses the legend entirely, when the author's intent — a visible
    # legend — is honourable at another edge. So ``Top``/``Bottom`` mean "band
    # if it fits, column if it cannot"; the wire is unchanged.
    #
    # The comparison INCLUDES the last entry's trailing ``_LEGEND_ENTRY_GAP``,
    # exactly as the emitter computes it — that gap is the clearance to the
    # right margin. Strict ``>``, so an exact fit stays a band. And the fallback
    # is UNIFORM: the whole legend moves, never a split across two edges.
    band_overflows = requested_pos in ("Top", "Bottom") and (
        sum(band_entry_width(t) for _, t in legend_entries) > band_available_w
    )

    # The placement actually used.
    legend_pos = "Right" if band_overflows else requested_pos

    # COLUMN arms: the widest label decides the column, bounded by
    # ``_LEGEND_COLUMN_MAX_SHARE`` of the canvas and truncated beyond it — the
    # margin autosizes' posture, adopted for the same reason. A name with no
    # bound is a data problem the layout should report by truncating, not absorb
    # by shrinking the picture.
    legend_name_budget = max(0.0, _LEGEND_COLUMN_MAX_SHARE * _W - _LEGEND_LABEL_OFFSET_X - _LEGEND_COLUMN_GAP)

    # A BAND arm packs at NATURAL width and never truncates: its overflow is in
    # the SUM, not in one name, so truncating would cost information without
    # fixing anything — a band that cannot pack falls back to the column above.
    legend_texts = [
        _truncate_to_width(tick_size, legend_name_budget, t) if legend_pos == "Right" else t for _, t in legend_entries
    ]

    legend_column_w = (
        _r2(_LEGEND_COLUMN_GAP + _LEGEND_LABEL_OFFSET_X + widest_of(legend_texts)) if legend_pos == "Right" else 0.0
    )

    # The ``Bottom`` band's height — one line plus its padding, reserved BELOW
    # everything the bottom margin's autosize already accounts for (the x-axis
    # title's line included), so the two computations never contend for the same
    # pixels. The exact mirror of ``subtitle_band`` at the top: one term that
    # shifts the whole band, present only when the arm is.
    legend_band_h = _r2(line_height + _AXIS_LABEL_PADDING) if legend_pos == "Bottom" else 0.0

    # Phase 880 — a ``Right`` legend takes its column off the PLOT, not off the
    # right margin: the margin stays the clearance between the legend's widest
    # label and the canvas edge, exactly as it was the clearance to the plot
    # before. Every other placement leaves ``legend_column_w = 0``, so the
    # pre-880 rectangle is recovered term-for-term.
    plot_x1 = _W - _MARGIN_RIGHT - legend_column_w
    plot_w = plot_x1 - plot_x0

    band_w = plot_w / float(n) if n > 0 else plot_w

    def centre_x(i: int) -> float:
        return _r2(plot_x0 + band_w * (float(i) + 0.5))

    def boundary_x(i: int) -> float:
        """The ``i``th BAND BOUNDARY.

        ``n`` bands have ``n+1`` of them, boundary ``0`` on the y-axis spine and
        boundary ``n`` on the plot's right edge. Phase 903's category tick marks
        land here, where a label lands at ``centre_x``.
        """
        return _r2(plot_x0 + band_w * float(i))

    # ── The x-axis-label ANGLE LADDER (Phase 903, correcting Phase 879) ──
    # The BAND arms label categories; Pie has no x axis at all and Scatter labels
    # numeric x ticks (short by construction, left horizontal). Both of those must
    # contribute NO drop, or their bottom margin — and with it the pie's centre —
    # would move for a decision they never take.
    draws_category_labels = not is_scatter and not is_temporal and spec.kind != "Pie"

    # Phase 882 — a TEMPORAL axis labels its TICKS, and the ladder applies to
    # them: same three rungs, same footprint formula, measured against the TICK
    # PITCH instead of the band pitch. A date label is not short by construction
    # the way a numeric tick is ("15 Jan 26" against "150"), so leaving it
    # always-flat would recreate exactly the overlap the ladder exists to resolve
    # — and reusing the ladder rather than adding a second rule is what keeps one
    # angle policy for the whole x axis.
    temporal_tick_texts = [x_tick_text(t) for t in x_ticks] if is_temporal else []

    # Whether the x axis draws labels the ladder governs at all — the band arms'
    # categories or a temporal axis's ticks. Scatter and Pie: no.
    draws_x_axis_labels = draws_category_labels or is_temporal

    # The pitch the ladder measures a label against: a band's width, or — on a
    # temporal axis — the SMALLEST pixel gap between consecutive ticks, since
    # calendar gaps are not uniform (28 to 31 days a month) and the tightest pair
    # is the one that has to fit. Computable here because it needs ``plot_w``
    # only, which the left margin has already fixed: the acyclicity Phase 879
    # established survives intact, with nothing reading the bottom margin the
    # ladder is about to decide.
    if not is_temporal:
        x_label_pitch = band_w
    elif len(x_ticks) < 2:
        x_label_pitch = plot_w
    else:
        span = x_nice_hi - x_nice_lo
        min_gap = span
        for a, b in zip(x_ticks[:-1], x_ticks[1:], strict=True):
            min_gap = min(min_gap, b - a)
        x_label_pitch = plot_w * min_gap / span

    # The labels the ladder decides on, AS AUTHORED (see below).
    x_labels_as_authored = temporal_tick_texts if is_temporal else categories

    # A rotated label's footprint ALONG the axis is w·cos θ + h·sin θ. At 0° that
    # is the bare width (cos 0 = 1, sin 0 = 0, both exact on every IEEE-754 host,
    # so the flat rung needs no special case); at 90° the width term vanishes, so
    # the vertical rung takes one line height per label at any count — which is
    # why it is terminal.
    def along_axis_footprint(deg: float, w: float) -> float:
        return w * math.cos(math.radians(deg)) + line_height * math.sin(math.radians(deg))

    # THREE RUNGS, ONE PREDICATE, applied to the WIDEST label and therefore
    # UNIFORMLY to the axis: flat while every label fits its band, 30° when it
    # does not, vertical when 30° no longer packs either. Deciding on the widest
    # label rather than per-label is what keeps an axis from mixing angles.
    #
    # Decided on the labels AS AUTHORED (``x_labels_as_authored``, not the
    # truncated ``x_label_texts``): the truncation budget below is a function of
    # the angle, so reading truncated text here would be circular as well as
    # wrong.
    widest_x_label = widest_of(x_labels_as_authored)

    def packs_at(deg: float) -> bool:
        return along_axis_footprint(deg, widest_x_label) <= x_label_pitch

    if not draws_x_axis_labels or n == 0 or _LABEL_TILT_DEGREES <= 0.0:
        # A zero angle is FLAT-ALWAYS, not "the ladder with a flat rung": a host
        # that zeroed it named the one rotation the ladder may use, so escalating
        # past it to vertical would override an explicit choice with a computed
        # one.
        tilt_degrees = 0.0
    elif packs_at(0.0):
        tilt_degrees = 0.0
    elif packs_at(_LABEL_TILT_DEGREES):
        tilt_degrees = _LABEL_TILT_DEGREES
    else:
        tilt_degrees = _VERTICAL_TILT_DEGREES

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
    # The x labels as DRAWN — the ladder's own labels, bounded by the drop
    # ceiling. Empty on the arms that draw none, so their bottom margin is unmoved
    # (Scatter's short numeric ticks are emitted separately, flat).
    x_label_texts = (
        [_truncate_to_width(tick_size, category_text_budget, c) for c in x_labels_as_authored]
        if draws_x_axis_labels
        else []
    )
    required_bottom = (
        _CATEGORY_LABEL_OFFSET_Y
        + sin_tilt * widest_of(x_label_texts)
        + _AXIS_LABEL_PADDING
        + line_height
        + _AXIS_TITLE_BOTTOM_OFFSET
    )
    # Phase 880 — the ``Bottom`` legend's band is ADDED to the autosized margin
    # rather than competing inside its ceiling: the ceiling exists to stop
    # LABELS eating the plot, and the legend is not a label. So the picture
    # shrinks by the band, and the tilt escalation still sees the budget it had.
    margin_bottom = _r2(legend_band_h + max(_MARGIN_BOTTOM, min(bottom_ceiling, required_bottom)))

    plot_y0 = margin_top
    plot_y1 = _H - margin_bottom
    plot_h = plot_y1 - plot_y0

    def y_scale(v: float) -> float:
        return _r2(plot_y1 - (v - nice_lo) / (nice_hi - nice_lo) * plot_h)

    def x_scale_raw(v: float) -> float:
        """The x-scale before rounding. Split out by Phase 882 so the bar arms can
        derive an UNROUNDED slot origin from it: rounding a centre and then
        subtracting half a width would round twice, and the band arms' goldens pin
        the single-rounding form."""
        return plot_x0 + (v - x_nice_lo) / (x_nice_hi - x_nice_lo) * plot_w

    def x_scale(v: float) -> float:
        return _r2(x_scale_raw(v))

    # ── Cartesian chrome (painter's order pieces) ──
    grid_style = _style_stroke_ink(_GRID_OPACITY, 1.0)
    axis_stroke_style = _style_stroke_ink(_AXIS_OPACITY, 1.0)
    gridlines: list[Value] = [_line(_r2(plot_x0), y_scale(t), _r2(plot_x1), y_scale(t), grid_style) for t in ticks]

    # Vertical gridlines — wherever the x axis is CONTINUOUS (Phase 875 for
    # Scatter, extended to the temporal axis by Phase 882). A continuous scale has
    # readable x positions, so a reader traces a point back to an x value the same
    # way the horizontal grid lets them trace a y value. A BAND x-axis has no such
    # positions to trace (a category is a label, not a magnitude), so a vertical
    # rule there would be decoration. Stating it as "continuous" rather than
    # "Scatter" is what let the temporal axis inherit the behaviour instead of
    # re-deciding it — including on a temporal BAR chart, where the rules read as
    # date guides through the bars rather than as chrome.
    x_gridlines: list[Value] = (
        [_line(x_scale(t), _r2(plot_y0), x_scale(t), _r2(plot_y1), grid_style) for t in x_ticks]
        if is_continuous_x
        else []
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
    #
    # BAND vs CONTINUOUS (Phase 903). Where the axis is CONTINUOUS a tick marks a
    # VALUE and sits at it: the y axis, and Scatter's numeric x. Where it is a
    # BAND axis a tick DELIMITS a group, so the n+1 marks land on the band
    # BOUNDARIES and the label stays centred between two of them — the
    # category-axis convention, and the honest one: a category has an extent, not
    # a position, so a mark under its centre claims a coordinate the axis does
    # not have.
    if _TICK_MARK_LENGTH <= 0.0:
        tick_marks: list[Value] = []
    else:
        y_marks: list[Value] = [
            _line(_r2(plot_x0 - _TICK_MARK_LENGTH), y_scale(t), _r2(plot_x0), y_scale(t), axis_stroke_style)
            for t in ticks
        ]

        def _x_mark(x: float) -> Value:
            return _line(x, _r2(plot_y1), x, _r2(plot_y1 + _TICK_MARK_LENGTH), axis_stroke_style)

        if is_continuous_x:
            x_marks: list[Value] = [_x_mark(x_scale(t)) for t in x_ticks]
        elif n == 0:
            x_marks = []
        else:
            x_marks = [_x_mark(boundary_x(i)) for i in range(n + 1)]
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
    # Every category label sits at its band CENTRE — including since Phase 903,
    # when the tick marks moved to the boundaries: the label names the band, the
    # marks delimit it.
    #
    # The ANCHOR follows the ladder's rung. At the FLAT rung a label is
    # Middle-anchored on the band centre (the pre-879 convention, restored). At
    # either ROTATED rung it is End-anchored at the same point and rotated
    # NEGATIVELY (counter-clockwise, against ``rotation``'s clockwise
    # convention): the anchor is the pivot, so the text ENDS under the band
    # centre and runs back down-and-left, reading up-to-the-right into it. The
    # opposite sign would swing the same text up into the plot area. At 90° this
    # degenerates to reading bottom-up. Scatter's numeric ticks stay horizontal
    # + Middle — short by construction, and centred on their value.
    #
    # Phase 882 — a TEMPORAL axis's labels sit at their TICKS (not at a band
    # centre, because there are no bands) and take the ladder's rung and anchor
    # exactly as the band arms do. So one expression covers "centred at the
    # position the label names" on both, and the only thing that differs is which
    # positions those are.
    x_label_style = (
        _text_style(_LABEL_OPACITY, "End", tick_size, "Normal", _r2(-tilt_degrees))
        if tilt_degrees > 0.0
        else _text_style(_LABEL_OPACITY, "Middle", tick_size, "Normal")
    )

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
    elif is_temporal:
        x_labels = [
            _label(
                x_scale(t),
                _r2(plot_y1 + _CATEGORY_LABEL_OFFSET_Y),
                _literal(text),
                x_label_style,
            )
            for t, text in zip(x_ticks, x_label_texts, strict=True)
        ]
    else:
        x_labels = [
            _label(centre_x(i), _r2(plot_y1 + _CATEGORY_LABEL_OFFSET_Y), _literal(c), x_label_style)
            for i, c in enumerate(x_label_texts)
        ]

    # ── Axis titles + the display-unit slot (Phase 878) ──
    #
    # Three rules, and together they retire the hardcoded "Value":
    #
    #   1. NAMES. The x title stays centred under the tick band (where it has
    #      always been); the y title is ROTATED by `-_Y_AXIS_TITLE_DEGREES` in
    #      the left margin, centred on the plot, reading BOTTOM-UP — the
    #      conventional treatment, and the same sign convention the vertical
    #      category labels already use. Each falls back to its capitalised field
    #      name, so an axis is never nameless.
    #
    #   2. UNITS KEEP THEIR OWN SLOT. The top-left label states the Phase-876
    #      display unit and NOTHING else: with no scaling in play it is not
    #      drawn at all, where it previously fell back to the literal "Value" —
    #      a word naming neither the measure nor its unit, printed on every
    #      chart in the corpus. Composing the unit INTO the rotated title
    #      ("Revenue (Millions of £)") was the alternative and was rejected:
    #      that concatenation is only expressible when the title is a literal,
    #      so a bound or i18n title would silently fall back to a different
    #      layout — and a layout rule with a shape that depends on which text
    #      source an author reached for is not a rule. Two slots, always the
    #      same two, is what stays total.
    #
    #   3. DEDUPE. An explicit subtitle SUPPRESSES the unit slot. The subtitle
    #      is the author's own place to say "£m", and the machine restating it
    #      two lines away is exactly the clutter this rule exists to prevent —
    #      so the author's sentence wins. PRESENCE is the whole test: no string
    #      comparison, which is what keeps the rule total over every text-source
    #      arm and identical on every host.
    #
    # A SELF-EVIDENT DATE AXIS SUPPRESSES ITS DEFAULT TITLE — an axis reading
    # "Jan Feb Mar" does not need the word "Month" beneath it. The rule is
    # recorded here and is WIRED when the temporal axis lands: nothing in the
    # lowering can currently tell a date column from a string one, and inferring
    # it from the label text would be a guess dressed as a rule. It will apply
    # to the FALLBACK only — an explicit x title is the author overriding the
    # default, and always draws.
    axis_titles: list[Value] = []
    if x_title is not None:
        axis_titles.append(
            _label(
                _r2((plot_x0 + plot_x1) / 2.0),
                # Phase 880 — the x title rides ABOVE a ``Bottom`` legend band,
                # keeping its own inset from whatever is beneath it.
                # ``legend_band_h`` is 0 on every other arm, so the pre-880
                # baseline is unchanged.
                _r2(_H - legend_band_h - _AXIS_TITLE_BOTTOM_OFFSET),
                _literal(bound_text(tick_size, plot_w, x_title)),
                _text_style(None, "Middle", tick_size, "Normal"),
            )
        )
    if y_title is not None:
        # Middle-anchored at the plot's vertical centre: the anchor is the
        # pivot, so the rotated text stays centred on the axis it names,
        # whatever its length. The x is measured from the CANVAS edge, not the
        # autosized margin, so the title does not slide as tick widths change.
        axis_titles.append(
            _label(
                _r2(_Y_AXIS_TITLE_OFFSET_X),
                _r2((plot_y0 + plot_y1) / 2.0),
                _literal(bound_text(tick_size, plot_h, y_title)),
                _text_style(None, "Middle", tick_size, "Normal", _r2(-_Y_AXIS_TITLE_DEGREES)),
            )
        )
    if y_unit_label != "" and spec.subtitle is None:
        axis_titles.append(
            _label(
                _r2(8.0),
                _r2(plot_y0 - 12.0),
                _literal(y_unit_label),
                _text_style(None, "Start", tick_size, "Normal"),
            )
        )

    # ── Where a datum sits along x (Phase 882) ───────────────────────────────
    #
    # ONE pair of expressions the series geometry reads, and the band-vs-value
    # difference lives here and nowhere else. On a band axis a datum sits at its
    # band's INDEX; on a temporal axis it sits at its DATE — the same datum, a
    # different question asked of the axis.
    #
    # The temporal slot keeps ``band_w`` as its PITCH — ``plot_w / n``, the average
    # spacing — so a bar's thickness is decided by the same expression on both
    # axes and a monthly bar chart looks like a bar chart rather than like a
    # sequence of hairlines. With irregular dates two slots can overlap; that is
    # honest, because the bars are at their true positions and the overlap is the
    # data's, not the layout's. ``_BAR_MAX_THICKNESS`` already bounds the other
    # direction.

    def x_centre(i: int) -> float:
        """The x a datum's mark centres on."""
        return x_scale(x_values[i]) if is_temporal else centre_x(i)

    def slot_origin_x(i: int) -> float:
        """The UNROUNDED left edge of the slot a datum's bar geometry lays out in.
        Unrounded because the bar arms round once, at the end — the band form is
        ``plot_x0 + band_w·i`` character-for-character, so every band golden is
        unmoved."""
        if is_temporal:
            return x_scale_raw(x_values[i]) - band_w / 2.0
        return plot_x0 + band_w * float(i)

    # ── Bar geometry ──
    #
    # Hoisted out of the two Bar arms (Phase 881) because the cap labels have to
    # land on the SAME caps the rectangles draw: one expression per quantity, so
    # a label and its bar cannot disagree about where the bar is. The arithmetic
    # is character-for-character what the arms computed inline before, which is
    # why every golden is unmoved.
    bar_group_w = band_w * 0.7
    stacked_bar_w = _r2(min(bar_group_w * 0.9, _BAR_MAX_THICKNESS))
    grouped_sub_w = bar_group_w / float(m) if m > 0 else bar_group_w
    grouped_bar_w = _r2(min(grouped_sub_w * 0.9, _BAR_MAX_THICKNESS))

    def stacked_bar_x(i: int) -> float:
        return _r2(slot_origin_x(i) + (band_w - stacked_bar_w) / 2.0)

    def grouped_bar_x(i: int, j: int) -> float:
        # Centre the (possibly capped) bar in its own sub-slot, so a cap takes
        # air off BOTH sides and the group stays symmetric about the band centre.
        slot_x = slot_origin_x(i) + (band_w - bar_group_w) / 2.0 + float(j) * grouped_sub_w
        return _r2(slot_x + (grouped_sub_w - grouped_bar_w) / 2.0)

    # ── Series geometry ──
    series_shapes: list[Value] = []
    if spec.kind in ("Bar", "Column") and stacked:
        # One capped bar per category, centred in its band; series stack as
        # segments between consecutive cumulative sums (Phase 637), each
        # shortened by `_STACK_SEGMENT_GAP` on the side facing the next
        # segment so the boundaries read as gaps rather than colour changes.
        bw = stacked_bar_w
        for i in range(n):
            bx = stacked_bar_x(i)
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
                # Phase 883 — a stack SEGMENT's tip carries its OWN series
                # value, never the running total. This is where an interior
                # segment gets its readout: Phase 881 prints the stack TOTAL
                # at the cap and nothing else, and pointed here for the rest.
                seg_style = datum_tip(
                    _with_mark(_style_fill(_colour_for(j)), mark), spec.y_fields[j], categories[i], series[j][i]
                )
                series_shapes.append(_rectangle(bx, top, bw, hgt, None, seg_style))
    elif spec.kind in ("Bar", "Column"):
        bw = grouped_bar_w
        base_y = y_scale(0.0)
        for j in range(m):
            colour = _colour_for(j)
            values = series[j]
            for i in range(n):
                v = values[i]
                bx = grouped_bar_x(i, j)
                vy = y_scale(v)
                top = min(vy, base_y)
                hgt = _r2(abs(vy - base_y))
                mark = f"{spec.y_fields[j]}|{categories[i]}"
                bar_style = datum_tip(_with_mark(_style_fill(colour), mark), spec.y_fields[j], categories[i], v)
                series_shapes.append(_rectangle(bx, top, bw, hgt, None, bar_style))
    elif spec.kind == "Area" and stacked and n > 0:
        # Cumulative bands, bottom band first (painter's order): band j fills
        # between boundary j (below) and boundary j+1 (above); its upper
        # boundary carries the full-strength series edge (Phase 637).
        cum_rows = [cums_for(i) for i in range(n)]
        for j in range(m):
            colour = _colour_for(j)
            yf = spec.y_fields[j]
            upper = [(x_centre(i), y_scale(cum_rows[i][j + 1])) for i in range(n)]
            lower_pts = [(x_centre(i), y_scale(cum_rows[i][j])) for i in range(n - 1, -1, -1)]
            series_shapes.append(
                _polygon(
                    upper + lower_pts,
                    series_tip(_with_mark(_style_fill_opacity(colour, _AREA_FILL_OPACITY), yf), yf),
                )
            )
            series_shapes.append(_polyline(upper, series_tip(_with_mark(_style_stroke(colour, 2.0), yf), yf)))
    elif spec.kind == "Area" and n > 0:
        # Overlaid baseline-closed bands in palette order (painter's order:
        # later series draw over earlier); the translucent fill keeps the
        # overlap legible, the Polyline edge keeps each series distinct.
        base_y = y_scale(0.0)
        for j in range(m):
            colour = _colour_for(j)
            values = series[j]
            yf = spec.y_fields[j]
            points = [(x_centre(i), y_scale(values[i])) for i in range(n)]
            band = [(x_centre(0), base_y), *points, (x_centre(n - 1), base_y)]
            series_shapes.append(
                _polygon(band, series_tip(_with_mark(_style_fill_opacity(colour, _AREA_FILL_OPACITY), yf), yf))
            )
            series_shapes.append(_polyline(points, series_tip(_with_mark(_style_stroke(colour, 2.0), yf), yf)))
    elif spec.kind == "Line":
        for j in range(m):
            colour = _colour_for(j)
            values = series[j]
            points = [(x_centre(i), y_scale(values[i])) for i in range(n)]
            series_shapes.append(
                _polyline(
                    points,
                    series_tip(_with_mark(_style_stroke(colour, 2.0), spec.y_fields[j]), spec.y_fields[j]),
                )
            )
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
                        # The tip's middle part is the x cell as PROJECTED
                        # (``categories[i]``), not the mark id's canonical
                        # numeric form: the id is for object constancy, the
                        # tip is for a human, and on a temporal axis the
                        # projection is the ISO date, not a day count.
                        datum_tip(
                            _with_mark(_style_fill(colour), f"{yf}|{_format_num(x_values[i])}"),
                            yf,
                            categories[i],
                            values[i],
                        ),
                    )
                )

    # ── Data labels (Phase 881) — the values, written selectively ────────────
    #
    # Two states and no third: ``Off`` (the default, and what an absent field
    # means) and ``Ends``. There is deliberately NO all-points mode — a number
    # on every interior point is the clutter this vocabulary exists to prevent,
    # so the API cannot express it. ``Ends`` names the placements that read on
    # their own:
    #
    #   * BARS label the CAP — above a positive cap, below a negative one, the
    #     two exact mirrors about the cap.
    #   * A GROUPED bar labels every bar. A STACKED bar labels the TOTAL at the
    #     stack cap and nothing else: an interior segment's value is unreadable
    #     against the segment above it, and the legend plus the hover readout
    #     already serve it.
    #   * LINES and AREA EDGES label the LAST point of each series, right of the
    #     endpoint and nudged up off the line.
    #   * SCATTER gets nothing in v1 (recorded decision): a scatter's x IS a
    #     value axis, so its last ROW carries no meaning its first does not, and
    #     labelling by row order would present an accident of the feed as a
    #     reading of the chart.
    #   * PIE is unchanged — its legend already carries ``name (NN%)``.
    #
    # Every value goes through ``y_tick_text``, so a label and a tick agree by
    # construction. NO LABEL EVER MOVES A MARGIN: the plot rectangle is decided
    # long before this point, so a label either fits the room the picture
    # already has or it is SUPPRESSED — never clipped, never overlapped, never
    # relocated inside the bar.
    data_labels_on = (spec.data_labels or _DATA_LABEL_MODE_DEFAULT) == "Ends"
    data_label_line = _text_line_height(_DATA_LABEL_FONT_SIZE, _TEXT_LINE_HEIGHT_FACTOR)
    data_label_shapes: list[Value] = []

    def push_data_label(anchor: str, x: float, baseline: float, max_width: float, max_height: float, text: str) -> bool:
        """The single fit gate: ``text_fits_box`` against the room the placement
        actually has. No fit, no label."""
        if not text_fits_box(_DATA_LABEL_FONT_SIZE, _TEXT_LINE_HEIGHT_FACTOR, max_width, max_height, text):
            return False
        # Label-role ink at the chrome opacity — NEVER the series colour: a
        # value is a reading of the mark, not a second copy of its identity.
        data_label_shapes.append(
            _label(
                _r2(x),
                _r2(baseline),
                _literal(text),
                _text_style(_LABEL_OPACITY, anchor, _DATA_LABEL_FONT_SIZE, "Normal"),
            )
        )
        return True

    def push_cap_label(cx: float, pitch: float, v: float) -> None:
        """A value at a bar's cap, centred on ``cx``. ``pitch`` is the distance
        to the NEXT label's centre — the neighbouring bar's slot — so the budget
        is what separates two labels rather than what fits one bar."""
        cap_y = y_scale(v)
        max_width = max(0.0, pitch - 2.0 * _DATA_LABEL_PADDING)
        if v < 0.0:
            push_data_label(
                "Middle",
                cx,
                cap_y + _DATA_LABEL_OFFSET_Y + _DATA_LABEL_FONT_SIZE,
                max_width,
                plot_y1 - cap_y - _DATA_LABEL_OFFSET_Y - _DATA_LABEL_PADDING,
                y_tick_text(v),
            )
        else:
            push_data_label(
                "Middle",
                cx,
                cap_y - _DATA_LABEL_OFFSET_Y,
                max_width,
                cap_y - plot_y0 - _DATA_LABEL_OFFSET_Y - _DATA_LABEL_PADDING,
                y_tick_text(v),
            )

    def push_endpoint_labels(value_at: Callable[[int], float]) -> None:
        """The series-endpoint labels, in series order. Two gates, the second the
        vertical analogue of the cap labels' pitch: every endpoint label shares
        one x, so the thing they collide with is each other. A label is admitted
        only when its line clears every ALREADY-ADMITTED one — series order
        decides who yields, which makes the outcome deterministic."""
        if n == 0:
            return
        label_x = x_centre(n - 1) + _DATA_LABEL_END_OFFSET_X
        # The budget runs to the PLOT's right edge, not the canvas's: beyond it
        # lies the legend column, and running into it is the collision the gate
        # refuses.
        max_width = max(0.0, plot_x1 - label_x - _DATA_LABEL_PADDING)
        admitted: list[float] = []
        for j in range(m):
            v = value_at(j)
            baseline = y_scale(v) - _DATA_LABEL_END_NUDGE_Y
            if not all(abs(b - baseline) >= data_label_line + _DATA_LABEL_PADDING for b in admitted):
                continue
            if push_data_label(
                "Start",
                label_x,
                baseline,
                max_width,
                baseline - plot_y0 - _DATA_LABEL_PADDING,
                y_tick_text(v),
            ):
                admitted.append(baseline)

    if data_labels_on:
        if spec.kind in ("Bar", "Column") and stacked:
            # The TOTAL at the stack cap, once per category.
            for i in range(n):
                push_cap_label(stacked_bar_x(i) + stacked_bar_w / 2.0, band_w, cums_for(i)[m])
        elif spec.kind in ("Bar", "Column"):
            for j in range(m):
                for i in range(n):
                    push_cap_label(grouped_bar_x(i, j) + grouped_bar_w / 2.0, grouped_sub_w, series[j][i])
        elif spec.kind == "Area" and stacked:
            # The band's own UPPER boundary is the edge that was drawn, so it is
            # the cumulative value there — not the series' own datum, which is
            # nowhere on the picture.
            last_cums = cums_for(n - 1) if n > 0 else ()
            push_endpoint_labels(lambda j: last_cums[j + 1])
        elif spec.kind in ("Line", "Area"):
            push_endpoint_labels(lambda j: series[j][n - 1])

    # ── Legend (Phase 880) — one entry list, four placements ──
    #
    # COLUMN (``Right``, the shipped default): one row per entry, each a swatch
    # and its label, the plot already shrunk by the column above. Rows are
    # TOP-ALIGNED with the plot rather than vertically centred, deliberately:
    # centring makes row j's y a function of the entry COUNT, so adding a series
    # moves every row that was already there — chrome sliding under a data
    # refresh is precisely what this module's mark-identity rule exists to
    # avoid, and there is no reason to reintroduce it for the legend. Reading
    # order is also series order, which is the order the rows are in.
    #
    # This is what structurally retires the overflow. A BAND's width is the SUM
    # of its entries, so it runs off the canvas once the names are long enough
    # or numerous enough, silently and with no ellipsis. A COLUMN's width is the
    # MAX of its entries — bounded by ``_LEGEND_COLUMN_MAX_SHARE`` and truncated
    # at it — and its height is one pitch per entry into 400 px of canvas.
    #
    # BAND (``Top`` / ``Bottom``): Phase 879's horizontal row, entries laid out
    # cumulatively from the plot's left edge at each entry's own natural width —
    # unchanged for ``Top``, which is the pre-880 shape every pre-880 golden
    # pins. A band that cannot PACK into the plot's width no longer runs off the
    # edge: ``band_overflows`` above sends the whole legend to the column
    # instead (operator decision, 2026-08-18), so by the time this arm is
    # reached the entries are known to fit.
    #
    # The label styling is one expression for all four: chrome ink at the label
    # opacity, ``Start``-anchored, tick-sized.
    legend_label_style = _text_style(_LABEL_OPACITY, "Start", tick_size, "Normal")

    def legend_row(swatch_x: float, row_top: float, j: int) -> list[Value]:
        return [
            _rectangle(
                _r2(swatch_x),
                _r2(row_top),
                _LEGEND_SWATCH_SIZE,
                _LEGEND_SWATCH_SIZE,
                _LEGEND_SWATCH_CORNER_RADIUS,
                _style_fill(legend_entries[j][0]),
            ),
            _label(
                _r2(swatch_x + _LEGEND_LABEL_OFFSET_X),
                _r2(row_top + _LEGEND_LABEL_BASELINE_DY),
                _literal(legend_texts[j]),
                legend_label_style,
            ),
        ]

    legend: list[Value] = []
    if legend_pos == "Right":
        column_swatch_x = plot_x1 + _LEGEND_COLUMN_GAP
        for j in range(len(legend_entries)):
            legend.extend(legend_row(column_swatch_x, plot_y0 + _LEGEND_ROW_PITCH_Y * float(j), j))
    elif legend_pos != "None":
        # Phase 878 — the TOP band sits BELOW the subtitle, so it moves down by
        # the line the subtitle took; ``subtitle_band`` is 0 without one,
        # leaving the pre-878 constants exactly where they were. The BOTTOM band
        # mirrors from the canvas bottom off the band the margin already
        # reserved, so it needs no constants of its own.
        if legend_pos == "Bottom":
            band_row_top = _H - legend_band_h
            swatch_y = band_row_top
            baseline_y = band_row_top + _LEGEND_LABEL_BASELINE_DY
        else:
            swatch_y = _LEGEND_SWATCH_Y + subtitle_band
            baseline_y = _LEGEND_LABEL_BASELINE_Y + subtitle_band
        lx_acc = plot_x0
        for j in range(len(legend_entries)):
            # The label offsets from the ROUNDED swatch x, exactly as the
            # reference does — rounding the sum instead can differ in the last
            # 2 dp.
            sx = _r2(lx_acc)
            legend.append(
                _rectangle(
                    sx,
                    _r2(swatch_y),
                    _LEGEND_SWATCH_SIZE,
                    _LEGEND_SWATCH_SIZE,
                    _LEGEND_SWATCH_CORNER_RADIUS,
                    _style_fill(legend_entries[j][0]),
                )
            )
            legend.append(
                _label(_r2(sx + _LEGEND_LABEL_OFFSET_X), _r2(baseline_y), _literal(legend_texts[j]), legend_label_style)
            )
            # The same ``band_entry_width`` the overflow rule measured against.
            lx_acc += band_entry_width(legend_texts[j])

    # ── Visible title (a Label — bigger + emphasised) ──
    title_x = _r2(plot_x0)
    title_shapes: list[Value] = []
    if spec.title is not None:
        title_shapes.append(_label(title_x, 22.0, _literal(spec.title), _text_style(None, "Start", title_size, "Loud")))

    # ── Subtitle (Phase 878) — the muted line under the title ──
    #
    # MUTED (label-role opacity, not full-strength ink) and SMALLER than the
    # title, sharing its x and its anchor, so the pair reads as one block and the
    # subtitle is unmistakably subordinate. It draws independently of the title:
    # an author who sets one and not the other gets what they asked for, and the
    # top margin has already reserved the line either way.
    subtitle_shapes: list[Value] = []
    if spec.subtitle is not None:
        subtitle_shapes.append(
            _label(
                title_x,
                _SUBTITLE_BASELINE_Y,
                _literal(bound_text(subtitle_size, plot_w, spec.subtitle)),
                _text_style(_LABEL_OPACITY, "Start", subtitle_size, "Normal"),
            )
        )

    # Pie is polar — no axes/gridlines/tick chrome; every other arm assembles
    # the shared cartesian chrome in painter's order: gridlines (h then v),
    # the zero baseline, axes, tick marks, y-tick + x labels, axis titles,
    # series, legend, chart title. Since Phase 880 BOTH arms take the same
    # ``legend`` in the same slot — geometry, then legend, then titles.
    if spec.kind == "Pie":
        shapes: list[Value] = (
            _pie_shapes(
                spec,
                categories,
                n,
                pie_refused,
                pie_fractions,
                plot_x0,
                plot_x1,
                plot_y0,
                plot_y1,
                pie_values,
                datum_tip,
            )
            + legend
            + title_shapes
            + subtitle_shapes
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
            # Phase 881 — the values sit ON the series, so they are painted
            # straight after it and before the legend.
            + data_label_shapes
            + legend
            + title_shapes
            + subtitle_shapes
        )

    # ── The accessible summary (Phase 921) ───────────────────────────────────
    #
    # The grammar is stated at the section head above and normatively in §4i;
    # this is its four clauses in order. A REFUSED PIE announces nothing, for the
    # reason Phase 880 gave when it stopped emitting the refused pie's legend: a
    # claim about data the drawing declined to show.
    accessible_summary: str | None = None
    if not pie_refused:
        named_series = ", ".join(
            _clamp_text(_SUMMARY_MAX_NAME_CHARS, f) for f in spec.y_fields[:_SUMMARY_MAX_SERIES_NAMED]
        )
        if m == 0:
            series_clause = "no series"
        elif m > _SUMMARY_MAX_SERIES_NAMED:
            series_clause = f"{m} series: {named_series}, and {m - _SUMMARY_MAX_SERIES_NAMED} more"
        else:
            series_clause = f"{m} series: {named_series}"

        # The extent clause follows the X AXIS's own kind, not the chart's: a
        # band axis states its first and last category, a continuous axis its
        # domain endpoints through that axis's own tick formatter.
        if is_continuous_x:
            if n == 0:
                extent_clause = "no points"
            else:
                head = "1 point: " if n == 1 else f"{n} points: "
                extent_clause = f"{head}{x_tick_text(x_nice_lo)} to {x_tick_text(x_nice_hi)}"
        elif n == 0:
            extent_clause = "no categories"
        elif n == 1:
            extent_clause = f"1 category: {_clamp_text(_SUMMARY_MAX_NAME_CHARS, categories[0])}"
        else:
            first = _clamp_text(_SUMMARY_MAX_NAME_CHARS, categories[0])
            last = _clamp_text(_SUMMARY_MAX_NAME_CHARS, categories[n - 1])
            extent_clause = f"{n} categories: {first} to {last}"

        clauses = [_summary_kind_words(spec.kind, stacked), series_clause, extent_clause]

        # The peak is the largest SINGLE DATUM — never a stacked total, because
        # the clause names one series at one category and a total belongs to
        # neither. Ties resolve to the earliest category then the earliest series
        # (a strict ``>`` scanned category-major), which is the axis's own
        # reading order. The number takes the value axis's rendering (the
        # Phase-876 formatter at the axis's step precision, plus the axis's
        # display unit in its own words); the category is the datum's OWN label,
        # verbatim, even on a temporal axis.
        if n > 0 and m > 0:
            bi = bj = 0
            bv = series[0][0]
            for i in range(n):
                for j in range(m):
                    if series[j][i] > bv:
                        bv, bi, bj = series[j][i], i, j
            unit_suffix = "" if y_unit_label == "" else f" {y_unit_label}"
            peak_series = _clamp_text(_SUMMARY_MAX_NAME_CHARS, spec.y_fields[bj])
            peak_category = _clamp_text(_SUMMARY_MAX_NAME_CHARS, categories[bi])
            clauses.append(f"Peak {peak_series} at {peak_category}, {y_tick_text(bv)}{unit_suffix}")

        accessible_summary = _clamp_text(_SUMMARY_MAX_CHARS, _SUMMARY_CLAUSE_SEPARATOR.join(clauses) + ".")

    kind_fields: dict[str, Value] = {
        "viewBox": Obj(None, {"minX": 0.0, "minY": 0.0, "width": _W, "height": _H}),
        "shapes": Arr(shapes),
        "style": Obj(None, {}),
    }
    if spec.title is not None:
        kind_fields["title"] = _literal(spec.title)
    if accessible_summary is not None:
        kind_fields["description"] = _literal(accessible_summary)
    return Obj("Drawing", kind_fields)


def _pie_shapes(  # noqa: PLR0914
    spec: ChartSpec,
    categories: list[str],
    n: int,
    refused: bool,
    fractions: list[float],
    plot_x0: float,
    plot_x1: float,
    plot_y0: float,
    plot_y1: float,
    values: list[float],
    datum_tip: Callable[[Obj, str, str, float], Obj],
) -> list[Value]:
    """The Pie arm (Phase 638) — polar, cubic-approximated wedges.

    Bounded v1: exactly ONE series (multi-series pie is a grounded-validation
    refusal upstream, never a silent first-series truncation) and non-negative
    values (any negative refuses the geometry). Zero-value categories draw no
    wedge but keep their legend row. Wedges start at 12 o'clock and sweep
    clockwise; arcs are the standard <=90-degree-segment cubic-Bezier
    approximation (the closed `CurveCommand` vocabulary has no arc case,
    deliberately). A lone 100% category degenerates to a `Circle`. Category
    share reads in the legend ("name (NN%)").

    Phase 880 — this emits WEDGES ONLY. The pie's legend was the vertical
    right-hand column the cartesian arms have now converged on, so it is emitted
    by the shared legend in :func:`lower` (from the shared entry list, which
    carries the shares) and honours ``legend_position`` like any other arm. The
    refusal guard and the shares themselves are computed by the caller, above
    the margins, because the legend's width is layout input."""
    if refused:
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
            # The wedge's own VALUE, not its share. The share is already
            # stated, once, in the legend entry (``name (NN%)``); restating it
            # here would leave the magnitude behind the slice the one number
            # still unreachable.
            mark_style = datum_tip(
                _with_mark(_style_fill(colour), f"{yf}|{categories[i]}"), yf, categories[i], values[i]
            )
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

    return segs


def lower_node(node_id: str, spec: ChartSpec, rows: Sequence[Mapping[str, object]]) -> Node:
    """Lower + wrap the ``Drawing`` kind in a node envelope (id + kind)."""
    return Node(id=node_id, kind=lower(spec, rows))
