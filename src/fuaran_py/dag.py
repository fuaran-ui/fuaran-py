"""``fuaran_py.dag`` — the branching op-stream DAG-record codec.

The linear op-stream carries a *chain* of ``TreeOp`` edits; the branching DAG
generalises it to a content-addressed, multi-parent record so divergent edit
histories (an AI branch + a human branch) can fork and later merge. This module
is the Python conformant host of that record's canonical wire form — the sibling
of the F# ``DagWire`` encoder/decoder and the TypeScript ``@fuaran-ui/ops``
``encodeDagRecord`` / ``decodeDagRecord``.

The wire shape is a plain (non-``$type``) object whose keys sort in Ordinal order
(``actor`` < ``hash`` < ``op`` < ``outcomeHash`` < ``parents`` < ``promptId`` <
``resultEnvelope`` < ``streamId`` < ``timestamp`` < ``tombstoned``):

* ``actor`` — the typed author, ``Human | Agent``: the same :data:`Actor
  <fuaran_py.op_stream.types.Actor>` the linear op-stream has carried since
  Phase 320, nested verbatim in its own **pinned** member order (``kind`` first,
  then the case fields) rather than re-sorted — exactly as ``op`` nests the
  canonical ``TreeOp``. Reuses :func:`~fuaran_py.op_stream.hash_chain.encode_actor`,
  so there is one canonical actor encoding in this host, not two.
* ``hash`` — the record's content address (hex). See the pre-image note below.
* ``op`` — the nested canonical ``TreeOp`` (decoded / re-encoded by the same
  :mod:`fuaran_py.ops` codec the linear wire path uses).
* ``outcomeHash`` — present only on a merge node (the hash of the resulting
  tree); omitted otherwise (§2 rule 4).
* ``parents`` — author-order parent hashes (head = primary parent). Empty for a
  genesis record; a single element is the degenerate linear step.
* ``promptId`` — optional provenance of the authoring prompt; omitted when absent.
* ``resultEnvelope`` — a ``$type`` DU, ``Success`` or ``Failure{code, message}``.
* ``streamId`` — identity string.
* ``timestamp`` — Unix seconds (integer).
* ``tombstoned`` — whether the record's payload has been pruned (hash + parents
  preserved for reachability).

The pre-1144 wire carried a bare ``"userId":"…"`` member, which sorted LAST;
``actor`` sorts FIRST, so the attribution member moved to the front of the
envelope in the same change that typed it.

**The ``hash`` pre-image, and why this host does not recompute it.** The DAG
content address is minted by the reference host over a delimited envelope that
folds the sorted parents, the op (or, on a merge node, the outcome tree hash
under a ``"merge"`` tag) and the full provenance. Phase 1144 replaced that
envelope's attribution member in place — ``…,"ts":<unix>,"userId":"alice",…``
became ``…,"ts":<unix>,"actor":{"kind":"human","id":"alice"},…``, the same
pinned :func:`~fuaran_py.op_stream.hash_chain.encode_actor` bytes this module
now emits on the wire — so **every DAG address was re-minted and pre-1144
addresses do not carry forward**; there is no in-place upgrade for a persisted
DAG. ``fuaran-py`` is a *codec* host for this artefact: it mints no DAG address
and verifies none (the only hash pre-images it owns are the linear chain's, in
:mod:`fuaran_py.op_stream.hash_chain`), so ``hash`` is an opaque string it
round-trips byte-for-byte. The pre-image is recorded here rather than
implemented because a Python DAG addresser would be a new capability, not this
adoption. Should one ever land, it folds the envelope above — not the
``userId`` form.

Because the actor is inside that address, :func:`decode_dag_record` **refuses** a
pre-1144 ``userId`` envelope by name instead of lifting it to ``Human``: a lifted
record would carry a stored ``hash`` no host can reproduce, turning a clear
refusal here into a silent verification failure downstream. Both sibling hosts
refuse identically.

Byte-stable round-trip — ``encode_dag_record(decode_dag_record(x)) == x`` — is the
conformance property, exercised by the ``dag/`` sub-corpus. Reuses the shared
canonical encoder (so key order + number/string layout are byte-identical to the
other hosts by construction) and the shared ``TreeOp`` decoder for ``op``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .canonical import encode_value
from .model import Arr, Obj, Value
from .op_stream.hash_chain import encode_actor
from .op_stream.types import Actor, AgentActor, HumanActor
from .ops.decode import _decode_op_value
from .result import (
    INVALID_JSON,
    MISSING_FIELD,
    UNKNOWN_DU_CASE,
    DecodeError,
    DecodeResult,
    Err,
    Ok,
)
from .schema.decode import (
    _dispatch,
    _expect_array,
    _expect_bool,
    _expect_int,
    _expect_object,
    _expect_string,
    _Fail,
    _fail,
)

_ENVELOPE_CASES = frozenset({"Success", "Failure"})


@dataclass(frozen=True)
class DagResultEnvelope:
    """The apply outcome carried by a DAG record: ``Success`` or ``Failure``."""

    kind: str  # "Success" | "Failure"
    code: str = ""
    message: str = ""


#: The common ``Success`` envelope (no payload).
SUCCESS: DagResultEnvelope = DagResultEnvelope("Success")


@dataclass(frozen=True)
class DagOpRecord:
    """A content-addressed, multi-parent op-stream record (the DAG generalisation
    of the linear ``OpRecord``). ``parents`` is in author order; ``outcome_hash``
    is set only on a merge node; a single-parent record is the degenerate linear
    step and a zero-parent record is a genesis.

    ``actor`` is the typed author (Phase 1144) — the same ``Human | Agent`` the
    linear :class:`~fuaran_py.op_stream.types.OpRecord` carries, replacing the
    pre-1144 bare ``user_id: str`` at the same position. Where a caller wants the
    bare attribution id, :func:`~fuaran_py.op_stream.types.actor_id` is the
    pre-1144 value exactly; a host still threading a bare string lifts it with
    :func:`~fuaran_py.op_stream.types.human_actor`."""

    stream_id: str
    hash: str
    parents: tuple[str, ...]
    op: Obj
    actor: Actor
    timestamp: int
    result_envelope: DagResultEnvelope
    tombstoned: bool
    outcome_hash: str | None = None
    prompt_id: str | None = None


def _envelope_obj(env: DagResultEnvelope) -> Obj:
    if env.kind == "Failure":
        return Obj("Failure", {"code": env.code, "message": env.message})
    return Obj("Success", {})


def encode_dag_record(record: DagOpRecord) -> str:
    """Encode a :class:`DagOpRecord` to its canonical wire JSON.

    Keys are emitted in Ordinal order; the optional ``outcomeHash`` / ``promptId``
    are included only when present. The nested ``op`` re-encodes through the
    shared ``TreeOp`` encoder, so the output is byte-identical to the F# and
    TypeScript hosts.

    ``actor`` is the one member the shared canonical encoder cannot carry: its
    member order is **pinned** (``kind`` first, then the case fields), not
    Ordinal-sorted, so it is spliced in verbatim from
    :func:`~fuaran_py.op_stream.hash_chain.encode_actor` — the "embedded
    verbatim" treatment both sibling hosts give it, and the reason this host has
    exactly one actor encoding. Splicing it at the FRONT is valid precisely
    because ``actor`` is the Ordinal-least top-level key; every remaining key is
    encoded, and sorted, by the shared encoder. The invariant is pinned by
    ``test_dag_roundtrip.test_top_level_keys_are_ordinal_sorted``, so a future
    member sorting ahead of ``actor`` fails a test rather than silently emitting
    out-of-order bytes.
    """
    fields: dict[str, Value] = {
        "hash": record.hash,
        "op": record.op,
        "parents": Arr(list(record.parents)),
        "resultEnvelope": _envelope_obj(record.result_envelope),
        "streamId": record.stream_id,
        "timestamp": record.timestamp,
        "tombstoned": record.tombstoned,
    }
    if record.outcome_hash is not None:
        fields["outcomeHash"] = record.outcome_hash
    if record.prompt_id is not None:
        fields["promptId"] = record.prompt_id
    rest = encode_value(Obj(None, fields))
    return '{"actor":' + encode_actor(record.actor) + "," + rest[1:]


def _decode_envelope(value: object, path: str) -> DagResultEnvelope:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, _ENVELOPE_CASES)
    if tag == "Failure":
        code = _expect_string(obj["code"], f"{path}.code") if "code" in obj else ""
        message = _expect_string(obj["message"], f"{path}.message") if "message" in obj else ""
        return DagResultEnvelope("Failure", code, message)
    return SUCCESS


def _decode_actor(value: object, path: str) -> Actor:
    """Read a canonical actor object back to the typed ``Human | Agent``.

    Every defect is a named refusal rather than a default — the actor is inside
    the record's content address, so a guessed one silently invalidates the
    record's own ``hash``. An unknown ``kind`` is ``UNKNOWN_DU_CASE`` (the closed
    two-case union), a missing case field ``MISSING_FIELD``.
    """
    obj = _expect_object(value, path)
    if "kind" not in obj:
        _fail(MISSING_FIELD, f"{path}.kind", "missing required field 'kind'")
    kind = _expect_string(obj["kind"], f"{path}.kind")
    if kind == "human":
        if "id" not in obj:
            _fail(MISSING_FIELD, f"{path}.id", "missing required field 'id'")
        return HumanActor(_expect_string(obj["id"], f"{path}.id"))
    if kind == "agent":
        for required in ("model", "version", "id"):
            if required not in obj:
                _fail(MISSING_FIELD, f"{path}.{required}", f"missing required field '{required}'")
        return AgentActor(
            model=_expect_string(obj["model"], f"{path}.model"),
            version=_expect_string(obj["version"], f"{path}.version"),
            id=_expect_string(obj["id"], f"{path}.id"),
        )
    _fail(UNKNOWN_DU_CASE, f"{path}.kind", f"unknown actor kind '{kind}' (expected 'human' or 'agent')")
    raise AssertionError("unreachable")  # _fail always raises


def _decode_dag_value(value: object, path: str) -> DagOpRecord:
    obj = _expect_object(value, path)

    if "actor" not in obj and "userId" in obj:
        # A pre-1144 envelope. Refused BY NAME rather than lifted to Human: the
        # actor is folded into the content address, so a lifted record would
        # carry a stored `hash` no host can reproduce — a silent verification
        # failure later instead of a clear refusal here. Both sibling hosts
        # refuse identically.
        _fail(
            MISSING_FIELD,
            f"{path}.actor",
            "pre-1144 envelope — 'userId' was replaced by the typed 'actor', "
            "and DAG content addresses do not carry forward",
        )

    for required in ("actor", "hash", "op", "parents", "streamId", "timestamp"):
        if required not in obj:
            _fail(MISSING_FIELD, f"{path}.{required}", f"missing required field '{required}'")

    parents_raw = _expect_array(obj["parents"], f"{path}.parents")
    parents = tuple(_expect_string(item, f"{path}.parents.{i}") for i, item in enumerate(parents_raw))

    envelope = _decode_envelope(obj["resultEnvelope"], f"{path}.resultEnvelope") if "resultEnvelope" in obj else SUCCESS
    tombstoned = _expect_bool(obj["tombstoned"], f"{path}.tombstoned") if "tombstoned" in obj else False
    outcome_hash = _expect_string(obj["outcomeHash"], f"{path}.outcomeHash") if "outcomeHash" in obj else None
    prompt_id = _expect_string(obj["promptId"], f"{path}.promptId") if "promptId" in obj else None

    return DagOpRecord(
        stream_id=_expect_string(obj["streamId"], f"{path}.streamId"),
        hash=_expect_string(obj["hash"], f"{path}.hash"),
        parents=parents,
        op=_decode_op_value(obj["op"], f"{path}.op"),
        actor=_decode_actor(obj["actor"], f"{path}.actor"),
        timestamp=_expect_int(obj["timestamp"], f"{path}.timestamp"),
        result_envelope=envelope,
        tombstoned=tombstoned,
        outcome_hash=outcome_hash,
        prompt_id=prompt_id,
    )


def decode_dag_record(text: str) -> DecodeResult[DagOpRecord]:
    """Decode a canonical-wire DAG-record document into a :class:`DagOpRecord`.

    Never throws: returns ``Err`` with the canonical :class:`DecodeError` on any
    wire-shape violation, ``Ok`` with the record otherwise.
    """
    try:
        parsed = json.loads(text)
    except ValueError:
        return Err(DecodeError(INVALID_JSON, "$", "input is not syntactically valid JSON"))
    try:
        return Ok(_decode_dag_value(parsed, "$"))
    except _Fail as fail:
        return Err(fail.error)
