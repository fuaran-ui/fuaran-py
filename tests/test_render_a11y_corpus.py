"""The a11y projection, driven by the SHARED CORPUS rather than by hand-built nodes.

``test_render_a11y_placement.py`` already asserts WHERE the projection lands, but
every node in it is authored in this repo — so it measures this host against this
host's own idea of the trait. The Phase-955 fixture family is the oracle every
host answers to: all six slots, both role classes (a named lower-case ``region``
and a deliberately-cased custom ``doc-pageFooter``), both binding forms (Static
and State), all three ``liveRegion`` tokens, and both placement shapes.

The assertions are placement-sensitive for the reason the placement suite
records: a ``role`` on a wrapper ``<div>`` is not associated by assistive
technology with the interactive element inside it, and a substring check over the
whole markup cannot tell the two apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import decode_node
from fuaran_py.renderer import render_html


@dataclass(frozen=True)
class A11yCase:
    """One fixture's expectation.

    ``element`` is ``None`` when the projection stays on the wrapper ``<div>``,
    else the tag of the semantic element the kind body renders (the D4 rule).
    """

    fixture: str
    element: str | None
    want: tuple[str, ...]
    absent_from_carrier: tuple[str, ...] = field(default=())


CASES: tuple[A11yCase, ...] = (
    # All six slots at once on an ordinary wrapper kind. `hidden` is an explicit
    # Static FALSE — distinct on the wire from omitted, and it must emit nothing
    # (`aria-hidden` is not a tri-state).
    A11yCase(
        fixture="a11y-wrapper-all-slots",
        element=None,
        want=(
            'aria-label="Channel performance summary"',
            'aria-labelledby="a11y-wrapper-heading"',
            'aria-describedby="a11y-wrapper-note"',
            'role="region"',
            'aria-live="polite"',
        ),
        absent_from_carrier=("aria-hidden",),
    ),
    # The State forms. `label` resolves through its declared `defaultValue` with
    # no host sources (the reference host's default law); the custom role's CASE
    # is carried verbatim — the exact spelling a fold bug once rewrote — and
    # `off` is a real `liveRegion` token, not an absence.
    A11yCase(
        fixture="a11y-wrapper-state-bound",
        element=None,
        want=(
            'aria-label="Site footer"',
            'role="doc-pageFooter"',
            'aria-live="off"',
        ),
        absent_from_carrier=("aria-hidden",),
    ),
    A11yCase(
        fixture="a11y-alert-assertive",
        element=None,
        want=('role="alert"', 'aria-live="assertive"'),
    ),
    # D4 forwarding: the body IS the semantic element. The accessible name
    # OVERRIDES the visible "Read more".
    A11yCase(
        fixture="a11y-link-labelled",
        element="a",
        want=('aria-label="Read the 2026 annual report (PDF)"',),
    ),
    A11yCase(
        fixture="a11y-button-named",
        element="button",
        want=('aria-label="Refresh revenue figures"', 'role="button"'),
    ),
    # The decorative shape: empty alt + `hidden` Static TRUE — the slot two hosts
    # dropped entirely before the Phase 951 port.
    A11yCase(
        fixture="a11y-image-decorative",
        element="img",
        want=('aria-hidden="true"',),
    ),
)


def _wrapper_tag(html: str) -> str:
    return html[: html.index(">") + 1]


def _open_tag(html: str, tag: str) -> str:
    frm = html[html.index(f"<{tag}") :]
    return frm[: frm.index(">") + 1]


@corpus_required
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.fixture)
def test_a11y_corpus_projection_lands_on_the_right_element(case: A11yCase) -> None:
    raw = (CORPUS_ROOT / "nodes" / f"{case.fixture}.json").read_text(encoding="utf-8")
    decoded = decode_node(raw)
    assert decoded.ok, getattr(decoded, "error", decoded)
    html = render_html(decoded.value)

    wrapper = _wrapper_tag(html)
    carrier = wrapper if case.element is None else _open_tag(html, case.element)

    for want in case.want:
        assert want in carrier, f"{case.fixture}: carrier missing {want!r}: {carrier}"
    for absent in case.absent_from_carrier:
        assert absent not in carrier, f"{case.fixture}: carrier must not emit {absent!r}: {carrier}"

    # A forwarding kind must not leave the projection behind.
    if case.element is not None:
        for want in case.want:
            attr = want.split("=", 1)[0]
            assert attr not in wrapper, f"{case.fixture}: {attr} leaked onto the wrapper: {wrapper}"

    # The wrapper keeps the node's ADDRESS whichever element carries the projection.
    assert f'data-fuaran-node-id="{decoded.value.id}"' in wrapper


@corpus_required
def test_a11y_corpus_family_is_the_full_set() -> None:
    """A table-driven leg that silently enumerated nothing checks nothing."""
    assert len(CASES) == 6, "the Phase 955 node family is six fixtures"
