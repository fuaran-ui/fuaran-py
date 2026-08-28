"""``Binding.State`` slot seeding — ``WIRE_FORMAT.md`` §24.4, and its §24.6
conformance leg.

§24.6 is a RENDER-parity obligation, not a codec one: the bytes round-trip
identically with or without the rule, so every codec family in this suite passes
on a host that has not adopted it. That is exactly why this module asserts a
DERIVED VALUE rather than bytes — it is the only leg that can tell an adopting
host from a non-adopting one.

Measured on this host before the pass landed: `nodes/shared-source-seeded-pair`
did not decode at all (the badge's ``"defaultValue": []`` source reached the
columnar codec as a bare array), and once it did, the badge rendered EMPTY
because nothing filled the slot the grid beside it carries the rows for. After:
``2``, the value the two reference tiers pin for this fixture.

Every rule below carries its deliberately mis-seeded case alongside the correct
one: a rule asserted only in its passing direction cannot tell a working
implementation from an absent one.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import decode_node
from fuaran_py.model import Arr, Node, Obj
from fuaran_py.renderer import collect_state_seeds, render_html, with_state_seeds

SEEDED_PAIR = "shared-source-seeded-pair"
_BADGE_OPEN = 'class="fuaran-badge fuaran-badge-info">'


def badge_text(html: str) -> str:
    """The Info badge's rendered text. Narrow on purpose: it matches the emitted
    element and nothing else, so a renderer that stopped emitting the badge
    fails rather than matching some other span."""
    i = html.find(_BADGE_OPEN)
    assert i >= 0, f"no Info badge in the rendered fragment: {html}"
    return html[i + len(_BADGE_OPEN) :].split("<", 1)[0]


def decode(doc: str) -> Node:
    result = decode_node(doc)
    assert result.ok, f"decode failed: {result.error}"
    return result.value


def metric(node_id: str, key: str, declaration: str) -> str:
    return (
        f'{{"id":"{node_id}","kind":{{"$type":"Metric","label":"L",'
        f'"value":{{"$type":"State",{declaration}"key":"{key}"}}}}}}'
    )


def box(*children: str) -> str:
    return (
        '{"id":"root","kind":{"$type":"Box","children":['
        + ",".join(children)
        + '],"layout":{"$type":"Auto"},"role":"Dashboard"}}'
    )


def seed_of(doc: str, key: str) -> tuple[object, bool]:
    seeds = collect_state_seeds(decode(doc))
    return seeds.get(key), key in seeds


@pytest.fixture
def seeded_pair() -> Node:
    path = CORPUS_ROOT / "nodes" / f"{SEEDED_PAIR}.json"
    return decode(path.read_text(encoding="utf-8"))


# ── §24.6 — the render-parity assertion ──────────────────────────────────────


@corpus_required
def test_seeded_pair_renders_the_declared_count(seeded_pair: Node) -> None:
    """One declared table under ``$state.members``, read by a grid's ``source``
    and by a badge's ``Transform``, resolves the badge's derivation over the
    grid's two rows.

    The VALUE is the assertion, not the markup: ``2`` is what the reference
    tiers render for this fixture, so a host that agrees on the bytes and
    disagrees here is exactly the divergence §24.4 was written to close.
    """
    assert badge_text(render_html(seeded_pair)) == "2"


@corpus_required
def test_the_parity_assertion_is_sensitive_to_the_derived_value(seeded_pair: Node) -> None:
    """The go-red half of the assertion above.

    An assertion nobody has watched fail is a claim about the author's
    confidence, not about the renderer — so the same badge is measured under a
    HOST value that makes the derivation say something else, and it must move.
    Both perturbations are legitimate documents; neither changes a byte of the
    tree.
    """
    one_row = Arr([Obj(None, {"team": "Solo"})])
    assert badge_text(render_html(seeded_pair, {"members": one_row})) == "1"
    assert badge_text(render_html(seeded_pair, {"members": Arr([])})) != "2"


@corpus_required
def test_a_host_store_in_either_representation_reaches_the_evaluator(seeded_pair: Node) -> None:
    """The store legitimately holds values in EITHER representation, and the
    seeded pair is what made that a primary path rather than a corner.

    A host that hands the renderer parsed JSON puts ``list``/``dict`` in the
    store; the tree's own values — a ``defaultValue`` returned by
    ``resolve_binding``, and now a SEED — are structural ``Arr``/``Obj``. Before
    seeding, only a host could put a structural value there; now the seeding
    pass does it on every render, so the two must agree. Measured, not reasoned:
    the first run of the assertion above raised ``TypeError: Object of type Arr
    is not JSON serializable`` out of the compute evaluator.
    """
    structural = Arr([Obj(None, {"team": "A"}), Obj(None, {"team": "B"}), Obj(None, {"team": "C"})])
    parsed = json.loads('[{"team":"A"},{"team":"B"},{"team":"C"}]')
    assert badge_text(render_html(seeded_pair, {"members": structural})) == "3"
    assert badge_text(render_html(seeded_pair, {"members": parsed})) == "3"


# ── §24.4 — the five rules ───────────────────────────────────────────────────


def test_rule1_a_present_default_declares_and_an_absent_one_does_not() -> None:
    """Rule 1 — WHO DECLARES: any ``Binding.State`` with a PRESENT
    ``defaultValue``, in any slot."""
    value, present = seed_of(metric("m", "users", '"defaultValue":7,'), "users")
    assert present and value == 7

    _, present = seed_of(metric("m", "users", ""), "users")
    assert not present, "a State carrying NO defaultValue seeded its slot"


def test_rule2_the_host_value_wins_over_the_seed() -> None:
    """Rule 2 — PRECEDENCE: host value > written value > seed. This host holds
    no written values, so the pair that matters is host vs seed."""
    tree = decode(metric("m", "users", '"defaultValue":7,'))

    merged = with_state_seeds(tree, {"users": 99})
    assert merged == {"users": 99}, "the seed overrode the host's own value"

    assert with_state_seeds(tree, None) == {"users": 7}, "the seed did not reach a caller that named nothing"

    # The caller's own mapping is never mutated: a host may reuse one across
    # renders, and a pass that wrote into it would leak the first tree's
    # declarations into the second tree's render.
    callers: dict[str, object] = {}
    with_state_seeds(tree, callers)
    assert callers == {}, "the caller's sources mapping was mutated"


def test_rule3_document_order_carries_no_meaning() -> None:
    """Rule 3 — ORDER-INDEPENDENCE: seeding runs over the WHOLE tree before any
    binding resolves, so a reader that appears before the declaration is not a
    special case."""
    declaring = metric("declares", "users", '"defaultValue":7,')
    reading = metric("reads", "users", "")

    assert seed_of(box(declaring, reading), "users") == seed_of(box(reading, declaring), "users")
    assert seed_of(box(reading, declaring), "users") == (7, True)


def test_rule4_the_first_declaration_wins() -> None:
    """Rule 4 — TWO DECLARATIONS OF ONE KEY. A disagreement is ``FUARAN106``'s
    to name; a renderer must still be deterministic and takes the FIRST in tree
    order."""
    first = metric("first", "k", '"defaultValue":1,')
    second = metric("second", "k", '"defaultValue":2,')

    assert seed_of(box(first, second), "k") == (1, True)
    assert seed_of(box(second, first), "k") == (2, True), "reversing the pair did not reverse the winner"


def test_rule4_an_empty_declaration_declares_nothing() -> None:
    """Rule 4, second half. The empty declaration must not WIN the race, or a
    badge spelling ``"defaultValue": []`` before the grid that carries the rows
    would seed the slot EMPTY and make rule 3 false — and it must not CONFLICT,
    or that same pair would raise ``FUARAN106`` on the very document the seeding
    rule exists to make work."""
    empty = metric("empty", "rows", '"defaultValue":[],')
    carrying = (
        '{"id":"g","kind":{"$type":"DataGrid","columns":[{"field":"team",'
        '"kind":{"$type":"Text"},"label":"Team"}],"rowKeyField":"team",'
        '"source":{"$type":"State","defaultValue":[{"team":"Ops"}],"key":"rows"}}}'
    )

    value, present = seed_of(box(empty, carrying), "rows")
    assert present, "an empty declaration ahead of a carrying one left the slot unseeded"
    assert value == Arr([Obj(None, {"team": "Ops"})])

    _, present = seed_of(box(empty), "rows")
    assert not present, "an empty declaration seeded its slot on its own"


def test_rule5_a_host_reserved_key_is_never_seeded() -> None:
    """Rule 5 — a seed is a tree-originated write, and §12's reserved ``host.``
    namespace refuses those on every path; the wire must not gain a way around a
    deliberate floor."""
    _, present = seed_of(metric("m", "host.users", '"defaultValue":7,'), "host.users")
    assert not present, "a host-reserved key was seeded from the tree"

    # The identical declaration on an ordinary key DOES seed, so the assertion
    # above is measuring the prefix rather than a broken walk.
    _, present = seed_of(metric("m", "users", '"defaultValue":7,'), "users")
    assert present, "the control declaration did not seed — rule 5's evidence is vacuous"
