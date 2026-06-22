"""ThemeManifest contract + decode + projectors — mirrors the F# ThemeManifest tests."""

from __future__ import annotations

from fuaran_py.theme_manifest import (
    ContrastFloor,
    Invariant,
    ManifestMeta,
    ManifestToken,
    NamedRole,
    RoleBinding,
    ThemeManifest,
    ToneRole,
    decode,
    invariant,
    merge,
    palette_colours,
    project_from_css_custom_properties,
    project_from_fuaran_tone_vars,
    resolve_named_role,
    resolve_role,
    try_get_token,
)

MANIFEST = ThemeManifest(
    meta=ManifestMeta(name="test", version="1.0"),
    tokens=[
        ManifestToken("color.brand.base", "color", "#3b5bdb"),
        ManifestToken("color.surface", "color", "#ffffff"),
        ManifestToken("space.md", "dimension", "16px"),
    ],
    roles=[
        RoleBinding(ToneRole("Brand"), "color.brand.base"),
        RoleBinding(NamedRole("body-text"), "color.surface"),
    ],
    invariants=[invariant(ContrastFloor("Brand", 7.0))],
)


def test_helpers() -> None:
    token = try_get_token("color.surface", MANIFEST)
    assert token is not None and token.value == "#ffffff"
    assert try_get_token("missing", MANIFEST) is None
    brand = resolve_role("Brand", MANIFEST)
    assert brand is not None and brand.name == "color.brand.base"
    assert resolve_role("Critical", MANIFEST) is None
    body = resolve_named_role("body-text", MANIFEST)
    assert body is not None and body.value == "#ffffff"
    assert palette_colours(MANIFEST) == {"#3b5bdb", "#ffffff"}


def test_decode_wrapper() -> None:
    import json

    payload = {
        "meta": {"name": "acme", "version": "2.1", "description": "x"},
        "tokens": {
            "color": {
                "brand": {"base": {"$type": "color", "$value": "#3b5bdb", "$description": "brand"}},
                "surface": {"$type": "color", "$value": "#ffffff"},
            }
        },
        "roles": [{"role": {"tone": "Brand"}, "token": "color.brand.base"}],
        "invariants": [{"kind": "ContrastFloor", "role": "Brand", "minRatio": 7, "weight": 2}],
    }
    m = decode(json.dumps(payload))
    assert m.meta == ManifestMeta(name="acme", version="2.1", description="x")
    assert try_get_token("color.brand.base", m) == ManifestToken(
        "color.brand.base", "color", "#3b5bdb", description="brand"
    )
    brand = resolve_role("Brand", m)
    assert brand is not None and brand.name == "color.brand.base"
    assert m.invariants == [Invariant(ContrastFloor("Brand", 7.0), 2.0)]


def test_decode_vanilla_dtcg() -> None:
    import json

    m = decode(json.dumps({"color": {"accent": {"$type": "color", "$value": "#ff8800"}}}))
    assert m.tokens == [ManifestToken("color.accent", "color", "#ff8800")]
    assert m.roles == []


def test_decode_role_extension() -> None:
    import json

    m = decode(
        json.dumps(
            {"color": {"brand": {"$type": "color", "$value": "#3b5bdb", "$extensions": {"fuaran": {"role": "accent"}}}}}
        )
    )
    assert m.tokens[0].role == "accent"


def test_projectors() -> None:
    m = project_from_fuaran_tone_vars(":root { --fuaran-tone-brand-bg: #3b5bdb; --fuaran-tone-brand-fg: #fff; }")
    bg = try_get_token("tone.brand.bg", m)
    assert bg is not None and bg.value == "#3b5bdb"
    brand = resolve_role("Brand", m)
    assert brand is not None and brand.name == "tone.brand.bg"

    css = ':root { --color-x: #111; } [data-theme="dark"] { --color-x: #eee; }'
    g = project_from_css_custom_properties(css)
    light = try_get_token("color-x", g)
    dark = try_get_token("color-x@dark", g)
    assert light is not None and light.value == "#111"
    assert dark is not None and dark.value == "#eee"
    assert g.roles == []


def test_merge_last_write_wins() -> None:
    base = project_from_css_custom_properties(":root { --a: 1px; --b: 2px; }")
    over = project_from_css_custom_properties(":root { --b: 9px; }")
    m = merge(base, over)
    b = try_get_token("b", m)
    a = try_get_token("a", m)
    assert b is not None and b.value == "9px"
    assert a is not None and a.value == "1px"
