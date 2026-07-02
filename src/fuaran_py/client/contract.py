"""The typed contract for the Fuaran generation endpoint.

These types mirror the generation endpoint's surface contract field-for-field:
the turn request's fields (prompt, optional current tree, request-scoped
credentials, the additive corpus opt-in/opt-out flags), the three-way turn
result (produced / access denied / turn failed), the applied-op record, and
the surface-version echo. They are the lockstep counterpart of the endpoint's
published surface contract — a field added there is added here in the same
change, and :mod:`fuaran_py.client.wire` pins how each maps onto the HTTP
envelope.

The endpoint itself is the Fuaran generation endpoint: a paid, stateless,
bring-your-own-key (BYOK) HTTPS surface that takes a prompt (+ an optional
current tree) and returns a new canonical wire-format tree. The endpoint URL
and the paid access token are the commercial gate; this client is a thin,
OSS-safe HTTPS + types layer over it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal

from ..model import Node, Obj
from ..ops import decode_op
from ..result import DecodeResult
from ..schema import decode_node

#: The generation-surface contract version this client is built against, kept
#: in lockstep with the endpoint's surface-version stamp.
#:
#: The client-facing request/response *shape* below is the additive
#: corpus-flag contract — ``disable_corpus_read`` / ``contribute_corpus`` plus
#: the surface-version echo on a produced result. Later minor surface bumps
#: have only added server-side usage fields that never cross the client
#: boundary, so this shape is stable across them;
#: :meth:`~fuaran_py.client.client.FuaranClient.generate` echoes back whichever
#: version the live surface stamps (see :attr:`Produced.version`).
SURFACE_VERSION = "1.2.0"


def _major(version: str) -> str:
    return version.split(".")[0].strip()


def is_surface_version_compatible(echoed: str) -> bool:
    """True when an echoed surface version shares this client's major version,
    i.e. the request/response shape is one this client understands. A differing
    major signals a breaking surface revision the client predates."""
    return _major(echoed) == _major(SURFACE_VERSION) and _major(echoed) != ""


#: The loop stage at which a turn failed — distinguishes a rejected access
#: token (no provider call made) from a provider/transport failure from an
#: emission the endpoint refused to apply (the default-deny-by-shape gate).
type TurnStage = Literal["access-token", "provider", "parse", "apply"]

TURN_STAGES: frozenset[str] = frozenset({"access-token", "provider", "parse", "apply"})


@dataclass(frozen=True)
class AppliedOp:
    """One op the turn applied to reach the produced tree.

    Mirrors the surface's applied-op record: a dedup ``op_id`` and the
    canonical wire JSON of the op. :meth:`decode` yields the typed op.
    """

    op_id: str
    op_json: str

    def decode(self) -> DecodeResult[Obj]:
        """Decode ``op_json`` into the typed tagged op via the wire codec."""
        return decode_op(self.op_json)


@dataclass(frozen=True)
class RecoverableError:
    """A recoverable failure surfaced by a turn.

    ``code`` is a stable discriminant; ``message`` is model-facing — for the
    ``apply`` stage it carries the apply-error envelope so the caller's next
    prompt can re-emit against the hint. Never carries the BYOK key. Mirrors
    the surface's recoverable-error envelope.
    """

    stage: TurnStage
    code: str
    message: str


@dataclass(frozen=True)
class Produced:
    """The turn produced a new tree (HTTP 200).

    Carries the canonical wire JSON of the new tree, the ops applied this
    turn, and the echoed surface version. :meth:`decode_tree` yields the typed
    tree via the wire codec, so a caller never parses raw model output by hand.
    """

    tree_json: str
    ops: tuple[AppliedOp, ...]
    version: str

    kind: ClassVar[Literal["produced"]] = "produced"

    def decode_tree(self) -> DecodeResult[Node]:
        """Decode ``tree_json`` into the typed ``Node`` tree via the wire codec."""
        return decode_node(self.tree_json)


@dataclass(frozen=True)
class AccessDenied:
    """The access token was missing / expired / invalid — rejected at the edge
    before any provider call, so the BYOK key was never used (HTTP 401)."""

    reason: str

    kind: ClassVar[Literal["access_denied"]] = "access_denied"


@dataclass(frozen=True)
class TurnFailed:
    """The provider / parse / apply stage failed; carries the recoverable
    envelope (HTTP 422, or a synthesised envelope for an unexpected transport
    status / network error)."""

    error: RecoverableError

    kind: ClassVar[Literal["turn_failed"]] = "turn_failed"


#: The endpoint's reply — discriminate with ``isinstance`` (or on ``.kind``).
#: Mirrors the surface's three-case turn result; the HTTP status selects the
#: case (see :mod:`fuaran_py.client.wire`).
type TurnResult = Produced | AccessDenied | TurnFailed
