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

Requires CPython **3.12+**. The runtime codec has **no third-party dependencies** —
it uses only the standard library.

## Use

```python
from fuaran_py import decode_node, encode_node, decode_op, encode_op

result = decode_node('{"id":"a","kind":{"$type":"Markdown","text":{"$type":"Literal","text":"hi"}}}')
if result.ok:
    canonical = encode_node(result.value)  # byte-identical canonical wire form
else:
    print(result.error.code, result.error.path)  # structured, recoverable
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
wire = encode(tree)  # canonical JSON
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
    body = render_html(result.value)  # body-fragment HTML string
    stylesheet = reference_css_path().read_text()  # the canonical reference CSS
```

The renderer is stdlib-only and inert by design: `Action`-bearing nodes render
dead until a client hydrates them, a `Link` is a real crawlable `<a href>`, and
every string-to-DOM seam (URLs, markdown, attributes) is sanitised. The host owns
the document shell (`<html>` / `<head>` / the `<link>` to the stylesheet); the
renderer emits the body fragment only.

### Destination policy — ambient, and default-deny

The scheme floor answers *is this URL safe to have*. It does not answer *is this
destination one the composition declared*, and only the second question closes
exfiltration: `https://collector.example/?s=…` passes every scheme rule, and in
an `<img src>` the browser contacts it with **no user act at all**, because
rendering *is* the request.

So every `href` / `src` the renderer emits — `Link`, `Image` (its `src`, and
*each* `srcSet` candidate), `Media` (its `src` and its poster frame), and every
destination inside a `Markdown` body — is checked against an **egress policy**
carried on the render context. Every one of those is a URL fetched with no user
act, so they take one rule; what differs is only what a **refusal** means. A slot
the element cannot do without collapses to the refusal URL and carries its
marker; a slot it can — a `srcSet` candidate, a poster frame, an expansion anchor
— is **dropped instead**, because offering a rendition or an affordance that
cannot work is worse than offering one fewer. It **defaults to deny-non-local**: a decoded tree
cannot declare its own egress, so absent a host's declaration it gets none. There
is no caller opt-in anywhere on the path; the guarantee does not depend on a call
site having remembered to ask.

A host that means to reach off-origin declares it, by name:

```python
from fuaran_py.renderer import (
    DENY_NON_LOCAL_EGRESS,
    EgressClass,
    HostSuffix,
    allow_origin,
    render_html,
)

policy = allow_origin(HostSuffix("cdn.example"), [EgressClass.MEDIA], DENY_NON_LOCAL_EGRESS)
body = render_html(result.value, egress_policy=policy)
```

`PERMISSIVE_EGRESS` — every destination, for a hand-authored tree where the
author is the trust boundary — is reached by that name and no other, so a grep
finds every host that widened it. The same keyword rides
`FuaranRuntime(..., egress_policy=…)`, because a client re-render re-issues every
`<img src>` fetch and a policy holding only on the server half would leak on the
first dispatch.

Two consequences on adoption, both deliberate:

* A `mailto:` / `tel:` href is **refused** under the default. Those are egress
  channels with no host for a rule to name, so they can only be permitted
  wholesale — and permitting them by omission is the failure the default exists
  to prevent.
* Same-origin destinations (a relative path, a fragment) are **allowed**, so
  ordinary in-app links and assets render unchanged. The default denies leaving,
  not linking.

A refused destination renders as a *refusal* — `href`/`src` becomes the inert
`about:blank#fuaran-egress-refused` and the element carries a trailing
`data-fuaran-egress-refused` attribute naming the class and the host
(`media:collector.example`) — never as a silent neuter: "nothing happened" and
"this was refused" are different facts, and only one of them is debuggable. The
marker value **never carries the path or query**, which is exactly where an
exfiltrated payload sits.

#### Where this host's shape differs — declared, not incidental

The policy model, the verdicts, the refusal URL and the marker spelling are
identical to the reference host's, and the shared markdown corpus pins them
byte-for-byte. Four things about how this host *carries* the policy differ, and
each is a decision rather than an omission:

* **The policy is a keyword argument on the existing entry point**, not a second
  entry point beside it. The reference tier mints a separate
  `render…AndEgress` function because its context record has five other optional
  fields and a parameter per permutation is combinatorial; this host's entry
  point takes two arguments, so the parameter *is* the declaration and stays
  greppable at the call site. `Renderer` already existed as the per-render
  context, so no new object was introduced to hold the field.
* **The `unsafeUrl` verdict now renders the marked refusal at the `Link` /
  `Image` call sites**, where before this host emitted a bare `about:blank`. The
  floor refuses the URL at exactly the same point; what changed is that the seam
  those call sites go through renders *every* refusal visibly, with the marker
  value `unsafe-url` distinguishing a floor refusal from a policy one. Any local
  test that pinned the bare form was updated in the same change.
* **The markdown seam deliberately keeps the bare `about:blank` for that same
  verdict**, with no marker. Those bytes are pinned by the shared corpus and have
  read that way in every conformant host since the markdown renderer shipped;
  re-spelling them would churn a conformance corpus inside a change about egress,
  which is where a genuine divergence hides. The reference host draws the line in
  the same place.
* **Two of the reference host's call sites have no counterpart here**, because
  the emissions do not exist in this host: a `DataGrid` link column (this host's
  grid renders each cell's *text* projection, inert server semantics, so it emits
  no per-row anchor) and the `route` class (this renderer emits no navigation —
  `Action`-bearing nodes are dead until a client hydrates them). Both are
  absences of a sink, not unchecked sinks; if either emission ever lands here it
  arrives already owing a policy consultation.

### Bound-grid rendering — the completeness posture

A `DataGrid` bound to data renders its **rows**, server-side. The `source` is
resolved through the same render-time compute path every other bound slot uses
(a `Transform` pipeline is evaluated by the certified evaluator; a `Selection` /
`Filter` / `State` default resolves), and the resolved rows are emitted as the
reference grid's own `<table class="fuaran-grid">` markup — the same element
shape and class vocabulary a client renders, so a page that is later hydrated
attaches to markup it already agrees with rather than replacing a placeholder.

The posture is *completeness*: a static host that holds the rows and prints a
row count withholds what it already has, and a no-JS surface — an email digest,
an ops report, a crawler — can never recover it.

One boundary remains, and it is declared rather than incidental. A column
projects its cell either **declaratively**, by `field` (a row property name that
rides the wire), or through a **host closure** (`value`) — and a closure does not
survive serialisation; it decodes as an opaque sentinel. So:

| Bound grid | Rendered |
|---|---|
| at least one `field`-projected column, source resolves to rows | the rows, as a `fuaran-grid` table (closure-projected cells empty) |
| no `field`-projected column (including no columns at all) | the `[Grid: N rows — hydrates client-side]` placeholder, with `N` the *resolved* row count |
| source does not resolve to rows | the same placeholder |

Rich cell kinds (`TonedPill`, `Checkbox`, `Link`, `Progress`, …) render their
**text** projection — the renderer's inert server semantics for every
interactive node, not a special case for grids.

### Form-field rendering — what a declared rule reaches

A `FormField.rule` declares the **accepted set** (`FormFieldKind` names the
control). A static emitter's job is to project that into the *platform's own*
constraint vocabulary, so the platform — here, the browser receiving this HTML —
is what enforces it rather than a script that may never load:

| Rule slot | Rendered as |
|---|---|
| `format` (`email` / `url` / `tel`) | the input's `type`, so the browser enforces the shorthand |
| `pattern` | the HTML `pattern` attribute (ECMA-262 source, anchored to the whole value) |
| `minLength` / `maxLength` | `minlength` / `maxlength` |
| `compare` | **declared, not enforced** — `data-fuaran-field-compare="<op>:<key>"` |
| `message` | *not rendered* — see the note below |

Three boundaries, each declared rather than incidental.

**`compare` has no HTML equivalent.** It is emitted as a declaration matching the
reference renderers' marker so a reader can see the constraint was carried and
not dropped, and it is explicitly **not claimed as coverage**: nothing in the
platform reads that attribute, and this emitter produces inert markup with no
gate of its own. A cross-field comparison is enforced by a rendering host's
submit gate and, non-bypassably, by a server-side re-check.

**`message` is not rendered**, for the same reason it is not rendered by the
reference server host: the unmet message needs an element for the field to be
described *by*, and minting that markup means minting class vocabulary that is
parity-locked across every renderer and both stylesheet copies. That is a
renderer change with its own cross-host change-set.

**`pattern` is omitted on a `TextArea`**, which has no such attribute in HTML.
Emitting one would look like coverage and be inert.

One narrowing is this host's own and worth stating: the reference host always
emits a control `type`, and this baseline never has, so `type` appears here only
where a `format` rule declares it. Every form rendered before the rule slot
existed is therefore byte-unchanged. The wider gap — this baseline projects one
generic `<input>` per field rather than a per-control element — is the baseline's
and not the rule slot's.

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

counter_runtime().mount("fuaran-root")  # clicking "+1" re-renders the count
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
assert verify_chain(records) is None  # a clean, untampered chain
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
    access_token=os.environ["FUARAN_ACCESS_TOKEN"],  # the paid credential
    provider_key=os.environ["PROVIDER_API_KEY"],  # your BYOK LLM key
)
session = FuaranSession(client)
result = session.next("a metric card showing revenue")  # fresh generation
if isinstance(result, Produced):
    tree = result.decode_tree()  # typed Node via the wire codec
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

## Retired wire vocabulary — the positional slot on `InsertChild` / `MoveNode`

`InsertChild` and `MoveNode` both **append**; `ReorderChildren` states order by naming
child ids. The integer `position` / `newPosition` these two ops once carried was removed
from the wire format, and this host **REFUSES** it: `WRONG_TYPE` at `$.position` /
`$.newPosition`, with a message naming `ReorderChildren`. Placing a node anywhere but
last is `Batch [InsertChild …, ReorderChildren …]`.

There was a migration window during which every host accepted and ignored the field so
the hosts could adopt independently. It is **closed**. How it closed is worth knowing,
because it is not the obvious thing: the op decoder walks each op's schema and never
looks at anything else, so *not reading* the ordinal **was** the tolerance — there was
never a read to delete. Closing the window therefore meant ADDING a refusal, not removing
an acceptance; a host that merely stopped mentioning the field would have gone on
accepting it forever, indistinguishable from one that had never adopted.

The refusal is **by name** and is the enumerated-near-miss narrowing of WIRE_FORMAT §2
rule 2: a genuinely unknown key is still tolerated, because a slot a future profile may
add must stay addable. It is checked **before** the schema loop, so an op carrying both a
retired ordinal and another defect names the ordinal — identically ordered in every host,
so which defect surfaces first is deterministic. Certified by the corpus fixtures
`reject-op-insertchild-retired-position` / `reject-op-movenode-retired-newposition` and
pinned by `tests/test_retired_position.py`.

**The encoder applies no schema filter**, so this refusal is the decode-side guarantee
only: a construction site that leaves a dead key on an op still reaches the wire, where a
conformant decoder — including this one — now refuses it. Whether the encoder should
filter to the schema is an open question, recorded here rather than implied closed.

This host declares no stability policy yet (pre-1.0, `0.0.1`), so the change is recorded
here rather than in a `STABILITY.md` it does not have.

## Conformance

`fuaran-py` round-trips the shared wire-format corpus byte-for-byte and surfaces
the canonical reject code + path for every malformed fixture. Run the smoke
harness:

```bash
pytest
```

A standalone offline corpus snapshot + drift guard, schema validation, a
language-agnostic certification bridge, and CI integration all ship.

### Two generative layers, and neither replaces the other

The curated corpus pins named traps. Beyond it there are two distinct floors,
often conflated:

- **Within-host** (`tests/test_generative_parity.py`) — over ≥1000 `hypothesis`
  generated trees, `encode(decode(encode x)) == encode x`: this host's canonical
  form is a fixed point. It proves `fuaran-py` is *self-consistent*, and it runs
  under a plain `pytest` with no other toolchain.
- **Cross-host** (`fuaran_py.conformance.fuzz_exchange`) — one host's canonical
  bytes are checked by a *different* host's codec, in both directions. That is
  the whole value of it: a shared misreading of the spec is invisible to any
  within-host property and shows up here immediately.

The cross-host exchange needs a sibling host's emitter, so it is driven by hand:

```bash
# emit the other host's canonical samples into <dir>/fsharp/, then:
python -m fuaran_py.conformance.fuzz_exchange <dir>   # decode + re-encode + write <dir>/python/
```

Exit `0` all samples agree, `1` a divergence (named, with the first differing
byte), `2` the input set is missing. The runner itself is pinned by
`tests/test_fuzz_exchange.py`, which drives it over real corpus payloads and
asserts a deliberately corrupted sample is rejected.

## License

Apache-2.0. See [LICENSE](LICENSE).
