# CLAUDE.md — fuaran-py (Python reference implementation)

This repo is the **Python host of the Fuaran UI wire format**: the
canonical-JSON codec (`decode_node` / `encode_node` / `decode_op` / `encode_op`),
a pre-emit validator, and a corpus conformance harness — what a Python AI
orchestrator needs to read and write Fuaran UI trees. The codec core is
**headless** (no rendering dependency); an **optional, stdlib-only server-HTML
renderer** (`fuaran_py.renderer`, Phase 239) ships alongside it for hosts that
want to render a decoded tree to HTML with no client runtime.

This is a sibling repo under the Fuaran-UI sub-estate at `../` (alongside
`fuaran`, `fuaran-ts`, `orchestration`, `orchestrator-demo`, `eval-suite`).
Cross-repo conventions (port allocation, Sync All, strategic commands, the
formatting mandate, the language-baseline pinning, the OSS publication boundary)
live in the workspace `CLAUDE.md` (`../../../CLAUDE.md`) and the Fuaran-UI
sub-estate `CLAUDE.md` (`../CLAUDE.md`). Read those first.

## Posture

- **Apache 2.0 from day one** — same posture as `fuaran-ts`, to make the
  reference-implementation claim unambiguous.
- **Sibling reference implementation, not a transpile.** `fuaran-py` is built to
  the language-neutral wire-format spec (`../fuaran/docs/WIRE_FORMAT.md`) +
  conformance corpus (`../wire-format-fixtures/`), not generated from the F# tier.
  The Fable-Python build-vs-port evaluation that settled this is recorded in
  [`docs/fable-python-decision.md`](docs/fable-python-decision.md).
- **Wire-format conformance is the stability contract.** The codec encodes /
  decodes byte-identically against the shared corpus; it is certified the same
  way the F# and TypeScript hosts are.
- **Dependency-light.** The runtime codec uses only the Python standard library.
  Third-party packages appear only as dev tooling (`pytest` / `mypy` / `ruff`).

## Language baseline

CPython **3.13+** (the workspace-chosen floor for this sibling; uses PEP 695
type parameters / `type` aliases). The Python analogue of the workspace's
F#-10/.NET-10 pinning.

## Layout

```
fuaran-py/
├── src/fuaran_py/
│   ├── canonical.py      # the canonical-JSON encoder (number form, key sort, escaping)
│   ├── model.py          # the structural typed tree (Node / Obj / Arr)
│   ├── result.py         # Ok / Err + the six DecodeError codes
│   ├── schema/           # decode_node / encode_node + the per-kind field schemas
│   ├── ops/              # decode_op / encode_op over the 11-op TreeOp algebra
│   ├── validator/        # pre-emit, default-deny-by-shape structural validator
│   ├── conformance/      # corpus round-trip smoke harness
│   └── renderer/         # optional server-HTML renderer + sanitiser + reference CSS (Phase 239)
├── tests/                # pytest: number form, full-corpus round-trip + reject, validator
├── docs/                 # fable-python-decision.md (the build-vs-port decision record)
├── pyproject.toml        # dependency-light; dev extras = pytest / mypy / ruff
├── LICENSE               # Apache 2.0 + Diametrical Pty Ltd copyright
└── run.ps1               # Stage-0 entry point — lint + format-check + type-check + test
```

## Build / verify pipeline

```powershell
.\run.ps1                 # provision .venv (first run) + ruff + mypy + pytest
.\run.ps1 -SkipInstall    # run the gate against an already-provisioned .venv
```

Or drive the tools directly inside the venv: `ruff check .`, `ruff format .`,
`mypy`, `pytest`.

## Formatting mandate

The workspace formatting mandate (Fantomas for F#, Prettier for TS) maps here to
**ruff** — every commit is preceded by `ruff format` + `ruff check` over the
changed files. The CI gate is `ruff format --check` + `ruff check`.

## Wire format

The canonical wire format is owned by the F# `fuaran` tier
(`../fuaran/docs/WIRE_FORMAT.md`) with the workspace-level
`../wire-format-fixtures/` corpus as the executable conformance suite. `fuaran-py`
is one conformant host: it round-trips the corpus byte-for-byte and surfaces the
canonical reject code + path for every malformed fixture. The **forward-coupling
rule** (`WIRE_FORMAT.md` §11) means a new `NodeKind` / `Spec` / `TreeOp` /
`Binding` / `Action` case must move every host in one change — `fuaran-py` is now
one of those hosts.

### Conformance coverage (v0 bootstrap)

The codec **round-trips the full corpus** (55 nodes + 11 ops) and rejects all 28
malformed fixtures with the canonical code + path. Typed field-level validation
is implemented for the common kinds; recognised-but-not-yet-typed kinds are
accepted structurally (still byte-exact on round-trip). The formal certification
harness (offline corpus snapshot + drift guard, schema validation, a
language-agnostic certification bridge, a CI leg, and generative parity) is
follow-up work.

## Renderer (Phase 239)

`fuaran_py.renderer.render_html` walks a decoded `Node` tree and emits a
**body-fragment HTML string** carrying the reference `fuaran-*` class vocabulary,
so the byte-copied stylesheet styles it exactly as the F#/TS hosts style their
output. Server semantics mirror the F# SSR precedent: no runtime, no dispatch
(`Button` inert, `Link` a real `<a href>`), `Static` bindings resolve and the
rest placeholder to an em-dash, and client-library visualisations render a
deterministic placeholder.

Two disciplines keep it honest:

- **Reference-CSS byte-copy.** `src/fuaran_py/renderer/content/fuaran-reference.css`
  is a byte-for-byte copy of the F# canonical
  (`../fuaran/src/Fuaran.UI.Renderer/content/fuaran-reference.css`). A test
  (`test_render_parity` / the byte-identical check) fails if the copy drifts when
  the F# sibling is checked out alongside. Re-copy it in the same change-set as
  any F#-side CSS change (the §11 forward-coupling rule now spans this host too).
- **Class-name vocabulary parity.** `tests/test_render_parity.py` extracts the
  class vocabulary straight from the F# reference renderer source (`Render.fs` +
  `Theme.fs`, literals + `sprintf "...-%s"` prefixes) and asserts every class the
  Python renderer emits over the node corpus is in it — a cross-host parity lock,
  the rendering analogue of the wire corpus. It skips when the F# sibling is
  absent. **A new `NodeKind` / variant that changes the emitted class vocabulary
  updates the renderer here in the same move that updates the codec.**

Sanitisation matches the F#/TS posture (`fuaran_py.renderer.sanitize` ports
`Sanitize.fs`): URL-scheme default-deny, `data-*`/`aria-*` attribute allowlist,
markdown escaped-first then swept. The `Custom` host-renderer registry is a host
trust boundary — the baseline ships no registry seam, so `Custom` renders an
inert labelled placeholder.

## Cross-repo dependencies

No upstream dependency on any other sibling. At test time it reads the
workspace-relative corpus at `../wire-format-fixtures/` (skipped when absent, so
the repo is standalone-testable). It produces a Python package, not a NuGet pack
— the workspace `pack-all.ps1` treats it as a no-op.

## Public vocabulary discipline

`fuaran-py` is OSS-public (Apache 2.0). Per the workspace OSS publication
boundary, **shipped artefacts** (source, README, package metadata) reference only
"the Fuaran UI wire format" generically — never a private sibling/package name
(`orchestration`, `orchestrator-demo`, `eval-suite`, the `Fuaran.UI.Orchestration.*`
/ `ToolUp.Fuaran.Adapter.*` packages), commercial product names, or the
strategic-command names. This `CLAUDE.md` is workspace-internal and not shipped.
