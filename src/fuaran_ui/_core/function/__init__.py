"""The Core ``Function`` twin: the value-space vocabulary and the signature-searchable registry.

* :mod:`~fuaran_ui._core.function.space` — :class:`HoleSpace`, the twin of the reference
  ``ValueSpace``.
* :mod:`~fuaran_ui._core.function.registry` — ``FunctionRegistry`` / ``findBySignature`` and
  deterministic composition over it.

Private — the published import paths are :mod:`fuaran_ui.function` (the registry) and
:mod:`fuaran_ui.ui.capability` (``HoleSpace``), which re-export these modules unchanged. See
:mod:`fuaran_ui._core` for the boundary rule.
"""
