"""Golden parity tests for the §-layout tier layout observer.

Mirrors the F# ``Fuaran.UI.LayoutObserver.Tests.FlagDerivationTests`` and the Rust
``introspect/layout.rs`` derivation flag-for-flag — same geometry in, same flags
out (same names, thresholds, deterministic order, encode bytes). Plus the
in-memory observer's register/observe/change-only-emission behaviour.
"""

from __future__ import annotations

import dataclasses

from fuaran_py.layout_observer import (
    DEFAULT_OPTIONS,
    AspectRatioWildlyOff,
    ChildClippedByAncestor,
    InMemoryLayoutObserver,
    LayoutInput,
    LayoutObservation,
    LayoutObserverOptions,
    OverflowHorizontal,
    OverflowVertical,
    SqueezedToMin,
    ZeroDimension,
    aspect_ratio_wildly_off,
    child_clipped_by_ancestor,
    derive,
    encode_layout_flag,
    encode_layout_observation,
    layout_input,
    overflow_horizontal,
    overflow_vertical,
    squeezed_to_min,
    to_layout_observation,
    zero_dimension,
)

_FACTOR = DEFAULT_OPTIONS.aspect_ratio_wildly_off_factor


def _with(width: float, height: float, **kw) -> LayoutInput:
    return dataclasses.replace(layout_input(width, height), **kw)


# ── Per-flag derivation rules (golden parity vs go/rs/F#) ───────────────────


def test_overflow_horizontal_fires_when_scroll_exceeds_client_and_clips() -> None:
    inp = _with(100, 50, scroll_width=250, client_width=100, overflow_x="hidden")
    assert overflow_horizontal(inp) == OverflowHorizontal()


def test_overflow_horizontal_does_not_fire_when_visible() -> None:
    inp = _with(100, 50, scroll_width=250, client_width=100, overflow_x="visible")
    assert overflow_horizontal(inp) is None


def test_overflow_vertical_fires() -> None:
    inp = _with(100, 50, scroll_height=500, client_height=50, overflow_y="scroll")
    assert overflow_vertical(inp) == OverflowVertical()


def test_zero_dimension_fires_per_collapsed_axis() -> None:
    assert zero_dimension(layout_input(0, 50)) == [ZeroDimension("width")]
    assert zero_dimension(layout_input(0, 0)) == [ZeroDimension("width"), ZeroDimension("height")]


def test_squeezed_to_min_fires_per_axis_that_hits_min() -> None:
    inp = _with(120, 80, min_width=120, min_height=200)
    assert squeezed_to_min(inp) == [SqueezedToMin("width")]


def test_child_clipped_fires_when_rect_overflows_ancestor() -> None:
    inp = _with(200, 100, element_rect=(50, 50, 250, 150), clipping_ancestor_rect=(0, 0, 200, 200))
    assert child_clipped_by_ancestor(inp) == ChildClippedByAncestor()


def test_child_clipped_does_not_fire_when_inside() -> None:
    inp = _with(100, 50, element_rect=(10, 10, 110, 60), clipping_ancestor_rect=(0, 0, 200, 200))
    assert child_clipped_by_ancestor(inp) is None


def test_aspect_ratio_fires_above_threshold() -> None:
    # Expected 16:9 (~1.778); observed 400/50 = 8.0 → magnitude 4.5x.
    inp = _with(400, 50, expected_aspect_ratio=16 / 9)
    flag = aspect_ratio_wildly_off(_FACTOR, inp)
    assert isinstance(flag, AspectRatioWildlyOff) and flag.factor >= 3.0


def test_aspect_ratio_does_not_fire_below_threshold() -> None:
    inp = _with(320, 200, expected_aspect_ratio=16 / 9)
    assert aspect_ratio_wildly_off(_FACTOR, inp) is None


def test_derive_combines_rules_in_deterministic_order() -> None:
    inp = _with(100, 0, scroll_width=250, client_width=100, overflow_x="hidden")
    assert derive(DEFAULT_OPTIONS, inp) == [OverflowHorizontal(), ZeroDimension("height")]


# ── Encode parity (byte-identical to the F# host) ───────────────────────────


def test_encode_nullary_flag() -> None:
    assert encode_layout_flag(OverflowHorizontal()) == '{"kind":"OverflowHorizontal"}'


def test_encode_axis_flag() -> None:
    assert encode_layout_flag(ZeroDimension("width")) == '{"kind":"ZeroDimension","axis":"width"}'


def test_encode_aspect_flag_two_decimals() -> None:
    assert encode_layout_flag(AspectRatioWildlyOff(3.25)) == '{"kind":"AspectRatioWildlyOff","factor":3.25}'


def test_encode_observation_camel_case() -> None:
    obs = LayoutObservation("panel-1", 120.5, 80.0, 10.0, 20.0, [OverflowHorizontal()])
    assert encode_layout_observation(obs) == (
        '{"nodeId":"panel-1","width":120.50,"height":80.00,'
        '"viewportX":10.00,"viewportY":20.00,"flags":[{"kind":"OverflowHorizontal"}]}'
    )


# ── to_layout_observation + in-memory observer behaviour ────────────────────


def test_to_observation_carries_viewport_coords_from_element_rect() -> None:
    inp = _with(120, 80, element_rect=(10, 20, 130, 100))
    obs = to_layout_observation(DEFAULT_OPTIONS, "n", inp)
    assert (obs.width, obs.height, obs.viewport_x, obs.viewport_y) == (120, 80, 10, 20)


def test_in_memory_observer_register_and_observe() -> None:
    obs = InMemoryLayoutObserver()
    emissions: list[tuple[str, list]] = []
    obs.subscribe(lambda node_id, observation: emissions.append((node_id, observation.flags)))
    obs.register_fixture("root", _with(0, 50))  # zero-width → ZeroDimension("width")
    snapshot = obs.observe("root")
    assert snapshot is not None and snapshot.flags == [ZeroDimension("width")]
    assert len(emissions) == 1  # initial emission fires unconditionally


def test_in_memory_observer_change_only_emission() -> None:
    obs = InMemoryLayoutObserver(LayoutObserverOptions(emit_on_flag_change_only=True))
    count = 0

    def on_emit(_node_id: str, _observation: LayoutObservation) -> None:
        nonlocal count
        count += 1

    obs.subscribe(on_emit)
    obs.register_fixture("n", _with(0, 50))  # 1 — initial
    obs.update("n", _with(0, 60))  # same flags (still zero-width) → no emit
    obs.update("n", _with(100, 60))  # flags cleared → emit
    assert count == 2


def test_in_memory_observer_observe_tree_bfs() -> None:
    obs = InMemoryLayoutObserver()
    obs.register_fixture("root", layout_input(100, 100))
    obs.register_fixture("child", layout_input(50, 50), parent="root")
    tree = obs.observe_tree("root")
    assert [o.node_id for o in tree] == ["root", "child"]
