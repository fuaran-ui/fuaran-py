"""The interactive client runtime — the in-browser dispatch→apply→re-render loop.

The Python analogue of the F# (Fable/React) and TypeScript (React) interactive
hosts: mount a decoded ``Node`` tree to the live DOM, wire DOM events to a host
``on_event`` handler, fold the ``TreeOp``\\ s it returns through the Phase 279 apply
engine, and re-render. It runs under **Pyodide** (CPython compiled to WASM,
client-side) — but every browser-API touch is behind an injectable
:class:`BrowserDeps` seam (the established ``style_observer`` precedent), so the
module imports cleanly under plain CPython and tests drive it against a fake DOM.

It reuses, never re-implements: the Phase 239 :func:`fuaran_py.renderer.render_html`
for markup + the reference ``fuaran-*`` class vocabulary, and the Phase 279
:func:`fuaran_py.ops.apply` for op semantics. The runtime owns only the loop.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..compute import ComputeResult, ComputeState, evaluate_tree
from ..model import Arr, Node, Obj, Value
from ..ops import ApplyErr, ApplyResult, apply
from ..renderer import render_html
from ..renderer.bindings import BindingSources
from ..result import Ok

# A host event handler: given the node id + DOM event type that fired, return the
# op(s) to apply (or ``None`` for "ignore"). This is the app's update function —
# the typed-message dispatch the wire's erased closures cannot carry.
EventHandler = Callable[[str, str], "Obj | list[Obj] | None"]


@dataclass
class BrowserDeps:
    """Injectable browser-surface dependencies — the default drives the live DOM.

    ``add_event_listener`` returns a *cleanup* callable the runtime invokes before
    re-binding, so listeners (and, under Pyodide, the proxies behind them) do not
    accumulate across re-renders.
    """

    get_element_by_id: Callable[[str], Any]
    set_inner_html: Callable[[Any, str], None]
    add_event_listener: Callable[[Any, str, Callable[[Any], None]], Callable[[], None]]


def _pyodide_deps() -> BrowserDeps:
    """The default deps: drive the live DOM via the Pyodide ``js`` interop module."""
    import js  # noqa: PLC0415 — lazy; only importable under Pyodide.
    from pyodide.ffi import create_proxy  # noqa: PLC0415

    def get_element_by_id(element_id: str) -> Any:
        return js.document.getElementById(element_id)

    def set_inner_html(element: Any, html: str) -> None:
        element.innerHTML = html

    def add_event_listener(element: Any, event: str, handler: Callable[[Any], None]) -> Callable[[], None]:
        proxy = create_proxy(handler)
        element.addEventListener(event, proxy)

        def cleanup() -> None:
            element.removeEventListener(event, proxy)
            proxy.destroy()

        return cleanup

    return BrowserDeps(
        get_element_by_id=get_element_by_id,
        set_inner_html=set_inner_html,
        add_event_listener=add_event_listener,
    )


# ── Node-id enumeration over the decoded tree ────────────────────────────────


def _as_node(value: Value) -> Node | None:
    """Coerce a child position to a ``Node`` (typed or structurally-decoded)."""
    if isinstance(value, Node):
        return value
    if isinstance(value, Obj) and isinstance(value.fields.get("kind"), Obj):
        node_id = value.fields.get("id")
        kind = value.fields["kind"]
        if isinstance(node_id, str) and isinstance(kind, Obj):
            extras: dict[str, Value] = {
                k: value.fields[k] for k in ("state", "style", "accessibility") if k in value.fields
            }
            return Node(node_id, kind, extras)
    return None


def _node_ids(node: Node, acc: list[str]) -> None:
    """Every node id in the tree (the binding set), mirroring the render walk."""
    acc.append(node.id)
    fields = node.kind.fields
    children = fields.get("children")
    if isinstance(children, Arr):
        for item in children.items:
            child = _as_node(item)
            if child is not None:
                _node_ids(child, acc)
    for key in ("child", "fallback", "body"):
        child = _as_node(fields.get(key))
        if child is not None:
            _node_ids(child, acc)
    state = node.extras.get("state")
    if isinstance(state, Obj):
        for key in ("onLoading", "onEmpty"):
            child = _as_node(state.fields.get(key))
            if child is not None:
                _node_ids(child, acc)


# ── The runtime ──────────────────────────────────────────────────────────────


class FuaranRuntime:
    """Holds the live tree and drives the dispatch→apply→re-render loop.

    Construct with a decoded ``Node`` tree + an ``on_event`` update function, then
    :meth:`mount` it under a DOM root id. Each bound DOM event calls ``on_event``;
    the returned op(s) fold through the apply engine (atomically when many), and a
    successful apply re-renders the root. An apply failure leaves the tree
    untouched and is surfaced via :attr:`last_error`.
    """

    def __init__(
        self,
        tree: Node,
        on_event: EventHandler | None = None,
        sources: BindingSources | None = None,
        deps: BrowserDeps | None = None,
        events: tuple[str, ...] = ("click",),
        compute_state: ComputeState | None = None,
    ) -> None:
        self._tree = tree
        self._on_event = on_event
        self._sources = sources
        self._deps = deps if deps is not None else _pyodide_deps()
        self._events = events
        self._root: Any = None
        self._cleanups: list[Callable[[], None]] = []
        self.last_error: object | None = None
        # The compute-layer state store (parameters bind to it) + the derived rows the
        # compute graph currently evaluates to. A SetState change recomputes both.
        self._compute_state: ComputeState = dict(compute_state) if compute_state else {}
        self.derived: dict[str, ComputeResult] = {}
        self._recompute()

    @property
    def tree(self) -> Node:
        """The current decoded tree (replaced on every successful dispatch)."""
        return self._tree

    @property
    def compute_state(self) -> ComputeState:
        """The current compute-layer state store (read-only view)."""
        return dict(self._compute_state)

    def _recompute(self) -> None:
        """Re-evaluate every compute-graph source in the tree against the current state."""
        self.derived = evaluate_tree(self._tree, self._compute_state)

    def set_compute_state(self, updates: ComputeState | None = None, **kwargs: object) -> dict[str, ComputeResult]:
        """Update the compute-layer state store, recompute the derived cells, and
        re-render — the reactive Living-Sheet loop. Returns the new derived rows."""
        if updates:
            self._compute_state.update(updates)
        if kwargs:
            self._compute_state.update(kwargs)
        self._recompute()
        if self._root is not None:
            self._paint()
        return self.derived

    def render(self) -> str:
        """The current body-fragment HTML (the same string :meth:`mount` writes)."""
        if self._compute_state:
            merged: BindingSources = {**(self._sources or {}), **self._compute_state}
            return render_html(self._tree, merged)
        return render_html(self._tree, self._sources)

    def mount(self, root_id: str) -> None:
        """Render the tree into the element with id ``root_id`` and wire events."""
        self._root = self._deps.get_element_by_id(root_id)
        if self._root is None:
            raise ValueError(f"mount root '{root_id}' not found in the document")
        self._paint()

    def dispatch(self, ops: Obj | list[Obj]) -> ApplyResult:
        """Apply one op (or a list, atomically as a ``Batch``) and re-render on success.

        On an apply failure the tree is left untouched, :attr:`last_error` is set,
        and no re-render occurs (so the DOM keeps reflecting the last good tree).
        """
        op = ops if isinstance(ops, Obj) else Obj("Batch", {"ops": Arr(list(ops))})
        result = apply(op, self._tree)
        if isinstance(result, Ok):
            self._tree = result.value
            self.last_error = None
            self._recompute()  # the tree changed — the compute graph may have too
            if self._root is not None:
                self._paint()
        else:
            assert isinstance(result, ApplyErr)
            self.last_error = result.error
        return result

    # ── internals ────────────────────────────────────────────────────────────

    def _paint(self) -> None:
        self._deps.set_inner_html(self._root, self.render())
        self._bind()

    def _bind(self) -> None:
        for cleanup in self._cleanups:
            cleanup()
        self._cleanups = []
        if self._on_event is None:
            return
        ids: list[str] = []
        _node_ids(self._tree, ids)
        for node_id in ids:
            element = self._deps.get_element_by_id(node_id)
            if element is None:
                continue
            for event in self._events:
                handler = self._make_handler(node_id, event)
                self._cleanups.append(self._deps.add_event_listener(element, event, handler))

    def _make_handler(self, node_id: str, event: str) -> Callable[[Any], None]:
        def handler(_dom_event: Any) -> None:
            self._on_dom_event(node_id, event)

        return handler

    def _on_dom_event(self, node_id: str, event: str) -> None:
        if self._on_event is None:
            return
        result = self._on_event(node_id, event)
        if result is None:
            return
        self.dispatch(result)
