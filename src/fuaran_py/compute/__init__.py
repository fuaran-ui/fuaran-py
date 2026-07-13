"""``fuaran_py.compute`` — the compute-layer host resolver.

Evaluates a wire-declared compute graph (a ``Binding.Transform`` source: embedded
datasource + ``Transform`` pipeline + state-bound ``parameters``) to derived values,
the Python leg of the F#/TS compute-layer host parity. See :mod:`fuaran_py.compute.evaluate`.
"""

from __future__ import annotations

from .evaluate import (
    ComputeErr,
    ComputeOk,
    ComputeResult,
    ComputeState,
    evaluate_transform,
    evaluate_tree,
    resolve_param_binding,
    rows_of,
)

__all__ = [
    "evaluate_tree",
    "evaluate_transform",
    "resolve_param_binding",
    "rows_of",
    "ComputeOk",
    "ComputeErr",
    "ComputeResult",
    "ComputeState",
]
