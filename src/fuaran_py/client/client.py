"""The client over the Fuaran generation endpoint.

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

Both credentials travel as HEADERS — ``Authorization: Bearer`` and
``X-Fuaran-Provider-Key`` — and NEVER in the request body. A body is the thing
most likely to be logged wholesale by an intermediary; a header is the thing
most likely to be redacted by one. The endpoint enforces it: a body carrying
``ByokKey`` or ``AccessToken`` is refused ``400 SECRETS_IN_BODY`` and the value
is not read. This module gives you no way to write one.

THE ENDPOINT MUST BE https, OR LOOPBACK, OR EXPLICITLY OPTED OUT OF. Because
both credentials ride headers on every call, a plaintext hop hands them to
anyone on the path. ``http://127.0.0.1`` and ``http://localhost`` are admitted
(that is where the offline mock and a local proxy live, and no packet leaves
the machine); anything else plaintext is refused as ``INSECURE_ENDPOINT``
unless ``allow_insecure_endpoint=True`` — an opt-in that has to be written
down, not a default that has to be noticed.

NO UPSTREAM EXCEPTION TEXT REACHES THE CALLER. A transport failure and an
elapsed timeout are ``NETWORK`` with a FIXED message: an exception string can
quote a URL, a header, or a proxy's internal hostname, and this result is
routinely rendered into a page. The detail belongs in the host's log, which is
why the transport is a seam the host owns.

The client never logs, echoes, or persists either credential; its ``repr``
deliberately omits them.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping

from .contract import ClientCode, ProducedDetail, RecoverableError, TurnFailed, TurnResult
from .wire import parse_produced_detail, parse_turn_response, to_wire_body

#: The minimal transport shape the client needs: ``(url, headers, body) →
#: (status, response_text)`` for one POST. Satisfied by the default
#: ``urllib``-backed transport and trivial to fake in a test. A raised
#: exception is surfaced as a ``provider``-stage ``NETWORK`` failure.
type Transport = Callable[[str, Mapping[str, str], bytes], tuple[int, str]]

#: The fixed message a failed call reports. Fixed on purpose — see the module
#: docstring.
_NETWORK_MESSAGE = "the request to the generation endpoint did not complete"
_TIMEOUT_MESSAGE = "the request to the generation endpoint timed out"


def default_transport(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    *,
    timeout: float | None = None,
) -> tuple[int, str]:
    """POST via the standard library. Non-2xx statuses are returned (not
    raised) so the status map in :mod:`fuaran_py.client.wire` sees them.

    ``timeout`` is a wall-clock ceiling in seconds; ``None`` waits as long as
    the socket does. An elapsed timeout raises, and the client turns that into
    a ``NETWORK`` failure like any other transport error.
    """
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        # 4xx / 5xx arrive here — they are protocol outcomes, not transport
        # failures; hand the status + body to the parser.
        return error.code, error.read().decode("utf-8")


def is_secure_endpoint(endpoint: str) -> bool:
    """True when credentials may be sent to this endpoint.

    A relative path (no scheme) is same-origin by construction and admitted; an
    absolute ``https`` URL is admitted; plaintext is admitted only to loopback.
    Exposed so a host can apply the same rule to a URL it is about to configure
    rather than discovering it on the first turn.
    """
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme == "":
        return True
    if parsed.scheme == "https":
        return True
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


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
        provider: str | None = None,
        headers: Mapping[str, str] | None = None,
        send_bearer_header: bool = True,
        timeout: float | None = None,
        allow_insecure_endpoint: bool = False,
        transport: Transport | None = None,
    ) -> None:
        """``endpoint`` is the Fuaran generation endpoint URL, or a same-origin
        proxy path in the server-proxied pattern.

        ``access_token`` (the paid credential) and ``provider_key`` (the BYOK
        key, memory-only — never commit one) are optional: omit both in the
        server-proxied pattern, where the proxy injects them. Per-call
        arguments to :meth:`generate` override them. Both are sent as HEADERS.

        ``provider`` names which allowlisted provider id to ask for; omitted,
        the deployment chooses its own default. ``send_bearer_header=False``
        suppresses ``Authorization`` — that is the endpoint's only auth
        channel, so set it only when pointing at a proxy that authenticates
        some other way, and supply that header via ``headers``.

        ``timeout`` is a wall-clock ceiling in seconds on one turn (default
        transport only; an injected transport owns its own). It is applied to
        the default transport, so an elapsed timeout is a ``NETWORK``
        turn-failed rather than an exception.

        ``allow_insecure_endpoint`` permits a plaintext, non-loopback endpoint;
        see the module docstring for why it is an opt-in.
        """
        if endpoint.strip() == "":
            raise ValueError("fuaran_py.client: `endpoint` is required.")
        self._endpoint = endpoint
        self._access_token = access_token
        self._provider_key = provider_key
        self._provider = provider
        self._headers: dict[str, str] = dict(headers) if headers is not None else {}
        self._send_bearer_header = send_bearer_header
        self._timeout = timeout
        self._allow_insecure_endpoint = allow_insecure_endpoint
        if transport is not None:
            self._transport: Transport = transport
        else:

            def _default(url: str, hdrs: Mapping[str, str], body: bytes) -> tuple[int, str]:
                return default_transport(url, hdrs, body, timeout=timeout)

            self._transport = _default

    def __repr__(self) -> str:
        # Deliberately credential-free: a logged client never leaks the BYOK
        # key or the access token.
        return f"FuaranClient(endpoint={self._endpoint!r})"

    def _insecure_endpoint(self) -> TurnResult:
        return TurnFailed(
            RecoverableError(
                stage="provider",
                code=ClientCode.INSECURE_ENDPOINT,
                message=(
                    "the endpoint is plaintext http and is not loopback; the access token and "
                    "BYOK key would travel in the clear. Use https, or set "
                    "allow_insecure_endpoint if you genuinely mean it."
                ),
            )
        )

    def generate_detailed(
        self,
        prompt: str,
        *,
        current_tree_json: str | None = None,
        provider_key: str | None = None,
        access_token: str | None = None,
        disable_corpus_read: bool | None = None,
        contribute_corpus: bool | None = None,
        interaction_id: str | None = None,
    ) -> tuple[TurnResult, ProducedDetail | None]:
        """Run one generation turn, returning the typed result AND the
        deployment facts the reply carried (``ops_applied``, ``provider``,
        ``served_model``, the snapshot state). The detail is ``None`` for every
        non-produced outcome.
        """
        if not self._allow_insecure_endpoint and not is_secure_endpoint(self._endpoint):
            # Refused BEFORE the request is built, so neither credential is
            # ever handed to the transport.
            return self._insecure_endpoint(), None

        token = access_token if access_token is not None else self._access_token
        key = provider_key if provider_key is not None else self._provider_key

        headers: dict[str, str] = {"content-type": "application/json", **self._headers}
        if token is not None and self._send_bearer_header:
            headers["authorization"] = f"Bearer {token}"
        if key is not None:
            headers["x-fuaran-provider-key"] = key
        if self._provider is not None:
            headers["x-fuaran-provider"] = self._provider

        body = json.dumps(
            to_wire_body(
                prompt,
                current_tree_json=current_tree_json,
                disable_corpus_read=disable_corpus_read,
                contribute_corpus=contribute_corpus,
                interaction_id=interaction_id,
            )
        ).encode("utf-8")

        try:
            status, text = self._transport(self._endpoint, headers, body)
        except TimeoutError:
            return (
                TurnFailed(
                    RecoverableError(
                        stage="provider", code=ClientCode.NETWORK, message=_TIMEOUT_MESSAGE
                    )
                ),
                None,
            )
        except Exception:  # noqa: BLE001 — every transport failure funnels into the typed result
            # Deliberately no exception text — see the module docstring.
            return (
                TurnFailed(
                    RecoverableError(
                        stage="provider", code=ClientCode.NETWORK, message=_NETWORK_MESSAGE
                    )
                ),
                None,
            )

        return parse_turn_response(status, text), parse_produced_detail(status, text)

    def generate(
        self,
        prompt: str,
        *,
        current_tree_json: str | None = None,
        provider_key: str | None = None,
        access_token: str | None = None,
        disable_corpus_read: bool | None = None,
        contribute_corpus: bool | None = None,
        interaction_id: str | None = None,
    ) -> TurnResult:
        """Run one generation turn.

        Pass ``current_tree_json`` (the canonical wire JSON of the tree the
        model is editing) to make the turn a *repair* — the token-saving
        ergonomic the whole model hinges on. Omit it for a fresh generation;
        the turn-loop helper carries it forward for you.

        ``disable_corpus_read`` opts OUT of corpus reads for this turn;
        ``contribute_corpus`` opts IN to contributing it as a corpus
        candidate. Both default to the endpoint's privacy-preserving values
        when omitted. ``interaction_id`` is your own opaque correlation id.

        Returns the typed :data:`~fuaran_py.client.contract.TurnResult`
        (produced / access denied / turn failed); it never raises for an
        endpoint-level outcome — a transport error or an elapsed ``timeout``
        surfaces as a turn-failed result with a ``provider``-stage ``NETWORK``
        envelope.
        """
        result, _ = self.generate_detailed(
            prompt,
            current_tree_json=current_tree_json,
            provider_key=provider_key,
            access_token=access_token,
            disable_corpus_read=disable_corpus_read,
            contribute_corpus=contribute_corpus,
            interaction_id=interaction_id,
        )
        return result
