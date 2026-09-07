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
  leniently parsed (`value="£42k"` → `Static(42.0)`; `"1,234"` → `1234.0`,
  `"12.5%"` → `12.5`, `"$3.4M"` → `3.4` — a magnitude suffix is dropped, not
  applied). A display string the strip leaves unparseable — `"n/a"`, `"—"`, a
  date — **raises `ValueError`** naming the input and these shapes; it does not
  become `0.0`, because a tile reading zero is a number a reader will act on
  rather than a visibly missing value.
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

## Displaying a tree inline (Jupyter, JupyterLab, VS Code, marimo)

A tree evaluated as the last expression of a cell **renders in place**, with no
import beyond `fuaran_py`:

```python
from fuaran_py.ui import quick

quick.dashboard(
    "Regional revenue",
    quick.metric_strip(rows, label="region", value="revenue"),
    quick.grid(rows),
)
```

The rich-display protocol is a **method name** (`_repr_mimebundle_`), not an
IPython import, so this costs the package nothing: `fuaran-py`'s dependency set is
still empty and no notebook library is imported anywhere in it. Both tree types
implement it — the authored `UiNode` above and any `Node` you decoded off the wire
— through one display path, so what a notebook shows and what a server serves
cannot drift apart.

Three representations go to the front end, and it picks the richest it
understands:

| Media type | What it is |
|---|---|
| `text/html` | the shipped server renderer's output (`fuaran_py.renderer.render_html`), wrapped in a container with the reference stylesheet inlined once per output and rewritten to apply only inside it |
| `application/vnd.fuaran.ui+json` | the canonical wire JSON, as the bytes `encode_node` produced — a string, not a re-serialised object, so byte-identity survives the trip |
| `text/plain` | a one-line summary, for a terminal REPL or a diff of a recorded notebook. `repr()` is still the full structural view |

`fuaran_py.renderer.mimebundle(node, …)` is the same thing as a function, if you
want to hand a bundle to something yourself; `display_html(node, …)` is the HTML
half alone. Both take the `sources` and `egress_policy` keywords `render_html`
takes, which is how a notebook declares a wider destination posture — by name,
exactly as a web host does.

### What is, and is not, interactive

The output is **static and read-only**, deliberately. Being precise about the
boundary is more useful than the summary:

- **It renders** the full node vocabulary the server renderer supports, styled by
  the reference stylesheet, so it looks the same inline as it does served.
- **It resolves** `Binding.Static` values and a data-bound grid's rows at render
  time; every other binding shows the renderer's em-dash placeholder, because
  there is no host supplying values.
- **Nothing happens on click.** A `Button` is inert, a `Toggle` does not toggle,
  no `Action` reaches your kernel, and the output carries no script.
- **The wrapper fetches nothing.** The inlined stylesheet has no `url(...)` and no
  `@import`. A destination the *tree* declares — an `Image` source, an `Embed`
  frame — goes through the ambient destination policy, which refuses a non-local
  one unless you named a wider one.
- **Re-running a cell produces a new output**, not a patch of the one on screen.
  Patching in place needs a live channel and a client runtime, which is a
  different problem with different dependencies.

What is cheap today is that re-running an *unchanged* cell yields a byte-identical
tree, because `quick` derives ids from labels rather than positions — so
`fuaran_py.ops.diff` across two runs is the short typed op script an in-place patch
would eventually carry (see [Derived ids](#derived-ids--the-discipline) above).

A worked notebook, committed with its recorded outputs, is
[`../examples/notebook_display.ipynb`](../examples/notebook_display.ipynb); its
header carries the one command that re-records it.

## Parameter-bound controls (`fuaran_py.ui.control`)

A dashboard built from the layers above is read-only: the numbers are baked in at
authoring time and a reader can only look at them. A **control** makes it answer back.
It declares a state slot; a pipeline reads that slot through `param(name)`; and a host
re-derives the rows when the slot changes — with no Python present, because the
`Transform` and its parameters are ordinary wire data:

```python
from fuaran_py.ui import col, control, frame, fuaran, node, param, quick

region = control.select("region", options=col("region").unique(), source=frame(rows))

fr = frame(rows).filter(col("region").eq(param("region"))).bind(region)

app = quick.dashboard(
    "Revenue by region",
    region,
    node.bare(fuaran.chart("revenue", source=fr.to_transform_binding(), x_field="month", y_fields=["revenue"])),
)
```

| Constructor | Declares | Read it with |
|---|---|---|
| `control.select(name, options=…, default=…)` | `name` | `col("c").eq(param(name))` |
| `control.multi_select(name, options=…, default=…)` | `name` (a LIST parameter) | `col("c").is_in(param(name))` |
| `control.range(name, low=…, high=…)` | `name_min`, `name_max` | `col("c") >= param(f"{name}_min")` |
| `control.date_range(name, start=…, end=…)` | `name_from`, `name_to` | `col("c") >= param(f"{name}_from")` |

Ids are derived exactly as `quick`'s are, so a re-run of an unchanged cell is still
byte-identical and still patchable.

**A range declares two parameters, not one.** A `Transform` parameter resolves to a
single scalar and the expression algebra has no projection from a pair to its ends, so a
parameter bound to a pair-valued slot is a *list* parameter and can only test membership.
Two scalar slots is the shape that can be compared against; the constructor still renders
one labelled pair of inputs.

**An unseeded slot is an absent constraint, not a zero.** The parameter is unbound, so
the filter step reading it is pruned and that end is open — which is what makes
`control.range("revenue", low=0)` mean "at least zero, no upper bound" and a cleared
select mean "every region".

**Options can be data.** `options=col("region").unique()` lowers to a `Transform` over
the `source` frame that projects the column, de-duplicates, orders, and copies it to the
`label` column — so the option list is derived by the same evaluator the rows are, and a
region that appears in the data appears in the control without anyone maintaining a list.

**A parameter no control fills is refused when the binding is lowered**, by name, and the
refusal says what *is* declared:

```python
frame(rows).filter(col("region").eq(param("regoin"))).bind(region).to_transform_binding()
# UnboundParamError: the pipeline reads parameter(s) 'regoin' that no declared control
# fills; declared: region. Pass the control(s) to .bind(...) before lowering the binding.
```

The evaluator's `UNBOUND_PARAM` still exists and is still correct — it is the backstop
for a pipeline that arrived some other way, never the first thing an author meets.

## Handlers and values are optional on every control

Two slots on a control are optional on the wire, and an author reaches each by saying
nothing about it — but "nothing" has to be spelled, because on this surface the *default*
is what every tree written before them already meant.

| You want | You write | On the wire |
|---|---|---|
| a host closure (the default) | `t.TextField(value)` | `"onChange":"<closure>"` |
| the write-back default | `t.TextField(value, on_change=False)` | no `onChange` key |
| the auto-bound minimal control | `t.TextField(on_change=False)` | `{"$type":"Text"}` |

**No handler is what ARMS the write-back default.** A closure cannot cross the wire, so a
control declaring one describes changes that go somewhere the document cannot reach; a
control declaring none writes its own slot, and that single fact is what makes an exported
file interactive rather than merely pretty. It is why `fuaran_py.ui.control`'s
constructors have always passed `on_change=False`.

**No value is a BOUND control, not an empty one.** A decoder synthesises the context's
auto-binding — `Filter(name)` on a filter chip, `State(<field id>, <typed placeholder>)`
on a form field — so `{"$type":"Text"}` is the canonical minimal control and carries a
slot. A declared `State` or `Local` binding is honoured exactly as before; absence is
only what an author who says nothing gets.

The flags are `on_change` on the text, number, ranged, range, date, date-range, choice,
segmented, combobox, tokens, rating and colour controls; `on_toggle` on the checkbox and
the switch; `on_select` on `Tabs`, `Stepper` and `FileUpload`; `on_toggle` on
`Disclosure`; and `on_dismiss=None` on `Modal`. Two controls carry a SECOND channel that
arms independently of the first: `Tabs.on_select_tag` over `active_tag` / `tab_tags`, and
`Select.on_change_multi` over `values`.

The structural members each control requires are unaffected — `options`, `rows`,
`variant`, `max` are required where the wire schema requires them, and omitting `rows`
from a `TextAreaField` is refused by name at construction rather than encoded as a
control no schema accepts.

## Declarative behaviours, chart annotations, and the node tooltip

A second family of slots was missing for a different reason: not a hard-coded value, but
a record simply **narrower than the wire**. The grid, the chart, the static table, the
link and the node envelope each now carry the whole set the wire declares.

**Every one of them is absent by default**, in one of three shapes, and the shape is what
a host reads back:

| Shape | Members | Absent means |
|---|---|---|
| optional | grid `sort_state_key` / `default_sort` / `page_size` / `page_state_key` / `edit_state_key`; every chart slot; `Link.protection`; `UiNode.tooltip` | the author said nothing |
| tri-state | `Column.sortable`, `Column.editable`, `Table.sortable` | the author was not asked — which is NOT the same as an explicit `False` |
| omitted-at-false | `DataGrid.reorderable` | `False`, restored by every host's decoder |

**A declarative behaviour names a State key, and that is the whole point.** `sortStateKey`,
`pageStateKey` and `editStateKey` say WHERE the live sort, page and pending edits live, so
the affordance survives a round trip; a closure-sorted grid sorts somewhere a decoded
document cannot reach. The server-side renderer in this host does not realise them — it
emits every row in source order — and carries them intact for a client host that does.

### Chart annotations (§4l)

```python
fuaran.chart(
    "revenue",
    source=t.Static(rows),
    x_field="quarter",
    y_fields=["revenue"],
    kind="Bar",
    annotations=[
        t.ReferenceLine(140, "Target"),  # a place on the VALUE axis
        t.EventMarker(t.AnnotationCategory("Q3"), "Repricing"),  # one x address
        t.RangeBand(t.ValueRange(0, 100), "Tolerance"),  # an interval on the value axis
        t.RangeBand(
            t.XRange(
                t.AnnotationDate("2026-01-20"),  # …or on the x axis
                t.AnnotationDate("2026-02-20"),
            ),
            "Incident",
        ),
    ],
)
```

Three members, closed. An annotation carries an **address** and a **label** and nothing
else — the ink is the host's. An address is `t.AnnotationCategory(key)` on a band axis or
`t.AnnotationDate(iso)` on one declaring `x_scale="Temporal"`, and the language refuses
the mismatch rather than coercing it: a date read as a category grounds against no band,
and a category read as a date lands on 1970-01-01.

That refusal is the pre-emit validator's, and it is what "declared, not sniffed" buys:

| Code | Refuses |
|---|---|
| `FUARAN137` | a non-finite value on a reference line or a value band's end |
| `FUARAN138` | a category key no row carries — or one that two rows do |
| `FUARAN139` | an address in the other axis's form |
| `FUARAN140` | a `Date` that is not a readable ISO-8601 day |
| `FUARAN141` | a band whose pair runs backwards |

Two windows, deliberately different. `FUARAN137` reads the spec's own literal, so it is
total over every source shape. The grounding rules need the ROWS, so they fire only where
the rows are literally in the tree (a `Static` source) and stand down otherwise — refuse
only what is *provably* wrong. A `Pie` is silent about every address, matching the
lowering, which neutralises the whole family there: the polar arm has no x axis, so there
is no form for an address to mismatch.

### The tooltip is a node trait

```python
node.with_tooltip("Re-reads every document; about a minute.", fuaran.button("b", label="Rebuild"))
```

A postfix modifier beside `node.with_tone` / `node.bare`, not a keyword on forty
constructors, because a tooltip is a trait of the thing pointed at and every kind can be
pointed at. It is a `TextSource`, so it can be bound or localised. **It is never the
accessible name**: an icon-only control whose only name is a tooltip has no name — give it
`accessibility.label` and let the tooltip say the thing the name cannot.

## Placed geometry, labelled statements, and guest boundaries

`fuaran.drawing` / `fuaran.fact` / `fuaran.mount` complete the three kinds that had no
spelling at all here. The distinction from the sections above is worth keeping straight:
those records were *narrower* than the wire, these kinds were *absent* — `encode` needs a
`.to_wire()` root, so an omitted kind is not awkward to author, it is unauthorable.

```python
from fuaran_py.schema import types as t
from fuaran_py.ui import fuaran

fuaran.drawing(
    "revenue",
    view_box=t.ViewBox(0, 0, 200, 100),
    title="Quarterly revenue",
    shapes=[
        t.Rectangle(10, 10, 80, 40, corner_radius=4, style=t.DrawStyle(fill=t.Static("#3366cc"))),
        t.Group((t.Circle(150, 50, 20), t.Line(0, 0, 200, 100))),
        t.Label(100, 90, t.LiteralText("Revenue"), style=t.DrawStyle(rotation=-30, text_anchor="Middle")),
    ],
)

fuaran.fact("today", label="Today", value=t.Bound(t.Now("Day")))

fuaran.mount("side", scope_id="guest-sidebar", capabilities=["notify"])
```

**`Drawing` — the shape vocabulary is closed and carries no `d` string.** `Group`,
`Rectangle`, `Line`, `Polyline`, `Polygon`, `Curve`, `Circle`, `Ellipse` and `Label`, with
`Curve` taking the five typed path commands (`MoveTo` / `LineTo` / `CubicTo` /
`QuadraticTo` / `Close`). A path string would smuggle a second grammar past every
validator and every tree op. Coordinates are plain floats — a drawing is a *resolved*
artefact, and a chart lowering produces concrete numbers — while `DrawStyle` carries the
bindings, which is what keeps colour reactive on a static picture. Every `DrawStyle` slot
is absent by default, so a shape emits only what differs from what it inherits; the one
trap is `rotation`, where an explicit `0` is a document and only `None` omits the key.

**`Fact` — `metric()`'s complementary kind.** A metric carries a number through a
`CellFormat`; a fact carries a `TextSource`, which is why its value binds to the host's
clock (`t.Bound(t.Now(...))`) or to a grid selection (`t.Bound(binding.selection(...))`)
as naturally as it takes a literal. `tone` omits at `"Default"` and `emphasis` at `False`,
so the minimal fact is the two-key document.

**`Mount` — the isolation boundary.** `capabilities` is the whole of what the guest may
do, so its default is the EMPTY list rather than an absent key: default-deny is a
statement. `inputs` shares the `FragmentArg` vocabulary with `fragment_ref`, so scalars
carry configuration and `t.SlotArg(tree)` hands the guest a whole node tree. `on_bubble`
follows the handler-flag convention from the section above — `True` by default, `False`
for a guest whose bubbles the host does not take.

## Conformance

`encode(tree)` is byte-identical to the canonical wire-format corpus for any tree
that matches a fixture, and any authored tree survives a decode→encode round-trip
byte-stably – the same conformance bar the F# and TypeScript hosts meet. See
[`../README.md`](../README.md) and `WIRE_FORMAT.md` for the wire contract.
