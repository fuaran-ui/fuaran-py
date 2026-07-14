"""Render-time ``Chart`` → ``Drawing`` lowering (the Python host of the S4 parity leg).

``Chart`` stays a **semantic** wire kind; this module is the bounded layout engine
that turns a resolved chart spec + data rows into a canonical ``Drawing`` subtree
(scales, ticks, axes, gridlines, legend, series geometry) — so a chart renders as
first-party inline SVG on every host, headless included, and a new chart type is a
lowering rule + fixtures rather than bespoke per-host drawing.

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
_MARGIN_BOTTOM = 56.0  # x-axis category labels + x-axis title
_MARGIN_LEFT = 64.0  # right-aligned y-axis tick labels

_PLOT_X0 = _MARGIN_LEFT
_PLOT_X1 = _W - _MARGIN_RIGHT
_PLOT_Y0 = _MARGIN_TOP
_PLOT_Y1 = _H - _MARGIN_BOTTOM
_PLOT_W = _PLOT_X1 - _PLOT_X0
_PLOT_H = _PLOT_Y1 - _PLOT_Y0

# A fixed, deterministic categorical palette (series index → colour).
_PALETTE = ("#3366cc", "#dc3912", "#ff9900", "#109618", "#990099", "#0099c6")


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


# ── Deterministic numeric helpers ────────────────────────────────────────────


def _r2(x: float) -> float:
    """Round-half-up to 2 dp — the single deterministic rule every host reproduces."""
    return math.floor(x * 100.0 + 0.5) / 100.0


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


def _nice_domain(lo: float, hi: float) -> tuple[float, float, list[float]]:
    """A nice value domain + its tick values for ``[lo, hi]``, targeting ~5 ticks."""
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
    return nice_lo, nice_hi, ticks


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


def _tick_label(v: float) -> str:
    return _format_num(_r2(v))


# ── DrawStyle builders (untagged style objects; only Some fields emitted) ─────


def _static(value: Value) -> Obj:
    return Obj("Static", {"value": value})


def _style_fill(fill: str) -> Obj:
    return Obj(None, {"fill": _static(fill)})


def _style_stroke(stroke: str, width: float) -> Obj:
    return Obj(None, {"stroke": _static(stroke), "strokeWidth": _static(width)})


def _style_stroke_ink(opacity: float, width: float) -> Obj:
    """Surface-relative structural stroke (``currentColor`` at a per-role opacity)."""
    return Obj(None, {"stroke": _static(_INK), "strokeWidth": _static(width), "opacity": _static(opacity)})


def _text_style(opacity: float | None, anchor: str, size: float, emphasis: str) -> Obj:
    """Surface-relative text-label style: ``currentColor`` + optional per-role opacity."""
    fields: dict[str, Value] = {
        "fill": _static(_INK),
        "textAnchor": anchor,
        "fontSize": size,
        "emphasis": emphasis,
        "fontFamily": _CHART_FONT,
    }
    if opacity is not None:
        fields["opacity"] = _static(opacity)
    return Obj(None, fields)


def _literal(text: str) -> Obj:
    return Obj("Literal", {"text": text})


# ── Shape builders (tagged ``$type`` objects) ────────────────────────────────


def _line(x1: float, y1: float, x2: float, y2: float, style: Obj) -> Obj:
    return Obj("Line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "style": style})


def _rectangle(x: float, y: float, w: float, h: float, corner: float | None, style: Obj) -> Obj:
    fields: dict[str, Value] = {"x": x, "y": y, "width": w, "height": h, "style": style}
    if corner is not None:
        fields["cornerRadius"] = corner
    return Obj("Rectangle", fields)


def _label(x: float, y: float, text: Obj, style: Obj) -> Obj:
    return Obj("Label", {"x": x, "y": y, "text": text, "style": style})


def _polyline(points: list[tuple[float, float]], style: Obj) -> Obj:
    pts = Arr([Obj(None, {"x": px, "y": py}) for px, py in points])
    return Obj("Polyline", {"points": pts, "style": style})


# ── The chart spec (the neutral cross-host lowering input) ────────────────────


@dataclass(frozen=True)
class ChartSpec:
    """The resolved chart layout inputs — the neutral lowering contract.

    Mirrors the F# ``ChartSpec`` fields the lowering reads: ``kind`` (only
    ``Bar``/``Column``/``Line`` are lowered), the ``x_field`` category column, the
    ``y_fields`` series columns, and an optional literal ``title``. ``stacked`` is
    carried for parity but does not affect the S3 layout.
    """

    kind: str
    x_field: str
    y_fields: tuple[str, ...]
    title: str | None = None
    stacked: bool = field(default=False)


# ── Row field extraction ─────────────────────────────────────────────────────


def _row_get(row: Mapping[str, object], field_name: str) -> object:
    return row.get(field_name)


def _numeric_of(row: Mapping[str, object], field_name: str) -> float:
    v = _row_get(row, field_name)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
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


def lower(spec: ChartSpec, rows: Sequence[Mapping[str, object]]) -> Obj:
    """Lower a resolved ``ChartSpec`` + data rows to a canonical ``Drawing`` kind.

    Returns the ``Drawing`` kind object (``$type = "Drawing"``); wrap it in a node
    with :func:`lower_node`. Only ``Bar``/``Column`` and ``Line`` are lowered (S3);
    any other kind produces an empty drawing.
    """
    rows = list(rows)
    categories = [_string_of(r, spec.x_field) for r in rows]
    n = len(rows)

    series = [[_numeric_of(r, yf) for r in rows] for yf in spec.y_fields]
    m = len(series)

    all_values = [v for s in series for v in s] or [0.0]
    data_min = min(all_values)
    data_max = max(all_values)
    # Bars + lines share a zero-anchored domain — deterministic + honest for bars.
    nice_lo, nice_hi, ticks = _nice_domain(min(0.0, data_min), max(0.0, data_max))

    def y_scale(v: float) -> float:
        return _r2(_PLOT_Y1 - (v - nice_lo) / (nice_hi - nice_lo) * _PLOT_H)

    band_w = _PLOT_W / float(n) if n > 0 else _PLOT_W

    def centre_x(i: int) -> float:
        return _r2(_PLOT_X0 + band_w * (float(i) + 0.5))

    tick_size = 13.0
    title_size = 16.0

    shapes: list[Value] = []

    # ── Gridlines (drawn first, painter's order) ──
    for t in ticks:
        y = y_scale(t)
        shapes.append(_line(_r2(_PLOT_X0), y, _r2(_PLOT_X1), y, _style_stroke_ink(_GRID_OPACITY, 1.0)))

    # ── Axes ──
    shapes.append(
        _line(_r2(_PLOT_X0), _r2(_PLOT_Y0), _r2(_PLOT_X0), _r2(_PLOT_Y1), _style_stroke_ink(_AXIS_OPACITY, 1.0))
    )
    shapes.append(
        _line(_r2(_PLOT_X0), _r2(_PLOT_Y1), _r2(_PLOT_X1), _r2(_PLOT_Y1), _style_stroke_ink(_AXIS_OPACITY, 1.0))
    )

    # ── y-axis tick labels — right-anchored (End) in the left margin ──
    for t in ticks:
        shapes.append(
            _label(
                _r2(_PLOT_X0 - 8.0),
                _r2(y_scale(t) + 4.0),
                _literal(_tick_label(t)),
                _text_style(_LABEL_OPACITY, "End", tick_size, "Normal"),
            )
        )

    # ── x-axis category labels — centred (Middle) under each band centre ──
    for i, c in enumerate(categories):
        shapes.append(
            _label(
                centre_x(i),
                _r2(_PLOT_Y1 + 20.0),
                _literal(c),
                _text_style(_LABEL_OPACITY, "Middle", tick_size, "Normal"),
            )
        )

    # ── Axis titles (a name on both axes) ──
    shapes.append(
        _label(
            _r2((_PLOT_X0 + _PLOT_X1) / 2.0),
            _r2(_H - 12.0),
            _literal(_capitalise(spec.x_field)),
            _text_style(None, "Middle", tick_size, "Normal"),
        )
    )
    shapes.append(
        _label(
            _r2(8.0),
            _r2(_PLOT_Y0 - 12.0),
            _literal("Value"),
            _text_style(None, "Start", tick_size, "Normal"),
        )
    )

    # ── Series geometry ──
    if spec.kind in ("Bar", "Column"):
        group_w = band_w * 0.7
        sub_w = group_w / float(m) if m > 0 else group_w
        base_y = y_scale(0.0)
        for j in range(m):
            colour = _colour_for(j)
            values = series[j]
            for i in range(n):
                v = values[i]
                bx = _r2(_PLOT_X0 + band_w * float(i) + (band_w - group_w) / 2.0 + float(j) * sub_w)
                bw = _r2(sub_w * 0.9)
                vy = y_scale(v)
                top = min(vy, base_y)
                hgt = _r2(abs(vy - base_y))
                shapes.append(_rectangle(bx, top, bw, hgt, None, _style_fill(colour)))
    elif spec.kind == "Line":
        for j in range(m):
            colour = _colour_for(j)
            values = series[j]
            points = [(centre_x(i), y_scale(values[i])) for i in range(n)]
            shapes.append(_polyline(points, _style_stroke(colour, 2.0)))

    # ── Legend (only when >1 series) — a swatch + series name per series ──
    if m > 1:
        for j in range(m):
            colour = _colour_for(j)
            lx = _r2(_PLOT_X0 + float(j) * 100.0)
            shapes.append(_rectangle(lx, 34.0, 10.0, 10.0, 2.0, _style_fill(colour)))
            shapes.append(
                _label(
                    _r2(lx + 15.0),
                    43.0,
                    _literal(spec.y_fields[j]),
                    _text_style(_LABEL_OPACITY, "Start", tick_size, "Normal"),
                )
            )

    # ── Visible title (a Label — bigger + emphasised) ──
    if spec.title is not None:
        shapes.append(_label(_r2(_PLOT_X0), 22.0, _literal(spec.title), _text_style(None, "Start", title_size, "Loud")))

    kind_fields: dict[str, Value] = {
        "viewBox": Obj(None, {"minX": 0.0, "minY": 0.0, "width": _W, "height": _H}),
        "shapes": Arr(shapes),
        "style": Obj(None, {}),
    }
    if spec.title is not None:
        kind_fields["title"] = _literal(spec.title)
    return Obj("Drawing", kind_fields)


def lower_node(node_id: str, spec: ChartSpec, rows: Sequence[Mapping[str, object]]) -> Node:
    """Lower + wrap the ``Drawing`` kind in a node envelope (id + kind)."""
    return Node(id=node_id, kind=lower(spec, rows))
