"""``InMemorySink`` — a per-process dict-backed :class:`OpStreamSink`.

The Python twin of the sibling hosts' in-memory sink: useful for tests, the
authoring loop, and ephemeral environments. Records are stored as typed
:class:`~fuaran_py.op_stream.types.OpRecord` values — no JSON round-trip, no host
codec needed (a file / SQLite sink that needs one is out of scope for this
stdlib-only module).

The ``(stream_id, sequence)`` uniqueness invariant is enforced on append; a
duplicate is a structural defect (the caller should query
:meth:`latest_sequence` + 1 before assigning a sequence).
"""

from __future__ import annotations

from .types import Checkpoint, OpRecord


class InMemorySink:
    """A dict-backed op-stream sink (also holds checkpoints)."""

    def __init__(self) -> None:
        self._streams: dict[str, list[OpRecord]] = {}
        self._checkpoints: dict[str, list[Checkpoint]] = {}

    # ── OpStreamSink ─────────────────────────────────────────────────────────

    def append(self, record: OpRecord) -> None:
        bucket = self._streams.setdefault(record.stream_id, [])
        if any(r.sequence == record.sequence for r in bucket):
            raise ValueError(
                f"InMemorySink: duplicate (stream_id={record.stream_id!r}, "
                f"sequence={record.sequence}) — sinks reject overwrites."
            )
        bucket.append(record)

    def replay(self, stream_id: str, from_sequence: int, to_sequence: int) -> list[OpRecord]:
        bucket = self._streams.get(stream_id, [])
        matching = [r for r in bucket if from_sequence <= r.sequence <= to_sequence]
        return sorted(matching, key=lambda r: r.sequence)

    def latest_sequence(self, stream_id: str) -> int:
        bucket = self._streams.get(stream_id)
        if not bucket:
            return 0
        return max(r.sequence for r in bucket)

    def streams(self) -> list[str]:
        return list(self._streams.keys())

    # ── Checkpoint surface ───────────────────────────────────────────────────

    def append_checkpoint(self, checkpoint: Checkpoint) -> None:
        bucket = self._checkpoints.setdefault(checkpoint.stream_id, [])
        if any(c.sequence == checkpoint.sequence for c in bucket):
            raise ValueError(
                f"InMemorySink: duplicate checkpoint (stream_id={checkpoint.stream_id!r}, "
                f"sequence={checkpoint.sequence}) — sinks reject overwrites."
            )
        bucket.append(checkpoint)

    def latest_checkpoint_at_or_before(self, stream_id: str, up_to_sequence: int) -> Checkpoint | None:
        bucket = self._checkpoints.get(stream_id, [])
        candidates = [c for c in bucket if c.sequence <= up_to_sequence]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.sequence)

    def list_checkpoints(self, stream_id: str) -> list[Checkpoint]:
        bucket = self._checkpoints.get(stream_id, [])
        return sorted(bucket, key=lambda c: c.sequence)


def create_in_memory_sink() -> InMemorySink:
    """A fresh in-memory sink."""
    return InMemorySink()
