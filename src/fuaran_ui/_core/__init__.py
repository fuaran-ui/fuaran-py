"""``fuaran_ui._core`` — the private home of this host's Core twins (Phase 1864).

This package gathers the host's reimplementations of the Core subsystems — twins of the
reference ``Fuaran.Core`` libraries — behind ONE internal boundary:

* :mod:`fuaran_ui._core.dataframe` — the columnar ``DataFrame`` model, its byte-exact
  canonical codec (including the lenient-ingest rules) and the reference-parity
  ``Transform`` evaluator.
* :mod:`fuaran_ui._core.function` — the ``Function`` twin: the ``HoleSpace`` value-space
  vocabulary (the reference ``ValueSpace``) and the signature-searchable function registry
  that matches over it (Phase 1872).

**The boundary rule.** Nothing in here imports from the host's domain packages (the UI
authoring surface, the schema codec, the renderer, the validator, the compute resolver, …).
Its only imports from the rest of ``fuaran_ui`` are the wire foundation it encodes and
guards with — :mod:`fuaran_ui.canonical`, :mod:`fuaran_ui.model`, :mod:`fuaran_ui.result`,
:mod:`fuaran_ui.shapeguard` and :mod:`fuaran_ui.limits` — which is itself this host's twin
of the reference wire layer. ``tests/test_core_boundary.py`` holds the rule with an AST
walk rather than a convention; ``CONTRIBUTING.md`` says why the wire foundation is a named
allow-list and not yet inside the boundary.

**Private.** Nothing here is a published import path. The published names stay where they
have always been (``fuaran_ui.dataframe`` and its submodules, ``fuaran_ui.function`` and
``fuaran_ui.ui.capability``'s ``HoleSpace`` re-export this package unchanged); import from
there.
"""
