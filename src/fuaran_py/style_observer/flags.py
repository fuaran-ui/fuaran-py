"""StyleFlag vocabulary + the pure flag-derivation core.

Python port of ``Fuaran.UI.StyleObserver.Abstractions`` (StyleFlag /
StyleObservation / StyleObserverOptions / StyleInput) + ``Flags`` (compositing +
WCAG contrast + per-flag predicates). Both the in-memory observer and the Pyodide
browser observer feed captured colours through :func:`derive_style_flags`, so
identical inputs produce identical flags.

The ``encode_style_flag`` / ``encode_style_observation`` JSON forms are
byte-identical to the F#/TypeScript hosts for the same value.

Two tiers: the first three flags are MANIFEST-FREE (derived here from resolved
colours + WCAG contrast); the last four are MANIFEST-AWARE (derived against a
declared theme manifest by :mod:`fuaran_py.style_observer.manifest_flags`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .color import BLACK, TRANSPARENT, WHITE, FontRole, Rgba, is_opaque

# ── StyleFlag — the AI-facing legibility interpretations ───────────────────────


@dataclass(frozen=True)
class ContrastBelowAA:
    """Composited foreground/background WCAG contrast below the AA floor but still faintly visible."""

    ratio: float


@dataclass(frozen=True)
class InvisibleText:
    """Contrast at/near 1.0 — text ≈ surface behind it. The severe subset."""

    ratio: float


@dataclass(frozen=True)
class AccentIndistinct:
    """A toned element's accent surface contrasts its container below the UI-component floor."""

    ratio: float


@dataclass(frozen=True)
class TokenResolutionFailed:
    """A tone/role the declared manifest has no token for (manifest-aware)."""

    slot: str


@dataclass(frozen=True)
class OffPaletteColour:
    """A toned element's resolved fill is not present in the manifest palette (manifest-aware)."""

    value: str


@dataclass(frozen=True)
class UsageBudgetExceeded:
    """A token's surface-area share breached its declared usage budget (manifest-aware)."""

    token: str
    declared_pct: float
    observed_pct: float


@dataclass(frozen=True)
class ContrastBelowDeclaredFloor:
    """A role's resolved contrast is below the manifest's declared per-role floor (manifest-aware)."""

    role: str
    ratio: float
    floor: float


StyleFlag = (
    ContrastBelowAA
    | InvisibleText
    | AccentIndistinct
    | TokenResolutionFailed
    | OffPaletteColour
    | UsageBudgetExceeded
    | ContrastBelowDeclaredFloor
)


def flag_kind(flag: StyleFlag) -> str:
    """Stable discriminator — the dataclass name is the PascalCase wire kind."""
    return type(flag).__name__


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def encode_style_flag(flag: StyleFlag) -> str:
    """Encode a flag as the AI-friendly tagged-object JSON (byte-identical to F#/TS)."""
    if isinstance(flag, (ContrastBelowAA, InvisibleText, AccentIndistinct)):
        return f'{{"kind":"{flag_kind(flag)}","ratio":{flag.ratio:.2f}}}'
    if isinstance(flag, TokenResolutionFailed):
        return f'{{"kind":"TokenResolutionFailed","slot":"{_esc(flag.slot)}"}}'
    if isinstance(flag, OffPaletteColour):
        return f'{{"kind":"OffPaletteColour","value":"{_esc(flag.value)}"}}'
    if isinstance(flag, UsageBudgetExceeded):
        return (
            f'{{"kind":"UsageBudgetExceeded","token":"{_esc(flag.token)}",'
            f'"declaredPct":{flag.declared_pct:.2f},"observedPct":{flag.observed_pct:.2f}}}'
        )
    return (
        f'{{"kind":"ContrastBelowDeclaredFloor","role":"{_esc(flag.role)}",'
        f'"ratio":{flag.ratio:.2f},"floor":{flag.floor:.2f}}}'
    )


# ── StyleObservation ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StyleObservation:
    """One resolved-style snapshot for a single addressable node."""

    node_id: str
    foreground: Rgba
    effective_background: Rgba
    font_role: FontRole
    emitted_tone: str | None
    contrast_ratio: float
    flags: list[StyleFlag] = field(default_factory=list)


def encode_style_observation(obs: StyleObservation) -> str:
    """Encode an observation as JSON, byte-identical to the F#/TS hosts."""
    from .color import encode_rgba

    tone = "null" if obs.emitted_tone is None else f'"{_esc(obs.emitted_tone)}"'
    flags_json = ",".join(encode_style_flag(f) for f in obs.flags)
    return (
        f'{{"nodeId":"{_esc(obs.node_id)}","foreground":{encode_rgba(obs.foreground)},'
        f'"effectiveBackground":{encode_rgba(obs.effective_background)},'
        f'"fontRole":"{obs.font_role.value}","emittedTone":{tone},'
        f'"contrastRatio":{obs.contrast_ratio:.2f},"flags":[{flags_json}]}}'
    )


# ── StyleObserverOptions ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class StyleObserverOptions:
    """Host-tunable policy. v1 defaults pin the standard WCAG floors."""

    debounce_ms: int = 100
    contrast_aa_threshold: float = 4.5
    invisible_text_threshold: float = 1.1
    accent_indistinct_threshold: float = 3.0
    emit_on_flag_change_only: bool = True


DEFAULT_OPTIONS = StyleObserverOptions()


# ── StyleInput — the abstract evidence envelope ────────────────────────────────


@dataclass(frozen=True)
class StyleInput:
    """The abstract evidence envelope the derivation operates on."""

    foreground: Rgba = BLACK
    background_layers: list[Rgba] = field(default_factory=list)
    font_family: str | None = None
    emitted_tone: str | None = None


def baseline_style_input() -> StyleInput:
    """Opaque-black text on the implicit white canvas, no font, no tone."""
    return StyleInput()


# ── Compositing + WCAG contrast ────────────────────────────────────────────────


def composite(top: Rgba, bottom: Rgba) -> Rgba:
    """Source-over composite of ``top`` (with its alpha) over ``bottom``."""
    a = top.a + bottom.a * (1.0 - top.a)
    if a <= 0.0:
        return TRANSPARENT

    def blend(tc: float, bc: float) -> float:
        return (tc * top.a + bc * bottom.a * (1.0 - top.a)) / a

    return Rgba(blend(top.r, bottom.r), blend(top.g, bottom.g), blend(top.b, bottom.b), a)


def effective_background(layers: list[Rgba]) -> Rgba:
    """Composite a background layer stack (element-first) down to the first opaque layer."""
    truncated: list[Rgba] = []
    found_opaque = False
    for layer in layers:
        truncated.append(layer)
        if is_opaque(layer):
            found_opaque = True
            break
    stack = truncated if found_opaque else [*truncated, WHITE]
    base_first = list(reversed(stack))
    if not base_first:
        return WHITE
    acc = base_first[0]
    for top in base_first[1:]:
        acc = composite(top, acc)
    return acc


def relative_luminance(c: Rgba) -> float:
    """WCAG relative luminance of an (assumed opaque) colour."""

    def channel(v: float) -> float:
        s = v / 255.0
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b)


def contrast_ratio(a: Rgba, b: Rgba) -> float:
    """WCAG contrast ratio between two opaque colours — range 1.0 … 21.0."""
    la = relative_luminance(a)
    lb = relative_luminance(b)
    lighter = max(la, lb)
    darker = min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


# ── Derived evidence ───────────────────────────────────────────────────────────


def resolved_background(inp: StyleInput) -> Rgba:
    """The opaque background the text sits on, after the composite walk."""
    return effective_background(inp.background_layers)


def resolved_foreground(inp: StyleInput) -> Rgba:
    """The colour the text actually paints with — declared fg composited over the bg."""
    return composite(inp.foreground, resolved_background(inp))


def contrast(inp: StyleInput) -> float:
    """The WCAG contrast ratio between the resolved foreground and the effective background."""
    return contrast_ratio(resolved_foreground(inp), resolved_background(inp))


def font_role(inp: StyleInput) -> FontRole:
    """Classify the computed font-family string into a :class:`FontRole`."""
    if inp.font_family is None:
        return FontRole.UNKNOWN
    f = inp.font_family.lower()
    if "mono" in f:
        return FontRole.MONOSPACE
    if "sans" in f:
        return FontRole.SANS_SERIF
    if "serif" in f:
        return FontRole.SERIF
    return FontRole.UNKNOWN


# ── Per-flag predicates ────────────────────────────────────────────────────────


def invisible_text(invisible_threshold: float, inp: StyleInput) -> StyleFlag | None:
    """``InvisibleText`` — contrast at/below the invisible threshold (text ≈ background)."""
    c = contrast(inp)
    return InvisibleText(c) if c < invisible_threshold else None


def contrast_below_aa(invisible_threshold: float, aa_threshold: float, inp: StyleInput) -> StyleFlag | None:
    """``ContrastBelowAA`` — contrast in ``[invisible_threshold, aa_threshold)``."""
    c = contrast(inp)
    return ContrastBelowAA(c) if invisible_threshold <= c < aa_threshold else None


def accent_indistinct(accent_threshold: float, inp: StyleInput) -> StyleFlag | None:
    """``AccentIndistinct`` — a toned element's tint barely contrasts the surface behind it."""
    if inp.emitted_tone is None or not inp.background_layers:
        return None
    own = inp.background_layers[0]
    if own.a <= 0.0:
        return None
    accent_surface = resolved_background(inp)
    ancestor_surface = effective_background(inp.background_layers[1:])
    c = contrast_ratio(accent_surface, ancestor_surface)
    return AccentIndistinct(c) if c < accent_threshold else None


def derive_style_flags(options: StyleObserverOptions, inp: StyleInput) -> list[StyleFlag]:
    """Derive the manifest-free flag list for one input (deterministic order)."""
    out: list[StyleFlag] = []
    inv = invisible_text(options.invisible_text_threshold, inp)
    if inv is not None:
        out.append(inv)
    aa = contrast_below_aa(options.invisible_text_threshold, options.contrast_aa_threshold, inp)
    if aa is not None:
        out.append(aa)
    accent = accent_indistinct(options.accent_indistinct_threshold, inp)
    if accent is not None:
        out.append(accent)
    return out


def to_style_observation(options: StyleObserverOptions, node_id: str, inp: StyleInput) -> StyleObservation:
    """Build a fully-populated observation — the shared shape every observer uses."""
    return StyleObservation(
        node_id=node_id,
        foreground=resolved_foreground(inp),
        effective_background=resolved_background(inp),
        font_role=font_role(inp),
        emitted_tone=inp.emitted_tone,
        contrast_ratio=contrast(inp),
        flags=derive_style_flags(options, inp),
    )


def flags_equal(a: list[StyleFlag], b: list[StyleFlag]) -> bool:
    """Order-sensitive flag-list equality (the derive order)."""
    return a == b
