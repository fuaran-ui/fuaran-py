"""``fuaran_py.dag`` — the branching op-stream DAG-record codec.

The linear op-stream carries a *chain* of ``TreeOp`` edits; the branching DAG
generalises it to a content-addressed, multi-parent record so divergent edit
histories (an AI branch + a human branch) can fork and later merge. This module
is the Python conformant host of that record's canonical wire form — the sibling
of the F# ``DagWire`` encoder/decoder and the TypeScript ``@fuaran-ui/ops``
``encodeDagRecord`` / ``decodeDagRecord``.

The wire shape is a plain (non-``$type``) object whose keys sort in Ordinal order
(``hash`` < ``op`` < ``outcomeHash`` < ``parents`` < ``promptId`` <
``resultEnvelope`` < ``streamId`` < ``timestamp`` < ``tombstoned`` < ``userId``):

* ``hash`` — the record's content hash (hex).
* ``op`` — the nested canonical ``TreeOp`` (decoded / re-encoded by the same
  :mod:`fuaran_py.ops` codec the linear wire path uses).
* ``outcomeHash`` — present only on a merge node (the hash of the resulting
  tree); omitted otherwise (§2 rule 4).
* ``parents`` — author-order parent hashes (head = primary parent). Empty for a
  genesis record; a single element is the degenerate linear step.
* ``promptId`` — optional provenance of the authoring prompt; omitted when absent.
* ``resultEnvelope`` — a ``$type`` DU, ``Success`` or ``Failure{code, message}``.
* ``streamId`` / ``userId`` — identity strings.
* ``timestamp`` — Unix seconds (integer).
* ``tombstoned`` — whether the record's payload has been pruned (hash + parents
  preserved for reachability).

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
from .ops.decode import _decode_op_value
from .result import INVALID_JSON, MISSING_FIELD, DecodeError, DecodeResult, Err, Ok
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
    step and a zero-parent record is a genesis."""

    stream_id: str
    hash: str
    parents: tuple[str, ...]
    op: Obj
    user_id: str
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

    Keys are emitted in Ordinal order by the shared canonical encoder; the
    optional ``outcomeHash`` / ``promptId`` are included only when present. The
    nested ``op`` re-encodes through the shared ``TreeOp`` encoder, so the output
    is byte-identical to the F# and TypeScript hosts.
    """
    fields: dict[str, Value] = {
        "hash": record.hash,
        "op": record.op,
        "parents": Arr(list(record.parents)),
        "resultEnvelope": _envelope_obj(record.result_envelope),
        "streamId": record.stream_id,
        "timestamp": record.timestamp,
        "tombstoned": record.tombstoned,
        "userId": record.user_id,
    }
    if record.outcome_hash is not None:
        fields["outcomeHash"] = record.outcome_hash
    if record.prompt_id is not None:
        fields["promptId"] = record.prompt_id
    return encode_value(Obj(None, fields))


def _decode_envelope(value: object, path: str) -> DagResultEnvelope:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, _ENVELOPE_CASES)
    if tag == "Failure":
        code = _expect_string(obj["code"], f"{path}.code") if "code" in obj else ""
        message = _expect_string(obj["message"], f"{path}.message") if "message" in obj else ""
        return DagResultEnvelope("Failure", code, message)
    return SUCCESS


def _decode_dag_value(value: object, path: str) -> DagOpRecord:
    obj = _expect_object(value, path)

    for required in ("hash", "op", "parents", "streamId", "timestamp", "userId"):
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
        user_id=_expect_string(obj["userId"], f"{path}.userId"),
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
