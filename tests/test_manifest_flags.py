"""Manifest-aware flag derivation — mirrors the F# ManifestFlagTests."""

from __future__ import annotations

from fuaran_py.style_observer import (
    BLACK,
    WHITE,
    ContrastBelowDeclaredFloor,
    FontRole,
    InMemoryStyleObserver,
    OffPaletteColour,
    StyleInput,
    StyleObservation,
    TokenResolutionFailed,
    UsageBudgetExceeded,
    per_node_flags,
    rgb,
    verify_usage_budgets,
)
from fuaran_py.theme_manifest import (
    ContrastFloor,
    ManifestMeta,
    ManifestToken,
    RoleBinding,
    ThemeManifest,
    ToneRole,
    UsageBudget,
    invariant,
)

BRAND = rgb(59, 91, 219)  # #3b5bdb

MANIFEST = ThemeManifest(
    meta=ManifestMeta(),
    tokens=[
        ManifestToken(name="color.brand.base", type="color", value="#3b5bdb"),
        ManifestToken(name="color.surface", type="color", value="#ffffff"),
    ],
    roles=[
        RoleBinding(role=ToneRole("Brand"), token_name="color.brand.base"),
        RoleBinding(role=ToneRole("Default"), token_name="color.surface"),
    ],
    invariants=[
        invariant(UsageBudget("color.brand.base", 9.0, 3.0)),
        invariant(UsageBudget("color.surface", 60.0, 10.0)),
        invariant(ContrastFloor("Brand", 7.0)),
    ],
)


def _toned(node_id: str, tone: str | None, bg: object, contrast: float) -> StyleObservation:
    return StyleObservation(
        node_id=node_id,
        foreground=BLACK,
        effective_background=bg,  # type: ignore[arg-type]
        font_role=FontRole.SANS_SERIF,
        emitted_tone=tone,
        contrast_ratio=contrast,
        flags=[],
    )


def test_usage_budget_exceeded() -> None:
    nodes = [
        (_toned("b1", "Brand", BRAND, 21.0), 180.0),
        (_toned("b2", "Brand", BRAND, 21.0), 100.0),
        (_toned("s1", "Default", WHITE, 21.0), 720.0),
    ]
    flags = verify_usage_budgets(MANIFEST, nodes)
    assert UsageBudgetExceeded("color.brand.base", 9.0, 28.0) in flags
    assert UsageBudgetExceeded("color.surface", 60.0, 72.0) in flags


def test_usage_budget_within_tolerance() -> None:
    nodes = [
        (_toned("b1", "Brand", BRAND, 21.0), 90.0),
        (_toned("s1", "Default", WHITE, 21.0), 610.0),
        (_toned("x", None, rgb(1, 2, 3), 21.0), 300.0),
    ]
    budget_flags = [f for f in verify_usage_budgets(MANIFEST, nodes) if isinstance(f, UsageBudgetExceeded)]
    assert budget_flags == []


def test_usage_budget_deterministic_and_empty() -> None:
    nodes = [(_toned("b1", "Brand", BRAND, 21.0), 280.0), (_toned("s1", "Default", WHITE, 21.0), 720.0)]
    assert verify_usage_budgets(MANIFEST, nodes) == verify_usage_budgets(MANIFEST, nodes)
    assert verify_usage_budgets(MANIFEST, []) == []


def test_contrast_below_declared_floor() -> None:
    flags = per_node_flags(MANIFEST, _toned("b1", "Brand", BRAND, 5.0))
    assert ContrastBelowDeclaredFloor("Brand", 5.0, 7.0) in flags
    assert not any(
        isinstance(f, ContrastBelowDeclaredFloor) for f in per_node_flags(MANIFEST, _toned("b1", "Brand", BRAND, 8.0))
    )


def test_token_resolution_failed() -> None:
    flags = per_node_flags(MANIFEST, _toned("c1", "Critical", rgb(200, 0, 0), 21.0))
    assert TokenResolutionFailed("Critical") in flags
    assert not any(
        isinstance(f, TokenResolutionFailed) for f in per_node_flags(MANIFEST, _toned("b1", "Brand", BRAND, 21.0))
    )


def test_off_palette_and_custom_exemption() -> None:
    flags = per_node_flags(MANIFEST, _toned("b1", "Brand", rgb(12, 200, 180), 21.0))
    assert OffPaletteColour("rgb(12, 200, 180)") in flags
    assert not any(
        isinstance(f, OffPaletteColour) for f in per_node_flags(MANIFEST, _toned("b1", "Brand", BRAND, 21.0))
    )
    # untoned (Custom / domain-SVG) node — never manifest-checked.
    assert per_node_flags(MANIFEST, _toned("chart-series", None, rgb(255, 0, 128), 21.0)) == []


def test_observer_wiring() -> None:
    no_manifest = InMemoryStyleObserver()
    no_manifest.register_fixture(
        "b1",
        StyleInput(foreground=BLACK, background_layers=[rgb(12, 200, 180)], emitted_tone="Critical"),
    )
    snap = no_manifest.observe("b1")
    assert snap is not None
    assert not any(
        isinstance(f, (TokenResolutionFailed, OffPaletteColour, UsageBudgetExceeded, ContrastBelowDeclaredFloor))
        for f in snap.flags
    )

    wired = InMemoryStyleObserver(manifest=MANIFEST)
    wired.register_fixture(
        "c1",
        StyleInput(foreground=BLACK, background_layers=[rgb(200, 0, 0)], emitted_tone="Critical"),
    )
    snap2 = wired.observe("c1")
    assert snap2 is not None and TokenResolutionFailed("Critical") in snap2.flags
