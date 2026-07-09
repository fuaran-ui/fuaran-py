# FastAPI host — server-proxied BYOK

A worked FastAPI host for the Fuaran generation endpoint, demonstrating the
**server-proxied bring-your-own-key (BYOK)** pattern: the paid access token and
the BYOK provider key live in **server** config and are injected by this proxy —
the browser posts only a prompt and never sees either secret.

It wraps the [`fuaran_py.client`](../../src/fuaran_py/client/) SDK: the `/generate`
route calls `FuaranClient.generate` server-side, decodes the returned canonical
wire tree, renders it to HTML with `fuaran_py.renderer.render_html`, and returns
the tree + its markup. The turn loop runs client-side — the page holds the current
tree's JSON and posts it back as `currentTreeJson`, so each prompt is a cheap
*repair* while the server stays stateless.

## Why server-proxied?

The BYOK key and access token are secrets. In anything user-facing or multi-user,
they must never reach the browser. The SDK supports both placements
([`client.py`](../../src/fuaran_py/client/client.py) docstring):

- **Direct** — a backend script/notebook passes `access_token` + `provider_key`
  at construction. Fine when the calling environment is already trusted.
- **Server-proxied** (this sample) — the browser talks to *your* server; the
  server holds the secrets and calls the endpoint. The key never leaves the
  server process, and this page carries no credential field at all.

## Run it

```bash
pip install -r requirements.txt          # fastapi + uvicorn + fuaran-py
export FUARAN_ENDPOINT=https://<your-endpoint>/generate
export FUARAN_ACCESS_TOKEN=...            # server-side only
export FUARAN_PROVIDER_KEY=...            # the BYOK key, server-side only
uvicorn app:app --port 14140
```

Open <http://127.0.0.1:14140/>, type a prompt (e.g. *a metric card showing
revenue*), and the server-rendered tree appears. A second prompt (*rename the
metric to ARR*) repairs the held tree.

> On Windows PowerShell use `$env:FUARAN_ENDPOINT = "..."` instead of `export`.
> `14140` is a free slot in the Fuaran-UI 14000-band; any free port works.

## The proxy hop (the whole point)

```
browser ──POST /generate {prompt, currentTreeJson}──▶ FastAPI (this host)
                                                         │  holds token + BYOK key
                                                         ▼
                                        FuaranClient.generate(...) ──▶ Fuaran endpoint
                                                         │
   {status, tree, html, version}  ◀─────────────────────┘   (no credential in the reply)
```

The response body is `{status, tree, html, version}` on success — never the token
or the key. `app.py`'s `/generate` handler maps the SDK's three-way `TurnResult`
(`Produced` → 200, `AccessDenied` → 401, `TurnFailed` → 422).

## Equivalent Django view

No separate app is required — the same server-proxied pattern is a single Django
view. Hold the secrets in settings/environment, call the SDK, return the tree +
rendered markup as JSON:

```python
# views.py
import json, os
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt   # or use Django's CSRF token client-side
from fuaran_py.client import FuaranClient, Produced, AccessDenied, TurnFailed
from fuaran_py.renderer import render_html

_client = FuaranClient(
    os.environ["FUARAN_ENDPOINT"],
    access_token=os.environ.get("FUARAN_ACCESS_TOKEN"),   # server-side only
    provider_key=os.environ.get("FUARAN_PROVIDER_KEY"),   # the BYOK key, server-side only
)

@csrf_exempt
def generate(request):
    body = json.loads(request.body or "{}")
    result = _client.generate(body.get("prompt", ""), current_tree_json=body.get("current_tree_json"))
    if isinstance(result, Produced):
        decoded = result.decode_tree()
        html = render_html(decoded.value) if decoded.ok else ""
        return JsonResponse({"status": "produced", "tree": result.tree_json, "html": html, "version": result.version})
    if isinstance(result, AccessDenied):
        return JsonResponse({"status": "access_denied", "reason": result.reason}, status=401)
    err = result.error
    return JsonResponse(
        {"status": "turn_failed", "error": {"stage": err.stage, "code": err.code, "message": err.message}},
        status=422,
    )
```

```python
# urls.py
from django.urls import path
from . import views
urlpatterns = [path("generate", views.generate)]
```

The credential placement is identical: secrets in `settings`/environment, injected
server-side, never serialized into the response. The client-side turn loop (hold
the tree, post it back as `current_tree_json`) is the same as in `app.py`.

## Smoke test

[`../../tests/test_fastapi_host.py`](../../tests/test_fastapi_host.py) drives the
host with FastAPI's `TestClient` and a **mock transport** (no real endpoint):
it completes a turn, carries the tree across a second (repair) turn, maps
access-denied → 401 and turn-failed → 422, and asserts the server-held access
token + BYOK key never appear in any response body. It skips when `fastapi` is
not installed (`fuaran-py` itself stays dependency-light).
