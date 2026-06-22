"""Phase 285 — the polars-like authoring surface emits canonical Compute JSON.

The headline acceptance: a data scientist authors a transform pipeline in idiomatic,
polars-shaped Python and it serialises **byte-identically** to the canonical wire form
the F# and TypeScript tiers produce. The ``grid-transform`` corpus fixture (Phase 282)
is the oracle — authored here end-to-end (source + pipeline + the node envelope) and
asserted equal to the committed fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py.dataframe import eval_pipeline
from fuaran_py.ui import col, encode, frame, lit, node, transform, when
from fuaran_py.ui import fuaran as F

# The grid-transform pipeline, authored once (the headline example).
GRID_FRAME = (
    frame(
        {"dept": ["eng", "eng", "sales"], "amount": [100, 120, None]},
        schema={"dept": "string", "amount": "int"},
    )
    .filter(col("amount") > 0)
    .group_by("dept")
    .agg(col("amount").sum().alias("total"))
    .sort("total", descending=True)
)


def test_transform_json_matches_corpus_source() -> None:
    """``frame(...).to_transform_json()`` == the ``source`` of the grid-transform fixture."""
    if not (CORPUS_ROOT / "manifest.json").is_file():
        pytest.skip("corpus not present")
    fixture = json.loads((CORPUS_ROOT / "nodes" / "grid-transform.json").read_text(encoding="utf-8"))
    expected_source = fixture["kind"]["source"]
    # Re-encode the fixture's own Transform binding canonically for a stable comparison.
    from fuaran_py.canonical import encode_value
    from fuaran_py.model import from_json

    expected = encode_value(from_json(expected_source))
    assert GRID_FRAME.to_transform_json() == expected


@corpus_required
def test_authored_grid_node_matches_corpus_fixture() -> None:
    """Author the whole node — DataGrid over the Transform source — and match the fixture."""
    expected = (CORPUS_ROOT / "nodes" / "grid-transform.json").read_text(encoding="utf-8").rstrip("\n")
    grid = node.bare(F.grid("grid-transform", source=transform(GRID_FRAME)))
    assert encode(grid) == expected


def test_collect_runs_reference_evaluator_locally() -> None:
    """``.collect()`` previews via the same reference evaluator (eng total 220, sales dropped)."""
    table = GRID_FRAME.collect()
    # one row: dept=eng, total=220 (sales' only row had amount NULL → dropped by amount>0)
    assert [c.name for c in table.columns] == ["dept", "total"]
    dept = next(c for c in table.columns if c.name == "dept")
    total = next(c for c in table.columns if c.name == "total")
    assert [c.value for c in dept.cells] == ["eng"]
    assert [c.value for c in total.cells] == [220]


def test_expression_dsl_builds_algebra() -> None:
    """The operator-overloaded DSL builds the serializable ColExpr algebra."""
    fr = (
        frame({"x": [1, 2, 3], "y": [10, 20, 30]}, schema={"x": "int", "y": "int"})
        .with_column("z", (col("x") + col("y")) * 2)
        .with_column("flag", (col("x") > 1) & (col("y") < 30))
        .with_column("bucket", when(col("x") > 2).then("big").otherwise("small"))
        .filter(~col("flag").eq(lit(False)))
    )
    # round-trips through the canonical codec (decode == author)
    from fuaran_py.dataframe import decode_pipeline, encode_pipeline

    wire = fr.to_pipeline_json()
    decoded = decode_pipeline(wire)
    assert decoded.ok
    assert encode_pipeline(decoded.value) == wire


def test_join_and_window_author() -> None:
    left = frame({"id": [1, 2, 3], "name": ["a", "b", "c"]}, schema={"id": "int", "name": "string"})
    right = frame({"id": [1, 2], "score": [10, 20]}, schema={"id": "int", "score": "int"})
    joined = left.join(right, on="id", how="left")
    result = joined.collect()
    # reference join keeps left cols ++ right cols; a name collision is suffixed _right.
    assert [c.name for c in result.columns] == ["id", "name", "id_right", "score"]

    windowed = frame({"g": ["a", "a", "b"], "v": [10, 20, 5]}, schema={"g": "string", "v": "int"}).window(
        "rowNumber", of="v", as_="rn", partition_by=["g"], order_by="v"
    )
    wt = windowed.collect()
    assert [c.name for c in wt.columns] == ["g", "v", "rn"]


def test_static_artifact_round_trips_through_evaluator() -> None:
    """The emitted pipeline JSON decodes + evaluates to the same preview (the artifact contract)."""
    from fuaran_py.dataframe import Embedded, decode_pipeline, decode_source

    src = decode_source(_source_json(GRID_FRAME))
    pipe = decode_pipeline(GRID_FRAME.to_pipeline_json())
    assert src.ok and pipe.ok and isinstance(src.value, Embedded)
    rerun = eval_pipeline(pipe.value, src.value.table)
    assert rerun.ok
    preview = GRID_FRAME.collect()
    from fuaran_py.dataframe import encode_source

    assert encode_source(Embedded(rerun.value)) == encode_source(Embedded(preview))


def _source_json(fr) -> str:  # type: ignore[no-untyped-def]
    from fuaran_py.dataframe import encode_source

    return encode_source(fr.source)


# Keep Path imported for the conformance helper's type even when corpus is absent.
_ = Path
