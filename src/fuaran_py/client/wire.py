"""The HTTP envelope mapping.

This is the SINGLE place that pins how the typed contract crosses the wire,
so a future change to the endpoint's framing touches one file.

IT SPEAKS THE DEPLOYED WIRE, and that was a correction. This module used to
write ``{Prompt, CurrentTreeJson, ByokKey, AccessToken, …}`` and read
``{TreeJson, Ops, Version}`` across a 200/401/422 status map, because that is
what the published specification described. The endpoint reads ``prompt`` /
``currentTree``, takes secrets from HEADERS ONLY, replies
``{version, tree, opsApplied, provider, servedModel?, snapshot}``, and refuses
with ``{"error": {"code", "message", "stage"?}}`` at 400 / 401 / 405 / 422 /
500 / 503. A client built from the old shape was answered
``400 BAD_REQUEST: request body has no 'prompt' string``.

So: the request body carries NO SECRET — :mod:`fuaran_py.client.client` puts
the access token and the BYOK key in headers, and the endpoint REFUSES a body
that carries either, never reading the value. There is no way to express the
old shape through this module, which is the point: a body-carried key is a key
an intermediary may have logged.

Reads stay tolerant of the retired PascalCase spelling — a same-origin proxy or
a mock in front of the endpoint may still speak it — but writes do not.
"""

from __future__ import annotations

import json
from typing import cast

from ..shapeguard import check_shape, load_bounded
from .contract import (
    TURN_STAGES,
    AccessDenied,
    AppliedOp,
    ClientCode,
    Produced,
    ProducedDetail,
    RecoverableError,
    SnapshotState,
    TurnFailed,
    TurnResult,
    TurnStage,
)


def to_wire_body(
    prompt: str,
    *,
    current_tree_json: str | None = None,
    disable_corpus_read: bool | None = None,
    contribute_corpus: bool | None = None,
    interaction_id: str | None = None,
) -> dict[str, object]:
    """Build the JSON request body, mirroring the endpoint's request shape.

    Fields are omitted (not sent as ``null``) when absent, matching the
    endpoint's defaults (a missing corpus flag is privacy-preserving; a missing
    current tree is a fresh generation).

    ``current_tree_json`` is written as a JSON STRING rather than inlined as an
    object. The endpoint accepts both, and the string form is the one that
    cannot corrupt the payload: inlining would mean re-emitting the caller's
    canonical bytes, and the whole repair ergonomic depends on the tree crossing
    back and forth unchanged.

    **No credential parameter exists.** The access token and BYOK key are
    headers; a body carrying either is refused by the endpoint with
    ``400 SECRETS_IN_BODY`` and the value is not read.
    """
    body: dict[str, object] = {"prompt": prompt}
    if current_tree_json is not None:
        body["currentTree"] = current_tree_json
    if disable_corpus_read is not None:
        body["disableCorpusRead"] = disable_corpus_read
    if contribute_corpus is not None:
        body["contributeCorpus"] = contribute_corpus
    if interaction_id is not None:
        body["interactionId"] = interaction_id
    return body


def _as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _as_dict(value: object) -> dict[str, object] | None:
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _pick(obj: dict[str, object], canonical: str, alias: str) -> object:
    """Read a value tolerant of the canonical wire key or an alternate
    spelling, so a proxy or mock that has not yet moved still parses."""
    value = obj.get(canonical)
    return value if value is not None else obj.get(alias)


def _parse_applied_ops(raw: object) -> tuple[AppliedOp, ...]:
    if not isinstance(raw, list):
        return ()
    ops: list[AppliedOp] = []
    for entry in raw:
        obj = _as_dict(entry)
        if obj is None:
            continue
        op_id = _as_str(_pick(obj, "opId", "OpId")) or ""
        op_json = _as_str(_pick(obj, "opJson", "OpJson")) or ""
        ops.append(AppliedOp(op_id=op_id, op_json=op_json))
    return tuple(ops)


def _as_stage(value: object) -> TurnStage:
    s = _as_str(value)
    return cast(TurnStage, s) if s in TURN_STAGES else "provider"


def _parse_json(text: str) -> dict[str, object] | None:
    """The body as a JSON object, or ``None`` — so a 200 can be told from a
    200-shaped nothing."""
    if text.strip() == "":
        return None
    # §20.1 — a server reply is wire bytes reaching this host through an entry
    # point of its own, so it is parsed under the same §20 rows and §21 bounds
    # as every other. A hostile or over-deep body answers ``None`` here rather
    # than escaping as a throw, which is the shape this function already
    # promises its caller.
    value, error = load_bounded(text)
    if error is not None or check_shape(value) is not None:
        return None
    return _as_dict(value)


def _failed(stage: TurnStage, code: str, message: str) -> TurnResult:
    return TurnFailed(RecoverableError(stage=stage, code=code, message=message))


def malformed_response(detail: str) -> TurnResult:
    """The endpoint replied 200 with nothing usable in it.

    A turn-failed, not a ``Produced("")``: the session HOLDS the produced tree,
    so an empty one poisons every later repair rather than failing the turn that
    caused it.
    """
    return _failed("provider", ClientCode.MALFORMED_RESPONSE, detail)


def _read_tree(body: dict[str, object]) -> str | None:
    """The produced tree's canonical wire JSON, however the reply carried it.

    The deployed endpoint writes ``tree`` as an OBJECT; a proxy or mock may
    write it as a JSON string, and the retired shape called it ``TreeJson``.
    """
    raw = _pick(body, "tree", "TreeJson")
    as_object = _as_dict(raw)
    if as_object is not None:
        return json.dumps(as_object, separators=(",", ":"))
    as_text = _as_str(raw)
    return as_text if as_text is not None and as_text.strip() != "" else None


def _read_snapshot(body: dict[str, object]) -> SnapshotState | None:
    snapshot = _as_dict(body.get("snapshot"))
    if snapshot is None:
        return None
    state = _as_str(_pick(snapshot, "state", "State"))
    if state is None:
        return None
    return SnapshotState(
        state=state,
        version=_as_str(_pick(snapshot, "version", "Version")),
        content_hash=_as_str(_pick(snapshot, "contentHash", "ContentHash")),
    )


def parse_produced_detail(status: int, body_text: str) -> ProducedDetail | None:
    """The deployment facts a 200 carries beyond the tree.

    ``None`` when the status was not 200, or the body carried no usable tree —
    the same condition that makes the turn ``MALFORMED_RESPONSE``, so a caller
    can never read a detail off a reply the turn itself rejected.
    """
    if status != 200:
        return None
    body = _parse_json(body_text)
    if body is None or _read_tree(body) is None:
        return None
    count = _pick(body, "opsApplied", "OpsApplied")
    return ProducedDetail(
        # No count on the wire: fall back to the length of an op list, which is
        # what a proxy or an in-process host sends instead.
        ops_applied=(
            count
            if isinstance(count, int) and not isinstance(count, bool)
            else len(_parse_applied_ops(_pick(body, "ops", "Ops")))
        ),
        provider=_as_str(_pick(body, "provider", "Provider")),
        served_model=_as_str(_pick(body, "servedModel", "ServedModel")),
        snapshot=_read_snapshot(body),
    )


def parse_turn_response(status: int, body_text: str) -> TurnResult:
    """Map an HTTP ``(status, body)`` pair onto the typed :data:`TurnResult`.

    The status selects the case; the body supplies the payload. The endpoint
    sends ONE refusal shape — ``{"error": {"code", "message", "stage"?}}`` — at
    every non-200 status, so every refusal is read the same way and a caller
    never has to special-case transport. The retired flat and ``Error``-nested
    PascalCase forms still parse, for a proxy or mock that has not moved.
    """
    body = _parse_json(body_text) or {}
    envelope = _as_dict(_pick(body, "error", "Error")) or body

    if status == 200:
        tree_json = _read_tree(body)
        if tree_json is None:
            return malformed_response("the endpoint replied 200 with no tree")
        return Produced(
            tree_json=tree_json,
            ops=_parse_applied_ops(_pick(body, "ops", "Ops")),
            version=_as_str(_pick(body, "version", "Version")) or "",
        )

    if status == 401:
        return AccessDenied(
            reason=(
                _as_str(_pick(envelope, "message", "Message"))
                # The retired shape put the reason in a bare `Reason` member.
                or _as_str(_pick(body, "Reason", "reason"))
                or "access denied"
            )
        )

    if status == 422:
        return _failed(
            _as_stage(_pick(envelope, "stage", "Stage")),
            _as_str(_pick(envelope, "code", "Code")) or "TURN_FAILED",
            _as_str(_pick(envelope, "message", "Message")) or "the turn failed",
        )

    # Every other refusal — 400 / 405 / 500 / 503, and anything a proxy invents
    # — is surfaced as a provider-stage envelope so the caller handles it
    # through the same turn-failed path. The endpoint's OWN code is preferred
    # over a synthesised one: a body-carried secret and a missing key header are
    # different mistakes with different remedies, and HTTP_400 names neither.
    return _failed(
        _as_stage(_pick(envelope, "stage", "Stage")),
        _as_str(_pick(envelope, "code", "Code")) or f"HTTP_{status}",
        _as_str(_pick(envelope, "message", "Message")) or f"unexpected status {status}",
    )
