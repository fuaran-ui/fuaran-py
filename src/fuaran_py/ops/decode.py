"""Decode canonical wire JSON into the ``TreeOp`` algebra (WIRE_FORMAT.md §3.4).

A ``TreeOp`` is, on the wire, a top-level ``$type``-discriminated object, so a
decoded op is modelled as an :class:`~fuaran_py.model.Obj` whose ``tag`` is the
op kind. ``decode_op`` validates the discriminator + each op's required fields,
reusing the node / kind / binding / style / state decoders from
:mod:`fuaran_py.schema.decode`.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from ..model import Arr, Obj, Value, from_json
from ..result import INVALID_JSON, MISSING_FIELD, DecodeError, DecodeResult, Err, Ok
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
)

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
    return from_json(value)


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
    "InsertChild": [("child", True, _op_node), ("parentId", True, _op_target), ("position", True, _op_int)],
    "RemoveNode": [("target", True, _op_target)],
    "MoveNode": [("newParentId", True, _op_target), ("newPosition", True, _op_int), ("target", True, _op_target)],
    "ReorderChildren": [("newOrder", True, _op_id_list), ("parentId", True, _op_target)],
    "ReplaceRoot": [("node", True, _op_node)],
    "Batch": [("ops", True, _op_list)],
}


def _decode_op_value(value: object, path: str) -> Obj:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, OP_CASES)
    fields: dict[str, Value] = {}
    for name, required, dec in OP_SCHEMAS[tag]:
        if name in obj:
            fields[name] = dec(obj[name], f"{path}.{name}")
        elif required:
            _fail(MISSING_FIELD, f"{path}.{name}", f"missing required field '{name}'")
    return Obj(tag, fields)


def decode_op(text: str) -> DecodeResult[Obj]:
    """Decode a canonical-wire ``TreeOp`` document into a tagged :class:`~fuaran_py.model.Obj`."""
    try:
        parsed = json.loads(text)
    except ValueError:
        return Err(DecodeError(INVALID_JSON, "$", "input is not syntactically valid JSON"))
    try:
        return Ok(_decode_op_value(parsed, "$"))
    except _Fail as fail:
        return Err(fail.error)
