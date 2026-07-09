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

Run it::

    pip install -r requirements.txt          # fastapi + uvicorn + fuaran-py
    export FUARAN_ENDPOINT=https://<your-endpoint>/generate
    export FUARAN_ACCESS_TOKEN=...            # server-side only
    export FUARAN_PROVIDER_KEY=...            # the BYOK key, server-side only
    uvicorn app:app --port 14140

then open http://127.0.0.1:14140/. See README.md (incl. the equivalent Django view).
(14140 is a free slot in the Fuaran-UI 14000-band; adjust to any free port.)
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

from fuaran_py.client import AccessDenied, FuaranClient, Produced, TurnFailed
from fuaran_py.renderer import reference_css_path, render_html


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


def create_app(client: FuaranClient | None = None) -> FastAPI:
    """Build the app. Pass ``client`` to inject a pre-configured SDK client
    (used by the smoke test with a mock transport); otherwise it is built from
    the environment."""
    app = FastAPI(title="Fuaran FastAPI server-proxied BYOK host")
    resolved = client if client is not None else build_client(load_settings())

    @app.post("/generate")
    def generate(req: GenerateRequest) -> JSONResponse:
        # The proxy hop: the SDK call runs here with the server-held token + key.
        result = resolved.generate(req.prompt, current_tree_json=req.current_tree_json)

        if isinstance(result, Produced):
            decoded = result.decode_tree()
            html = render_html(decoded.value) if decoded.ok else ""
            return JSONResponse(
                {
                    "status": "produced",
                    "tree": result.tree_json,  # the client holds this for the next repair
                    "html": html,  # server-rendered markup for display
                    "version": result.version,
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
  <form id="f">
    <input id="p" type="text" placeholder="e.g. a metric card showing revenue" autocomplete="off" />
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
      const res = await fetch('/generate', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ prompt, current_tree_json: currentTree }),
      });
      const data = await res.json();
      if (data.status === 'produced') {
        currentTree = data.tree;          // hold it for the next repair
        outEl.innerHTML = data.html;
        statusEl.textContent = 'produced (surface ' + data.version + ')';
      } else if (data.status === 'access_denied') {
        statusEl.textContent = 'access denied: ' + data.reason;
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
