"""Style-observer pure tier + InMemory observer + byte-identical encode parity."""

from __future__ import annotations

from fuaran_py.style_observer import (
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
)


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
