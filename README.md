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
| `fuaran_py.ui` | The ergonomic, typed **authoring** surface — smart constructors over a typed per-kind model (`fuaran.metric(...)`, `binding.static(...)`, `format.currency(...)`). See [docs/AUTHORING.md](docs/AUTHORING.md). |
| `fuaran_py.schema` | The typed tree + `decode_node` / `encode_node` (canonical Node codec); `schema.types` is the typed per-kind authoring model. |
| `fuaran_py.ops` | The `TreeOp` algebra: `decode_op` / `encode_op` + `apply(op, tree)` (the reducer over all 11 ops). |
| `fuaran_py.validator` | A pre-emit, default-deny-by-shape structural validator. |
| `fuaran_py.canonical` | The canonical-JSON encoder (key sort, number form, escaping). |
| `fuaran_py.conformance` | A corpus round-trip smoke harness. |
| `fuaran_py.renderer` | Optional server-HTML renderer (`render_html`) + the byte-copied reference stylesheet. |
| `fuaran_py.runtime` | Interactive Pyodide client runtime — the in-browser mount + dispatch→apply→re-render loop, behind an injectable `BrowserDeps` seam. |

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
