"""``fuaran_py.op_stream`` — the hash-chained provenance log.

A stream's applied ``TreeOp`` edits form an append-only, hash-chained sequence of
:class:`OpRecord` envelopes; the SHA-256 chain (:mod:`.hash_chain`) makes the
stream tamper-evident and its authorship answerable from the record sequence
alone. This is the Python conformant host of the cross-host chain contract — it
reproduces the committed golden hashes (``wire-format-fixtures/chain``) exactly,
the same way the F# and TypeScript hosts do.

Public surface::

    from fuaran_py.op_stream import (
        CHAIN_FORMAT_VERSION, GENESIS_PREVIOUS_HASH,
        HumanActor, AgentActor, Success, Failure,
        OpRecord, Checkpoint,
        compute_hash, verify_chain, encode_stream_entry, format_version,
        InMemorySink, apply_and_persist, replay_stream, PersistContext,
        CasOpStreamSink, CasAppendOutcome, AppendReceipt, Appended, StaleHead,
    )

``InMemorySink`` also implements the optional ``CasOpStreamSink`` extension (a
typed compare-and-append: ``head`` / ``append_if``); ``apply_and_persist`` uses
it automatically when a sink supports it, retrying a lost race against the
sink-reported actual head instead of the plain read-then-append every sink
still supports.
"""

from __future__ import annotations

from .hash_chain import (
    CHAIN_FORMAT_VERSION,
    GENESIS_PREVIOUS_HASH,
    compute_hash,
    encode_actor,
    encode_result,
    encode_stream_entry,
    format_version,
    sha256_hex,
    snapshot_hash,
    verify_chain,
)
from .in_memory_sink import InMemorySink, create_in_memory_sink
from .replay import (
    CasRetryExhausted,
    OpStreamGapError,
    PersistContext,
    PersistErr,
    PersistOk,
    PersistResult,
    ReplayErr,
    ReplayOk,
    ReplayResult,
    SinkAppendError,
    apply_and_persist,
    apply_to,
    default_sink_error_reporter,
    replay_stream,
)
from .types import (
    SUCCESS,
    Actor,
    AgentActor,
    Appended,
    AppendReceipt,
    ApplyFailed,
    CasAppendOutcome,
    CasOpStreamSink,
    Checkpoint,
    Failure,
    HashMismatch,
    HumanActor,
    OpRecord,
    OpResultEnvelope,
    OpStreamSink,
    OutOfOrder,
    PreviousHashMismatch,
    ReplayError,
    StaleHead,
    Success,
    VerificationError,
    actor_id,
    human_actor,
)

__all__ = [
    # hash chain
    "CHAIN_FORMAT_VERSION",
    "GENESIS_PREVIOUS_HASH",
    "sha256_hex",
    "compute_hash",
    "verify_chain",
    "snapshot_hash",
    "encode_actor",
    "encode_result",
    "encode_stream_entry",
    "format_version",
    # types
    "Actor",
    "HumanActor",
    "AgentActor",
    "actor_id",
    "human_actor",
    "OpResultEnvelope",
    "Success",
    "Failure",
    "SUCCESS",
    "OpRecord",
    "Checkpoint",
    "OpStreamSink",
    "VerificationError",
    "PreviousHashMismatch",
    "HashMismatch",
    "OutOfOrder",
    "ReplayError",
    "ApplyFailed",
    # compare-and-append (optional sink extension)
    "CasOpStreamSink",
    "CasAppendOutcome",
    "AppendReceipt",
    "Appended",
    "StaleHead",
    # sink
    "InMemorySink",
    "create_in_memory_sink",
    # replay + persist
    "apply_to",
    "replay_stream",
    "apply_and_persist",
    "PersistContext",
    "default_sink_error_reporter",
    "SinkAppendError",
    "OpStreamGapError",
    "CasRetryExhausted",
    "ReplayOk",
    "ReplayErr",
    "ReplayResult",
    "PersistOk",
    "PersistErr",
    "PersistResult",
]
