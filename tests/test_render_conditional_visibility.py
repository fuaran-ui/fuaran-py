"""Conditional visibility, predicate cases and the scalar selector (fuaran#1535).

Driven by the SHARED CORPUS rather than by hand-built nodes: ``node-visible``,
``switch-predicate``, ``switch-predicate-only`` and ``switch-on-transform-scalar``
are the oracle every host answers to, and rendering the same bytes here is what
makes "this host agrees with the others" a measurement rather than a claim.

The codec round-trip is covered by the conformance suite. What is asserted here
is the RENDERING — which no round-trip can see, and which is the whole of what
this phase changed on a host that already decoded these bytes fine.

One of these tests is about a defect this host carried alone: ``_switch`` read
``stateKey`` ONLY, so every Phase-768 ``on``-form switch rendered its default
here while the other four hosts selected a case. ``switch-on-transform-scalar``
is the fixture that catches it, and ``switch-on-selection`` (which predates this
phase) is the one that shows the class was wider than the computed selector.
"""

from __future__ import annotations

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import decode_node
from fuaran_py.renderer import render_html

pytestmark = corpus_required


def _render(fixture: str, sources: dict[str, object] | None = None) -> str:
    decoded = decode_node((CORPUS_ROOT / "nodes" / f"{fixture}.json").read_text(encoding="utf-8"))
    assert decoded.ok, f"{fixture} failed to decode: {decoded.error if not decoded.ok else ''}"
    return render_html(decoded.value, sources)


# ── node-visible: presence, not concealment ─────────────────────────────────


def test_resolved_false_removes_the_node_entirely() -> None:
    html = _render("node-visible", {"banner.shown": False})
    assert "Shown while the flag is set" not in html
    # Removal is not concealment: the node contributes no id and no aria-hidden.
    assert 'id="visible-state-flag"' not in html


def test_resolved_true_renders_the_node() -> None:
    assert "Shown while the flag is set" in _render("node-visible", {"banner.shown": True})


def test_unresolved_predicate_renders_the_node() -> None:
    # `visible-unresolved` reads a query result no host has furnished. Rendering
    # it is the rule: a missing source silently hiding content is the one failure
    # a reader cannot see, cannot report and cannot work around.
    assert "Rendered, because nothing could say whether to hide it" in _render("node-visible")


def test_computed_predicate_resolves_through_the_scalar_path() -> None:
    # An `Expr` over a State param, both directions from one fixture — a host
    # that ignored the predicate entirely fails one of the two.
    shown = "Shown once the basket has more than three things in it"
    assert shown in _render("node-visible", {"cart.itemCount": 9})
    assert shown not in _render("node-visible", {"cart.itemCount": 1})


def test_visible_and_aria_hidden_stay_distinct() -> None:
    # The contrast the fixture carries on sibling nodes: the aria-hidden node is
    # RENDERED and marked, where a visible:false node is not there at all.
    html = _render("node-visible", {"banner.shown": False})
    assert "aria-hidden-decoration" in html
    assert 'aria-hidden="true"' in html
    assert 'id="visible-state-flag"' not in html


def test_default_visible_spelling_needs_no_host_state() -> None:
    # `visible-until-dismissed` declares `defaultValue: true`. That spelling is
    # what an author needs, because a DEFAULT-LESS State predicate follows the
    # shared `Binding.State` rule and resolves false — the FUARAN148 shape.
    assert "Shown until the reader dismisses it" in _render("node-visible")
    assert "Shown until the reader dismisses it" not in _render("node-visible", {"banner.dismissed": False})


# ── switch-predicate: first-match-wins over a mixed case list ───────────────


def test_predicate_case_is_taken_on_a_resolved_true() -> None:
    assert "Your basket is empty" in _render("switch-predicate", {"cart.empty": True})


def test_first_match_wins_runs_over_the_authored_order() -> None:
    # The fixture's two predicate cases precede its `match` case. With BOTH the
    # first predicate true and the selector equal to the match value, a host that
    # checked matches ahead of predicates would render 'Summary view'.
    html = _render("switch-predicate", {"cart.empty": True, "view": "summary"})
    assert "Your basket is empty" in html
    assert "Summary view" not in html


def test_a_match_case_still_wins_once_the_predicates_decline() -> None:
    html = _render("switch-predicate", {"cart.empty": False, "cart.itemCount": 1, "view": "summary"})
    assert "Summary view" in html


def test_nothing_selecting_falls_through_to_the_default() -> None:
    html = _render("switch-predicate", {"cart.empty": False, "cart.itemCount": 1, "view": "no-such-case"})
    assert "A few things in your basket" in html


@pytest.mark.parametrize(("valid", "expected"), [(True, "Ready to send"), (False, "Fill in the form to continue")])
def test_a_switch_with_no_selector_selects_by_predicate_alone(valid: bool, expected: str) -> None:
    assert expected in _render("switch-predicate-only", {"form.valid": valid})


# ── the wire-neutral fix ────────────────────────────────────────────────────


def test_a_computed_selector_selects_the_matching_case() -> None:
    # Before fuaran#1535 this rendered `Default` here for TWO reasons at once:
    # `_switch` read `stateKey` only, and the generic resolver's `Transform` arm
    # is row-shaped. The default is asserted ABSENT as well as the case present,
    # so a host rendering both would fail rather than satisfy the first check.
    html = _render("switch-on-transform-scalar")
    assert "Plenty of rows" in html
    assert "Could not tell" not in html


def test_a_selection_selector_selects_a_case() -> None:
    # The wider half of the same defect: `switch-on-selection` predates this
    # phase, and this host rendered its default for every value of the selection
    # because it never read `on` at all. The fixture's selector declares
    # `defaultValue: "steady"`, so the unwritten case is genuinely the default
    # branch — the assertion that shows `on` is read at all is the WRITTEN one.
    assert "Escalate admissions" in _render("switch-on-selection", {"ward-grid": "critical"})
    assert "Occupancy within normal range" in _render("switch-on-selection")
