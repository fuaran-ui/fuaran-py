"""DAG-record conformance: ``encode(decode(input)) == expectedFile`` byte-for-byte.

Parametrized over every fixture in the additive ``dag/`` sub-corpus (its own
manifest), plus hand-built round-trips for the wire surfaces the curated fixtures
don't reach (the ``Failure`` envelope), and a decode-error check.
"""

from __future__ import annotations

import pytest

from _corpus import DAG_CORPUS_ROOT, dag_corpus_required, dag_fixtures
from fuaran_py import DagOpRecord, DagResultEnvelope, decode_dag_record, encode_dag_record
from fuaran_py.conformance import run_fixture
from fuaran_py.model import Obj


@dag_corpus_required
@pytest.mark.parametrize("fixture", dag_fixtures(), ids=lambda fx: fx["id"])
def test_dag_roundtrip(fixture: dict) -> None:
    result = run_fixture(fixture, DAG_CORPUS_ROOT)
    assert result.passed, f"{fixture['id']}: {result.detail}"


def _roundtrip(text: str) -> str:
    decoded = decode_dag_record(text)
    assert decoded.ok, decoded
    return encode_dag_record(decoded.value)


def test_failure_envelope_roundtrips() -> None:
    # No curated fixture carries a Failure envelope; assert the branch is byte-stable.
    wire = (
        '{"hash":"abc","op":{"$type":"RemoveNode","target":"n1"},"parents":[],'
        '"resultEnvelope":{"$type":"Failure","code":"E_CONFLICT","message":"refused"},'
        '"streamId":"s1","timestamp":1700000000,"tombstoned":false,"userId":"u1"}'
    )
    assert _roundtrip(wire) == wire


def test_merge_outcome_hash_roundtrips() -> None:
    wire = (
        '{"hash":"m1","op":{"$type":"Batch","ops":[]},'
        '"outcomeHash":"deadbeef","parents":["p1","p2"],'
        '"resultEnvelope":{"$type":"Success"},"streamId":"s1",'
        '"timestamp":1700000120,"tombstoned":false,"userId":"merge"}'
    )
    assert _roundtrip(wire) == wire


def test_encode_omits_absent_optionals() -> None:
    record = DagOpRecord(
        stream_id="s1",
        hash="h1",
        parents=(),
        op=Obj("RemoveNode", {"target": "n1"}),
        user_id="u1",
        timestamp=1700000000,
        result_envelope=DagResultEnvelope("Success"),
        tombstoned=False,
    )
    encoded = encode_dag_record(record)
    assert "outcomeHash" not in encoded
    assert "promptId" not in encoded


def test_missing_required_field_is_rejected() -> None:
    result = decode_dag_record('{"op":{"$type":"RemoveNode","target":"n1"},"parents":[]}')
    assert not result.ok
    assert result.error.code == "MISSING_FIELD"
