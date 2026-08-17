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
from fuaran_py.charts import ChartSpec, lower_node
from fuaran_py.schema.encode import encode_node

_CHART_LOWERING_DIR = CORPUS_ROOT / "chart-lowering"


def _cases() -> list[str]:
    if not corpus_available() or not _CHART_LOWERING_DIR.is_dir():
        return []
    return sorted(p.name[: -len(".input.json")] for p in _CHART_LOWERING_DIR.glob("*.input.json"))


def _spec_and_rows(inp: dict) -> tuple[ChartSpec, list[dict]]:
    # Phase 876 — `valueFormat` is a WIRE field carried in canonical `Format`
    # JSON; `axisUnitMode` is a harness-only STYLE selector (the chart style is
    # a lowering parameter, never wire), present so the corpus can pin every
    # mode. Both absent on the pre-876 cases.
    #
    # Phase 878 — `xTitle` / `yTitle` / `subtitle` are WIRE fields carried as
    # plain strings (the canonical `TextSource.Literal` form), omitted when the
    # author declared none. Absent on every pre-878 case.
    #
    # Phase 880 — `legendPosition` is a WIRE field carried as the bare enum name
    # (`Top | Right | Bottom | None`). ABSENT means the host default (`Right`),
    # never "no legend", so it is omitted on every pre-880 case even though the
    # picture many of them lower to moved when the default did.
    #
    # Phase 881 — `dataLabels` is a WIRE field carried as the bare enum name
    # (`Off | Ends`). ABSENT means `Off`, which is also the default, so it is
    # omitted on every pre-881 case AND every pre-881 golden is unchanged.
    spec = ChartSpec(
        kind=inp["kind"],
        x_field=inp["xField"],
        y_fields=tuple(inp["yFields"]),
        title=inp.get("title"),
        stacked=bool(inp.get("stacked", False)),
        value_format=inp.get("valueFormat"),
        axis_unit_mode=inp.get("axisUnitMode", "Words"),
        x_title=inp.get("xTitle"),
        y_title=inp.get("yTitle"),
        subtitle=inp.get("subtitle"),
        legend_position=inp.get("legendPosition"),
        data_labels=inp.get("dataLabels"),
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
