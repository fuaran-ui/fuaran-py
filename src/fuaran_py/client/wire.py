"""The HTTP envelope mapping.

This is the SINGLE place that pins how the typed contract crosses the wire,
so a future change to the endpoint's framing touches one file. The request
body mirrors the surface's request record field-for-field (the names below
are the canonical on-the-wire keys); the response is discriminated by HTTP
status — 200 → produced, 401 → access denied, 422 → turn failed — per the
endpoint's documented status map. Any other status is surfaced as a
``provider``-stage failure so a caller never has to special-case transport.
"""

from __future__ import annotations

import json
from typing import cast

from .contract import (
    TURN_STAGES,
    AccessDenied,
    AppliedOp,
    Produced,
    RecoverableError,
    TurnFailed,
    TurnResult,
    TurnStage,
)


def to_wire_body(
    prompt: str,
    *,
    current_tree_json: str | None = None,
    byok_key: str | None = None,
    access_token: str | None = None,
    disable_corpus_read: bool | None = None,
    contribute_corpus: bool | None = None,
) -> dict[str, object]:
    """Build the JSON request body, mirroring the surface request record.

    Fields are omitted (not sent as ``null``) when absent, matching the
    surface defaults (a missing corpus flag is privacy-preserving; a missing
    current tree is a fresh generation). Secrets (``ByokKey`` /
    ``AccessToken``) are request-scoped and present only when supplied — in a
    server-proxied deployment the proxy injects them, so the caller-side body
    omits them.
    """
    body: dict[str, object] = {"Prompt": prompt}
    if current_tree_json is not None:
        body["CurrentTreeJson"] = current_tree_json
    if byok_key is not None:
        body["ByokKey"] = byok_key
    if access_token is not None:
        body["AccessToken"] = access_token
    if disable_corpus_read is not None:
        body["DisableCorpusRead"] = disable_corpus_read
    if contribute_corpus is not None:
        body["ContributeCorpus"] = contribute_corpus
    return body


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _pick(obj: dict[str, object], pascal: str, camel: str) -> object:
    """Read a value tolerant of the canonical PascalCase wire key or a
    camelCase alias, so a deployment that lower-cases its JSON still parses."""
    value = obj.get(pascal)
    return value if value is not None else obj.get(camel)


def _parse_applied_ops(raw: object) -> tuple[AppliedOp, ...]:
    if not isinstance(raw, list):
        return ()
    ops: list[AppliedOp] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        obj = cast(dict[str, object], entry)
        op_id = _as_str(_pick(obj, "OpId", "opId")) or ""
        op_json = _as_str(_pick(obj, "OpJson", "opJson")) or ""
        ops.append(AppliedOp(op_id=op_id, op_json=op_json))
    return tuple(ops)


def _as_stage(value: object) -> TurnStage:
    s = _as_str(value)
    return cast(TurnStage, s) if s in TURN_STAGES else "provider"


def _parse_json(text: str) -> dict[str, object]:
    if text.strip() == "":
        return {}
    try:
        value = json.loads(text)
    except ValueError:
        return {}
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def _failed(stage: TurnStage, code: str, message: str) -> TurnResult:
    return TurnFailed(RecoverableError(stage=stage, code=code, message=message))


def parse_turn_response(status: int, body_text: str) -> TurnResult:
    """Map an HTTP ``(status, body)`` pair onto the typed :data:`TurnResult`.

    The status selects the case; the body supplies the payload. The error
    envelope may be flat (``{Stage, Code, Message}``) or nested under
    ``Error`` — both parse.
    """
    body = _parse_json(body_text)

    if status == 200:
        return Produced(
            tree_json=_as_str(_pick(body, "TreeJson", "treeJson")) or "",
            ops=_parse_applied_ops(_pick(body, "Ops", "ops")),
            version=_as_str(_pick(body, "Version", "version")) or "",
        )

    if status == 401:
        return AccessDenied(reason=_as_str(_pick(body, "Reason", "reason")) or "access denied")

    if status == 422:
        raw_envelope = _pick(body, "Error", "error")
        envelope = cast(dict[str, object], raw_envelope) if isinstance(raw_envelope, dict) else body
        return _failed(
            _as_stage(_pick(envelope, "Stage", "stage")),
            _as_str(_pick(envelope, "Code", "code")) or "TURN_FAILED",
            _as_str(_pick(envelope, "Message", "message")) or "the turn failed",
        )

    # Any other status is a transport-level failure — surfaced as a
    # provider-stage envelope so the caller handles it through the same
    # turn-failed path.
    detail = _as_str(_pick(body, "Message", "message")) or body_text[:200]
    return _failed(
        "provider",
        f"HTTP_{status}",
        detail if detail != "" else f"unexpected status {status}",
    )
