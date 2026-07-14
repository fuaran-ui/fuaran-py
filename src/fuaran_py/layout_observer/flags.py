"""LayoutFlag vocabulary + the pure geometry→flags derivation core.

Python port of ``Fuaran.UI.LayoutObserver`` (the F# ``LayoutFlag`` DU +
``LayoutInput`` + ``Flags.derive`` + ``LayoutObservation`` + options), and the twin
of the Rust ``introspect/layout.rs`` derivation. The orchestrator's semantic-state
channel is blind to layout failures — a Stack squeezed flat by an oversized sibling,
a child clipped by a parent's ``overflow: hidden``, a flex item collapsed to zero
height. This module is the small fixed vocabulary the layout observer derives from
raw geometry.

The derivation is a **pure tier over an observed-measurements input record** — no
browser dependency. The flag names + thresholds + deterministic order match the
go/rs/F# hosts flag-for-flag, so the same geometry in produces the same flags out;
the ``encode_layout_flag`` / ``LayoutObservation.encode`` JSON forms are
byte-identical to the F# host for the same value.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── LayoutFlag — the AI-facing geometric interpretations (closed, additive-only) ──


@dataclass(frozen=True)
class OverflowHorizontal:
    """``scrollWidth > clientWidth`` AND computed ``overflow-x`` is not ``visible``."""


@dataclass(frozen=True)
class OverflowVertical:
    """``scrollHeight > clientHeight`` AND computed ``overflow-y`` is not ``visible``."""


@dataclass(frozen=True)
class ZeroDimension:
    """One of the element's dimensions resolved to ≤ 0.5px. ``axis`` is ``"width"`` / ``"height"``."""

    axis: str


@dataclass(frozen=True)
class SqueezedToMin:
    """The rendered dimension equals its computed ``min-width`` / ``min-height``. ``axis`` is the axis."""

    axis: str


@dataclass(frozen=True)
class ChildClippedByAncestor:
    """The element's bounding rect extends beyond a clipping ancestor's rect."""


@dataclass(frozen=True)
class AspectRatioWildlyOff:
    """The observed ``width/height`` ratio diverges from the expected by ``factor`` (≥ 1.0, direction-agnostic)."""

    factor: float


LayoutFlag = (
    OverflowHorizontal
    | OverflowVertical
    | ZeroDimension
    | SqueezedToMin
    | ChildClippedByAncestor
    | AspectRatioWildlyOff
)


def flag_kind(flag: LayoutFlag) -> str:
    """Stable PascalCase discriminator — the dataclass name is the wire kind."""
    return type(flag).__name__


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def encode_layout_flag(flag: LayoutFlag) -> str:
    """Encode a flag as the AI-friendly tagged-object JSON (byte-identical to the F# host)."""
    if isinstance(flag, (ZeroDimension, SqueezedToMin)):
        return f'{{"kind":"{flag_kind(flag)}","axis":"{_esc(flag.axis)}"}}'
    if isinstance(flag, AspectRatioWildlyOff):
        return f'{{"kind":"AspectRatioWildlyOff","factor":{flag.factor:.2f}}}'
    return f'{{"kind":"{flag_kind(flag)}"}}'


# ── LayoutObserverOptions — host-tunable policy ────────────────────────────────


@dataclass(frozen=True)
class LayoutObserverOptions:
    """Host-tunable policy. v1 defaults: 100ms debounce, 3x aspect-ratio threshold, change-only emission."""

    debounce_ms: int = 100
    aspect_ratio_wildly_off_factor: float = 3.0
    emit_on_flag_change_only: bool = True


DEFAULT_OPTIONS = LayoutObserverOptions()


# ── LayoutInput — the abstract metric envelope ─────────────────────────────────


@dataclass(frozen=True)
class LayoutInput:
    """The abstract metric envelope the derivation operates on.

    Pre-populated by either the browser observer (live geometry) or a hand-authored
    fixture. Optional fields stay ``None`` when the source can't provide them —
    derivation degrades gracefully (the dependent flag simply doesn't fire).
    """

    width: float
    height: float
    scroll_width: float | None = None
    scroll_height: float | None = None
    client_width: float | None = None
    client_height: float | None = None
    overflow_x: str | None = None
    overflow_y: str | None = None
    min_width: float | None = None
    min_height: float | None = None
    clipping_ancestor_rect: tuple[float, float, float, float] | None = None
    element_rect: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    expected_aspect_ratio: float | None = None


def layout_input(width: float, height: float) -> LayoutInput:
    """Baseline input — element rect defaults to ``(0, 0, width, height)``."""
    return LayoutInput(width=width, height=height, element_rect=(0.0, 0.0, width, height))


# ── Per-flag predicates ────────────────────────────────────────────────────────


def overflow_horizontal(inp: LayoutInput) -> LayoutFlag | None:
    """``OverflowHorizontal`` — content extends beyond paint region AND the element clips."""
    if inp.scroll_width is None or inp.client_width is None or inp.overflow_x is None:
        return None
    if inp.scroll_width > inp.client_width and inp.overflow_x != "visible":
        return OverflowHorizontal()
    return None


def overflow_vertical(inp: LayoutInput) -> LayoutFlag | None:
    """``OverflowVertical`` — the vertical counterpart of :func:`overflow_horizontal`."""
    if inp.scroll_height is None or inp.client_height is None or inp.overflow_y is None:
        return None
    if inp.scroll_height > inp.client_height and inp.overflow_y != "visible":
        return OverflowVertical()
    return None


def zero_dimension(inp: LayoutInput) -> list[LayoutFlag]:
    """``ZeroDimension`` — fires per axis whose rendered dimension resolved to ≤ 0.5px."""
    out: list[LayoutFlag] = []
    if inp.width <= 0.5:
        out.append(ZeroDimension("width"))
    if inp.height <= 0.5:
        out.append(ZeroDimension("height"))
    return out


def squeezed_to_min(inp: LayoutInput) -> list[LayoutFlag]:
    """``SqueezedToMin`` — fires per axis whose rendered dimension is within 0.5px of its min."""
    out: list[LayoutFlag] = []
    if inp.min_width is not None and inp.min_width > 0.0 and abs(inp.width - inp.min_width) <= 0.5:
        out.append(SqueezedToMin("width"))
    if inp.min_height is not None and inp.min_height > 0.0 and abs(inp.height - inp.min_height) <= 0.5:
        out.append(SqueezedToMin("height"))
    return out


def child_clipped_by_ancestor(inp: LayoutInput) -> LayoutFlag | None:
    """``ChildClippedByAncestor`` — the element's rect extends beyond a clipping ancestor's rect."""
    if inp.clipping_ancestor_rect is None:
        return None
    anc_l, anc_t, anc_r, anc_b = inp.clipping_ancestor_rect
    el_l, el_t, el_r, el_b = inp.element_rect
    if el_l < anc_l - 0.5 or el_t < anc_t - 0.5 or el_r > anc_r + 0.5 or el_b > anc_b + 0.5:
        return ChildClippedByAncestor()
    return None


def aspect_ratio_wildly_off(factor_threshold: float, inp: LayoutInput) -> LayoutFlag | None:
    """``AspectRatioWildlyOff`` — observed/expected magnitude ≥ ``factor_threshold`` (direction-agnostic)."""
    if inp.expected_aspect_ratio is None or inp.expected_aspect_ratio <= 0.0:
        return None
    if inp.height <= 0.0 or inp.width <= 0.0:
        return None
    observed = inp.width / inp.height
    ratio = observed / inp.expected_aspect_ratio
    magnitude = ratio if ratio >= 1.0 else 1.0 / ratio
    if magnitude >= factor_threshold:
        return AspectRatioWildlyOff(magnitude)
    return None


def derive(options: LayoutObserverOptions, inp: LayoutInput) -> list[LayoutFlag]:
    """Derive the full flag list for one input — deterministic order across every host."""
    out: list[LayoutFlag] = []
    h = overflow_horizontal(inp)
    if h is not None:
        out.append(h)
    v = overflow_vertical(inp)
    if v is not None:
        out.append(v)
    out.extend(zero_dimension(inp))
    out.extend(squeezed_to_min(inp))
    clip = child_clipped_by_ancestor(inp)
    if clip is not None:
        out.append(clip)
    aspect = aspect_ratio_wildly_off(options.aspect_ratio_wildly_off_factor, inp)
    if aspect is not None:
        out.append(aspect)
    return out


# ── LayoutObservation — one geometric snapshot per node ────────────────────────


@dataclass(frozen=True)
class LayoutObservation:
    """One geometric snapshot for a single addressable node — raw metrics + derived flags."""

    node_id: str
    width: float
    height: float
    viewport_x: float
    viewport_y: float
    flags: list[LayoutFlag] = field(default_factory=list)


def encode_layout_observation(obs: LayoutObservation) -> str:
    """Encode an observation as camelCase JSON — byte-identical to the F# host."""
    flags_json = ",".join(encode_layout_flag(f) for f in obs.flags)
    return (
        f'{{"nodeId":"{_esc(obs.node_id)}","width":{obs.width:.2f},"height":{obs.height:.2f},'
        f'"viewportX":{obs.viewport_x:.2f},"viewportY":{obs.viewport_y:.2f},"flags":[{flags_json}]}}'
    )


def to_layout_observation(options: LayoutObserverOptions, node_id: str, inp: LayoutInput) -> LayoutObservation:
    """Build a fully-populated observation from a measured input (viewport coords from the element rect)."""
    return LayoutObservation(
        node_id=node_id,
        width=inp.width,
        height=inp.height,
        viewport_x=inp.element_rect[0],
        viewport_y=inp.element_rect[1],
        flags=derive(options, inp),
    )


def flags_equal(a: list[LayoutFlag], b: list[LayoutFlag]) -> bool:
    """Order-sensitive flag-list equality (the derive order)."""
    return a == b
