"""``Metric.comparisons`` and its trend lines (fuaran#2472).

A figure compared against more than one period at once carries each further
comparison as a ``MetricComparison`` (label, value, format, polarity); each renders
as one more trend line after the primary trend, with its own glyph and sentiment
under its OWN polarity, then its figure and its label.

The markup below is byte-for-byte what the reference server renderer emits for the
corpus fixture (checked against the F# and TypeScript server renderers when the
slot was admitted), so a divergence here is a real divergence.
"""

from __future__ import annotations

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_ui import decode_node, encode_node
from fuaran_ui.renderer import render_html
from fuaran_ui.schema import types as t
from fuaran_ui.ui import encode, fuaran

_INVERTED = "nodes/metric-comparison-inverted.json"
_TWO = "nodes/metric-two-comparisons.json"

_INVERTED_LINES = (
    '<div class="fuaran-metric-trend fuaran-metric-trend-improving">'
    '<span class="fuaran-metric-trend-glyph" role="img" aria-label="improving">\u25b2</span>-7.34%</div>'
    '<div class="fuaran-metric-trend fuaran-metric-trend-regressing">'
    '<span class="fuaran-metric-trend-glyph" role="img" aria-label="regressing">\u25bc</span>'
    "12 minutes vs last year</div>"
)


def _fixture(rel: str) -> str:
    return (CORPUS_ROOT / rel).read_text(encoding="utf-8").strip()


@corpus_required
def test_the_comparison_fixtures_round_trip_byte_for_byte() -> None:
    for rel in (_TWO, _INVERTED):
        wire = _fixture(rel)
        result = decode_node(wire)
        assert result.ok, getattr(result, "error", result)
        assert encode_node(result.value) == wire


@corpus_required
def test_each_comparison_is_a_trend_line_under_its_own_polarity() -> None:
    html = render_html(decode_node(_fixture(_INVERTED)).value)
    assert _INVERTED_LINES in html


@corpus_required
def test_two_comparisons_render_in_authored_order() -> None:
    html = render_html(decode_node(_fixture(_TWO)).value)
    assert html.count("fuaran-metric-trend-glyph") == 2
    assert html.index("vs same day last week") < html.index("vs same day last year")
    assert "fuaran-metric-trend-improving" in html
    assert "fuaran-metric-trend-regressing" in html


def test_explicit_defaults_decode_to_the_bytes_that_omit_them() -> None:
    wire = (
        '{"id":"m","kind":{"$type":"Metric","comparisons":[{"format":{"$type":"None"},'
        '"label":"vs last week","polarity":"HigherIsBetter","value":{"$type":"Static","value":0.1}}],'
        '"label":"Orders","value":{"$type":"Static","value":1}}}'
    )
    result = decode_node(wire)
    assert result.ok, getattr(result, "error", result)
    assert encode_node(result.value) == (
        '{"id":"m","kind":{"$type":"Metric","comparisons":[{"label":"vs last week",'
        '"value":{"$type":"Static","value":0.1}}],"label":"Orders","value":{"$type":"Static","value":1}}}'
    )


def test_an_empty_list_emits_no_key_and_an_absent_label_is_refused() -> None:
    node = fuaran.metric("m", label="Orders", value=1.0, comparisons=())
    assert "comparisons" not in encode(node)
    missing = decode_node(
        '{"id":"m","kind":{"$type":"Metric","comparisons":[{"value":{"$type":"Static","value":0.1}}],'
        '"label":"Orders","value":{"$type":"Static","value":1}}}'
    )
    assert not missing.ok


def test_the_builder_authors_the_slot() -> None:
    node = fuaran.metric(
        "m",
        label="Avg wait",
        value=80.0,
        comparisons=[t.MetricComparison(label="vs last year", value=t.Static(12.0), polarity="LowerIsBetter")],
    )
    assert '"comparisons":[{"label":"vs last year","polarity":"LowerIsBetter"' in encode(node)
