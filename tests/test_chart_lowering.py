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
    spec = ChartSpec(
        kind=inp["kind"],
        x_field=inp["xField"],
        y_fields=tuple(inp["yFields"]),
        title=inp.get("title"),
        stacked=bool(inp.get("stacked", False)),
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
    assert "#3366cc" in html
