"""``fuaran_py.layout_observer`` — geometry→LayoutFlag derivation (WIRE_FORMAT §-layout tier).

A pure tier over an observed-measurements input record: given a node's measured
geometry (:class:`LayoutInput`), derive the typed structural :data:`LayoutFlag`s
(overflow, truncation via zero-dimension, wrap/squeeze, viewport-fit via aspect) —
deterministically, with no browser dependency in the pure tier. Same geometry in →
same flags out on every host (F# / Rust / Go), so a Python agent can "read the
layout" of the UI it drives. The :class:`BrowserLayoutObserver` feeds it live
measurements under Pyodide; :class:`InMemoryLayoutObserver` feeds it fixtures.
"""

from __future__ import annotations

from .flags import (
    DEFAULT_OPTIONS,
    AspectRatioWildlyOff,
    ChildClippedByAncestor,
    LayoutFlag,
    LayoutInput,
    LayoutObservation,
    LayoutObserverOptions,
    OverflowHorizontal,
    OverflowVertical,
    SqueezedToMin,
    ZeroDimension,
    aspect_ratio_wildly_off,
    child_clipped_by_ancestor,
    derive,
    encode_layout_flag,
    encode_layout_observation,
    flag_kind,
    flags_equal,
    layout_input,
    overflow_horizontal,
    overflow_vertical,
    squeezed_to_min,
    to_layout_observation,
    zero_dimension,
)
from .observer import BrowserDeps, BrowserLayoutObserver, InMemoryLayoutObserver

__all__ = [
    "LayoutFlag",
    "OverflowHorizontal",
    "OverflowVertical",
    "ZeroDimension",
    "SqueezedToMin",
    "ChildClippedByAncestor",
    "AspectRatioWildlyOff",
    "LayoutInput",
    "LayoutObservation",
    "LayoutObserverOptions",
    "DEFAULT_OPTIONS",
    "derive",
    "layout_input",
    "to_layout_observation",
    "overflow_horizontal",
    "overflow_vertical",
    "zero_dimension",
    "squeezed_to_min",
    "child_clipped_by_ancestor",
    "aspect_ratio_wildly_off",
    "encode_layout_flag",
    "encode_layout_observation",
    "flag_kind",
    "flags_equal",
    "InMemoryLayoutObserver",
    "BrowserLayoutObserver",
    "BrowserDeps",
]
