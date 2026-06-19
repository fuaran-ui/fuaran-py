# Fable-Python build-vs-port decision record

**Status:** decided — **hand-write all codec layers** (no Fable-Python transpile).
**Date:** 2026-06-19. **Scope:** the headless wire-format codec cores
(schema / ops / validator). The renderer is out of scope (headless host).

This record settles, with evidence, whether `fuaran-py`'s codec cores are
**hand-written** or **transpiled from F# via Fable's Python backend**, so the
host's implementation proceeds on a recorded decision rather than an assumption.
The deliverable of the evaluation is this record; the codec it informs lives
alongside it in this repo.

## TL;DR

Hand-write every layer. A Fable-Python transpile buys nothing on the one
genuinely hard problem (canonical number formatting), produces a non-idiomatic
Python API that Python consumers would reject, and would add a third
encoder-parity pipeline to maintain — while the hand-written codec **already
passes the entire conformance corpus byte-for-byte** (55 node + 11 op
round-trips, 28 reject cases) at a few hundred lines of dependency-free Python.
This matches the established sibling-not-port stance of the TypeScript host.

## What was evaluated

The decision hinges on four questions from the build-vs-port brief. Each is
answered from concrete evidence below.

### 1. The make-or-break: canonical number formatting (§5)

The canonical wire form requires finite floats in the `.NET Double.ToString("R")`
layout (fixed-point iff the leading-digit exponent is in `[-4, 16]`, else
scientific with an uppercase `E`, an always-present sign, and a ≥2-digit
zero-padded exponent; `-0` collapses to `0`). This is the project's pivot point:
if the encoder cannot reproduce this exactly, the host is not a host.

**Finding — it is trivially solvable in hand-written Python, and a transpile does
not help.** CPython's shortest `repr(float)` produces the same significant-digit
sequence as .NET `"R"` and V8 `Number.toString` (all David-Gay-family
shortest-round-trip); only the *layout* differs. Re-laying-out those digits into
the canonical form is ~25 lines. Verified byte-for-byte against the corpus
divergence-zone values and edge cases:

| input | canonical output | source fixture |
|---|---|---|
| `1e21` | `1E+21` | `metric-float-exp-pos` |
| `1e-7` | `1E-07` | `metric-float-exp-neg` |
| `0.30000000000000004` | `0.30000000000000004` | `metric-float-17sig` |
| `1.2345678901234568e17` | `1.2345678901234568E+17` | `metric-float-bigint` |
| `5e-324` (smallest subnormal) | `5E-324` | — |
| `-0.0` | `0` | — |

The decisive observation: **even the F# host's own Fable (JS) pipeline hand-rolls
this exact algorithm.** Its `formatFiniteDouble` carries an
`[<Emit("$0.toString()")>]` shim and re-implements the .NET `"R"` layout by hand,
precisely because neither JS-native nor Fable-native number formatting matches
the canonical form. A Fable-Python transpile would inherit that hand-rolled
algorithm — *and* its `Emit` shim emits **JavaScript** `.toString()`, which is
meaningless on CPython. So the hard part must be written by hand for Python
**regardless** of transpile-vs-hand-write; transpiling only adds a broken
JS-emit dependency to fix. `format_finite_double` in `canonical.py` is the
clean hand-written equivalent (`repr()` in place of the JS shim).

### 2. What builds / BCL + feature gaps

The codec cores are nominally `FSharp.Core`-only, but the encoder
(`CanonicalJson.fs`) and decoder (`JsonDecode.fs`) are **not** clean transpile
inputs:

- They carry `Fable.Core` `Emit` blocks fenced under `#if FABLE_COMPILER`
  that are **JS-specific** (the number shim above), so the Fable-Python output
  would be wrong where it matters most.
- The decoder is a ~3,600-line hand-rolled recursive-descent parser built around
  F#'s `Result` / discriminated-union surface; Fable-Python's coverage of the
  full F# feature + BCL surface is immature relative to the JS backend, so the
  spike risk is high and the payoff (see §3) is negative.

Rather than chase a brittle transpile of pre-existing JS-fenced code, the host
implements the **language-neutral spec** (`WIRE_FORMAT.md`) directly — the same
contract the TypeScript host implements without reading F# source.

### 3. Idiomaticity + the generated API

A Fable-Python transpile would expose F#'s shapes (DU-encoded unions, generated
`Result` classes, F# naming) to Python callers. Python consumers expect
`snake_case` functions, dataclasses, and a `{ok, value}` / `{ok, error}` result
— exactly the `AdapterDecodeResult` shape the conformance contract already
specifies. A transpile cannot produce that surface; a hand-written host produces
it natively (`Ok` / `Err`, `decode_node`, `DecodeError(code, path, message)`).
For a host whose entire purpose is to be the *idiomatic Python* surface on the
wire format, the generated-API gap alone is disqualifying.

### 4. Maintenance cost — the third parity pipeline

The workspace already maintains one cross-pipeline parity gate
(Fable-JS-vs-.NET). A Fable-Python transpile would add a **second** parity
pipeline (Fable-Python-vs-.NET): every Fable toolchain bump, every `Emit`-fence
change, every F# core refactor would have to be re-verified against the Python
output. The sibling approach takes on **no new pipeline** — it pins to the
existing canonical corpus (the same gate the TypeScript host clears), so the only
coupling is "a wire-format change moves every host," which the forward-coupling
rule (§11) already mandates.

## Decision (per layer)

| Layer | Decision | Rationale |
|---|---|---|
| `schema` (Node tree + decode/encode) | **Hand-write** | Idiomatic dataclasses + `{ok,value}` result; spec-driven; canonical number form is hand-rolled regardless. |
| `ops` (TreeOp decode/encode) | **Hand-write** | Same contract, same reasoning; trivially shares the canonical encoder. |
| `validator` (pre-emit checks) | **Hand-write** | A small default-deny-by-shape surface; no F# logic worth transpiling. |

No layer benefits from a Fable-Python transpile.

## Evidence the recommendation is actionable

The hand-written codec this record informs is already in the repo and verified:
it round-trips the **full** `../wire-format-fixtures/` corpus byte-for-byte (55
nodes + 11 ops) and rejects all 28 malformed fixtures with the canonical error
code + `$`-rooted path — i.e. it clears the same conformance bar any chosen
approach must clear, today, with zero runtime dependencies. The hard datapoint
(the canonical number form) is proven by `tests/test_canonical_numbers.py`.

## What this record does not decide

- The renderer (out of scope — headless host).
- The formal third-host certification machinery (offline corpus snapshot + drift
  guard, schema validation, a language-agnostic certification bridge, a CI leg,
  and generative parity) — follow-up work that builds on this hand-written codec.
