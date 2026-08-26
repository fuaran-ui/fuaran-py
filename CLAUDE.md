# CLAUDE.md — fuaran-py (Python reference implementation)

This repo is the **Python host of the Fuaran UI language** — a **co-equal sibling
to the F# (`Fuaran.UI`) and TypeScript (`@fuaran-ui/*`) tiers**, not merely a
codec. The target identity is a full authoring + rendering host: an ergonomic
`fuaran_py.ui` smart-constructor authoring surface (roadmap Phase 278), a tree-op
apply engine (279), a server-HTML renderer (`fuaran_py.renderer`, Phase 239 —
shipped) plus an interactive Pyodide client runtime (280), all conformant to the
shared wire format. What ships **today** is the floor: the canonical-JSON codec
(`decode_node` / `encode_node` / `decode_op` / `encode_op`), a pre-emit validator,
the corpus conformance harness, and the headless renderer; the authoring +
interactive surfaces are the Wave 35 co-equality expansion (Phases 278–281).

**Framing — load-bearing, do not regress.** The LLM's emission surface is the
**canonical JSON wire format, for every host**. The three language tiers (F#, TS,
Python) are **human-developer authoring surfaces** that produce that JSON; the AI
never authors host-language code — a structure-free baseline exists only as the
*thing to beat*, not a direction. Python being
the language AI orchestrators are written in is a reason it is *valuable*, not a
reason to make it a lesser, codec-only kind of artefact than F#/TS.

This repo sits alongside the `fuaran` (F#) and `fuaran-ts` (TypeScript) tiers as a co-equal
conformant host. Cross-repo development conventions (port allocation, formatting, language-baseline pinning) live at the maintainers' workspace level and are not shipped here.

## Posture

- **Apache 2.0 from day one** — same posture as `fuaran-ts`, to make the
  reference-implementation claim unambiguous.
- **Sibling reference implementation, not a transpile.** `fuaran-py` is built to
  the language-neutral wire-format spec (`../fuaran-dotnet/docs/WIRE_FORMAT.md`) +
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
│   ├── op_stream/        # hash-chained provenance log: StreamEntry envelope + SHA-256 chain + in-memory sink + replay
│   ├── validator/        # pre-emit, default-deny-by-shape structural validator
│   ├── ai_tools/         # AI-tools introspection: emittable-surface catalog + value-space + tool schemas + tree introspection + default-deny dispatch gate (Phase 237)
│   ├── conformance/      # corpus round-trip smoke harness + certification bridge + the cross-host fuzz-sample exchange (Phase 236)
│   ├── renderer/         # optional server-HTML renderer + sanitiser + reference CSS (Phase 239)
│   ├── style_observer/   # computed-style observer: pure flag tier + InMemory + Pyodide live read-back
│   └── theme_manifest/   # DTCG-compatible theme contract (tokens + role bindings + invariants)
├── tests/                # pytest: number form, full-corpus round-trip + reject, validator
├── docs/                 # fable-python-decision.md (the build-vs-port decision record)
├── pyproject.toml        # dependency-light; dev extras = pytest / mypy / ruff
├── LICENSE               # Apache 2.0 + Diametrical Ltd copyright
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
(`../fuaran-dotnet/docs/WIRE_FORMAT.md`) with the workspace-level
`../wire-format-fixtures/` corpus as the executable conformance suite. `fuaran-py`
is one conformant host: it round-trips the corpus byte-for-byte and surfaces the
canonical reject code + path for every malformed fixture. The **forward-coupling
rule** (`WIRE_FORMAT.md` §11) means a new `NodeKind` / `Spec` / `TreeOp` /
`Binding` / `Action` case must move every host in one change — `fuaran-py` is now
one of those hosts.

### Conformance coverage (v0 bootstrap)

The codec **runs the full four-family corpus green** — node round-trips, op
round-trips, rejects, and `lenient-accept` per WIRE_FORMAT §16's normative
block (fixture counts drift as the corpus grows —
`../wire-format-fixtures/manifest.json` is the authoritative enumeration).
The 2026-07-05 parity pass closed the measured divergences: the `Mount`
NodeKind decodes (typed scopeId/channel, structural inputs); the §16
bare-string TextSource shorthand is accepted and normalises to canonical
bytes; JSON null rejects in structured JVal positions at the null's exact
path (`_from_json_strict` — the §5 `Binding.Static` opaque seam deliberately
stays null-lenient); the op decoder gates the reserved `"<opaque>"` /
`"<closure>"` sentinels in `UpdateProp` values like the F# reference. Typed
field-level validation is implemented for the common kinds;
recognised-but-not-yet-typed kinds are accepted structurally (still
byte-exact on round-trip). The formal certification harness (offline corpus
snapshot + drift guard, schema validation, a language-agnostic certification
bridge, a CI leg, and generative parity) has since landed.

### Within-host parity vs the cross-host exchange (do not conflate these)

Two generative floors sit above the curated corpus, and each proves something the
other cannot:

- **Within-host** — `tests/test_generative_parity.py`, ≥1000 `hypothesis` trees,
  `encode(decode(encode x)) == encode x`. This host's canonical form is a fixed
  point. Self-consistency only: a misreading of the spec that this host applies
  *consistently* passes it every time.
- **Cross-host** — `fuaran_py.conformance.fuzz_exchange`, the fuzz-sample
  exchange. Another host emits its canonical bytes for generated trees into
  `<dir>/fsharp/`; this module decodes + re-encodes each through the Python codec,
  asserts byte-identity, and writes **this** host's canonical output to
  `<dir>/python/` so the sibling's `--check-fuzz-samples <dir> python` can check
  the converse direction. One host's output, a different host's codec — the only
  arrangement in which a shared misreading is visible.

The exchange needs a sibling host's emitter and so is hand-driven (the module
docstring carries the full command sequence); the within-host floor runs under a
plain `pytest`. `tests/test_fuzz_exchange.py` pins the *runner* — that it detects
a divergence, emits this host's set, and refuses a missing input set — using real
corpus payloads, so it needs no other toolchain.

The exchange's directory names (`fsharp` / `python`) are a cross-repo contract
with the sibling's `--check-fuzz-samples <dir> <host>` argument; renaming either
side silently un-wires it.

### Decoder robustness fuzz (`fuaran_py.conformance.decoder_fuzz` + `tests/test_decoder_fuzz.py`)

A third generative floor, and the only one aimed at inputs a conformant emitter would never produce.
The refusal contract — decoding is TOTAL, so a malformed or hostile input yields a structured typed
error, never an exception and never a hang — is asserted against **generated** hostile input rather
than against the curated reject corpus alone. A curated corpus is evidence about the author's
imagination.

- **The bounded run IS the PR gate.** It landed in the suite `pytest` already runs, so no workflow
  change was needed and none can silently switch it off.
- **The long run is a CLI**, and it writes its own machine-readable record:
  `python -m fuaran_py.conformance.decoder_fuzz --long --iterations 250000 --evidence <file>`.
- **The go-red self-test is permanent, and so is its inverse.** Five mutants, one per invariant, plus
  a pin that each is PARTIAL — a mutant that broke every input would make the harness look sensitive
  while testing nothing.
- **The allocation invariant is split in two, deliberately.** The main stream carries a cheap
  canonical-output amplification proxy; `test_pathological_inputs_stay_inside_an_allocation_budget`
  spends `tracemalloc` on the pathological family, where an adversarial shape would actually
  amplify. Neither half is the whole invariant, and the docstrings say which is which.
- **`RecursionError` is the escape this host most needs to see.** A recursive-descent decoder that
  lets the interpreter's stack limit answer for it has no depth guard, whatever its §21 constant
  says. `check` catches it and reports it as a counterexample rather than letting it propagate.
- **The generator is a SIBLING of the reference one, not a transpile.** Same five input families; a
  different byte stream, necessarily — the reference alphabet carries lone surrogates a `str` cannot
  encode to UTF-8 at all, and the module names that gap rather than closing it silently.
- **The `duplicate-key` mutator and the `NaN` / `Infinity` / `1e999` / `+1` tokens are generated
  deliberately, and nothing here asserts which answer is right.** Those are §20 "Decode
  determinism", landed PROPOSED and not yet ratified; crash-freedom on them is in scope, agreement
  is not.

`fuaran_py.conformance.refusal_report` is the companion emitter: this host's refusal class for every
reject fixture, as JSON, for a cross-host runner that compares the hosts to each other rather than
each to the corpus in isolation.

## Renderer (Phase 239)

`fuaran_py.renderer.render_html` walks a decoded `Node` tree and emits a
**body-fragment HTML string** carrying the reference `fuaran-*` class vocabulary,
so the byte-copied stylesheet styles it exactly as the F#/TS hosts style their
output. Server semantics mirror the F# SSR precedent: no runtime, no dispatch
(`Button` inert, `Link` a real `<a href>`), `Static` bindings resolve and the
rest placeholder to an em-dash, and a visualisation this host cannot paint
renders a deterministic placeholder. A **data-bound `DataGrid` renders its
rows** — the completeness posture; the declared boundary (a closure-projected
column, which cannot survive the wire) is written up in the README's
"Bound-grid rendering" section.

Two disciplines keep it honest:

- **Reference-CSS byte-copy.** `src/fuaran_py/renderer/content/fuaran-reference.css`
  is a byte-for-byte copy of the F# canonical
  (`../fuaran-dotnet/src/Fuaran.UI.Renderer/content/fuaran-reference.css`). A test
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

**Destination policy is AMBIENT on the render context, and defaults to deny.**
`Renderer.egress_policy` (`fuaran_py.renderer.egress`, the port of the reference
host's destination-policy section) is consulted by every `href` / `src` the
renderer emits — the `Link` node's `href` under the `HYPERLINK` class, the
`Image` node's `src` under `MEDIA`, and the whole markdown body through
`markdown.to_html_with_egress`. `render_html(..., egress_policy=…)` and
`FuaranRuntime(..., egress_policy=…)` are the declaration points; both default to
`DENY_NON_LOCAL_EGRESS`, so a host that declares nothing gets deny, and
`PERMISSIVE_EGRESS` is reachable only by that name.

Three disciplines follow, and the third is the one that decays quietly:

- **A new emission that carries a URL owes a policy consultation in the same
  change.** `sanitize_url_or_blank` is the *floor*, not the seam — reaching for
  it directly at a render call site re-opens the hole the ambient default closed.
  Use `egress.sanitize_url_for_egress(self.egress_policy, <class>, url)` and
  splice the returned attribute pairs onto the emitted element, last.
- **Never widen the default to make a test pass.** The corpus fixtures and the
  ambient tests in `tests/test_egress_policy.py` are the falsifiers in both
  directions; a fixture that needs an off-origin host names a policy.
- **`tests/test_markdown_corpus.py` runs TWO legs.** The seam leg calls
  `to_html_with_egress` directly; the ambient leg renders a `Markdown` node
  through `render_html` with **no policy named** for the `denyNonLocal` fixtures.
  Keep both — the seam leg cannot detect a renderer that stopped reaching the
  seam, which is precisely the state this host was in while the policy was merely
  available.

The README's "Destination policy" section carries the host-facing contract and
the four declared shape differences from the reference host.

## Op-stream (hash chain)

`fuaran_py.op_stream` is the Python host of the op-stream **hash-chained provenance
log** — the twin of the F# and TypeScript op-stream tiers. A stream's applied
`TreeOp` edits are an append-only sequence of `OpRecord` envelopes; a host-side
SHA-256 chain (`sha256(previousHash | payload)`) links them so the stream is
tamper-evident. The pre-image is byte-identical across hosts: the versioned
`StreamEntry` envelope leads with `{"v":2,…}` (the chain format version folded in
first), wrapped in the canonical delimited `{"seq":…,"actor":…,"op":…}` payload,
with the op encoded by the shared `fuaran_py.ops.encode_op` + `fuaran_py.canonical`
(never a re-implemented encoder). The module is **stdlib-only** — unlike the
Fable-constrained hosts it uses `hashlib.sha256` directly.

- **Conformance is the contract.** `tests/test_op_stream.py` loads the shared
  `chain/chain-corpus.json` golden and asserts every computed chain hash equals the
  committed value byte-for-byte — the same corpus the F# and TypeScript hosts
  certify against. A mismatch is a bug in this host's encoder/chain, **never** the
  corpus; do not regenerate the golden to make a test pass.
- **Version lock-step.** `CHAIN_FORMAT_VERSION` is pinned to the corpus `version`;
  bump it in lock-step with the other hosts + the golden whenever the pre-image
  formula, envelope shape, or hash function changes (the wire-format forward-coupling
  rule now spans the chain envelope too).

## Computed-style observer + theme manifest

`fuaran_py.style_observer` is the Python twin of `Fuaran.UI.StyleObserver`: it
reads back a rendered tree's resolved computed styles and derives a fixed
vocabulary of resolved-style flags (contrast-below-AA, invisible-text,
accent-indistinct, plus the four manifest-aware checks) the semantic-state channel
is blind to — as small typed facts, deterministically. The flag + observation
JSON encode is **byte-identical** to the F# and TypeScript hosts.

- `InMemoryStyleObserver` — fixture-driven, substrate-free (tests / headless).
- `BrowserStyleObserver` — reads **live** `getComputedStyle` under **Pyodide**
  (CPython compiled to WASM running client-side in the browser — the Python
  analogue of the F# Fable / TS browser observers). Browser-API access is behind
  an injectable `BrowserDeps` (default: the `js` interop module, imported lazily so
  the package stays stdlib-only at install time and importable under plain CPython;
  tests inject a fake DOM). `connect()` wires a live `MutationObserver`
  (Pyodide-only) so a theme toggle re-derives automatically.

`fuaran_py.theme_manifest` is the Python twin of `Fuaran.UI.ThemeManifest`: a
DTCG-compatible token model + semantic role bindings + quantified invariants
(per-role contrast floors, 60-30-10 usage budgets, motion voice). It is the
contract the observer's manifest-aware tier (`per_node_flags` /
`verify_usage_budgets`) verifies resolved style against. Both modules are
stdlib-only; the Pyodide-only `js` / `pyodide.ffi` imports are lazy and
import-guarded (the `#if FABLE_COMPILER` analogue). Manifest JSON encode + the F#
`ThemeBridge` (typed-`Theme` projector) are not yet ported (follow-up).

## Cross-repo dependencies

No upstream dependency on any other sibling. At test time it reads the
workspace-relative corpus at `../wire-format-fixtures/` (skipped when absent, so
the repo is standalone-testable). It produces a Python package, not a NuGet pack
— the workspace `pack-all.ps1` treats it as a no-op.

## Public vocabulary discipline

`fuaran-py` is OSS-public (Apache 2.0). Per the workspace OSS publication
boundary, **shipped artefacts** (source, README, package metadata) reference only
"the Fuaran UI wire format" generically — never a private sibling/package name,
commercial product names, or the strategic-command names. The specific banned
list lives in the workspace OSS publication boundary doc, not here. This
`CLAUDE.md` lives in the public repo, so it observes the same boundary — it names
no private sibling, package, product, or command.
