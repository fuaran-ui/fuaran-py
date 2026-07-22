# fuaran-py

A **headless Python host of the Fuaran UI wire format** — a dependency-light,
idiomatic-Python reference implementation of the canonical-JSON contract a Python
AI orchestrator needs to read and write Fuaran UI trees.

`fuaran-py` is a **sibling reference implementation**, not a transpile of any
other host: it is built to the language-neutral wire-format specification
(`WIRE_FORMAT.md`) and certified against the shared conformance corpus. Conformance
to the spec is the contract; idiomatic Python is the deliverable. The **core is
headless** (codec + validator only); an **optional, dependency-light server-HTML
renderer** ships alongside for hosts that want to render a decoded tree to HTML
without a client runtime.

## What's here

| Module | Role |
|---|---|
| `fuaran_py.ui` | The ergonomic, typed **authoring** surface — smart constructors over a typed per-kind model (`fuaran.metric(...)`, `binding.static(...)`, `format.currency(...)`), plus the **polars-like Compute authoring** API (`frame(...).filter(col("x") > 0).group_by(...).agg(...)`) that emits canonical `Transform` JSON. See [docs/AUTHORING.md](docs/AUTHORING.md) and [examples/quickstart_reactive_data_app.py](examples/quickstart_reactive_data_app.py). |
| `fuaran_py.schema` | The typed tree + `decode_node` / `encode_node` (canonical Node codec); `schema.types` is the typed per-kind authoring model. |
| `fuaran_py.ops` | The `TreeOp` algebra: `decode_op` / `encode_op` + `apply(op, tree)` (the reducer over all 11 ops). |
| `fuaran_py.dataframe` | The Compute-layer columnar strand — the typed `Cell`/`Column`/`Table`/`DataSource` model + the serializable `Transform`/`ColExpr` algebra, a byte-exact canonical codec, and a pure reference evaluator certified byte-identical to the reference over the parity fixtures. |
| `fuaran_py.validator` | A pre-emit, default-deny-by-shape structural validator. |
| `fuaran_py.op_stream` | The hash-chained provenance log — the `StreamEntry` envelope, a host-side SHA-256 chain, an in-memory sink, and replay. Reproduces the committed cross-host chain hashes byte-for-byte. |
| `fuaran_py.canonical` | The canonical-JSON encoder (key sort, number form, escaping). |
| `fuaran_py.conformance` | A corpus round-trip smoke harness. |
| `fuaran_py.renderer` | Optional server-HTML renderer (`render_html`) + the byte-copied reference stylesheet. |
| `fuaran_py.runtime` | Interactive Pyodide client runtime — the in-browser mount + dispatch→apply→re-render loop, behind an injectable `BrowserDeps` seam. |
| `fuaran_py.client` | Typed client over the Fuaran generation endpoint — `FuaranClient.generate` + the `FuaranSession` turn loop (holds the tree → repair diffs). See [Generate](#generate-client-for-the-hosted-endpoint-optional) below and [examples/quickstart_client.py](examples/quickstart_client.py). |

## Install

```bash
pip install -e ".[dev]"   # editable + dev tooling (pytest / mypy / ruff)
```

Requires CPython **3.13+**. The runtime codec has **no third-party dependencies** —
it uses only the standard library.

## Use

```python
from fuaran_py import decode_node, encode_node, decode_op, encode_op

result = decode_node('{"id":"a","kind":{"$type":"Markdown","text":{"$type":"Literal","text":"hi"}}}')
if result.ok:
    canonical = encode_node(result.value)   # byte-identical canonical wire form
else:
    print(result.error.code, result.error.path)   # structured, recoverable
```

Decoding never throws on malformed input — it returns `Ok(value)` or
`Err(DecodeError)` carrying one of the six canonical codes (`INVALID_JSON`,
`MISSING_FIELD`, `WRONG_TYPE`, `UNKNOWN_DU_CASE`, `WRONG_NODE_KIND`,
`EMPTY_NODE_ID`) and a `$`-rooted path.

## Author (ergonomic, typed)

`fuaran_py.ui` is the Python analogue of `@fuaran-ui/ui` / `Fuaran.UI` — smart
constructors over a typed per-kind model, with per-kind defaults + ARIA injection.
A human developer authors a tree the same way an F#/TS developer does; `encode`
serialises it byte-identically to the corpus.

```python
from fuaran_py.ui import fuaran, format, encode

tree = fuaran.dashboard(
    "root",
    children=[
        fuaran.metric("rev", label="Revenue", value=1234.5, format=format.currency("GBP")),
        fuaran.markdown("note", "Updated hourly."),
    ],
)
wire = encode(tree)   # canonical JSON
```

This is the **human** authoring surface; the AI's emission surface is the wire
format itself, for every host. Full guide: [docs/AUTHORING.md](docs/AUTHORING.md).

## Render (optional)

A decoded tree renders to a sanitised HTML **body fragment** from Python — no
client runtime — emitting the reference `fuaran-*` class vocabulary so the output
is styled by the byte-copied reference stylesheet exactly as every other Fuaran
host styles it. This is what makes a Python web host (e.g. FastAPI) render Fuaran
chrome end-to-end.

```python
from fuaran_py import decode_node
from fuaran_py.renderer import render_html, reference_css_path

result = decode_node(wire_json)
if result.ok:
    body = render_html(result.value)            # body-fragment HTML string
    stylesheet = reference_css_path().read_text()  # the canonical reference CSS
```

The renderer is stdlib-only and inert by design: `Action`-bearing nodes render
dead until a client hydrates them, a `Link` is a real crawlable `<a href>`, and
every string-to-DOM seam (URLs, markdown, attributes) is sanitised. The host owns
the document shell (`<html>` / `<head>` / the `<link>` to the stylesheet); the
renderer emits the body fragment only.

### Chart lowering coverage

`fuaran_py.charts` lowers a resolved `Chart` to a canonical `Drawing` subtree
(first-party inline SVG, headless included), byte-identical to the shared
`chart-lowering/*` goldens the reference implementation generates. Lowered
arms: **Bar** (grouped + stacked), **Line**, **Area** (overlaid + stacked
bands), **Scatter** (linear numeric x-scale, point marks), **Pie** (polar,
cubic-approximated wedges; single-series). `Heatmap` renders the
client-hydration placeholder. Data-bearing shapes carry a derivation-based
`markId` (`series|category`, stable under row reorder) emitted as
`data-fuaran-mark` for mark addressability; chrome stays unstamped. The pytest
suite certifies **every** golden pair byte-for-byte, including canonical-float
formatting of pie arc control points and stacked cumulative sums.

## Run (interactive, optional)

Under **Pyodide** (CPython-on-WASM), `fuaran_py.runtime` adds the live loop the F#
(Fable) and TypeScript (React) hosts provide: mount a decoded tree, wire DOM events
to a host update function, fold the returned `TreeOp`s through `apply`, and
re-render — reusing the renderer (markup + class vocabulary) and the apply engine
(op semantics), never a parallel copy.

```python
from fuaran_py.runtime import counter_runtime

counter_runtime().mount("fuaran-root")   # clicking "+1" re-renders the count
```

Browser-API access is behind an injectable `BrowserDeps` seam (default: the Pyodide
`js` interop module), so the package stays stdlib-only and importable under plain
CPython; tests drive the loop against a fake DOM.

## Op-stream (hash-chained provenance, optional)

A stream's applied `TreeOp` edits form an append-only, **hash-chained** sequence of
`OpRecord` envelopes: each record folds its op, timestamp, author, prompt
correlation, and apply outcome into a versioned `StreamEntry` envelope, and a
host-side SHA-256 chain (`sha256(previousHash | payload)`) links records so the
stream is tamper-evident and its authorship answerable from the record sequence
alone. `apply_and_persist` is the write path (apply once, then persist a chained
record on success); `replay_stream` folds a stream back into a tree; `verify_chain`
proves integrity.

```python
from fuaran_py import decode_node
from fuaran_py.model import Obj
from fuaran_py.op_stream import InMemorySink, PersistContext, apply_and_persist, verify_chain

sink = InMemorySink()
ctx = PersistContext(stream_id="doc-1", user_id="alice")
tree = decode_node(wire_json).value

result = apply_and_persist(sink, ctx, Obj("RemoveNode", {"target": "leaf"}), tree)
records = sink.replay("doc-1", 1, sink.latest_sequence("doc-1"))
assert verify_chain(records) is None          # a clean, untampered chain
```

The chain is **byte-stable across hosts**: the pre-image envelope leads with
`{"v":2,…}` (the chain format version, folded in first so the format is
self-describing) and this host reproduces the committed golden hashes in the shared
`chain/` conformance corpus exactly — the same golden the F# and TypeScript hosts
certify against. The module is stdlib-only (`hashlib.sha256`); a genuinely
I/O-backed sink is a follow-up implementing the same `OpStreamSink` protocol.

## Generate (client for the hosted endpoint, optional)

The **Fuaran generation endpoint** is a paid, stateless, bring-your-own-key
(BYOK) HTTPS surface: it takes a prompt (+ an optional current tree) and returns
a new canonical wire-format tree. `fuaran_py.client` is a thin, typed,
stdlib-only layer over it that collapses the integration to **call, hold the
tree, repair**:

```python
import os
from fuaran_py.client import FuaranClient, FuaranSession, Produced

client = FuaranClient(
    "https://<your-endpoint>/generate",
    access_token=os.environ["FUARAN_ACCESS_TOKEN"],   # the paid credential
    provider_key=os.environ["PROVIDER_API_KEY"],      # your BYOK LLM key
)
session = FuaranSession(client)
result = session.next("a metric card showing revenue")   # fresh generation
if isinstance(result, Produced):
    tree = result.decode_tree()                # typed Node via the wire codec
result = session.next("rename the metric to ARR")  # a cheap repair diff
```

Every call returns a typed three-way result — `Produced` (the new tree JSON +
the ops applied + the surface-version echo), `AccessDenied` (the token was
rejected at the edge, before your BYOK key was touched), or `TurnFailed` (a
recoverable stage-tagged envelope; for the `apply` stage its message carries the
hint the next prompt can re-emit against). The client never raises for an
endpoint-level outcome; transport errors surface as a `TurnFailed` with a
`NETWORK` code. `Produced.decode_tree()` / `AppliedOp.decode()` hand back typed
values through the same codec the corpus certifies — you never parse raw model
output by hand.

The session holds the current tree between turns, so each subsequent prompt is
a **repair** against it (a cheap diff) rather than a from-scratch regeneration
— the token-saving ergonomic the loop is built around. `session.reset()`
forgets the tree; `FuaranSession(client, initial_tree_json=...)` seeds it so
the first turn is already a repair.

### BYOK key and access token — where each credential lives

Two credentials cross the wire, and they are not the same kind of secret:

- the **access token** — the paid credential for the endpoint. Sent in the
  request body and (by default) as an `Authorization: Bearer` header.
- the **BYOK provider key** — your own LLM-provider API key. Sent in the
  request body only, **never in a header**; the endpoint uses it in memory for
  the one call and never stores, logs, or meters it. The client mirrors that
  posture: the key appears in no header, no error envelope, and no `repr` —
  a logged client object cannot leak it.

Pick the placement by who can see the calling environment:

- **Direct** (a server-side script, a notebook, a backend service you control):
  pass both credentials to `FuaranClient(...)`, sourced from environment
  variables or a secret store. Never commit either; never bundle the BYOK key
  into anything you ship.
- **Server-proxied** (anything user-facing or multi-user — a web app, a
  Pyodide/browser host, a shared tool): point `endpoint` at **your own proxy
  path** and pass **no credentials** client-side. Your proxy injects both
  server-side (`wire.to_wire_body` / `wire.parse_turn_response` are exported
  for exactly this), so the BYOK key never reaches the calling environment.

The contract this client is built against is stamped
`fuaran_py.client.SURFACE_VERSION`; a produced result echoes the live surface's
version, and `is_surface_version_compatible(echoed)` tells you whether the
shape is one this client understands (major-version check).

## The canonical number form (the make-or-break)

The encoder reproduces the canonical float layout directly — it does **not**
delegate number or key formatting to `json.dumps`, whose output would not match.
CPython's shortest `repr(float)` yields the same significant digits as the other
hosts; `fuaran_py.canonical.format_finite_double` re-lays-out those digits into the
canonical fixed-point/scientific form (the cross-host divergence zone — large
exponents, sign padding, `-0` collapse — is pinned by the corpus float fixtures).

## Conformance

`fuaran-py` round-trips the shared wire-format corpus byte-for-byte and surfaces
the canonical reject code + path for every malformed fixture. Run the smoke
harness:

```bash
pytest
```

A standalone offline corpus snapshot + drift guard, schema validation, a
language-agnostic certification bridge, CI integration, and generative parity are
follow-up work.

## License

Apache-2.0. See [LICENSE](LICENSE).
