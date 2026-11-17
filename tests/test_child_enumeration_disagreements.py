"""One child enumeration: the walks that disagreed about reach (fuaran#2663).

Before the host had one enumeration of "the nodes a node holds", each walk wrote its own,
and the copies disagreed. The byte corpus cannot see that class — every walk decodes and
re-encodes the same bytes — so each disagreement found is pinned here through the PUBLIC
surface that showed it, in the shape a caller meets it:

* the apply engine addressed six container kinds, while the IDL, the reference host and
  this host's own speech projection hold eight: a node inside a ``Modal`` or a
  ``ScrollArea`` could not be removed, edited or inserted beside;
* the AI-tools lookup, the validator and the runtime's event binding did not see the
  ``state.onLoading`` / ``state.onEmpty`` alternatives the apply engine reaches;
* the runtime's event binding did not see ``Switch`` arms;
* the transform evaluator did not see slot arguments;
* the renderers' fragment registry did not descend into ``ErrorBoundary.fallback``, the
  state alternatives or a fragment body.

Every test here fails on the tree before the enumeration landed and passes after it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_ui import decode_node
from fuaran_ui.ai_tools import child_ids, find_node, walk_nodes
from fuaran_ui.compute import ComputeOk, evaluate_tree
from fuaran_ui.model import Node, Obj
from fuaran_ui.ops import apply
from fuaran_ui.renderer import render_html
from fuaran_ui.result import Ok
from fuaran_ui.runtime import BrowserDeps, FuaranRuntime
from fuaran_ui.validator import validate_node

_GROUP = {"layout": {"$type": "Flex", "direction": "Vertical"}, "role": "Group"}


def _md(node_id: str, text: str = "text") -> dict[str, object]:
    return {"id": node_id, "kind": {"$type": "Markdown", "text": text}}


def _box(node_id: str, children: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {"id": node_id, "kind": {"$type": "Box", "children": children, **_GROUP}, **extra}


def _decode(tree: dict[str, object]) -> Node:
    result = decode_node(json.dumps(tree))
    assert result.ok, getattr(result, "error", result)
    return result.value


# ── the apply engine reaches a Modal's and a ScrollArea's children ───────────

_CONTAINERS = {
    "Modal": {
        "$type": "Modal",
        "children": [_md("inner")],
        "heading": "Confirm",
        "onDismiss": {"$type": "Chain", "ops": []},
        "open": {"$type": "Static", "value": True},
    },
    "ScrollArea": {"$type": "ScrollArea", "children": [_md("inner")], "orientation": "Vertical"},
}


def _contained(kind: str) -> Node:
    return _decode(_box("root", [{"id": "box", "kind": _CONTAINERS[kind]}]))


@pytest.mark.parametrize("kind", sorted(_CONTAINERS))
def test_remove_node_reaches_a_child_of(kind: str) -> None:
    result = apply(Obj("RemoveNode", {"target": "inner"}), _contained(kind))
    assert isinstance(result, Ok), result
    assert find_node(result.value, "inner") is None


@pytest.mark.parametrize("kind", sorted(_CONTAINERS))
def test_insert_child_appends_to(kind: str) -> None:
    added = _decode(_md("added"))
    result = apply(Obj("InsertChild", {"parentId": "box", "child": added}), _contained(kind))
    assert isinstance(result, Ok), result
    assert child_ids(find_node(result.value, "box")) == ("inner", "added")  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", sorted(_CONTAINERS))
def test_update_prop_reaches_a_child_of(kind: str) -> None:
    op = Obj("UpdateProp", {"target": "inner", "path": "Text", "value": "changed"})
    result = apply(op, _contained(kind))
    assert isinstance(result, Ok), result
    inner = find_node(result.value, "inner")
    assert inner is not None and inner.kind.fields["text"] == "changed"


# ── the state alternatives are node positions for every walk ─────────────────


def _with_state_arms() -> Node:
    return _decode(_box("root", [_md("a")], state={"onLoading": _md("loading"), "onEmpty": _md("empty")}))


def test_ai_tools_find_node_sees_a_state_alternative() -> None:
    tree = _with_state_arms()
    assert find_node(tree, "empty") is not None
    assert [n.id for n in walk_nodes(tree)] == ["root", "a", "loading", "empty"]
    assert child_ids(tree) == ("a", "loading", "empty")


def test_validator_reports_a_duplicate_id_inside_a_state_alternative() -> None:
    tree = _decode(_box("root", [_md("a")], state={"onLoading": _md("a")}))
    dup = [f for f in validate_node(tree) if f.code == "FUARAN-DUP-ID"]
    assert [f.path for f in dup] == ["$.state.onLoading.id"]


# ── the runtime binds every node the render can paint ────────────────────────


class _Dom:
    def __init__(self) -> None:
        self.listeners: dict[str, list[Callable[[Any], None]]] = {}

    def deps(self) -> BrowserDeps:
        def add(element: Any, event: str, handler: Callable[[Any], None]) -> Callable[[], None]:
            self.listeners.setdefault(element, []).append(handler)
            return lambda: None

        return BrowserDeps(get_element_by_id=lambda i: i, set_inner_html=lambda e, h: None, add_event_listener=add)


def test_runtime_binds_a_button_held_in_a_switch_arm() -> None:
    button = {
        "id": "btn",
        "kind": {"$type": "Button", "label": "Go", "onClick": {"$type": "Chain", "ops": []}, "variant": "Primary"},
    }
    switch = {"id": "sw", "kind": {"$type": "Switch", "cases": [], "default": button, "stateKey": "view"}}
    dom = _Dom()
    runtime = FuaranRuntime(_decode(_box("root", [switch])), lambda node_id, event: [], deps=dom.deps())
    runtime.mount("fuaran-root")
    assert "btn" in dom.listeners


# ── the transform evaluator sees a slot argument ─────────────────────────────


@corpus_required
def test_evaluate_tree_derives_a_transform_held_in_a_slot_argument() -> None:
    grid = json.loads((CORPUS_ROOT / "nodes" / "grid-transform.json").read_text(encoding="utf-8"))
    ref = {
        "id": "ref",
        "kind": {"$type": "FragmentRef", "args": {"grid": {"$type": "SlotArg", "tree": grid}}, "name": "panel"},
    }
    derived = evaluate_tree(_decode(ref), {})
    assert isinstance(derived.get("grid-transform"), ComputeOk)


# ── the fragment registry walks the fragment scope ───────────────────────────


def _decl(name: str, body_text: str) -> dict[str, object]:
    return {
        "id": f"decl-{name}",
        "kind": {"$type": "FragmentDecl", "body": _md(f"body-{name}", body_text), "name": name},
    }


def _ref(name: str) -> dict[str, object]:
    return {"id": f"ref-{name}", "kind": {"$type": "FragmentRef", "name": name}}


def _unresolved(html: str) -> bool:
    return "fuaran-fragment-unresolved-placeholder" in html


def test_a_fragment_declared_in_an_error_boundary_fallback_resolves() -> None:
    boundary = {"id": "eb", "kind": {"$type": "ErrorBoundary", "child": _md("ok"), "fallback": _decl("x", "From x")}}
    html = render_html(_decode(_box("root", [boundary, _ref("x")])))
    assert not _unresolved(html) and "From x" in html


def test_a_fragment_declared_in_a_state_alternative_resolves() -> None:
    html = render_html(_decode(_box("root", [_ref("x")], state={"onEmpty": _decl("x", "From x")})))
    assert not _unresolved(html) and "From x" in html


def test_a_fragment_declared_inside_a_fragment_body_resolves() -> None:
    outer = {"id": "decl-outer", "kind": {"$type": "FragmentDecl", "body": _decl("x", "From x"), "name": "outer"}}
    html = render_html(_decode(_box("root", [outer, _ref("x")])))
    assert not _unresolved(html) and "From x" in html
