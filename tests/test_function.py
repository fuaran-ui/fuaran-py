"""Phase 523 — the signature-searchable function registry (``fuaran_py.function``).

There is no shared corpus for this engine (the F# ``Fuaran.Core.Function`` registry is
certified in code, not as wire fixtures), so parity is asserted against the documented
F# ``findBySignature`` behaviour: result-type gating, subsumes (``available ⊆ required``)
vs exact matching, required-vs-optional holes, id-sorted determinism (no relevance score),
the typed no-match (empty list), and deterministic no-LLM composition with a typed no-path.
"""

from __future__ import annotations

from fuaran_py.function import (
    EXACT,
    SUBSUMES,
    ComposePath,
    FunctionRegistry,
    NoPath,
    function_entry,
    slot_hole,
    value_hole,
)
from fuaran_py.ui.capability import any_string, enum, int_range

# ── fixtures ─────────────────────────────────────────────────────────────────


def _registry() -> FunctionRegistry:
    reg = FunctionRegistry()
    reg.register(function_entry("metric.count", "Metric", (value_hole("n", int_range(0, 100)),)))
    reg.register(function_entry("metric.label", "Metric", (value_hole("t", any_string()),)))
    reg.register(function_entry("chart.bars", "Chart", (value_hole("series", any_string()),)))
    return reg


# ── result-type gating + subsumes ────────────────────────────────────────────


def test_matches_when_context_subsumes_required_space() -> None:
    reg = _registry()
    # context provides a value in [0..50] — fits inside the required [0..100]
    matches = reg.find_by_signature([value_hole("n", int_range(0, 50))], "Metric")
    assert [m.id for m in matches] == ["metric.count"]


def test_no_match_when_context_exceeds_required_space() -> None:
    reg = _registry()
    # context [0..200] is wider than the required [0..100] — not subsumed
    matches = reg.find_by_signature([value_hole("n", int_range(0, 200))], "Metric")
    assert matches == []


def test_wrong_result_type_is_no_match() -> None:
    reg = _registry()
    matches = reg.find_by_signature([value_hole("series", any_string())], "Metric")
    assert matches == []  # 'series' produces a Chart, not a Metric


def test_missing_required_hole_is_no_match() -> None:
    reg = _registry()
    assert reg.find_by_signature([], "Metric") == []  # no context to satisfy the required hole


def test_optional_hole_never_blocks() -> None:
    reg = FunctionRegistry()
    reg.register(
        function_entry(
            "metric.opt",
            "Metric",
            (value_hole("n", int_range(0, 100)), value_hole("hint", any_string(), required=False)),
        )
    )
    # context satisfies the required hole; the optional 'hint' is absent — still matches
    assert [m.id for m in reg.find_by_signature([value_hole("n", int_range(0, 10))], "Metric")] == ["metric.opt"]


# ── wildcard + determinism (no ranking score) ────────────────────────────────


def test_wildcard_output_searches_all() -> None:
    reg = _registry()
    # a wildcard produce-axis with a context satisfying only the any_string holes
    matches = reg.find_by_signature([value_hole("t", any_string()), value_hole("series", any_string())], None)
    assert [m.id for m in matches] == ["chart.bars", "metric.label"]  # id-sorted, not scored


def test_candidates_are_lexicographically_id_sorted() -> None:
    reg = FunctionRegistry()
    for fid in ("metric.zeta", "metric.alpha", "metric.mu"):
        reg.register(function_entry(fid, "Metric", ()))
    matches = reg.find_by_signature([], "Metric")
    assert [m.id for m in matches] == ["metric.alpha", "metric.mu", "metric.zeta"]


# ── exact mode ────────────────────────────────────────────────────────────────


def test_exact_requires_equal_address_sets() -> None:
    reg = FunctionRegistry()
    reg.register(function_entry("f", "Metric", (value_hole("n", int_range(0, 100)),)))
    # subsumes matches with an extra context hole; exact does not (address sets differ)
    ctx = [value_hole("n", int_range(0, 50)), value_hole("extra", any_string())]
    assert [m.id for m in reg.find_by_signature(ctx, "Metric", SUBSUMES)] == ["f"]
    assert reg.find_by_signature(ctx, "Metric", EXACT) == []


def test_exact_requires_shape_equal_pair() -> None:
    reg = FunctionRegistry()
    reg.register(function_entry("f", "Metric", (value_hole("n", int_range(0, 100)),)))
    # same address, but a different (narrower) space — exact needs shape equality
    assert reg.find_by_signature([value_hole("n", int_range(0, 50))], "Metric", EXACT) == []
    assert [m.id for m in reg.find_by_signature([value_hole("n", int_range(0, 100))], "Metric", EXACT)] == ["f"]


def test_enum_subsumption() -> None:
    reg = FunctionRegistry()
    reg.register(function_entry("f", "Badge", (value_hole("variant", enum("Neutral", "Brand", "Info")),)))
    assert [m.id for m in reg.find_by_signature([value_hole("variant", enum("Brand"))], "Badge")] == ["f"]
    # a choice outside the required enum is not subsumed
    assert reg.find_by_signature([value_hole("variant", enum("Danger"))], "Badge") == []


# ── deterministic composition + typed no-path ────────────────────────────────


def test_compose_direct_single_step() -> None:
    reg = _registry()
    result = reg.compose("Metric", [value_hole("n", int_range(0, 10))])
    assert isinstance(result, ComposePath)
    assert [s.function_id for s in result.steps] == ["metric.count"]


def test_compose_chains_a_slot() -> None:
    reg = FunctionRegistry()
    # a Dashboard needs a Metric slot it cannot get from context directly
    reg.register(function_entry("dash.one", "Dashboard", (slot_hole("body", "Metric"),)))
    reg.register(function_entry("metric.count", "Metric", (value_hole("n", int_range(0, 100)),)))
    result = reg.compose("Dashboard", [value_hole("n", int_range(0, 10))])
    assert isinstance(result, ComposePath)
    ids = [s.function_id for s in result.steps]
    assert ids == ["metric.count", "dash.one"]  # child first, root last
    assert result.steps[0].fills_slot == "body"


def test_compose_reports_typed_no_path() -> None:
    reg = _registry()
    result = reg.compose("Table", [value_hole("n", int_range(0, 10))])
    assert isinstance(result, NoPath)
    assert "Table" in result.reason


def test_register_rejects_duplicate_id() -> None:
    reg = FunctionRegistry()
    reg.register(function_entry("f", "Metric", ()))
    try:
        reg.register(function_entry("f", "Chart", ()))
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("expected a duplicate-id rejection")
