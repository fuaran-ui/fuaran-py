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
| `fuaran_py.ui` | The ergonomic, typed **authoring** surface — smart constructors over a typed per-kind model (`fuaran.metric(...)`, `binding.static(...)`, `format.currency(...)`), plus the **polars-like Compute authoring** API (`frame(...).filter(col("x") > 0).group_by(...).agg(...)`) that emits canonical `Transform` JSON. Its terse sibling `fuaran_py.ui.quick` is the notebook shape — title-first, records-in, ids derived. See [docs/AUTHORING.md](docs/AUTHORING.md), [examples/quickstart_reactive_data_app.py](examples/quickstart_reactive_data_app.py) and [examples/quickstart_terse_dashboard.py](examples/quickstart_terse_dashboard.py). |
| `fuaran_py.schema` | The typed tree + `decode_node` / `encode_node` (canonical Node codec); `schema.types` is the typed per-kind authoring model. |
| `fuaran_py.ops` | The `TreeOp` algebra: `decode_op` / `encode_op` + `apply(op, tree)` (the reducer over all 11 ops), plus the [placement helpers](#placement-helpers--fuaran_pyopsplacement) — placed insert / move / nudge and the clone verbs, which emit only those 11 ops. |
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

## Start here

[`examples/getting_started.py`](examples/getting_started.py) is a six-lesson tour of
what this language is for, and it runs:

```bash
python examples/getting_started.py            # the whole tour
python examples/getting_started.py replay     # just one lesson
```

| | Lesson | What it shows |
|---|---|---|
| 1 | `authoring` | A user interface is a **value** — build it, encode it, render it to HTML. |
| 2 | `ops` | **Edit the tree, don't regenerate it.** A typed, addressed edit that fails by name. |
| 3 | `replay` | A **hash-chained** session replays exactly, time-travels, and detects tampering — with the same hashes the other hosts compute. |
| 4 | `safety` | **Default-deny by shape.** Malformed emissions are refused because there is no code case to strip. |
| 5 | `operations` | **Declared operations** dispatch by structural search, with no model and no network. |
| 6 | `ai` | Bring your own key: prompt → wire JSON → strict decode → render. |

Five of the six need no key, no network and no browser. Only `ai` calls a provider,
and only when you set `ANTHROPIC_API_KEY` (or pass `--key`).

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
`Err(DecodeError)` carrying one of the canonical codes (`INVALID_JSON`,
`MISSING_FIELD`, `WRONG_TYPE`, `UNKNOWN_DU_CASE`, `WRONG_NODE_KIND`,
`EMPTY_NODE_ID`, `LIMIT_EXCEEDED`) and a `$`-rooted path.

That claim covers **every** reader here that takes wire text, not only
`decode_node` and `decode_op`: the versioned envelope, the DAG record, the
elicitation documents, the teleport bundle, the dataframe source and pipeline,
the theme manifest and the client's reply parser all share one guarded parse. So
each is total on hostile input, and each answers the decode-determinism rules the
same way — a repeated object member, an unpaired surrogate, a bare `NaN`, a
number outside the RFC 8259 grammar and content after the root value are refused
at all of them, and the resource limits are enforced before the document is
built rather than measured after.

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

### …and terser, from a notebook

`fuaran_py.ui.quick` is a thin layer over those constructors for the case where the
data arrives as records and the ids do not matter to you: **title-first, records-in,
ids derived**.

```python
from fuaran_py.ui import quick

app = quick.dashboard(
    "Regional revenue",
    quick.metric_strip(totals),  # {label: value}, pairs, or records
    quick.chart(rows, x="region", y="revenue", kind="Bar"),
    quick.grid(rows),  # df.to_dict("records")
)
```

Ids are derived from each node's kind and label and hashed, so re-running the same
cell produces the same ids — and `fuaran_py.ops.diff` between two runs is then a
short, typed op script a host can *apply* to the rendered page rather than a
rebuild. See [docs/AUTHORING.md](docs/AUTHORING.md#the-terse-layer-fuaran_pyuiquick--title-first-records-in-ids-derived)
and [examples/quickstart_terse_dashboard.py](examples/quickstart_terse_dashboard.py).

### …and it can answer back

`fuaran_py.ui.control` declares a state slot; a pipeline reads that slot through
`param(name)`; and a host re-derives the rows when the slot changes — a `Transform` and
its parameters are ordinary wire data, so this happens wherever the tree is rendered and
needs no Python there.

```python
from fuaran_py.ui import col, control, frame, param

region = control.select("region", options=col("region").unique(), source=frame(rows))
fr = frame(rows).filter(col("region").eq(param("region"))).bind(region)
```

`select` / `multi_select` / `range` / `date_range`; the slot is seeded with the declared
default (WIRE_FORMAT §24.4), an unseeded one is an absent constraint rather than a zero,
and a parameter no control fills is refused when the binding is lowered rather than
silently dropped in a browser. See
[docs/AUTHORING.md](docs/AUTHORING.md#parameter-bound-controls-fuaran_pyuicontrol).

### …and every handler and value is optional

A control's **handler** and its **value** are each optional on the wire, and the two
absences say different things a host acts on. Every constructor in `fuaran_py.ui` can
now reach both, where several used to hard-code one:

```python
from fuaran_py.ui import binding, fuaran
from fuaran_py.schema import types as t

# No handler: the renderer's write-back default arms, so the control writes its own slot.
t.TextField(binding.state("profileName", ""), on_change=False)

# No value either: `{"$type":"Text"}` — the canonical MINIMAL control, and a BOUND one.
# A decoder synthesises the context's auto-binding (`Filter(name)` on a filter chip,
# `State(field id, <typed placeholder>)` on a form field).
t.TextField(on_change=False)

fuaran.tabs("t", on_select=False)  # the index channel writes back
fuaran.modal("m", dismissable=True, on_dismiss=None)  # a decoded modal closes itself
```

The handler flag defaults to **present** on every control that emitted one before this
change, so no tree authored against the earlier surface moves a byte; reaching the
shorter document is an explicit `False` (or, for `Modal.on_dismiss`, an explicit `None`,
which is why the omitted argument still yields the no-op `Chain`). `Tabs` gained
`on_select_tag` and `Select` gained `on_change_multi` — a second channel each, arming
independently of the first — and the `Range` pair-valued control record joined the
`FormFieldKind` roster it had been missing from.

This host declares no stability policy yet (pre-1.0), so the change is recorded here
rather than in a `STABILITY.md` it does not have. It rides the 0.2.0 slot: it is
additive — new keyword arguments, and every prior call encodes identically.

### …and the records are no longer narrower than the wire

Several records reached fewer slots than the wire declares, so a document every other
host can read had no spelling here at all. They now carry the whole set:

```python
from fuaran_py.schema import types as t
from fuaran_py.ui import fuaran, node

# A grid's DECLARATIVE behaviours — each names a host State key, which is what makes
# the affordance survive the wire where a closure cannot.
fuaran.grid(
    "ledger",
    source=t.State("ledger", rows),
    columns=[t.Column(label="Month", field_name="month"), t.Column(label="Note", field_name="note", sortable=False)],
    row_key_field="month",
    sort_state_key="ledger-sort",
    default_sort=t.DefaultSort(1, "desc"),
    page_size=20,
    page_state_key="ledger-page",
    edit_state_key="ledger-edits",
    reorderable=True,
)

# A chart's eight: the value-axis format, the two axis names and the subtitle, the
# legend edge, data labels, what the x column MEANS, and the §4l annotations.
fuaran.chart(
    "revenue",
    source=t.Static(rows),
    x_field="quarter",
    y_fields=["revenue"],
    kind="Bar",
    subtitle="Millions",
    x_title="Quarter",
    y_title="Revenue",
    value_format=t.FmtCurrency("GBP"),
    legend_position="Bottom",
    data_labels="Ends",
    x_scale="Category",
    annotations=[
        t.ReferenceLine(140, "Target"),
        t.EventMarker(t.AnnotationCategory("Q3"), "Repricing"),
        t.RangeBand(t.ValueRange(0, 100), "Tolerance"),
    ],
)

node.with_tooltip("Takes about a minute.", fuaran.button("b", label="Rebuild"))
fuaran.link("m", href="mailto:a@example.com", label="Email us", protection="email")
fuaran.table("t", headers=[...], rows=[...], sortable=True, default_sort=t.DefaultSort(1, "desc"))
```

Three details are decisions rather than mechanics:

1. **The annotation union is CLOSED at three members**, and each carries an ADDRESS and
   a LABEL and nothing else: where in the data a threshold or an episode sits is the
   author's meaning, while the strokes and offsets that draw it are the host's. The
   addresses are declared rather than sniffed, which is what lets the pre-emit validator
   **ground** them — a category key no row carries, a date on a band axis, an
   unparseable date, a non-finite value and a band whose pair runs backwards are each
   refused by name (`FUARAN137`–`FUARAN141`) instead of drawing a picture nobody meant.
2. **`Column.sortable` / `Column.editable` and `Table.sortable` are TRI-STATE.** Absent,
   `true` and `false` are three different documents: a column that explicitly declines a
   sort has said something a column that was never asked has not.
3. **`tooltip` is a NODE trait, not a per-kind keyword** — every kind can be pointed at —
   so it is reached through `node.with_tooltip(...)` beside the other postfix modifiers.
   It is never a substitute for an accessible name: an icon-only control whose only name
   is a tooltip has no name.

Additive on the same terms as the section above: every slot is absent by default, so a
tree authored before any of them existed encodes byte-for-byte as it did. Rides 0.2.0.

### 0.3.0 — `Drawing`, `Fact` and `Mount` become authorable

The codec has decoded all three for a long time; what it did not have was a *spelling*.
`encode` needs a `.to_wire()` root, so a node kind the typed model omitted could not be
written from Python at all — thirteen corpus fixtures were readable and unwritable.

```python
from fuaran_py.schema import types as t
from fuaran_py.ui import fuaran, encode

# Placed geometry: a closed shape vocabulary, no raw SVG. Geometry is STATIC —
# a chart lowering hands over concrete coordinates — and only DrawStyle binds.
fuaran.drawing(
    "chart",
    view_box=t.ViewBox(0, 0, 200, 100),
    title="Quarterly revenue",
    shapes=[
        t.Rectangle(10, 10, 80, 40, corner_radius=4, style=t.DrawStyle(fill=t.Static("#3366cc"))),
        t.Curve((t.MoveTo(t.DrawPoint(0, 0)), t.LineTo(t.DrawPoint(40, 20)), t.Close())),
        t.Label(100, 90, t.LiteralText("Revenue"), style=t.DrawStyle(rotation=-30, text_anchor="Middle")),
    ],
)

# The labelled TEXT statement beside metric()'s number. Its value is a TextSource,
# so it binds to the host's clock or to a grid selection.
fuaran.fact("today", label="Today", value=t.Bound(t.Now("Day")))
fuaran.fact("patient", label="Patient", value="Alice Smith", tone="Brand", emphasis=True)

# The isolation boundary: a guest tree, a channel, and the whole of what it may do.
fuaran.mount(
    "metrics",
    scope_id="guest-metrics",
    channel=t.GuestChannel("TwoWay", "MetricsMsg"),
    capabilities=["notify"],
    inputs={"seed": t.SlotArg(fuaran.markdown("seed", "Initial guest state"))},
)
```

Four details are decisions rather than mechanics:

1. **Geometry accepts the non-finite sentinels.** `NaN` / `Infinity` reach every typed
   float slot and encode as the quoted tokens. Nothing refuses them here: a degenerate
   box is a document a conformant host must be able to *carry* and refuse for itself,
   and a record that raised would make this the one tier unable to read a fixture the
   corpus ships.
2. **An explicit `rotation=0` is a document; an absent one is not.** Only `None` omits
   the key — an upright label the author wrote and a label never asked about are two
   different trees.
3. **A mount's empty capability list is written, never omitted.** Default-deny is the
   posture the boundary exists for, so the empty grant says something; an absent key
   would read as "unspecified" to the host that has to decide.
4. **`Mount.onBubble` is optional on the wire, and this host's decoder had it required** —
   so a mount whose bubbles the host does not take was a document every other host
   accepts and this one refused. Both corpus fixtures carry the sentinel, which is why
   no fixture caught it; the generative floor did, on the first run after the authoring
   surface could spell the absence. Decode-side only: nothing that encoded before
   encodes differently.

Additive: the three kinds are new constructors and new records, and no existing tree
moves a byte. The version advances because the public surface does — 0.2.0 is tagged and
published, so a widened surface rides a new slot rather than being repacked over an old
one.

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

**Data-addressed annotations** are lowered too — `ChartSpec.annotations` carries a
closed union of a horizontal `ReferenceLine` at a value, a vertical `EventMarker`
at an x address (a category key or an ISO-8601 date), and a shaded `RangeBand`
over a pair on either axis. An annotation names a place in the *data's*
coordinates and, optionally, a label; it carries no geometry and no style, so it
survives a data change, a theme flip and a resize. Three rules the lowering
applies, each pinned by the shared goldens: an address **participates in the
domain it addresses** before the axis is nice-d (a target above every bar still
draws, and the axis moves to say so); the **draw order is part of the lowering**
— bands behind everything including the grid, lines and markers in front of the
series, every label last, because in inline SVG z-order *is* emission order; and
a label is **fit-gated and suppressed, never clipped**, with the gate asked only
of the literal arm since the text behind a bound or i18n arm is not known at
lowering time. A suppressed label never suppresses its annotation. `Pie` is
neutralised for all three members — a polar arm has neither axis for an address
to name.

The codec carries the slot **structurally and checked**: a conformant document
round-trips byte-for-byte, while three refusals still bite at the wire boundary —
a non-finite reference-line value or value-band end, an unparseable event date,
and an unordered value or date pair. Each is refused rather than normalised for
one reason: an address participates in the domain it addresses, so a non-finite
one would take every gridline, tick and mark to NaN and a typo'd date would drag
the axis back to the epoch. Two category keys order only through the rows, so
that pair's order is the authoring path's question rather than the wire's.

### Sparkline lowering coverage

A `Sparkline` whose `source` resolves to a series is **drawn**, server-side, as
first-party inline SVG — byte-identical to the shared `sparkline-lowering/*`
goldens the reference implementation generates. Before this it was a placeholder:
this renderer emitted an em-dash and never read the series at all. The geometry
comes from `fuaran_py.charts.try_lower_sparkline`, which produces a canonical
`Drawing` kind, and the markup from the same builder the `Drawing` node uses — so
there is no second sparkline renderer to drift.

This is a deliberate change in what the server emits:

| Resolved `source` | Rendered |
|---|---|
| a non-empty series | `<div class="fuaran-sparkline">` wrapping the lowered `fuaran-drawing` SVG — a 100 × 30 canvas, one `currentColor` polyline at stroke-width 1.5 |
| an empty series | the `fuaran-sparkline fuaran-sparkline-empty` em-dash element, as before |
| a source that does not resolve to a series (an unbound `Query`, a foreign host value) | the same em-dash element |

Three properties are worth stating because each is a contract rather than an
accident, and each is pinned by a golden:

**The geometry is the corpus's.** Over `n` values with `min` / `max`:
`x = i/(n-1)·100` (a lone point centred at 50), `y = 30 − (v−min)/range·28 − 1`,
with `range = max − min` except below a `1e-9` flat guard where it is `1.0`, so a
constant series sits on its own line instead of dividing by zero. Both
coordinates round half-up to 2 dp. There is no title and no description: a
sparkline has no spec to summarise, so it carries no accessible name of its own.

**Non-finite values are not filtered.** The `"NaN"` / `"Infinity"` /
`"-Infinity"` sentinels a series may carry propagate through that arithmetic and
reach the canvas as `0` through the drawing builder's number form — the same
thing every other geometry-bearing kind does with them. That is the input class
where a hand-written copy drifts first, so it has its own golden.

**Nothing to draw is not an empty canvas.** The em-dash fallback is a *host*
element rather than a drawing shape, so the lowering cannot express it and
returns nothing at all; the `empty` golden is the JSON literal `null`, which is
that fact. The renderer's fallback branch is what supplies the element.

The `Sparkline` row of the corpus's `render-fidelity.json` reads `"class":
"none"` accordingly — the parity-checked fallback is the whole render, as it has
been for `Drawing`.

## Project (document + digest, optional)

The same tree, rendered for two targets that run nothing at all. `render_html` above is the
page a browser paints and a client hydrates; these two are the other things a decoded tree
can be, from the same bytes:

```python
from fuaran_py import decode_node
from fuaran_py.renderer import render_markdown, render_email_document

tree = decode_node(wire_json).value

open("report.md", "w", encoding="utf-8", newline="
").write(render_markdown(tree, title="Weekly"))
open("digest.html", "w", encoding="utf-8", newline="
").write(render_email_document(tree, "Weekly"))
```

Both are **pure functions of the tree and its resolved bindings**: same tree, same options,
same sources ⇒ same bytes, on every run and every platform. Both resolve text and figures
through the functions `render_html` uses, so a document, a digest and the page cannot disagree
about what a number is.

### The scope line is the feature

Neither target can execute anything, so each kind needs an answer to a question the browser
renderer never asks: *what does this become when nothing runs?* Each projection declares one,
per canonical wire kind, in a `SCOPE` table alongside the code —
`fuaran_py.renderer.document.SCOPE` and `fuaran_py.renderer.email.SCOPE`, four dispositions
from `fuaran_py.renderer.projection`:

| Disposition | Meaning |
|---|---|
| `rendered` | painted in full by the projection |
| `structural` | a carrier: the node paints nothing beyond layout, its children render |
| `openLive` | a labelled "open live" affordance. Never a half-working control |
| `omitted` | zero-paint in this target, deliberately |

The distinction between the last two carries weight: `openLive` says "this exists and you have
to leave the document to use it", `omitted` says "this carries nothing a static target can
convey". A reader can act on the first.

The tables are **checked, not asserted**. Completeness is measured against the corpus's
`render-fidelity.json` kind list, so a new `NodeKind` cannot arrive with no declared posture;
and every kind that artefact marks `behavioural` — "inert server-side, gains its behaviour at
hydration" — must be `openLive` in both, derived from the manifest rather than restated. A new
interactive kind therefore reddens the suite instead of shipping a dead button to an inbox.

### The digest (`render_email`, `render_email_document`)

HTML email is the most hostile render target in computing: no JavaScript, no external
stylesheet, no flexbox or grid worth relying on, and a rendering engine per client (Outlook
desktop still lays out through Word). The projection is bounded hard to the Display subset,
lays out entirely in presentation tables with inline styles, and never emits a control.

`EmailOptions` is deliberately small — a live URL, a column width (600px, what the Outlook
reading pane fits), a webfont-free font stack, and the destination policy. An email projection
with a theme engine is a CSS framework, and the client fragmentation this exists to survive is
what defeats one.

`fuaran_py.renderer.lint(html)` is the falsifiable half of "email-safe". The client-matrix
question cannot be answered offline and it does not pretend to: it scans for constructs the
matrix is *known* to break on — flexbox, grid, positioning, `<style>`, `<script>`, controls,
`<svg>`, `<iframe>`, and an apostrophe entity inside a style attribute. A clean lint is not a
certificate; a dirty one is proof of the opposite, and that asymmetry is worth automating.

### The document (`render_markdown`)

Markdown is what a tree becomes when it has to be read, diffed, committed, pasted into an issue
or indexed by something that will never run JavaScript. The output stays inside §14's own IN
bucket — CommonMark core plus GFM tables — so `fuaran_py.renderer.markdown.to_html`, the
renderer this host already certifies against the shared corpus, is a valid reader of it.

`MarkdownOptions.charts` is the one place that loop does not close, and it is a choice rather
than a default:

| `charts` | What a chart becomes | Right when |
|---|---|---|
| `"svg"` (default) | the picture, lowered through this host's own `Chart` → `Drawing` lowering and inlined as raw HTML | the reader passes HTML through — a docs site, a browser preview |
| `"table"` | the chart's resolved rows as a GFM table under a caption | the reader is §14, a plain-text reader, or a diff |

§14 escapes raw HTML by construction, so an `<svg>` prints as visible angle brackets through
it. Neither mode is the safe one and neither is a fallback: they are two honest readings of a
picture, and the caller knows which reader is downstream. Nothing is lost in `"table"` mode
that the tree did not already carry as data — which is the argument for lowering charts from
data in the first place.

### Destination policy applies to both

Every `href` and image `src` either projection emits is checked against the same ambient
policy the page uses, defaulting to deny-non-local. In a digest this matters more than on a
page the reader chose to load: an undeclared image `src` **is** the tracking pixel, fetched on
open, reporting that this named person read this message. A refused destination becomes the
inert `about:blank#fuaran-egress-refused`; neither projection emits the `data-*` marker the
page carries, because `data-*` attributes do not survive the sanitisers most mail clients run
and markdown has no spelling for one at all. The refusal itself is not dropped — the
destination is still inert, which is the half that stops it being reached.

### What these are NOT

Neither is a second conformant rendering surface, and the digest is **not byte-parity with the
reference host's** own email projection: that implementation is pinned by a golden corpus
living inside its own test project rather than in the shared conformance corpus, so no
cross-host gate exists for this surface in either direction. What is deliberately shared is the
part worth a corpus — the four dispositions, the scope table row for row with its reasoning,
the derivation of the interactive set from the fidelity manifest, the inline style
vocabulary's values, the option defaults, and the lint's code and token set. Two hosts agreeing
about what an email projection *is* is the property that matters; agreeing about which pixel a
padding lands on is not, and claiming it without a gate would be the worse failure.

The markdown projection has no reference at all: no language tier carries one. What every host
carries is the opposite direction — §14's GFM **→ HTML** renderer for the `Markdown` node's own
body. This one is written to be ported rather than re-derived, and its `SCOPE` table is the
half a second implementation would agree with.

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

### Replay skips recorded refusals

A record's `result_envelope` is `Success` or `Failure`, and a `Failure` record is
the point of the field: it says an op was **refused**, and therefore never touched
the tree. So `apply_to` and `replay_stream` fold only the successes by default, and
a chain carrying a refusal replays cleanly:

```python
from fuaran_py.op_stream import replay_stream

replay_stream(sink, "doc-1", tree)  # accepted records only
replay_stream(sink, "doc-1", tree, include_refused=True)  # the literal fold
```

Folding every record is still reachable, by name. It is what an audit rebuild
wants — where would the refused op have landed? — and what a host whose `Failure`
records are advisory rather than final wants. It is not a sensible default: the
refused op is by construction the one the tree could not take, so the literal fold
fails on the very record that says it failed.

### Compare-and-append — the concurrent-writer write path

`OpStreamSink.append` alone only supports a **proposal**: a caller reads
`latest_sequence` and appends at `+1`, and two callers racing the same stream can
propose the same sequence. One of them wins; historically the loser's `append`
raised and `apply_and_persist` swallowed it by default — a real edit, silently
gone, with the caller told the apply succeeded (which it had — only the durable
record of it was lost).

`InMemorySink` also implements the optional `CasOpStreamSink` extension — a typed
compare-and-append:

```python
from fuaran_py.op_stream import Appended, StaleHead

expected = sink.head("doc-1")  # the chain head, or GENESIS_PREVIOUS_HASH
outcome = sink.append_if(record, expected)  # built against `expected`
match outcome:
    case Appended(receipt):
        ...  # `receipt` names the record now at `receipt.sequence`
    case StaleHead(expected, actual):
        ...  # nothing was persisted; rebuild the record against `actual` and retry
```

`apply_and_persist` uses this automatically whenever `sink` supports it
(`isinstance(sink, CasOpStreamSink)`), retrying against the sink-reported actual
head — bounded, not unbounded spinning — instead of the plain read-then-append. A
sink that does not implement the extension keeps the read-then-append path
unchanged. Either way, a durability failure (a stale race exhausting its retries, a
rejected append, or a detected gap in the stream) now reaches
`PersistContext.on_sink_error`, whose default — `default_sink_error_reporter` — logs
it rather than staying silent; pass `on_sink_error=None` to opt back into silence
deliberately.

This host declares no stability policy yet (pre-1.0), so the change is
recorded here rather than in a `STABILITY.md` it does not have.

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
endpoint-level outcome. `Produced.decode_tree()` / `AppliedOp.decode()` hand
back typed values through the same codec the corpus certifies — you never parse
raw model output by hand.

`generate_detailed(...)` returns the same result plus what the deployment
reported: `ops_applied` (a count — the endpoint returns how much changed, not
the op list), `provider` (which allowlisted provider it chose), `served_model`
(what the provider's own reply said actually answered; `None` means
**unreported**, deliberately not the model the deployment asked for), and the
grounding `snapshot` state.

### Failures the CLIENT reports, as distinct from the endpoint's

`RecoverableError.code` carries the endpoint's own code whenever there is one
(`ACCESS_DENIED`, `APPLY_REJECTED`, `SECRETS_IN_BODY`, `MISSING_PROVIDER_KEY`,
…). Three codes are this client's own, on `ClientCode`:

| Code | Means |
|---|---|
| `NETWORK` | the call did not complete — the transport raised, or `timeout=` elapsed. The message is FIXED: an exception string can quote a URL, a header, or a proxy's internal hostname, and this result is routinely rendered into a page. The detail belongs in your log. |
| `MALFORMED_RESPONSE` | a 200 with no usable tree. Not a success — accepting it would leave the session holding `""` and silently repairing nothing on every later turn. |
| `INSECURE_ENDPOINT` | the endpoint is plaintext `http://` and not loopback, so both credentials would travel in the clear. Refused before the request is built. Loopback and a relative same-origin path are admitted; `allow_insecure_endpoint=True` is the written-down opt-out. |

The session holds the current tree between turns, so each subsequent prompt is
a **repair** against it (a cheap diff) rather than a from-scratch regeneration
— the token-saving ergonomic the loop is built around. `session.reset()`
forgets the tree; `FuaranSession(client, initial_tree_json=...)` seeds it so
the first turn is already a repair.

### BYOK key and access token — where each credential lives

Two credentials cross the wire, and they are not the same kind of secret:

- the **access token** — the paid credential for the endpoint. Sent as
  `Authorization: Bearer <token>`.
- the **BYOK provider key** — your own LLM-provider API key. Sent as
  `X-Fuaran-Provider-Key`; the endpoint uses it in memory for the one call and
  never stores, logs, or meters it.

**Both travel as HEADERS, and neither is ever in the request body.** A body is
the thing most likely to be logged wholesale by an intermediary; a header is the
thing most likely to be redacted by one. The endpoint enforces it: a body
carrying `ByokKey` or `AccessToken` is refused `400 SECRETS_IN_BODY` and the
value is not read — so if you get that code, treat the key you just sent as
exposed and rotate it. `to_wire_body` has no credential parameter at all, so
this client cannot produce such a body. The key also appears in no error
envelope and no `repr`: a logged client object cannot leak it.

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

This host declares no stability policy yet (pre-1.0), so the change is recorded
here rather than in a `STABILITY.md` it does not have.

## Placement helpers — `fuaran_py.ops.placement`

The section above leaves every caller deriving the sibling permutation itself. That
derivation is shipped once, in `fuaran_py.ops.placement`:

```python
from fuaran_py.ops import After, Last, Target, duplicate_op, move_op, nudge_op, place_op

place_op(tree, child, Target("sidebar", After("filters")))  # placed insert
move_op(tree, "chart", Target("main", Last()))  # placed move
nudge_op(tree, "chart", -1)  # keyboard move-up
duplicate_op(tree, "chart", Target("main", After("chart")))  # clone beside its source
```

`Placement` is `Last()` | `First()` | `Before(anchor)` | `After(anchor)` — an id, never
an ordinal, for the reason the section above gives. `can_place` is the same verdict
without the op, for greying out an illegal drop without a dry-run apply.

**These helpers emit only existing `TreeOp` shapes** — `InsertChild`, `MoveNode`,
`ReorderChildren`, and `Batch` of those. There is **no new wire vocabulary, no new
fixture family, and no conformance obligation** attached to any of it: the emitted op
goes through the ordinary `apply` gate exactly as a hand-written one does, and a host
that never imports this module reads the same bytes. The reorder leg is dropped whenever
appending already yields the wanted order, so the common case stays a single bare op.

Two behaviours are worth knowing before you rely on them:

- **An anchor that is not among the destination's post-op children is REFUSED**
  (`UnknownAnchor`), not silently appended. The only op that could honour such an anchor
  is a `ReorderChildren` naming it, which `apply` refuses as `OrderingMismatch`; saying so
  before emission beats a rejection after it. Every other refusal is a pre-statement of
  the apply-time refusal the emitted op would have met, so a helper verdict and an apply
  verdict never disagree.
- **The clone verbs remap ids across the whole traversal surface**, not just the
  structural child lists — the id-uniqueness contract is tree-wide, so a clone keeping an
  old id inside a `Switch` case or a `State` slot would smuggle a duplicate past it.
  Colliding ids are remapped and non-colliding ones preserved, so `paste_op` keeps a
  lifted subtree's identity where it can while `duplicate_op` (every id collides)
  remaps all of them. The minting strategy is injectable — `derived_ids` (the default,
  `<id>-copy`, `-copy-2`, …) or `sequential_ids(prefix)` for deterministic replay.

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
