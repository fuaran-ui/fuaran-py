"""Theme-manifest encode, unknown invariant kinds, and the validation pass (Phase 2133).

The decoder stays tolerant (the byte law omits a member at its default); the
validation pass asks what the decoded values mean, with the shared code table of
WIRE_FORMAT.md §13. The corpus's ``manifest-validation`` vectors are certified with
the rest of the style-observer family (``tests/test_style_observer_corpus.py``);
these are this host's go-red partners, one per rule.

The encode byte literals are copied VERBATIM from the Rust oracle's tests
(``fuaran-rs/tests/manifest.rs``, Phase 1725), as the TypeScript and Go hosts copied
them — NOT recorded from a run of the code under test: a byte pin whose recorder is
the code under test pins nothing. Read a change to one as a wire-format change for
every host, never as a fixture refresh.
"""

from __future__ import annotations

import json

import pytest

from fuaran_ui.canonical import encode_value
from fuaran_ui.model import from_json
from fuaran_ui.theme_manifest import (
    DEFAULT_WEIGHT,
    EMPTY_MANIFEST,
    ContrastFloor,
    Invariant,
    ManifestMeta,
    ManifestToken,
    MotionBudget,
    MotionVoice,
    NamedRole,
    RoleBinding,
    ThemeManifest,
    ToneRole,
    UnknownInvariant,
    decode,
    encode,
    encode_manifest_defect,
    merge,
    project_from_fuaran_tone_vars,
    validate,
)

# ── Encode — byte parity with the Rust oracle ─────────────────────────────────

_SAMPLE = ThemeManifest(
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
    invariants=[Invariant(ContrastFloor("Brand", 7.0))],
)

_SAMPLE_BYTES = (
    '{"invariants":[{"kind":"ContrastFloor","minRatio":7,"role":"Brand"}],'
    '"meta":{"name":"test","version":"1.0"},'
    '"roles":[{"role":{"tone":"Brand"},"token":"color.brand.base"},'
    '{"role":{"named":"body-text"},"token":"color.surface"}],'
    '"tokens":{"color":{"brand":{"base":{"$type":"color","$value":"#3b5bdb"}},'
    '"surface":{"$type":"color","$value":"#ffffff"}},'
    '"space":{"md":{"$type":"dimension","$value":"16px"}}}}'
)

_DECODABLE_SOURCES = [
    """{"meta":{"name":"acme","version":"2.1","description":"x"},
    "tokens":{"color":{"brand":{"base":{"$type":"color","$value":"#3b5bdb","$description":"brand"}},
                       "surface":{"$type":"color","$value":"#ffffff"}}},
    "roles":[{"role":{"tone":"Brand"},"token":"color.brand.base"}],
    "invariants":[{"kind":"ContrastFloor","role":"Brand","minRatio":7,"weight":2}]}""",
    '{"color":{"accent":{"$type":"color","$value":"#ff8800"}}}',
    '{"color":{"brand":{"$type":"color","$value":"#3b5bdb","$extensions":{"fuaran":{"role":"accent"}}}}}',
    """{"tokens":{"a":{"$value":"1"}},
    "invariants":[{"kind":"UsageBudget","token":"a","targetPct":12.5,"tolerancePct":2,"weight":0.25},
                  {"kind":"MotionVoice","maxDurationMs":240,"easing":"ease-out"},
                  {"kind":"MotionVoice"},
                  {"kind":"FocusRing","minWidthPx":2,"nested":{"b":[1,2.5,null,true],"a":"s"},"weight":3}]}""",
]


def test_encode_pins_the_oracle_bytes_of_a_hand_built_manifest() -> None:
    assert encode(_SAMPLE) == _SAMPLE_BYTES
    # encode∘decode is the identity ON CANONICAL BYTES.
    assert encode(decode(_SAMPLE_BYTES)) == _SAMPLE_BYTES


def test_encode_pins_the_oracle_bytes_of_a_projected_manifest() -> None:
    m = project_from_fuaran_tone_vars(":root { --fuaran-tone-brand-bg: #3b5bdb; --fuaran-tone-brand-fg: #fff; }")
    assert encode(m) == (
        '{"roles":[{"role":{"tone":"Brand"},"token":"tone.brand.bg"}],'
        '"tokens":{"tone":{"brand":{"bg":{"$type":"color","$value":"#3b5bdb"},'
        '"fg":{"$type":"color","$value":"#fff"}}}}}'
    )


@pytest.mark.parametrize("src", _DECODABLE_SOURCES)
def test_decode_of_encode_is_the_identity_and_the_output_is_canonical(src: str) -> None:
    m = decode(src)
    once = encode(m)
    assert decode(once) == m
    assert encode(decode(once)) == once
    # The host-neutral half: the output is a fixpoint of the shared canonical renderer.
    assert encode_value(from_json(json.loads(once))) == once


def test_encode_omits_every_member_the_decoder_tolerates_the_absence_of() -> None:
    assert encode(EMPTY_MANIFEST) == '{"tokens":{}}'

    def with_weight(weight: float) -> ThemeManifest:
        return ThemeManifest(invariants=[Invariant(MotionVoice(MotionBudget(0)), weight)])

    assert encode(with_weight(DEFAULT_WEIGHT)) == '{"invariants":[{"kind":"MotionVoice"}],"tokens":{}}'
    assert encode(with_weight(2.0)) == '{"invariants":[{"kind":"MotionVoice","weight":2}],"tokens":{}}'

    anonymous = ThemeManifest(roles=[RoleBinding(NamedRole(""), "t")])
    assert encode(anonymous) == '{"roles":[{"token":"t"}],"tokens":{}}'
    assert decode(encode(anonymous)) == anonymous


def test_colliding_token_paths_resolve_last_write_wins_in_both_directions() -> None:
    deeper_last = ThemeManifest(tokens=[ManifestToken("a", "", "1"), ManifestToken("a.b", "", "2")])
    assert encode(deeper_last) == '{"tokens":{"a":{"b":{"$value":"2"}}}}'
    shallower_last = ThemeManifest(tokens=[ManifestToken("a.b", "", "2"), ManifestToken("a", "", "1")])
    assert encode(shallower_last) == '{"tokens":{"a":{"$value":"1"}}}'


# ── Unknown invariant kinds are kept ──────────────────────────────────────────


def test_an_unknown_kind_round_trips_with_its_members_and_weight() -> None:
    m = decode(
        '{"invariants":[{"kind":"FocusRing","minWidthPx":2,"nested":{"b":1,"a":[true,"s",null]},"weight":3}],'
        '"tokens":{}}'
    )
    assert m.invariants == [
        Invariant(UnknownInvariant("FocusRing", {"minWidthPx": 2, "nested": {"b": 1, "a": [True, "s", None]}}), 3.0)
    ]
    assert encode(m) == (
        '{"invariants":[{"kind":"FocusRing","minWidthPx":2,"nested":{"a":[true,"s",null],"b":1},"weight":3}],'
        '"tokens":{}}'
    )
    # Byte-identical through decode and encode, once canonical.
    assert encode(decode(encode(m))) == encode(m)


@pytest.mark.parametrize("member", ['{"role":"x","minRatio":7}', '{"kind":"","role":"x"}', '{"kind":7}'])
def test_an_object_with_no_non_empty_string_kind_is_still_dropped(member: str) -> None:
    assert decode(f'{{"tokens":{{}},"invariants":[{member}]}}').invariants == []


def test_unknown_invariant_identity_is_its_canonical_bytes() -> None:
    a = UnknownInvariant("F", {"x": 1, "y": {"b": 1, "a": 2}})
    assert a == UnknownInvariant("F", {"y": {"a": 2, "b": 1.0}, "x": 1})
    assert hash(a) == hash(UnknownInvariant("F", {"y": {"a": 2, "b": 1.0}, "x": 1}))
    # `true` and `1` are different wire values, though Python's `==` calls them equal.
    assert UnknownInvariant("F", {"x": True}) != UnknownInvariant("F", {"x": 1})
    assert UnknownInvariant("F", {}) != UnknownInvariant("G", {})


def test_merge_keeps_unknown_invariants_and_de_duplicates_identical_ones() -> None:
    a = decode('{"tokens":{},"invariants":[{"kind":"FocusRing","minWidthPx":2}]}')
    b = decode('{"tokens":{},"invariants":[{"kind":"FocusRing","minWidthPx":2},{"kind":"FocusRing","minWidthPx":3}]}')
    assert len(merge(a, b).invariants) == 2


# ── The validation pass, one rule at a time ───────────────────────────────────


def _invariants(json_members: str) -> ThemeManifest:
    return decode(f'{{"tokens":{{}},"invariants":[{json_members}]}}')


def _defects(m: ThemeManifest) -> list[str]:
    return [encode_manifest_defect(d) for d in validate(m)]


def test_the_omitted_default_floor_is_out_of_range_and_its_role_unbound() -> None:
    assert _defects(_invariants('{"kind":"ContrastFloor","role":"x"}')) == [
        '{"code":"CONTRAST_FLOOR_OUT_OF_RANGE","index":0,"subject":"x"}',
        '{"code":"CONTRAST_FLOOR_ROLE_UNBOUND","index":0,"subject":"x"}',
    ]


@pytest.mark.parametrize(
    ("ratio", "out_of_range"), [("0.5", True), ("21.5", True), ("1", False), ("21", False), ("7", False)]
)
def test_the_floor_is_held_to_1_through_21_inclusive(ratio: str, out_of_range: bool) -> None:
    m = decode(
        f'{{"tokens":{{}},"roles":[{{"role":{{"tone":"Brand"}},"token":"t"}}],'
        f'"invariants":[{{"kind":"ContrastFloor","role":"Brand","minRatio":{ratio}}}]}}'
    )
    expected = ['{"code":"CONTRAST_FLOOR_OUT_OF_RANGE","index":0,"subject":"Brand"}'] if out_of_range else []
    assert _defects(m) == expected


def test_an_empty_role_is_empty_and_an_unbound_one_unbound() -> None:
    assert _defects(_invariants('{"kind":"ContrastFloor","minRatio":7}')) == [
        '{"code":"CONTRAST_FLOOR_ROLE_EMPTY","index":0,"subject":""}'
    ]
    unbound = _invariants('{"kind":"ContrastFloor","minRatio":7,"role":"body-text"}')
    assert _defects(unbound) == ['{"code":"CONTRAST_FLOOR_ROLE_UNBOUND","index":0,"subject":"body-text"}']
    bound = ThemeManifest(invariants=unbound.invariants, roles=[RoleBinding(NamedRole("body-text"), "t")])
    assert _defects(bound) == []


def test_a_budget_on_an_undeclared_token_is_reported() -> None:
    assert _defects(_invariants('{"kind":"UsageBudget","targetPct":10,"token":"color.ghost"}')) == [
        '{"code":"USAGE_BUDGET_TOKEN_UNDECLARED","index":0,"subject":"color.ghost"}'
    ]
    declared = decode(
        '{"tokens":{"color":{"ghost":{"$type":"color","$value":"#fff"}}},'
        '"invariants":[{"kind":"UsageBudget","targetPct":10,"token":"color.ghost"}]}'
    )
    assert _defects(declared) == []


def test_an_unknown_kind_is_reported_unenforced_at_its_index() -> None:
    assert _defects(_invariants('{"kind":"MotionVoice"},{"kind":"FocusRing","minWidthPx":2}')) == [
        '{"code":"INVARIANT_KIND_UNKNOWN","index":1,"subject":"FocusRing"}'
    ]


def test_defects_come_in_list_order_and_validation_never_repairs() -> None:
    src = (
        '{"tokens":{},"invariants":[{"kind":"FocusRing"},{"kind":"UsageBudget","token":"g"},{"kind":"ContrastFloor"}]}'
    )
    m = decode(src)
    assert _defects(m) == [
        '{"code":"INVARIANT_KIND_UNKNOWN","index":0,"subject":"FocusRing"}',
        '{"code":"USAGE_BUDGET_TOKEN_UNDECLARED","index":1,"subject":"g"}',
        '{"code":"CONTRAST_FLOOR_OUT_OF_RANGE","index":2,"subject":""}',
        '{"code":"CONTRAST_FLOOR_ROLE_EMPTY","index":2,"subject":""}',
    ]
    assert decode(src) == m
