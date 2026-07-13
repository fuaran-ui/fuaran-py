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
from fuaran_py.compute import ComputeOk, evaluate_transform, evaluate_tree, rows_of
from fuaran_py.dataframe import (
    NULL,
    Column,
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
