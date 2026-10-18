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
"""

from __future__ import annotations

import json
import warnings

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui.dataframe import (
    Embedded,
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
    assert encode_source(Embedded(result.value)) == expected["table"], f"{vector['id']}: this host ≠ the reference"


@pytest.mark.parametrize("kind", sorted(NOT_RUN))
def test_case_kinds_this_host_has_no_surface_for(kind: str) -> None:
    ids = [v["id"] for v in VECTORS if v["case"] == kind]
    pytest.skip(f"{len(ids)} {kind} vector(s) not run — {NOT_RUN[kind]}: {', '.join(ids)}")
