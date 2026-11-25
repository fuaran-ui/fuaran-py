"""Phase 1956 — every scalar function the ColExpr wire admits, certified against the reference.

The oracle is ``fixtures/scalar_fns/reference-scalar-fns.json``: one vector per case, each answer
computed by the REFERENCE evaluator (the published ``Fuaran.Compute.DataFrame`` package) through
``generate_reference.fsx`` beside it, never written by hand. Regenerate it with ``dotnet fsi`` when
the vector list changes; never hand-edit it.

Three legs:

* **evaluation** — every vector evaluates here to the reference's cell, bit for bit on a float, or to
  the reference's refusal (its code, and for a type error its detail; an arity error's expected and
  received counts);
* **the string predicates** — ``contains`` / ``startsWith`` / ``endsWith`` are binary operators
  rather than functions, under the same contract and the same oracle;
* **coverage** — the oracle names every function this host's decoder admits and nothing else, so a
  function the wire gains is a red test here until it has vectors, and a function this host's
  decoder silently lacks is a red test too;
* **the corpus's ``Now`` fixtures** — the ``dateDiffDays`` derive that ``nodes/now-grain.json`` and
  ``nodes/now-environment-binding.json`` carry, evaluated through this host's compute layer at the
  instant ``render-text.json`` pins, answers what the reference answers for the same operands.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_ui import decode_node
from fuaran_ui._core.dataframe.evaluate import _apply_scalar, _eval_expr
from fuaran_ui._core.dataframe.model import (
    BIN_OPS,
    NULL,
    SCALAR_FNS,
    Binary,
    Cell,
    EvalError,
    Lit,
    Result,
    cell_bool,
    cell_date,
    cell_decimal,
    cell_float,
    cell_int,
    cell_str,
    cell_timestamp,
)
from fuaran_ui.compute import ComputeOk
from fuaran_ui.compute.evaluate import evaluate_transform
from fuaran_ui.dataframe import Embedded, decode_pipeline, decode_source, encode_pipeline, eval_pipeline
from fuaran_ui.model import Arr, Obj, Value
from fuaran_ui.renderer.bindings import truncate_to_grain

_ORACLE = Path(__file__).parent / "fixtures" / "scalar_fns" / "reference-scalar-fns.json"
_ORACLE_DOC = json.loads(_ORACLE.read_text(encoding="utf-8"))
_VECTORS: list[dict] = _ORACLE_DOC["vectors"]
_PREDICATES: list[dict] = _ORACLE_DOC["predicates"]
_BY_ID = {v["id"]: v for v in _VECTORS}


def _float_of_bits(bits: str) -> float:
    return struct.unpack("<d", int(bits, 16).to_bytes(8, "little"))[0]


def _bits_of_float(x: float) -> str:
    return format(struct.unpack("<Q", struct.pack("<d", x))[0], "016x")


def _cell(enc: list) -> Cell:
    tag = enc[0]
    if tag == "null":
        return NULL
    v = enc[1]
    makers = {
        "int": cell_int,
        "bool": cell_bool,
        "str": cell_str,
        "date": cell_date,
        "timestamp": cell_timestamp,
        "decimal": cell_decimal,
    }
    if tag == "float":
        return cell_float(_float_of_bits(v))
    return makers[tag](v)


def _answer(c: Cell) -> list:
    """This host's cell in the oracle's encoding (a float as its bit pattern)."""
    if c.kind == "null":
        return ["null"]
    if c.kind == "float":
        return ["float", _bits_of_float(c.value)]  # type: ignore[arg-type]
    tag = {"string": "str"}.get(c.kind, c.kind)
    return [tag, c.value]


def _check(vector: dict, result: Result[Cell, EvalError]) -> None:
    if "ok" in vector:
        assert result.ok, f"{vector['id']}: the reference answers {vector['ok']}, this host refused {result}"
        assert _answer(result.value) == vector["ok"], vector["id"]
        return
    want = vector["err"]
    assert not result.ok, f"{vector['id']}: the reference refuses {want}, this host answered {result}"
    err = result.error
    assert err.code == want["code"], vector["id"]
    if want["code"] == "TYPE_ERROR":
        assert err.detail == want["detail"], vector["id"]
    elif want["code"] == "ARITY_ERROR":
        assert f"expects {want['expected']} args, got {want['got']}" in err.detail, vector["id"]


@pytest.mark.parametrize("vector", _VECTORS, ids=[v["id"] for v in _VECTORS])
def test_scalar_fn_evaluates_as_the_reference_does(vector: dict) -> None:
    _check(vector, _apply_scalar(vector["fn"], [_cell(a) for a in vector["args"]]))


@pytest.mark.parametrize("vector", _PREDICATES, ids=[v["id"] for v in _PREDICATES])
def test_string_predicate_evaluates_as_the_reference_does(vector: dict) -> None:
    a, b = (Lit(_cell(x)) for x in vector["args"])
    _check(vector, _eval_expr([], [], Binary(vector["op"], a, b)))


def test_the_oracle_covers_every_string_predicate_the_decoder_admits() -> None:
    assert {v["op"] for v in _PREDICATES} == {"contains", "startsWith", "endsWith"} <= set(BIN_OPS)


def test_the_oracle_covers_exactly_the_functions_the_decoder_admits() -> None:
    """The decoder's scalar-function set is the contract: each one has reference vectors here, and
    the reference names no function this host's decoder refuses."""
    assert {v["fn"] for v in _VECTORS} == set(SCALAR_FNS)


def test_the_reference_admits_twenty_one_scalar_functions() -> None:
    """The reference decoder's list (``scalarOf`` in the reference evaluator) as of the oracle's
    producer. A function added there is added to the generator and to this host's decoder together."""
    assert len(SCALAR_FNS) == 21


# ── the corpus's two `Now` fixtures ──────────────────────────────────────────

_PINNED_NOW = "2026-08-02T06:59:24Z"  # the instant render-text.json pins for these fixtures


def _grid_transform(fixture: str, grid_id: str) -> Obj:
    tree = decode_node((CORPUS_ROOT / "nodes" / fixture).read_text(encoding="utf-8")).value
    children = tree.kind.fields["children"]
    assert isinstance(children, Arr)
    for child in children.items:
        if child.id == grid_id:
            source = child.kind.fields["source"]
            assert isinstance(source, Obj)
            return source
    raise AssertionError(f"{fixture} has no node {grid_id}")


def _bind_now(transform: Obj, grain: str | None) -> Obj:
    """The transform with its ``Now`` param furnished as the value a host's clock yields at the
    pinned instant under the param's grain — a ``Static``, since that is the host-furnished value."""
    furnished = truncate_to_grain(grain, _PINNED_NOW) if grain else _PINNED_NOW
    declared = transform.fields["params"]
    assert isinstance(declared, Arr)
    params: list[Value] = []
    for p in declared.items:
        assert isinstance(p, Obj)
        source = p.fields["from"]
        assert isinstance(source, Obj) and source.tag == "Now"
        params.append(Obj(p.tag, {**p.fields, "from": Obj("Static", {"value": furnished})}))
    return Obj(transform.tag, {**transform.fields, "params": Arr(params)})


@corpus_required
@pytest.mark.parametrize(
    ("fixture", "grid_id", "grain", "expected"),
    [
        ("now-grain.json", "overdue-grid-day-grain", "Day", {"INV-2001": "ddd-now-grain-inv-2001"}),
        (
            "now-environment-binding.json",
            "overdue-grid",
            None,
            {"INV-1001": "ddd-now-env-inv-1001", "INV-1002": "ddd-now-env-inv-1002"},
        ),
    ],
)
def test_the_corpus_now_fixtures_derive_the_reference_day_delta(
    fixture: str, grid_id: str, grain: str | None, expected: dict[str, str]
) -> None:
    result = evaluate_transform(_bind_now(_grid_transform(fixture, grid_id), grain), {})
    assert isinstance(result, ComputeOk), result
    got = {row["id"]: row["daysOverdue"] for row in result.rows}
    assert got == {inv: _BY_ID[vid]["ok"][1] for inv, vid in expected.items()}


# ── the two functions this host's decoder used to refuse ─────────────────────


def test_sqrt_and_indexof_decode_and_evaluate_in_a_derive() -> None:
    """Through the wire, not just the evaluator: the decoder admits both, the derive runs, the
    encoder writes them back in the canonical ``apply`` spelling."""
    source = decode_source(
        '{"columns":{"s":{"validity":[true],"values":["ab-c"]},"x":{"validity":[true],"values":[6.25]}},'
        '"schema":[{"name":"s","type":"string"},{"name":"x","type":"float"}]}'
    )
    pipeline_text = (
        '[{"$type":"derive","expr":{"$type":"apply","args":[{"$type":"col","name":"s"},'
        '{"$type":"lit","cell":{"$type":"Str","value":"-"}}],"fn":"indexOf"},"name":"at"},'
        '{"$type":"derive","expr":{"$type":"apply","args":[{"$type":"col","name":"x"}],"fn":"sqrt"},"name":"root"}]'
    )
    pipeline = decode_pipeline(pipeline_text)
    assert source.ok and pipeline.ok, (source, pipeline)
    assert isinstance(source.value, Embedded)
    assert encode_pipeline(pipeline.value) == pipeline_text
    result = eval_pipeline(pipeline.value, source.value.table)
    assert result.ok, result
    columns = {c.name: c for c in result.value.columns}
    assert (columns["at"].type, columns["at"].cells) == ("int", [cell_int(2)])
    assert (columns["root"].type, columns["root"].cells) == ("float", [cell_float(2.5)])
