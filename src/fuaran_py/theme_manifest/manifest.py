"""The declared theme-token contract — Python port of ``Fuaran.UI.ThemeManifest``.

A machine-readable theme contract the AI can reason against and the computed-style
observer (:mod:`fuaran_py.style_observer`) can verify resolved style against.
DTCG-compatible (a vanilla DTCG file decodes cleanly) extended with the two things
DTCG lacks:

1. A per-token role → tone mapping (:class:`RoleBinding`), so ``Tone.Brand`` is
   known to resolve to the manifest's brand token.
2. An invariant block — contrast floors, colour-usage budgets, motion voice —
   each soft-weighted.

Tones are represented as their wire strings (``"Default"`` … ``"Info"``); the
package is dependency-light (stdlib only).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The canonical ToneVariant palette, as wire strings.
_TONES = ("Default", "Subdued", "Brand", "Success", "Warning", "Critical", "Info")


def tone_to_string(tone: str) -> str:
    """Identity — a tone is already its PascalCase wire string."""
    return tone


def tone_of_string(s: str) -> str | None:
    """Validate a wire string as a tone, or ``None`` for an unrecognised token."""
    return s if s in _TONES else None


@dataclass(frozen=True)
class ManifestMeta:
    """Manifest metadata — the ``meta`` block."""

    name: str = ""
    version: str = ""
    description: str | None = None


ANONYMOUS_META = ManifestMeta()


@dataclass(frozen=True)
class ManifestToken:
    """One token — DTCG-compatible (``type``/``value``/``description`` round-trip
    the DTCG ``$type``/``$value``/``$description``) plus the SPEC dual-field
    ``role`` tag. ``name`` is the dotted path (``"color.brand.base"``)."""

    name: str
    type: str
    value: str
    description: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class ToneRole:
    """A role bound to one of the canonical tone variants."""

    tone: str


@dataclass(frozen=True)
class NamedRole:
    """A role bound to a broader named semantic role (body text, divider, …)."""

    name: str


ManifestRole = ToneRole | NamedRole


@dataclass(frozen=True)
class RoleBinding:
    """Binds a role onto a manifest token by name."""

    role: ManifestRole
    token_name: str


@dataclass(frozen=True)
class MotionBudget:
    """The motion-voice budget — the payload of :class:`MotionVoice`."""

    max_duration_ms: int
    easing: str | None = None


@dataclass(frozen=True)
class ContrastFloor:
    """A named role's resolved contrast must be at least ``min_ratio``."""

    role: str
    min_ratio: float


@dataclass(frozen=True)
class UsageBudget:
    """A token's share of visible surface must stay within ``target_pct ± tolerance_pct``."""

    token: str
    target_pct: float
    tolerance_pct: float


@dataclass(frozen=True)
class MotionVoice:
    """The theme's motion must stay within the declared :class:`MotionBudget`."""

    budget: MotionBudget


InvariantKind = ContrastFloor | UsageBudget | MotionVoice

DEFAULT_WEIGHT = 1.0


@dataclass(frozen=True)
class Invariant:
    """One declared invariant + its soft weight (defaults to ``1.0``)."""

    kind: InvariantKind
    weight: float = DEFAULT_WEIGHT


def invariant(kind: InvariantKind) -> Invariant:
    """Construct an invariant with the default weight."""
    return Invariant(kind)


def weighted_invariant(weight: float, kind: InvariantKind) -> Invariant:
    """Construct an invariant with an explicit weight."""
    return Invariant(kind, weight)


def invariant_kind_name(inv: Invariant) -> str:
    """Stable discriminator string for an invariant."""
    return type(inv.kind).__name__


@dataclass(frozen=True)
class ThemeManifest:
    """The declared theme contract: metadata + tokens + role bindings + invariants."""

    meta: ManifestMeta = ANONYMOUS_META
    tokens: list[ManifestToken] = field(default_factory=list)
    roles: list[RoleBinding] = field(default_factory=list)
    invariants: list[Invariant] = field(default_factory=list)


EMPTY_MANIFEST = ThemeManifest()


def try_get_token(name: str, manifest: ThemeManifest) -> ManifestToken | None:
    """Look up a token by its dotted name."""
    for t in manifest.tokens:
        if t.name == name:
            return t
    return None


def resolve_role(tone: str, manifest: ThemeManifest) -> ManifestToken | None:
    """Resolve a tone to its declared manifest token, or ``None`` (no binding / dangling)."""
    for b in manifest.roles:
        if isinstance(b.role, ToneRole) and b.role.tone == tone:
            return try_get_token(b.token_name, manifest)
    return None


def resolve_named_role(role: str, manifest: ThemeManifest) -> ManifestToken | None:
    """Resolve a named (non-tone) role to its declared manifest token."""
    for b in manifest.roles:
        if isinstance(b.role, NamedRole) and b.role.name == role:
            return try_get_token(b.token_name, manifest)
    return None


def palette_colours(manifest: ThemeManifest) -> set[str]:
    """Every colour value declared in the palette — the off-palette check's membership set."""
    return {t.value for t in manifest.tokens if t.type == "color"}
