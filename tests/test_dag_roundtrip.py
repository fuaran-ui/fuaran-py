"""DAG-record conformance: ``encode(decode(input)) == expectedFile`` byte-for-byte.

Parametrized over every fixture in the additive ``dag/`` sub-corpus (its own
manifest), plus hand-built round-trips for the wire surfaces the curated fixtures
don't reach (the ``Failure`` envelope), and decode-error checks.

Phase 1144 typed the record's author: the bare ``userId`` member became the
``actor`` object the linear chain has carried since Phase 320, moving to the
front of the Ordinal-sorted envelope. The refusal cases below are the load-
bearing half — the actor is inside the reference host's content address, so a
pre-1144 envelope must be REFUSED rather than lifted, and a malformed actor must
be named rather than defaulted.
"""

from __future__ import annotations

import json

import pytest

from _corpus import DAG_CORPUS_ROOT, dag_corpus_required, dag_fixtures
from fuaran_py import DagOpRecord, DagResultEnvelope, decode_dag_record, encode_dag_record
from fuaran_py.conformance import run_fixture
from fuaran_py.model import Obj
from fuaran_py.op_stream import AgentActor, HumanActor


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
        '{"actor":{"kind":"human","id":"u1"},"hash":"abc",'
        '"op":{"$type":"RemoveNode","target":"n1"},"parents":[],'
        '"resultEnvelope":{"$type":"Failure","code":"E_CONFLICT","message":"refused"},'
        '"streamId":"s1","timestamp":1700000000,"tombstoned":false}'
    )
    assert _roundtrip(wire) == wire


def test_failure_envelope_with_agent_actor_roundtrips() -> None:
    # The Failure branch crossed with the Agent actor case — no curated fixture
    # reaches both at once, and the agent case carries three pinned members.
    wire = (
        '{"actor":{"kind":"agent","model":"claude","version":"4.8","id":"planner"},'
        '"hash":"abc","op":{"$type":"RemoveNode","target":"n1"},"parents":[],'
        '"resultEnvelope":{"$type":"Failure","code":"E_CONFLICT","message":"refused"},'
        '"streamId":"s1","timestamp":1700000000,"tombstoned":false}'
    )
    assert _roundtrip(wire) == wire


def test_merge_outcome_hash_roundtrips() -> None:
    wire = (
        '{"actor":{"kind":"human","id":"merge"},"hash":"m1",'
        '"op":{"$type":"Batch","ops":[]},'
        '"outcomeHash":"deadbeef","parents":["p1","p2"],'
        '"resultEnvelope":{"$type":"Success"},"streamId":"s1",'
        '"timestamp":1700000120,"tombstoned":false}'
    )
    assert _roundtrip(wire) == wire


def _full_record(**overrides: object) -> DagOpRecord:
    fields: dict = {
        "stream_id": "s1",
        "hash": "h1",
        "parents": ("p1", "p2"),
        "op": Obj("RemoveNode", {"target": "n1"}),
        "actor": HumanActor("u1"),
        "timestamp": 1700000000,
        "result_envelope": DagResultEnvelope("Success"),
        "tombstoned": False,
        "outcome_hash": "o1",
        "prompt_id": "prompt-1",
    }
    fields.update(overrides)
    return DagOpRecord(**fields)


def test_top_level_keys_are_ordinal_sorted() -> None:
    """`encode_dag_record` splices the pinned `actor` value in AHEAD of the
    canonically-encoded remainder, which is only valid while `actor` is the
    Ordinal-least top-level key. Pin the whole envelope's key order over a record
    carrying every optional member, so a future key sorting before `actor` fails
    here rather than emitting out-of-order bytes."""
    encoded = encode_dag_record(_full_record())
    keys = list(json.JSONDecoder(object_pairs_hook=lambda pairs: dict(pairs)).decode(encoded))
    assert keys == sorted(keys)
    assert keys[0] == "actor"


def test_actor_members_are_pinned_not_sorted() -> None:
    """The nested actor keeps its PINNED member order (`kind` first, then the case
    fields) — it is embedded verbatim, not re-sorted like the envelope around it.
    Ordinal-sorting the agent case would emit `id, kind, model, version`."""
    encoded = encode_dag_record(_full_record(actor=AgentActor("claude", "4.8", "planner")))
    assert '"actor":{"kind":"agent","model":"claude","version":"4.8","id":"planner"}' in encoded


def test_encode_omits_absent_optionals() -> None:
    record = DagOpRecord(
        stream_id="s1",
        hash="h1",
        parents=(),
        op=Obj("RemoveNode", {"target": "n1"}),
        actor=HumanActor("u1"),
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


def test_pre_1144_user_id_envelope_is_refused_by_name() -> None:
    """A pre-1144 record is REFUSED, never lifted to `Human`. The actor is inside
    the reference host's content address, so a lifted record would carry a stored
    `hash` no host can reproduce — the refusal must name the cause here rather
    than surface later as an unexplained verification failure."""
    wire = (
        '{"hash":"abc","op":{"$type":"RemoveNode","target":"n1"},"parents":[],'
        '"resultEnvelope":{"$type":"Success"},"streamId":"s1",'
        '"timestamp":1700000000,"tombstoned":false,"userId":"u1"}'
    )
    result = decode_dag_record(wire)
    assert not result.ok
    assert result.error.code == "MISSING_FIELD"
    assert result.error.path == "$.actor"
    assert "userId" in result.error.message


@pytest.mark.parametrize(
    ("actor_json", "code", "path"),
    [
        ('"u1"', "WRONG_TYPE", "$.actor"),
        ('{"id":"u1"}', "MISSING_FIELD", "$.actor.kind"),
        ('{"kind":"human"}', "MISSING_FIELD", "$.actor.id"),
        ('{"kind":"agent","model":"claude","id":"planner"}', "MISSING_FIELD", "$.actor.version"),
        ('{"kind":"service","id":"bot"}', "UNKNOWN_DU_CASE", "$.actor.kind"),
    ],
    ids=["bare-string", "no-kind", "human-no-id", "agent-no-version", "unknown-kind"],
)
def test_malformed_actor_is_named_not_defaulted(actor_json: str, code: str, path: str) -> None:
    """Every actor defect is a named refusal. A defaulted or guessed actor would
    silently invalidate the record's own content address."""
    wire = (
        f'{{"actor":{actor_json},"hash":"abc",'
        '"op":{"$type":"RemoveNode","target":"n1"},"parents":[],'
        '"resultEnvelope":{"$type":"Success"},"streamId":"s1",'
        '"timestamp":1700000000,"tombstoned":false}'
    )
    result = decode_dag_record(wire)
    assert not result.ok
    assert result.error.code == code
    assert result.error.path == path
