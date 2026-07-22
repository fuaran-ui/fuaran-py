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


# A translucent categorical fill (Phase 637 — area bands). The gridlines stay
# legible through the band; the series' full-strength Polyline edge on top
# carries the categorical colour at full contrast.
_AREA_FILL_OPACITY = 0.35


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
    nice_lo, nice_hi, ticks = _nice_domain(min(0.0, data_min), max(0.0, data_max))

    def y_scale(v: float) -> float:
        return _r2(_PLOT_Y1 - (v - nice_lo) / (nice_hi - nice_lo) * _PLOT_H)

    band_w = _PLOT_W / float(n) if n > 0 else _PLOT_W

    def centre_x(i: int) -> float:
        return _r2(_PLOT_X0 + band_w * (float(i) + 0.5))

    # ── Linear x-scale (Phase 636 — the Scatter arm's numeric x axis) ──
    # Scatter reads the x-field NUMERICALLY and plots on a linear x-domain (the
    # first non-band x-scale arm). The domain is NOT zero-anchored — a scatter's
    # x range carries no baseline semantics (the y domain stays zero-anchored
    # with the other arms, deliberately: one shared y-domain rule).
    is_scatter = spec.kind == "Scatter"
    x_values = [_numeric_of(r, spec.x_field) for r in rows] if is_scatter else []
    if is_scatter:
        if x_values:
            x_nice_lo, x_nice_hi, x_ticks = _nice_domain(min(x_values), max(x_values))
        else:
            x_nice_lo, x_nice_hi, x_ticks = _nice_domain(0.0, 1.0)
    else:
        x_nice_lo, x_nice_hi, x_ticks = 0.0, 1.0, []

    def x_scale(v: float) -> float:
        return _r2(_PLOT_X0 + (v - x_nice_lo) / (x_nice_hi - x_nice_lo) * _PLOT_W)

    tick_size = 13.0
    title_size = 16.0

    # ── Cartesian chrome (painter's order pieces) ──
    gridlines: list[Value] = [
        _line(_r2(_PLOT_X0), y_scale(t), _r2(_PLOT_X1), y_scale(t), _style_stroke_ink(_GRID_OPACITY, 1.0))
        for t in ticks
    ]
    axes: list[Value] = [
        _line(_r2(_PLOT_X0), _r2(_PLOT_Y0), _r2(_PLOT_X0), _r2(_PLOT_Y1), _style_stroke_ink(_AXIS_OPACITY, 1.0)),
        _line(_r2(_PLOT_X0), _r2(_PLOT_Y1), _r2(_PLOT_X1), _r2(_PLOT_Y1), _style_stroke_ink(_AXIS_OPACITY, 1.0)),
    ]

    # y-axis tick labels — right-anchored (End) in the left margin.
    y_tick_labels: list[Value] = [
        _label(
            _r2(_PLOT_X0 - 8.0),
            _r2(y_scale(t) + 4.0),
            _literal(_tick_label(t)),
            _text_style(_LABEL_OPACITY, "End", tick_size, "Normal"),
        )
        for t in ticks
    ]

    # x-axis labels — band arms label each category under its band centre;
    # Scatter labels its numeric x-ticks along the linear axis (Phase 636).
    if is_scatter:
        x_labels: list[Value] = [
            _label(
                x_scale(t),
                _r2(_PLOT_Y1 + 20.0),
                _literal(_tick_label(t)),
                _text_style(_LABEL_OPACITY, "Middle", tick_size, "Normal"),
            )
            for t in x_ticks
        ]
    else:
        x_labels = [
            _label(
                centre_x(i),
                _r2(_PLOT_Y1 + 20.0),
                _literal(c),
                _text_style(_LABEL_OPACITY, "Middle", tick_size, "Normal"),
            )
            for i, c in enumerate(categories)
        ]

    # ── Axis titles (a name on both axes) ──
    axis_titles: list[Value] = [
        _label(
            _r2((_PLOT_X0 + _PLOT_X1) / 2.0),
            _r2(_H - 12.0),
            _literal(_capitalise(spec.x_field)),
            _text_style(None, "Middle", tick_size, "Normal"),
        ),
        _label(
            _r2(8.0),
            _r2(_PLOT_Y0 - 12.0),
            _literal("Value"),
            _text_style(None, "Start", tick_size, "Normal"),
        ),
    ]

    # ── Series geometry ──
    series_shapes: list[Value] = []
    if spec.kind in ("Bar", "Column") and stacked:
        # One full group-width bar per category; series stack as segments
        # between consecutive cumulative sums (Phase 637).
        group_w = band_w * 0.7
        for i in range(n):
            bx = _r2(_PLOT_X0 + band_w * float(i) + (band_w - group_w) / 2.0)
            bw = _r2(group_w * 0.9)
            cums = cums_for(i)
            for j in range(m):
                y0 = y_scale(cums[j])
                y1 = y_scale(cums[j + 1])
                top = min(y0, y1)
                hgt = _r2(abs(y1 - y0))
                mark = f"{spec.y_fields[j]}|{categories[i]}"
                series_shapes.append(_rectangle(bx, top, bw, hgt, None, _with_mark(_style_fill(_colour_for(j)), mark)))
    elif spec.kind in ("Bar", "Column"):
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
    legend: list[Value] = []
    if m > 1:
        for j in range(m):
            colour = _colour_for(j)
            lx = _r2(_PLOT_X0 + float(j) * 100.0)
            legend.append(_rectangle(lx, 34.0, 10.0, 10.0, 2.0, _style_fill(colour)))
            legend.append(
                _label(
                    _r2(lx + 15.0),
                    43.0,
                    _literal(spec.y_fields[j]),
                    _text_style(_LABEL_OPACITY, "Start", tick_size, "Normal"),
                )
            )

    # ── Visible title (a Label — bigger + emphasised) ──
    title_shapes: list[Value] = []
    if spec.title is not None:
        title_shapes.append(
            _label(_r2(_PLOT_X0), 22.0, _literal(spec.title), _text_style(None, "Start", title_size, "Loud"))
        )

    # Pie is polar — no axes/gridlines/tick chrome; every other arm assembles
    # the shared cartesian chrome in painter's order: gridlines, axes, y-tick +
    # x labels, axis titles, series, legend, chart title.
    if spec.kind == "Pie":
        shapes: list[Value] = _pie_shapes(spec, series, categories, n, m) + title_shapes
    else:
        shapes = gridlines + axes + y_tick_labels + x_labels + axis_titles + series_shapes + legend + title_shapes

    kind_fields: dict[str, Value] = {
        "viewBox": Obj(None, {"minX": 0.0, "minY": 0.0, "width": _W, "height": _H}),
        "shapes": Arr(shapes),
        "style": Obj(None, {}),
    }
    if spec.title is not None:
        kind_fields["title"] = _literal(spec.title)
    return Obj("Drawing", kind_fields)


def _pie_shapes(  # noqa: PLR0914
    spec: ChartSpec, series: list[list[float]], categories: list[str], n: int, m: int
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
    tick_size = 13.0
    values = series[0] if m == 1 else []
    refused = m != 1 or any(v < 0.0 for v in values)
    total = sum(values)
    if refused or total <= 0.0:
        return []

    cx = _r2((_PLOT_X0 + _PLOT_X1) / 2.0)
    cy = _r2((_PLOT_Y0 + _PLOT_Y1) / 2.0)
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

    yf = spec.y_fields[0]
    segs: list[Value] = []
    for i in range(n):
        f = fractions[i]
        if f > 0.0:
            colour = _colour_for(i)
            mark_style = _with_mark(_style_fill(colour), f"{yf}|{categories[i]}")
            if f >= 1.0 - 1e-9:
                segs.append(_circle(cx, cy, radius, mark_style))
            else:
                a0 = top + 2.0 * math.pi * starts[i]
                a1 = top + 2.0 * math.pi * starts[i + 1]
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
        pct = _format_num(math.floor(fractions[i] * 100.0 + 0.5))
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
