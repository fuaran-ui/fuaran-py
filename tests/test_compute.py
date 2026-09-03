"""Phase 522 — the compute-layer host parity + reactive runtime loop.

The pipeline evaluator itself is certified against the F# reference vectors by
``test_dataframe_parity.py``. This suite certifies the **node-level** leg Phase 522
adds: resolving a ``Binding.Transform`` source (embedded datasource + pipeline +
state-bound parameters) to derived rows, and the reactive recompute-on-state-change
loop under the runtime. The shared ``nodes/grid-transform*.json`` fixtures are the
oracle for the derived values (the same worked example the F#/TS hosts evaluate).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import decode_node
from fuaran_py.canonical import encode_value
from fuaran_py.compute import (
    ComputeOk,
    ParamNonScalar,
    ParamResolved,
    ParamResolvedList,
    ParamUnbound,
    evaluate_transform,
    evaluate_tree,
    resolve_param_binding,
    rows_of,
)
from fuaran_py.dataframe import (
    NULL,
    Binary,
    Col,
    Column,
    Embedded,
    Filter,
    Param,
    Ref,
    Table,
    cell_int,
    cell_str,
    decode_pipeline,
    encode_pipeline,
    encode_source,
)
from fuaran_py.dataframe.model import UNBOUND_PARAM
from fuaran_py.model import Arr, Node, Obj, from_json
from fuaran_py.runtime import BrowserDeps, FuaranRuntime

_NODES = CORPUS_ROOT / "nodes"


def _decode(name: str) -> Node:
    result = decode_node((_NODES / name).read_text(encoding="utf-8"))
    assert result.ok, f"decode {name}: {result.error}"
    return result.value


# ── derived-value parity (the shared worked example) ─────────────────────────


@corpus_required
def test_grid_transform_derives_expected_rows() -> None:
    """filter(amount>0) → groupBy(dept, sum→total) → sort(total desc) over the fixture."""
    node = _decode("grid-transform.json")
    derived = evaluate_tree(node, {})
    result = derived["grid-transform"]
    assert isinstance(result, ComputeOk)
    assert result.rows == [{"dept": "eng", "total": 220}]  # sales' amount 0 filtered out


@corpus_required
def test_evaluate_transform_directly() -> None:
    node = _decode("grid-transform.json")
    result = evaluate_transform(node.kind.fields["source"], {})
    assert isinstance(result, ComputeOk)
    assert result.rows == [{"dept": "eng", "total": 220}]


# ── parameters + the lenient filter pruning ──────────────────────────────────


@corpus_required
def test_param_filter_pruned_when_unbound() -> None:
    """An unbound param prunes its filter — no constraint, all rows pass."""
    node = _decode("grid-transform-param.json")
    result = evaluate_transform(node.kind.fields["source"], {})
    assert isinstance(result, ComputeOk)
    assert result.rows == [{"dept": "eng", "amount": 100}, {"dept": "sales", "amount": 90}]


@corpus_required
@pytest.mark.parametrize(
    ("dept", "expected"),
    [("eng", [{"dept": "eng", "amount": 100}]), ("sales", [{"dept": "sales", "amount": 90}])],
)
def test_param_binds_from_state(dept: str, expected: list[dict[str, object]]) -> None:
    """A bound param substitutes into its filter — the reactive selector."""
    node = _decode("grid-transform-param.json")
    result = evaluate_transform(node.kind.fields["source"], {"dept": dept})
    assert isinstance(result, ComputeOk)
    assert result.rows == expected


# ── declared param defaults (Selection 0.2.9 / Filter 0.2.0) ─────────────────
#
# A param whose `from` binding is unwritten but carries a `defaultValue` is
# BOUND to that default, not unbound — so its filter step is evaluated, never
# pruned. The two paths are observably different (a pruned filter yields every
# row; a seeded one yields the selected subset), and the reference hosts seed.


def _related_grid(tree: Node) -> Node:
    """The `related-grid` child of `master-detail-preselected` — a DataGrid whose
    Transform param is a `Selection` with a declared `defaultValue`."""
    return next(c for c in tree.kind.fields["children"].items if c.id == "related-grid")


@corpus_required
def test_selection_param_seeds_declared_default() -> None:
    """An unwritten `Selection` with a `defaultValue` SEEDS the param — the
    preselected-row mechanism (0.2.9). Pruning instead would yield both rows."""
    node = _related_grid(_decode("master-detail-preselected.json"))
    result = evaluate_transform(node.kind.fields["source"], {})
    assert isinstance(result, ComputeOk)
    assert result.rows == [{"id": "TCK-2041", "priority": "high"}]


@corpus_required
def test_selection_param_default_is_not_first_row_coincidence() -> None:
    """The load-bearing case the corpus cannot yet express: a default naming a
    NON-FIRST row. Pruning yields both rows; seeding yields only TCK-2042 —
    the two implementations are indistinguishable when the default is row 0."""
    node = _related_grid(_decode("master-detail-preselected.json"))
    source = node.kind.fields["source"]
    param = source.fields["params"].items[0]
    seeded = Obj(
        "Transform",
        {
            **source.fields,
            "params": Arr(
                [
                    Obj(
                        None,
                        {
                            **param.fields,
                            "from": Obj("Selection", {**param.fields["from"].fields, "defaultValue": "TCK-2042"}),
                        },
                    )
                ]
            ),
        },
    )
    result = evaluate_transform(seeded, {})
    assert isinstance(result, ComputeOk)
    assert result.rows == [{"id": "TCK-2042", "priority": "low"}]


@corpus_required
def test_selection_param_projects_field_off_a_written_row() -> None:
    """A written selection stores the whole row; a declared `field` projects the
    scalar off it (0.2.10), and the live value wins over the default."""
    node = _related_grid(_decode("master-detail-preselected.json"))
    state = {"ticket-grid": {"id": "TCK-2042", "priority": "low"}}
    result = evaluate_transform(node.kind.fields["source"], state)
    assert isinstance(result, ComputeOk)
    assert result.rows == [{"id": "TCK-2042", "priority": "low"}]


def _param_transform(from_binding: Obj) -> Obj:
    """A one-column embedded source + `filter(name = param p)` over it."""
    table = Table([("name", "string")], [Column("name", "string", [cell_str("a"), cell_str("b")])])
    source = from_json(json.loads(encode_source(Embedded(table))))
    pipeline = from_json(
        json.loads(
            encode_pipeline([Filter(Binary("eq", Col("name"), Param("p")))]),
        )
    )
    return Obj(
        "Transform",
        {
            "params": Arr([Obj(None, {"from": from_binding, "name": "p"})]),
            "pipeline": pipeline,
            "source": source,
        },
    )


def test_filter_param_seeds_declared_default() -> None:
    """`Binding.Filter.defaultValue` (0.2.0) seeds the same way — the value the
    resolver yields before the filter is first written."""
    result = evaluate_transform(_param_transform(Obj("Filter", {"defaultValue": "b", "name": "q"})), {})
    assert isinstance(result, ComputeOk)
    assert result.rows == [{"name": "b"}]


def test_filter_param_written_value_wins_over_the_default() -> None:
    result = evaluate_transform(_param_transform(Obj("Filter", {"defaultValue": "b", "name": "q"})), {"q": "a"})
    assert isinstance(result, ComputeOk)
    assert result.rows == [{"name": "a"}]


def test_filter_param_without_a_default_still_prunes() -> None:
    """The one host leniency is untouched: no written value AND no declared
    default ⇒ unbound ⇒ the filter step is pruned ("an unset filter is no
    constraint")."""
    result = evaluate_transform(_param_transform(Obj("Filter", {"name": "q"})), {})
    assert isinstance(result, ComputeOk)
    assert result.rows == [{"name": "a"}, {"name": "b"}]


def test_state_param_seeds_declared_default() -> None:
    result = evaluate_transform(_param_transform(Obj("State", {"defaultValue": "b", "key": "k"})), {})
    assert isinstance(result, ComputeOk)
    assert result.rows == [{"name": "b"}]


# ── non-scalar param sources (the loud channel, rs/TS parity) ────────────────
#
# A param slot is scalar. A structured value reaching one — the classic case is a
# grid row-click writing the WHOLE row into a `field`-less `Selection` — has no
# scalar form. Boxing it to the NULL cell (this host's behaviour before the error
# channel) prunes or matches nothing and reads as *empty data* rather than a
# *broken binding*; the reference hosts fail loudly with a named message, and
# these pin that parity. No corpus fixture exercises it — the fixtures are all
# well-formed, and this is a HOST-side resolution defect, not a wire one.

_NON_SCALAR_MESSAGE = "Transform param 'p' resolved to a non-scalar value"


def test_field_less_selection_of_a_written_row_is_a_loud_error() -> None:
    """The finding: a row `Obj` written into a `field`-less `Selection` slot."""
    transform = _param_transform(Obj("Selection", {"nodeId": "grid"}))
    result = evaluate_transform(transform, {"grid": Obj(None, {"name": "a", "amount": 1})})
    assert not result.ok
    assert result.error.code == "TYPE_ERROR"
    assert result.error.detail == _NON_SCALAR_MESSAGE


def test_field_less_selection_of_a_plain_dict_row_is_a_loud_error() -> None:
    """The host store may hold a plain dict rather than a wire `Obj` — same verdict."""
    transform = _param_transform(Obj("Selection", {"nodeId": "grid"}))
    result = evaluate_transform(transform, {"grid": {"name": "a", "amount": 1}})
    assert not result.ok
    assert result.error.detail == _NON_SCALAR_MESSAGE


def test_structured_declared_default_is_a_loud_error() -> None:
    """A declared default is resolved the same way a written value is.

    Phase 610 moved WHICH loud error this is, not whether there is one. An array is a
    legitimate LIST-param source now, so the default resolves to a list — bound to a name
    this pipeline reads as a SCALAR `Param`. That is the kind mismatch, and it reaches the
    strict `UNBOUND_PARAM` refusal rather than the non-scalar one. Both are loud; only the
    silent readings (a boxed NULL, or a pruned filter) would be defects."""
    transform = _param_transform(Obj("Filter", {"defaultValue": Arr([1, 2]), "name": "q"}))
    result = evaluate_transform(transform, {})
    assert not result.ok
    assert result.error.code == UNBOUND_PARAM
    assert "p" in result.error.detail


def test_structured_non_list_declared_default_is_still_the_non_scalar_error() -> None:
    """The non-scalar channel is unchanged for what has no list reading either — a record
    in a scalar slot — and for a list holding a structured item (element coercion is the
    scalar param's own)."""
    record = _param_transform(Obj("Filter", {"defaultValue": Obj(None, {"a": 1}), "name": "q"}))
    assert evaluate_transform(record, {}).error.detail == _NON_SCALAR_MESSAGE
    nested = _param_transform(Obj("Filter", {"defaultValue": Arr([Arr([1])]), "name": "q"}))
    assert evaluate_transform(nested, {}).error.detail == _NON_SCALAR_MESSAGE


def test_resolve_param_binding_is_four_valued() -> None:
    """Resolved / resolved-list / unbound / non-scalar are distinct outcomes — the whole
    point of the error channel. A silent `Cell | None` cannot express the last two.

    The EMPTY array moved from non-scalar to UNBOUND at Phase 610, and deliberately: an
    empty multi-select selection is the absence of a constraint, so its filter prunes and
    the unfiltered table shows. `tests/test_list_params.py` pins that end to end."""
    assert resolve_param_binding("p", Obj("Static", {"value": "a"}), {}) == ParamResolved(cell_str("a"))
    assert resolve_param_binding("p", Obj("Filter", {"name": "q"}), {}) == ParamUnbound()
    assert resolve_param_binding("p", Obj("Static", {"value": Arr([])}), {}) == ParamUnbound()
    assert resolve_param_binding("p", Obj("Static", {"value": Arr(["a"])}), {}) == ParamResolvedList([cell_str("a")])
    assert resolve_param_binding("p", Obj("Static", {"value": Obj(None, {})}), {}) == ParamNonScalar(
        _NON_SCALAR_MESSAGE
    )


def test_absent_and_null_sources_stay_scalar_nulls() -> None:
    """The boundary the error channel must NOT swallow: JSON null (and a `Static`
    carrying no value at all) IS a scalar — the NULL cell, per the reference
    `JVal::Null -> Cell::Null`. Only Obj / Arr / host records are non-scalar."""
    assert resolve_param_binding("p", Obj("Static", {}), {}) == ParamResolved(NULL)
    assert resolve_param_binding("p", Obj("State", {"defaultValue": None, "key": "k"}), {}) == ParamResolved(NULL)


# ── the reactive runtime loop ─────────────────────────────────────────────────


@dataclass
class _FakeElement:
    element_id: str
    inner_html: str = ""
    listeners: dict[str, list[Callable[[Any], None]]] = field(default_factory=dict)


@dataclass
class _FakeDom:
    elements: dict[str, _FakeElement] = field(default_factory=dict)
    paints: int = 0

    def _el(self, element_id: str) -> _FakeElement:
        return self.elements.setdefault(element_id, _FakeElement(element_id))

    def deps(self) -> BrowserDeps:
        def get_element_by_id(element_id: str) -> Any:
            return self._el(element_id)

        def set_inner_html(element: Any, html: str) -> None:
            element.inner_html = html
            self.paints += 1

        def add_event_listener(element: Any, event: str, handler: Callable[[Any], None]) -> Callable[[], None]:
            element.listeners.setdefault(event, []).append(handler)
            return lambda: None

        return BrowserDeps(get_element_by_id, set_inner_html, add_event_listener)


@corpus_required
def test_runtime_recomputes_on_state_change() -> None:
    """set_compute_state recomputes the derived cells and re-renders — the Living-Sheet loop."""
    node = _decode("grid-transform-param.json")
    dom = _FakeDom()
    runtime = FuaranRuntime(node, deps=dom.deps(), compute_state={"dept": "eng"})  # type: ignore[arg-type]
    runtime.mount("fuaran-root")
    assert runtime.derived["grid-transform-param"].rows == [{"dept": "eng", "amount": 100}]
    paints_before = dom.paints

    derived = runtime.set_compute_state(dept="sales")
    assert derived["grid-transform-param"].rows == [{"dept": "sales", "amount": 90}]
    assert dom.paints > paints_before  # re-rendered
    assert runtime.compute_state == {"dept": "sales"}


def test_runtime_without_compute_state_is_inert() -> None:
    """A tree with no compute graph derives nothing and renders as before."""
    tree = Node("m", Obj("Markdown", {"text": Obj("Literal", {"text": "x"})}), {})
    runtime = FuaranRuntime(tree, deps=_FakeDom().deps())  # type: ignore[arg-type]
    assert runtime.derived == {}
    assert "fuaran-markdown" in runtime.render()


# ── null projection + Ref rejection + param codec ─────────────────────────────


def test_rows_of_projects_null_to_none() -> None:
    table = Table(
        [("a", "int"), ("b", "string")],
        [Column("a", "int", [cell_int(1), NULL]), Column("b", "string", [cell_str("x"), NULL])],
    )
    assert rows_of(table) == [{"a": 1, "b": "x"}, {"a": None, "b": None}]


def test_ref_source_is_unresolved() -> None:
    """A Ref (host-named) source does not evaluate — embedded-only, per the reference."""
    ref_wire = from_json(json.loads(encode_source(Ref("orders"))))
    transform = Obj("Transform", {"pipeline": Arr([]), "source": ref_wire})
    result = evaluate_transform(transform, {})
    assert not result.ok
    assert result.error.code == "UNRESOLVED_SOURCE"


@corpus_required
def test_param_pipeline_codec_round_trips() -> None:
    """The new ``param`` ColExpr encodes + decodes byte-identically (wire coupling)."""
    node = _decode("grid-transform-param.json")
    pipeline_wire = encode_value(node.kind.fields["source"].fields["pipeline"])
    decoded = decode_pipeline(pipeline_wire)
    assert decoded.ok
    assert encode_pipeline(decoded.value) == pipeline_wire
    # a Param survived the decode (the filter predicate's right operand)
    filt = next(s for s in decoded.value if isinstance(s, Filter))
    assert isinstance(filt.pred.right, Param)  # type: ignore[attr-defined]
