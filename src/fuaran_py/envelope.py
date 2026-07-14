"""The wire versioning envelope (WIRE_FORMAT.md §15).

An artefact may be wrapped as
``{"$payload":<Node|TreeOp>,"$profile":"<name>@<major>.<minor>"}``. A consumer
negotiates the authored profile against its own ``core@1.0``:

* **Current** (same name+major, authored minor ≤ ours) — decode fully.
* **Behind** (same name+major, authored minor > ours) — tolerate: an unknown
  kind becomes a transport-only preserved payload whose verbatim bytes re-encode
  identically (must-ignore-but-preserve).
* **Foreign** (different name, or different major) — hard-refuse
  (``FOREIGN_PROFILE``), never silently mis-decode.

The transport-only preserve is decode-only: there is no encoder entry point that
mints an unknown kind, so the closed authoring surface stays intact. This is the
Python twin of the Go host's ``wire/envelope.go`` (``Negotiate`` /
``DecodeEnvelope`` / ``EncodeEnvelope``) and certifies against the shared
``envelope-*`` corpus family.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

from .canonical import encode_value
from .model import Obj, Value, from_json
from .result import (
    INVALID_JSON,
    MISSING_FIELD,
    WRONG_NODE_KIND,
    WRONG_TYPE,
    DecodeError,
    DecodeResult,
    Err,
    Ok,
)
from .schema import decode_node

# ── §15 profile / negotiation vocabulary ────────────────────────────────────

HOST_PROFILE = "core@1.0"
"""The profile this host implements."""

# The §15 negotiation outcome code — kept OUT of the core six (like §18's
# extra codes); a foreign profile is refused with this code at ``$.$profile``.
FOREIGN_PROFILE = "FOREIGN_PROFILE"

type Negotiation = Literal["Current", "Behind", "Foreign"]

CURRENT: Negotiation = "Current"
BEHIND: Negotiation = "Behind"
FOREIGN: Negotiation = "Foreign"


@dataclass(frozen=True)
class Envelope:
    """A decoded §15 versioned artefact.

    ``payload`` is a decoded ``Node`` for Current, or the verbatim-preserved
    structural value for a Behind unknown kind; ``profile`` is the authored
    profile string; ``negotiation`` is the outcome.
    """

    payload: Value
    profile: str
    negotiation: Negotiation


def _parse_profile(p: str) -> tuple[str, int, int] | None:
    at = p.find("@")
    if at <= 0:
        return None
    name = p[:at]
    ver = p[at + 1 :]
    dot = ver.find(".")
    if dot < 0:
        return None
    try:
        major = int(ver[:dot])
        minor = int(ver[dot + 1 :])
    except ValueError:
        return None
    # ``int`` accepts leading ``+``/``-`` and surrounding forms the wire never
    # uses; reject anything that isn't a plain non-negative integer literal.
    if not ver[:dot].isdigit() or not ver[dot + 1 :].isdigit():
        return None
    return name, major, minor


def negotiate(profile: str) -> Negotiation:
    """Compare an authored profile against the host's ``core@1.0``."""
    parsed = _parse_profile(profile)
    if parsed is None:
        return FOREIGN
    name, major, minor = parsed
    if name != "core" or major != 1:
        return FOREIGN
    return CURRENT if minor <= 0 else BEHIND


def _reroot(error: DecodeError, prefix: str) -> DecodeError:
    """Re-root a payload decode error's ``$``-path under ``prefix``."""
    return DecodeError(error.code, prefix + error.path[1:], error.message, error.expected_shape)


def decode_envelope(text: str) -> DecodeResult[Envelope]:
    """Decode a §15 versioned artefact.

    Negotiate the profile, then decode the payload (Current → strict node
    decode; Behind → tolerate an unknown kind by preserving it verbatim). A
    Foreign profile refuses with ``FOREIGN_PROFILE`` at ``$.$profile``.
    """
    try:
        raw = json.loads(text)
    except ValueError as exc:
        return Err(DecodeError(INVALID_JSON, "$", f"input is not valid JSON: {exc}"))

    if not isinstance(raw, dict):
        return Err(DecodeError(WRONG_TYPE, "$", "expected an object at $"))

    if "$profile" not in raw:
        return Err(DecodeError(MISSING_FIELD, "$.$profile", "missing required field '$profile'"))
    profile = raw["$profile"]
    if not isinstance(profile, str):
        return Err(DecodeError(WRONG_TYPE, "$.$profile", "$profile must be a string"))
    if "$payload" not in raw:
        return Err(DecodeError(MISSING_FIELD, "$.$payload", "missing required field '$payload'"))
    payload_raw = raw["$payload"]

    negotiation = negotiate(profile)
    if negotiation == FOREIGN:
        return Err(
            DecodeError(
                FOREIGN_PROFILE,
                "$.$profile",
                f"foreign profile '{profile}' — a different namespace or major version, hard-refused",
            )
        )

    node_result = decode_node(json.dumps(payload_raw))
    if isinstance(node_result, Ok):
        payload: Value = node_result.value
    elif negotiation == BEHIND and node_result.error.code == WRONG_NODE_KIND:
        # Must-ignore-but-preserve: an unknown kind a Behind consumer meets is
        # preserved verbatim so re-encoding reproduces the producer's bytes.
        payload = from_json(payload_raw)
    else:
        return Err(_reroot(node_result.error, "$.$payload"))

    return Ok(Envelope(payload=payload, profile=profile, negotiation=negotiation))


def encode_envelope(env: Envelope) -> str:
    """Re-encode an envelope to canonical wire JSON.

    ``$payload`` sorts before ``$profile`` under the code-point key order, so the
    round-trip is byte-exact.
    """
    return encode_value(Obj(None, {"$payload": env.payload, "$profile": env.profile}))
