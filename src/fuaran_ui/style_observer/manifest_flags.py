"""Manifest-aware flag derivation — Python port of ``Fuaran.UI.StyleObserver.ManifestFlags``.

The render-time enforcement of a declared aesthetic-semantic budget, composing the
resolved fills (the manifest-free observation) + a :class:`ThemeManifest`.
Deterministic — no vision model in the verify path.

Two surfaces:

- :func:`per_node_flags` — per-node fidelity checks (token resolution, palette
  membership, declared contrast floor). Appended to each observation by an
  observer that has a manifest wired.
- :func:`verify_usage_budgets` — the tree-level area-weighted colour-budget check
  (the 60-30-10 enforcement). The caller joins each observation with its rendered
  area per node.

Custom-subtree policy: EXEMPT. Every per-node check fires only for TONED nodes;
untoned content (Custom / domain SVG) is exempt by construction.
"""

from __future__ import annotations

from ..theme_manifest import (
    ContrastFloor,
    ManifestToken,
    ThemeManifest,
    UsageBudget,
    resolve_named_role,
    resolve_role,
    tone_of_string,
)
from .color import Rgba, same_rgb, try_parse_hex
from .flags import (
    ContrastBelowDeclaredFloor,
    OffPaletteColour,
    StyleFlag,
    StyleObservation,
    TokenResolutionFailed,
    UsageBudgetExceeded,
)


def _rgb_string(c: Rgba) -> str:
    return f"rgb({round(c.r)}, {round(c.g)}, {round(c.b)})"


def _token_path_key(name: str) -> list[str]:
    """Canonical token-path order — the order palette ATTRIBUTION iterates in
    (Phase 1727; the rule is stated once, in fuaran-dotnet's
    ``docs/THEME-BRIDGE-GUIDE.md`` under "Palette attribution order", and pinned by
    the corpus's ``style-observer/budget-same-valued-tokens-*`` vectors). Paths
    compare segment by segment, a shorter prefix first, each segment by Unicode
    code point — which is what comparing the segment lists does, and is the order
    a depth-first walk of the DTCG tree yields when it visits every group's
    members in ascending key order (the Go and Rust decoders emit exactly that).
    This host's decoder preserves DOCUMENT order, and a projection consumer may
    rely on that, so the ordering lives here, at the one site where order is a
    contract — not in ``theme_manifest.decode``."""
    return name.split(".")


def _palette_rgba(manifest: ThemeManifest) -> list[tuple[Rgba, str]]:
    """The manifest's colour palette, parsed and paired with the declaring token
    name, in canonical token-path order. The first entry whose colour matches a
    rendered fill is the token the fill is ATTRIBUTED to, so two same-valued
    tokens attribute to the path-first one on every host."""
    out: list[tuple[Rgba, str]] = []
    for t in sorted(manifest.tokens, key=lambda t: _token_path_key(t.name)):
        if t.type != "color":
            continue
        c = try_parse_hex(t.value)
        if c is not None:
            out.append((c, t.name))
    return out


def _resolve_slot(manifest: ThemeManifest, slot: str) -> ManifestToken | None:
    tone = tone_of_string(slot)
    return resolve_role(tone, manifest) if tone is not None else resolve_named_role(slot, manifest)


def per_node_flags(manifest: ThemeManifest, obs: StyleObservation) -> list[StyleFlag]:
    """Per-node manifest-aware flags for one observation. Empty for untoned nodes."""
    if obs.emitted_tone is None:
        return []
    slot = obs.emitted_tone
    resolved = _resolve_slot(manifest, slot)
    out: list[StyleFlag] = []

    if resolved is None:
        out.append(TokenResolutionFailed(slot))
    else:
        on_palette = any(same_rgb(c, obs.effective_background) for c, _ in _palette_rgba(manifest))
        if not on_palette:
            out.append(OffPaletteColour(_rgb_string(obs.effective_background)))

    for inv in manifest.invariants:
        kind = inv.kind
        if isinstance(kind, ContrastFloor) and kind.role == slot and obs.contrast_ratio < kind.min_ratio:
            out.append(ContrastBelowDeclaredFloor(kind.role, obs.contrast_ratio, kind.min_ratio))

    return out


def verify_usage_budgets(manifest: ThemeManifest, nodes: list[tuple[StyleObservation, float]]) -> list[StyleFlag]:
    """Tree-level area-weighted usage-budget verification. ``nodes`` pairs each
    observation with its rendered area (px²). Empty when no area is available."""
    total_area = sum(area for _, area in nodes)
    if total_area <= 0.0:
        return []

    palette = _palette_rgba(manifest)
    area_by_token: dict[str, float] = {}
    for obs, area in nodes:
        for c, name in palette:
            if same_rgb(c, obs.effective_background):
                area_by_token[name] = area_by_token.get(name, 0.0) + area
                break

    out: list[StyleFlag] = []
    for inv in manifest.invariants:
        kind = inv.kind
        if isinstance(kind, UsageBudget):
            token_area = area_by_token.get(kind.token, 0.0)
            observed_pct = 100.0 * token_area / total_area
            if abs(observed_pct - kind.target_pct) > kind.tolerance_pct:
                out.append(UsageBudgetExceeded(kind.token, kind.target_pct, observed_pct))
    return out
