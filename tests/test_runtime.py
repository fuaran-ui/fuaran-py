"""Phase 280 — the interactive runtime drives mount + dispatch→apply→re-render.

The loop is exercised against an injected **fake DOM** (substrate-free) — the
Pyodide-only ``js`` path is import-guarded and never touched here, so these run
under plain CPython. They assert the mount paints the rendered markup, a DOM event
folds its op through the apply engine and re-renders, and an apply failure leaves
the tree (and the DOM) untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fuaran_py import decode_node
from fuaran_py.model import Obj
from fuaran_py.runtime import BrowserDeps, FuaranRuntime, counter_runtime, counter_tree
from fuaran_py.ui import encode, fuaran

# ── A minimal fake DOM (the BrowserDeps the runtime drives) ──────────────────


@dataclass
class _FakeElement:
    element_id: str
    inner_html: str = ""
    listeners: dict[str, list[Callable[[Any], None]]] = field(default_factory=dict)


@dataclass
class _FakeDom:
    elements: dict[str, _FakeElement] = field(default_factory=dict)

    def _el(self, element_id: str) -> _FakeElement:
        return self.elements.setdefault(element_id, _FakeElement(element_id))

    def deps(self) -> BrowserDeps:
        def get_element_by_id(element_id: str) -> Any:
            return self._el(element_id)

        def set_inner_html(element: Any, html: str) -> None:
            element.inner_html = html

        def add_event_listener(element: Any, event: str, handler: Callable[[Any], None]) -> Callable[[], None]:
            element.listeners.setdefault(event, []).append(handler)

            def cleanup() -> None:
                element.listeners[event].remove(handler)

            return cleanup

        return BrowserDeps(
            get_element_by_id=get_element_by_id,
            set_inner_html=set_inner_html,
            add_event_listener=add_event_listener,
        )

    def click(self, node_id: str, event: str = "click") -> None:
        for handler in list(self._el(node_id).listeners.get(event, [])):
            handler(None)


def _decoded(tree_uinode: object) -> object:
    result = decode_node(encode(tree_uinode))  # type: ignore[arg-type]
    assert result.ok
    return result.value


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_module_imports_under_plain_cpython() -> None:
    """The package must import with no `js` / Pyodide present (import-guarded seam)."""
    import fuaran_py.runtime as runtime_pkg

    assert hasattr(runtime_pkg, "FuaranRuntime")


def test_mount_paints_the_rendered_markup() -> None:
    dom = _FakeDom()
    runtime = counter_runtime(deps=dom.deps())
    runtime.mount("fuaran-root")
    root_html = dom.elements["fuaran-root"].inner_html
    assert "fuaran-metric-value" in root_html
    assert ">0<" in root_html  # the initial count


def test_dispatch_apply_rerender_cycle() -> None:
    dom = _FakeDom()
    runtime = counter_runtime(deps=dom.deps())
    runtime.mount("fuaran-root")

    dom.click("inc")  # one DOM event → ReplaceBinding → apply → re-render
    assert ">1<" in dom.elements["fuaran-root"].inner_html

    dom.click("inc")
    assert ">2<" in dom.elements["fuaran-root"].inner_html


def test_dispatch_updates_the_live_tree() -> None:
    dom = _FakeDom()
    runtime = counter_runtime(deps=dom.deps())
    runtime.mount("fuaran-root")
    dom.click("inc")
    # The metric's Source binding now carries the bumped value in the live tree.
    metric = None
    for child in runtime.tree.kind.fields["children"].items:  # type: ignore[union-attr]
        if getattr(child, "id", None) == "count":
            metric = child
    assert metric is not None
    assert metric.kind.fields["source"] == Obj("Static", {"value": 1})


def test_failed_apply_leaves_tree_and_dom_untouched() -> None:
    dom = _FakeDom()
    tree = _decoded(fuaran.stack("root", children=[fuaran.markdown("a", "x")]))
    runtime = FuaranRuntime(tree, deps=dom.deps())  # type: ignore[arg-type]
    runtime.mount("fuaran-root")
    before = dom.elements["fuaran-root"].inner_html

    result = runtime.dispatch(Obj("RemoveNode", {"target": "ghost"}))
    assert not result.ok
    assert runtime.last_error is not None
    assert runtime.tree is tree  # unchanged reference
    assert dom.elements["fuaran-root"].inner_html == before  # no re-render


def test_dispatch_list_is_atomic_batch() -> None:
    dom = _FakeDom()
    tree = _decoded(fuaran.stack("root", children=[fuaran.markdown("a", "hello")]))
    runtime = FuaranRuntime(tree, deps=dom.deps())  # type: ignore[arg-type]
    runtime.mount("fuaran-root")

    good = Obj("UpdateProp", {"path": "Text", "target": "a", "value": "changed"})
    bad = Obj("RemoveNode", {"target": "ghost"})
    result = runtime.dispatch([good, bad])
    assert not result.ok  # the whole batch aborts; the good op is rolled back
    assert "hello" in dom.elements["fuaran-root"].inner_html
    assert "changed" not in dom.elements["fuaran-root"].inner_html


def test_counter_tree_is_authored_and_decodable() -> None:
    tree = counter_tree()
    assert tree.id == "counter-root"
    assert {c.id for c in tree.kind.fields["children"].items} == {"count", "inc"}  # type: ignore[union-attr]
