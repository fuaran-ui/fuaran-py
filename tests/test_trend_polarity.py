"""``Metric.trendPolarity`` and the trend-sentiment render (fuaran#867).

The wire slot and the render are one phase and one contract, so they are tested
together here: WIRE_FORMAT §3.6.1 states the composition rule normatively —
``sentiment = sign(trend) x polarity``, rendered on the trend element ALONE,
``tone`` never derived from it and never written to — and a codec that decodes
the field while the renderer keeps painting the trend a constant colour has
adopted the byte and not the rule.

The markup strings below are byte-for-byte what the reference server renderer
emits for the same nodes. That is the Phase 801 precedent applied to a render
leg: the corpus is the oracle for the codec, and for the emission half the
reference host's bytes are, so a divergence here is a real divergence rather than
a difference of opinion about whitespace.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import decode_node, encode_node
from fuaran_py.ops import apply, decode_op
from fuaran_py.renderer import render_html
from fuaran_py.renderer.theme import trend_sentiment

_FIXTURE = "nodes/metric-inverted-polarity.json"


def _metric(trend: float, polarity: str | None, fmt: str = '"trendFormat":{"$type":"Percent","decimals":2},') -> str:
    pol = f'"trendPolarity":"{polarity}",' if polarity is not None else ""
    return (
        '{"id":"m","kind":{"$type":"Metric","label":"Avg wait",'
        f'"trend":{{"$type":"Static","value":{trend}}},{fmt}{pol}'
        '"value":{"$type":"Static","value":80}}}'
    )


def _render(wire: str) -> str:
    result = decode_node(wire)
    assert result.ok, getattr(result, "error", result)
    return render_html(result.value)


# ── The codec ────────────────────────────────────────────────────────────────


@corpus_required
def test_corpus_fixture_round_trips_byte_identically() -> None:
    src = (CORPUS_ROOT / _FIXTURE).read_text(encoding="utf-8")
    result = decode_node(src)
    assert result.ok, getattr(result, "error", result)
    assert encode_node(result.value) == src


@pytest.mark.parametrize(
    ("spelling", "code"),
    [
        ('"Neutral"', "UNKNOWN_DU_CASE"),  # RESERVED, and deliberately not a case
        ('"higherIsBetter"', "UNKNOWN_DU_CASE"),
        ("3", "WRONG_TYPE"),
        ("null", "WRONG_TYPE"),
    ],
)
def test_a_polarity_outside_the_two_case_set_is_refused(spelling: str, code: str) -> None:
    """The reserved third case is the whole reason the slot is an enum: admitting
    it later must be a bare-string addition, which it can only be if refusing it
    now is real."""
    result = decode_node(
        _metric(-0.0734, None).replace(
            '"value":{"$type":"Static","value":80}',
            f'"trendPolarity":{spelling},"value":{{"$type":"Static","value":80}}',
        )
    )
    assert not result.ok
    assert result.error.code == code
    assert result.error.path == "$.kind.trendPolarity"


def test_the_default_is_omitted_on_re_encode() -> None:
    """Omitted-when-``HigherIsBetter`` on BOTH boundaries — which is what makes
    every Metric authored before the slot existed byte-unchanged."""
    explicit = decode_node(_metric(0.5, "HigherIsBetter"))
    assert explicit.ok
    assert '"trendPolarity"' not in encode_node(explicit.value)


def test_an_absent_trend_leaves_a_declared_polarity_inert_and_legal() -> None:
    """§3.6.1 clause 4: a Metric with no trend that declares a polarity is legal
    and says nothing. Nothing couples the two slots."""
    wire = (
        '{"id":"m","kind":{"$type":"Metric","label":"x","trendPolarity":"LowerIsBetter",'
        '"value":{"$type":"Static","value":1}}}'
    )
    result = decode_node(wire)
    assert result.ok
    assert encode_node(result.value) == wire
    assert "fuaran-metric-trend" not in render_html(result.value)


# ── The apply engine ─────────────────────────────────────────────────────────


def test_update_prop_sets_the_polarity_and_refuses_the_reserved_case() -> None:
    """The UpdateProp twin of the decode slot, with the same two-case refusal —
    rejection parity with the reference engines, which both carry this path."""
    tree = decode_node(_metric(-0.0734, None))
    assert tree.ok

    op = decode_op('{"$type":"UpdateProp","path":"TrendPolarity","target":"m","value":"LowerIsBetter"}')
    assert op.ok, getattr(op, "error", op)
    applied = apply(op.value, tree.value)
    assert applied.ok, getattr(applied, "error", applied)
    assert '"trendPolarity":"LowerIsBetter"' in encode_node(applied.value)

    bad = decode_op('{"$type":"UpdateProp","path":"TrendPolarity","target":"m","value":"Neutral"}')
    assert bad.ok
    assert not apply(bad.value, tree.value).ok


# ── The composition rule ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("trend", "polarity", "sentiment", "glyph"),
    [
        (0.5, None, "improving", "▲"),
        (-0.5, None, "regressing", "▼"),
        (0.0, None, "unchanged", "→"),
        (0.5, "HigherIsBetter", "improving", "▲"),
        (-0.5, "HigherIsBetter", "regressing", "▼"),
        # The inversion: the SAME numbers read the other way round.
        (0.5, "LowerIsBetter", "regressing", "▼"),
        (-0.5, "LowerIsBetter", "improving", "▲"),
        (0.0, "LowerIsBetter", "unchanged", "→"),
    ],
)
def test_sentiment_is_the_sign_times_the_polarity(
    trend: float, polarity: str | None, sentiment: str, glyph: str
) -> None:
    assert trend_sentiment(polarity, trend) == (sentiment, glyph)


def test_the_glyph_disagrees_with_the_sign_under_an_inverted_polarity() -> None:
    """The disagreement is the visible evidence the declaration was honoured, so
    it is pinned rather than left as an incidental consequence of the arithmetic.
    A falling number under ``LowerIsBetter`` gets the UP triangle."""
    html = _render(_metric(-0.0734, "LowerIsBetter"))
    assert "▲" in html
    assert "▼" not in html
    # And the number still SAYS what it says: the sign is untouched.
    assert "-7.34%" in html


def test_polarity_never_reaches_tone() -> None:
    """A renderer that inferred "improving => tile is Success" would re-create in
    the render the conflation the slot exists to remove, and would override an
    emitter's deliberate tone on a metric improving from a bad place."""
    wire = _metric(-0.0734, "LowerIsBetter").replace('"label":"Avg wait",', '"label":"Avg wait","tone":"Warning",')
    html = _render(wire)
    assert "fuaran-metric fuaran-metric-warning" in html
    assert "fuaran-metric-trend-improving" in html
    assert "fuaran-metric-success" not in html


# ── The emitted markup ───────────────────────────────────────────────────────


@corpus_required
def test_the_corpus_fixture_renders_the_reference_markup_byte_for_byte() -> None:
    src = (CORPUS_ROOT / _FIXTURE).read_text(encoding="utf-8")
    result = decode_node(src)
    assert result.ok
    assert (
        '<div class="fuaran-metric-trend fuaran-metric-trend-improving">'
        '<span class="fuaran-metric-trend-glyph" role="img" aria-label="improving">▲</span>'
        "-7.34%</div>"
    ) in render_html(result.value)


def test_an_unresolved_trend_keeps_its_bare_div_byte_for_byte() -> None:
    """No sentiment is stated where there is no number to state one about, and
    ``unchanged`` would be a claim rather than an absence. This is also the
    byte-unchanged guarantee for every pre-867 tree whose trend does not resolve."""
    wire = (
        '{"id":"m","kind":{"$type":"Metric","label":"x",'
        '"trend":{"$type":"State","key":"never-written"},"value":{"$type":"Static","value":1}}}'
    )
    assert '<div class="fuaran-metric-trend"></div>' in _render(wire)


def test_the_aria_label_sits_on_the_glyph_not_the_trend_element() -> None:
    """Colour alone fails WCAG 1.4.1, so the sentiment owes a non-colour channel —
    but a label on the trend DIV would override its text and lose the number to
    assistive technology. On the glyph, AT hears "improving -7.3%"."""
    html = _render(_metric(-0.0734, "LowerIsBetter"))
    assert 'class="fuaran-metric-trend fuaran-metric-trend-improving">' in html
    assert 'aria-label="improving"' in html
    assert '<div class="fuaran-metric-trend fuaran-metric-trend-improving" aria-label' not in html


# ── The authoring surface ────────────────────────────────────────────────────


def test_the_authoring_surface_can_express_the_polarity() -> None:
    """A codec-only adoption would leave this host behind the tiers it is
    co-equal with — a Python author could decode the slot and not write it."""
    from fuaran_py.ui import encode, fuaran

    wire = encode(fuaran.metric("m", label="Avg wait", value=80, trend=-0.0734, trend_polarity="LowerIsBetter"))
    assert '"trendPolarity":"LowerIsBetter"' in wire
    assert decode_node(wire).ok

    default = encode(fuaran.metric("m", label="Avg wait", value=80, trend=-0.0734))
    assert '"trendPolarity"' not in default


def test_the_value_space_offers_the_polarity_to_an_agent() -> None:
    """The AI-tools projection is derived from the typed enum, so an agent is
    never offered the reserved case the decoder refuses."""
    from fuaran_py import ai_tools

    assert ai_tools.value_space()["TrendPolarity"] == ["HigherIsBetter", "LowerIsBetter"]


# ── The stylesheet ───────────────────────────────────────────────────────────


def test_the_three_sentiment_classes_are_styled() -> None:
    """The base rule painted the trend ``success`` unconditionally before this
    phase, so a modifier class the stylesheet does not carry would leave the
    render neutral in every direction — the same defect, differently spelled."""
    css = Path(__file__).resolve().parents[1] / "src" / "fuaran_py" / "renderer" / "content" / "fuaran-reference.css"
    text = css.read_text(encoding="utf-8")
    for cls in ("improving", "regressing", "unchanged", "glyph"):
        assert f".fuaran-metric-trend-{cls}" in text


@corpus_required
def test_the_manifest_lists_the_fixture() -> None:
    """A guard against the whole file passing vacuously: the corpus resolution
    could point at a snapshot predating the fixture, and every assertion above
    that reads it would then be skipped-by-absence rather than run."""
    manifest = json.loads((CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert any(fx["inputFile"] == _FIXTURE for fx in manifest["fixtures"])
