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
and the error class is never compared (the file carries none). The ``-unit-`` vectors run like
every other (Phase 2208): the evaluator carries field metadata (fuaran#2207) through every verb and
checks units, so a refusal among them is the unit check's and an answer carries its schema's units,
labels and descriptions.

Every ``columnOp`` vector runs (Phase 2500 gave this host the surface): the op decodes through
``decode_op_json``, re-encodes byte for byte to ``expected.op``, and applies through ``apply_op``,
compared as an ``evalPipeline`` vector is. The one exception is named and counted, never silent: the
``delta`` case kind, whose vectors ask for the diff of two tables, where this host's ``delta_of``
derives a row delta from an op over the before table. The set of case kinds is pinned exactly, so a
new kind fails loudly.

The tests after the vector leg pin the unit rules past what the vectors draw: the whole schema entry
through each verb class (kept, rebuilt with the derived unit), and a go-red — each refusal is a
``UNIT_MISMATCH``, and the same pipeline over the same data answers once the units are withdrawn.

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
    Agg,
    Binary,
    Col,
    Column,
    Convert,
    Derive,
    Embedded,
    Field,
    FieldMeta,
    Filter,
    GroupBy,
    Join,
    Project,
    Table,
    UnitOfMeasure,
    Window,
    WindowSpec,
    cell_float,
    cell_int,
    cell_str,
    decode_pipeline,
    decode_source,
    encode_pipeline,
    encode_source,
    eval_pipeline,
    parse_unit,
    render_unit,
    table_fields,
    table_of_fields,
)
from fuaran_ui.dataframe.grid_edit import apply_op, decode_op_json, encode_op
from fuaran_ui.dataframe.model import UNIT_MISMATCH
from fuaran_ui.dataframe.unit import DIMENSIONLESS, compatible_units, conversion_factor, pow_unit

FAMILY = "transformLaws"
EVAL = "evalPipeline"
COLUMN_OP = "columnOp"

#: The case kinds the file may carry that this host does not run, and why.
NOT_RUN = {
    "delta": "this host derives a row delta from an op over the before table (delta_of); "
    "it has no surface that diffs two tables",
}
CASE_KINDS = {EVAL, COLUMN_OP, *NOT_RUN}


def _family() -> tuple[dict, dict]:
    laws = CORPUS_ROOT / "laws"
    manifest = json.loads((laws / "manifest.json").read_text(encoding="utf-8"))
    rows = [f for f in manifest["families"] if f["id"] == FAMILY]
    assert len(rows) == 1, f"laws/manifest.json declares {len(rows)} {FAMILY} families"
    return rows[0], json.loads((laws / rows[0]["file"]).read_text(encoding="utf-8"))


ROW, FILE = _family()
VECTORS: list[dict] = FILE["vectors"]
EVAL_VECTORS = [v for v in VECTORS if v["case"] == EVAL]
COLUMN_OP_VECTORS = [v for v in VECTORS if v["case"] == COLUMN_OP]


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


def test_the_family_carries_unit_vectors() -> None:
    """The unit leg is not vacuous: the family draws ``-unit-`` vectors, and every one runs."""
    assert any("-unit-" in v["id"] for v in EVAL_VECTORS)


def test_every_vector_but_the_delta_kind_runs() -> None:
    assert COLUMN_OP_VECTORS, "the family carries no columnOp vector"
    not_run = [v for v in VECTORS if v["case"] in NOT_RUN]
    assert {v["case"] for v in not_run} == {"delta"}
    assert len(EVAL_VECTORS) + len(COLUMN_OP_VECTORS) + len(not_run) == len(VECTORS)


def test_the_exceptions_are_reported_by_name() -> None:
    """The run output names every vector this leg does not run, and counts them."""
    lines = []
    for kind, reason in NOT_RUN.items():
        ids = [v["id"] for v in VECTORS if v["case"] == kind]
        lines.append(f"{len(ids)} {kind} vector(s) not run — {reason}: {', '.join(ids)}")
    warnings.warn("transformLaws exceptions:\n" + "\n".join(lines), stacklevel=1)


@pytest.mark.parametrize("vector", EVAL_VECTORS, ids=[v["id"] for v in EVAL_VECTORS])
def test_eval_pipeline_vector(vector: dict) -> None:
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


@pytest.mark.parametrize("vector", COLUMN_OP_VECTORS, ids=[v["id"] for v in COLUMN_OP_VECTORS])
def test_column_op_vector(vector: dict) -> None:
    expected = vector["expected"]
    op = decode_op_json(json.loads(vector["input"]["op"]))
    assert op.ok, f"{vector['id']}: op decode failed: {getattr(op, 'error', None)}"
    assert encode_op(op.value) == expected["op"], f"{vector['id']}: the op does not re-encode"
    src = decode_source(vector["input"]["source"])
    assert src.ok, f"{vector['id']}: source decode failed: {getattr(src, 'error', None)}"
    assert isinstance(src.value, Embedded)

    result = apply_op(op.value, src.value.table)
    if expected["verdict"] == "error":
        # The error class is never compared; the file carries none.
        assert not result.ok, f"{vector['id']}: this host applied an op the reference refused"
        return
    assert expected["verdict"] == "ok", f"{vector['id']}: unknown verdict {expected['verdict']!r}"
    assert result.ok, f"{vector['id']}: this host refused, the reference applied: {getattr(result, 'error', None)}"
    assert encode_source(Embedded(result.value)) == expected["table"], f"{vector['id']}: this host ≠ the reference"


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
def test_case_kinds_this_host_does_not_run(kind: str) -> None:
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


# ── Phase 2208: the whole entry rides each verb class, and the refusals are the unit check ──


def _units(text: str) -> UnitOfMeasure:
    u = parse_unit(text)
    assert isinstance(u, UnitOfMeasure)
    return u


def _unit_table(*, with_units: bool) -> Table:
    """d metres (labelled), t seconds, dk kilometres, n unitless with a label, description and ext."""

    def unit(text: str) -> UnitOfMeasure | None:
        return _units(text) if with_units else None

    fields = [
        Field("g", "string"),
        Field("d", "int", FieldMeta(unit=unit("m"), label="Distance")),
        Field("t", "float", FieldMeta(unit=unit("s"))),
        Field("dk", "float", FieldMeta(unit=unit("km"))),
        Field("n", "int", FieldMeta(label="Count", description="How many", ext={"acme.k": "v"})),
    ]
    columns = [
        Column("g", "string", [cell_str("a"), cell_str("b")]),
        Column("d", "int", [cell_int(100), cell_int(200)]),
        Column("t", "float", [cell_float(4.0), cell_float(8.0)]),
        Column("dk", "float", [cell_float(0.5), cell_float(1.0)]),
        Column("n", "int", [cell_int(1), cell_int(2)]),
    ]
    return table_of_fields(fields, columns)


def _entry(pipeline: list, name: str) -> Field:
    r = eval_pipeline(pipeline, _unit_table(with_units=True))
    assert r.ok, getattr(r, "error", None)
    return next(f for f in table_fields(r.value) if f.name == name)


def test_unit_algebra_conversion_factor() -> None:
    assert conversion_factor(_units("km/h"), _units("m/s")) == (5, 18)
    assert conversion_factor(_units("[mi_i]"), _units("km")) == (25146, 15625)
    assert conversion_factor(_units("m"), _units("s")) is None
    assert compatible_units(_units("N"), _units("kg.m/s2"))
    assert not compatible_units(_units("[GBP]"), _units("[USD]"))
    assert render_unit(pow_unit(_units("m/s"), 2) or DIMENSIONLESS) == "m2/s2"


def test_kept_a_projection_and_a_join_keep_the_whole_entry() -> None:
    kept = _entry([Project([("n", "count")])], "count").meta
    assert (kept.label, kept.description, kept.ext) == ("Count", "How many", {"acme.k": "v"})
    right = _entry([Join(Embedded(_unit_table(with_units=True)), [("g", "g")], "inner")], "d_right").meta
    assert (right.unit, right.label) == (_units("m"), "Distance")


def test_rebuilt_a_derive_an_aggregate_and_a_window_carry_the_derived_unit_and_no_label() -> None:
    assert _entry([Derive("v", Col("d"))], "v").meta == FieldMeta(unit=_units("m"))
    assert _entry([Derive("n2", Col("n"))], "n2").meta.is_empty
    assert _entry([GroupBy(["g"], [Agg("c", "count", "d")])], "c").meta.is_empty
    assert _entry([GroupBy(["g"], [Agg("s", "sum", "d")])], "s").meta == FieldMeta(unit=_units("m"))
    window = Window(WindowSpec([], [("n", "asc")], "lag", "d", "p"))
    assert _entry([window], "p").meta == FieldMeta(unit=_units("m"))


@pytest.mark.parametrize(
    "step",
    [
        Derive("x", Binary("add", Col("d"), Col("t"))),
        Derive("x", Binary("add", Col("dk"), Col("d"))),
        Filter(Binary("gt", Col("d"), Col("t"))),
        Derive("x", Convert(Col("d"), parse_unit("s"))),  # type: ignore[arg-type]
    ],
    ids=["m + s", "km + m", "m > s", "m to s"],
)
def test_go_red_each_refusal_is_the_unit_check(step: object) -> None:
    refused = eval_pipeline([step], _unit_table(with_units=True))
    assert not refused.ok
    assert refused.error.code == UNIT_MISMATCH
    assert eval_pipeline([step], _unit_table(with_units=False)).ok
