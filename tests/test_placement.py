"""Phase 831 — the placement algebra + clone verbs, checked against the REAL apply engine.

Two obligations, stated as properties over generated trees and never as a re-derivation
of the engine's logic:

1. **No false permit** — every op a helper emits is accepted by
   :func:`~fuaran_py.ops.apply.apply`, and the applied tree exhibits the placement's
   declared order (the moved / inserted node sits exactly where the ``Placement`` said,
   with the other siblings' order preserved).
2. **No false refuse** — every helper rejection corresponds to an apply-side rejection of
   the op the helper would otherwise have emitted (or, for ``UnknownAnchor``, to the
   ``OrderingMismatch`` refusal of the only op that could have honoured the anchor).

The clone verbs add the tree-wide id obligations: a duplicate never collides with any id
in the target tree (including ids held in non-structural positions), the clone is
structurally equal to its source modulo ids, and a paste preserves non-colliding ids
while remapping colliding ones.

Base trees are authored with the typed surface (:mod:`fuaran_py.ui`) and decoded through
the real node codec, so every fixture is a genuinely wire-valid tree rather than a
hand-assembled model that only the helpers ever see.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from fuaran_py import decode_node
from fuaran_py.model import Arr, Node, Obj
from fuaran_py.ops import apply
from fuaran_py.ops.apply import (
    CHILDLESS_KIND as APPLY_CHILDLESS_KIND,
)
from fuaran_py.ops.apply import (
    DUPLICATE_NODE_ID,
    KIND_MISMATCH,
    ORDERING_MISMATCH,
    _all_ids,
    _child_slots,
    _find,
    _find_layout_parent,
    _layout_children,
)
from fuaran_py.ops.apply import (
    NODE_NOT_FOUND as APPLY_NODE_NOT_FOUND,
)
from fuaran_py.ops.apply import (
    PARENT_NOT_FOUND as APPLY_PARENT_NOT_FOUND,
)
from fuaran_py.ops.placement import (
    CANNOT_NUDGE_ROOT,
    CHILDLESS_KIND,
    DUPLICATE_ID,
    MOVE_INTO_DESCENDANT,
    MOVE_INTO_SELF,
    NODE_NOT_FOUND,
    NUDGE_OUT_OF_RANGE,
    PARENT_NOT_FOUND,
    UNKNOWN_ANCHOR,
    After,
    Before,
    First,
    Last,
    PlaceErr,
    PlaceError,
    Placement,
    Target,
    can_place,
    derived_ids,
    duplicate_op,
    duplicate_op_with,
    move_op,
    nudge_op,
    paste_op,
    place_op,
    sequential_ids,
)
from fuaran_py.result import Ok
from fuaran_py.schema import types as t
from fuaran_py.ui import encode, fuaran, node

# ── Fixtures ─────────────────────────────────────────────────────────────────


def _decoded(tree: t.UiNode) -> Node:
    result = decode_node(encode(tree))
    assert result.ok, result
    return result.value


def _leaf(node_id: str) -> t.UiNode:
    """A childless kind — `Markdown` carries no `children` list."""
    return fuaran.markdown(node_id, "body")


def _container(node_id: str, children: list[t.UiNode]) -> t.UiNode:
    """A layout kind — `stack` lowers to `Box`, which apply descends into."""
    return fuaran.stack(node_id, children=children)


def _fixture() -> Node:
    """root ── left [a; b; c] · solo (childless leaf) · right [d] · empty []"""
    return _decoded(
        _container(
            "root",
            [
                _container("left", [_leaf("a"), _leaf("b"), _leaf("c")]),
                _leaf("solo"),
                _container("right", [_leaf("d")]),
                _container("empty", []),
            ],
        )
    )


def _fresh(node_id: str = "x") -> Node:
    return _decoded(_leaf(node_id))


# ── Assertions over the real engine ──────────────────────────────────────────


def _child_ids(root: Node, parent_id: str) -> list[str]:
    parent = _find(parent_id, root)
    assert parent is not None, f"parent '{parent_id}' not found in tree"
    return [c.id for c in (_layout_children(parent) or [])]


def _applied(op: Obj, root: Node) -> Node:
    result = apply(op, root)
    assert result.ok, f"apply refused an op the helper emitted: {result}"
    assert isinstance(result, Ok)
    return result.value


def _refused_as(op: Obj, root: Node) -> str:
    result = apply(op, root)
    assert not result.ok, "apply accepted an op the helper refused"
    return result.error.code  # type: ignore[union-attr]


def _refusal(result: object) -> PlaceError:
    assert isinstance(result, PlaceErr), f"expected a refusal, got {result}"
    return result.error


def _op(result: object) -> Obj:
    assert isinstance(result, Ok), f"expected an emitted op, got {result}"
    return result.value


def _raw_ids(root: Node) -> list[str]:
    return _all_ids(root)


def _all_distinct(root: Node) -> bool:
    ids = _raw_ids(root)
    return len(ids) == len(set(ids))


def _kind_shape(n: Node) -> list[str]:
    """Preorder kind tags over the whole traversal surface — structural equality
    modulo ids."""
    shape = [n.kind.tag or ""]
    for child, _ in _child_slots(n):
        shape.extend(_kind_shape(child))
    return shape


def _inserted_child(op: Obj) -> Node:
    if op.tag == "InsertChild":
        child = op.fields["child"]
        assert isinstance(child, Node)
        return child
    assert op.tag == "Batch", f"expected a placed insert, got {op.tag}"
    ops = op.fields["ops"]
    assert isinstance(ops, Arr)
    return _inserted_child(_as_obj(ops.items[0]))


def _as_obj(value: object) -> Obj:
    assert isinstance(value, Obj)
    return value


def _batched(op: Obj) -> list[str]:
    """The op tags of a `Batch`, or the single tag of a bare op."""
    if op.tag != "Batch":
        return [op.tag or ""]
    ops = op.fields["ops"]
    assert isinstance(ops, Arr)
    return [_as_obj(inner).tag or "" for inner in ops.items]


# ── place_op ─────────────────────────────────────────────────────────────────


def test_last_emits_a_bare_insertchild_and_appends() -> None:
    tree = _fixture()
    op = _op(place_op(tree, _fresh(), Target("left", Last())))
    assert _batched(op) == ["InsertChild"]
    assert _child_ids(_applied(op, tree), "left") == ["a", "b", "c", "x"]


def test_first_emits_batch_insert_then_reorder_and_lands_first() -> None:
    tree = _fixture()
    op = _op(place_op(tree, _fresh(), Target("left", First())))
    assert _batched(op) == ["InsertChild", "ReorderChildren"]
    assert _child_ids(_applied(op, tree), "left") == ["x", "a", "b", "c"]


def test_first_into_an_empty_container_stays_a_bare_insertchild() -> None:
    tree = _fixture()
    op = _op(place_op(tree, _fresh(), Target("empty", First())))
    assert _batched(op) == ["InsertChild"]
    assert _child_ids(_applied(op, tree), "empty") == ["x"]


def test_before_an_interior_sibling_lands_immediately_before_it() -> None:
    tree = _fixture()
    op = _op(place_op(tree, _fresh(), Target("left", Before("b"))))
    assert _child_ids(_applied(op, tree), "left") == ["a", "x", "b", "c"]


def test_after_the_last_sibling_stays_a_bare_insertchild() -> None:
    tree = _fixture()
    op = _op(place_op(tree, _fresh(), Target("left", After("c"))))
    assert _batched(op) == ["InsertChild"]
    assert _child_ids(_applied(op, tree), "left") == ["a", "b", "c", "x"]


def test_absent_parent_is_refused_as_the_apply_engine_would_refuse_it() -> None:
    tree = _fixture()
    assert _refusal(place_op(tree, _fresh(), Target("ghost", Last()))) == PlaceError(PARENT_NOT_FOUND, "ghost")
    assert _refused_as(Obj("InsertChild", {"parentId": "ghost", "child": _fresh()}), tree) == APPLY_PARENT_NOT_FOUND


def test_childless_parent_is_refused_as_the_apply_engine_would_refuse_it() -> None:
    tree = _fixture()
    assert _refusal(place_op(tree, _fresh(), Target("solo", Last()))) == PlaceError(CHILDLESS_KIND, "solo")
    assert _refused_as(Obj("InsertChild", {"parentId": "solo", "child": _fresh()}), tree) == APPLY_CHILDLESS_KIND


def test_duplicate_id_is_refused_as_the_apply_engine_would_refuse_it() -> None:
    tree = _fixture()
    clash = _decoded(_leaf("a"))
    assert _refusal(place_op(tree, clash, Target("right", Last()))) == PlaceError(DUPLICATE_ID, "a")
    assert _refused_as(Obj("InsertChild", {"parentId": "right", "child": clash}), tree) == DUPLICATE_NODE_ID


def test_an_anchor_that_is_not_a_destination_child_is_refused_matching_orderingmismatch() -> None:
    tree = _fixture()
    # "d" exists in the tree but is not a child of "left".
    assert _refusal(place_op(tree, _fresh(), Target("left", Before("d")))) == PlaceError(UNKNOWN_ANCHOR, "d")

    # The only op that could honour the anchor names it in a reorder, which the
    # apply engine refuses.
    reorder = Obj("ReorderChildren", {"parentId": "left", "newOrder": Arr(["d", "a", "b", "c"])})
    assert _refused_as(reorder, tree) == ORDERING_MISMATCH


# ── move_op / can_place ──────────────────────────────────────────────────────


def test_cross_parent_last_emits_a_bare_movenode() -> None:
    tree = _fixture()
    op = _op(move_op(tree, "a", Target("right", Last())))
    assert _batched(op) == ["MoveNode"]
    updated = _applied(op, tree)
    assert _child_ids(updated, "right") == ["d", "a"]
    assert _child_ids(updated, "left") == ["b", "c"]


def test_same_parent_re_placement_emits_batch_move_then_reorder() -> None:
    tree = _fixture()
    op = _op(move_op(tree, "c", Target("left", Before("a"))))
    assert _batched(op) == ["MoveNode", "ReorderChildren"]
    assert _child_ids(_applied(op, tree), "left") == ["c", "a", "b"]


def test_move_into_itself_is_refused_as_the_apply_engine_would_refuse_it() -> None:
    tree = _fixture()
    assert _refusal(move_op(tree, "left", Target("left", Last()))) == PlaceError(MOVE_INTO_SELF, "left")
    assert _refused_as(Obj("MoveNode", {"target": "left", "newParentId": "left"}), tree) == KIND_MISMATCH


def test_move_into_a_descendant_is_refused_as_the_apply_engine_would_refuse_it() -> None:
    tree = _fixture()
    assert _refusal(move_op(tree, "root", Target("left", Last()))) == PlaceError(MOVE_INTO_DESCENDANT, "root", "left")
    assert _refused_as(Obj("MoveNode", {"target": "root", "newParentId": "left"}), tree) == KIND_MISMATCH


def test_absent_node_is_refused_as_the_apply_engine_would_refuse_it() -> None:
    tree = _fixture()
    assert _refusal(move_op(tree, "ghost", Target("left", Last()))) == PlaceError(NODE_NOT_FOUND, "ghost")
    assert _refused_as(Obj("MoveNode", {"target": "ghost", "newParentId": "left"}), tree) == APPLY_NODE_NOT_FOUND


def test_childless_destination_is_refused_as_the_apply_engine_would_refuse_it() -> None:
    tree = _fixture()
    assert _refusal(move_op(tree, "a", Target("solo", Last()))) == PlaceError(CHILDLESS_KIND, "solo")
    assert _refused_as(Obj("MoveNode", {"target": "a", "newParentId": "solo"}), tree) == APPLY_CHILDLESS_KIND


def test_anchoring_a_move_on_the_moved_node_itself_is_an_unknown_anchor() -> None:
    tree = _fixture()
    # The moved node is never among its own destination siblings.
    assert _refusal(move_op(tree, "a", Target("right", After("a")))) == PlaceError(UNKNOWN_ANCHOR, "a")


def test_a_node_in_a_non_structural_position_is_not_movable() -> None:
    tree = _decoded(_container("root", [node.on_loading(_leaf("ph"), _leaf("m")), _container("box", [])]))
    assert _refusal(move_op(tree, "ph", Target("box", Last()))) == PlaceError(NODE_NOT_FOUND, "ph")
    assert _refused_as(Obj("MoveNode", {"target": "ph", "newParentId": "box"}), tree) == APPLY_NODE_NOT_FOUND


def test_can_place_agrees_with_move_op_on_the_legal_drop() -> None:
    tree = _fixture()
    assert can_place(tree, "a", Target("right", Before("d"))) == Ok(None)


def test_target_defaults_to_appending() -> None:
    assert Target("left").placement == Last()


# ── nudge_op ─────────────────────────────────────────────────────────────────


def test_minus_one_swaps_the_node_with_its_previous_sibling() -> None:
    tree = _fixture()
    op = _op(nudge_op(tree, "b", -1))
    assert op.tag == "ReorderChildren"
    assert _child_ids(_applied(op, tree), "left") == ["b", "a", "c"]


def test_plus_two_swaps_across_the_list() -> None:
    tree = _fixture()
    op = _op(nudge_op(tree, "a", 2))
    assert _child_ids(_applied(op, tree), "left") == ["c", "b", "a"]


def test_the_first_sibling_cannot_move_up() -> None:
    tree = _fixture()
    assert _refusal(nudge_op(tree, "a", -1)) == PlaceError(NUDGE_OUT_OF_RANGE, "a", delta=-1)


def test_the_last_sibling_cannot_move_down() -> None:
    tree = _fixture()
    assert _refusal(nudge_op(tree, "c", 1)) == PlaceError(NUDGE_OUT_OF_RANGE, "c", delta=1)


def test_the_root_has_no_siblings_to_nudge_among() -> None:
    tree = _fixture()
    assert _refusal(nudge_op(tree, "root", 1)) == PlaceError(CANNOT_NUDGE_ROOT, "root")


def test_an_absent_node_cannot_be_nudged() -> None:
    tree = _fixture()
    assert _refusal(nudge_op(tree, "ghost", 1)) == PlaceError(NODE_NOT_FOUND, "ghost")


# ── duplicate_op / paste_op ──────────────────────────────────────────────────


def test_duplicate_places_a_fresh_id_clone_beside_its_source() -> None:
    tree = _fixture()
    op = _op(duplicate_op(tree, "left", Target("root", After("left"))))
    updated = _applied(op, tree)

    assert _child_ids(updated, "root") == ["left", "left-copy", "solo", "right", "empty"]
    assert _child_ids(updated, "left-copy") == ["a-copy", "b-copy", "c-copy"]
    assert _all_distinct(updated)


def test_duplicate_is_structurally_equal_to_its_source_modulo_ids() -> None:
    tree = _fixture()
    op = _op(duplicate_op(tree, "left", Target("right", Last())))
    source = _find("left", tree)
    assert source is not None
    assert _kind_shape(_inserted_child(op)) == _kind_shape(source)


def test_the_injectable_strategy_mints_deterministic_sequential_ids() -> None:
    tree = _fixture()
    op = _op(duplicate_op_with(sequential_ids("dup"), tree, "left", Target("root", Last())))
    updated = _applied(op, tree)

    assert _child_ids(updated, "root")[-1] == "dup-1"
    assert _child_ids(updated, "dup-1") == ["dup-2", "dup-3", "dup-4"]


def test_the_sequential_strategy_dodges_ids_already_taken() -> None:
    # `dup-1` is already in the tree, so the counter must step past it rather than
    # minting a collision the apply gate would then refuse.
    tree = _decoded(_container("root", [_container("src", [_leaf("k")]), _leaf("dup-1")]))
    op = _op(duplicate_op_with(sequential_ids("dup"), tree, "src", Target("root", Last())))
    updated = _applied(op, tree)

    assert _child_ids(updated, "root") == ["src", "dup-1", "dup-2"]
    assert _all_distinct(updated)


def test_the_derived_strategy_probes_past_a_taken_suffix() -> None:
    taken = {"n-copy"}
    assert derived_ids("n", lambda c: c in taken) == "n-copy-2"


def test_duplicate_remaps_ids_held_in_non_structural_positions_too() -> None:
    # `ph` lives in a State slot — invisible to the structural child lists, but
    # inside the tree-wide id-uniqueness contract.
    tree = _decoded(_container("root", [node.on_loading(_leaf("ph"), _leaf("m"))]))
    op = _op(duplicate_op(tree, "m", Target("root", Last())))
    updated = _applied(op, tree)

    assert _all_distinct(updated), "the State-slot id was remapped, not smuggled"
    assert "ph-copy" in _raw_ids(updated)


def test_duplicate_of_an_absent_source_is_refused() -> None:
    tree = _fixture()
    assert _refusal(duplicate_op(tree, "ghost", Target("root", Last()))) == PlaceError(NODE_NOT_FOUND, "ghost")


def test_paste_remaps_colliding_ids_and_preserves_the_rest() -> None:
    tree = _fixture()
    # Lifted from a different tree: "left" and "a" collide with the target; "z" does not.
    foreign = _decoded(_container("left", [_leaf("a"), _leaf("z")]))

    op = _op(paste_op(tree, foreign, Target("right", Last())))
    updated = _applied(op, tree)

    assert _child_ids(updated, "right") == ["d", "left-copy"]
    assert _child_ids(updated, "left-copy") == ["a-copy", "z"]
    assert _all_distinct(updated)


def test_paste_with_no_collisions_preserves_every_id() -> None:
    tree = _fixture()
    foreign = _decoded(_container("p", [_leaf("q")]))

    op = _op(paste_op(tree, foreign, Target("empty", Last())))
    updated = _applied(op, tree)

    assert _child_ids(updated, "empty") == ["p"]
    assert _child_ids(updated, "p") == ["q"]


def test_paste_into_an_absent_parent_is_refused() -> None:
    tree = _fixture()
    foreign = _decoded(_container("p", [_leaf("q")]))
    assert _refusal(paste_op(tree, foreign, Target("ghost", Last()))) == PlaceError(PARENT_NOT_FOUND, "ghost")


# ── The emitted vocabulary is the EXISTING one (no wire change) ──────────────


def test_every_helper_emits_only_existing_treeop_shapes() -> None:
    """The phase's library-only claim, asserted rather than asserted-in-prose: each
    emitted op decodes as one of the vocabulary the apply engine already folds."""
    known = {"InsertChild", "MoveNode", "ReorderChildren", "Batch"}
    tree = _fixture()
    foreign = _decoded(_container("p", [_leaf("q")]))

    emitted = [
        _op(place_op(tree, _fresh(), Target("left", First()))),
        _op(move_op(tree, "a", Target("right", First()))),
        _op(nudge_op(tree, "b", -1)),
        _op(duplicate_op(tree, "left", Target("root", First()))),
        _op(paste_op(tree, foreign, Target("empty", Last()))),
    ]
    for op in emitted:
        assert set(_batched(op)) <= known, op


# ── Property tests ───────────────────────────────────────────────────────────
#
# Generated trees are Box containers over Markdown leaves with sequential preorder ids
# (`n1`, `n2`, …) under a fixed `root` container; parents, anchors and moved nodes are
# drawn from the tree's OWN ids plus a `ghost`, so both the legal and every illegal class
# is generated.

_SHAPES = st.recursive(
    st.none(),
    lambda children: st.lists(children, max_size=3),
    max_leaves=8,
)


def _build(shape: object) -> Node:
    counter = 0

    def go(s: object) -> t.UiNode:
        nonlocal counter
        counter += 1
        node_id = f"n{counter}"
        if s is None:
            return _leaf(node_id)
        assert isinstance(s, list)
        return _container(node_id, [go(child) for child in s])

    return _decoded(_container("root", [go(shape)]))


_TREES = _SHAPES.map(_build)


@st.composite
def _tree_and_pick(draw: st.DrawFn) -> tuple[Node, str]:
    tree = draw(_TREES)
    return tree, draw(st.sampled_from(["ghost", *_all_ids(tree)]))


def _placement_for(draw: st.DrawFn, tree: Node) -> Placement:
    ids = ["ghost", *_all_ids(tree)]
    return draw(
        st.one_of(
            st.just(Last()),
            st.just(First()),
            st.sampled_from(ids).map(Before),
            st.sampled_from(ids).map(After),
        )
    )


@st.composite
def _tree_and_target(draw: st.DrawFn) -> tuple[Node, Target]:
    tree = draw(_TREES)
    parent = draw(st.sampled_from(["ghost", *_all_ids(tree)]))
    return tree, Target(parent, _placement_for(draw, tree))


@st.composite
def _tree_pick_target(draw: st.DrawFn) -> tuple[Node, str, Target]:
    tree = draw(_TREES)
    moved = draw(st.sampled_from(["ghost", *_all_ids(tree)]))
    parent = draw(st.sampled_from(["ghost", *_all_ids(tree)]))
    return tree, moved, Target(parent, _placement_for(draw, tree))


@st.composite
def _two_trees_pick_target(draw: st.DrawFn) -> tuple[Node, Node, str, Target]:
    tree_a = draw(_TREES)
    tree_b = draw(_TREES)
    source = draw(st.sampled_from(["ghost", *_all_ids(tree_a)]))
    parent = draw(st.sampled_from(["ghost", *_all_ids(tree_b)]))
    return tree_a, tree_b, source, Target(parent, _placement_for(draw, tree_b))


_SETTINGS = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def _child_node_ids(root: Node, parent_id: str) -> list[str]:
    parent = _find(parent_id, root)
    if parent is None:
        return []
    return [c.id for c in (_layout_children(parent) or [])]


def _anchor_of(placement: Placement) -> str | None:
    return placement.anchor if isinstance(placement, (Before, After)) else None


def _anchor_not_a_sibling(tree: Node, parent_id: str, moved: str, anchor: str) -> bool:
    """The anchor is genuinely not among the destination's post-op children (excluding
    the moved node itself, which is never its own anchor)."""
    return anchor not in [i for i in _child_node_ids(tree, parent_id) if i != moved]


def _declared_order_holds(before: Node, after: Node, moved: str, target: Target) -> bool:
    """The moved / inserted node sits exactly where the placement declared, and the other
    siblings keep their relative order."""
    ids = _child_node_ids(after, target.parent_id)
    if moved not in ids:
        return False
    idx = ids.index(moved)

    others_after = [i for i in ids if i != moved]
    others_before = [i for i in _child_node_ids(before, target.parent_id) if i != moved]
    if others_after != others_before:
        return False

    placement = target.placement
    if isinstance(placement, Last):
        return idx == len(ids) - 1
    if isinstance(placement, First):
        return idx == 0
    if isinstance(placement, Before):
        return idx + 1 < len(ids) and ids[idx + 1] == placement.anchor
    return idx > 0 and ids[idx - 1] == placement.anchor


@given(_tree_and_target())
@_SETTINGS
def test_property_place_op_applies_to_the_declared_order_or_mirrors_the_engine(
    scenario: tuple[Node, Target],
) -> None:
    tree, target = scenario
    fresh = _fresh("fresh-child")
    result = place_op(tree, fresh, target)

    if isinstance(result, Ok):
        updated = apply(result.value, tree)
        assert updated.ok, f"apply refused an emitted op: {updated}"
        assert isinstance(updated, Ok)
        assert _declared_order_holds(tree, updated.value, "fresh-child", target)
        return

    error = _refusal(result)
    naive = Obj("InsertChild", {"parentId": target.parent_id, "child": fresh})
    if error.code == PARENT_NOT_FOUND:
        assert error.subject == target.parent_id
        assert _refused_as(naive, tree) == APPLY_PARENT_NOT_FOUND
    elif error.code == CHILDLESS_KIND:
        assert error.subject == target.parent_id
        assert _refused_as(naive, tree) == APPLY_CHILDLESS_KIND
    elif error.code == UNKNOWN_ANCHOR:
        assert _anchor_of(target.placement) == error.subject
        assert _anchor_not_a_sibling(tree, target.parent_id, "fresh-child", error.subject)
    else:
        pytest.fail(f"unexpected refusal {error}")


@given(_tree_pick_target())
@_SETTINGS
def test_property_move_op_applies_to_the_declared_order_or_mirrors_the_engine(
    scenario: tuple[Node, str, Target],
) -> None:
    tree, moved, target = scenario
    result = move_op(tree, moved, target)
    naive = Obj("MoveNode", {"target": moved, "newParentId": target.parent_id})

    if isinstance(result, Ok):
        updated = apply(result.value, tree)
        assert updated.ok, f"apply refused an emitted op: {updated}"
        assert isinstance(updated, Ok)
        assert _declared_order_holds(tree, updated.value, moved, target)
        new_parent = _find_layout_parent(moved, updated.value)
        assert new_parent is not None and new_parent.id == target.parent_id
        return

    error = _refusal(result)
    if error.code == UNKNOWN_ANCHOR:
        assert _anchor_of(target.placement) == error.subject
        assert _anchor_not_a_sibling(tree, target.parent_id, moved, error.subject)
    elif error.code in {NODE_NOT_FOUND, MOVE_INTO_SELF, MOVE_INTO_DESCENDANT, PARENT_NOT_FOUND, CHILDLESS_KIND}:
        # Every one of these classes is a refusal of the bare MoveNode itself.
        assert not apply(naive, tree).ok
    else:
        pytest.fail(f"unexpected refusal {error}")


@given(_tree_pick_target())
@_SETTINGS
def test_property_can_place_agrees_with_move_op_verdict_for_verdict(
    scenario: tuple[Node, str, Target],
) -> None:
    tree, moved, target = scenario
    check = can_place(tree, moved, target)
    emitted = move_op(tree, moved, target)

    if isinstance(check, Ok):
        assert isinstance(emitted, Ok), f"can_place permitted what move_op refused: {emitted}"
    else:
        assert isinstance(emitted, PlaceErr), "move_op permitted what can_place refused"
        assert check.error == emitted.error


@given(_tree_and_pick(), st.integers(min_value=-2, max_value=2))
@_SETTINGS
def test_property_nudge_op_swaps_where_declared_or_refuses_honestly(scenario: tuple[Node, str], delta: int) -> None:
    tree, node_id = scenario
    result = nudge_op(tree, node_id, delta)

    if isinstance(result, Ok):
        parent = _find_layout_parent(node_id, tree)
        assert parent is not None
        updated = apply(result.value, tree)
        assert updated.ok, f"apply refused an emitted op: {updated}"
        assert isinstance(updated, Ok)

        before = _child_node_ids(tree, parent.id)
        idx = before.index(node_id)
        expected = list(before)
        expected[idx], expected[idx + delta] = before[idx + delta], before[idx]
        assert _child_node_ids(updated.value, parent.id) == expected
        return

    error = _refusal(result)
    if error.code == CANNOT_NUDGE_ROOT:
        assert error.subject == node_id == tree.id
    elif error.code == NODE_NOT_FOUND:
        assert tree.id != node_id and _find_layout_parent(node_id, tree) is None
    elif error.code == NUDGE_OUT_OF_RANGE:
        parent = _find_layout_parent(node_id, tree)
        assert parent is not None
        siblings = _child_node_ids(tree, parent.id)
        idx = siblings.index(node_id)
        assert idx + delta < 0 or idx + delta >= len(siblings)
    else:
        pytest.fail(f"unexpected refusal {error}")


@given(_tree_pick_target())
@_SETTINGS
def test_property_duplicate_never_collides_grows_by_the_subtree_and_clones_the_shape(
    scenario: tuple[Node, str, Target],
) -> None:
    tree, source, target = scenario
    result = duplicate_op(tree, source, target)

    if isinstance(result, Ok):
        updated = apply(result.value, tree)
        assert updated.ok, f"apply refused an emitted op: {updated}"
        assert isinstance(updated, Ok)

        source_node = _find(source, tree)
        assert source_node is not None
        ids = _raw_ids(updated.value)

        assert len(ids) == len(set(ids)), "a clone smuggled a duplicate id past the gate"
        assert len(ids) == len(_raw_ids(tree)) + len(_all_ids(source_node))
        assert _kind_shape(_inserted_child(result.value)) == _kind_shape(source_node)
        return

    error = _refusal(result)
    if error.code == NODE_NOT_FOUND:
        assert error.subject == source and _find(source, tree) is None
    elif error.code == PARENT_NOT_FOUND:
        assert error.subject == target.parent_id and _find(target.parent_id, tree) is None
    elif error.code == CHILDLESS_KIND:
        parent = _find(target.parent_id, tree)
        assert parent is not None and _layout_children(parent) is None
    elif error.code == UNKNOWN_ANCHOR:
        # The clone's fresh root id is never its own anchor, so exclusion is moot.
        assert _anchor_of(target.placement) == error.subject
        assert _anchor_not_a_sibling(tree, target.parent_id, "«none»", error.subject)
    else:
        pytest.fail(f"unexpected refusal {error}")


@given(_two_trees_pick_target())
@_SETTINGS
def test_property_paste_remaps_collisions_preserves_the_rest_and_never_duplicates(
    scenario: tuple[Node, Node, str, Target],
) -> None:
    tree_a, tree_b, source, target = scenario
    lifted = _find(source, tree_a)
    if lifted is None:
        return  # only tree ids are generated; a ghost source is not the contract under test

    result = paste_op(tree_b, lifted, target)

    if isinstance(result, Ok):
        updated = apply(result.value, tree_b)
        assert updated.ok, f"apply refused an emitted op: {updated}"
        assert isinstance(updated, Ok)

        ids = _raw_ids(updated.value)
        before = set(_raw_ids(tree_b))
        preserved = [i for i in _all_ids(lifted) if i not in before]

        assert len(ids) == len(set(ids)), "a paste smuggled a duplicate id past the gate"
        assert all(i in ids for i in preserved), "a non-colliding id was needlessly remapped"
        return

    error = _refusal(result)
    if error.code == PARENT_NOT_FOUND:
        assert _find(target.parent_id, tree_b) is None
    elif error.code == CHILDLESS_KIND:
        parent = _find(target.parent_id, tree_b)
        assert parent is not None and _layout_children(parent) is None
    elif error.code == UNKNOWN_ANCHOR:
        assert _anchor_of(target.placement) == error.subject
    else:
        pytest.fail(f"unexpected refusal {error}")
