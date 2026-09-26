# Contributing to fuaran-py

This repo is licensed **Apache-2.0** (see [`LICENSE`](LICENSE)). Contributions are welcome under
the same licence. The conventions below keep the tree green and the wire format stable.

## Contribution licensing — Developer Certificate of Origin

Every commit must be signed off under the [Developer Certificate of Origin 1.1](https://developercertificate.org/)
to certify you have the right to contribute the code under Apache-2.0. Add a `Signed-off-by:`
trailer to each commit:

```
git commit -s -m "feat: your change"
```

A pull request without DCO sign-off on every commit will not be merged.

## Per-commit hard requirements

1. **`pwsh ./run.ps1` is green** — the one-command gate: format check, build, and the full test
   suite in one pass.
2. **Formatting** — run `ruff format` (and `ruff check`) before every commit. Unformatted code is not mergeable.
3. **Conformance** — wire-surface changes must keep the bundled conformance corpus green (round-trip, reject, and lenient-accept families). The corpus is canonical upstream — corpus updates arrive as corpus-sync changes, never hand-edits to fixtures.

## The Core boundary

This host reimplements the Core subsystems it needs as twins of the reference `Fuaran.Core`
libraries (Apache-2.0, public). Those twins live together behind one private boundary,
`src/fuaran_ui/_core/`, so Core semantics in Python have one known home: a Core change is one
edit there, and cutting a separate Core package later is a copy rather than an untangling.

- **What it holds:** `_core/dataframe/` — the columnar DataFrame model, its byte-exact canonical
  codec (including the lenient-ingest rules) and the reference-parity Transform evaluator; and
  `_core/function/` — the `Fuaran.Core.Function` twin: `HoleSpace`, the value-space vocabulary
  (the reference `ValueSpace`), and the signature-searchable function registry that matches over it.
- **Published paths do not move.** `fuaran_ui.dataframe` and its `model` / `codec` / `evaluate`
  submodules, `fuaran_ui.function`, and `fuaran_ui.ui.capability`'s `HoleSpace` re-export `_core`
  unchanged. Import from those; `_core` is private.
- **Decision — why `HoleSpace` moved rather than stayed with the capability registry** (Phase 1872).
  The function registry was the one Core twin left outside the boundary, because it took its value
  space from the UI capability module. That space is not a UI type: it is the Core `ValueSpace`, which
  the capability registry *also* validates against. So the type moved into `_core/function/` and the
  capability module re-exports it — one type, two published paths, no widening of the allow-list.
- **The rule:** nothing under `_core` imports from the host's domain packages (`ui`, `schema`,
  `renderer`, `validator`, `compute`, …). Its only other `fuaran_ui` imports are the wire
  foundation — `canonical`, `model`, `result`, `shapeguard`, `limits`. `tests/test_core_boundary.py`
  holds the rule with an AST walk (lazy, function-scoped imports included) and fails on a planted
  domain import; the allow-list is pinned there, so widening it is a deliberate, reviewed change.
- **Decision — why the wire foundation is an allow-list, not inside the boundary.** It is this
  host's twin of the reference wire layer, but it is entangled with the UI node type: the
  structural `Value` is recursive over `Node`, `canonical.encode_value` has a `Node` branch, and
  `Node`'s notebook display imports the renderer (inside the method, on display only). Moving it
  would mean splitting the encoder away from `Node` — a rewrite, not a move. That split is the
  first step of any future extraction of `_core` into its own package.

## Pull request flow

1. Branch from `main` with a descriptive name (`feat/<short-name>`, `fix/<short-name>`,
   `docs/<short-name>`).
2. Make focused, DCO-signed commits. Group related changes; do not bundle unrelated cleanups.
3. Run the per-commit hard requirements above.
4. Open a PR describing the change and its wire-format impact.
5. A maintainer reviews and merges.
