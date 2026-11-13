"""Merge by fold (WIRE_FORMAT §34): the Python host reproduces the reference's artefacts.

Reads the merge-conformance manifest's ``foldFixtures`` key BY NAME. Each fixture
names a base tree and two branches' op deltas; the reconcile either derives a tree
(``merge-fold-derive``: byte-equal to ``expectedFile``, sha256 == ``outcomeHash``),
refuses (``merge-fold-refusal``: the §34.5 envelope byte-equal to ``envelopeFile``,
sha256 == ``envelopeHash``), or derives a tree the sample validator finds a defect
in that neither branch head carries (``merge-fold-gated``: the verdict byte-equal
to ``verdictFile``, sha256 == ``verdictHash``).

Two laws ride on the same fixtures: swapping the branches swaps every refusal
entry's ``left`` and ``right`` and changes nothing else, and a derivation is the
same tree whichever branch is named first.

Since fuaran#2645 ``foldFixtures`` is the manifest's only family (version 2): the
three-way tree merge and its triad tests retired with it (fuaran#2646).

Since fuaran#2705 the host certifies the four fixtures over nodes held only in a
``Mount``'s inputs (specification commit ``0b2ec20``, Phase 2701):
``fold-mount-input-insert``, ``fold-refusal-mount-input-tone``,
``fold-refusal-mount-input-disjoint`` and ``fold-refusal-mount-input-delete-modify``.
The apply lookup visits slot arguments (WIRE_FORMAT §34.2), so each delta applies
and the fold judges it; ``disjoint`` refuses as the reference draws it.

Since Phase 2718 the host certifies the unchanged-reorder fixtures (specification
commit ``5a9b4bb``, Phase 2696): ``fold-two-restated-orders`` derives, and
``fold-refusal-two-reorders`` / ``fold-refusal-two-real-reorders`` refuse. A reorder
whose list is its parent's child order reads that list and writes nothing.
"""

from __future__ import annotations

import hashlib

import pytest

from _corpus import MERGE_CORPUS_ROOT, merge_corpus_required, merge_fold_fixtures
from _merge_sample_validator import encode_verdict, introduced_defects
from fuaran_ui import decode_node, decode_op, encode_node
from fuaran_ui.footprint import CHILD_LIST_SLOT, footprint_in, footprint_of
from fuaran_ui.merge import CONCURRENT_UPDATE, INSERT_POSITION_CLASH, conflicts_in
from fuaran_ui.model import Arr, Node, Obj
from fuaran_ui.reconcile import (
    DELETE_MODIFY,
    IntroducedDefects,
    Reconciled,
    Refusal,
    Refused,
    encode_refusal_envelope,
    reconcile,
    reconcile_gated,
)


def _read(rel: str) -> str:
    text = (MERGE_CORPUS_ROOT / rel).read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


def _sha256hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _tree(rel: str) -> Node:
    result = decode_node(_read(rel))
    assert result.ok, f"decode {rel} failed: {result.error}"
    return result.value


def _ops_text(text: str) -> list[Obj]:
    # A canonical TreeOp array is exactly a Batch's `ops` member, so it decodes
    # through the one op decoder rather than a second array reader.
    result = decode_op('{"$type":"Batch","ops":' + text + "}")
    assert result.ok, f"decode ops failed: {result.error}"
    ops = result.value.fields["ops"]
    assert isinstance(ops, Arr)
    return [o for o in ops.items if isinstance(o, Obj)]


def _ops(rel: str) -> list[Obj]:
    return _ops_text(_read(rel))


def _inputs(fixture: dict) -> tuple[Node, list[Obj], list[Obj]]:
    return _tree(fixture["baseFile"]), _ops(fixture["aOpsFile"]), _ops(fixture["bOpsFile"])


def _of_kind(kind: str) -> list[dict]:
    return [fx for fx in merge_fold_fixtures() if fx["kind"] == kind]


def _id(fx: dict) -> str:
    return str(fx["id"])


# ─── the family is present and read by name ───────────────────────────────────


@merge_corpus_required
def test_fold_family_is_read_and_every_kind_is_covered() -> None:
    fixtures = merge_fold_fixtures()
    assert fixtures, "the manifest's foldFixtures key is empty or absent"
    kinds = {fx["kind"] for fx in fixtures}
    assert kinds == {"merge-fold-derive", "merge-fold-refusal", "merge-fold-gated"}, kinds


# ─── derivations ──────────────────────────────────────────────────────────────


@merge_corpus_required
@pytest.mark.parametrize("fixture", _of_kind("merge-fold-derive"), ids=_id)
def test_fold_derives_the_expected_tree(fixture: dict) -> None:
    base, a, b = _inputs(fixture)
    result = reconcile(base, a, b)
    assert isinstance(result, Reconciled), f"{fixture['id']}: {result}"
    derived = encode_node(result.tree)
    assert derived == _read(fixture["expectedFile"]), fixture["id"]
    assert _sha256hex(derived) == fixture["outcomeHash"], fixture["id"]


@merge_corpus_required
@pytest.mark.parametrize("fixture", _of_kind("merge-fold-derive"), ids=_id)
def test_fold_derivation_is_symmetric(fixture: dict) -> None:
    base, a, b = _inputs(fixture)
    forward, swapped = reconcile(base, a, b), reconcile(base, b, a)
    assert isinstance(forward, Reconciled) and isinstance(swapped, Reconciled), fixture["id"]
    assert encode_node(forward.tree) == encode_node(swapped.tree), fixture["id"]


# ─── refusals ─────────────────────────────────────────────────────────────────


def _refused(fixture: dict, swap: bool = False) -> tuple[Refusal, ...]:
    base, a, b = _inputs(fixture)
    result = reconcile(base, b, a) if swap else reconcile(base, a, b)
    assert isinstance(result, Refused), f"{fixture['id']}: expected a refusal, got {result}"
    return result.refusals


@merge_corpus_required
@pytest.mark.parametrize("fixture", _of_kind("merge-fold-refusal"), ids=_id)
def test_fold_refusal_envelope(fixture: dict) -> None:
    envelope = encode_refusal_envelope(_refused(fixture))
    assert envelope == _read(fixture["envelopeFile"]), fixture["id"]
    assert _sha256hex(envelope) == fixture["envelopeHash"], fixture["id"]


@merge_corpus_required
@pytest.mark.parametrize("fixture", _of_kind("merge-fold-refusal"), ids=_id)
def test_fold_refusal_envelope_mirrors_on_swap(fixture: dict) -> None:
    """Swapping A and C swaps every entry's ``left`` and ``right`` and re-sorts;
    nothing else changes (§34.5)."""
    forward, swapped = _refused(fixture), _refused(fixture, swap=True)
    mirrored = tuple(
        Refusal(
            type(r.conflict)(r.conflict.right, r.conflict.left, r.conflict.address, r.conflict.shape, r.conflict.slot),
            r.refusal_class,
        )
        for r in forward
    )
    assert encode_refusal_envelope(mirrored) == encode_refusal_envelope(swapped), fixture["id"]
    # Non-vacuity: a refusal whose two sides are equal would mirror to itself.
    assert any(r.conflict.left != r.conflict.right for r in forward), fixture["id"]


# ─── the validator gate ───────────────────────────────────────────────────────


@merge_corpus_required
@pytest.mark.parametrize("fixture", _of_kind("merge-fold-gated"), ids=_id)
def test_fold_gated_verdict(fixture: dict) -> None:
    base, a, b = _inputs(fixture)
    assert isinstance(reconcile(base, a, b), Reconciled), f"{fixture['id']}: the fold itself must derive"
    result = reconcile_gated(introduced_defects, base, a, b)
    assert isinstance(result, IntroducedDefects), f"{fixture['id']}: expected the gate to refuse, got {result}"
    verdict = encode_verdict(list(result.defects))
    assert verdict == _read(fixture["verdictFile"]), fixture["id"]
    assert _sha256hex(verdict) == fixture["verdictHash"], fixture["id"]


# ─── direct pins on the projection and the policy ─────────────────────────────

_BASE = (
    '{"id":"dash","kind":{"$type":"Box","children":['
    '{"id":"left","kind":{"$type":"Markdown","text":"L"}},'
    '{"id":"right","kind":{"$type":"Box","children":[{"id":"inner","kind":{"$type":"Markdown","text":"I"}}],'
    '"layout":{"$type":"Auto"},"role":"Dashboard"}}],"layout":{"$type":"Auto"},"role":"Dashboard"}}'
)


def _base() -> Node:
    result = decode_node(_BASE)
    assert result.ok, result
    return result.value


def _op(text: str) -> Obj:
    return _ops_text("[" + text + "]")[0]


def test_located_patch_style_is_slot_precise_and_the_pure_form_is_not() -> None:
    op = _op('{"$type":"PatchStyle","style":{"tone":"Brand"},"target":"left"}')
    located = footprint_in(_base(), op)
    assert located.slot_writes == {("left", "style.tone")}
    assert located.slot_reads == {("left", "style.tone"), ("left", "style")}
    assert not located.content_writes and not located.unknown_parent_writes
    pure = footprint_of(op)
    assert pure.content_writes == {"left"} and pure.unknown_parent_writes == {"left"}


def test_a_removal_content_writes_the_subtree_it_destroys_and_names_its_parent() -> None:
    fp = footprint_in(_base(), _op('{"$type":"RemoveNode","target":"right"}'))
    assert fp.content_writes == {"right", "inner"}
    assert fp.structure_writes == {"dash"} and fp.reads == {"right", "dash"}
    assert not fp.unknown_parent_writes


def test_an_edit_beneath_a_removed_ancestor_is_a_delete_modify_refusal() -> None:
    remove = _op('{"$type":"RemoveNode","target":"right"}')
    edit = _op('{"$type":"PatchStyle","style":{"tone":"Brand"},"target":"inner"}')
    result = reconcile(_base(), [remove], [edit])
    assert isinstance(result, Refused)
    assert {(r.conflict.address, r.conflict.shape, r.refusal_class) for r in result.refusals} == {
        ("inner", CONCURRENT_UPDATE, DELETE_MODIFY)
    }


def test_a_batch_is_threaded_member_by_member() -> None:
    # The second member edits the node the first one inserts: located only at the
    # tree the first member reaches, so the edit is slot-precise there.
    batch = _op(
        '{"$type":"Batch","ops":['
        '{"$type":"InsertChild","child":{"id":"n","kind":{"$type":"Markdown","text":"N"}},"parentId":"dash"},'
        '{"$type":"PatchStyle","style":{"voice":"Display"},"target":"n"}]}'
    )
    fp = footprint_in(_base(), batch)
    assert ("n", "style.voice") in fp.slot_writes
    assert "n" not in fp.unknown_parent_writes


def test_concurrent_appends_report_only_the_parent_and_interleave() -> None:
    a = _op('{"$type":"InsertChild","child":{"id":"z","kind":{"$type":"Markdown","text":"Z"}},"parentId":"dash"}')
    b = _op('{"$type":"InsertChild","child":{"id":"m","kind":{"$type":"Markdown","text":"M"}},"parentId":"dash"}')
    report = conflicts_in(_base(), [a], [b])
    assert [(c.address, c.shape) for c in report] == [("dash", INSERT_POSITION_CLASH)]
    result = reconcile(_base(), [a], [b])
    assert isinstance(result, Reconciled) and result.interleaves == 1
    # The fold order is z, m; the rule orders the appended run m, z and records it.
    assert [c.fields["newOrder"] for c in result.script] == [Arr(["left", "right", "m", "z"])]
    # Swapped, the fold order is already the rule's, and the script is recorded
    # anyway (fuaran#2645: the script fixes the order a replay reaches).
    swapped = reconcile(_base(), [b], [a])
    assert isinstance(swapped, Reconciled)
    assert [c.fields["newOrder"] for c in swapped.script] == [Arr(["left", "right", "m", "z"])]


def test_a_reorder_beside_an_append_is_rebased() -> None:
    """A reorder beside an append to its parent is a rebase (fuaran#2671): the
    reorder applies to the children the parent holds when the fold reaches it and
    the append follows, in both orders; two differing reorders still refuse."""
    reorder = _op('{"$type":"ReorderChildren","newOrder":["right","left"],"parentId":"dash"}')
    append = _op('{"$type":"InsertChild","child":{"id":"z","kind":{"$type":"Markdown","text":"Z"}},"parentId":"dash"}')
    trees = []
    for d1, d2 in (([reorder], [append]), ([append], [reorder])):
        result = reconcile(_base(), d1, d2)
        assert isinstance(result, Reconciled) and result.interleaves == 1
        trees.append(encode_node(result.tree))
    assert trees[0] == trees[1]
    derived = reconcile(_base(), [reorder], [append])
    assert isinstance(derived, Reconciled)
    assert [c.id for c in derived.tree.kind.fields["children"].items] == ["right", "left", "z"]
    other = _op('{"$type":"ReorderChildren","newOrder":["left","right"],"parentId":"dash"}')
    assert isinstance(reconcile(_base(), [reorder], [other]), Refused)


@merge_corpus_required
def test_the_unchanged_reorder_fixtures_are_certified() -> None:
    """The fixtures Phase 2696 pinned (WIRE_FORMAT §34.3, §34.6) are in the family the
    parametrised legs above run, under the kind each is certified as: two reorders that
    each restate their own order derive; a restated order beside a real reorder, and
    two real reorders, refuse."""
    kinds = {fx["id"]: fx["kind"] for fx in merge_fold_fixtures()}
    assert kinds.get("fold-two-restated-orders") == "merge-fold-derive"
    assert kinds.get("fold-refusal-two-reorders") == "merge-fold-refusal"
    assert kinds.get("fold-refusal-two-real-reorders") == "merge-fold-refusal"


def test_an_unchanged_reorder_reads_its_parent_list_and_writes_nothing() -> None:
    """A reorder whose list is the parent's child order where it applies changes
    nothing (fuaran#2696): it reads the parent's ``kind`` and child-list slots and each
    listed child's ``parent`` slot, and writes none of them. A real reorder writes both."""
    unchanged = footprint_in(_base(), _op('{"$type":"ReorderChildren","newOrder":["left","right"],"parentId":"dash"}'))
    assert not unchanged.slot_writes and not unchanged.structure_writes and not unchanged.content_writes
    assert unchanged.slot_reads == {
        ("dash", "kind"),
        ("dash", CHILD_LIST_SLOT),
        ("left", "parent"),
        ("right", "parent"),
    }
    real = footprint_in(_base(), _op('{"$type":"ReorderChildren","newOrder":["right","left"],"parentId":"dash"}'))
    assert {("dash", "kind"), ("dash", CHILD_LIST_SLOT)} <= real.slot_writes


def test_an_unchanged_reorder_still_meets_a_removal_of_a_listed_child() -> None:
    """Freeing the write must not free the reads: removing a child the unchanged
    reorder lists writes the parent's child list, which the reorder reads, so the
    conflict report still sees the pair in both orders (what the fold then does with
    it is the reconcile policy's, fuaran#2671, not the footprint's)."""
    unchanged = _op('{"$type":"ReorderChildren","newOrder":["left","right"],"parentId":"dash"}')
    remove = _op('{"$type":"RemoveNode","target":"left"}')
    assert "dash" in {c.address for c in conflicts_in(_base(), [unchanged], [remove])}
    assert "dash" in {c.address for c in conflicts_in(_base(), [remove], [unchanged])}
    # Without the reads it would not be: the same pair against a footprint that
    # dropped them is what this test exists to catch.
    restated = footprint_in(_base(), unchanged)
    assert ("dash", CHILD_LIST_SLOT) in restated.slot_reads


def test_two_reorders_that_each_restate_the_order_derive() -> None:
    """Two unchanged reorders of one parent write nothing, so nothing clashes and the
    fold derives the base order on either side."""
    restate = _op('{"$type":"ReorderChildren","newOrder":["left","right"],"parentId":"dash"}')
    result = reconcile(_base(), [restate], [restate])
    assert isinstance(result, Reconciled), result
    assert encode_node(result.tree) == encode_node(_base())


def test_the_merge_corpus_is_the_fold_family_alone() -> None:
    """The manifest is version 2: ``foldFixtures`` alone. A retired tree-merge key
    reappearing would be a family this host no longer certifies."""
    if not (MERGE_CORPUS_ROOT / "manifest.json").is_file():
        pytest.skip("merge-conformance sub-corpus not found")
    import json

    manifest = json.loads((MERGE_CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == 2
    for retired in ("fixtures", "refusalFixtures", "totalityFixtures"):
        assert retired not in manifest


_FRAGMENT_REF = (
    '{"id":"ref","kind":{"$type":"FragmentRef","args":{'
    '"content":{"$type":"SlotArg","tree":{"id":"slot-tree","kind":{"$type":"Box","children":'
    '[{"id":"inner","kind":{"$type":"Markdown","text":"Bound slot"}}],"layout":{"$type":"Auto"},"role":"Dashboard"}}},'
    '"count":{"$type":"Int","value":7}},"name":"stat-card"}}'
)


def test_an_op_reaches_a_node_held_in_a_fragment_ref_argument() -> None:
    """The lookup visits a FragmentRef's slot arguments as it visits a Mount's inputs
    (fuaran#2705, WIRE_FORMAT §34.2); the value argument holds no node and is kept
    as it was. The corpus draws the Mount shape; this pins the FragmentRef one."""
    from fuaran_ui.ops.apply import apply

    base = decode_node(_FRAGMENT_REF)
    assert base.ok, base
    insert = _op(
        '{"$type":"InsertChild","child":{"id":"added","kind":{"$type":"Markdown","text":"A"}},"parentId":"slot-tree"}'
    )
    result = apply(insert, base.value)
    assert result.ok, result
    args = result.value.kind.fields["args"]
    assert isinstance(args, Obj)
    tree = args.fields["content"]
    assert isinstance(tree, Obj) and isinstance(tree.fields["tree"], Node)
    assert [c.id for c in tree.fields["tree"].kind.fields["children"].items] == ["inner", "added"]
    assert args.fields["count"] == base.value.kind.fields["args"].fields["count"]
    removed = apply(_op('{"$type":"RemoveNode","target":"inner"}'), base.value)
    assert removed.ok, removed
    # The footprint's keyed walk reaches the same position: removing the FragmentRef
    # writes the nodes held in its argument, as removing a Mount writes its inputs'.
    wrapped = decode_node(
        '{"id":"root","kind":{"$type":"Box","children":['
        + _FRAGMENT_REF
        + '],"layout":{"$type":"Auto"},"role":"Dashboard"}}'
    )
    assert wrapped.ok, wrapped
    written = footprint_in(wrapped.value, _op('{"$type":"RemoveNode","target":"ref"}')).content_writes
    assert {"ref", "slot-tree", "inner"} <= written, written
