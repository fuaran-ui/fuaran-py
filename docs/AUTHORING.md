# Authoring Fuaran trees in Python (`fuaran_py.ui`)

`fuaran_py.ui` is the **ergonomic, typed authoring surface** — the Python analogue
of `@fuaran-ui/ui` (TypeScript) and `Fuaran.UI` (F#). A Python developer builds a
Fuaran UI tree with smart constructors that inject per-kind defaults and ARIA, and
`encode` serialises it to canonical JSON **byte-identically** to the shared
wire-format corpus.

> **Where this sits.** The LLM's emission surface is the canonical JSON wire
> format, for *every* host. The three language tiers (F#, TypeScript, Python) are
> **human-developer** authoring surfaces that produce that JSON — what you reach
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
            value=1234.5,                 # a bare number → Binding.Static
            format=format.currency("GBP"),
            tone="Brand",
            trend=0.07,
            trend_format=format.percent(1),
        ),
        fuaran.markdown("note", "Updated hourly."),   # a bare str → Literal text
    ],
)

wire = encode(tree)   # canonical JSON, byte-identical to every other host's output
```

`encode(tree)` is exactly `encode_node(tree.to_wire())` — the typed tree lowers to
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

The surface is Pythonic — `snake_case` names, keyword arguments, sensible
optionals — the analogue of the TypeScript options-object constructors, not a
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
fuaran.button("go", label="Go").accessibility        # Accessibility(role="button")
fuaran.metric("m", label="X", value=1).accessibility # Accessibility(live_region="polite")
fuaran.markdown("md", "body").accessibility          # None (decorative — no ARIA)
```

Decorative and structural kinds default to no ARIA; interactive (`Button`,
`Select`, `FileUpload`) and notification (`Callout`, `Progress`) kinds carry a role
and/or live-region. To drop an injected trait — for example to match a fixture
authored without one — wrap the node in `node.bare(...)`:

```python
node.bare(fuaran.metric("m", label="Revenue", value=1234.5))   # no accessibility key on the wire
```

## Postfix modifiers

`node.*` returns a new node (everything is immutable / frozen):

```python
styled = node.with_voice("Display", node.with_role("Data", fuaran.markdown("h", "Q3 revenue")))
busy = node.on_loading(fuaran.skeleton("ph", 3), fuaran.metric("m", label="X", value=1))
```

## Conformance

`encode(tree)` is byte-identical to the canonical wire-format corpus for any tree
that matches a fixture, and any authored tree survives a decode→encode round-trip
byte-stably — the same conformance bar the F# and TypeScript hosts meet. See
[`../README.md`](../README.md) and `WIRE_FORMAT.md` for the wire contract.
