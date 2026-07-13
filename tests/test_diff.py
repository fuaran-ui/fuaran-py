"""Phase 520 — the tree-op diff engine (``fuaran_py.ops.diff``).

The correctness contract is the **round-trip law**, the same invariant every host
shares (F# ``TreeOpDiff``, the sibling engines):

    fold(apply, before, diff(before, after)) == after      # canonical-JSON equality

There is no committed cross-host *diff* corpus (the diff's op vocabulary is exercised
by ``ops/`` round-trip fixtures, and its result is contracted only by the round-trip
law — the F# engine emits an applyable ``TreeOp`` list, not a byte-committed artefact).
So parity here certifies the shared contract the F# engine guarantees: the round-trip
law over op-reachable ``after`` trees, determinism, empty-on-identical, and the
deterministic child-op order. The corpus node fixtures are the ``before`` population;
each ``after`` is produced by applying real ``TreeOp``\\s (so every difference is
op-expressible — the law's domain). The suite skips cleanly when the corpus is absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import decode_node, encode_node
from fuaran_py.model import Arr, Node, Obj
from fuaran_py.ops import apply, decode_op, diff, diff_batched, encode_op
from fuaran_py.ops.apply import ApplyErr
from fuaran_py.result import Ok

_NODES_DIR = CORPUS_ROOT / "nodes"


def _node_fixture_paths() -> list[Path]:
    return sorted(_NODES_DIR.glob("*.json")) if _NODES_DIR.is_dir() else []


def _decode(path: Path) -> Node:
    result = decode_node(path.read_text(encoding="utf-8"))
    assert result.ok, f"decode {path.name} failed: {result.error}"
    return result.value


def _apply_all(ops: list[Obj], tree: Node) -> Node:
    """Fold an op list over a tree, asserting each step succeeds."""
    state = tree
    for i, op in enumerate(ops):
        result = apply(op, state)
        assert isinstance(result, Ok), (
            f"apply op #{i} ({op.tag}) failed: {result.error if isinstance(result, ApplyErr) else result}"
        )
        state = result.value
    return state


def _round_trips(before: Node, after: Node) -> None:
    """Assert the round-trip law under canonical-JSON equality."""
    ops = diff(before, after)
    rebuilt = _apply_all(ops, before)
    assert encode_node(rebuilt) == encode_node(after)


_FIXTURE_IDS = [p.stem for p in _node_fixture_paths()]
_FIXTURES = pytest.mark.parametrize("path", _node_fixture_paths(), ids=_FIXTURE_IDS)


# ── identity ─────────────────────────────────────────────────────────────────


@corpus_required
@_FIXTURES
def test_identity_diff_is_empty(path: Path) -> None:
    """A node diffed against itself yields no ops."""
    node = _decode(path)
    assert diff(node, node) == []


# ── per-op round-trips over the whole corpus ─────────────────────────────────


@corpus_required
@_FIXTURES
def test_add_style_round_trips(path: Path) -> None:
    """Applying an ``UpdateStyle`` then diffing back round-trips (UpdateStyle leg)."""
    before = _decode(path)
    style = Obj(None, {"emphasis": "Normal", "tone": "Brand", "weight": "Standard"})
    after = _apply_all([Obj("UpdateStyle", {"style": style, "target": before.id})], before)
    _round_trips(before, after)


@corpus_required
@_FIXTURES
def test_kind_swap_floors_to_editnode(path: Path) -> None:
    """A wholesale kind change round-trips and floors to an ``EditNode``."""
    before = _decode(path)
    # A target kind whose tag differs from the fixture's, so this is a real kind swap.
    swapped = (
        Obj("Skeleton", {"rows": 3})
        if before.kind.tag != "Skeleton"
        else Obj("Markdown", {"text": Obj("Literal", {"text": "swapped"})})
    )
    after = _apply_all([Obj("EditNode", {"newKind": swapped, "target": before.id})], before)
    ops = diff(before, after)
    assert encode_node(_apply_all(ops, before)) == encode_node(after)
    assert any(op.tag == "EditNode" for op in ops)


# ── UpdateProp minimality (the granular refinement) ──────────────────────────


@corpus_required
def test_single_field_change_is_one_updateprop() -> None:
    """Changing one Metric field emits exactly one ``UpdateProp`` — minimal, not a floor."""
    before = _decode(_NODES_DIR / "metric-1.json")
    after = _apply_all([Obj("UpdateProp", {"path": "Tone", "target": before.id, "value": "Success"})], before)
    ops = diff(before, after)
    assert len(ops) == 1
    assert ops[0].tag == "UpdateProp"
    assert ops[0].fields["path"] == "Tone"
    _round_trips(before, after)


@corpus_required
def test_multi_field_change_emits_updateprops() -> None:
    """Two Metric field changes emit granular ``UpdateProp``\\s (no wholesale EditNode)."""
    before = _decode(_NODES_DIR / "metric-1.json")
    after = _apply_all(
        [
            Obj("UpdateProp", {"path": "Tone", "target": before.id, "value": "Success"}),
            Obj("UpdateProp", {"path": "Icon", "target": before.id, "value": "arrow-up"}),
        ],
        before,
    )
    ops = diff(before, after)
    assert all(op.tag == "UpdateProp" for op in ops)
    assert {op.fields["path"] for op in ops} == {"Tone", "Icon"}
    _round_trips(before, after)


# ── structural child ops (on a Box with children) ────────────────────────────


def _call_into() -> Node:
    return _decode(_NODES_DIR / "call-into.json")


@corpus_required
def test_remove_child_round_trips() -> None:
    before = _call_into()
    after = _apply_all([Obj("RemoveNode", {"target": "total-metric"})], before)
    ops = diff(before, after)
    assert any(op.tag == "RemoveNode" for op in ops)
    _round_trips(before, after)


@corpus_required
def test_insert_child_round_trips() -> None:
    before = _call_into()
    new_child = Node("fresh-note", Obj("Markdown", {"text": Obj("Literal", {"text": "new"})}), {})
    after = _apply_all([Obj("InsertChild", {"child": new_child, "parentId": before.id, "position": 2})], before)
    ops = diff(before, after)
    assert any(op.tag == "InsertChild" for op in ops)
    _round_trips(before, after)


@corpus_required
def test_reorder_children_round_trips() -> None:
    before = _call_into()
    order = list(reversed([c.id for c in _children_ids(before)]))
    after = _apply_all([Obj("ReorderChildren", {"newOrder": Arr(order), "parentId": before.id})], before)
    ops = diff(before, after)
    assert any(op.tag == "ReorderChildren" for op in ops)
    _round_trips(before, after)


@corpus_required
def test_nested_child_field_change_recurses() -> None:
    """A change to a grandchild field round-trips (child recursion)."""
    before = _call_into()
    after = _apply_all(
        [Obj("UpdateProp", {"path": "Tone", "target": "total-metric", "value": "Success"})],
        before,
    )
    ops = diff(before, after)
    _round_trips(before, after)
    # the recursion targets the child, not the parent
    assert any(op.tag == "UpdateProp" and op.fields["target"] == "total-metric" for op in ops)


def _children_ids(node: Node) -> list[Node]:
    children = node.kind.fields.get("children")
    assert isinstance(children, Arr)
    return [c for c in children.items if isinstance(c, Node)]


# ── cross-parent move (the pre-pass) ─────────────────────────────────────────


@corpus_required
def test_cross_parent_move_round_trips() -> None:
    """Reparenting a node across two Boxes round-trips via a MoveNode pre-pass."""
    inner_a = Node("a1", Obj("Markdown", {"text": Obj("Literal", {"text": "a1"})}), {})
    box_a = Node("box-a", Obj("Box", {"children": Arr([inner_a]), "layout": Obj("Auto", {}), "role": "Group"}), {})
    box_b = Node("box-b", Obj("Box", {"children": Arr([]), "layout": Obj("Auto", {}), "role": "Group"}), {})
    root = Node("root", Obj("Box", {"children": Arr([box_a, box_b]), "layout": Obj("Auto", {}), "role": "Group"}), {})
    after = _apply_all([Obj("MoveNode", {"newParentId": "box-b", "newPosition": 0, "target": "a1"})], root)
    ops = diff(root, after)
    assert any(op.tag == "MoveNode" for op in ops)
    _round_trips(root, after)


# ── ReplaceRoot (root id change) ─────────────────────────────────────────────


@corpus_required
def test_root_id_change_uses_replace_root() -> None:
    before = _decode(_NODES_DIR / "metric-1.json")
    after = Node("different-root", before.kind, before.extras)
    ops = diff(before, after)
    assert ops == [Obj("ReplaceRoot", {"node": after})]
    _round_trips(before, after)


# ── determinism + op validity ────────────────────────────────────────────────


@corpus_required
def test_diff_is_deterministic() -> None:
    before = _call_into()
    after = _apply_all(
        [
            Obj("RemoveNode", {"target": "total-metric"}),
            Obj("UpdateProp", {"path": "Tone", "target": "orders-metric", "value": "Warning"}),
        ],
        before,
    )
    assert diff(before, after) == diff(before, after)


@corpus_required
def test_emitted_ops_encode_and_decode() -> None:
    """Every emitted op is valid wire — it encodes and decodes back to itself."""
    before = _call_into()
    after = _apply_all([Obj("RemoveNode", {"target": "orders-metric"})], before)
    for op in diff(before, after):
        decoded = decode_op(encode_op(op))
        assert decoded.ok, f"op {op.tag} did not decode: {decoded.error}"
        assert encode_op(decoded.value) == encode_op(op)


@corpus_required
def test_diff_batched_wraps_multi_op() -> None:
    before = _call_into()
    after = _apply_all(
        [
            Obj("RemoveNode", {"target": "total-metric"}),
            Obj("RemoveNode", {"target": "orders-metric"}),
        ],
        before,
    )
    batched = diff_batched(before, after)
    assert len(batched) == 1
    assert batched[0].tag == "Batch"
    rebuilt = _apply_all(batched, before)
    assert encode_node(rebuilt) == encode_node(after)
