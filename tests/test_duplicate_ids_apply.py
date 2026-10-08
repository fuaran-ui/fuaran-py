"""Phase 2172 — apply refuses a tree that would hold duplicate node ids.

Every op addresses its target by id alone (WIRE_FORMAT §8.1), so a tree that
holds one id twice makes every later id-addressed op ambiguous. The decoder
accepts a repeated id, and an apply could BUILD one from parts that each decoded
cleanly. The check reads the op's RESULT and charges it only for the ids it
installed. Two halves:

* the shared ``apply/duplicate-ids-apply.json`` family, each vector's tree and op
  decoded by this host's own decoders and applied — the certification every
  op-applying host runs;
* this host's own pins: the in-memory shapes, and the precedence the limits
  guard takes over this one.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui import decode_node
from fuaran_ui.limits import MAX_NODE_DEPTH
from fuaran_ui.model import Arr, Node, Obj
from fuaran_ui.ops import apply, decode_op
from fuaran_ui.ops.apply import DUPLICATE_NODE_ID, LIMIT_EXCEEDED, ApplyErr, Ok

APPLY_CORPUS_ROOT = CORPUS_ROOT / "apply"
_FAMILY_ID = "duplicateIdsApply"


def _family() -> tuple[dict, list[dict]]:
    manifest = json.loads((APPLY_CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    entry = next((f for f in manifest["families"] if f["id"] == _FAMILY_ID), None)
    assert entry is not None, f"apply/manifest.json declares no {_FAMILY_ID} family"
    body = json.loads((APPLY_CORPUS_ROOT / entry["file"]).read_text(encoding="utf-8"))
    return entry, list(body["vectors"])


def _vectors() -> list[dict]:
    if not (APPLY_CORPUS_ROOT / "manifest.json").is_file():
        return []
    return _family()[1]


# The snapshot carries apply/ (conformance/sync_corpus.py), so a missing family is
# a broken copy and must read RED, not as a skip.
def test_the_duplicate_ids_family_is_present_and_complete() -> None:
    assert (APPLY_CORPUS_ROOT / "manifest.json").is_file(), f"apply corpus not found at {APPLY_CORPUS_ROOT}"
    entry, vectors = _family()
    assert len(vectors) == entry["vectors"], (
        f"{_FAMILY_ID} holds {len(vectors)} vectors, the manifest declares {entry['vectors']}"
    )
    assert vectors, "an empty family certifies nothing"


@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: v["id"])
def test_duplicate_ids_apply_vector(vector: dict) -> None:
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


def _box(node_id: str, children: list[Node], extras: dict | None = None) -> Node:
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
        extras or {},
    )


def _kind(children: list[Node]) -> Obj:
    return _box("carrier", children).kind


def _edit(target: str, children: list[Node]) -> Obj:
    return Obj("EditNode", {"newKind": _kind(children), "target": target})


def _loading(target: str, alternative: Node) -> Obj:
    return Obj("UpdateState", {"state": Obj(None, {"onLoading": alternative}), "target": target})


def _base() -> Node:
    return _box("r", [_box("a", []), _box("b", [])])


_X = _box("x", [])

_REFUSED = {
    "ReplaceRoot repeats an id": Obj("ReplaceRoot", {"node": _box("r2", [_X, _X])}),
    "ReplaceRoot repeats its root id below": Obj("ReplaceRoot", {"node": _box("r", [_box("r", [])])}),
    "EditNode collides with the tree": _edit("a", [_box("b", [])]),
    "EditNode repeats an id within its kind": _edit("a", [_X, _X]),
    "EditNode repeats the edited node's id": _edit("a", [_box("a", [])]),
    "UpdateState collides with the tree": _loading("a", _box("b", [])),
    "InsertChild repeats an id within itself": Obj("InsertChild", {"child": _box("c", [_X, _X]), "parentId": "r"}),
    "InsertChild collides with the tree": Obj("InsertChild", {"child": _box("b", []), "parentId": "r"}),
    "a Batch whose result repeats an installed id": Obj("Batch", {"ops": Arr([_edit("a", [_box("b", [])])])}),
}


@pytest.mark.parametrize("op", list(_REFUSED.values()), ids=list(_REFUSED))
def test_an_installed_duplicate_is_refused(op: Obj) -> None:
    result = apply(op, _base())
    assert isinstance(result, ApplyErr), "the op applied and left a duplicate id in the tree"
    assert result.error.code == DUPLICATE_NODE_ID


_ACCEPTED = {
    "ReplaceRoot reuses the replaced tree's ids": (
        _base(),
        Obj("ReplaceRoot", {"node": _box("r", [_box("a", []), _box("b", [])])}),
    ),
    "EditNode restates the children it replaces": (
        _box("r", [_box("a", [_box("c", [])])]),
        _edit("a", [_box("c", []), _box("d", [])]),
    ),
    "UpdateState replaces its own alternative": (
        _box("r", [_box("a", [], {"state": Obj(None, {"onLoading": _box("l", [])})})]),
        _loading("a", _box("l", [_box("l2", [])])),
    ),
    "a duplicate the op did not install is not charged to it": (
        _box("r", [_box("x", []), _box("y", [_box("x", [])]), _box("z", [])]),
        _edit("z", [_box("w", [])]),
    ),
    "a Batch whose intermediate state duplicates but whose result does not": (
        _box("r", [_box("a", []), _box("s", [_box("b", [])])]),
        Obj("Batch", {"ops": Arr([_edit("a", [_box("b", [])]), _edit("s", [])])}),
    ),
}


@pytest.mark.parametrize(("tree", "op"), list(_ACCEPTED.values()), ids=list(_ACCEPTED))
def test_what_only_looks_like_a_duplicate_applies(tree: Node, op: Obj) -> None:
    result = apply(op, tree)
    assert isinstance(result, Ok), f"refused: {result}"


def test_limit_exceeded_takes_precedence_over_a_duplicate() -> None:
    # limitsApply's ``editnode-repeated-id-past-maxdepth`` pins this order.
    chain = _box("n1", [])
    for level in range(2, MAX_NODE_DEPTH + 1):
        chain = _box(f"n{level}", [chain])
    result = apply(_edit("n1", [_box("n1", [])]), chain)
    assert isinstance(result, ApplyErr)
    assert result.error.code == LIMIT_EXCEEDED
