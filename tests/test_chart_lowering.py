"""Chart → Drawing lowering — cross-host byte-parity (Phase 534, S4).

The Python lowering (:mod:`fuaran_py.charts`) must reproduce the shared
``wire-format-fixtures/chart-lowering/*`` goldens byte-for-byte — the same
fixtures the F# reference (``Fuaran.UI.Charts.lower``) and the TypeScript host
certify against. Each case ships an ``<name>.input.json`` (the neutral ChartSpec
+ data contract) and an ``<name>.expected.json`` (the canonical themed Drawing
node JSON). Skipped when the corpus is absent (a standalone checkout), mirroring
the render-parity pattern.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT, corpus_available

# The Phase-882 calendar is a NORMATIVE cross-host spec (§4h), so its properties
# are asserted directly rather than inferred from pixel positions — which means
# reaching for the module-private helpers that implement it.
from fuaran_py.charts import (
    ChartSpec,
    _choose_temporal_step,
    _civil_from_days,
    _day_of,
    _days_from_civil,
    _is_leap_year,
    _nominal_days,
    _temporal_domain,
    _temporal_label,
    _temporal_ticks,
    _TemporalStep,
    _trunc_div,
    _try_parse_day,
    lower,
    lower_node,
)
from fuaran_py.model import Arr, Obj
from fuaran_py.schema.encode import encode_node

_CHART_LOWERING_DIR = CORPUS_ROOT / "chart-lowering"


def _cases() -> list[str]:
    if not corpus_available() or not _CHART_LOWERING_DIR.is_dir():
        return []
    return sorted(p.name[: -len(".input.json")] for p in _CHART_LOWERING_DIR.glob("*.input.json"))


def _text_source(raw: object) -> object | None:
    """The corpus carries a ``TextSource`` in canonical wire JSON; the lowering
    takes this host's ``Value`` model. Every arm crosses the lowering unresolved
    (Phase 1143), so all three decode here; a binding arm no fixture uses raises
    rather than being invented, exactly as ``valueFormat`` refuses one.
    """
    if raw is None or isinstance(raw, str):
        # The bare string IS the canonical Literal form (WIRE_FORMAT 16).
        return raw
    assert isinstance(raw, dict), raw
    tag = raw.get("$type")
    if tag == "Literal":
        return raw["text"]
    if tag == "Bound":
        binding = raw["binding"]
        assert binding.get("$type") == "Static", f"unsupported Bound binding {binding.get('$type')}"
        return Obj("Bound", {"binding": Obj("Static", {"value": binding["value"]})})
    if tag == "I18n":
        return Obj("I18n", {"args": Obj(None, dict(raw.get("args") or {})), "key": raw["key"]})
    raise AssertionError(f"chart-lowering input: unsupported TextSource {tag}")


def _spec_and_rows(inp: dict) -> tuple[ChartSpec, list[dict]]:
    # Phase 876 — `valueFormat` is a WIRE field carried in canonical `Format`
    # JSON; `axisUnitMode` is a harness-only STYLE selector (the chart style is
    # a lowering parameter, never wire), present so the corpus can pin every
    # mode. Both absent on the pre-876 cases.
    #
    # Phase 878 — `xTitle` / `yTitle` / `subtitle` are WIRE fields carried in the
    # same `TextSource` vocabulary as `title`, omitted when the author declared
    # none. Absent on every pre-878 case. Phase 1143 — every ARM crosses, so the
    # corpus spells `Bound` and `I18n` here too and `_text_source` decodes them.
    #
    # Phase 880 — `legendPosition` is a WIRE field carried as the bare enum name
    # (`Top | Right | Bottom | None`). ABSENT means the host default (`Right`),
    # never "no legend", so it is omitted on every pre-880 case even though the
    # picture many of them lower to moved when the default did.
    #
    # Phase 881 — `dataLabels` is a WIRE field carried as the bare enum name
    # (`Off | Ends`). ABSENT means `Off`, which is also the default, so it is
    # omitted on every pre-881 case AND every pre-881 golden is unchanged.
    #
    # Phase 882 — `xScale` is a WIRE field carried as the bare enum name
    # (`Category | Temporal`). ABSENT means `Category`, which is also the
    # default, so it is omitted on every pre-882 case AND every pre-882 golden
    # is unchanged — not one `.expected.json` was rewritten by the phase.
    spec = ChartSpec(
        kind=inp["kind"],
        x_field=inp["xField"],
        y_fields=tuple(inp["yFields"]),
        title=_text_source(inp.get("title")),
        stacked=bool(inp.get("stacked", False)),
        value_format=inp.get("valueFormat"),
        axis_unit_mode=inp.get("axisUnitMode", "Words"),
        x_title=_text_source(inp.get("xTitle")),
        y_title=_text_source(inp.get("yTitle")),
        subtitle=_text_source(inp.get("subtitle")),
        legend_position=inp.get("legendPosition"),
        data_labels=inp.get("dataLabels"),
        x_scale=inp.get("xScale"),
    )
    return spec, list(inp["data"])


_cases_available = pytest.mark.skipif(
    not _cases(),
    reason=f"chart-lowering fixtures not found at {_CHART_LOWERING_DIR}",
)


@_cases_available
@pytest.mark.parametrize("name", _cases())
def test_lowers_byte_identical_to_golden(name: str) -> None:
    inp = json.loads((_CHART_LOWERING_DIR / f"{name}.input.json").read_text(encoding="utf-8"))
    expected = (_CHART_LOWERING_DIR / f"{name}.expected.json").read_text(encoding="utf-8")
    spec, rows = _spec_and_rows(inp)
    got = encode_node(lower_node(f"chart-{name}", spec, rows))
    assert got == expected, f"{name}: lowering drifted from golden"


@_cases_available
@pytest.mark.parametrize("name", _cases())
def test_lowering_is_order_independent(name: str) -> None:
    # The lowering reads row fields by name, so a reversed field-insertion order
    # must produce an identical Drawing.
    inp = json.loads((_CHART_LOWERING_DIR / f"{name}.input.json").read_text(encoding="utf-8"))
    spec, rows = _spec_and_rows(inp)
    reversed_rows = [dict(reversed(list(r.items()))) for r in rows]
    a = encode_node(lower_node("c", spec, rows))
    b = encode_node(lower_node("c", spec, reversed_rows))
    assert a == b, f"{name}: field-order-dependent"


def test_headless_chart_renders_real_inline_svg() -> None:
    # A Chart node with resolved embedded rows renders as first-party inline SVG
    # (via the lowering), not the client-hydration placeholder (Phase 534 wiring).
    from fuaran_py.model import Arr, Node, Obj
    from fuaran_py.renderer import render_html

    rows = Arr(
        [
            Obj(None, {"quarter": "Q1", "revenue": 120}),
            Obj(None, {"quarter": "Q2", "revenue": 150}),
        ]
    )
    chart = Node(
        id="chart-demo",
        kind=Obj(
            "Chart",
            {
                "kind": "Bar",
                "xField": "quarter",
                "yFields": Arr(["revenue"]),
                "title": Obj("Literal", {"text": "Revenue by quarter"}),
                "stacked": False,
                "source": Obj("Static", {"value": rows}),
            },
        ),
    )
    html = render_html(chart)
    assert "<svg" in html
    assert "fuaran-drawing" in html
    assert "ssr-placeholder" not in html
    # A bar rectangle from the series geometry made it into the SVG.
    assert "#1a86ac" in html


# ── SSR bridge coverage (the wire-node → ChartSpec seam) ─────────────────────
# The parametrized goldens above exercise the LOWERING with a hand-built
# ChartSpec; these pin the other half — that the renderer's `_lower_chart`
# bridge actually carries the declared wire fields (`valueFormat`, `xTitle`,
# `yTitle`, `subtitle`) into the spec, so a dropped field cannot silently
# regress to the lowering's defaults again (the Phase 876/878 SSR gap).

_SSR_NODE_FIXTURES: dict[str, tuple[str, ...]] = {
    # Phase 876 — Currency GBP: ticks carry the symbol only when the declared
    # format crossed the bridge (unformatted ticks are bare numbers).
    "chart-value-format": ("£",),
    # Phase 878 — the subtitle draws only when declared (it has no fallback).
    "chart-axis-titles": ("Millions of £",),
}


@pytest.mark.parametrize("name", sorted(_SSR_NODE_FIXTURES))
def test_ssr_bridge_carries_declared_wire_fields(name: str) -> None:
    from fuaran_py import decode_node
    from fuaran_py.renderer import render_html

    path = CORPUS_ROOT / "nodes" / f"{name}.json"
    if not path.is_file():
        pytest.skip(f"corpus fixture nodes/{name}.json not found under {CORPUS_ROOT}")
    result = decode_node(path.read_text(encoding="utf-8"))
    assert result.ok, getattr(result, "error", result)
    html = render_html(result.value)
    assert "<svg" in html and "ssr-placeholder" not in html
    for needle in _SSR_NODE_FIXTURES[name]:
        assert needle in html, f"{name}: declared wire field did not reach the SVG ({needle!r})"


def test_ssr_bridge_passes_all_declared_chart_fields() -> None:
    # Discriminating values: the axis titles DIFFER from the capitalised
    # field-name fallbacks and the format is Percent, so each assertion fails
    # individually if its field is dropped by the bridge (a corpus fixture
    # whose declared titles coincide with the fallbacks cannot catch that).
    from fuaran_py import decode_node
    from fuaran_py.renderer import render_html

    wire = json.dumps(
        {
            "id": "chart-bridge",
            "kind": {
                "$type": "Chart",
                "kind": "Bar",
                "xField": "quarter",
                "yFields": ["share"],
                "stacked": False,
                "title": "Market share",
                "subtitle": "Share of segment",
                "xTitle": "Fiscal quarter",
                "yTitle": "Segment share",
                "valueFormat": {"$type": "Percent"},
                "source": {
                    "$type": "Static",
                    "value": [
                        {"quarter": "Q1", "share": 0.42},
                        {"quarter": "Q2", "share": 0.55},
                    ],
                },
            },
        }
    )
    result = decode_node(wire)
    assert result.ok, getattr(result, "error", result)
    html = render_html(result.value)
    assert "<svg" in html and "ssr-placeholder" not in html
    assert "Fiscal quarter" in html  # x_title (fallback would be "Quarter")
    assert "Segment share" in html  # y_title (fallback would be "Share")
    assert "Share of segment" in html  # subtitle (absent without the bridge)
    assert ">0%<" in html  # value_format Percent (a bare "0" tick without it)


def test_ssr_bridge_carries_non_literal_text_sources() -> None:
    # Phase 1143 — the four TextSource-typed fields cross the bridge UNRESOLVED
    # and resolve at RENDER time. Until 1143 this bridge kept the LITERAL arm
    # only: a Bound title vanished from the picture entirely and a Bound axis
    # name was silently replaced by the capitalised column name, which is what
    # makes the second assertion below the discriminating one — the fallback
    # standing IS the old behaviour, and it reads as a perfectly ordinary chart.
    from fuaran_py import decode_node
    from fuaran_py.renderer import render_html

    bound = {"$type": "Bound", "binding": {"$type": "Static", "value": "Resolved at render time"}}
    wire = json.dumps(
        {
            "id": "chart-bound-titles",
            "kind": {
                "$type": "Chart",
                "kind": "Bar",
                "xField": "quarter",
                "yFields": ["share"],
                "stacked": False,
                "title": bound,
                "xTitle": {
                    "$type": "Bound",
                    "binding": {"$type": "Static", "value": "Bound axis name"},
                },
                "source": {
                    "$type": "Static",
                    "value": [{"quarter": "Q1", "share": 0.42}, {"quarter": "Q2", "share": 0.55}],
                },
            },
        }
    )
    result = decode_node(wire)
    assert result.ok, getattr(result, "error", result)
    html = render_html(result.value)
    assert "<svg" in html and "ssr-placeholder" not in html
    assert "Resolved at render time" in html, "a Bound title was dropped at the bridge"
    assert "Bound axis name" in html, "a Bound axis title was dropped at the bridge"
    # The capitalised-field-name fallback answers ABSENCE only: a declared arm
    # is never substituted for because the bridge could not resolve it.
    assert ">Quarter<" not in html


def test_ssr_bridge_passes_legend_position() -> None:
    # Phase 880. Two halves, each discriminating against the default (`Right`):
    # the corpus fixture declares `Bottom`, so its render must DIFFER from the
    # same node with the declaration stripped; and an explicit `"None"`
    # suppresses the legend, so the series labels a two-series default legend
    # would draw must be absent.
    from fuaran_py import decode_node
    from fuaran_py.renderer import render_html

    def render_wire(wire: str) -> str:
        result = decode_node(wire)
        assert result.ok, getattr(result, "error", result)
        html = render_html(result.value)
        assert "<svg" in html and "ssr-placeholder" not in html
        return html

    fixture = CORPUS_ROOT / "nodes" / "chart-legend-position.json"
    if fixture.is_file():
        declared = json.loads(fixture.read_text(encoding="utf-8"))
        assert declared["kind"].pop("legendPosition") == "Bottom"
        stripped_html = render_wire(json.dumps(declared))
        declared_html = render_wire(fixture.read_text(encoding="utf-8"))
        assert declared_html != stripped_html, "declared legendPosition did not move the legend"

    none_wire = json.dumps(
        {
            "id": "chart-legend-none",
            "kind": {
                "$type": "Chart",
                "kind": "Bar",
                "xField": "region",
                "yFields": ["alpha_series", "beta_series"],
                "stacked": False,
                "legendPosition": "None",
                "source": {
                    "$type": "Static",
                    "value": [
                        {"region": "North", "alpha_series": 80, "beta_series": 100},
                        {"region": "South", "alpha_series": 130, "beta_series": 110},
                    ],
                },
            },
        }
    )
    # The legend's labels are the raw series names AS TEXT CONTENT (`>name<`);
    # the series geometry also carries them in `data-fuaran-mark` attributes,
    # so the text-node form is the discriminator. Positive control first: the
    # default legend draws them, so their disappearance is the suppression —
    # not a vacuously-true substring.
    stripped = json.loads(none_wire)
    del stripped["kind"]["legendPosition"]
    default_html = render_wire(json.dumps(stripped))
    assert ">alpha_series<" in default_html and ">beta_series<" in default_html
    html = render_wire(none_wire)
    assert ">alpha_series<" not in html
    assert ">beta_series<" not in html


def test_ssr_bridge_passes_data_labels() -> None:
    # Phase 881. The discriminator is a POSITIVE CONTROL first: the same wire
    # with the declaration stripped must NOT carry the value as a text node, so
    # the assertion cannot pass vacuously on a substring that was there anyway.
    # `120` and `150` are the bar values, and they appear as text ONLY as data
    # labels — the axis ticks of this chart are 0/50/100/150, so `>150<` alone
    # would be ambiguous and `>120<` is the one that is not.
    from fuaran_py import decode_node
    from fuaran_py.renderer import render_html

    def render_wire(wire: str) -> str:
        result = decode_node(wire)
        assert result.ok, getattr(result, "error", result)
        html = render_html(result.value)
        assert "<svg" in html and "ssr-placeholder" not in html
        return html

    ends_wire = json.dumps(
        {
            "id": "chart-ends",
            "kind": {
                "$type": "Chart",
                "kind": "Bar",
                "xField": "quarter",
                "yFields": ["revenue"],
                "stacked": False,
                "dataLabels": "Ends",
                "source": {
                    "$type": "Static",
                    "value": [
                        {"quarter": "Q1", "revenue": 120},
                        {"quarter": "Q2", "revenue": 150},
                    ],
                },
            },
        }
    )
    stripped = json.loads(ends_wire)
    del stripped["kind"]["dataLabels"]
    off_html = render_wire(json.dumps(stripped))
    assert ">120<" not in off_html  # negative control: Off writes no values

    html = render_wire(ends_wire)
    assert ">120<" in html  # the cap label the bridge now carries
    # …and it is set at the data-label size, which no chrome label uses.
    assert 'font-size="12px"' in html
    assert 'font-size="12px"' not in off_html


# ── Phase 882 — the temporal x-axis ──────────────────────────────────────────
#
# The calendar arithmetic is tested DIRECTLY (it is a normative spec five hosts
# mirror, so its properties are worth asserting rather than inferring from pixel
# positions); the lowering behaviour is read back off the drawing.


def test_calendar_conversions_are_exact_inverses_across_four_centuries() -> None:
    # The property that matters: the two conversions round-trip for EVERY day,
    # including the negative side of the epoch and the century leap rules. A
    # coprime stride samples all residues rather than a lattice. The range runs
    # well past -719468 so the `z - 146096` negative-bias branch is exercised too
    # — the one Python's floor division gets wrong.
    failures = [d for d in range(-900000, 900000, 977) if _days_from_civil(*_civil_from_days(d)) != d]
    assert failures == [], f"{len(failures)} sampled days did not round-trip"

    # The anchors, stated so a port has fixed points to check.
    assert _civil_from_days(0) == (1970, 1, 1)
    assert _days_from_civil(1970, 1, 1) == 0
    assert _days_from_civil(1969, 12, 31) == -1

    # 2000 is a leap year (÷400), 1900 is not (÷100, not ÷400) — the pair a naive
    # four-year rule gets wrong.
    assert _is_leap_year(2000)
    assert not _is_leap_year(1900)
    assert _days_from_civil(2000, 3, 1) - _days_from_civil(2000, 2, 1) == 29
    assert _days_from_civil(1900, 3, 1) - _days_from_civil(1900, 2, 1) == 28

    # THE PYTHON-SPECIFIC HAZARD, pinned with its counterfactual so the guard
    # cannot be silently removed: `//` FLOORS, and the algorithms' two
    # negative-bias branches need TRUNCATION toward zero. A floor-divided
    # `days_from_civil` is off by one for a pre-year-0 date, which would put this
    # host's day numbers a day away from every other host's.
    def floored_days_from_civil(year: int, month: int, day: int) -> int:
        y = year - 1 if month <= 2 else year
        era = (y if y >= 0 else y - 399) // 400
        yoe = y - era * 400
        mp = month - 3 if month > 2 else month + 9
        doy = (153 * mp + 2) // 5 + day - 1
        doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
        return era * 146097 + doe - 719468

    assert _trunc_div(-399, 400) == 0, "truncation toward zero, not the floor's -1"
    assert floored_days_from_civil(-220, 3, 15) != _days_from_civil(-220, 3, 15)
    assert floored_days_from_civil(2026, 1, 15) == _days_from_civil(2026, 1, 15), "identical where non-negative"


def test_iso_date_parser_is_strict_and_a_timestamp_keeps_only_its_date() -> None:
    assert _try_parse_day("2026-01-15") is not None  # the canonical form
    assert _try_parse_day("2000-02-29") is not None  # a real leap day

    # A timestamp's TIME-OF-DAY is discarded — the axis's unit is the day, so
    # 00:01 and 23:59 are the same value. That is the whole of the time-zone
    # policy, and it is why no host needs one.
    assert _try_parse_day("2026-01-15T10:30:00Z") == _try_parse_day("2026-01-15")

    # Refused: an impossible calendar date, a month out of range, a day the month
    # lacks, a locale spelling, a bare year, and the empty cell. Admitting any of
    # them would be the string-sniffing this axis exists to avoid.
    for bad in ("1900-02-29", "2026-13-01", "2026-00-10", "2026-01-32", "15/01/2026", "2026", ""):
        assert _try_parse_day(bad) is None, f"{bad!r} is not a canonical ISO date"

    # And an unparseable cell reads as the EPOCH rather than raising — the
    # lowering stays total; FUARAN097 is the loud part, upstream.
    assert _day_of("not a date") == 0


def test_tick_ladder_picks_a_calendar_nice_step_and_formats_to_the_granularity() -> None:
    if not _cases():
        pytest.skip("chart-lowering fixtures not found")

    def step_and_first(name: str) -> tuple[_TemporalStep, int]:
        inp = json.loads((_CHART_LOWERING_DIR / f"{name}.input.json").read_text(encoding="utf-8"))
        days = [_day_of(str(r[inp["xField"]])) for r in inp["data"]]
        lo, hi = _temporal_domain(days)
        return _choose_temporal_step(6, lo, hi), days[0]

    def expect(name: str, unit: str, count: int, sample: str) -> None:
        step, first = step_and_first(name)
        assert (step.unit, step.count) == (unit, count), f"{name}: rung"
        assert _temporal_label(step, first) == sample, f"{name}: label shape"

    # The three granularity regimes, read off the CHOSEN RUNG rather than off the
    # picture: one rung decides both the positions and the format.
    expect("line-temporal-daily", "Days", 5, "05 Jan 26")
    expect("bar-temporal-monthly", "Months", 6, "Jan 26")
    expect("line-temporal-yearly", "Years", 2, "2017")

    # The FORMAT BOUNDARIES: the adjacent rungs the two thresholds separate. 10
    # days is the last nominal under 27; one month (30.436875) the first over. Six
    # months (182.6) is the last under 365; one year (365.2425) the first over.
    expect("line-temporal-format-day-boundary", "Days", 10, "02 Mar 26")
    expect("line-temporal-format-month-boundary", "Months", 1, "Jan 26")
    expect("line-temporal-format-halfyear-boundary", "Months", 6, "Jan 24")
    expect("line-temporal-format-year-boundary", "Years", 1, "2021")

    # The thresholds themselves, on the nominals, so the arithmetic is checkable
    # without a fixture.
    assert _nominal_days(_TemporalStep("Days", 10)) <= 27.0
    assert _nominal_days(_TemporalStep("Months", 1)) > 27.0  # 30.436875
    assert _nominal_days(_TemporalStep("Months", 6)) <= 365.0
    assert _nominal_days(_TemporalStep("Years", 1)) > 365.0  # 365.2425

    # The ladder is total: a millennium-wide domain still resolves, and it does so
    # without generating a tick per day on the way.
    wide = _choose_temporal_step(6, _days_from_civil(1000, 1, 1), _days_from_civil(2000, 1, 1))
    assert wide.unit == "Years"
    assert wide.count >= 200


def test_month_and_year_rungs_land_on_calendar_boundaries_not_data_offsets() -> None:
    # The quarters fall out of the alignment rule rather than being a case of
    # their own: `(month-1) % 3 == 0` IS Jan/Apr/Jul/Oct.
    quarters = [
        _civil_from_days(d)
        for d in _temporal_ticks(
            _TemporalStep("Months", 3), _days_from_civil(2026, 1, 15), _days_from_civil(2027, 12, 20)
        )
    ]
    assert all(d == 1 and (m - 1) % 3 == 0 for _, m, d in quarters)
    assert quarters[0] == (2026, 4, 1), "the first is INSIDE the domain, not at its start"

    # A year rung anchors on the January 1 of years divisible by the step — so a
    # decade chart ticks 2020, 2030, never 2021, 2031.
    decades = [
        _civil_from_days(d)[0]
        for d in _temporal_ticks(_TemporalStep("Years", 10), _days_from_civil(2013, 6, 1), _days_from_civil(2044, 6, 1))
    ]
    assert decades == [2020, 2030, 2040]

    # A DAY rung steps from the domain's own start, because a "nice" 5-day
    # boundary does not exist.
    assert _temporal_ticks(_TemporalStep("Days", 5), 100, 118) == [100, 105, 110, 115]


def _shapes_of(spec: ChartSpec, rows: list[dict]) -> list[Obj]:
    shapes = lower(spec, rows).fields["shapes"]
    assert isinstance(shapes, Arr)
    return [s for s in shapes.items if isinstance(s, Obj)]


def _fixture_spec(name: str) -> tuple[ChartSpec, list[dict]]:
    inp = json.loads((_CHART_LOWERING_DIR / f"{name}.input.json").read_text(encoding="utf-8"))
    return _spec_and_rows(inp)


@_cases_available
def test_temporal_axis_is_continuous_marks_at_the_dates_labels_centred_on_them() -> None:
    # The x tick marks are the short segments hanging below the spine — the same
    # reader the band/continuous split uses.
    spec, rows = _fixture_spec("line-temporal-daily")
    shapes = _shapes_of(spec, rows)

    def is_line(s: Obj) -> bool:
        return s.tag == "Line"

    spine_y = max(s.fields["y1"] for s in shapes if is_line(s) and s.fields["y1"] == s.fields["y2"])
    mark_xs = sorted(
        s.fields["x1"]
        for s in shapes
        if is_line(s) and s.fields["x1"] == s.fields["x2"] and s.fields["y1"] == spine_y and s.fields["y2"] > spine_y
    )
    # SIX ticks from thirty rows: the count follows the tick rule, not the row
    # count — which is the whole difference from a band axis, where it would be
    # thirty-one boundaries.
    assert len(mark_xs) == 6

    labelled = sorted(
        (s.fields["x"], s.fields["text"])
        for s in shapes
        if s.tag == "Label"
        and s.fields["y"] > spine_y
        and isinstance(s.fields.get("style"), Obj)
        and "opacity" in s.fields["style"].fields
        # `Middle`-anchored excludes the y axis's lowest tick label, which also
        # sits below the spine but is `End`-anchored in the left margin.
        and s.fields["style"].fields.get("textAnchor") == "Middle"
        and "rotation" not in s.fields["style"].fields
    )
    assert [x for x, _ in labelled] == mark_xs, "a continuous label sits AT its mark, not beside it"
    assert [t for _, t in labelled] == [
        "05 Jan 26",
        "10 Jan 26",
        "15 Jan 26",
        "20 Jan 26",
        "25 Jan 26",
        "30 Jan 26",
    ], "and reads at the data's own granularity"


@_cases_available
def test_vertical_gridlines_follow_from_the_axis_being_continuous() -> None:
    # A temporal BAR chart has them too — the rule is a property, not a kind
    # list. The GRID opacity (0.12) is the discriminator: the axis spines and the
    # tick marks carry the axis opacity.
    def vertical_rules(name: str) -> int:
        spec, rows = _fixture_spec(name)
        count = 0
        for s in _shapes_of(spec, rows):
            if s.tag != "Line" or s.fields["x1"] != s.fields["x2"] or s.fields["y2"] <= s.fields["y1"]:
                continue
            style = s.fields.get("style")
            if not isinstance(style, Obj):
                continue
            opacity = style.fields.get("opacity")
            if isinstance(opacity, Obj) and opacity.fields.get("value") == 0.12:
                count += 1
        return count

    assert vertical_rules("bar-temporal-monthly") == 4, "a temporal bar axis rules its dates"
    assert vertical_rules("bar-single") == 0, "a band axis has no positions to rule"


def test_a_dates_position_is_its_value_so_an_irregular_run_is_not_evenly_spaced() -> None:
    # The point of a temporal axis over a band one: 1 Jan, 2 Jan and 1 Feb are not
    # three equal steps. A band axis would draw them evenly and silently misstate
    # the data.
    rows = [
        {"day": "2026-01-01", "v": 1.0},
        {"day": "2026-01-02", "v": 2.0},
        {"day": "2026-02-01", "v": 3.0},
    ]
    base = ChartSpec(kind="Line", x_field="day", y_fields=("v",))

    def polyline_xs(spec: ChartSpec) -> list[float]:
        for s in _shapes_of(spec, rows):
            if s.tag == "Polyline":
                pts = s.fields["points"]
                assert isinstance(pts, Arr)
                return [p.fields["x"] for p in pts.items if isinstance(p, Obj)]
        raise AssertionError("no polyline")

    a, b, c = polyline_xs(ChartSpec(**{**base.__dict__, "x_scale": "Temporal"}))
    # One day out of thirty-one: the second point sits hard against the first,
    # and the third at the far edge.
    assert b - a < (c - b) / 10.0

    p, q, r = polyline_xs(base)
    assert round(q - p, 2) == round(r - q, 2), "a band axis spaces them evenly"


@_cases_available
def test_a_temporal_axis_suppresses_its_default_x_title_never_an_explicit_one() -> None:
    # §4e's rule, recorded by Phase 878 and wired here. The x-axis title is the
    # unrotated, opacity-free label on the canvas's bottom inset.
    def x_axis_title(spec: ChartSpec, rows: list[dict]) -> str:
        for s in _shapes_of(spec, rows):
            if s.tag != "Label" or s.fields["y"] != 388.0:
                continue
            style = s.fields.get("style")
            if isinstance(style, Obj) and "opacity" not in style.fields and "rotation" not in style.fields:
                text = s.fields["text"]
                assert isinstance(text, str)
                return text
        return ""

    spec, rows = _fixture_spec("line-temporal-daily")
    assert x_axis_title(spec, rows) == "", "no fallback title on a date axis"

    # The band twin of the same chart DOES title itself, so the suppression is
    # attributable to the scale and to nothing else.
    banded = ChartSpec(**{**spec.__dict__, "x_scale": None})
    assert x_axis_title(banded, rows) == "Day", "a category axis still falls back to the field name"

    # And an explicit title always draws — the author overriding the default,
    # which the rule never touches.
    titled_spec, titled_rows = _fixture_spec("bar-temporal-x-title")
    assert x_axis_title(titled_spec, titled_rows) == "Reporting month"


@_cases_available
def test_the_label_ladder_governs_a_temporal_axiss_tick_labels_too() -> None:
    # Every ordinary temporal fixture rests FLAT — six short date labels in a
    # comfortable pitch — and the crowded one escalates, uniformly. Keyed on the
    # x-label baseline exactly, because the y axis's lowest tick label also sits
    # below the plot bottom and a looser reader picks it up.
    def rotations(name: str) -> set[float | None]:
        spec, rows = _fixture_spec(name)
        shapes = _shapes_of(spec, rows)
        spine_y = max(
            s.fields["y1"]
            for s in shapes
            if s.tag == "Line" and s.fields["y1"] == s.fields["y2"] and s.fields["x1"] < s.fields["x2"]
        )
        baseline = round(spine_y + 20.0, 2)
        out: set[float | None] = set()
        for s in shapes:
            if s.tag != "Label" or s.fields["y"] != baseline:
                continue
            style = s.fields.get("style")
            if isinstance(style, Obj) and "opacity" in style.fields:
                rot = style.fields.get("rotation")
                out.add(rot if isinstance(rot, float) else None)
        return out

    assert rotations("line-temporal-daily") == {None}, "a roomy date axis reads flat"
    assert rotations("line-temporal-vertical-labels") == {-90.0}, "a crowded one goes vertical — never a mix"


@_cases_available
def test_an_absent_x_scale_is_byte_identical_to_an_explicit_category() -> None:
    # The stronger form the corpus cannot state: the default is not merely
    # similar to absence, it is the same bytes. Which is why every pre-882 golden
    # is unmoved.
    for name in _cases():
        inp = json.loads((_CHART_LOWERING_DIR / f"{name}.input.json").read_text(encoding="utf-8"))
        if inp.get("xScale") is not None:
            continue
        spec, rows = _spec_and_rows(inp)
        explicit = ChartSpec(**{**spec.__dict__, "x_scale": "Category"})
        assert encode_node(lower_node("c", explicit, rows)) == encode_node(lower_node("c", spec, rows)), (
            f"{name}: Category must be indistinguishable from absent"
        )


@_cases_available
def test_a_temporal_declaration_on_a_pie_is_inert() -> None:
    # Dead intent the lowering cannot honour, neutralised rather than
    # half-applied: a pie's picture must not depend on a scale it never reads.
    spec, rows = _fixture_spec("pie-quarters")
    temporal = ChartSpec(**{**spec.__dict__, "x_scale": "Temporal"})
    assert encode_node(lower_node("c", temporal, rows)) == encode_node(lower_node("c", spec, rows))


def test_ssr_bridge_passes_x_scale() -> None:
    # The bridge half. Positive control first: with the declaration stripped the
    # axis is a band one and falls back to the capitalised field name as its x
    # title; declared, the title is suppressed and the labels read as dates.
    from fuaran_py import decode_node
    from fuaran_py.renderer import render_html

    def render_wire(wire: str) -> str:
        result = decode_node(wire)
        assert result.ok, getattr(result, "error", result)
        html = render_html(result.value)
        assert "<svg" in html and "ssr-placeholder" not in html
        return html

    temporal_wire = json.dumps(
        {
            "id": "chart-temporal",
            "kind": {
                "$type": "Chart",
                "kind": "Line",
                "xField": "day",
                "yFields": ["sessions"],
                "stacked": False,
                "xScale": "Temporal",
                "source": {
                    "$type": "Static",
                    "value": [
                        {"day": "2026-01-05", "sessions": 1200},
                        {"day": "2026-01-12", "sessions": 1450},
                        {"day": "2026-01-19", "sessions": 1310},
                        {"day": "2026-01-26", "sessions": 1580},
                    ],
                },
            },
        }
    )
    stripped = json.loads(temporal_wire)
    del stripped["kind"]["xScale"]
    band_html = render_wire(json.dumps(stripped))
    assert ">Day<" in band_html, "negative control: a band axis titles itself from the field"
    assert ">2026-01-05<" in band_html, "and labels the raw cell strings"

    html = render_wire(temporal_wire)
    assert ">Day<" not in html, "the declared date axis suppresses its fallback title"
    assert ">2026-01-05<" not in html, "and labels calendar ticks, not the cells"
    assert ">05 Jan 26<" in html


# ── Phase 921 — the accessible summary + the root's announced name ────────────
#
# The goldens above already pin the summary byte-for-byte on every fixture (it is
# a field of the Drawing they encode). These pin the GRAMMAR's arms by name, so a
# rewrite that keeps the goldens passing by regenerating them still has to answer
# for each rule, and they pin the root wiring that gets it ANNOUNCED.


def _summary(spec: ChartSpec, rows: list[dict[str, object]]) -> str:
    from fuaran_py.charts import lower

    # 0.2.0 — the bare JSON string IS the canonical TextSource.Literal form.
    desc = lower(spec, rows).fields.get("description")
    assert isinstance(desc, str), "the lowering generated no summary"
    return desc


def test_summary_grammar_arms() -> None:
    rows: list[dict[str, object]] = [
        {"region": "North", "sales": 80.0, "target": 100.0},
        {"region": "South", "sales": 130.0, "target": 110.0},
        {"region": "East", "sales": 60.0, "target": 90.0},
    ]
    summary = _summary(
        ChartSpec(kind="Bar", x_field="region", y_fields=("sales", "target"), title="Sales vs target"),
        rows,
    )
    assert summary == ("Bar chart. 2 series: sales, target. 3 categories: North to East. Peak sales at South, 130.")

    # `stacked` earns a word only where it changes the geometry, and the peak is
    # a DATUM, never the stack total.
    stacked = _summary(
        ChartSpec(kind="Bar", x_field="region", y_fields=("sales", "target"), title="Sales", stacked=True),
        rows,
    )
    assert stacked.startswith("Stacked bar chart.")
    assert stacked.endswith("Peak sales at South, 130.")

    # …and not on a kind where the flag is ignored.
    line = _summary(ChartSpec(kind="Line", x_field="region", y_fields=("sales",), stacked=True), rows)
    assert line.startswith("Line chart.")


def test_summary_series_folding_and_clamp() -> None:
    five = [f"s{i}" for i in range(5)]
    rows: list[dict[str, object]] = [{"region": "North", **{f: 1.0 for f in five}}]
    summary = _summary(ChartSpec(kind="Bar", x_field="region", y_fields=tuple(five)), rows)
    assert "5 series: s0, s1, s2, s3, and 1 more" in summary

    # The per-name clamp is exactly 32 units INCLUDING the ellipsis.
    long_name = "monthly_recurring_revenue_in_pounds_sterling"
    clamped = _summary(
        ChartSpec(kind="Bar", x_field="region", y_fields=(long_name,)),
        [{"region": "a region name comfortably past the clamp", long_name: 1.0}],
    )
    assert "1 series: monthly_recurring_revenue_in_po…" in clamped
    assert "a region name comfortably past …" in clamped
    assert len("monthly_recurring_revenue_in_po…") == 32
    assert len(clamped) <= 320


def test_refused_pie_announces_nothing() -> None:
    # A refused pie draws no geometry and no legend, because either would be a
    # claim about data the drawing declined to show. A summary is the same claim.
    from fuaran_py.charts import lower

    refused = lower(
        ChartSpec(kind="Pie", x_field="slice", y_fields=("a", "b")),
        [{"slice": "A", "a": 1.0, "b": 2.0}],
    )
    assert refused.fields.get("description") is None


def test_drawing_root_announces_its_description() -> None:
    # `role="img"` presents the drawing as ONE graphic and does not traverse into
    # it, and `<desc>` is not uniformly mapped to the accessible description — so
    # the root composes title + description into `aria-label`, the accessible NAME
    # every assistive technology announces. Byte-parity with the F# builder.
    from fuaran_py import decode_node
    from fuaran_py.renderer import render_html

    def render_drawing(title: str | None, description: str | None) -> str:
        kind: dict[str, object] = {
            "$type": "Drawing",
            "viewBox": {"minX": 0, "minY": 0, "width": 200, "height": 100},
            "shapes": [],
            "style": {},
        }
        if title is not None:
            kind["title"] = title
        if description is not None:
            kind["description"] = description
        result = decode_node(json.dumps({"id": "d", "kind": kind}))
        assert result.ok, getattr(result, "error", result)
        return render_html(result.value)

    both = render_drawing("Sales vs target", "Bar chart. 2 series: sales, target.")
    assert (
        '<svg class="fuaran-drawing" role="img" viewBox="0 0 200 100" '
        'aria-label="Sales vs target. Bar chart. 2 series: sales, target.">'
        "<title>Sales vs target</title>"
        "<desc>Bar chart. 2 series: sales, target.</desc></svg>"
    ) in both

    # The title is terminated only when it needs to be.
    assert 'aria-label="Really? D."' in render_drawing("Really?", "D.")
    assert 'aria-label="Plain. D."' in render_drawing("Plain", "D.")
    assert 'aria-label="D."' in render_drawing("", "D.")

    # A title-only or bare root is byte-identical to pre-921.
    assert ('<svg class="fuaran-drawing" role="img" viewBox="0 0 200 100"><title>Bars</title></svg>') in render_drawing(
        "Bars", None
    )
    assert "aria-label" not in render_drawing(None, None)

    # A description-only root announces the description alone.
    assert 'aria-label="One filled circle."' in render_drawing(None, "One filled circle.")

    # Hostile text is inert inside the ATTRIBUTE — the builder emits raw markup,
    # so its own XML escape is the whole defence.
    hostile = render_drawing('a"b', "<script>alert('x') & \"y\"</script>")
    assert ('aria-label="a&quot;b. &lt;script&gt;alert(&#39;x&#39;) &amp; &quot;y&quot;&lt;/script&gt;"') in hostile
    assert "<script>" not in hostile
