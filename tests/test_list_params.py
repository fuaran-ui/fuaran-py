"""Phase 610 / fuaran-core#91 — LIST-valued ``Binding.Transform`` params.

The shared ``nodes/multiselect-chip-list-param.json`` fixture is the oracle: a multiple
``Select`` whose ``values`` binding names the ``depts`` filter, beside a ``DataGrid``
whose ``Transform`` scopes its rows off a param bound to that same filter through the
membership test's ``param`` form. The shared name is the whole wiring.

Three behaviours are asserted, each with the discriminator that tells the correct
implementation from the plausible wrong one:

1. **Substitution, not the evaluation env.** The bound list is rewritten into the
   pipeline as the literal ``in`` form BEFORE evaluation; a pipeline that still carries
   an ``in``/``param`` when it reaches the evaluator is a strict ``UNBOUND_PARAM``, never
   a silent pass. The discriminator is that the *raw* fixture pipeline fails loudly under
   the same env the resolver would build.
2. **An EMPTY selection is UNBOUND, never ``items: []``.** Deselecting everything shows
   the UNFILTERED table. The discriminator is three rows vs zero — substituting an empty
   membership set is the plausible wrong answer, and it matches nothing.
3. **A kind mismatch reaches the strict refusal** — in BOTH directions, since a
   silently-pruned filter (the plausible wrong answer for a list bound to a scalar name)
   is indistinguishable from an unfiltered grid.
"""

from __future__ import annotations

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import decode_node
from fuaran_py.canonical import encode_value
from fuaran_py.compute import (
    ComputeErr,
    ComputeOk,
    ParamNonScalar,
    ParamResolved,
    ParamResolvedList,
    ParamUnbound,
    evaluate_transform,
    evaluate_tree,
    resolve_param_binding,
    substitute_list_params,
)
from fuaran_py.dataframe import (
    Col,
    Column,
    Derive,
    Filter,
    InList,
    InParam,
    Lit,
    Param,
    Table,
    cell_int,
    cell_str,
    decode_pipeline,
    eval_pipeline,
)
from fuaran_py.dataframe.model import NULL, UNBOUND_PARAM
from fuaran_py.model import Node, Obj

_NODES = CORPUS_ROOT / "nodes"

_ALL_ROWS = [
    {"dept": "eng", "amount": 100},
    {"dept": "sales", "amount": 90},
    {"dept": "ops", "amount": 70},
]


def _decode(name: str) -> Node:
    result = decode_node((_NODES / name).read_text(encoding="utf-8"))
    assert result.ok, f"decode {name}: {result.error}"
    return result.value


def _grid_source() -> Obj:
    """The ``dept-grid`` DataGrid's ``Transform`` source from the shared fixture."""
    tree = _decode("multiselect-chip-list-param.json")
    grid = next(c for c in tree.kind.fields["children"].items if c.id == "dept-grid")
    source = grid.kind.fields["source"]
    assert isinstance(source, Obj) and source.tag == "Transform"
    return source


# ── the fixture says what this phase claims it says ──────────────────────────


@corpus_required
def test_the_fixture_wires_a_chip_and_a_grid_through_one_shared_name() -> None:
    """What the conformance case asserts over the shared fixture, stated once: the chip's
    ``values`` binding, the grid's param ``from`` binding and the pipeline's ``in``/``param``
    all name ``depts`` — the shared name IS the wiring — and the ``in`` step carries
    ``param`` rather than ``items``, which is what makes it a LIST param at all."""
    tree = _decode("multiselect-chip-list-param.json")
    children = {c.id: c for c in tree.kind.fields["children"].items}

    chip = children["dept-chip"].kind
    assert chip.fields["multiple"] is True
    assert chip.fields["values"].tag == "Filter"
    assert chip.fields["values"].fields["name"] == "depts"
    assert "onChangeMulti" not in chip.fields  # the write-back stores the selection

    source = _grid_source()
    param = source.fields["params"].items[0]
    assert param.fields["name"] == "depts"
    assert param.fields["from"].tag == "Filter"
    assert param.fields["from"].fields["name"] == "depts"

    pipeline = decode_pipeline(encode_value(source.fields["pipeline"]))
    assert pipeline.ok, pipeline
    (step,) = pipeline.value
    assert isinstance(step, Filter)
    assert isinstance(step.pred, InParam)
    assert step.pred.param == "depts"


# ── 1. substitution, not the evaluation env ──────────────────────────────────


@corpus_required
def test_the_raw_pipeline_reaching_the_evaluator_is_a_strict_error() -> None:
    """The load-bearing half of "resolution is by SUBSTITUTION": an ``in``/``param`` that
    reaches the evaluator names an unbound param and FAILS. If the evaluator resolved it
    through an env instead, this would quietly succeed and substitution would be
    unobservable."""
    source = _grid_source()
    pipeline = decode_pipeline(encode_value(source.fields["pipeline"]))
    assert pipeline.ok
    table = Table(
        [("dept", "string"), ("amount", "int")],
        [
            Column("dept", "string", [cell_str("eng"), cell_str("sales"), cell_str("ops")]),
            Column("amount", "int", [cell_int(100), cell_int(90), cell_int(70)]),
        ],
    )
    result = eval_pipeline(pipeline.value, table)
    assert not result.ok
    assert result.error.code == UNBOUND_PARAM
    assert "depts" in result.error.detail


@corpus_required
def test_substitution_rewrites_in_param_to_the_literal_in_form() -> None:
    """``substitute_list_params`` is the rewrite the reference specifies: the bound
    ``InParam`` becomes an ``InList`` of literals — so the substituted step names NO param
    at all, which is exactly why one prune covers both param kinds."""
    source = _grid_source()
    pipeline = decode_pipeline(encode_value(source.fields["pipeline"]))
    assert pipeline.ok
    (substituted,) = substitute_list_params(pipeline.value, {"depts": [cell_str("eng"), cell_str("ops")]})
    assert isinstance(substituted, Filter)
    assert isinstance(substituted.pred, InList)
    assert substituted.pred.expr == Col("dept")
    assert substituted.pred.items == [Lit(cell_str("eng")), Lit(cell_str("ops"))]


def test_substitution_leaves_an_unbound_list_param_intact() -> None:
    """An unbound ``InParam`` survives the rewrite NAMING ITS OWN PARAM — which is what
    lets the single prune catch it. Rewriting it to an empty ``InList`` here would erase
    the name and silently match nothing."""
    pipeline = [Filter(InParam(Col("dept"), "depts"))]
    (out,) = substitute_list_params(pipeline, {"other": [cell_str("x")]})
    assert isinstance(out, Filter)
    assert out.pred == InParam(Col("dept"), "depts")


def test_substitution_reaches_every_expression_position() -> None:
    """The rewrite is a total walk, not a top-level special case — a ``derive`` step and a
    nested position are rewritten too."""
    pipeline = [Derive("hit", InParam(Col("dept"), "depts"))]
    (out,) = substitute_list_params(pipeline, {"depts": [cell_str("eng")]})
    assert isinstance(out, Derive)
    assert out.expr == InList(Col("dept"), [Lit(cell_str("eng"))])


@corpus_required
@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        (["eng"], [{"dept": "eng", "amount": 100}]),
        (["eng", "ops"], [{"dept": "eng", "amount": 100}, {"dept": "ops", "amount": 70}]),
        (["sales", "ops"], [{"dept": "sales", "amount": 90}, {"dept": "ops", "amount": 70}]),
    ],
)
def test_a_selection_scopes_the_grid(selection: list[str], expected: list[dict[str, object]]) -> None:
    """The chip's selection scopes the DataGrid declaratively — and a proper subset, so
    "the filter did nothing" cannot masquerade as a pass."""
    result = evaluate_transform(_grid_source(), {"depts": selection})
    assert isinstance(result, ComputeOk)
    assert result.rows == expected
    assert result.rows != _ALL_ROWS


@corpus_required
def test_the_whole_tree_walk_reaches_the_grid() -> None:
    """The node-level leg: the tree walk keys the derived rows by the owning node id."""
    tree = _decode("multiselect-chip-list-param.json")
    derived = evaluate_tree(tree, {"depts": ["ops"]})
    result = derived["dept-grid"]
    assert isinstance(result, ComputeOk)
    assert result.rows == [{"dept": "ops", "amount": 70}]


# ── 2. an EMPTY selection is UNBOUND — the pinned acceptance criterion ────────


@corpus_required
def test_deselecting_everything_shows_the_unfiltered_table() -> None:
    """Phase 610's acceptance criterion, pinned as OUTPUT. An empty selection is UNBOUND,
    so the dependent ``filter`` step PRUNES: all three rows. Substituting ``items: []``
    instead — the plausible wrong answer — would yield ZERO rows, which is why the row
    count is the discriminator rather than the absence of an error."""
    result = evaluate_transform(_grid_source(), {"depts": []})
    assert isinstance(result, ComputeOk)
    assert result.rows == _ALL_ROWS


@corpus_required
def test_an_unwritten_filter_shows_the_unfiltered_table_too() -> None:
    """The never-touched chip and the emptied one agree — one lenient rule, not two."""
    result = evaluate_transform(_grid_source(), {})
    assert isinstance(result, ComputeOk)
    assert result.rows == _ALL_ROWS


# ── 3. a kind mismatch reaches the strict unbound-param refusal ───────────────


@corpus_required
def test_a_list_bound_to_a_scalar_param_is_refused() -> None:
    """A LIST resolved for a name the pipeline reads as a SCALAR ``param`` substitutes
    nothing. It must NOT prune either — a pruned filter is an unfiltered grid, which reads
    as success — so the surviving ``Param`` reaches the evaluator's strict refusal."""
    node = _decode("grid-transform-param.json")
    result = evaluate_transform(node.kind.fields["source"], {"dept": ["eng", "sales"]})
    assert isinstance(result, ComputeErr)
    assert result.error.code == UNBOUND_PARAM
    assert "dept" in result.error.detail


@corpus_required
def test_a_scalar_bound_to_a_list_param_is_refused() -> None:
    """The converse: a SCALAR resolved for a name the pipeline reads as an ``in``/``param``
    leaves the ``InParam`` in place, and it is not pruned because the name IS bound — so
    the evaluator refuses rather than silently scoping by the wrong kind."""
    result = evaluate_transform(_grid_source(), {"depts": "eng"})
    assert isinstance(result, ComputeErr)
    assert result.error.code == UNBOUND_PARAM
    assert "depts" in result.error.detail


@corpus_required
def test_a_list_holding_a_non_scalar_item_stays_the_loud_error() -> None:
    """Element coercion is the scalar param's own, so a nested array has no cell form and
    the pre-existing "non-scalar value" refusal stands — never a boxed NULL."""
    result = evaluate_transform(_grid_source(), {"depts": ["eng", ["nested"]]})
    assert isinstance(result, ComputeErr)
    assert "non-scalar" in result.error.detail


# ── param resolution reports the list kind ───────────────────────────────────


@pytest.mark.parametrize("raw", [["eng", "ops"], ["eng"]])
def test_resolve_param_binding_reports_a_list(raw: list[str]) -> None:
    binding = Obj("Filter", {"name": "depts"})
    resolution = resolve_param_binding("depts", binding, {"depts": raw})
    assert isinstance(resolution, ParamResolvedList)
    assert resolution.cells == [cell_str(v) for v in raw]


def test_resolve_param_binding_reports_an_empty_list_as_unbound() -> None:
    binding = Obj("Filter", {"name": "depts"})
    assert isinstance(resolve_param_binding("depts", binding, {"depts": []}), ParamUnbound)


def test_resolve_param_binding_still_reports_a_scalar_as_a_scalar() -> None:
    """Nothing about an existing scalar param changes — the scalar reading is tried first."""
    binding = Obj("Filter", {"name": "dept"})
    resolution = resolve_param_binding("dept", binding, {"dept": "eng"})
    assert isinstance(resolution, ParamResolved)
    assert resolution.cell == cell_str("eng")


def test_resolve_param_binding_reports_a_nested_list_as_non_scalar() -> None:
    binding = Obj("Filter", {"name": "depts"})
    resolution = resolve_param_binding("depts", binding, {"depts": [["nested"]]})
    assert isinstance(resolution, ParamNonScalar)


def test_a_declared_default_list_seeds_the_param() -> None:
    """A structural ``Arr`` default carried on the binding resolves the same way a raw
    host list does — the two representations the store legitimately holds."""
    from fuaran_py.model import Arr

    binding = Obj("Filter", {"name": "depts", "defaultValue": Arr(["eng", "ops"])})
    resolution = resolve_param_binding("depts", binding, {})
    assert isinstance(resolution, ParamResolvedList)
    assert resolution.cells == [cell_str("eng"), cell_str("ops")]


# ── the substituted membership test evaluates three-valued ───────────────────


def _one_col(values: list[object]) -> Table:
    cells = [NULL if v is None else cell_str(str(v)) for v in values]
    return Table([("dept", "string")], [Column("dept", "string", cells)])


@pytest.mark.parametrize(
    ("items", "expected_depts"),
    [
        ([Lit(cell_str("eng"))], ["eng"]),
        ([Lit(cell_str("eng")), Lit(cell_str("ops"))], ["eng", "ops"]),
        ([Lit(cell_str("nobody"))], []),
    ],
)
def test_in_list_membership(items: list[object], expected_depts: list[str]) -> None:
    """The literal ``in`` form the substitution produces evaluates as SQL membership —
    the form is useless to a host that cannot evaluate it."""
    table = _one_col(["eng", "sales", "ops"])
    result = eval_pipeline([Filter(InList(Col("dept"), items))], table)
    assert result.ok, result
    assert [c.value for c in result.value.columns[0].cells] == expected_depts


def test_in_over_an_empty_item_list_matches_NOTHING() -> None:
    """The fact that makes "an empty selection is UNBOUND" DISCRIMINATING on this host,
    rather than a distinction without a difference.

    ``in`` over no items is ``false`` for every row — so substituting ``items: []`` (the
    plausible wrong reading of an empty selection) yields ZERO rows where the correct
    unbound reading yields the whole table. On a host whose ``in`` returned true or null
    here, the empty-selection test above could pass for the wrong reason; on this one the
    two readings are visibly different output, which is what the row-count assertion
    is entitled to conclude from."""
    table = _one_col(["eng", "sales", "ops"])
    result = eval_pipeline([Filter(InList(Col("dept"), []))], table)
    assert result.ok, result
    assert result.value.columns[0].cells == []


def test_in_list_is_three_valued_over_nulls() -> None:
    """A null subject is null, and a non-match having seen a null item is null — neither
    passes a ``filter``, and neither is an error."""
    table = _one_col([None, "sales"])
    result = eval_pipeline([Filter(InList(Col("dept"), [Lit(cell_str("eng")), Lit(NULL)]))], table)
    assert result.ok, result
    assert result.value.columns[0].cells == []


def test_in_param_reaching_the_evaluator_names_the_param() -> None:
    """The strictness parity with a scalar ``Param``: unbound, loudly, by name."""
    result = eval_pipeline([Filter(InParam(Col("dept"), "depts"))], _one_col(["eng"]))
    assert not result.ok
    assert result.error.code == UNBOUND_PARAM
    assert "depts" in result.error.detail


def test_the_scalar_param_refusal_is_unchanged() -> None:
    """The pre-existing scalar strictness is the shape the list rule mirrors."""
    from fuaran_py.dataframe import Binary

    result = eval_pipeline([Filter(Binary("eq", Col("dept"), Param("dept")))], _one_col(["eng"]))
    assert not result.ok
    assert result.error.code == UNBOUND_PARAM


# ── the fixture round-trips (the codec leg was already green; pin it here) ────


@corpus_required
def test_the_fixture_round_trips_byte_identically() -> None:
    """The codec leg of the conformance case: this host's canonical bytes for the shared
    fixture ARE the fixture's bytes. Adoption is the resolution rule on top of that, not
    instead of it."""
    from fuaran_py import encode_node

    raw = (_NODES / "multiselect-chip-list-param.json").read_text(encoding="utf-8").rstrip("\n")
    node = decode_node(raw)
    assert node.ok, node
    assert encode_node(node.value) == raw


# ── the reactivity edge (derived from the same param walk) ───────────────────


@corpus_required
def test_the_chip_to_grid_edge_is_reactive() -> None:
    """The filter→consumer edge is DERIVED from the pipeline's params, never separately
    declared — and a list param shares the scalar params' namespace, so the same walk
    names it. This host recomputes the whole tree on a store write rather than keeping a
    per-param subscription graph, so the edge cannot go stale for a list param in a way it
    would not for a scalar one; what this pins is the loop the operator actually sees.

    The last line is Phase 610's acceptance criterion under the reactive loop: deselecting
    everything comes BACK to the unfiltered table, rather than sticking at the last
    selection or collapsing to nothing."""
    from fuaran_py.runtime import BrowserDeps, FuaranRuntime

    headless = BrowserDeps(lambda _id: None, lambda _e, _h: None, lambda _e, _t, _h: lambda: None)
    runtime = FuaranRuntime(_decode("multiselect-chip-list-param.json"), deps=headless)

    assert runtime.derived["dept-grid"].rows == _ALL_ROWS
    assert runtime.set_compute_state({"depts": ["eng"]})["dept-grid"].rows == [{"dept": "eng", "amount": 100}]
    assert runtime.set_compute_state({"depts": ["eng", "ops"]})["dept-grid"].rows == [
        {"dept": "eng", "amount": 100},
        {"dept": "ops", "amount": 70},
    ]
    assert runtime.set_compute_state({"depts": []})["dept-grid"].rows == _ALL_ROWS


@corpus_required
def test_the_server_renderer_honours_the_rule_end_to_end() -> None:
    """The rule is not confined to the compute layer: the shipped server-HTML renderer
    resolves the grid through the same seam, so the rendered table IS the pinned output —
    all three rows while nothing is selected, the selected subset otherwise."""
    import re

    from fuaran_py.renderer import render_html

    tree = _decode("multiselect-chip-list-param.json")

    def rendered_depts(sources: dict[str, object]) -> list[str]:
        return re.findall(r">(eng|sales|ops)<", render_html(tree, sources))

    assert rendered_depts({}) == ["eng", "sales", "ops"]
    assert rendered_depts({"depts": []}) == ["eng", "sales", "ops"]
    assert rendered_depts({"depts": ["eng", "ops"]}) == ["eng", "ops"]
