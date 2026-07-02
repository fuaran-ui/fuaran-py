"""The HTTPS client over the Fuaran generation endpoint.

``client.generate(prompt, current_tree_json=...)`` collapses the integration
to one call: it builds the request, sends it, and returns the typed three-way
result. No hand-rolled HTTP, no JSON wrangling, no token plumbing. The
default transport is the standard library's ``urllib`` (the package stays
dependency-light); tests and alternate runtimes inject their own.

Credential placement (load-bearing — see the README's "BYOK key and access
token" section):

* **Direct** (a server-side script, a notebook, a backend service): pass
  ``access_token`` + ``provider_key`` at construction, sourced from the
  environment or a secret store — never from a committed file.
* **Server-proxied** (anything user-facing or multi-user): point ``endpoint``
  at your own proxy path and pass NO credentials here — the proxy injects
  them server-side, so the BYOK key never reaches the calling environment.

The client never logs, echoes, or persists either credential; its ``repr``
deliberately omits them.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping

from .contract import RecoverableError, TurnFailed, TurnResult
from .wire import parse_turn_response, to_wire_body

#: The minimal transport shape the client needs: ``(url, headers, body) →
#: (status, response_text)`` for one POST. Satisfied by the default
#: ``urllib``-backed transport and trivial to fake in a test. A raised
#: exception is surfaced as a ``provider``-stage ``NETWORK`` failure.
type Transport = Callable[[str, Mapping[str, str], bytes], tuple[int, str]]


def default_transport(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, str]:
    """POST via the standard library. Non-2xx statuses are returned (not
    raised) so the status map in :mod:`fuaran_py.client.wire` sees them."""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        # 401 / 422 / 5xx arrive here — they are protocol outcomes, not
        # transport failures; hand the status + body to the parser.
        return error.code, error.read().decode("utf-8")


class FuaranClient:
    """A thin, typed client over the Fuaran generation endpoint.

    Construct once with the endpoint (+ credentials, in the direct pattern)
    and reuse it across turns;
    :class:`~fuaran_py.client.session.FuaranSession` wraps it with the
    tree-carrying loop.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        access_token: str | None = None,
        provider_key: str | None = None,
        headers: Mapping[str, str] | None = None,
        send_bearer_header: bool = True,
        transport: Transport | None = None,
    ) -> None:
        """``endpoint`` is the Fuaran generation endpoint URL, or a same-origin
        proxy path in the server-proxied pattern.

        ``access_token`` (the paid credential) and ``provider_key`` (the BYOK
        key, memory-only — never commit one) are optional: omit both in the
        server-proxied pattern, where the proxy injects them. Per-call
        arguments to :meth:`generate` override them. ``send_bearer_header``
        also sends the access token as ``Authorization: Bearer <token>`` (in
        addition to the request body) — many edge gateways gate on it; set
        ``False`` for a deployment that reads the token from the body only.
        """
        if endpoint.strip() == "":
            raise ValueError("fuaran_py.client: `endpoint` is required.")
        self._endpoint = endpoint
        self._access_token = access_token
        self._provider_key = provider_key
        self._headers: dict[str, str] = dict(headers) if headers is not None else {}
        self._send_bearer_header = send_bearer_header
        self._transport: Transport = transport if transport is not None else default_transport

    def __repr__(self) -> str:
        # Deliberately credential-free: a logged client never leaks the BYOK
        # key or the access token.
        return f"FuaranClient(endpoint={self._endpoint!r})"

    def generate(
        self,
        prompt: str,
        *,
        current_tree_json: str | None = None,
        provider_key: str | None = None,
        access_token: str | None = None,
        disable_corpus_read: bool | None = None,
        contribute_corpus: bool | None = None,
    ) -> TurnResult:
        """Run one generation turn.

        Pass ``current_tree_json`` (the canonical wire JSON of the tree the
        model is editing) to make the turn a *repair* — the token-saving
        ergonomic the whole model hinges on. Omit it for a fresh generation;
        the turn-loop helper carries it forward for you.

        ``disable_corpus_read`` opts OUT of corpus reads for this turn;
        ``contribute_corpus`` opts IN to contributing it as a corpus
        candidate. Both default to the endpoint's privacy-preserving values
        when omitted.

        Returns the typed :data:`~fuaran_py.client.contract.TurnResult`
        (produced / access denied / turn failed); it never raises for an
        endpoint-level outcome — a transport error surfaces as a turn-failed
        result with a ``provider``-stage ``NETWORK`` envelope.
        """
        token = access_token if access_token is not None else self._access_token
        key = provider_key if provider_key is not None else self._provider_key

        headers: dict[str, str] = {"content-type": "application/json", **self._headers}
        if token is not None and self._send_bearer_header:
            headers["authorization"] = f"Bearer {token}"

        body = json.dumps(
            to_wire_body(
                prompt,
                current_tree_json=current_tree_json,
                byok_key=key,
                access_token=token,
                disable_corpus_read=disable_corpus_read,
                contribute_corpus=contribute_corpus,
            )
        ).encode("utf-8")

        try:
            status, text = self._transport(self._endpoint, headers, body)
        except Exception as error:  # noqa: BLE001 — every transport failure funnels into the typed result
            return TurnFailed(RecoverableError(stage="provider", code="NETWORK", message=str(error)))

        return parse_turn_response(status, text)
