# FastAPI host — server-proxied BYOK

A worked FastAPI host for the Fuaran generation endpoint, demonstrating the
**server-proxied bring-your-own-key (BYOK)** pattern: the paid access token and
the BYOK provider key live in **server** config and are injected by this proxy —
the browser posts only a prompt and never sees either secret.

It wraps the [`fuaran_py.client`](../../src/fuaran_py/client/) SDK: the `/generate`
route calls `FuaranClient.generate` server-side, decodes the returned canonical
wire tree, renders it to HTML with `fuaran_py.renderer.render_html`, and returns
the tree + its markup. The turn loop runs client-side — the page holds the current
tree's JSON and posts it back as `current_tree_json`, so each prompt is a cheap
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

## This route is not an open spend endpoint — read this before you copy it

**It holds your BYOK key and answers whoever can reach it.** A proxy route like
this one is, by construction, permission to spend your provider budget: anything
that can POST to it can generate, at whatever rate it likes, for as long as it
likes. Three guards stand between the two, all of them in
[`app.py`](app.py) rather than in this file, because a sample is copied before
it is read:

| Guard | What it bounds | How to change it |
|---|---|---|
| **Authorisation** — fail-closed | who may call at all | `FUARAN_SAMPLE_SECRET` (callers present it as `X-Sample-Secret`), or `FUARAN_SAMPLE_ANONYMOUS=1` to serve everyone. **The app refuses to start with neither.** |
| **Prompt-length cap** — 4000 chars | the input-token bill, which is the one cost dimension a caller controls directly | `Policy(max_prompt_length=…)` |
| **Per-caller rate limit** — 30 turns / minute | how fast a single caller can spend | `Policy(rate_limiter=RateLimiter(…))` |

None of the three is a substitute for a real gateway, and two of them are
deliberately crude. The shared secret is a placeholder with the right SHAPE, so
replacing it with your app's real authentication is a one-line change and
forgetting to replace it is not silent. The rate limiter is per-PROCESS, so two
instances allow twice the traffic — a fact worth knowing rather than a defect
worth hiding; a real deployment wants a shared store. The caller key is the
remote address, which is the weakest useful choice: behind a proxy every caller
shares one, so key on whatever you already authenticate.

**What they buy is that a copy of this file cannot become an unauthenticated,
unbounded spend endpoint by omission — only by deliberate opt-out.** Every
refusal happens BEFORE the endpoint is called, so none of them costs a token.

## Run it

```bash
pip install -r requirements.txt          # fastapi + uvicorn + fuaran-py
export FUARAN_ENDPOINT=https://<your-endpoint>/generate
export FUARAN_ACCESS_TOKEN=...            # server-side only
export FUARAN_PROVIDER_KEY=...            # the BYOK key, server-side only
export FUARAN_SAMPLE_SECRET=...           # who may call this route
#   …or, deliberately: export FUARAN_SAMPLE_ANONYMOUS=1
uvicorn app:app --port 14140
```

Open <http://127.0.0.1:14140/>, put the shared secret in the secret box (leave it
empty on the anonymous arm), type a prompt (e.g. *a metric card showing
revenue*), and the server-rendered tree appears. A second prompt (*rename the
metric to ARR*) repairs the held tree.

> On Windows PowerShell use `$env:FUARAN_ENDPOINT = "..."` instead of `export`.
> `14140` is a free slot in the Fuaran-UI 14000-band; any free port works.

## The proxy hop (the whole point)

```
browser ──POST /generate {prompt, current_tree_json}──▶ FastAPI (this host)
                                                         │  authorise, cap, rate-limit
                                                         │  holds token + BYOK key
                                                         ▼
                                    FuaranClient.generate_detailed(...) ──▶ Fuaran endpoint
                                                         │   (secrets as HEADERS,
                                                         │    never in the body)
   {status, tree, html, version,   ◀─────────────────────┘   (no credential in the reply)
    provider, servedModel}
```

The response body is `{status, tree, html, version, provider, servedModel}` on
success — never the token or the key. `servedModel` is what the provider's own
reply said actually answered; `null` means **unreported**, deliberately not the
model the deployment asked for.

`app.py`'s `/generate` handler maps the SDK's three-way `TurnResult` (`Produced`
→ 200, `AccessDenied` → 401, `TurnFailed` → 422), and adds two refusals of its
own, both in the endpoint's own `{"error": {"code", "message"}}` envelope so the
page decodes one contract either way:

- its **guard** refusals — `ACCESS_DENIED` (401), `BAD_REQUEST` /
  `PROMPT_TOO_LONG` (400), `RATE_LIMITED` (429);
- **`MALFORMED_TREE` (502)** — the endpoint produced a tree this host could not
  decode. That used to be a 200 with `html: ""`, which the page rendered as an
  empty panel and the caller recorded as a successful turn — while still holding
  the undecodable tree, so every later repair built on it.

## Equivalent Django view

No separate app is required — the same server-proxied pattern is a single Django
view. Hold the secrets in settings/environment, call the SDK, return the tree +
rendered markup as JSON:

```python
# views.py
import json, os
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt  # or use Django's CSRF token client-side
from fuaran_py.client import FuaranClient, Produced, AccessDenied, TurnFailed
from fuaran_py.renderer import render_html

_client = FuaranClient(
    os.environ["FUARAN_ENDPOINT"],
    access_token=os.environ.get("FUARAN_ACCESS_TOKEN"),  # server-side only
    provider_key=os.environ.get("FUARAN_PROVIDER_KEY"),  # the BYOK key, server-side only
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
