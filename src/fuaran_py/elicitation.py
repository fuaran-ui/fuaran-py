"""The elicitation artefact (WIRE_FORMAT.md §18).

A question posed as a live Fuaran tree plus a typed answer contract, resolving to
exactly one typed outcome. Three codecs:

* the **elicitation envelope** (``{$elicitation, contract, default?, id,
  timeoutMs?, tree}``) — :func:`decode_elicitation` / :func:`encode_elicitation`;
* the closed four-case **outcome** DU (Answered / Declined / TimedOut /
  Superseded) — :func:`decode_outcome` / :func:`encode_outcome`;
* the **answer-conformance** validation — :func:`decode_answer_doc`.

Every object position is strict — undeclared keys are refused (default-deny by
shape), and the envelope evolves explicitly via ``$elicitation``, not by
tolerance. This is the Python twin of the Go ``elicitation`` package and
certifies against the shared ``elicitation-*`` corpus families.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .canonical import encode_value
from .model import Arr, Node, Obj, Value, from_json
from .result import (
    INVALID_JSON,
    MISSING_FIELD,
    UNKNOWN_DU_CASE,
    WRONG_TYPE,
    DecodeError,
    DecodeResult,
    Err,
    Ok,
)
from .schema import decode_node

# ── §18 error codes ─────────────────────────────────────────────────────────
# Kept OUT of the core six (like §15's FOREIGN_PROFILE); structural failures
# reuse the §6 codes on the same {code, path, message} envelope.
UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
UNDECLARED_FIELD = "UNDECLARED_FIELD"
CONTRACT_EMPTY = "CONTRACT_EMPTY"
CONTRACT_DUPLICATE_FIELD = "CONTRACT_DUPLICATE_FIELD"
CONTRACT_UNKNOWN_NODE = "CONTRACT_UNKNOWN_NODE"
ANSWER_MISSING_FIELD = "ANSWER_MISSING_FIELD"
ANSWER_UNDECLARED_FIELD = "ANSWER_UNDECLARED_FIELD"
ANSWER_TYPE_MISMATCH = "ANSWER_TYPE_MISMATCH"
ANSWER_OUT_OF_SPACE = "ANSWER_OUT_OF_SPACE"
DEFAULT_NONCONFORMANT = "DEFAULT_NONCONFORMANT"

FORMAT_VERSION = "1"
"""The elicitation format version this codec accepts."""

_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


class _Fail(Exception):
    """Internal short-circuit carrying a :class:`DecodeError`."""

    def __init__(self, error: DecodeError) -> None:
        self.error = error


def _fail(code: str, path: str, message: str) -> None:
    raise _Fail(DecodeError(code, path, message))


# ── number helpers (§18.2) ──────────────────────────────────────────────────


def _is_number(v: object) -> bool:
    # bool is a subclass of int — a boolean is never a JSON number here.
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_whole_int32(v: object) -> bool:
    if not _is_number(v):
        return False
    f = float(v)  # type: ignore[arg-type]
    return f == math.floor(f) and _INT32_MIN <= f <= _INT32_MAX


# ── value spaces (§18.1) ────────────────────────────────────────────────────


@dataclass(frozen=True)
class Space:
    """A decoded value space (``intRange`` / ``floatRange`` / ``stringLen`` / ``enum`` / ``anyString``)."""

    kind: str
    min: float = 0.0
    max: float = 0.0
    values: tuple[str, ...] = ()


_RANGE_KEYS = frozenset({"$type", "min", "max"})
_ENUM_KEYS = frozenset({"$type", "values"})
_ANY_KEYS = frozenset({"$type"})


def _strict_keys(obj: dict, declared: frozenset[str], path: str) -> None:
    """Reject any key on ``obj`` not in ``declared``; first offender in code-point order."""
    for key in sorted(obj):
        if key not in declared:
            _fail(UNDECLARED_FIELD, f"{path}.{key}", f"undeclared key '{key}'")


def _require_non_empty(obj: dict, key: str, path: str) -> str:
    raw = obj.get(key, _MISSING)
    if raw is _MISSING:
        _fail(MISSING_FIELD, f"{path}.{key}", f"missing '{key}'")
    if not isinstance(raw, str) or raw == "":
        _fail(WRONG_TYPE, f"{path}.{key}", f"'{key}' must be a non-empty string")
    return raw  # type: ignore[return-value]


_MISSING = object()


def _decode_space(raw: object, path: str) -> Space:
    if not isinstance(raw, dict):
        _fail(WRONG_TYPE, path, "expected a space object")
    obj: dict = raw  # type: ignore[assignment]
    tag = obj.get("$type", _MISSING)
    if tag is _MISSING:
        _fail(MISSING_FIELD, f"{path}.$type", "missing $type discriminator")
    if not isinstance(tag, str):
        _fail(WRONG_TYPE, f"{path}.$type", "$type must be a string")
    if tag in ("intRange", "floatRange", "stringLen"):
        _strict_keys(obj, _RANGE_KEYS, path)
        min_v = obj.get("min", _MISSING)
        max_v = obj.get("max", _MISSING)
        if not _is_number(min_v):
            _fail(MISSING_FIELD, f"{path}.min", "missing/invalid 'min'")
        if not _is_number(max_v):
            _fail(MISSING_FIELD, f"{path}.max", "missing/invalid 'max'")
        min_f = float(min_v)  # type: ignore[arg-type]
        max_f = float(max_v)  # type: ignore[arg-type]
        if min_f > max_f:
            _fail(WRONG_TYPE, path, "min must be <= max")
        return Space(kind=tag, min=min_f, max=max_f)
    if tag == "enum":
        _strict_keys(obj, _ENUM_KEYS, path)
        raw_values = obj.get("values")
        if not isinstance(raw_values, list) or len(raw_values) == 0:
            _fail(WRONG_TYPE, f"{path}.values", "enum.values must be a non-empty string array")
        values: list[str] = []
        for item in raw_values:  # type: ignore[union-attr]
            if not isinstance(item, str):
                _fail(WRONG_TYPE, f"{path}.values", "enum.values must be strings")
            values.append(item)
        return Space(kind="enum", values=tuple(values))
    if tag == "anyString":
        _strict_keys(obj, _ANY_KEYS, path)
        return Space(kind="anyString")
    _fail(UNKNOWN_DU_CASE, f"{path}.$type", f"unrecognised value-space '{tag}'")
    raise AssertionError  # unreachable — _fail always raises


_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1


def _num_value(f: float) -> Value:
    """A whole-valued bound re-encodes as an int (canonical collapses it identically)."""
    if f == math.floor(f) and _INT64_MIN <= f <= _INT64_MAX:
        return int(f)
    return f


def _encode_space(space: Space) -> Value:
    if space.kind in ("intRange", "floatRange", "stringLen"):
        return Obj(space.kind, {"max": _num_value(space.max), "min": _num_value(space.min)})
    if space.kind == "enum":
        return Obj("enum", {"values": Arr([v for v in space.values])})
    return Obj("anyString", {})


def _conforms_to_space(value: object, space: Space, path: str) -> None:
    """Check a JSON answer value against a space (type-vs-space first, then in-space)."""
    if space.kind == "intRange":
        if not _is_number(value):
            _fail(ANSWER_TYPE_MISMATCH, path, "expected an integer for an intRange field")
        if not _is_whole_int32(value):
            _fail(ANSWER_TYPE_MISMATCH, path, "value is not a 32-bit integer")
        f = float(value)  # type: ignore[arg-type]
        if f < space.min or f > space.max:
            _fail(ANSWER_OUT_OF_SPACE, path, "integer outside its intRange")
    elif space.kind == "floatRange":
        if not _is_number(value):
            _fail(ANSWER_TYPE_MISMATCH, path, "expected a number for a floatRange field")
        f = float(value)  # type: ignore[arg-type]
        if f < space.min or f > space.max:
            _fail(ANSWER_OUT_OF_SPACE, path, "number outside its floatRange")
    elif space.kind == "stringLen":
        if not isinstance(value, str):
            _fail(ANSWER_TYPE_MISMATCH, path, "expected a string for a stringLen field")
        n = len(value)  # type: ignore[arg-type]
        if n < space.min or n > space.max:
            _fail(ANSWER_OUT_OF_SPACE, path, "string length outside its stringLen bound")
    elif space.kind == "enum":
        if not isinstance(value, str):
            _fail(ANSWER_TYPE_MISMATCH, path, "expected a string for an enum field")
        if value not in space.values:
            _fail(ANSWER_OUT_OF_SPACE, path, "string outside its enum")
    elif space.kind == "anyString":
        if not isinstance(value, str):
            _fail(ANSWER_TYPE_MISMATCH, path, "expected a string for an anyString field")


# ── answer contract (§18.1) ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Field:
    """One answer-contract field. All five keys are required and strict."""

    name: str
    node_id: str
    state_key: str
    required: bool
    space: Space


@dataclass(frozen=True)
class Contract:
    """The answer contract: a non-empty ordered field set."""

    fields: tuple[Field, ...]


_FIELD_KEYS = frozenset({"name", "nodeId", "required", "space", "stateKey"})
_CONTRACT_KEYS = frozenset({"fields"})


def _decode_field(raw: object, path: str) -> Field:
    if not isinstance(raw, dict):
        _fail(WRONG_TYPE, path, "expected a field object")
    obj: dict = raw  # type: ignore[assignment]
    _strict_keys(obj, _FIELD_KEYS, path)
    name = _require_non_empty(obj, "name", path)
    node_id = _require_non_empty(obj, "nodeId", path)
    state_key = _require_non_empty(obj, "stateKey", path)
    required = obj.get("required", _MISSING)
    if not isinstance(required, bool):
        _fail(WRONG_TYPE, f"{path}.required", "required must be a boolean")
    raw_space = obj.get("space", _MISSING)
    if raw_space is _MISSING:
        _fail(MISSING_FIELD, f"{path}.space", "missing 'space'")
    space = _decode_space(raw_space, f"{path}.space")
    return Field(name=name, node_id=node_id, state_key=state_key, required=required, space=space)  # type: ignore[arg-type]


def _decode_contract(raw: object, path: str) -> Contract:
    if not isinstance(raw, dict):
        _fail(WRONG_TYPE, path, "expected a contract object")
    obj: dict = raw  # type: ignore[assignment]
    _strict_keys(obj, _CONTRACT_KEYS, path)
    raw_fields = obj.get("fields")
    if not isinstance(raw_fields, list):
        _fail(WRONG_TYPE, f"{path}.fields", "contract.fields must be an array")
    if len(raw_fields) == 0:  # type: ignore[arg-type]
        _fail(CONTRACT_EMPTY, f"{path}.fields", "the contract declares no fields")
    seen: set[str] = set()
    fields: list[Field] = []
    for i, item in enumerate(raw_fields):  # type: ignore[arg-type]
        field_path = f"{path}.fields[{i}]"
        field = _decode_field(item, field_path)
        if field.name in seen:
            _fail(CONTRACT_DUPLICATE_FIELD, f"{field_path}.name", f"duplicate field name '{field.name}'")
        seen.add(field.name)
        fields.append(field)
    return Contract(fields=tuple(fields))


def _validate_answer(answer: dict, contract: Contract, path_prefix: str) -> None:
    """Run the §18.4 answer validation (undeclared keys first, then each field in order)."""
    declared = {f.name for f in contract.fields}
    for key in sorted(answer):
        if key not in declared:
            _fail(ANSWER_UNDECLARED_FIELD, f"{path_prefix}.{key}", f"undeclared answer key '{key}'")
    for field in contract.fields:
        if field.name not in answer:
            if field.required:
                _fail(
                    ANSWER_MISSING_FIELD,
                    f"{path_prefix}.{field.name}",
                    f"required answer field '{field.name}' is absent",
                )
            continue
        _conforms_to_space(answer[field.name], field.space, f"{path_prefix}.{field.name}")


# ── tree node-id collection (for CONTRACT_UNKNOWN_NODE) ──────────────────────


def _collect_node_ids(value: Value, into: set[str]) -> None:
    """Collect every ``Node.id`` reachable in a decoded structural value."""
    if isinstance(value, Node):
        into.add(value.id)
        _collect_node_ids(value.kind, into)
        for v in value.extras.values():
            _collect_node_ids(v, into)
    elif isinstance(value, Obj):
        for v in value.fields.values():
            _collect_node_ids(v, into)
    elif isinstance(value, Arr):
        for item in value.items:
            _collect_node_ids(item, into)


# ── elicitation envelope (§18.2) ────────────────────────────────────────────


@dataclass(frozen=True)
class Elicitation:
    """A decoded §18 envelope: the question tree + the answer contract + optional default/timeout."""

    id: str
    tree: Node
    contract: Contract
    default: dict | None = None
    timeout_ms: int | None = None


_ENVELOPE_KEYS = frozenset({"$elicitation", "contract", "default", "id", "timeoutMs", "tree"})


def _reroot(error: DecodeError, prefix: str) -> DecodeError:
    return DecodeError(error.code, prefix + error.path[1:], error.message, error.expected_shape)


def _decode_elicitation(text: str) -> Elicitation:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        _fail(INVALID_JSON, "$", f"input is not valid JSON: {exc}")
    if not isinstance(raw, dict):
        _fail(WRONG_TYPE, "$", "expected an object at $")
    obj: dict = raw  # type: ignore[assignment]
    # 2 — undeclared envelope keys.
    _strict_keys(obj, _ENVELOPE_KEYS, "$")
    # 3 — version tag.
    raw_ver = obj.get("$elicitation", _MISSING)
    if raw_ver is _MISSING:
        _fail(MISSING_FIELD, "$.$elicitation", "missing '$elicitation' format tag")
    if not isinstance(raw_ver, str):
        _fail(WRONG_TYPE, "$.$elicitation", "$elicitation must be a string")
    if raw_ver != FORMAT_VERSION:
        _fail(UNSUPPORTED_VERSION, "$.$elicitation", f"unsupported elicitation version '{raw_ver}'")
    # 4 — id.
    elc_id = _require_non_empty(obj, "id", "$")
    # 5 — tree.
    raw_tree = obj.get("tree", _MISSING)
    if raw_tree is _MISSING:
        _fail(MISSING_FIELD, "$.tree", "missing 'tree'")
    tree_result = decode_node(json.dumps(raw_tree))
    if isinstance(tree_result, Err):
        raise _Fail(_reroot(tree_result.error, "$.tree"))
    tree = tree_result.value
    # 6 — contract (structure/shape/duplicate, then tree membership).
    raw_contract = obj.get("contract", _MISSING)
    if raw_contract is _MISSING:
        _fail(MISSING_FIELD, "$.contract", "missing 'contract'")
    contract = _decode_contract(raw_contract, "$.contract")
    ids: set[str] = set()
    _collect_node_ids(tree, ids)
    for i, field in enumerate(contract.fields):
        if field.node_id not in ids:
            _fail(
                CONTRACT_UNKNOWN_NODE,
                f"$.contract.fields[{i}].nodeId",
                f"field nodeId '{field.node_id}' names no node in the tree",
            )
    # 7 — timeoutMs.
    timeout_ms: int | None = None
    raw_timeout = obj.get("timeoutMs", _MISSING)
    if raw_timeout is not _MISSING:
        t = float(raw_timeout) if _is_number(raw_timeout) else None  # type: ignore[arg-type]
        if t is None or t < 1 or t != math.floor(t):
            _fail(WRONG_TYPE, "$.timeoutMs", "timeoutMs must be an integer >= 1")
        timeout_ms = int(raw_timeout)  # type: ignore[arg-type]
    # 8 — default (conformance → DEFAULT_NONCONFORMANT).
    default: dict | None = None
    raw_default = obj.get("default", _MISSING)
    if raw_default is not _MISSING:
        if not isinstance(raw_default, dict):
            _fail(DEFAULT_NONCONFORMANT, "$.default", "default must be an answer object")
        try:
            _validate_answer(raw_default, contract, "$.default")  # type: ignore[arg-type]
        except _Fail as f:
            _fail(DEFAULT_NONCONFORMANT, f.error.path, f.error.message)
        default = raw_default  # type: ignore[assignment]
    return Elicitation(id=elc_id, tree=tree, contract=contract, default=default, timeout_ms=timeout_ms)


def decode_elicitation(text: str) -> DecodeResult[Elicitation]:
    """Decode a §18 elicitation envelope, failing fast with one structured error."""
    try:
        return Ok(_decode_elicitation(text))
    except _Fail as f:
        return Err(f.error)


def encode_elicitation(elc: Elicitation) -> str:
    """Re-encode an envelope to canonical wire JSON (byte-exact round-trip)."""
    field_objs: list[Value] = [
        Obj(
            None,
            {
                "name": field.name,
                "nodeId": field.node_id,
                "required": field.required,
                "space": _encode_space(field.space),
                "stateKey": field.state_key,
            },
        )
        for field in elc.contract.fields
    ]
    fields: dict[str, Value] = {
        "$elicitation": FORMAT_VERSION,
        "contract": Obj(None, {"fields": Arr(field_objs)}),
        "id": elc.id,
        "tree": elc.tree,
    }
    if elc.default is not None:
        fields["default"] = _answer_obj(elc.default)
    if elc.timeout_ms is not None:
        fields["timeoutMs"] = elc.timeout_ms
    return encode_value(Obj(None, fields))


def _answer_obj(answer: dict) -> Obj:
    """Convert a raw answer object (name→scalar) to a canonical wire ``Obj``."""
    return Obj(None, {k: from_json(v) for k, v in answer.items()})


# ── answer-conformance document (§18.4) ─────────────────────────────────────

_ANSWER_DOC_KEYS = frozenset({"answer", "contract"})


def _decode_answer_doc(text: str) -> None:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        _fail(INVALID_JSON, "$", f"input is not valid JSON: {exc}")
    if not isinstance(raw, dict):
        _fail(WRONG_TYPE, "$", "expected an object at $")
    obj: dict = raw  # type: ignore[assignment]
    _strict_keys(obj, _ANSWER_DOC_KEYS, "$")
    raw_contract = obj.get("contract", _MISSING)
    if raw_contract is _MISSING:
        _fail(MISSING_FIELD, "$.contract", "missing 'contract'")
    contract = _decode_contract(raw_contract, "$.contract")
    raw_answer = obj.get("answer", _MISSING)
    if raw_answer is _MISSING:
        _fail(MISSING_FIELD, "$.answer", "missing 'answer'")
    if not isinstance(raw_answer, dict):
        _fail(WRONG_TYPE, "$.answer", "answer must be an object")
    _validate_answer(raw_answer, contract, "$.answer")  # type: ignore[arg-type]


def decode_answer_doc(text: str) -> DecodeResult[None]:
    """Run the ``{answer, contract}`` conformance document — ``Ok(None)`` on acceptance."""
    try:
        _decode_answer_doc(text)
        return Ok(None)
    except _Fail as f:
        return Err(f.error)


# ── outcome DU (§18.3) ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Outcome:
    """One of the closed four-case §18.3 outcome shapes, correlated by ``elicitation_id``."""

    kind: str
    elicitation_id: str
    answer: dict | None = None  # Answered only (raw scalars)
    by: str | None = None  # Superseded only, optional


# Per-outcome declared key sets (default-deny by shape).
_OUTCOME_KEYS: dict[str, frozenset[str]] = {
    "Answered": frozenset({"$type", "answer", "elicitationId"}),
    "Declined": frozenset({"$type", "elicitationId"}),
    "TimedOut": frozenset({"$type", "elicitationId"}),
    "Superseded": frozenset({"$type", "by", "elicitationId"}),
}


def _decode_outcome(text: str) -> Outcome:
    try:
        raw = json.loads(text)
    except ValueError as exc:
        _fail(INVALID_JSON, "$", f"input is not valid JSON: {exc}")
    if not isinstance(raw, dict):
        _fail(WRONG_TYPE, "$", "expected an object at $")
    obj: dict = raw  # type: ignore[assignment]
    raw_tag = obj.get("$type", _MISSING)
    if raw_tag is _MISSING:
        _fail(MISSING_FIELD, "$.$type", "missing $type discriminator")
    if not isinstance(raw_tag, str):
        _fail(WRONG_TYPE, "$.$type", "$type must be a string")
    declared = _OUTCOME_KEYS.get(raw_tag)
    if declared is None:
        _fail(UNKNOWN_DU_CASE, "$.$type", f"unrecognised outcome '{raw_tag}'")
        raise AssertionError  # unreachable
    _strict_keys(obj, declared, "$")
    elicitation_id = _require_non_empty(obj, "elicitationId", "$")
    answer: dict | None = None
    by: str | None = None
    if raw_tag == "Answered":
        raw_answer = obj.get("answer", _MISSING)
        if raw_answer is _MISSING:
            _fail(MISSING_FIELD, "$.answer", "Answered outcome missing 'answer'")
        if not isinstance(raw_answer, dict):
            _fail(WRONG_TYPE, "$.answer", "answer must be an object")
        answer = raw_answer  # type: ignore[assignment]
    elif raw_tag == "Superseded":
        raw_by = obj.get("by", _MISSING)
        if raw_by is not _MISSING:
            if not isinstance(raw_by, str):
                _fail(WRONG_TYPE, "$.by", "by must be a string")
            by = raw_by  # type: ignore[assignment]
    return Outcome(kind=raw_tag, elicitation_id=elicitation_id, answer=answer, by=by)


def decode_outcome(text: str) -> DecodeResult[Outcome]:
    """Decode an outcome document (does NOT check contract conformance)."""
    try:
        return Ok(_decode_outcome(text))
    except _Fail as f:
        return Err(f.error)


def encode_outcome(outcome: Outcome) -> str:
    """Re-encode an outcome to canonical wire JSON (byte-exact)."""
    fields: dict[str, Value] = {"elicitationId": outcome.elicitation_id}
    if outcome.kind == "Answered":
        fields["answer"] = _answer_obj(outcome.answer or {})
    elif outcome.kind == "Superseded" and outcome.by is not None:
        fields["by"] = outcome.by
    return encode_value(Obj(outcome.kind, fields))
