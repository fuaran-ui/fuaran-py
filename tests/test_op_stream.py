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

import threading
from collections.abc import Callable
from dataclasses import replace

import pytest

from _corpus import CORPUS_ROOT, chain_corpus, chain_corpus_required, chain_records
from fuaran_ui import decode_node, decode_op
from fuaran_ui.model import Node, Obj
from fuaran_ui.op_stream import (
    CHAIN_FORMAT_VERSION,
    GENESIS_PREVIOUS_HASH,
    AgentActor,
    Appended,
    ApplyFailed,
    Failure,
    HashMismatch,
    HumanActor,
    InMemorySink,
    OpRecord,
    OutOfOrder,
    PersistContext,
    PersistResult,
    PreviousHashMismatch,
    SinkAppendError,
    StaleHead,
    Success,
    apply_and_persist,
    apply_to,
    compute_hash,
    encode_stream_entry,
    format_version,
    replay_stream,
    verify_chain,
)
from fuaran_ui.op_stream.replay import _Gap, _previous_hash_for
from fuaran_ui.op_stream.types import Actor, OpResultEnvelope


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
        '{"id":"root","kind":{"$type":"Box","children":['
        '{"id":"leaf","kind":{"$type":"Markdown","text":{"$type":"Literal","text":"a"}}},'
        '{"id":"leaf2","kind":{"$type":"Markdown","text":{"$type":"Literal","text":"b"}}}'
        '],"layout":{"$type":"Flex","direction":"Vertical","wrap":false},"role":"Card"}}'
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


def _record(sequence: int, op: Obj, envelope: OpResultEnvelope) -> OpRecord:
    """A record for the replay legs below. Replay never verifies the chain (that
    is :func:`verify_chain`'s job), so the hashes here are placeholders and the
    ``result_envelope`` is the only field under test."""
    return OpRecord(
        stream_id="s",
        sequence=sequence,
        previous_hash=GENESIS_PREVIOUS_HASH,
        hash=f"h{sequence}",
        op=op,
        actor=HumanActor("u"),
        timestamp_unix_seconds=1700000000,
        result_envelope=envelope,
    )


def _chain_with_a_refusal() -> list[OpRecord]:
    """Two applied ops with a RECORDED REFUSAL between them.

    The middle record is what a host that records denials actually writes: the
    op was refused, so it never touched the tree, and its ``Failure`` envelope
    says so. Replaying it would remove a node that is already gone.
    """
    return [
        _record(1, Obj("RemoveNode", {"target": "leaf"}), Success()),
        _record(2, Obj("RemoveNode", {"target": "leaf"}), Failure("E_CONFLICT", "refused")),
        _record(3, Obj("RemoveNode", {"target": "leaf2"}), Success()),
    ]


def test_replay_skips_recorded_refusals_by_default() -> None:
    """A chain carrying a ``Failure`` record replays cleanly.

    The envelope is part of the type contract precisely so a denial can be
    recorded, so a replay that folds one is folding an edit the stream says
    never happened — and it fails on the record that says it failed.
    """
    tree = _two_child_card()
    result = apply_to(tree, _chain_with_a_refusal())
    assert result.ok, result

    only_successes = apply_to(
        tree,
        [r for r in _chain_with_a_refusal() if isinstance(r.result_envelope, Success)],
    )
    assert only_successes.ok
    assert result.value == only_successes.value


def test_replay_folds_every_record_when_the_caller_asks() -> None:
    """``include_refused=True`` is the explicit selector for a caller that wants
    the literal fold — an audit rebuild, or a host whose refusals are advisory
    rather than final. It restores the pre-selector behaviour exactly, which is
    why the refused op here fails at its own sequence."""
    result = apply_to(_two_child_card(), _chain_with_a_refusal(), include_refused=True)
    assert not result.ok
    assert isinstance(result.error, ApplyFailed)
    assert result.error.sequence == 2


def test_replay_stream_threads_the_selector_through() -> None:
    """The sink-reading entry point takes the same keyword — the skip is not a
    property of the in-memory list form alone."""
    sink = InMemorySink()
    for record in _chain_with_a_refusal():
        sink.append(record)
    tree = _two_child_card()

    assert replay_stream(sink, "s", tree).ok
    assert not replay_stream(sink, "s", tree, include_refused=True).ok


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


def test_sink_rejects_a_duplicate_BELOW_the_head_sequence() -> None:
    """The discriminator for the plausible wrong duplicate guard (Phase 1654).

    The guard was an O(n) scan of the whole log per append. Indexing it is
    correct, but only against the set of sequences PRESENT — an index of the
    maximum alone (which the sink already keeps, for ``latest_sequence`` and
    ``head``) would admit a re-append of any sequence below the head, which is
    exactly what a gap-fill or a replayed partial batch offers. That admission
    would be an overwrite in a sink whose stated contract is that it refuses
    them, and the append would look entirely successful.
    """
    sink = InMemorySink()

    def _rec(sequence: int) -> OpRecord:
        return OpRecord(
            stream_id="s",
            sequence=sequence,
            previous_hash=GENESIS_PREVIOUS_HASH,
            hash=f"h{sequence}",
            op=Obj("RemoveNode", {"target": f"n{sequence}"}),
            actor=HumanActor("u"),
            timestamp_unix_seconds=1700000000,
        )

    for sequence in (1, 2, 3):
        sink.append(_rec(sequence))
    assert sink.latest_sequence("s") == 3

    with pytest.raises(ValueError, match="duplicate"):
        sink.append(_rec(2))
    assert len(sink.replay("s", 0, 99)) == 3

    # And the guard is per stream, not global — the same sequence on a second
    # stream is an ordinary append.
    other = OpRecord(
        stream_id="t",
        sequence=2,
        previous_hash=GENESIS_PREVIOUS_HASH,
        hash="h2t",
        op=Obj("RemoveNode", {"target": "n2"}),
        actor=HumanActor("u"),
        timestamp_unix_seconds=1700000000,
    )
    sink.append(other)
    assert sink.latest_sequence("t") == 2


# ── Compare-and-append — concurrent writers ──────────────────────────────────


class _FirstPairGate:
    """Makes the first TWO callers — across however many threads reach it, in
    whichever order the OS scheduler picks — return the exact SAME value:
    whatever the very first call actually computes. A call beyond the first
    two reads live.

    This is deliberately not a :class:`threading.Barrier`: a barrier only
    synchronises *arrival*, and two threads released together can still run
    their next line of Python in either order (or one can run to completion
    before the other resumes at all — entirely possible under the GIL for a
    lock-only critical section with no I/O). Caching the first call's result
    for the second caller to reuse makes "two readers observe identical stale
    state" a deterministic property of the test, not a hopeful side effect of
    thread scheduling.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0
        self._cached: object = None

    def read(self, compute: Callable[[], object]) -> object:
        with self._lock:
            if self._count == 0:
                self._cached = compute()
            use_cache = self._count < 2
            self._count += 1
            if use_cache:
                return self._cached
        return compute()


class _ForcedRaceSink(InMemorySink):
    """An :class:`InMemorySink` whose ``head`` and ``latest_sequence`` each
    gate their first two callers through their own :class:`_FirstPairGate`.

    Both of :mod:`~fuaran_ui.op_stream.replay`'s write paths read one or both
    of these as their very first step, before either caller has appended
    anything: the read-then-append fallback calls `latest_sequence` once; the
    compare-and-append path calls `head` then `latest_sequence` once per
    attempt, and only a RETRY (from whichever thread lost the race) calls
    either a third time — by which point each gate's pair is already spent,
    so a retry always reads live. Gating both methods independently — rather
    than only `latest_sequence` — matters for the compare-and-append path
    specifically: `_persist_via_cas` reads `head` before `latest_sequence`,
    and real time only moves forward, so in genuine concurrent execution a
    `head` read can never be FRESHER than the `latest_sequence` read that
    follows it. Gating only `latest_sequence` breaks that invariant (an
    artefact of the gate, not of the code under test) — the second thread
    would see a live `head` paired with a stale cached `sequence`, a
    combination `append_if` never protects against because real execution
    cannot produce it, and the sink's own duplicate-sequence guard would raise
    OUT of `append_if` instead of yielding the `StaleHead` the retry loop
    knows how to handle. Gating both restores a consistent stale snapshot for
    the second caller, exactly like a real race.
    """

    def __init__(self) -> None:
        super().__init__()
        self._head_gate = _FirstPairGate()
        self._sequence_gate = _FirstPairGate()

    def head(self, stream_id: str) -> str:
        value = self._head_gate.read(lambda: InMemorySink.head(self, stream_id))
        assert isinstance(value, str)
        return value

    def latest_sequence(self, stream_id: str) -> int:
        value = self._sequence_gate.read(lambda: InMemorySink.latest_sequence(self, stream_id))
        assert isinstance(value, int)
        return value


def test_two_concurrent_writers_both_persist_via_compare_and_append() -> None:
    """Two threads racing `apply_and_persist` on the same stream must both end
    up durably persisted, at contiguous sequences, with neither told it
    succeeded while its op was actually lost.

    This is the regression test for the read-then-append race: a plain
    `latest_sequence` + `append` write path lets both threads compute the same
    next sequence, and the loser's `append` used to raise into a default
    `on_sink_error` hook that discarded it silently. Confirmed by hand: with
    `apply_and_persist`'s `isinstance(sink, CasOpStreamSink)` branch forced to
    always take the read-then-append path, this test fails — one thread's op
    never reaches the sink, `sink.latest_sequence("s")` stops at 1, and
    `errors` gains a `SinkAppendError` wrapping a plain duplicate-sequence
    `ValueError` instead of staying empty. Restored immediately after
    confirming that failure; not pinned as a second code path here.
    """
    sink = _ForcedRaceSink()
    tree = _two_child_card()
    results: dict[str, PersistResult] = {}
    errors: list[Exception] = []

    def _persist(name: str, target: str) -> None:
        ctx = PersistContext(
            stream_id="s",
            user_id=name,
            now=lambda: 1700000000,
            on_sink_error=errors.append,
        )
        results[name] = apply_and_persist(sink, ctx, Obj("RemoveNode", {"target": target}), tree)

    t1 = threading.Thread(target=_persist, args=("alice", "leaf"))
    t2 = threading.Thread(target=_persist, args=("bob", "leaf2"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    # Both callers saw a successful apply — durability is best-effort, but
    # neither actually lost its op underneath that success.
    assert results["alice"].ok
    assert results["bob"].ok

    records = _chain_of(sink, "s")
    assert [r.sequence for r in records] == [1, 2]
    assert verify_chain(records) is None
    persisted_targets = {r.op.fields["target"] for r in records}
    assert persisted_targets == {"leaf", "leaf2"}
    # The race was real (one attempt lost it and had to retry) but resolved —
    # no failure ever reached the caller's hook.
    assert errors == []


def test_append_if_at_a_stale_head_persists_nothing() -> None:
    sink = InMemorySink()
    first = OpRecord(
        stream_id="s",
        sequence=1,
        previous_hash=GENESIS_PREVIOUS_HASH,
        hash="h1",
        op=Obj("RemoveNode", {"target": "n1"}),
        actor=HumanActor("u"),
        timestamp_unix_seconds=1700000000,
    )
    outcome = sink.append_if(first, GENESIS_PREVIOUS_HASH)
    assert isinstance(outcome, Appended)
    assert outcome.receipt.sequence == 1
    assert sink.head("s") == "h1"

    stale = OpRecord(
        stream_id="s",
        sequence=2,
        previous_hash="not-the-real-head",
        hash="h2",
        op=Obj("RemoveNode", {"target": "n2"}),
        actor=HumanActor("u"),
        timestamp_unix_seconds=1700000000,
    )
    outcome = sink.append_if(stale, "not-the-real-head")
    assert isinstance(outcome, StaleHead)
    assert outcome.expected == "not-the-real-head"
    assert outcome.actual == "h1"
    # Nothing was persisted for the rejected attempt.
    assert sink.latest_sequence("s") == 1
    assert sink.replay("s", 2, 2) == []
    assert sink.head("s") == "h1"


class _GappySink:
    """Minimal :class:`~fuaran_ui.op_stream.types.OpStreamSink` stand-in whose
    ``replay`` reports nothing for any range, regardless of ``latest_sequence``
    — a sink whose own bookkeeping has a hole in it. Deliberately does NOT
    implement :class:`~fuaran_ui.op_stream.types.CasOpStreamSink`, so
    ``apply_and_persist`` takes the read-then-append path this scenario is
    about."""

    def append(self, record: OpRecord) -> None:
        raise AssertionError("a detected gap must refuse the write, not attempt it")

    def replay(self, stream_id: str, from_sequence: int, to_sequence: int) -> list[OpRecord]:
        return []

    def latest_sequence(self, stream_id: str) -> int:
        return 5

    def streams(self) -> list[str]:
        return []


def test_previous_hash_for_refuses_a_gap_rather_than_returning_genesis() -> None:
    result = _previous_hash_for(_GappySink(), "s", 5)
    assert isinstance(result, _Gap)
    assert result.missing_sequence == 4


def test_apply_and_persist_reports_a_gap_instead_of_writing_over_it() -> None:
    sink = _GappySink()
    errors: list[Exception] = []
    ctx = PersistContext(stream_id="s", user_id="u", now=lambda: 1700000000, on_sink_error=errors.append)

    result = apply_and_persist(sink, ctx, Obj("RemoveNode", {"target": "leaf"}), _two_child_card())

    # The apply itself still succeeded — durability is best-effort, so a gap
    # in the sink's own bookkeeping must not turn a successful edit into a
    # PersistErr.
    assert result.ok
    assert len(errors) == 1
    assert isinstance(errors[0], SinkAppendError)
    assert "gap" in str(errors[0])
