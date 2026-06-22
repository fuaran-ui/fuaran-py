"""Computed-style observer for the Fuaran UI — Python host.

Python twin of ``Fuaran.UI.StyleObserver``. Reads back a rendered tree's resolved
computed styles and derives a fixed vocabulary of resolved-style flags
(contrast-below-AA, invisible-text, accent-indistinct, plus the manifest-aware
token-resolution / off-palette / usage-budget / declared-contrast-floor checks)
the semantic-state channel is blind to — as small typed facts rather than a
screenshot, deterministically.

Two observers:

- :class:`InMemoryStyleObserver` — fixture-driven, substrate-free (tests / headless).
- :class:`BrowserStyleObserver` — reads **live** ``getComputedStyle`` under Pyodide
  (client-side Python in the browser), the analogue of the F# Fable / TS browser
  observers.

The flag + observation JSON encode is byte-identical to the F# and TypeScript
hosts for the same value.
"""

from __future__ import annotations

from .color import (
    BLACK,
    TRANSPARENT,
    WHITE,
    FontRole,
    Rgba,
    encode_rgba,
    is_opaque,
    rgb,
    rgba,
    same_rgb,
    try_parse_hex,
)
from .flags import (
    DEFAULT_OPTIONS,
    AccentIndistinct,
    ContrastBelowAA,
    ContrastBelowDeclaredFloor,
    InvisibleText,
    OffPaletteColour,
    StyleFlag,
    StyleInput,
    StyleObservation,
    StyleObserverOptions,
    TokenResolutionFailed,
    UsageBudgetExceeded,
    accent_indistinct,
    baseline_style_input,
    composite,
    contrast,
    contrast_below_aa,
    contrast_ratio,
    derive_style_flags,
    effective_background,
    encode_style_flag,
    encode_style_observation,
    flag_kind,
    flags_equal,
    font_role,
    invisible_text,
    relative_luminance,
    resolved_background,
    resolved_foreground,
    to_style_observation,
)
from .manifest_flags import per_node_flags, verify_usage_budgets
from .observer import (
    BrowserDeps,
    BrowserStyleObserver,
    InMemoryStyleObserver,
    parse_css_color,
)

__all__ = [
    # colour primitives
    "Rgba",
    "BLACK",
    "WHITE",
    "TRANSPARENT",
    "rgb",
    "rgba",
    "is_opaque",
    "same_rgb",
    "try_parse_hex",
    "encode_rgba",
    "FontRole",
    # flags + observation
    "ContrastBelowAA",
    "InvisibleText",
    "AccentIndistinct",
    "TokenResolutionFailed",
    "OffPaletteColour",
    "UsageBudgetExceeded",
    "ContrastBelowDeclaredFloor",
    "StyleFlag",
    "flag_kind",
    "encode_style_flag",
    "StyleObservation",
    "encode_style_observation",
    "StyleObserverOptions",
    "DEFAULT_OPTIONS",
    "StyleInput",
    "baseline_style_input",
    # derivation
    "composite",
    "effective_background",
    "relative_luminance",
    "contrast_ratio",
    "resolved_background",
    "resolved_foreground",
    "contrast",
    "font_role",
    "invisible_text",
    "contrast_below_aa",
    "accent_indistinct",
    "derive_style_flags",
    "to_style_observation",
    "flags_equal",
    # manifest-aware tier
    "per_node_flags",
    "verify_usage_budgets",
    # observers
    "InMemoryStyleObserver",
    "BrowserStyleObserver",
    "BrowserDeps",
    "parse_css_color",
]
