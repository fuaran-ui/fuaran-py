"""Phase 2171 — the apply engine enforces the WIRE_FORMAT §21 tree limits.

The decoder bounds what ARRIVES; these tests pin that ``apply`` bounds what it
PRODUCES. Two halves:

* the shared ``apply/limits-apply.json`` family (Phase 2141), each vector's tree
  and op decoded by this host's own decoders and applied — the certification the
  .NET, Go and Rust hosts already run;
* this host's own unit pins for what the family deliberately leaves to each host:
  ``InsertChild`` and ``ReplaceRoot``, ``MaxNodes``, ``Batch``, and the ops that
  cannot grow a tree and so are never charged the walk.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui import decode_node
from fuaran_ui.limits import MAX_NODE_DEPTH, MAX_NODES
from fuaran_ui.model import Arr, Node, Obj
from fuaran_ui.ops import apply, decode_op
from fuaran_ui.ops.apply import LIMIT_EXCEEDED, ApplyErr, Ok

APPLY_CORPUS_ROOT = CORPUS_ROOT / "apply"
_FAMILY_ID = "limitsApply"


def _apply_corpus_available() -> bool:
    return (APPLY_CORPUS_ROOT / "manifest.json").is_file()


def _family() -> tuple[dict, list[dict]]:
    manifest = json.loads((APPLY_CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    entry = next((f for f in manifest["families"] if f["id"] == _FAMILY_ID), None)
    assert entry is not None, f"apply/manifest.json declares no {_FAMILY_ID} family"
    body = json.loads((APPLY_CORPUS_ROOT / entry["file"]).read_text(encoding="utf-8"))
    return entry, list(body["vectors"])


def _vectors() -> list[dict]:
    if not _apply_corpus_available():
        return []
    return _family()[1]


# The snapshot carries apply/ (conformance/sync_corpus.py), so this family is
# never absent from a checkout of this repo; a missing manifest is a broken copy
# and must read RED, not as a skip.
def test_the_apply_family_is_present_and_complete() -> None:
    assert _apply_corpus_available(), f"apply corpus not found at {APPLY_CORPUS_ROOT}"
    entry, vectors = _family()
    assert len(vectors) == entry["vectors"], (
        f"{_FAMILY_ID} holds {len(vectors)} vectors, the manifest declares {entry['vectors']}"
    )
    assert vectors, "an empty family certifies nothing"


def test_the_family_pins_the_same_limits_this_host_enforces() -> None:
    body = json.loads((APPLY_CORPUS_ROOT / _family()[0]["file"]).read_text(encoding="utf-8"))
    assert body["limits"] == {"maxDepth": MAX_NODE_DEPTH, "maxNodes": MAX_NODES}


#: The apply families this host certifies. Every other family the apply manifest lists
#: is a declared lag here and is NAMED below, never silently absent.
_CERTIFIED_APPLY_FAMILIES = (_FAMILY_ID,)


def test_apply_families_this_host_does_not_run_are_named(capsys: pytest.CaptureFixture[str]) -> None:
    manifest = json.loads((APPLY_CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    ids = [f["id"] for f in manifest["families"]]
    with capsys.disabled():
        for family in manifest["families"]:
            if family["id"] not in _CERTIFIED_APPLY_FAMILIES:
                state = family.get("adoption", {}).get("fuaran-py", "undeclared")
                print(f"apply family not run by this host (adoption: {state}): {family['id']} x {family['vectors']}")
    stale = [f for f in _CERTIFIED_APPLY_FAMILIES if f not in ids]
    assert not stale, f"named as certified but absent from apply/manifest.json: {stale}"


@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: v["id"])
def test_limits_apply_vector(vector: dict) -> None:
    tree = decode_node(vector["input"]["tree"])
    assert tree.ok, f"the tree did not decode: {tree}"
    op = decode_op(vector["input"]["op"])
    assert op.ok, f"the op did not decode: {op}"

    result = apply(op.value, tree.value)
    expected = vector["expected"]
    if expected["verdict"] == "accept":
        assert isinstance(result, Ok), f"expected accept, refused: {result}"
    elif expected["verdict"] == "reject":
        assert isinstance(result, ApplyErr), f"expected a {expected['code']} refusal, applied"
        assert result.error.code == expected["code"]
    else:
        pytest.fail(f"unknown verdict {expected['verdict']!r}")


# ── Host-owned pins ──────────────────────────────────────────────────────────


def _md(node_id: str) -> Node:
    return Node(node_id, Obj("Markdown", {"text": node_id}))


def _box(node_id: str, children: list[Node]) -> Node:
    return Node(
        node_id,
        Obj(
            "Box",
            {
                "children": Arr(list(children)),
                "layout": Obj("Flex", {"direction": "Vertical", "wrap": False}),
                "role": "Group",
            },
        ),
    )


def _chain(depth: int, prefix: str = "n") -> Node:
    """A chain of ``depth`` nested Boxes, the deepest (``<prefix>1``) childless."""
    node = _box(f"{prefix}1", [])
    for level in range(2, depth + 1):
        node = _box(f"{prefix}{level}", [node])
    return node


def _insert(parent: str, child: Node) -> Obj:
    return Obj("InsertChild", {"child": child, "parentId": parent})


def _refused_for_limit(result: object) -> None:
    assert isinstance(result, ApplyErr), f"expected {LIMIT_EXCEEDED}, applied"
    assert result.error.code == LIMIT_EXCEEDED
    assert result.error.batch_index is None


def test_insert_child_past_max_depth_is_refused() -> None:
    _refused_for_limit(apply(_insert("n1", _md("over")), _chain(MAX_NODE_DEPTH)))


def test_insert_child_at_max_depth_applies() -> None:
    assert isinstance(apply(_insert("n1", _md("at")), _chain(MAX_NODE_DEPTH - 1)), Ok)


def test_replace_root_past_max_depth_is_refused() -> None:
    _refused_for_limit(apply(Obj("ReplaceRoot", {"node": _chain(MAX_NODE_DEPTH + 1)}), _md("root")))


def test_replace_root_at_max_depth_applies() -> None:
    assert isinstance(apply(Obj("ReplaceRoot", {"node": _chain(MAX_NODE_DEPTH)}), _md("root")), Ok)


def test_max_nodes_is_enforced_on_the_count_not_the_depth() -> None:
    # A root over MAX_NODES - 1 leaves is exactly MAX_NODES nodes, two levels deep.
    wide = _box("root", [_md(f"m{i}") for i in range(MAX_NODES - 1)])
    assert isinstance(apply(Obj("ReplaceRoot", {"node": wide}), _md("x")), Ok)
    _refused_for_limit(apply(_insert("root", _md("one-more")), wide))


def test_a_node_in_a_state_alternative_counts_as_a_level() -> None:
    deepest = _chain(MAX_NODE_DEPTH)
    op = Obj("UpdateState", {"state": Obj(None, {"onEmpty": _md("empty")}), "target": "n1"})
    _refused_for_limit(apply(op, deepest))


def test_a_batch_is_checked_on_its_result_and_refused_whole() -> None:
    base = _chain(MAX_NODE_DEPTH - 1)
    op = Obj("Batch", {"ops": Arr([_insert("n1", _box("a", [])), _insert("a", _md("b"))])})
    _refused_for_limit(apply(op, base))


def test_a_batch_that_ends_within_the_limits_applies() -> None:
    # Grows past the limit, then removes what crossed it: only the result is measured,
    # exactly as on the other hosts.
    base = _chain(MAX_NODE_DEPTH - 1)
    op = Obj(
        "Batch",
        {
            "ops": Arr(
                [
                    _insert("n1", _box("a", [])),
                    _insert("a", _md("b")),
                    Obj("RemoveNode", {"target": "a"}),
                ]
            )
        },
    )
    assert isinstance(apply(op, base), Ok)


def test_ops_that_cannot_grow_the_tree_still_apply_to_an_over_limit_tree() -> None:
    # A tree already past MaxDepth (built in memory, never decoded) is not this op's doing:
    # UpdateProp / UpdateStyle / RemoveNode / ReorderChildren are not charged the walk.
    over = _chain(MAX_NODE_DEPTH + 1)
    deepest_md = _box("p", [_md("x"), _md("y")])
    over_with_list = _box("top", [over, deepest_md])
    assert isinstance(apply(Obj("UpdateStyle", {"style": Obj(None, {}), "target": "n1"}), over), Ok)
    assert isinstance(apply(Obj("RemoveNode", {"target": "y"}), over_with_list), Ok)
    assert isinstance(apply(Obj("ReorderChildren", {"newOrder": Arr(["y", "x"]), "parentId": "p"}), over_with_list), Ok)


def test_edit_node_carrying_no_node_is_not_charged() -> None:
    over = _chain(MAX_NODE_DEPTH + 1)
    assert isinstance(apply(Obj("EditNode", {"newKind": Obj("Markdown", {"text": "t"}), "target": "n1"}), over), Ok)


def test_the_limit_walk_does_not_recurse() -> None:
    # A tree far deeper than the interpreter's recursion limit, built in memory. The walk
    # that measures it must refuse with the typed error, never raise RecursionError.
    import sys

    from fuaran_ui.ops.apply import _tree_exceeds_limits

    deep = _chain(sys.getrecursionlimit() * 3)
    breach = _tree_exceeds_limits(deep)
    assert breach is not None
    assert breach.code == LIMIT_EXCEEDED
