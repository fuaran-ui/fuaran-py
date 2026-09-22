"""Style-observer pure tier + InMemory observer + byte-identical encode parity.

**The encode literals here are the GO-RED PARTNER of the corpus family, not a
duplicate of it** (Phase 1752). ``test_style_observer_corpus.py`` certifies this
host against ``style-observer/`` in the shared conformance corpus, whose bytes
are written by the reference host's emitter. These are written by hand. Keeping
both is the point: a regression that moved the implementation AND the emitted
family together would satisfy the corpus checker and fail here, which is exactly
the failure mode a family emitted from the thing it certifies cannot see on its
own. Do not delete them as redundant.

The observer-plumbing tests below (subscription, change-only emission, BFS tree
order, callback isolation) are deliberately NOT in the family: they are in-memory
behaviour, and the corpus certifies bytes.
"""

from __future__ import annotations

from fuaran_ui.style_observer import (
    BLACK,
    DEFAULT_OPTIONS,
    TRANSPARENT,
    WHITE,
    AccentIndistinct,
    ContrastBelowAA,
    ContrastBelowDeclaredFloor,
    FontRole,
    InMemoryStyleObserver,
    InvisibleText,
    OffPaletteColour,
    StyleInput,
    StyleObservation,
    TokenResolutionFailed,
    UsageBudgetExceeded,
    accent_indistinct,
    contrast_below_aa,
    contrast_ratio,
    derive_style_flags,
    effective_background,
    encode_rgba,
    encode_style_flag,
    encode_style_observation,
    font_role,
    invisible_text,
    rgb,
    rgba,
    same_rgb,
    to_style_observation,
    verify_usage_budgets,
)
from fuaran_ui.theme_manifest import decode as decode_manifest


def _input(**over: object) -> StyleInput:
    base: dict[str, object] = {
        "foreground": BLACK,
        "background_layers": [],
        "font_family": None,
        "emitted_tone": None,
    }
    base.update(over)
    return StyleInput(**base)  # type: ignore[arg-type]


def test_rgba_primitives() -> None:
    assert same_rgb(rgb(255, 128, 0), rgba(255.4, 127.6, 0.2, 0.3))
    assert not same_rgb(rgb(255, 128, 0), rgb(254, 128, 0))
    assert encode_rgba(WHITE) == '{"r":255.00,"g":255.00,"b":255.00,"a":1.00}'
    assert encode_rgba(TRANSPARENT) == '{"r":0.00,"g":0.00,"b":0.00,"a":0.00}'


def test_compositing_and_contrast() -> None:
    assert effective_background([]) == WHITE
    assert effective_background([TRANSPARENT, WHITE]) == WHITE
    assert effective_background([rgb(10, 20, 30), rgb(99, 99, 99)]) == rgb(10, 20, 30)
    assert round(contrast_ratio(BLACK, WHITE), 2) == 21.0
    assert round(contrast_ratio(WHITE, WHITE), 2) == 1.0


def test_font_role() -> None:
    assert font_role(_input(font_family="ui-monospace, Menlo")) is FontRole.MONOSPACE
    assert font_role(_input(font_family="Inter, sans-serif")) is FontRole.SANS_SERIF
    assert font_role(_input(font_family="Georgia, serif")) is FontRole.SERIF
    assert font_role(_input(font_family="Wingdings")) is FontRole.UNKNOWN
    assert font_role(_input()) is FontRole.UNKNOWN


def test_predicates() -> None:
    opts = DEFAULT_OPTIONS
    assert invisible_text(
        opts.invisible_text_threshold, _input(foreground=WHITE, background_layers=[WHITE])
    ) == InvisibleText(1.0)
    assert invisible_text(opts.invisible_text_threshold, _input(foreground=BLACK, background_layers=[WHITE])) is None
    aa = contrast_below_aa(
        opts.invisible_text_threshold,
        opts.contrast_aa_threshold,
        _input(foreground=rgb(150, 150, 150), background_layers=[WHITE]),
    )
    assert isinstance(aa, ContrastBelowAA)
    assert opts.invisible_text_threshold <= aa.ratio < opts.contrast_aa_threshold
    accent = accent_indistinct(
        opts.accent_indistinct_threshold, _input(emitted_tone="brand", background_layers=[rgb(240, 240, 240), WHITE])
    )
    assert isinstance(accent, AccentIndistinct)
    assert (
        accent_indistinct(opts.accent_indistinct_threshold, _input(background_layers=[rgb(240, 240, 240), WHITE]))
        is None
    )


def test_derive_partitions_contrast_axis() -> None:
    flags = derive_style_flags(DEFAULT_OPTIONS, _input(foreground=WHITE, background_layers=[WHITE]))
    assert [type(f).__name__ for f in flags] == ["InvisibleText"]
    combined = derive_style_flags(
        DEFAULT_OPTIONS, _input(foreground=WHITE, emitted_tone="brand", background_layers=[WHITE, WHITE])
    )
    assert [type(f).__name__ for f in combined] == ["InvisibleText", "AccentIndistinct"]
    assert derive_style_flags(DEFAULT_OPTIONS, _input(foreground=BLACK, background_layers=[WHITE])) == []


def test_encode_flags_byte_identical() -> None:
    assert encode_style_flag(ContrastBelowAA(3.21)) == '{"kind":"ContrastBelowAA","ratio":3.21}'
    assert encode_style_flag(InvisibleText(1.02)) == '{"kind":"InvisibleText","ratio":1.02}'
    assert encode_style_flag(AccentIndistinct(2.5)) == '{"kind":"AccentIndistinct","ratio":2.50}'
    assert encode_style_flag(TokenResolutionFailed("Brand")) == '{"kind":"TokenResolutionFailed","slot":"Brand"}'
    assert encode_style_flag(OffPaletteColour("rgb(1, 2, 3)")) == '{"kind":"OffPaletteColour","value":"rgb(1, 2, 3)"}'
    assert (
        encode_style_flag(UsageBudgetExceeded("color.brand.base", 9.0, 28.0))
        == '{"kind":"UsageBudgetExceeded","token":"color.brand.base","declaredPct":9.00,"observedPct":28.00}'
    )
    assert (
        encode_style_flag(ContrastBelowDeclaredFloor("Brand", 5.0, 7.0))
        == '{"kind":"ContrastBelowDeclaredFloor","role":"Brand","ratio":5.00,"floor":7.00}'
    )


def test_encode_observation_byte_identical() -> None:
    obs = to_style_observation(DEFAULT_OPTIONS, "node-1", _input(foreground=BLACK, background_layers=[WHITE]))
    assert encode_style_observation(obs) == (
        '{"nodeId":"node-1","foreground":{"r":0.00,"g":0.00,"b":0.00,"a":1.00},'
        '"effectiveBackground":{"r":255.00,"g":255.00,"b":255.00,"a":1.00},'
        '"fontRole":"Unknown","emittedTone":null,"contrastRatio":21.00,"flags":[]}'
    )
    toned = to_style_observation(
        DEFAULT_OPTIONS, "n2", _input(foreground=BLACK, background_layers=[WHITE], emitted_tone="brand")
    )
    assert '"emittedTone":"brand"' in encode_style_observation(toned)


def _invisible() -> StyleInput:
    return _input(foreground=WHITE, background_layers=[WHITE])


def _legible() -> StyleInput:
    return _input(foreground=BLACK, background_layers=[WHITE])


def test_inmemory_observer() -> None:
    obs = InMemoryStyleObserver()
    obs.register_fixture("a", _invisible())
    snap = obs.observe("a")
    assert snap is not None and [type(f).__name__ for f in snap.flags] == ["InvisibleText"]
    assert obs.observe("missing") is None


def test_inmemory_change_only_emission() -> None:
    obs = InMemoryStyleObserver()
    obs.register_fixture("a", _legible())
    emissions: list[StyleObservation] = []
    obs.subscribe(lambda _nid, o: emissions.append(o))
    obs.update("a", _legible())
    assert emissions == []
    obs.update("a", _invisible())
    assert len(emissions) == 1
    obs.update("a", _invisible())
    assert len(emissions) == 1


def test_inmemory_observe_tree_bfs() -> None:
    obs = InMemoryStyleObserver()
    obs.register_fixture("root", _legible())
    obs.register_fixture("a", _legible(), parent="root")
    obs.register_fixture("b", _legible(), parent="root")
    obs.register_fixture("a1", _legible(), parent="a")
    assert [o.node_id for o in obs.observe_tree("root")] == ["root", "a", "b", "a1"]
    assert obs.observe_tree("unknown") == []


def test_inmemory_unsubscribe_and_baseline_and_isolation() -> None:
    obs = InMemoryStyleObserver()
    count = 0

    def inc(_nid: str, _o: StyleObservation) -> None:
        nonlocal count
        count += 1

    unsub = obs.subscribe(inc)
    obs.register_fixture("a", _invisible())
    unsub()
    obs.update("a", _legible())
    assert count == 1

    obs.register("baseline")
    base = obs.observe("baseline")
    assert base is not None and base.flags == [] and round(base.contrast_ratio, 2) == 21.0
    obs.unregister("baseline")
    assert obs.observe("baseline") is None

    reached = False

    def boom(_nid: str, _o: StyleObservation) -> None:
        raise RuntimeError("boom")

    def ok(_nid: str, _o: StyleObservation) -> None:
        nonlocal reached
        reached = True

    obs.subscribe(boom)
    obs.subscribe(ok)
    obs.register_fixture("c", _invisible())
    assert reached


# ── Phase 1727 — the palette-attribution tie-break ───────────────────────────


def _budget_obs(node_id: str, bg: object) -> StyleObservation:
    return StyleObservation(
        node_id=node_id,
        foreground=BLACK,
        effective_background=bg,  # type: ignore[arg-type]
        font_role=FontRole.UNKNOWN,
        emitted_tone=None,
        contrast_ratio=21.0,
        flags=[],
    )


def test_same_valued_tokens_attribute_to_the_path_first_token() -> None:
    """Two colour tokens carry the same value, declared secondary-before-brand.
    The decoder keeps DOCUMENT order (pinned below, so a change there is seen);
    attribution does not follow it — the 60px² fill goes to ``color.brand``, the
    first by canonical token-path order, so its budget breaches at 60% and
    ``color.secondary``'s 0% ± 5% budget stays silent. A document-order host
    reports two breaches here, which is the divergence this test exists to
    keep red. The corpus vector of the same name is the cross-host law; this is
    its go-red partner."""
    manifest = decode_manifest(
        '{"meta":{"name":"t","version":"1"},"tokens":{"color":{'
        '"secondary":{"$type":"color","$value":"#010203"},'
        '"brand":{"$type":"color","$value":"#010203"}}},"roles":[],"invariants":['
        '{"kind":"UsageBudget","token":"color.brand","targetPct":10,"tolerancePct":5},'
        '{"kind":"UsageBudget","token":"color.secondary","targetPct":0,"tolerancePct":5}]}'
    )
    assert [t.name for t in manifest.tokens] == ["color.secondary", "color.brand"]
    nodes = [(_budget_obs("a", rgb(1, 2, 3)), 60.0), (_budget_obs("b", rgb(9, 9, 9)), 40.0)]
    assert [encode_style_flag(f) for f in verify_usage_budgets(manifest, nodes)] == [
        '{"kind":"UsageBudgetExceeded","token":"color.brand","declaredPct":10.00,"observedPct":60.00}'
    ]


def test_token_path_order_is_segment_wise_not_a_string_sort() -> None:
    """``color.brand.base`` precedes ``color.brand-alt`` because the key ``brand``
    precedes ``brand-alt`` — although ``-`` sorts before ``.`` as a character, so a
    sort of the joined path would put ``brand-alt`` first and diverge from every
    host that sorts per group."""
    manifest = decode_manifest(
        '{"meta":{"name":"t","version":"1"},"tokens":{"color":{'
        '"brand-alt":{"$type":"color","$value":"#010203"},'
        '"brand":{"base":{"$type":"color","$value":"#010203"}}}},"roles":[],"invariants":['
        '{"kind":"UsageBudget","token":"color.brand.base","targetPct":10,"tolerancePct":5},'
        '{"kind":"UsageBudget","token":"color.brand-alt","targetPct":0,"tolerancePct":5}]}'
    )
    assert sorted(t.name for t in manifest.tokens) == ["color.brand-alt", "color.brand.base"]
    nodes = [(_budget_obs("a", rgb(1, 2, 3)), 60.0), (_budget_obs("b", rgb(9, 9, 9)), 40.0)]
    assert [encode_style_flag(f) for f in verify_usage_budgets(manifest, nodes)] == [
        '{"kind":"UsageBudgetExceeded","token":"color.brand.base","declaredPct":10.00,"observedPct":60.00}'
    ]
