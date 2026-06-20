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
| `fuaran_py.schema` | The typed tree + `decode_node` / `encode_node` (canonical Node codec). |
| `fuaran_py.ops` | The `TreeOp` algebra + `decode_op` / `encode_op`. |
| `fuaran_py.validator` | A pre-emit, default-deny-by-shape structural validator. |
| `fuaran_py.canonical` | The canonical-JSON encoder (key sort, number form, escaping). |
| `fuaran_py.conformance` | A corpus round-trip smoke harness. |
| `fuaran_py.renderer` | Optional server-HTML renderer (`render_html`) + the byte-copied reference stylesheet. |

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
renderer emits the body fragment only. An interactive (client-framework) target
is a deliberate non-goal of this baseline.

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
