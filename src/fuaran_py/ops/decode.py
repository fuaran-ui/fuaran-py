"""Decode canonical wire JSON into the ``TreeOp`` algebra (WIRE_FORMAT.md §3.4).

A ``TreeOp`` is, on the wire, a top-level ``$type``-discriminated object, so a
decoded op is modelled as an :class:`~fuaran_py.model.Obj` whose ``tag`` is the
op kind. ``decode_op`` validates the discriminator + each op's required fields,
reusing the node / kind / binding / style / state decoders from
:mod:`fuaran_py.schema.decode`.
"""

from __future__ import annotations

from collections.abc import Callable

from ..limits import MAX_NODE_DEPTH
from ..model import Arr, Obj, Value
from ..result import (
    LIMIT_EXCEEDED,
    MISSING_FIELD,
    WRONG_TYPE,
    DecodeResult,
    Err,
    Ok,
)
from ..schema.decode import (
    _decode_binding,
    _decode_kind,
    _decode_node_value,
    _decode_state,
    _decode_style,
    _dispatch,
    _expect_array,
    _expect_int,
    _expect_object,
    _expect_string,
    _Fail,
    _fail,
    _from_json_strict,
)
from ..schema.decode import (
    _reset_walk as _reset_node_walk,
)
from ..shapeguard import check_shape, load_bounded

OP_CASES = frozenset(
    {
        "EditNode",
        "UpdateProp",
        "ReplaceBinding",
        "UpdateStyle",
        "UpdateState",
        "InsertChild",
        "RemoveNode",
        "MoveNode",
        "ReorderChildren",
        "ReplaceRoot",
        "Batch",
    }
)


def _op_target(value: object, path: str) -> Value:
    return _expect_string(value, path)


def _op_string(value: object, path: str) -> Value:
    return _expect_string(value, path)


def _op_int(value: object, path: str) -> Value:
    return _expect_int(value, path)


def _op_json_value(value: object, path: str) -> Value:
    # UpdateProp's value is a structured JVal position (rule 12: no null) —
    # null-strict at the null's exact path, matching the F# reference and the
    # corpus reject-null-updateprop-value fixture. A top-level reserved
    # sentinel ("<opaque>" / "<closure>") is the canonical encoder's marker
    # for an in-process-only Native payload that was never wire-representable:
    # replaying it would apply the sentinel TEXT as live data, so it rejects
    # by name (same gate as the F# decoder).
    if isinstance(value, str) and value in ("<opaque>", "<closure>"):
        _fail(
            WRONG_TYPE,
            path,
            f"'{value}' is the reserved in-process-only sentinel, not a wire value"
            " — the op was logged from a Native payload that cannot replay",
            "a wire-representable JSON value (the sentinels are reserved vocabulary)",
        )
    return _from_json_strict(value, path)


def _op_kind(value: object, path: str) -> Value:
    return _decode_kind(value, path)


def _op_node(value: object, path: str) -> Value:
    return _decode_node_value(value, path)


def _op_binding(value: object, path: str) -> Value:
    return _decode_binding(value, path)


def _op_style(value: object, path: str) -> Value:
    return _decode_style(value, path)


def _op_state(value: object, path: str) -> Value:
    return _decode_state(value, path)


def _op_id_list(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_expect_string(item, f"{path}.{i}") for i, item in enumerate(arr)])


def _op_list(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_decode_op_value(item, f"{path}.{i}") for i, item in enumerate(arr)])


OpFieldDecoder = Callable[[object, str], Value]

OP_SCHEMAS: dict[str, list[tuple[str, bool, OpFieldDecoder]]] = {
    "EditNode": [("newKind", True, _op_kind), ("target", True, _op_target)],
    "UpdateProp": [("path", True, _op_string), ("target", True, _op_target), ("value", True, _op_json_value)],
    "ReplaceBinding": [("binding", True, _op_binding), ("slot", True, _op_string), ("target", True, _op_target)],
    "UpdateStyle": [("style", True, _op_style), ("target", True, _op_target)],
    "UpdateState": [("state", True, _op_state), ("target", True, _op_target)],
    "InsertChild": [("child", True, _op_node), ("parentId", True, _op_target)],
    "RemoveNode": [("target", True, _op_target)],
    "MoveNode": [("newParentId", True, _op_target), ("target", True, _op_target)],
    "ReorderChildren": [("newOrder", True, _op_id_list), ("parentId", True, _op_target)],
    "ReplaceRoot": [("node", True, _op_node)],
    "Batch": [("ops", True, _op_list)],
}


# ── The op axis, counted separately from the node axis ───────────────────
#
# §21.5's note for implementers is that bounding the NODE decoder is not
# sufficient: ``Batch`` makes this function self-recursive on a separate axis,
# and the syntactic bound only LOOKS like adequate cover for it. On the
# reference host, 2.6 KB of nested Batches killed the process with every
# node-side guard already in place. Same ceiling, its own counter.
_op_depth = 0


def _reset_op_walk() -> None:
    global _op_depth
    _op_depth = 0


def _decode_op_value(value: object, path: str) -> Obj:
    global _op_depth

    if _op_depth >= MAX_NODE_DEPTH:
        _fail(
            LIMIT_EXCEEDED,
            path,
            f"op nesting deeper than the wire limit MAX_NODE_DEPTH = {MAX_NODE_DEPTH}",
        )

    _op_depth += 1
    try:
        return _decode_op_value_inner(value, path)
    finally:
        _op_depth -= 1


# RETIRED positional slots, one per op (fuaran#687, closing the window fuaran#681 opened).
#
# fuaran#681 removed the field and every host then ACCEPTED AND IGNORED it so each
# could adopt independently. Silence was the whole mechanism: the loop below walks
# the op's schema and never looks at anything else, so *not reading it* was the
# tolerance. That is why closing the window cannot be done by deletion — there was
# never a read to delete, and the field would go on decoding silently forever. The
# close is an explicit refusal BY NAME, on the ``_check_near_misses`` pattern and
# for its reason: a key that no-ops is worse than one that fails, because the op
# decodes, applies, and puts the node somewhere other than where the ordinal asked.
_OP_RETIRED_FIELDS: dict[str, str] = {
    "InsertChild": "position",
    "MoveNode": "newPosition",
}


def _check_retired_positional(obj: dict, tag: str, path: str) -> None:
    """Refuse a retired positional slot by name.

    Called BEFORE the schema loop, mirroring the ``FormField`` near-miss ordering,
    so an op carrying both a retired ordinal and some other defect names the
    ordinal rather than reporting a defect the author would fix without ever
    learning the field is gone. The ordering is identical in all five hosts, so
    which defect surfaces first is deterministic.
    """
    name = _OP_RETIRED_FIELDS.get(tag)
    if name is not None and name in obj:
        _fail(
            WRONG_TYPE,
            f"{path}.{name}",
            f"'{name}' was removed from the wire format — {tag} appends, "
            "and order is stated by naming ids with ReorderChildren",
            "a Batch of the structural op followed by ReorderChildren",
        )


def _decode_op_value_inner(value: object, path: str) -> Obj:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, OP_CASES)
    _check_retired_positional(obj, tag, path)
    fields: dict[str, Value] = {}
    for name, required, dec in OP_SCHEMAS[tag]:
        if name in obj:
            fields[name] = dec(obj[name], f"{path}.{name}")
        elif required:
            _fail(MISSING_FIELD, f"{path}.{name}", f"missing required field '{name}'")
    return Obj(tag, fields)


def decode_op(text: str) -> DecodeResult[Obj]:
    """Decode a canonical-wire ``TreeOp`` document into a tagged :class:`~fuaran_py.model.Obj`."""
    parsed, error = load_bounded(text)
    if error is not None:
        return Err(error)
    shape = check_shape(parsed)
    if shape is not None:
        return Err(shape)
    _reset_op_walk()
    _reset_node_walk()
    try:
        return Ok(_decode_op_value(parsed, "$"))
    except _Fail as fail:
        return Err(fail.error)
