"""Replay + apply-and-persist.

The Python twin of the sibling hosts' replay engine: fold an :class:`OpRecord`
sequence through the apply engine (:func:`apply_to`), and the write path that
applies one op then persists a hash-chained record on success
(:func:`apply_and_persist`).

**Recorded refusals are not replayed.** A record whose ``result_envelope`` is a
``Failure`` describes an op that was refused and therefore never touched the
tree, so :func:`apply_to` and :func:`replay_stream` skip it by default and a
chain carrying one replays cleanly. ``include_refused=True`` folds every record
regardless — the literal reading, for an audit rebuild or a host whose refusals
are advisory.

Replay does **not** verify the hash chain — use
:func:`~fuaran_py.op_stream.hash_chain.verify_chain` for that. The two concerns
are orthogonal: replay drives the apply engine; chain verification proves the
stream itself was not tampered with.

**Write path.** When ``sink`` implements the optional
:class:`~fuaran_py.op_stream.types.CasOpStreamSink` extension,
:func:`apply_and_persist` writes through a bounded compare-and-append retry loop
instead of a bare read-then-append: it reads the stream's current head, builds
the record against it, and calls ``append_if``; a ``StaleHead`` outcome — another
writer won the race — rebuilds the record against the *reported* actual head and
tries again, up to :data:`_MAX_CAS_APPEND_ATTEMPTS` times. A read-then-append
(``latest_sequence`` then ``append``) is only ever a *proposal* — two concurrent
callers can compute the same next sequence, and the loser's ``append`` raises a
duplicate-sequence error that this module used to swallow by default, silently
losing the op. A sink that does not implement the extension keeps the plain
read-then-append path (existing third-party sinks are unaffected), but every
failure on either path now reaches :attr:`PersistContext.on_sink_error`, whose
default hook is no longer silence.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from ..model import Node, Obj
from ..ops import ApplyErr, ApplyError, apply
from ..result import Ok
from .hash_chain import GENESIS_PREVIOUS_HASH, compute_hash
from .types import (
    SUCCESS,
    Appended,
    ApplyFailed,
    CasOpStreamSink,
    HumanActor,
    OpRecord,
    OpStreamSink,
    ReplayError,
    StaleHead,
    Success,
)


@dataclass(frozen=True)
class ReplayOk:
    """A successful replay carrying the folded final tree."""

    value: Node

    @property
    def ok(self) -> bool:
        return True


@dataclass(frozen=True)
class ReplayErr:
    """A failed replay carrying the first :class:`ReplayError`."""

    error: ReplayError

    @property
    def ok(self) -> bool:
        return False


type ReplayResult = ReplayOk | ReplayErr


def apply_to(initial_tree: Node, records: list[OpRecord], *, include_refused: bool = False) -> ReplayResult:
    """Apply the records to ``initial_tree`` in order, returning the final tree
    or the first apply failure (:class:`ApplyFailed` with the offending record's
    sequence).

    **A record whose ``result_envelope`` is not :class:`~fuaran_py.op_stream.types.Success`
    is SKIPPED by default.** The envelope exists so a host can record a denial —
    an op that was refused and therefore never touched the tree — and folding one
    replays an edit the stream itself says did not happen. Worse, it fails on the
    record that says it failed: the refused op is by construction the one the tree
    cannot take, so a chain holding a single refusal used to be unreplayable, and
    every host that records denials had to rediscover the accepted-only filter for
    itself.

    ``include_refused=True`` asks for the literal fold over every record. That is
    the pre-selector behaviour, and it is a real requirement — an audit rebuild
    that wants to see exactly where a refused op would have landed, or a host
    whose ``Failure`` records are advisory rather than final — so it is a named
    keyword rather than an assumption either way.
    """
    tree = initial_tree
    for record in records:
        if not include_refused and not isinstance(record.result_envelope, Success):
            continue
        result = apply(record.op, tree)
        if isinstance(result, ApplyErr):
            return ReplayErr(ApplyFailed(record.sequence, result.error))
        assert isinstance(result, Ok)
        tree = result.value
    return ReplayOk(tree)


def replay_stream(
    sink: OpStreamSink,
    stream_id: str,
    initial_tree: Node,
    from_sequence: int = 1,
    to_sequence: int | None = None,
    *,
    include_refused: bool = False,
) -> ReplayResult:
    """Read records for ``stream_id`` in ``[from_sequence, to_sequence]`` from
    ``sink`` and fold them through the apply engine starting at ``initial_tree``.
    Resume from a checkpoint by passing its snapshot as ``initial_tree`` and
    ``checkpoint.sequence + 1`` as ``from_sequence``; ``to_sequence`` defaults to
    the sink's ``latest_sequence``.

    ``include_refused`` is :func:`apply_to`'s, unchanged: recorded refusals are
    skipped by default, and folding them is asked for by name."""
    up_to = to_sequence if to_sequence is not None else sink.latest_sequence(stream_id)
    records = sink.replay(stream_id, from_sequence, up_to)
    return apply_to(initial_tree, records, include_refused=include_refused)


class SinkAppendError(RuntimeError):
    """Wraps a rejected/failed append with the context a bare exception cannot
    carry — which stream, which sequence, and why. This is what
    :attr:`PersistContext.on_sink_error` receives; its message alone names all
    three, so a caller that just logs ``str(error)`` still gets the full
    picture."""

    def __init__(self, stream_id: str, sequence: int, cause: BaseException) -> None:
        super().__init__(f"op-stream append failed: stream_id={stream_id!r} sequence={sequence} cause={cause!r}")
        self.stream_id = stream_id
        self.sequence = sequence
        self.__cause__ = cause


class OpStreamGapError(RuntimeError):
    """The sink reports records past ``sequence``, but the record at
    ``sequence`` itself is missing — a hole in the stream, not something a
    write path may paper over by chaining onto the genesis hash. Raised only
    to be wrapped and handed to ``ctx.on_sink_error``; never returned as a
    :class:`PersistErr`, because the *apply* already succeeded and best-effort
    durability must not turn that success into a failure result — the loss is
    reported through the same non-silent channel as a rejected append."""

    def __init__(self, stream_id: str, missing_sequence: int) -> None:
        super().__init__(
            f"op-stream gap: stream_id={stream_id!r} is missing the record at "
            f"sequence={missing_sequence} — refusing to chain onto genesis over it"
        )
        self.stream_id = stream_id
        self.missing_sequence = missing_sequence


class CasRetryExhausted(RuntimeError):
    """A compare-and-append lost the race ``attempts`` times in a row against a
    stream under heavy concurrent write pressure. Raised only to be wrapped and
    handed to ``ctx.on_sink_error`` — never propagated, matching the best-effort
    durability posture of the read-then-append path it replaces."""

    def __init__(self, stream_id: str, attempts: int) -> None:
        super().__init__(
            f"op-stream compare-and-append exhausted {attempts} attempts on "
            f"stream_id={stream_id!r} — a concurrent writer kept winning the race"
        )
        self.stream_id = stream_id
        self.attempts = attempts


def default_sink_error_reporter(error: Exception) -> None:
    """The default :attr:`PersistContext.on_sink_error` hook.

    A lost append must never leave no trace, so the default is a ``logging``
    line at ``ERROR`` — not silence. ``error`` is normally a
    :class:`SinkAppendError`, whose message already names the stream, the
    sequence, and the reason. A caller that wants different observability
    (metrics, an alert) passes its own hook; a caller that truly wants a lost
    op to leave no trace passes ``on_sink_error=None`` explicitly — that is a
    deliberate opt-out, never the default.
    """
    logging.getLogger(__name__).error("%s", error)


@dataclass(frozen=True)
class PersistContext:
    """Per-op correlation + sink-error context threaded into the persisted record.

    :func:`apply_and_persist` queries the sink for the next sequence + the previous
    hash (or, against a compare-and-append sink, the current head); the caller
    supplies stream identity, user id, and (optionally) the conversation's current
    prompt id. ``now`` returns Unix-epoch *seconds* (UTC) — injected so tests pin a
    deterministic timestamp into the chain; it defaults to the wall clock.
    ``on_sink_error`` observes a durability failure without breaking the apply path
    (durability is best-effort) — it defaults to :func:`default_sink_error_reporter`
    so a lost append is always observable somewhere; pass ``None`` explicitly to
    opt out."""

    stream_id: str
    user_id: str
    prompt_id: str | None = None
    now: Callable[[], int] | None = None
    on_sink_error: Callable[[Exception], None] | None = default_sink_error_reporter


def _default_now() -> int:
    return int(time.time())


def _report_sink_error(ctx: PersistContext, stream_id: str, sequence: int, cause: BaseException) -> None:
    if ctx.on_sink_error is None:
        return
    try:
        ctx.on_sink_error(SinkAppendError(stream_id, sequence, cause))
    except Exception:  # noqa: BLE001 — a misbehaving hook must not propagate either.
        pass


@dataclass(frozen=True)
class _Gap:
    """Sentinel: :func:`_previous_hash_for` found a hole instead of a hash."""

    missing_sequence: int


def _previous_hash_for(sink: OpStreamSink, stream_id: str, sequence: int) -> str | _Gap:
    """The previous-hash a record at ``sequence`` should chain onto — the
    genesis anchor for ``sequence == 1``, otherwise the hash of the record at
    ``sequence - 1``. When ``sink.replay`` produces nothing for that prior
    sequence, that is a GAP in the stream, not a reason to fall back to the
    genesis hash: silently doing so would write a record that *looks* like it
    starts a fresh chain and only fails much later, at the next
    ``verify_chain``, far from the cause. Returning the typed :class:`_Gap`
    instead lets the caller refuse the write and report it immediately."""
    if sequence == 1:
        return GENESIS_PREVIOUS_HASH
    prev = sink.replay(stream_id, sequence - 1, sequence - 1)
    if not prev:
        return _Gap(sequence - 1)
    return prev[0].hash


def _build_record(ctx: PersistContext, sequence: int, previous_hash: str, op: Obj) -> OpRecord:
    timestamp = (ctx.now or _default_now)()
    # PersistContext keeps its bare-string user id (host API unchanged); lift it to
    # a typed human actor at the record boundary.
    actor = HumanActor(ctx.user_id)
    hash_hex = compute_hash(previous_hash, op, sequence, timestamp, actor, ctx.prompt_id, SUCCESS)
    return OpRecord(
        stream_id=ctx.stream_id,
        sequence=sequence,
        previous_hash=previous_hash,
        hash=hash_hex,
        op=op,
        actor=actor,
        timestamp_unix_seconds=timestamp,
        result_envelope=SUCCESS,
        prompt_id=ctx.prompt_id,
    )


#: Bound on the compare-and-append retry loop (:func:`_persist_via_cas`) — a
#: small constant, not unbounded spinning, so a pathologically hot stream fails
#: loudly (via ``ctx.on_sink_error``) rather than looping forever.
_MAX_CAS_APPEND_ATTEMPTS = 8


def _persist_via_cas(sink: CasOpStreamSink, ctx: PersistContext, op: Obj) -> None:
    """Persist ``op`` through compare-and-append, retrying against the
    reported actual head on a lost race.

    Each attempt reads the sink's current head and next sequence, builds the
    record against them, and calls ``append_if``. A ``StaleHead`` outcome means
    a concurrent writer's append landed between the read and this call —
    nothing was persisted — so the next attempt rebuilds against the head
    ``StaleHead`` names, with no extra round trip needed to discover it.
    Exhausting :data:`_MAX_CAS_APPEND_ATTEMPTS` reports through
    ``ctx.on_sink_error`` exactly like a rejected read-then-append.
    """
    expected_head = sink.head(ctx.stream_id)
    sequence = sink.latest_sequence(ctx.stream_id) + 1
    for _ in range(_MAX_CAS_APPEND_ATTEMPTS):
        record = _build_record(ctx, sequence, expected_head, op)
        outcome = sink.append_if(record, expected_head)
        if isinstance(outcome, Appended):
            return
        assert isinstance(outcome, StaleHead)
        expected_head = outcome.actual
        sequence = sink.latest_sequence(ctx.stream_id) + 1
    _report_sink_error(ctx, ctx.stream_id, sequence, CasRetryExhausted(ctx.stream_id, _MAX_CAS_APPEND_ATTEMPTS))


def _persist_read_then_append(sink: OpStreamSink, ctx: PersistContext, op: Obj) -> None:
    """The fallback write path for a sink that does not implement
    :class:`CasOpStreamSink`: read ``latest_sequence`` + the previous hash, then
    append. This is a *proposal*, not a guarantee — two concurrent callers can
    compute the same next sequence, and the loser's ``append`` raises, which is
    reported (never swallowed) via ``ctx.on_sink_error``."""
    sequence = sink.latest_sequence(ctx.stream_id) + 1
    previous_hash = _previous_hash_for(sink, ctx.stream_id, sequence)
    if isinstance(previous_hash, _Gap):
        _report_sink_error(
            ctx, ctx.stream_id, sequence, OpStreamGapError(ctx.stream_id, previous_hash.missing_sequence)
        )
        return
    record = _build_record(ctx, sequence, previous_hash, op)
    try:
        sink.append(record)
    except Exception as error:  # noqa: BLE001 — durability is best-effort; never poison the apply path.
        _report_sink_error(ctx, ctx.stream_id, sequence, error)


@dataclass(frozen=True)
class PersistOk:
    """A successful apply-and-persist carrying the updated tree."""

    value: Node

    @property
    def ok(self) -> bool:
        return True


@dataclass(frozen=True)
class PersistErr:
    """A failed apply — the sink is untouched — carrying the :class:`ApplyError`."""

    error: ApplyError

    @property
    def ok(self) -> bool:
        return False


type PersistResult = PersistOk | PersistErr


def apply_and_persist(sink: OpStreamSink, ctx: PersistContext, op: Obj, tree: Node) -> PersistResult:
    """Apply ``op`` against ``tree``. On success, persist a hash-chained
    :class:`OpRecord` to ``sink`` and return the updated tree; on failure, return
    the apply error unchanged (the sink is not touched).

    When ``sink`` implements :class:`CasOpStreamSink`, the write goes through a
    bounded compare-and-append retry loop (:func:`_persist_via_cas`) so two
    concurrent callers racing the same stream both persist, rather than the
    second one's write silently vanishing. A sink that does not implement the
    extension keeps the plain read-then-append path
    (:func:`_persist_read_then_append`). Either way, a durability failure is
    surfaced via ``ctx.on_sink_error`` but does NOT propagate — durability is
    best-effort, and the returned tree reflects the apply regardless."""
    result = apply(op, tree)
    if isinstance(result, ApplyErr):
        return PersistErr(result.error)
    assert isinstance(result, Ok)
    if isinstance(sink, CasOpStreamSink):
        _persist_via_cas(sink, ctx, op)
    else:
        _persist_read_then_append(sink, ctx, op)
    return PersistOk(result.value)
