"""Hash-chain primitive — SHA-256 over the canonical encoding.

The Python twin of the F# ``HashChain`` / ``StreamEntry`` and the TypeScript
``hashChain.ts``. All three hosts fold the same **byte-identical pre-image** and
run standard SHA-256, so a chain built here reproduces the committed golden hashes
(``wire-format-fixtures/chain/chain-corpus.json``) exactly — that byte-for-byte
reproduction is the whole contract.

Python is not compiled to the browser, so unlike the TS host (which ships a
dependency-free FIPS 180-4 implementation) and the F# host (which routes through a
Fable-safe pure SHA-256) this host uses :func:`hashlib.sha256` directly — the
digest is identical, and staying stdlib-only keeps the module dependency-light.

The canonical algorithm (the actor is folded in, so attribution is part of the
integrity chain)::

    hash[n] = sha256( previousHash[n] + "|" + payload[n] )
    payload = {"seq":<sequence-1>,"actor":<actor>,"op":<streamEntry>}
    streamEntry = {"v":<CHAIN_FORMAT_VERSION>,"op":<op>,"ts":<seconds>,
                   "promptId":<id|null>,"result":<result>}
    hash[0]'s previousHash is GENESIS_PREVIOUS_HASH (sixty-four '0' chars).

Field order in every object is **pinned** — ``v`` leads ``streamEntry`` so the
format is self-describing, and the payload object keys are ``seq`` / ``actor`` /
``op`` in that literal order (this is the delimited pre-image, NOT the canonical
sorted-key encoder). ``sequence`` is the public 1-based value; the pre-image folds
the 0-based record index (``sequence - 1``).
"""

from __future__ import annotations

import hashlib

from ..canonical import escape_string
from ..model import Obj
from ..ops import encode_op
from .types import (
    Actor,
    AgentActor,
    Failure,
    HashMismatch,
    HumanActor,
    OpRecord,
    OpResultEnvelope,
    OutOfOrder,
    PreviousHashMismatch,
    Success,
    VerificationError,
)

# ── Pinned constants ─────────────────────────────────────────────────────────

#: The chain FORMAT version, folded FIRST into the hash pre-image (the leading
#: ``{"v":<n>,...}`` of :func:`encode_stream_entry`). It makes the chain
#: self-describing and tamper-evident: a host can read ``v`` before verifying and
#: reject an unrecognised format with a clear error, and because it is inside the
#: pre-image a stream cannot be silently relabelled. Bump this in lock-step across
#: every host and the ``chain-corpus.json`` golden whenever the pre-image formula,
#: the envelope shape, or the hash function changes. (v2 = the provenance envelope
#: + delimited payload + host-side SHA-256; a tagless record is treated as v1.)
CHAIN_FORMAT_VERSION = 2

#: Sixty-four '0' characters — the ``previous_hash`` of every stream's
#: ``sequence == 1`` record.
GENESIS_PREVIOUS_HASH = "0" * 64


# ── Canonical sub-encodings (byte-identical to every host) ───────────────────


def sha256_hex(payload: str) -> str:
    """SHA-256 of UTF-8(``payload``) → 64 lower-case hex characters."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def encode_actor(actor: Actor) -> str:
    """Canonical JSON of the typed actor — folded into the record hash.

    Field order is pinned (kind first, then case fields); byte-for-byte with the
    sibling hosts' ``Actor.encode``.
    """
    if isinstance(actor, HumanActor):
        return '{"kind":"human","id":' + escape_string(actor.id) + "}"
    if isinstance(actor, AgentActor):
        return (
            '{"kind":"agent","model":'
            + escape_string(actor.model)
            + ',"version":'
            + escape_string(actor.version)
            + ',"id":'
            + escape_string(actor.id)
            + "}"
        )
    raise TypeError(f"not an Actor: {type(actor)!r}")


def encode_result(result: OpResultEnvelope) -> str:
    """Canonical encoding of the apply outcome. ``Success`` is a bare tag; a
    ``Failure`` carries its code + message, so flipping outcome breaks the hash."""
    if isinstance(result, Success):
        return '{"kind":"success"}'
    if isinstance(result, Failure):
        return (
            '{"kind":"failure","code":'
            + escape_string(result.code)
            + ',"message":'
            + escape_string(result.message)
            + "}"
        )
    raise TypeError(f"not an OpResultEnvelope: {type(result)!r}")


def encode_stream_entry(
    op: Obj,
    timestamp_unix_seconds: int,
    prompt_id: str | None,
    result_envelope: OpResultEnvelope,
) -> str:
    """The pinned cross-host provenance envelope — the opaque ``op`` payload the
    chain carries. Field order **v / op / ts / promptId / result** is pinned; ``v``
    sorts first so a reader can lift it with a minimal parse. ``ts`` is unix
    SECONDS; ``promptId`` is ``null`` when absent. Byte-for-byte with the sibling
    hosts' ``StreamEntry.encode``."""
    prompt = escape_string(prompt_id) if prompt_id is not None else "null"
    return (
        '{"v":'
        + str(CHAIN_FORMAT_VERSION)
        + ',"op":'
        + encode_op(op)
        + ',"ts":'
        + str(int(timestamp_unix_seconds))
        + ',"promptId":'
        + prompt
        + ',"result":'
        + encode_result(result_envelope)
        + "}"
    )


def format_version(encoded_envelope: str) -> int | None:
    """Read the chain format version from an encoded envelope without verifying it,
    so a host can reject an unrecognised format with a clear error before hash
    verification (which would otherwise surface as a cryptic chain break).

    ``None`` when no leading ``v`` is present (treated as the pre-v2 format). A
    minimal prefix scan, NOT a full JSON parse: the envelope carries
    ``promptId:null`` (which the wire model rejects) and a future envelope may be a
    shape this host cannot fully parse. Byte-for-byte with the sibling hosts'
    ``formatVersion``."""
    prefix = '{"v":'
    if not encoded_envelope.startswith(prefix):
        return None
    digits = ""
    for ch in encoded_envelope[len(prefix) :]:
        if "0" <= ch <= "9":
            digits += ch
        else:
            break
    return int(digits) if digits else None


# ── Chain construction + verification ────────────────────────────────────────


def compute_hash(
    previous_hash: str,
    op: Obj,
    sequence: int,
    timestamp_unix_seconds: int,
    actor: Actor,
    prompt_id: str | None,
    result_envelope: OpResultEnvelope,
) -> str:
    """Compute the hash for an op-record. Identical algorithm on every host —
    verification re-derives this and compares.

    The pre-image is the canonical delimited ``{"seq":...,"actor":...,"op":...}``
    payload with ``op = encode_stream_entry(...)``, hashed as
    ``sha256(previous_hash + "|" + payload)``. ``sequence`` is the public 1-based
    value; the payload folds the 0-based record index (``sequence - 1``)."""
    payload = (
        '{"seq":'
        + str(sequence - 1)
        + ',"actor":'
        + encode_actor(actor)
        + ',"op":'
        + encode_stream_entry(op, timestamp_unix_seconds, prompt_id, result_envelope)
        + "}"
    )
    return sha256_hex(previous_hash + "|" + payload)


def snapshot_hash(previous_chain_head: str, sequence: int, canonical_tree: str) -> str:
    """The content address of a checkpoint snapshot — binds the snapshot to its
    position in the chain (``previous_chain_head`` + ``sequence`` + the canonical
    tree, in a delimited pre-image), so a valid snapshot from one ``(head, seq)``
    no longer validates at a different one. Byte-for-byte with the sibling hosts'
    ``snapshotHash``."""
    payload = '{"snapshot":true,"seq":' + str(sequence) + ',"tree":' + canonical_tree + "}"
    return sha256_hex(previous_chain_head + "|" + payload)


def verify_chain(records: list[OpRecord]) -> VerificationError | None:
    """Walk ``records`` in order, asserting (a) the ``previous_hash`` chain links
    and (b) each ``hash`` recomputes to the stored value, over a contiguous 1-based
    sequence from genesis. Returns the first violation, or ``None`` on a clean
    chain."""
    previous_hash = GENESIS_PREVIOUS_HASH
    expected_sequence = 1

    for record in records:
        if record.sequence != expected_sequence:
            return OutOfOrder(expected_sequence, record.sequence)
        if record.previous_hash != previous_hash:
            return PreviousHashMismatch(record.sequence, previous_hash, record.previous_hash)
        recomputed = compute_hash(
            record.previous_hash,
            record.op,
            record.sequence,
            record.timestamp_unix_seconds,
            record.actor,
            record.prompt_id,
            record.result_envelope,
        )
        if recomputed != record.hash:
            return HashMismatch(record.sequence, recomputed, record.hash)
        previous_hash = record.hash
        expected_sequence += 1

    return None
