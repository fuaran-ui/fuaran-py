"""The machine-readable theme contract for the Fuaran UI — Python host.

Python twin of ``Fuaran.UI.ThemeManifest``: a DTCG-compatible token model extended
with semantic role bindings (which token a tone resolves to) and quantified
invariants (per-role contrast floors, 60-30-10 colour-usage budgets, motion
voice). The contract the computed-style observer (:mod:`fuaran_ui.style_observer`)
verifies resolved style against::

    from fuaran_ui.theme_manifest import decode, resolve_role

    manifest = decode(tokens_json)        # DTCG file or { meta, tokens, roles, invariants }
    defects = validate(manifest)          # what the decoded invariants MEAN (Phase 2133)
    brand = resolve_role("Brand", manifest)
    canonical = encode(manifest)          # canonical JSON, byte-identical with every host
"""

from __future__ import annotations

from .decode import decode, of_json
from .encode import encode, to_json
from .manifest import (
    ANONYMOUS_META,
    DEFAULT_WEIGHT,
    EMPTY_MANIFEST,
    ContrastFloor,
    Invariant,
    InvariantKind,
    ManifestMeta,
    ManifestRole,
    ManifestToken,
    MotionBudget,
    MotionVoice,
    NamedRole,
    RoleBinding,
    ThemeManifest,
    ToneRole,
    UnknownInvariant,
    UsageBudget,
    invariant,
    invariant_kind_name,
    palette_colours,
    resolve_named_role,
    resolve_role,
    tone_of_string,
    tone_to_string,
    try_get_token,
    weighted_invariant,
)
from .project import (
    CssBlock,
    merge,
    project_from_css_custom_properties,
    project_from_dtcg,
    project_from_fuaran_tone_vars,
    scan_css_blocks,
)
from .validate import ManifestDefect, ManifestDefectCode, encode_manifest_defect, validate

__all__ = [
    # contract
    "ManifestMeta",
    "ManifestToken",
    "ManifestRole",
    "ToneRole",
    "NamedRole",
    "RoleBinding",
    "MotionBudget",
    "InvariantKind",
    "ContrastFloor",
    "UsageBudget",
    "MotionVoice",
    "UnknownInvariant",
    "Invariant",
    "ThemeManifest",
    "ANONYMOUS_META",
    "EMPTY_MANIFEST",
    "DEFAULT_WEIGHT",
    "invariant",
    "weighted_invariant",
    "invariant_kind_name",
    "tone_to_string",
    "tone_of_string",
    "try_get_token",
    "resolve_role",
    "resolve_named_role",
    "palette_colours",
    # decode
    "decode",
    "of_json",
    # encode
    "encode",
    "to_json",
    # validate (Phase 2133)
    "ManifestDefect",
    "ManifestDefectCode",
    "validate",
    "encode_manifest_defect",
    # project
    "CssBlock",
    "scan_css_blocks",
    "project_from_fuaran_tone_vars",
    "project_from_css_custom_properties",
    "project_from_dtcg",
    "merge",
]
