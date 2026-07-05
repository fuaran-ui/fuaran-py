"""Op-stream hash-chain conformance + behaviour.

The load-bearing test is :func:`test_chain_corpus_reproduces_golden_hashes`: the
Python host must reproduce the committed chain hashes in
``wire-format-fixtures/chain/chain-corpus.json`` byte-for-byte — the same golden
the F# ``ChainCorpusTests`` and TS parity tests consume. A mismatch is a bug in
this host's encoder / chain, never in the corpus.

The rest exercise the wire surfaces the curated golden does not reach (the
``Failure`` outcome, ``format_version``, tamper detection) plus the sink / replay /
apply-and-persist behaviour.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from _corpus import CORPUS_ROOT, chain_corpus, chain_corpus_required, chain_records
from fuaran_py import decode_node, decode_op
from fuaran_py.model import Node, Obj
from fuaran_py.op_stream import (
    CHAIN_FORMAT_VERSION,
    GENESIS_PREVIOUS_HASH,
    AgentActor,
    Failure,
    HashMismatch,
    HumanActor,
    InMemorySink,
    OpRecord,
    OutOfOrder,
    PersistContext,
    PreviousHashMismatch,
    Success,
    apply_and_persist,
    compute_hash,
    encode_stream_entry,
    format_version,
    replay_stream,
    verify_chain,
)
from fuaran_py.op_stream.types import Actor, OpResultEnvelope


def _actor_of(spec: dict) -> Actor:
    if spec["kind"] == "human":
        return HumanActor(spec["id"])
    return AgentActor(spec["model"], spec["version"], spec["id"])


def _result_of(spec: dict) -> OpResultEnvelope:
    if spec["kind"] == "success":
        return Success()
    return Failure(spec["code"], spec["message"])


def _op_of(fixture_rel_path: str) -> Obj:
    text = (CORPUS_ROOT / fixture_rel_path).read_text(encoding="utf-8")
    decoded = decode_op(text)
    assert decoded.ok, decoded
    return decoded.value


def _record_of(rec: dict) -> OpRecord:
    return OpRecord(
        stream_id="s",
        sequence=rec["sequence"],
        previous_hash=rec["previousHash"],
        hash=rec["hash"],
        op=_op_of(rec["opFixture"]),
        actor=_actor_of(rec["actor"]),
        timestamp_unix_seconds=rec["timestampUnixSeconds"],
        result_envelope=_result_of(rec["result"]),
        prompt_id=rec["promptId"],
    )


# ── The conformance contract ─────────────────────────────────────────────────


@chain_corpus_required
def test_chain_corpus_format_version_is_pinned() -> None:
    corpus = chain_corpus()
    assert corpus["version"] == CHAIN_FORMAT_VERSION
    assert corpus["genesisPreviousHash"] == GENESIS_PREVIOUS_HASH


@chain_corpus_required
@pytest.mark.parametrize("rec", chain_records(), ids=lambda r: f"seq{r['sequence']}")
def test_chain_corpus_reproduces_golden_hashes(rec: dict) -> None:
    computed = compute_hash(
        rec["previousHash"],
        _op_of(rec["opFixture"]),
        rec["sequence"],
        rec["timestampUnixSeconds"],
        _actor_of(rec["actor"]),
        rec["promptId"],
        _result_of(rec["result"]),
    )
    assert computed == rec["hash"], (
        f"seq {rec['sequence']}: computed {computed} != committed {rec['hash']} — "
        "the bug is in the Python encoder/chain, not the corpus"
    )


@chain_corpus_required
def test_chain_corpus_verifies_as_a_chain() -> None:
    records = [_record_of(r) for r in chain_records()]
    assert verify_chain(records) is None


# ── Wire surfaces the golden does not reach + integrity behaviour ────────────


def test_failure_envelope_is_folded_into_the_hash() -> None:
    op = Obj("RemoveNode", {"target": "n1"})
    base = dict(
        previous_hash=GENESIS_PREVIOUS_HASH,
        op=op,
        sequence=1,
        timestamp_unix_seconds=1700000000,
        actor=HumanActor("u"),
        prompt_id=None,
    )
    success = compute_hash(**base, result_envelope=Success())
    failure = compute_hash(**base, result_envelope=Failure("E_CONFLICT", "refused"))
    # Flipping the recorded outcome must change the hash — the outcome is inside the chain.
    assert success != failure


def test_prompt_id_is_folded_into_the_hash() -> None:
    op = Obj("RemoveNode", {"target": "n1"})
    base = dict(
        previous_hash=GENESIS_PREVIOUS_HASH,
        op=op,
        sequence=1,
        timestamp_unix_seconds=1700000000,
        actor=HumanActor("u"),
        result_envelope=Success(),
    )
    assert compute_hash(**base, prompt_id=None) != compute_hash(**base, prompt_id="p-1")


def test_format_version_reads_the_leading_v() -> None:
    envelope = encode_stream_entry(Obj("RemoveNode", {"target": "n1"}), 1700000000, None, Success())
    assert envelope.startswith('{"v":2,')
    assert format_version(envelope) == CHAIN_FORMAT_VERSION
    # A tagless / non-v envelope reads as the pre-v2 format.
    assert format_version('{"op":{}}') is None


def _chain_of(sink: InMemorySink, stream_id: str) -> list[OpRecord]:
    return sink.replay(stream_id, 1, sink.latest_sequence(stream_id))


def test_verify_chain_catches_a_tampered_hash() -> None:
    r = OpRecord(
        stream_id="s",
        sequence=1,
        previous_hash=GENESIS_PREVIOUS_HASH,
        hash="deadbeef" * 8,  # not the real hash
        op=Obj("RemoveNode", {"target": "n1"}),
        actor=HumanActor("u"),
        timestamp_unix_seconds=1700000000,
    )
    error = verify_chain([r])
    assert isinstance(error, HashMismatch)
    assert error.sequence == 1


def test_verify_chain_catches_a_broken_previous_hash_link() -> None:
    sink = InMemorySink()
    ctx = PersistContext(stream_id="s", user_id="u", now=lambda: 1700000000)
    tree = _two_child_card()
    r1 = apply_and_persist(sink, ctx, Obj("RemoveNode", {"target": "leaf"}), tree)
    apply_and_persist(sink, ctx, Obj("RemoveNode", {"target": "leaf2"}), r1.value)
    records = _chain_of(sink, "s")
    assert len(records) == 2
    # Snap the second record's previous_hash so the link is broken.
    broken = [records[0], replace(records[1], previous_hash="0" * 64)]
    error = verify_chain(broken)
    assert isinstance(error, PreviousHashMismatch)
    assert error.sequence == 2


def test_verify_chain_catches_out_of_order() -> None:
    r2 = OpRecord(
        stream_id="s",
        sequence=2,
        previous_hash=GENESIS_PREVIOUS_HASH,
        hash="x",
        op=Obj("RemoveNode", {"target": "n1"}),
        actor=HumanActor("u"),
        timestamp_unix_seconds=1700000000,
    )
    error = verify_chain([r2])
    assert isinstance(error, OutOfOrder)
    assert error.expected_sequence == 1
    assert error.actual_sequence == 2


# ── Sink + apply-and-persist + replay behaviour ──────────────────────────────


def _two_child_card() -> Node:
    """A tiny two-child card the structural ops can act on."""
    result = decode_node(
        '{"id":"root","kind":{"$type":"Card","children":['
        '{"id":"leaf","kind":{"$type":"Markdown","text":{"$type":"Literal","text":"a"}}},'
        '{"id":"leaf2","kind":{"$type":"Markdown","text":{"$type":"Literal","text":"b"}}}'
        "]}}"
    )
    assert result.ok, result
    return result.value


def test_apply_and_persist_builds_a_verifiable_chain() -> None:
    sink = InMemorySink()
    ctx = PersistContext(stream_id="s", user_id="alice", now=lambda: 1700000000)
    tree = _two_child_card()

    r1 = apply_and_persist(sink, ctx, Obj("RemoveNode", {"target": "leaf"}), tree)
    assert r1.ok
    r2 = apply_and_persist(sink, ctx, Obj("RemoveNode", {"target": "leaf2"}), r1.value)
    assert r2.ok

    records = _chain_of(sink, "s")
    assert [r.sequence for r in records] == [1, 2]
    assert records[0].previous_hash == GENESIS_PREVIOUS_HASH
    assert records[1].previous_hash == records[0].hash
    assert verify_chain(records) is None


def test_apply_and_persist_leaves_sink_untouched_on_apply_failure() -> None:
    sink = InMemorySink()
    ctx = PersistContext(stream_id="s", user_id="u", now=lambda: 1700000000)
    result = apply_and_persist(sink, ctx, Obj("RemoveNode", {"target": "nope"}), _two_child_card())
    assert not result.ok
    assert sink.latest_sequence("s") == 0


def test_replay_reproduces_the_final_tree() -> None:
    sink = InMemorySink()
    ctx = PersistContext(stream_id="s", user_id="u", now=lambda: 1700000000)
    tree = _two_child_card()
    applied = apply_and_persist(sink, ctx, Obj("RemoveNode", {"target": "leaf"}), tree)
    assert applied.ok

    replayed = replay_stream(sink, "s", tree)
    assert replayed.ok
    assert replayed.value == applied.value


def test_sink_rejects_duplicate_sequence() -> None:
    sink = InMemorySink()
    r = OpRecord(
        stream_id="s",
        sequence=1,
        previous_hash=GENESIS_PREVIOUS_HASH,
        hash="h",
        op=Obj("RemoveNode", {"target": "n1"}),
        actor=HumanActor("u"),
        timestamp_unix_seconds=1700000000,
    )
    sink.append(r)
    with pytest.raises(ValueError, match="duplicate"):
        sink.append(r)
