"""The durable op-trace type contract — the Python twin of the F#/TS op-stream.

One stream's worth of applied ``TreeOp`` edits is an append-only, hash-chained
sequence of :class:`OpRecord` values; the hash chain (:mod:`.hash_chain`) makes
"what did the author do?" and "was the stream tampered with?" both answerable from
the record sequence alone (the op stream is the source of truth).

Modelling conventions, mirroring the sibling hosts:

* A tagged union becomes a small set of frozen dataclasses joined by a
  ``type`` alias, discriminated by ``isinstance`` (the case name verbatim).
* An optional field is ``| None`` with a ``None`` default; **absent is ``None``**.
* The sink contract is synchronous — a decoded record is a plain value, the
  in-memory sink computes its answers directly. A genuinely I/O-backed sink
  (SQLite, a file journal) implements the same :class:`OpStreamSink` protocol and
  may wrap its own concurrency; the type contract does not force ``async`` on the
  in-memory path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..model import Node, Obj
from ..ops import ApplyError

# ── Actor (typed attested provenance) ────────────────────────────────────────


@dataclass(frozen=True)
class HumanActor:
    """A person / account id — the load-bearing accountability case."""

    id: str


@dataclass(frozen=True)
class AgentActor:
    """An AI author; ``model`` / ``version`` double as corpus-quality metadata."""

    model: str
    version: str
    id: str


#: Who authored an op. The canonical encoding (:func:`.hash_chain.encode_actor`)
#: is folded into the record hash, so attribution is tamper-evident.
type Actor = HumanActor | AgentActor


def actor_id(actor: Actor) -> str:
    """The stable attribution id — the user id (human) or the agent id (agent)."""
    return actor.id


def human_actor(user_id: str) -> Actor:
    """Lift a bare-string user id to the typed ``Human`` case."""
    return HumanActor(user_id)


# ── Apply-outcome envelope ───────────────────────────────────────────────────


@dataclass(frozen=True)
class Success:
    """A successful apply — a bare tag with no payload."""


@dataclass(frozen=True)
class Failure:
    """A recorded apply failure — its code + message are folded into the hash."""

    code: str
    message: str


#: The outcome captured at ``append`` time; both cases fold into the chain hash,
#: so flipping a recorded ``Failure`` to ``Success`` breaks verification.
type OpResultEnvelope = Success | Failure


#: The common no-payload success outcome.
SUCCESS: OpResultEnvelope = Success()


# ── The append-only record + checkpoint ──────────────────────────────────────


@dataclass(frozen=True)
class OpRecord:
    """One stream-position's worth of apply trace.

    Append-only — sinks reject duplicate ``(stream_id, sequence)`` pairs as
    structural defects. Sequences begin at 1; the ``previous_hash`` of the
    ``sequence == 1`` record is :data:`.hash_chain.GENESIS_PREVIOUS_HASH`. ``op``
    is a decoded ``TreeOp`` (the tagged :class:`~fuaran_ui.model.Obj` the
    :mod:`fuaran_ui.ops` codec produces).
    """

    stream_id: str
    sequence: int
    previous_hash: str
    hash: str
    op: Obj
    actor: Actor
    #: Unix-epoch *seconds* (the value the chain consumes), UTC.
    timestamp_unix_seconds: int
    result_envelope: OpResultEnvelope = SUCCESS
    #: Conversation / prompt correlation, when the host threads one; folded into
    #: the hash. ``None`` when absent.
    prompt_id: str | None = None


@dataclass(frozen=True)
class Checkpoint:
    """A materialised snapshot at one op-index — replay can resume from the
    nearest checkpoint ``<=`` target rather than from genesis.

    ``previous_chain_head`` is the chain head at this op-index (equal to
    ``OpRecord(sequence).hash``, or :data:`.hash_chain.GENESIS_PREVIOUS_HASH` for a
    sequence-0 checkpoint over an initial tree); ``snapshot_hash`` is
    :func:`.hash_chain.snapshot_hash` over the canonical snapshot.
    """

    stream_id: str
    sequence: int
    previous_chain_head: str
    snapshot_hash: str
    snapshot: Node
    timestamp_unix_seconds: int


# ── Integrity + replay error unions ──────────────────────────────────────────


@dataclass(frozen=True)
class PreviousHashMismatch:
    """``record.previous_hash`` does not match the prior record's ``hash``."""

    sequence: int
    expected: str
    actual: str


@dataclass(frozen=True)
class HashMismatch:
    """``record.hash`` does not recompute to the hash of its fields."""

    sequence: int
    expected: str
    actual: str


@dataclass(frozen=True)
class OutOfOrder:
    """Records are not in contiguous ascending 1-based sequence order."""

    expected_sequence: int
    actual_sequence: int


#: A hash-chain integrity violation, surfaced by :func:`.hash_chain.verify_chain`.
type VerificationError = PreviousHashMismatch | HashMismatch | OutOfOrder


@dataclass(frozen=True)
class ApplyFailed:
    """A replay failure — the op at ``sequence`` did not apply."""

    sequence: int
    apply_error: ApplyError


#: A replay failure, surfaced by :func:`.replay.apply_to`.
type ReplayError = ApplyFailed


# ── Sink contract ────────────────────────────────────────────────────────────


@runtime_checkable
class OpStreamSink(Protocol):
    """The durable sink contract — the Python twin of ``IOpStreamSink``.

    Synchronous: the in-memory sink computes answers directly. Sinks reject
    duplicate ``(stream_id, sequence)`` pairs as structural defects — query
    :meth:`latest_sequence` before assigning a sequence.
    """

    def append(self, record: OpRecord) -> None:
        """Append ``record``; raises on a duplicate ``(stream_id, sequence)``."""
        ...

    def replay(self, stream_id: str, from_sequence: int, to_sequence: int) -> list[OpRecord]:
        """Records for ``stream_id`` with ``sequence`` in ``[from, to]`` inclusive,
        ascending. Empty when none are in range."""
        ...

    def latest_sequence(self, stream_id: str) -> int:
        """Highest sequence observed in ``stream_id``; ``0`` if empty."""
        ...

    def streams(self) -> list[str]:
        """Distinct stream ids the sink holds records for. Order unspecified."""
        ...


# ── Compare-and-append (optional sink extension) ─────────────────────────────


@dataclass(frozen=True)
class AppendReceipt:
    """The address of a record now in a stream, and its chain hash.

    Deliberately not the whole :class:`OpRecord` — a receipt is what a retry
    compares against, and the record it names is already recoverable via
    ``sink.replay`` at this address.
    """

    stream_id: str
    sequence: int
    hash: str


@dataclass(frozen=True)
class Appended:
    """A compare-and-append succeeded; ``receipt`` names the record now in the
    stream."""

    receipt: AppendReceipt


@dataclass(frozen=True)
class StaleHead:
    """A compare-and-append was rejected: the stream's actual head was not
    ``expected`` when the call reached the sink. ``actual`` is the value a
    retry rebuilds its record against — a caller never needs a second round
    trip just to learn what the real head is."""

    expected: str
    actual: str


#: The outcome of a compare-and-append (:meth:`CasOpStreamSink.append_if`).
type CasAppendOutcome = Appended | StaleHead


@runtime_checkable
class CasOpStreamSink(OpStreamSink, Protocol):
    """Optional extension of :class:`OpStreamSink` adding a typed
    compare-and-append.

    A separate protocol, not new members on :class:`OpStreamSink` itself:
    that protocol already ships with implementors outside this package, and a
    structural ``Protocol`` cannot grow a required member without breaking
    every one of them. A sink that supports the stronger contract implements
    this one too (it already carries every :class:`OpStreamSink` member); a
    caller checks ``isinstance(sink, CasOpStreamSink)`` — both protocols are
    ``@runtime_checkable`` — before reaching for it, and falls back to plain
    :meth:`OpStreamSink.append` when a sink does not support it.
    """

    def head(self, stream_id: str) -> str:
        """The chain hash of the record at ``latest_sequence(stream_id)``, or
        the chain's genesis anchor for a stream with no records yet."""
        ...

    def append_if(self, record: OpRecord, expected_head: str) -> CasAppendOutcome:
        """Append ``record`` only if the stream's current head is
        ``expected_head``.

        At the true head this behaves exactly like
        :meth:`OpStreamSink.append` plus a receipt. At a stale head
        **nothing is persisted** and ``StaleHead(expected_head, actual)`` is
        returned instead of raising — a conflict is a value a caller can
        retry against, not an exception it has to catch and re-derive the
        real head from.
        """
        ...
