# Authoring Fuaran trees in Python (`fuaran_py.ui`)

`fuaran_py.ui` is the **ergonomic, typed authoring surface** – the Python analogue
of `@fuaran-ui/ui` (TypeScript) and `Fuaran.UI` (F#). A Python developer builds a
Fuaran UI tree with smart constructors that inject per-kind defaults and ARIA, and
`encode` serialises it to canonical JSON **byte-identically** to the shared
wire-format corpus.

> **Where this sits.** The LLM's emission surface is the canonical JSON wire
> format, for *every* host. The three language tiers (F#, TypeScript, Python) are
> **human-developer** authoring surfaces that produce that JSON – what you reach
> for to write app shells, fragment libraries, fixtures, and golden trees by hand.
> The AI never authors host-language code; it emits the wire format directly.

## Quickstart

```python
from fuaran_py.ui import fuaran, binding, action, format, node, encode

tree = fuaran.dashboard(
    "root",
    children=[
        fuaran.heading("title", "Channel performance", level=1),
        fuaran.metric(
            "revenue",
            label="Revenue",
            value=1234.5,  # a bare number → Binding.Static
            format=format.currency("GBP"),
            tone="Brand",
            trend=0.07,
            trend_format=format.percent(1),
        ),
        fuaran.markdown("note", "Updated hourly."),  # a bare str → Literal text
    ],
)

wire = encode(tree)  # canonical JSON, byte-identical to every other host's output
```

`encode(tree)` is exactly `encode_node(tree.to_wire())` – the typed tree lowers to
the generic structural model and the proven canonical encoder serialises it, so
there is no second encoder to drift from the corpus.

## Namespaces

| Namespace | What it builds | Examples |
|---|---|---|
| `fuaran.*` | Element constructors (one per `NodeKind`) | `fuaran.metric(...)`, `fuaran.stack(...)`, `fuaran.button(...)` |
| `binding.*` | Typed `Binding` values | `binding.static(42)`, `binding.state("loading", False)`, `binding.opaque()` |
| `action.*` | Typed `Action` values | `action.dispatch(msg)`, `action.navigate("/home")`, `action.chain([...])` |
| `format.*` | Typed `CellFormat` (KPI / column formatting) | `format.currency("GBP")`, `format.percent(1)`, `format.number(2)` |
| `node.*` | Immutable postfix modifiers | `node.with_tone("Brand", n)`, `node.with_role("Data", n)`, `node.bare(n)` |
| `accessibility.*` | The per-kind ARIA defaults | `accessibility.button`, `accessibility.metric` |

## Idiomatic ergonomics

The surface is Pythonic – `snake_case` names, keyword arguments, sensible
optionals – the analogue of the TypeScript options-object constructors, not a
transliteration of F#:

- A bare `str` where a `TextSource` is expected becomes a `Literal` (`"hi"` →
  `{"$type":"Literal","text":"hi"}`).
- A bare number where a `Binding` is expected becomes a `Static`
  (`1234.5` → `{"$type":"Static","value":1234.5}`).
- A KPI `value` accepts a number, a `Binding`, or a display string that is
  leniently parsed (`value="£42k"` → `Static(42.0)`).
- `snake_case` field names map to the wire's `camelCase` automatically
  (`trend_format` → `trendFormat`, `x_field` → `xField`).

## Per-kind defaults + ARIA injection

Each constructor fills omitted fields with the per-kind default and injects the
ARIA trait for that kind, exactly as the F#/TS smart constructors do:

```python
fuaran.button("go", label="Go").accessibility  # Accessibility(role="button")
fuaran.metric("m", label="X", value=1).accessibility  # Accessibility(live_region="polite")
fuaran.markdown("md", "body").accessibility  # None (decorative — no ARIA)
```

Decorative and structural kinds default to no ARIA; interactive (`Button`,
`Select`, `FileUpload`) and notification (`Callout`, `Progress`) kinds carry a role
and/or live-region. To drop an injected trait – for example to match a fixture
authored without one – wrap the node in `node.bare(...)`:

```python
node.bare(fuaran.metric("m", label="Revenue", value=1234.5))  # no accessibility key on the wire
```

## Postfix modifiers

`node.*` returns a new node (everything is immutable / frozen):

```python
styled = node.with_voice("Display", node.with_role("Data", fuaran.markdown("h", "Q3 revenue")))
busy = node.on_loading(fuaran.skeleton("ph", 3), fuaran.metric("m", label="X", value=1))
```

## The terse layer (`fuaran_py.ui.quick`) — title-first, records-in, ids derived

Everything above is **id-first**: the first positional argument is the node id, because
an app shell, a fragment library or a golden fixture wants ids it chose and can address
later. A notebook cell wants the opposite. `fuaran_py.ui.quick` is a thin layer *over*
these constructors — same per-kind defaults, same ARIA injection, same `encode` — that
takes a title and a list of records and derives the ids:

```python
from fuaran_py.ui import quick

rows = df.to_dict("records")  # or any list of dicts
totals = df.groupby("region")["revenue"].sum().sort_values(ascending=False).to_dict()

app = quick.dashboard(
    "Regional revenue",
    quick.metric_strip(totals),
    quick.markdown(f"**{next(iter(totals))}** leads on revenue.", name="insight"),
    quick.grid(rows),
)
```

| Constructor | Takes |
|---|---|
| `quick.dashboard(title, *children)` | the title, then the children in order |
| `quick.heading(text, level=2)` | the text |
| `quick.markdown(body, name=None)` | the body |
| `quick.metric(label, value, …)` | one KPI tile |
| `quick.metric_strip(data, label=…, value=…)` | records + two column names, a `{label: value}` mapping, or `(label, value)` pairs |
| `quick.grid(records, columns=…, labels=…)` | a list of records; columns default to their keys |
| `quick.chart(records, x=…, y=…, kind=…)` | a list of records + the field names |

`grid` and `chart` build the embedded frame through the shipped `fuaran_py.ui.frame(...)`
Compute surface, so the rows travel as canonical columnar data with an empty
pipeline — which is also the one shape the pre-emit validator can ground a chart's
field references against. Everything returned is an ordinary `UiNode`: mix the two
surfaces freely, and drop to the id-first one the moment you need to address a node.

### Derived ids — the discipline

An id is derived from three things: the node's **kind**, its **label** (the
human-meaningful text that names it), and an **occurrence index** disambiguating two
otherwise-identical siblings. Those are hashed; the id is the kind, a slug of the
label, and six hex digits of the hash — readable in an op ticker, unique in practice:

```python
quick.metric("Revenue", 1284.5).id  # 'metric-revenue-893567'
```

Three properties follow:

- **Same input → same ids.** The derivation reads nothing but its arguments, so
  re-running an unchanged cell produces byte-identical wire.
- **A changed label moves only the nodes it names.** Every sibling's id is computed
  from its own label.
- **Changed data moves no id at all.** Values, trends and rows never feed the
  derivation.

That is what makes a re-run *patchable*. `fuaran_py.ops.diff` over two runs of the
same cell yields a short, typed op script against the nodes whose contents changed —
`UpdateProp` and `EditNode`, never a `RemoveNode` / `InsertChild` rebuild:

```python
from fuaran_py.ops import diff

before, after = build(january), build(february)
diff(before.to_wire(), after.to_wire())  # 5 ops for the dashboard above; [] if nothing changed
```

The op count is proportional to how many nodes' *contents* moved, so a larger
dashboard legitimately yields a longer script; what is invariant is that none of the
ops are structural.

**Absolute position is deliberately not part of the derivation**, though it is the
obvious thing to hash. It would make every insertion renumber everything after it, so
adding one metric would re-key the rest of the dashboard and turn a one-op patch into
a rebuild — the outcome the derivation exists to avoid. The occurrence index is a
*relative* position (the nth node sharing a kind and a label) and carries no such
coupling.

One node type needs help: prose recomputed from the data. Its text is not its
identity, so give it a `name`, which is then what the id derives from:

```python
quick.markdown(f"**{leader}** leads on revenue.", name="insight")
```

Without it the id follows the body, and a re-run with new prose removes one node and
inserts another rather than updating one.

A worked end-to-end script — authoring, validation, the round trip, and both diffs —
is [`../examples/quickstart_terse_dashboard.py`](../examples/quickstart_terse_dashboard.py).

## Conformance

`encode(tree)` is byte-identical to the canonical wire-format corpus for any tree
that matches a fixture, and any authored tree survives a decode→encode round-trip
byte-stably – the same conformance bar the F# and TypeScript hosts meet. See
[`../README.md`](../README.md) and `WIRE_FORMAT.md` for the wire contract.
