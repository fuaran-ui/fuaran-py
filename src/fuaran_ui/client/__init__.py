"""``fuaran_ui.client`` — a typed client over the Fuaran generation endpoint.

The Fuaran generation endpoint is a paid, stateless, bring-your-own-key (BYOK)
HTTPS surface: it takes a prompt (+ an optional current tree) and returns a
new canonical wire-format tree. This package collapses the integration to
**call, hold the tree, repair**::

    from fuaran_ui.client import FuaranClient, FuaranSession, Produced

    client = FuaranClient("https://<your-endpoint>/generate",
                          access_token=..., provider_key=...)
    session = FuaranSession(client)
    result = session.next("a metric card showing revenue")
    if isinstance(result, Produced):
        tree = result.decode_tree()                  # typed Node via the wire codec
    result = session.next("rename the metric to ARR")  # a cheap repair diff

The endpoint URL + paid access token are the commercial gate; this client is
a thin, OSS-safe HTTPS + types layer (standard library only). Credential
placement guidance (direct vs server-proxied — never leak the BYOK key) is in
the README and on :class:`~fuaran_ui.client.client.FuaranClient`.

Public surface:

* :class:`~fuaran_ui.client.client.FuaranClient` — ``generate(prompt, ...)`` →
  ``Produced | AccessDenied | TurnFailed``
* :class:`~fuaran_ui.client.session.FuaranSession` — the turn loop that holds
  the current tree so each prompt is a repair diff
* :mod:`~fuaran_ui.client.contract` — the typed surface contract (+ the
  surface-version echo helpers)
* :mod:`~fuaran_ui.client.wire` — the pinned HTTP envelope mapping (reused by
  server-proxied adapters)
"""

from __future__ import annotations

from .client import FuaranClient, Transport, default_transport, is_secure_endpoint
from .contract import (
    SURFACE_VERSION,
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
    is_surface_version_compatible,
)
from .session import FuaranSession
from .wire import malformed_response, parse_produced_detail, parse_turn_response, to_wire_body

__all__ = [
    "SURFACE_VERSION",
    "AccessDenied",
    "AppliedOp",
    "ClientCode",
    "FuaranClient",
    "FuaranSession",
    "Produced",
    "ProducedDetail",
    "RecoverableError",
    "SnapshotState",
    "Transport",
    "TurnFailed",
    "TurnResult",
    "TurnStage",
    "default_transport",
    "is_secure_endpoint",
    "is_surface_version_compatible",
    "malformed_response",
    "parse_produced_detail",
    "parse_turn_response",
    "to_wire_body",
]
