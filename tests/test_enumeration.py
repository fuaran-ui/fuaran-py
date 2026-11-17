"""``fuaran_ui.ops.enumeration`` — the host's one child enumeration (fuaran#2663).

The slot list is read from the generated IDL tables, so these tests pin the derivation
against the tables themselves rather than against a list of kinds: a kind the IDL gives an
ordered ``children`` list is a container here with no edit, and a member that holds a node
in a shape the module does not classify fails the import.
"""

from __future__ import annotations

import pytest

from fuaran_ui.model import Arr, Node, Obj
from fuaran_ui.ops import enumeration as en
from fuaran_ui.ops.enumeration import (
    FRAGMENT_SCOPE,
    KEYED,
    KIND_HELD,
    STRUCTURAL,
    Position,
    children,
    find_node,
    map_children,
    ordered_children,
    slots,
    walk,
    with_ordered_children,
)
from fuaran_ui.schema.generated import KIND_FIELDS, T


def _md(node_id: str) -> Node:
    return Node(node_id, Obj("Markdown", {"text": node_id}), {})


def _with_envelope(node_id: str, kind: Obj) -> Node:
    return Node(
        node_id,
        kind,
        {"state": Obj(None, {"onEmpty": _md("empty"), "onLoading": _md("loading")}), "fallback": _md("fb")},
    )


def test_every_kind_with_an_ordered_node_list_in_the_idl_is_a_container() -> None:
    declared = {
        kind
        for kind, fields in KIND_FIELDS.items()
        if any(f.type.kind == "list" and f.type.of is not None and f.type.of.kind == "node" for f in fields)
    }
    containers = {
        kind for kind in KIND_FIELDS if ordered_children(Node("n", Obj(kind, {"children": Arr([])}), {})) is not None
    }
    assert containers == declared
    assert {"Modal", "ScrollArea"} <= containers


def test_one_canonical_order_kind_then_state_then_fallback() -> None:
    switch = Obj(
        "Switch",
        {
            "cases": Arr(
                [Obj(None, {"child": _md("c0"), "match": "a"}), Obj(None, {"child": _md("c1"), "match": "b"})]
            ),
            "default": _md("d"),
        },
    )
    node = _with_envelope("sw", switch)
    assert [(s.position, s.node.id, s.address) for s in slots(node)] == [
        (Position.ARM, "c0", ("kind", "cases", 0, "child")),
        (Position.ARM, "c1", ("kind", "cases", 1, "child")),
        (Position.ARM, "d", ("kind", "default")),
        (Position.STATE_ARM, "loading", ("state", "onLoading")),
        (Position.STATE_ARM, "empty", ("state", "onEmpty")),
        (Position.FALLBACK, "fb", ("fallback",)),
    ]


def test_slot_arguments_are_read_in_key_order_and_value_arguments_hold_nothing() -> None:
    args = Obj(
        None,
        {
            "b": Obj("SlotArg", {"tree": _md("b")}),
            "n": Obj("Int", {"value": 1}),
            "a": Obj("SlotArg", {"tree": _md("a")}),
        },
    )
    ref = Node("ref", Obj("FragmentRef", {"args": args, "name": "f"}), {})
    assert [(s.position, s.node.id, s.address) for s in slots(ref)] == [
        (Position.ARGUMENT, "a", ("kind", "args", "a", "tree")),
        (Position.ARGUMENT, "b", ("kind", "args", "b", "tree")),
    ]


def test_each_reach_selects_its_classes() -> None:
    decl = Node("decl", Obj("FragmentDecl", {"body": _md("body"), "name": "f"}), {})
    box = _with_envelope("box", Obj("Box", {"children": Arr([_md("c"), decl])}))
    assert [n.id for n in children(box, STRUCTURAL)] == ["c", "decl"]
    assert [n.id for n in children(decl, STRUCTURAL)] == []
    assert [n.id for n in children(decl, KIND_HELD)] == ["body"]
    assert [n.id for n in children(box, FRAGMENT_SCOPE)] == ["c", "decl", "loading", "empty"]
    assert [n.id for n in children(box, KEYED)] == ["c", "decl", "loading", "empty", "fb"]
    assert [n.id for n in walk(box, KEYED)] == ["box", "c", "decl", "body", "loading", "empty", "fb"]
    assert find_node(box, "body") is decl.kind.fields["body"]
    assert find_node(box, "absent") is None


def test_map_children_rewrites_only_its_reach_and_keeps_identity_when_nothing_moved() -> None:
    box = _with_envelope("box", Obj("Box", {"children": Arr([_md("c")])}))
    assert map_children(box, KEYED, lambda n: n) is box

    def renamed(n: Node) -> Node:
        return Node(n.id + "'", n.kind, n.extras)

    structural = map_children(box, STRUCTURAL, renamed)
    assert [n.id for n in children(structural, KEYED)] == ["c'", "loading", "empty", "fb"]
    keyed = map_children(box, KEYED, renamed)
    assert [n.id for n in children(keyed, KEYED)] == ["c'", "loading'", "empty'", "fb'"]
    assert keyed.extras["state"].fields.keys() == box.extras["state"].fields.keys()  # type: ignore[union-attr]


def test_map_children_visits_each_child_once() -> None:
    wide = Node("w", Obj("Box", {"children": Arr([_md(f"c{i}") for i in range(200)])}), {})
    seen: list[str] = []

    def visit(n: Node) -> Node:
        seen.append(n.id)
        return n

    map_children(wide, KEYED, visit)
    assert len(seen) == 200


def test_with_ordered_children_leaves_a_kind_with_no_ordered_list_alone() -> None:
    leaf = _md("m")
    assert with_ordered_children(leaf, [_md("x")]) is leaf
    box = with_ordered_children(Node("b", Obj("Box", {"children": Arr([])}), {}), [_md("x")])
    assert [n.id for n in ordered_children(box) or []] == ["x"]


def test_a_node_held_in_an_unclassified_shape_fails_rather_than_being_missed() -> None:
    with pytest.raises(TypeError, match="does not classify"):
        en._classify("Kind", "member", T("map", of=T("node")))
