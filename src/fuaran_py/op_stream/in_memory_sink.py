"""``InMemorySink`` — a per-process dict-backed :class:`OpStreamSink`.

The Python twin of the sibling hosts' in-memory sink: useful for tests, the
authoring loop, and ephemeral environments. Records are stored as typed
:class:`~fuaran_py.op_stream.types.OpRecord` values — no JSON round-trip, no host
codec needed (a file / SQLite sink that needs one is out of scope for this
stdlib-only module).

The ``(stream_id, sequence)`` uniqueness invariant is enforced on append; a
duplicate is a structural defect (the caller should query
:meth:`latest_sequence` + 1 before assigning a sequence). This sink also
implements :class:`~fuaran_py.op_stream.types.CasOpStreamSink` (``head`` /
``append_if``): every stream method — the base four plus the two extension
methods — takes the SAME lock, so a compare-and-append genuinely compares
against a head no concurrent writer can move out from under it.
"""

from __future__ import annotations

import threading

from .hash_chain import GENESIS_PREVIOUS_HASH
from .types import Appended, AppendReceipt, CasAppendOutcome, Checkpoint, OpRecord, StaleHead


class InMemorySink:
    """A dict-backed op-stream sink (also holds checkpoints).

    Thread-safe: one lock guards every stream read and write, including the
    compare-and-append extension. Without that, ``append_if`` would only be a
    slower plain append — the whole point of comparing the head is that the
    comparison and the write happen as one atomic step.
    """

    def __init__(self) -> None:
        self._streams: dict[str, list[OpRecord]] = {}
        self._checkpoints: dict[str, list[Checkpoint]] = {}
        #: Highest sequence seen per stream — maintained on append, not
        #: rescanned, so ``latest_sequence`` and ``head`` stay O(1).
        self._max_sequence: dict[str, int] = {}
        #: Chain hash at ``_max_sequence[stream_id]`` — maintained on append
        #: alongside it (see :meth:`_append_locked`).
        self._heads: dict[str, str] = {}
        #: Every sequence present per stream, so the duplicate guard on append is
        #: O(1) rather than a scan of the whole log.
        #:
        #: ``_max_sequence`` cannot serve this. A duplicate is not only a re-append
        #: of the HEAD: a caller filling a gap, or replaying a partially-applied
        #: batch, re-offers a sequence BELOW the maximum, and comparing against the
        #: maximum would admit it silently — an overwrite in a sink whose whole
        #: contract is that it rejects them. The membership set is the smallest
        #: structure that answers the question actually being asked.
        self._sequences: dict[str, set[int]] = {}
        self._lock = threading.Lock()

    # ── OpStreamSink ─────────────────────────────────────────────────────────

    def append(self, record: OpRecord) -> None:
        with self._lock:
            self._append_locked(record)

    def replay(self, stream_id: str, from_sequence: int, to_sequence: int) -> list[OpRecord]:
        with self._lock:
            bucket = self._streams.get(stream_id, [])
            matching = [r for r in bucket if from_sequence <= r.sequence <= to_sequence]
            return sorted(matching, key=lambda r: r.sequence)

    def latest_sequence(self, stream_id: str) -> int:
        with self._lock:
            return self._max_sequence.get(stream_id, 0)

    def streams(self) -> list[str]:
        with self._lock:
            return list(self._streams.keys())

    # ── CasOpStreamSink — optional compare-and-append extension ────────────────

    def head(self, stream_id: str) -> str:
        with self._lock:
            return self._head_locked(stream_id)

    def append_if(self, record: OpRecord, expected_head: str) -> CasAppendOutcome:
        with self._lock:
            actual = self._head_locked(record.stream_id)
            if actual != expected_head:
                return StaleHead(expected_head, actual)
            return Appended(self._append_locked(record))

    # ── shared locked primitives — callers hold ``self._lock`` ─────────────────

    def _head_locked(self, stream_id: str) -> str:
        return self._heads.get(stream_id, GENESIS_PREVIOUS_HASH)

    def _append_locked(self, record: OpRecord) -> AppendReceipt:
        """The one place a record enters a stream's log.

        Returns the receipt so ``append`` and ``append_if`` share one
        duplicate-sequence guard and one piece of head bookkeeping — which is
        what keeps ``_max_sequence`` / ``_heads`` from drifting between the
        two call paths.
        """
        bucket = self._streams.setdefault(record.stream_id, [])
        seen = self._sequences.setdefault(record.stream_id, set())
        if record.sequence in seen:
            raise ValueError(
                f"InMemorySink: duplicate (stream_id={record.stream_id!r}, "
                f"sequence={record.sequence}) — sinks reject overwrites."
            )
        bucket.append(record)
        seen.add(record.sequence)
        if record.sequence > self._max_sequence.get(record.stream_id, 0):
            self._max_sequence[record.stream_id] = record.sequence
            self._heads[record.stream_id] = record.hash
        return AppendReceipt(stream_id=record.stream_id, sequence=record.sequence, hash=record.hash)

    # ── Checkpoint surface ───────────────────────────────────────────────────

    def append_checkpoint(self, checkpoint: Checkpoint) -> None:
        with self._lock:
            bucket = self._checkpoints.setdefault(checkpoint.stream_id, [])
            if any(c.sequence == checkpoint.sequence for c in bucket):
                raise ValueError(
                    f"InMemorySink: duplicate checkpoint (stream_id={checkpoint.stream_id!r}, "
                    f"sequence={checkpoint.sequence}) — sinks reject overwrites."
                )
            bucket.append(checkpoint)

    def latest_checkpoint_at_or_before(self, stream_id: str, up_to_sequence: int) -> Checkpoint | None:
        with self._lock:
            bucket = self._checkpoints.get(stream_id, [])
            candidates = [c for c in bucket if c.sequence <= up_to_sequence]
            if not candidates:
                return None
            return max(candidates, key=lambda c: c.sequence)

    def list_checkpoints(self, stream_id: str) -> list[Checkpoint]:
        with self._lock:
            bucket = self._checkpoints.get(stream_id, [])
            return sorted(bucket, key=lambda c: c.sequence)


def create_in_memory_sink() -> InMemorySink:
    """A fresh in-memory sink."""
    return InMemorySink()
