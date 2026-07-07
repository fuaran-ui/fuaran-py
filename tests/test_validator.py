"""Pre-emit validator surface."""

from __future__ import annotations

from fuaran_py import decode_node, validate_node
from fuaran_py.model import Arr, Node, Obj


def test_clean_tree_has_no_findings() -> None:
    result = decode_node('{"id":"a","kind":{"$type":"Markdown","text":{"$type":"Literal","text":"hi"}}}')
    assert result.ok
    assert validate_node(result.value) == []


def test_empty_id_is_flagged() -> None:
    node = Node("", Obj("Markdown", {"text": Obj("Literal", {"text": "x"})}))
    findings = validate_node(node)
    assert [f.code for f in findings] == ["EMPTY_NODE_ID"]
    assert findings[0].path == "$.id"


def test_duplicate_child_id_is_flagged() -> None:
    child_a = Node("dup", Obj("Markdown", {"text": Obj("Literal", {"text": "x"})}))
    child_b = Node("dup", Obj("Markdown", {"text": Obj("Literal", {"text": "y"})}))
    root = Node(
        "root",
        Obj(
            "Box",
            {
                "children": Arr([child_a, child_b]),
                "layout": Obj("Flex", {"direction": "Vertical", "wrap": False}),
                "role": "Group",
            },
        ),
    )
    findings = validate_node(root)
    assert any(f.code == "DUPLICATE_NODE_ID" for f in findings)


def test_unknown_kind_is_flagged() -> None:
    node = Node("a", Obj("Sparkler", {}))
    findings = validate_node(node)
    assert [f.code for f in findings] == ["UNKNOWN_NODE_KIND"]
    assert findings[0].path == "$.kind.$type"
