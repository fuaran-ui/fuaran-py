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
    )
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
    PersistContext,
    PersistErr,
    PersistOk,
    PersistResult,
    ReplayErr,
    ReplayOk,
    ReplayResult,
    apply_and_persist,
    apply_to,
    replay_stream,
)
from .types import (
    SUCCESS,
    Actor,
    AgentActor,
    ApplyFailed,
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
    # sink
    "InMemorySink",
    "create_in_memory_sink",
    # replay + persist
    "apply_to",
    "replay_stream",
    "apply_and_persist",
    "PersistContext",
    "ReplayOk",
    "ReplayErr",
    "ReplayResult",
    "PersistOk",
    "PersistErr",
    "PersistResult",
]
