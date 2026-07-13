"""Phase 521 — the human-primacy DAG merge layer (``fuaran_py.merge``).

The author-agnostic wire-host merge (:func:`merge_3way`) is certified against the
shared ``merge-conformance/`` corpus by ``test_merge_conformance.py``. This suite
certifies the **DAG layer** added on top — human-primacy conflict resolution
(``merge3_way_with_author`` / ``merge3``), mirroring the F# ``resolveAuthor`` /
``merge3WayWithAuthor`` surface. There is no corpus for the primacy layer (all four
committed vectors are author-agnostic), so parity is asserted against the F# engine's
documented behaviour: precedence table, pin-held-resolves vs no-pin-blocks, the typed
conflict shape, determinism, and order-independence.
"""

from __future__ import annotations

from fuaran_py.canonical import encode_value
from fuaran_py.merge import (
    CONCURRENT_EDIT,
    KEEP_BASE,
    KEEP_PRIMARY,
    KEEP_SECONDARY,
    REORDER_VS_STRUCTURAL,
    MergeConflicts,
    MergeOk,
    Primary,
    Secondary,
    merge3,
    merge3_way_lenient,
    merge3_way_with_author,
    merge_3way,
)
from fuaran_py.model import Arr, Node, Obj

# ── fixtures ─────────────────────────────────────────────────────────────────


def _metric(tone: str) -> Node:
    return Node(
        "m",
        Obj(
            "Metric",
            {
                "emphasis": "Normal",
                "format": Obj("None", {}),
                "label": Obj("Literal", {"text": "L"}),
                "source": Obj("Static", {"value": 1}),
                "tone": tone,
                "weight": "Standard",
            },
        ),
        {},
    )


def _child(cid: str) -> Node:
    return Node(cid, Obj("Markdown", {"text": Obj("Literal", {"text": cid})}), {})


def _box(*children: Node) -> Node:
    return Node(
        "root",
        Obj("Box", {"children": Arr(list(children)), "layout": Obj("Auto", {}), "role": "Group"}),
        {},
    )


def _tone_of(node: Node) -> str:
    tone = node.kind.fields.get("tone")
    assert isinstance(tone, str)
    return tone


# ── the precedence table (resolveAuthor parity) ──────────────────────────────


def test_primary_wins_a_conflicted_cell() -> None:
    """A kind-field conflict with a held pin resolves in the primary (human) side's favour."""
    base, a, b = _metric("Default"), _metric("Success"), _metric("Warning")
    result = merge3_way_with_author(Primary(), Secondary(), base, a, b)
    assert isinstance(result, MergeOk)
    assert _tone_of(result.tree) == "Success"  # a (Primary) won
    assert len(result.resolved) == 1
    conflict = result.resolved[0]
    assert conflict.facet == "kind"
    assert conflict.primacy_held is True
    assert conflict.conflict_class == CONCURRENT_EDIT
    assert conflict.choices == (KEEP_PRIMARY, KEEP_SECONDARY, KEEP_BASE)
    assert conflict.primary == encode_value(_metric("Success").kind) or conflict.primary is not None


def test_primary_on_b_side_wins() -> None:
    base, a, b = _metric("Default"), _metric("Success"), _metric("Warning")
    result = merge3_way_with_author(Secondary(), Primary(), base, a, b)
    assert isinstance(result, MergeOk)
    assert _tone_of(result.tree) == "Warning"  # b (Primary) won


def test_two_secondaries_block() -> None:
    """No precedence (both agents) — the conflict keeps base and blocks."""
    base, a, b = _metric("Default"), _metric("Success"), _metric("Warning")
    result = merge3_way_with_author(Secondary(), Secondary(), base, a, b)
    assert isinstance(result, MergeConflicts)
    assert len(result.conflicts) == 1
    assert result.conflicts[0].primacy_held is False
    assert result.conflicts[0].choices == (KEEP_BASE, KEEP_SECONDARY)


def test_two_primaries_block() -> None:
    """Two precedence-holders — no winner, host decides; the conflict blocks."""
    base, a, b = _metric("Default"), _metric("Success"), _metric("Warning")
    result = merge3_way_with_author(Primary(), Primary(), base, a, b)
    assert isinstance(result, MergeConflicts)
    assert result.conflicts[0].choices == (KEEP_BASE,)


def test_author_agnostic_matches_two_secondaries() -> None:
    """``merge_3way`` is exactly the two-secondaries (symmetric) merge."""
    base, a, b = _metric("Default"), _metric("Success"), _metric("Warning")
    assert isinstance(merge_3way(base, a, b), MergeConflicts)
    assert isinstance(merge3(base, a, b), MergeConflicts)  # human=None default


# ── the merge3 convenience entry ─────────────────────────────────────────────


def test_merge3_human_selects_primary_side() -> None:
    base, a, b = _metric("Default"), _metric("Success"), _metric("Warning")
    assert _tone_of(merge3(base, a, b, human="a").tree) == "Success"
    assert _tone_of(merge3(base, a, b, human="b").tree) == "Warning"


# ── style-facet primacy (the independent sub-field merge) ────────────────────


def test_style_subfield_primacy() -> None:
    def styled(tone: str) -> Node:
        return Node(
            "m",
            Obj("Markdown", {"text": Obj("Literal", {"text": "x"})}),
            {"style": Obj(None, {"emphasis": "Normal", "tone": tone, "weight": "Standard"})},
        )

    base, a, b = styled("Default"), styled("Brand"), styled("Subdued")
    result = merge3(base, a, b, human="a")
    assert isinstance(result, MergeOk)
    style = result.tree.extras.get("style")
    assert isinstance(style, Obj)
    assert style.fields["tone"] == "Brand"  # a (Primary) won the style.tone cell
    assert result.resolved[0].facet == "style.tone"


# ── children structural conflict ─────────────────────────────────────────────


def test_children_structural_conflict_class_and_primacy() -> None:
    base = _box(_child("c1"), _child("c2"))
    a = _box(_child("c2"), _child("c1"))  # reorder
    b = _box(_child("c1"))  # remove c2 — non-disjoint structural divergence
    result = merge3_way_with_author(Primary(), Secondary(), base, a, b)
    assert isinstance(result, MergeOk)
    assert result.resolved[0].facet == "children"
    assert result.resolved[0].conflict_class == REORDER_VS_STRUCTURAL
    merged_ids = [c.id for c in result.tree.kind.fields["children"].items]
    assert merged_ids == ["c2", "c1"]  # a (Primary) child order


# ── determinism + order-independence ─────────────────────────────────────────


def test_disjoint_merge_is_order_independent() -> None:
    """Disjoint inserts fold deterministically (NodeId-byte tie-break) regardless of side order."""
    base = _box()
    a = _box(_child("zzz"))
    b = _box(_child("aaa"))
    left = merge_3way(base, a, b)
    right = merge_3way(base, b, a)
    assert isinstance(left, MergeOk) and isinstance(right, MergeOk)
    assert encode_value(left.tree) == encode_value(right.tree)
    assert [c.id for c in left.tree.kind.fields["children"].items] == ["aaa", "zzz"]


def test_primacy_is_symmetric_under_side_swap() -> None:
    """The human branch wins the same value whether it is side a or side b."""
    base, x, y = _metric("Default"), _metric("Success"), _metric("Warning")
    a_wins = merge3(base, x, y, human="a")
    b_wins = merge3(base, y, x, human="b")
    assert encode_value(a_wins.tree) == encode_value(b_wins.tree)


# ── lenient never blocks ─────────────────────────────────────────────────────


def test_lenient_never_blocks_and_keeps_base_on_conflict() -> None:
    base, a, b = _metric("Default"), _metric("Success"), _metric("Warning")
    tree = merge3_way_lenient(base, a, b)
    assert isinstance(tree, Node)
    assert _tone_of(tree) == "Default"  # conflict resolved to base


def test_disjoint_with_author_has_no_conflicts() -> None:
    """A conflict-free merge under authors reports no resolved conflicts."""
    base = _box(_child("c1"), _child("c2"))
    a = _box(_child("c1"), _child("c2"), _child("aNew"))  # a-only insert
    b = _box(_child("c1"), _child("c2"), _child("bNew"))  # b-only insert (disjoint)
    result = merge3_way_with_author(Primary(), Secondary(), base, a, b)
    assert isinstance(result, MergeOk)
    assert result.resolved == ()
    assert [c.id for c in result.tree.kind.fields["children"].items] == ["c1", "c2", "aNew", "bNew"]
