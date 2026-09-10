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

import json
from dataclasses import dataclass, field

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import decode_node
from fuaran_py.renderer import render_html


@dataclass(frozen=True)
class A11yCase:
    """One fixture's expectation, DERIVED from the corpus's own a11y contract.

    Phase 1665 — this table used to be hand-written here, and the same table was
    hand-written again in four sibling hosts. Five copies of one cross-host claim
    is exactly the arrangement that let ``accessibility.label`` resolve five
    different ways with every conformance gate green: each host measured itself
    against its own idea of the trait, and no copy could contradict another. The
    claim now lives once, in ``a11y-contract.json``'s ``behaviour`` section, and
    every host reads it.

    What stays host-local is the one thing the contract deliberately does not
    state: which ELEMENT this host renders for a forwarding kind. The contract
    says the projection FORWARDS (the D4 predicate, host-neutral); ``element`` is
    this renderer's own answer, given in ``_FORWARDING_TAG`` below.
    """

    fixture: str
    element: str | None
    want: tuple[str, ...]
    absent_from_carrier: tuple[str, ...] = field(default=())


#: The six attribute names the accessibility projection can emit, in the wire's
#: slot order. The complement of a vector's own list is what that vector forbids:
#: the contract declares its attribute list EXHAUSTIVE for the projection.
_PROJECTION_ATTRIBUTES = (
    "aria-label",
    "aria-labelledby",
    "aria-describedby",
    "role",
    "aria-live",
    "aria-hidden",
)

#: The element THIS host's body renders for each forwarding fixture's kind — the
#: host-local half of a contract vector. A forwarding vector with no entry here
#: raises rather than falling back to the wrapper: a silent fallback would assert
#: the projection landed where the contract says it must not.
_FORWARDING_TAG = {
    "a11y-link-labelled": "a",
    "a11y-button-named": "button",
    "a11y-image-decorative": "img",
}


def _read_contract_vectors() -> tuple[A11yCase, ...]:
    """The contract's behaviour vectors as cases. Empty tuple with no corpus —
    the leg below turns that into a skip rather than a vacuous pass."""
    path = CORPUS_ROOT / "a11y-contract.json"
    if not path.is_file():
        return ()
    contract = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for vector in contract["behaviour"]["vectors"]:
        fixture = vector["fixture"]
        names = {name for name, _ in vector["attributes"]}
        element = None
        if vector["forwards"]:
            element = _FORWARDING_TAG.get(fixture)
            if element is None:
                raise AssertionError(
                    f"{fixture}: the contract says the projection forwards, and this host has not "
                    "said which element it renders for that kind - add it to _FORWARDING_TAG"
                )
        cases.append(
            A11yCase(
                fixture=fixture,
                element=element,
                want=tuple(f'{name}="{value}"' for name, value in vector["attributes"]),
                absent_from_carrier=tuple(n for n in _PROJECTION_ATTRIBUTES if n not in names),
            )
        )
    return tuple(cases)


CASES: tuple[A11yCase, ...] = _read_contract_vectors()


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
def test_a11y_contract_vectors_cover_the_transform_bound_name() -> None:
    """A table-driven leg that silently enumerated nothing checks nothing — and
    since Phase 1665 the table is READ rather than written here, so an empty one
    is also what a mis-shaped contract looks like. Both are refused. The count is
    not restated: the contract is the enumeration, exactly as ``manifest.json``
    is for the fixtures."""
    assert CASES, "a11y-contract.json's behaviour.vectors must enumerate the a11y fixture family"
    assert "a11y-wrapper-transform-label" in {c.fixture for c in CASES}, (
        "the contract must carry the Phase 1665 vector — the Transform-bound accessible name is "
        "the one every host resolved through its row-shaped generic path"
    )
