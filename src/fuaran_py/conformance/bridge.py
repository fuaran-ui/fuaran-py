"""Stdio bridge — plug the Python codec into the language-agnostic certification
kit (WIRE_FORMAT.md §12.1) through a thin JS adapter.

The Phase 168 kit runner drives any candidate host through a small JS adapter
seam (``decodeNode`` / ``encodeNode`` / ``decodeOp`` / ``encodeOp``). A host in
another language certifies through a subprocess bridge that speaks JSON over
stdio — this is the Python side of exactly that bridge (the companion JS adapter
is ``wire-format-fixtures/conformance/fuaran-py.adapter.mjs``).

Protocol: read one request object from stdin, write one response object to
stdout, exit. One process per call keeps the bridge stateless — the adapter
shells it per hook invocation.

Request::

    {"op": "decodeNode" | "encodeNode" | "decodeOp" | "encodeOp", "input": "<wire JSON>"}

Response::

    {"ok": true}                                  # decodeNode/decodeOp — input is valid
    {"ok": true,  "value": "<canonical JSON>"}    # encodeNode/encodeOp — decode+re-encode
    {"ok": false, "error": {"code": "...", "path": "...", "message": "..."}}

The kit runner treats a decoded value as opaque and only passes it back to the
host's own encoder, so the JS adapter carries the *input* wire text across the
seam as the opaque token; ``encodeNode`` / ``encodeOp`` here decode it again and
re-encode canonically — so **both** the Python decoder and the Python encoder
are genuinely exercised through the seam (not an identity pass-through).
"""

from __future__ import annotations

import json
import sys
from typing import Any

from ..ops import decode_op, encode_op
from ..result import Err, Ok
from ..schema import decode_node, encode_node


def _error_payload(error: Any) -> dict[str, Any]:
    return {"code": error.code, "path": error.path, "message": error.message}


def handle(request: dict[str, Any]) -> dict[str, Any]:
    op = request.get("op")
    text = request.get("input", "")

    if op == "decodeNode":
        rn = decode_node(text)
        return {"ok": True} if isinstance(rn, Ok) else {"ok": False, "error": _error_payload(rn.error)}

    if op == "decodeOp":
        ro = decode_op(text)
        return {"ok": True} if isinstance(ro, Ok) else {"ok": False, "error": _error_payload(ro.error)}

    if op == "encodeNode":
        rn = decode_node(text)
        if isinstance(rn, Err):
            return {"ok": False, "error": _error_payload(rn.error)}
        return {"ok": True, "value": encode_node(rn.value)}

    if op == "encodeOp":
        ro = decode_op(text)
        if isinstance(ro, Err):
            return {"ok": False, "error": _error_payload(ro.error)}
        return {"ok": True, "value": encode_op(ro.value)}

    return {"ok": False, "error": {"code": "INVALID_JSON", "path": "$", "message": f"unknown bridge op {op!r}"}}


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        json.dump({"ok": False, "error": {"code": "INVALID_JSON", "path": "$", "message": str(exc)}}, sys.stdout)
        return 0
    json.dump(handle(request), sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
