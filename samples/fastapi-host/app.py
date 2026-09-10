"""A worked FastAPI host for the Fuaran generation endpoint — the **server-proxied
BYOK** pattern.

The access token (the paid credential) and the BYOK provider key are held in
**server** config (environment variables) and injected by this proxy; the browser
posts only a prompt and never sees either secret. The server calls the Phase 235
SDK (:class:`fuaran_py.client.FuaranClient`), decodes the returned canonical wire
tree, renders it to HTML server-side (:func:`fuaran_py.renderer.render_html`), and
sends the browser the tree + its rendered markup — never a credential.

The turn loop lives client-side: the page holds the current tree's canonical JSON
and posts it back as ``currentTreeJson`` so each prompt is a cheap *repair* (the
token-saving ergonomic), while the server stays stateless.

THIS ROUTE IS NOT AN OPEN SPEND ENDPOINT. It holds a BYOK key and answers
whoever can reach it, so it is fail-closed on authorisation (a shared secret, or
an explicit anonymous opt-out — the app refuses to start with neither), caps
prompt length (prompt length is the input-token bill, and the one cost dimension
a caller controls directly), and rate-limits per caller. None of the three is a
substitute for a real gateway; what they are is a floor, so that a copy of this
sample cannot become an unauthenticated, unbounded spend endpoint by omission.

Run it::

    pip install "fuaran-py[live-host]"        # the host + fastapi + uvicorn
    export FUARAN_ENDPOINT=https://<your-endpoint>/generate
    export FUARAN_ACCESS_TOKEN=...            # server-side only
    export FUARAN_PROVIDER_KEY=...            # the BYOK key, server-side only
    export FUARAN_SAMPLE_SECRET=...           # callers present it as X-Sample-Secret
    #   ...or, deliberately: export FUARAN_SAMPLE_ANONYMOUS=1
    uvicorn app:app --port 14140

then open http://127.0.0.1:14140/. See README.md (incl. the equivalent Django view).
(14140 is a free slot in the Fuaran-UI 14000-band; adjust to any free port.)
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from fuaran_py.client import AccessDenied, FuaranClient, Produced, TurnFailed
from fuaran_py.renderer import reference_css_path, render_html

#: The header a caller presents the shared secret in. A header rather than a
#: body member, for the reason the endpoint itself gives: a body is the thing
#: most likely to be logged wholesale by an intermediary.
SECRET_HEADER = "X-Sample-Secret"

#: A decode failure is the one thing this route refuses on that the CALLER
#: cannot fix and the OPERATOR can, so it is the one refusal that is logged.
_LOG = logging.getLogger(__name__)

#: Long enough for any real authoring prompt, short enough that a scripted
#: caller cannot make each request expensive.
MAX_PROMPT_LENGTH = 4000

#: Enough for interactive authoring, far short of what a script does in a minute.
RATE_LIMIT_MAX_TURNS = 30
RATE_LIMIT_WINDOW_SECONDS = 60.0


@dataclass(frozen=True)
class Settings:
    """Server-side config — the two secrets live here, never on the client."""

    endpoint: str
    access_token: str | None
    provider_key: str | None


def load_settings() -> Settings:
    return Settings(
        endpoint=os.environ.get("FUARAN_ENDPOINT", ""),
        access_token=os.environ.get("FUARAN_ACCESS_TOKEN"),
        provider_key=os.environ.get("FUARAN_PROVIDER_KEY"),
    )


@dataclass
class RateLimiter:
    """A per-process fixed-window limiter.

    A real deployment wants a shared store — this one is per-instance, so two
    instances allow twice the traffic. That is a fact worth stating rather than
    a defect worth hiding, and the clock is injectable so a test can prove the
    window actually resets.
    """

    max_turns: int = RATE_LIMIT_MAX_TURNS
    window_seconds: float = RATE_LIMIT_WINDOW_SECONDS
    now: Callable[[], float] = time.monotonic
    _windows: dict[str, tuple[float, int]] = field(default_factory=dict)

    def admit(self, caller: str) -> bool:
        """True when this turn is within the caller's window."""
        current = self.now()
        started, turns = self._windows.get(caller, (current, 0))
        if current - started >= self.window_seconds:
            started, turns = current, 0
        turns += 1
        self._windows[caller] = (started, turns)
        return turns <= self.max_turns


@dataclass(frozen=True)
class Policy:
    """Everything this route refuses on, in one object so a host cannot wire the
    SDK client and forget the guards.

    ``expected_secret`` of ``None`` means ANONYMOUS — every caller served. There
    is no way to reach that state by accident: :func:`build_policy` refuses to
    produce one unless the operator asked for it by name.
    """

    expected_secret: str | None
    max_prompt_length: int = MAX_PROMPT_LENGTH
    rate_limiter: RateLimiter | None = None

    def authorise(self, presented: str | None) -> bool:
        if self.expected_secret is None:
            return True
        # Constant-time: the sample is copied, and a short-circuiting compare
        # over a secret is copied with it.
        return presented is not None and hmac.compare_digest(presented, self.expected_secret)


def build_policy() -> tuple[Policy, str]:
    """Resolve the route's authorisation arm, or raise.

    There is no third answer on purpose: a sample that quietly served everyone
    when a variable was unset would be copied into something that quietly serves
    everyone.
    """
    secret = os.environ.get("FUARAN_SAMPLE_SECRET")
    if secret:
        return (
            Policy(expected_secret=secret, rate_limiter=RateLimiter()),
            f"shared secret ({SECRET_HEADER})",
        )
    if os.environ.get("FUARAN_SAMPLE_ANONYMOUS") == "1":
        return (
            Policy(expected_secret=None, rate_limiter=RateLimiter()),
            "ANONYMOUS - every caller is served",
        )
    raise RuntimeError(
        "this route holds a BYOK key. Set FUARAN_SAMPLE_SECRET to the secret callers must "
        f"present in the {SECRET_HEADER} header, or FUARAN_SAMPLE_ANONYMOUS=1 if you genuinely "
        "mean to serve everyone."
    )


def _refuse(status: int, code: str, message: str) -> JSONResponse:
    """The endpoint's own refusal envelope, so a page decodes ONE contract
    whether it is talking to this proxy or a deployment directly."""
    return JSONResponse(
        status_code=status,
        content={"status": "refused", "error": {"code": code, "message": message}},
    )


def build_client(settings: Settings) -> FuaranClient:
    """The SDK client with the credentials injected server-side (the BYOK key
    never leaves this process)."""
    return FuaranClient(
        settings.endpoint,
        access_token=settings.access_token,
        provider_key=settings.provider_key,
    )


class GenerateRequest(BaseModel):
    """The browser's request — a prompt and (from the second turn) the held tree.
    Note there is **no** credential field: secrets are server-side only."""

    prompt: str
    current_tree_json: str | None = None


def create_app(client: FuaranClient | None = None, policy: Policy | None = None) -> FastAPI:
    """Build the app. Pass ``client`` to inject a pre-configured SDK client
    (used by the smoke test with a mock transport) and ``policy`` to inject the
    spend guards; otherwise both are built from the environment."""
    app = FastAPI(title="Fuaran FastAPI server-proxied BYOK host")
    resolved = client if client is not None else build_client(load_settings())
    resolved_policy = policy if policy is not None else build_policy()[0]

    @app.post("/generate")
    def generate(req: GenerateRequest, request: Request) -> JSONResponse:
        # The guards, in the order that spends least: authorise, then bound the
        # input, then bound the rate. Each refusal happens before the endpoint
        # is called, so none of them costs a token.
        if not resolved_policy.authorise(request.headers.get(SECRET_HEADER)):
            return _refuse(401, "ACCESS_DENIED", "this route requires the shared secret configured for it")
        if req.prompt.strip() == "":
            return _refuse(400, "BAD_REQUEST", "a prompt is required")
        if len(req.prompt) > resolved_policy.max_prompt_length:
            return _refuse(
                400,
                "PROMPT_TOO_LONG",
                f"a prompt may be at most {resolved_policy.max_prompt_length} characters",
            )
        # The remote address is the weakest useful caller key, and the README
        # says so: behind a proxy every caller shares one. A real deployment
        # keys on whatever it already authenticates.
        caller = request.client.host if request.client is not None else "unknown"
        if resolved_policy.rate_limiter is not None and not resolved_policy.rate_limiter.admit(caller):
            return _refuse(429, "RATE_LIMITED", "too many turns from this caller; try again shortly")

        # The proxy hop: the SDK call runs here with the server-held token + key.
        result, detail = resolved.generate_detailed(req.prompt, current_tree_json=req.current_tree_json)

        if isinstance(result, Produced):
            decoded = result.decode_tree()
            if not decoded.ok:
                # A decode failure is a FAILURE. It used to be `html: ""` inside
                # a 200, which the page rendered as an empty panel and the caller
                # recorded as a successful turn - while still holding the tree
                # that could not be decoded, so every later repair built on it.
                #
                # The structured error goes to the SERVER LOG and not into the
                # response, and the split is deliberate. The operator is the one
                # who can act on `$.kind.value` / MISSING_FIELD - it names which
                # endpoint emitted what - while the caller can only retry, so
                # returning the path would leak the deployment's internals to
                # whoever can reach the route in exchange for nothing. Logging it
                # is what keeps this a diagnosable failure rather than an opaque
                # 502 that recurs with no evidence anywhere.
                _LOG.error(
                    "generate: the endpoint produced a tree this host could not decode (%s at %s: %s)",
                    decoded.error.code,
                    decoded.error.path,
                    decoded.error.message,
                )
                return _refuse(
                    502,
                    "MALFORMED_TREE",
                    "the endpoint produced a tree this host could not decode; nothing was rendered",
                )
            return JSONResponse(
                {
                    "status": "produced",
                    "tree": result.tree_json,  # the client holds this for the next repair
                    "html": render_html(decoded.value),  # server-rendered markup for display
                    "version": result.version,
                    # What the deployment chose, and what actually answered. A
                    # `servedModel` of null means UNREPORTED - deliberately not
                    # the model the deployment asked for.
                    "provider": detail.provider if detail is not None else None,
                    "servedModel": detail.served_model if detail is not None else None,
                }
            )

        if isinstance(result, AccessDenied):
            # 401 — rejected at the edge; the BYOK key was never used.
            return JSONResponse(status_code=401, content={"status": "access_denied", "reason": result.reason})

        assert isinstance(result, TurnFailed)
        return JSONResponse(
            status_code=422,
            content={
                "status": "turn_failed",
                "error": {"stage": result.error.stage, "code": result.error.code, "message": result.error.message},
            },
        )

    @app.get("/fuaran-reference.css")
    def stylesheet() -> PlainTextResponse:
        return PlainTextResponse(reference_css_path().read_text(encoding="utf-8"), media_type="text/css")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _INDEX_HTML

    return app


# A minimal client page: prompt box + a turn loop that holds the tree JSON and
# posts it back as currentTreeJson so each prompt repairs the last tree. It shows
# the server-rendered markup; there is no credential anywhere in this page.
_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Fuaran — server-proxied BYOK host</title>
  <link rel="stylesheet" href="/fuaran-reference.css" />
  <style>
    body { font-family: system-ui, sans-serif; margin: 2rem; max-width: 60rem; }
    form { display: flex; gap: .5rem; margin-bottom: 1rem; }
    input[type=text] { flex: 1; padding: .5rem; }
    #status { color: #666; min-height: 1.2em; }
    #out { border: 1px solid #ddd; border-radius: 8px; padding: 1rem; margin-top: 1rem; }
  </style>
</head>
<body>
  <h1>Fuaran — server-proxied BYOK</h1>
  <p>The access token and BYOK key stay on the server. This page only sends a prompt.</p>
  <p>
    This route is not an open spend endpoint: it authorises the caller, caps prompt
    length, and rate-limits per caller. Leave the secret box empty when the host was
    started with <code>FUARAN_SAMPLE_ANONYMOUS=1</code>.
  </p>
  <form id="f">
    <input id="p" type="text" placeholder="e.g. a metric card showing revenue" autocomplete="off" />
    <input id="s" type="password" placeholder="shared secret (if required)" autocomplete="off" />
    <button type="submit">Generate</button>
    <button type="button" id="reset">Reset</button>
  </form>
  <div id="status"></div>
  <div id="out"></div>
  <script>
    let currentTree = null;               // the held tree — client-side turn loop
    const statusEl = document.getElementById('status');
    const outEl = document.getElementById('out');
    document.getElementById('reset').onclick = () => {
      currentTree = null; outEl.innerHTML = ''; statusEl.textContent = 'reset';
    };
    document.getElementById('f').onsubmit = async (e) => {
      e.preventDefault();
      const prompt = document.getElementById('p').value.trim();
      if (!prompt) return;
      statusEl.textContent = 'generating…';
      const secret = document.getElementById('s').value;
      const res = await fetch('/generate', {
        method: 'POST',
        headers: Object.assign(
          { 'content-type': 'application/json' },
          secret ? { 'X-Sample-Secret': secret } : {},
        ),
        body: JSON.stringify({ prompt, current_tree_json: currentTree }),
      });
      const data = await res.json();
      if (data.status === 'produced') {
        currentTree = data.tree;          // hold it for the next repair
        outEl.innerHTML = data.html;
        const served = data.servedModel || 'unreported';
        statusEl.textContent =
          'produced (surface ' + data.version + ', served ' + served + ')';
      } else if (data.status === 'access_denied') {
        statusEl.textContent = 'access denied: ' + data.reason;
      } else if (data.status === 'refused') {
        // This route's OWN refusal - no loop stage, because no turn ran.
        statusEl.textContent = 'refused [' + data.error.code + ']: ' + data.error.message;
      } else {
        statusEl.textContent = 'turn failed [' + data.error.stage + '/' + data.error.code + ']: ' + data.error.message;
      }
    };
  </script>
</body>
</html>
"""


# `uvicorn app:app` entry point.
app = create_app()
