"""The layout observer implementations — Python port of ``Fuaran.UI.LayoutObserver``.

- :class:`InMemoryLayoutObserver` — fixture-driven, substrate-free; drives tests +
  non-browser hosts. Walks a parent-pointer graph for ``observe_tree``.
- :class:`BrowserLayoutObserver` — reads **live** ``getBoundingClientRect`` +
  ``getComputedStyle`` under Pyodide (Python compiled to WASM running client-side in
  the browser). Browser-API access is behind an injectable :class:`BrowserDeps`
  object; tests inject a fake DOM. The Pyodide runtime supplies the measurements; the
  flag derivation stays the pure tier shared with the in-memory observer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .flags import (
    DEFAULT_OPTIONS,
    LayoutInput,
    LayoutObservation,
    LayoutObserverOptions,
    flags_equal,
    to_layout_observation,
)

Subscriber = Callable[[str, LayoutObservation], None]


# ── InMemoryLayoutObserver ─────────────────────────────────────────────────────


class InMemoryLayoutObserver:
    """Fixture-driven observer — substrate-free, for tests + non-browser hosts."""

    def __init__(self, options: LayoutObserverOptions = DEFAULT_OPTIONS) -> None:
        self._options = options
        self._registry: dict[str, tuple[LayoutInput, str | None]] = {}
        self._last_flags: dict[str, list] = {}
        self._subscribers: list[Subscriber] = []

    def _to_obs(self, node_id: str, inp: LayoutInput) -> LayoutObservation:
        return to_layout_observation(self._options, node_id, inp)

    def _emit(self, node_id: str, obs: LayoutObservation) -> None:
        for sub in self._subscribers:
            try:
                sub(node_id, obs)
            except Exception:  # noqa: BLE001 — a throwing subscriber must not poison siblings.
                pass

    def register_fixture(self, node_id: str, inp: LayoutInput, parent: str | None = None) -> None:
        """Register or replace a fixture; fires an initial emission unconditionally."""
        self._registry[node_id] = (inp, parent)
        obs = self._to_obs(node_id, inp)
        self._last_flags[node_id] = obs.flags
        self._emit(node_id, obs)

    def update(self, node_id: str, inp: LayoutInput) -> None:
        """Replace a registered node's input; honours ``emit_on_flag_change_only``. No-op if absent."""
        existing = self._registry.get(node_id)
        if existing is None:
            return
        self._registry[node_id] = (inp, existing[1])
        obs = self._to_obs(node_id, inp)
        previous = self._last_flags.get(node_id, [])
        self._last_flags[node_id] = obs.flags
        should_emit = (not flags_equal(obs.flags, previous)) if self._options.emit_on_flag_change_only else True
        if should_emit:
            self._emit(node_id, obs)

    def observe(self, node_id: str) -> LayoutObservation | None:
        entry = self._registry.get(node_id)
        return None if entry is None else self._to_obs(node_id, entry[0])

    def observe_tree(self, root_id: str) -> list[LayoutObservation]:
        if root_id not in self._registry:
            return []
        children: dict[str, list[str]] = {}
        for node_id, (_, parent) in self._registry.items():
            if parent is not None:
                children.setdefault(parent, []).append(node_id)
        acc: list[LayoutObservation] = []
        queue: list[str] = [root_id]
        while queue:
            node_id = queue.pop(0)
            entry = self._registry.get(node_id)
            if entry is not None:
                acc.append(self._to_obs(node_id, entry[0]))
            queue.extend(children.get(node_id, []))
        return acc

    def subscribe(self, handler: Subscriber) -> Callable[[], None]:
        self._subscribers.append(handler)

        def unsubscribe() -> None:
            if handler in self._subscribers:
                self._subscribers.remove(handler)

        return unsubscribe

    def unregister(self, node_id: str) -> None:
        self._registry.pop(node_id, None)
        self._last_flags.pop(node_id, None)


# ── BrowserLayoutObserver (Pyodide live read-back) ─────────────────────────────


@dataclass
class BrowserDeps:
    """Injectable browser-surface dependencies — the default reads the live DOM via Pyodide."""

    snapshot: Callable[[Any], LayoutInput]
    query_by_node_id: Callable[[str], Any]
    query_descendants: Callable[[Any], list[tuple[str, Any]]]


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pyodide_snapshot(element: Any) -> LayoutInput:
    """Read a :class:`LayoutInput` from a live DOM element via Pyodide."""
    import js  # noqa: PLC0415 — lazy; only importable under Pyodide.

    rect = element.getBoundingClientRect()
    style = js.window.getComputedStyle(element)
    min_width = _num(str(style.minWidth).replace("px", "")) if style.minWidth else None
    min_height = _num(str(style.minHeight).replace("px", "")) if style.minHeight else None
    return LayoutInput(
        width=float(rect.width),
        height=float(rect.height),
        scroll_width=_num(element.scrollWidth),
        scroll_height=_num(element.scrollHeight),
        client_width=_num(element.clientWidth),
        client_height=_num(element.clientHeight),
        overflow_x=str(style.overflowX) if style.overflowX else None,
        overflow_y=str(style.overflowY) if style.overflowY else None,
        min_width=min_width,
        min_height=min_height,
        element_rect=(float(rect.left), float(rect.top), float(rect.right), float(rect.bottom)),
    )


def _pyodide_query_by_node_id(node_id: str) -> Any:
    import js  # noqa: PLC0415

    return js.document.querySelector(f'[data-fuaran-node-id="{node_id}"]')


def _pyodide_query_descendants(root: Any) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for el in root.querySelectorAll("[data-fuaran-node-id]"):
        node_id = el.getAttribute("data-fuaran-node-id")
        if node_id:
            out.append((str(node_id), el))
    return out


def _pyodide_deps() -> BrowserDeps:
    return BrowserDeps(
        snapshot=_pyodide_snapshot,
        query_by_node_id=_pyodide_query_by_node_id,
        query_descendants=_pyodide_query_descendants,
    )


class BrowserLayoutObserver:
    """Reads live geometry under Pyodide (client-side Python in the browser).

    ``observe`` / ``observe_tree`` perform a live DOM read-back via the injectable
    :class:`BrowserDeps` (default: the ``js`` interop module). ``subscribe`` +
    ``refresh`` give a manual push channel honouring the change-detection policy.
    """

    def __init__(
        self,
        options: LayoutObserverOptions = DEFAULT_OPTIONS,
        deps: BrowserDeps | None = None,
    ) -> None:
        self._options = options
        self._deps = deps if deps is not None else _pyodide_deps()
        self._last_flags: dict[str, list] = {}
        self._subscribers: list[Subscriber] = []

    def _to_obs(self, node_id: str, element: Any) -> LayoutObservation:
        return to_layout_observation(self._options, node_id, self._deps.snapshot(element))

    def observe(self, node_id: str) -> LayoutObservation | None:
        """Read back the resolved geometry for a single node from the live DOM."""
        element = self._deps.query_by_node_id(node_id)
        return None if element is None else self._to_obs(node_id, element)

    def observe_tree(self, root_id: str) -> list[LayoutObservation]:
        """Read back the root + every ``[data-fuaran-node-id]`` descendant from the live DOM."""
        root = self._deps.query_by_node_id(root_id)
        if root is None:
            return []
        result = [self._to_obs(root_id, root)]
        for node_id, element in self._deps.query_descendants(root):
            result.append(self._to_obs(node_id, element))
        return result

    def subscribe(self, handler: Subscriber) -> Callable[[], None]:
        self._subscribers.append(handler)

        def unsubscribe() -> None:
            if handler in self._subscribers:
                self._subscribers.remove(handler)

        return unsubscribe

    def refresh(self, node_id: str) -> None:
        """Re-read one node and emit to subscribers per the change-detection policy."""
        obs = self.observe(node_id)
        if obs is None:
            return
        previous = self._last_flags.get(node_id)
        initial = previous is None
        self._last_flags[node_id] = obs.flags
        if initial or not self._options.emit_on_flag_change_only or not flags_equal(obs.flags, previous or []):
            for sub in self._subscribers:
                try:
                    sub(node_id, obs)
                except Exception:  # noqa: BLE001
                    pass
