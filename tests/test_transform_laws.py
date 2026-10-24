"""The ``transformLaws`` law family — the reference evaluator's answers over a DRAWN sample.

``laws/transform-laws.json`` carries, for each drawn (source, pipeline), the canonical wire table the
reference evaluator produced, or the bare verdict that it refused. ``tests/test_dataframe_parity.py``
runs the AUTHORED cases (``dataframeParity``); this leg runs the drawn ones, which reach the exact
decimal, the rounding modes and the derived-column typing rule (Phase 2001).

The family is read THROUGH ``laws/manifest.json``: the file must agree with its manifest row (family
id, kit version, seed, iteration count, vector count), so a re-draw the manifest does not record is
a failure here, never a silently different sample. Nothing is counted by hand.

Every ``evalPipeline`` vector runs — both inputs decoded and re-encoded byte for byte, the pipeline
evaluated by THIS host, and the result's canonical encoding compared byte for byte with
``expected.table``; an ``error`` vector requires this host to refuse too, at decode or evaluation,
and the error class is never compared (the file carries none). The exceptions are named and
counted, never silent:

* the ``-unit-`` vectors, which need field metadata (fuaran#2207) and the unit algebra — carried
  by fuaran#2208. The set is DERIVED twice — from the ids, and from the content (a schema entry
  carrying metadata, or a ``convert`` node) — and the two must agree, so a new unit vector, or a
  renamed one, is noticed;
* the ``columnOp`` and ``delta`` case kinds, which this host has no surface for. The set of case
  kinds is pinned exactly, so a new kind fails loudly.

A vector carrying ``expected.tolerance`` (Phase 2400) names the columns the reference marks
host-rounded, each with a bound in ulps. Those columns are compared cell by cell — one type, one
length, null in the same rows, two present nonzero finite floats of one sign within the bound (the
distance being the difference of their IEEE-754 binary64 bit patterns), every other cell (a zero of
either sign, a non-finite float, a non-float) exactly — and the rest of the table byte for byte: this
host's answer with every named column replaced by the recorded one must encode to ``expected.table``
exactly. The bound is the vector's own, never a constant here; the worst distance per named column is
reported, so a host drifting toward the bound is visible.
"""

from __future__ import annotations

import json
import math
import struct
import warnings

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui.dataframe import (
    Column,
    Embedded,
    Table,
    decode_pipeline,
    decode_source,
    encode_pipeline,
    encode_source,
    eval_pipeline,
)

FAMILY = "transformLaws"
EVAL = "evalPipeline"

#: The case kinds the file may carry, and why this host runs only the first.
NOT_RUN = {
    "columnOp": "this host carries no ColumnOp surface",
    "delta": "this host carries no table-delta surface",
}
CASE_KINDS = {EVAL, *NOT_RUN}

UNIT_REASON = "carried by fuaran#2208 — this host does not yet carry field metadata (fuaran#2207) or the unit algebra"


def _family() -> tuple[dict, dict]:
    laws = CORPUS_ROOT / "laws"
    manifest = json.loads((laws / "manifest.json").read_text(encoding="utf-8"))
    rows = [f for f in manifest["families"] if f["id"] == FAMILY]
    assert len(rows) == 1, f"laws/manifest.json declares {len(rows)} {FAMILY} families"
    return rows[0], json.loads((laws / rows[0]["file"]).read_text(encoding="utf-8"))


ROW, FILE = _family()
VECTORS: list[dict] = FILE["vectors"]
EVAL_VECTORS = [v for v in VECTORS if v["case"] == EVAL]


def _is_unit_by_id(vector: dict) -> bool:
    return "-unit-" in vector["id"]


def _carries_field_metadata(vector: dict) -> bool:
    """Does this vector need field metadata or the unit algebra — read off its CONTENT?"""

    def schema_has_metadata(source: object) -> bool:
        return isinstance(source, dict) and any(set(entry) - {"name", "type"} for entry in source.get("schema", []))

    def walk(el: object) -> bool:
        if isinstance(el, dict):
            if el.get("$type") == "convert" or ("source" in el and schema_has_metadata(el["source"])):
                return True
            return any(walk(v) for v in el.values())
        if isinstance(el, list):
            return any(walk(v) for v in el)
        return False

    source = json.loads(vector["input"]["source"])
    return schema_has_metadata(source) or walk(json.loads(vector["input"]["pipeline"]))


UNIT_IDS = sorted(v["id"] for v in EVAL_VECTORS if _is_unit_by_id(v))


def test_the_file_agrees_with_its_manifest_row() -> None:
    assert FILE["family"] == ROW["id"]
    assert FILE["kitVersion"] == ROW["kitVersion"]
    assert FILE["seed"] == ROW["seed"]
    assert FILE["iterations"] == ROW["iterations"]
    assert len(VECTORS) == ROW["vectors"]
    # `iterations` counts the evalPipeline vectors only.
    assert len(EVAL_VECTORS) == ROW["iterations"]


def test_the_case_kinds_are_exactly_the_known_set() -> None:
    """A new case kind fails here loudly rather than going unrun."""
    assert {v["case"] for v in VECTORS} == CASE_KINDS


def test_the_unit_skip_set_is_exactly_the_vectors_that_need_field_metadata() -> None:
    """The ``-unit-`` ids, derived from the names, must be exactly the vectors whose CONTENT needs
    field metadata or the unit algebra — so a unit vector under another name, or a ``-unit-`` id
    over plain data, is noticed rather than skipped or run by accident."""
    by_content = sorted(v["id"] for v in EVAL_VECTORS if _carries_field_metadata(v))
    assert UNIT_IDS, "the family carries no -unit- vectors: the exception is stale, remove it"
    assert by_content == UNIT_IDS


def test_the_exceptions_are_reported_by_name() -> None:
    """The run output names every vector this leg does not run, and counts them."""
    lines = [f"{len(UNIT_IDS)} {EVAL} vector(s) not run — {UNIT_REASON}: {', '.join(UNIT_IDS)}"]
    for kind, reason in NOT_RUN.items():
        ids = [v["id"] for v in VECTORS if v["case"] == kind]
        lines.append(f"{len(ids)} {kind} vector(s) not run — {reason}: {', '.join(ids)}")
    warnings.warn("transformLaws exceptions:\n" + "\n".join(lines), stacklevel=1)


@pytest.mark.parametrize("vector", EVAL_VECTORS, ids=[v["id"] for v in EVAL_VECTORS])
def test_eval_pipeline_vector(vector: dict) -> None:
    if _is_unit_by_id(vector):
        pytest.skip(UNIT_REASON)
    expected = vector["expected"]
    src = decode_source(vector["input"]["source"])
    pipe = decode_pipeline(vector["input"]["pipeline"])

    if expected["verdict"] == "error":
        # A refusal at decode is a refusal; the error class is never compared.
        if src.ok and pipe.ok:
            assert isinstance(src.value, Embedded)
            result = eval_pipeline(pipe.value, src.value.table)
            assert not result.ok, f"{vector['id']}: this host evaluated a pipeline the reference refused"
        return

    assert expected["verdict"] == "ok", f"{vector['id']}: unknown verdict {expected['verdict']!r}"
    assert src.ok, f"{vector['id']}: source decode failed: {getattr(src, 'error', None)}"
    assert pipe.ok, f"{vector['id']}: pipeline decode failed: {getattr(pipe, 'error', None)}"
    assert encode_source(src.value) == vector["input"]["source"]
    assert encode_pipeline(pipe.value) == vector["input"]["pipeline"]
    assert isinstance(src.value, Embedded)

    result = eval_pipeline(pipe.value, src.value.table)
    assert result.ok, f"{vector['id']}: this host refused, the reference succeeded: {getattr(result, 'error', None)}"
    tolerance: dict[str, int] = expected.get("tolerance", {})
    if not tolerance:
        assert encode_source(Embedded(result.value)) == expected["table"], f"{vector['id']}: this host ≠ the reference"
        return
    _compare_within_tolerance(vector["id"], tolerance, result.value, expected["table"])


def _ulp_distance(a: float, b: float) -> int:
    """The distance between two doubles of one sign: their binary64 bit patterns' difference."""
    (ia,) = struct.unpack("<q", struct.pack("<d", a))
    (ib,) = struct.unpack("<q", struct.pack("<d", b))
    return abs(ia - ib)


def _bits(f: float) -> bytes:
    return struct.pack("<d", f)


def _compare_within_tolerance(vid: str, tolerance: dict[str, int], answer: Table, expected_text: str) -> None:
    decoded = decode_source(expected_text)
    assert decoded.ok and isinstance(decoded.value, Embedded), f"{vid}: expected.table does not decode"
    want = decoded.value.table
    by_name = {c.name: c for c in want.columns}
    columns = list(answer.columns)
    worst: dict[str, int] = {}
    for name, bound in sorted(tolerance.items()):
        wc = by_name.get(name)
        at = next((i for i, c in enumerate(columns) if c.name == name), None)
        assert wc is not None and at is not None, f"{vid}: tolerance column {name!r} is absent"
        gc = columns[at]
        assert gc.type == wc.type and len(gc.cells) == len(wc.cells), (
            f"{vid}: tolerance column {name!r} differs in shape"
        )
        worst[name] = 0
        for r, (w, g) in enumerate(zip(wc.cells, gc.cells, strict=True)):
            assert (w.kind == "null") == (g.kind == "null"), f"{vid}: {name!r} row {r} is null in one table only"
            if w.kind == "null":
                continue
            both = w.kind == g.kind == "float"
            if (
                both
                and all(x != 0.0 and math.isfinite(x) for x in (w.value, g.value))
                and math.copysign(1.0, w.value) == math.copysign(1.0, g.value)
            ):
                d = _ulp_distance(w.value, g.value)
                worst[name] = max(worst[name], d)
                assert d <= bound, (
                    f"{vid}: {name!r} row {r}: {g.value!r} is {d} ulp from {w.value!r}; the bound is {bound}"
                )
                continue
            if both:
                same = _bits(w.value) == _bits(g.value) or (math.isnan(w.value) and math.isnan(g.value))
            else:
                same = w.kind == g.kind and w.value == g.value
            assert same, f"{vid}: {name!r} row {r}: {g.value!r} differs from {w.value!r} (compared exactly)"
        columns[at] = Column(gc.name, gc.type, list(wc.cells))
    warnings.warn(
        f"transformLaws tolerance: {vid} worst ulp per column {worst} (bounds {tolerance})",
        stacklevel=1,
    )
    replaced = Table(answer.schema, columns)
    assert encode_source(Embedded(replaced)) == expected_text, (
        f"{vid}: this host ≠ the reference outside the tolerance columns"
    )


@pytest.mark.parametrize("kind", sorted(NOT_RUN))
def test_case_kinds_this_host_has_no_surface_for(kind: str) -> None:
    ids = [v["id"] for v in VECTORS if v["case"] == kind]
    pytest.skip(f"{len(ids)} {kind} vector(s) not run — {NOT_RUN[kind]}: {', '.join(ids)}")


def test_power_rows_the_wire_cannot_carry() -> None:
    """The special-value rows over a NaN or an infinity (the wire carries neither, so the law file
    cannot), the pinned product tree past the cube, and the int32 band an int power stays inside."""
    from fuaran_ui._core.dataframe.evaluate import _apply_scalar, _exp_host, _log_host, _pow_float, _pow_go

    nan, inf = math.nan, math.inf
    assert math.isnan(_pow_float(1.0, nan).value)  # type: ignore[arg-type]
    assert math.isnan(_pow_float(nan, 0.0).value)  # type: ignore[arg-type]
    assert _pow_float(-1.0, inf).value == 1.0
    assert _pow_float(-1.0, -inf).value == 1.0
    assert _pow_float(1e200, 2.5).value == inf  # math.pow raises here; IEEE-754 answers +Inf
    assert _pow_float(-1e200, 3.0).value == -inf
    assert math.isnan(_exp_host(nan))
    assert _exp_host(709.8) == inf
    assert _log_host(math.log, inf).value == inf
    assert math.isnan(_log_host(math.log10, nan).value)  # type: ignore[arg-type]
    from fuaran_ui.dataframe import Cell

    assert _apply_scalar("pow", [Cell("float", 1e-300), Cell("int", -2)]).value.kind == "null"  # type: ignore[union-attr]
    x = 1.1
    assert _pow_go(x, 4) == (x * x) * (x * x)
    assert _pow_go(x, 5) == x * ((x * x) * (x * x))
    assert not _apply_scalar("pow", [Cell("int", 46341), Cell("int", 2)]).ok
    assert _apply_scalar("pow", [Cell("int", -2), Cell("int", 31)]).value == Cell("int", -(2**31))  # type: ignore[union-attr]
