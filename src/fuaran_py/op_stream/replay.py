"""Replay + apply-and-persist.

The Python twin of the sibling hosts' replay engine: fold an :class:`OpRecord`
sequence through the apply engine (:func:`apply_to`), and the write path that
applies one op then persists a hash-chained record on success
(:func:`apply_and_persist`).

Replay does **not** verify the hash chain — use
:func:`~fuaran_py.op_stream.hash_chain.verify_chain` for that. The two concerns
are orthogonal: replay drives the apply engine; chain verification proves the
stream itself was not tampered with.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..model import Node, Obj
from ..ops import ApplyErr, ApplyError, apply
from ..result import Ok
from .hash_chain import GENESIS_PREVIOUS_HASH, compute_hash
from .types import SUCCESS, ApplyFailed, HumanActor, OpRecord, OpStreamSink, ReplayError


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


def apply_to(initial_tree: Node, records: list[OpRecord]) -> ReplayResult:
    """Apply every record to ``initial_tree`` in order, returning the final tree
    or the first apply failure (:class:`ApplyFailed` with the offending record's
    sequence)."""
    tree = initial_tree
    for record in records:
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
) -> ReplayResult:
    """Read records for ``stream_id`` in ``[from_sequence, to_sequence]`` from
    ``sink`` and fold them through the apply engine starting at ``initial_tree``.
    Resume from a checkpoint by passing its snapshot as ``initial_tree`` and
    ``checkpoint.sequence + 1`` as ``from_sequence``; ``to_sequence`` defaults to
    the sink's ``latest_sequence``."""
    up_to = to_sequence if to_sequence is not None else sink.latest_sequence(stream_id)
    records = sink.replay(stream_id, from_sequence, up_to)
    return apply_to(initial_tree, records)


@dataclass(frozen=True)
class PersistContext:
    """Per-op correlation + sink-error context threaded into the persisted record.

    :func:`apply_and_persist` queries the sink for the next sequence + the previous
    hash; the caller supplies stream identity, user id, and (optionally) the
    conversation's current prompt id. ``now`` returns Unix-epoch *seconds* (UTC) —
    injected so tests pin a deterministic timestamp into the chain; it defaults to
    the wall clock. ``on_sink_error`` observes a rejected append without breaking
    the apply path (durability is best-effort)."""

    stream_id: str
    user_id: str
    prompt_id: str | None = None
    now: Callable[[], int] | None = None
    on_sink_error: Callable[[Exception], None] | None = None


def _default_now() -> int:
    return int(time.time())


def _previous_hash_for(sink: OpStreamSink, stream_id: str, sequence: int) -> str:
    if sequence == 1:
        return GENESIS_PREVIOUS_HASH
    prev = sink.replay(stream_id, sequence - 1, sequence - 1)
    # latest_sequence reported >0 but the prior record is missing — a sink
    # invariant violation. Best-effort: use the genesis hash; a later verify_chain
    # surfaces the gap as OutOfOrder / PreviousHashMismatch.
    return prev[0].hash if prev else GENESIS_PREVIOUS_HASH


def _append_record_at(sink: OpStreamSink, ctx: PersistContext, sequence: int, op: Obj) -> None:
    previous_hash = _previous_hash_for(sink, ctx.stream_id, sequence)
    timestamp = (ctx.now or _default_now)()
    # PersistContext keeps its bare-string user id (host API unchanged); lift it to
    # a typed human actor at the record boundary.
    actor = HumanActor(ctx.user_id)
    hash_hex = compute_hash(previous_hash, op, sequence, timestamp, actor, ctx.prompt_id, SUCCESS)
    record = OpRecord(
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
    try:
        sink.append(record)
    except Exception as error:  # noqa: BLE001 — durability is best-effort; never poison the apply path.
        if ctx.on_sink_error is not None:
            try:
                ctx.on_sink_error(error)
            except Exception:  # noqa: BLE001 — a misbehaving hook must not propagate either.
                pass


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
    the apply error unchanged (the sink is not touched). ``sink.append`` failures
    are surfaced via ``ctx.on_sink_error`` but do NOT propagate — durability is
    best-effort."""
    result = apply(op, tree)
    if isinstance(result, ApplyErr):
        return PersistErr(result.error)
    assert isinstance(result, Ok)
    latest = sink.latest_sequence(ctx.stream_id)
    _append_record_at(sink, ctx, latest + 1, op)
    return PersistOk(result.value)
