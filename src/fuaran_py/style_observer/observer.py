"""The observer implementations — Python port of ``Fuaran.UI.StyleObserver``.

- :class:`InMemoryStyleObserver` — fixture-driven, substrate-free; drives tests +
  non-browser hosts. Walks a parent-pointer graph for ``observe_tree``.
- :class:`BrowserStyleObserver` — reads **live** ``getComputedStyle`` under Pyodide
  (Python compiled to WASM running client-side in the browser — the Python
  analogue of the F# Fable browser observer). Browser-API access is behind an
  injectable :class:`BrowserDeps` object: the default reads the live DOM via the
  ``js`` interop module; tests inject a fake DOM. This is the host that closes the
  "client-side computed-style observer in Python" parity gap.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from ..theme_manifest import ThemeManifest
from .color import TRANSPARENT, Rgba, rgb, rgba
from .flags import (
    DEFAULT_OPTIONS,
    StyleInput,
    StyleObservation,
    StyleObserverOptions,
    baseline_style_input,
    flags_equal,
    to_style_observation,
)
from .manifest_flags import per_node_flags

Subscriber = Callable[[str, StyleObservation], None]


def _with_manifest(manifest: ThemeManifest | None, obs: StyleObservation) -> StyleObservation:
    """Append the manifest-aware flags when a manifest is wired; pass through otherwise."""
    if manifest is None:
        return obs
    return replace(obs, flags=[*obs.flags, *per_node_flags(manifest, obs)])


# ── InMemoryStyleObserver ──────────────────────────────────────────────────────


class InMemoryStyleObserver:
    """Fixture-driven observer — substrate-free, for tests + non-browser hosts."""

    def __init__(self, options: StyleObserverOptions = DEFAULT_OPTIONS, manifest: ThemeManifest | None = None) -> None:
        self._options = options
        self._manifest = manifest
        self._registry: dict[str, tuple[StyleInput, str | None]] = {}
        self._last_flags: dict[str, list] = {}
        self._subscribers: list[Subscriber] = []

    def _to_obs(self, node_id: str, inp: StyleInput) -> StyleObservation:
        return _with_manifest(self._manifest, to_style_observation(self._options, node_id, inp))

    def _emit(self, node_id: str, obs: StyleObservation) -> None:
        for sub in self._subscribers:
            try:
                sub(node_id, obs)
            except Exception:  # noqa: BLE001 — a throwing subscriber must not poison siblings.
                pass

    def register_fixture(self, node_id: str, inp: StyleInput, parent: str | None = None) -> None:
        """Register or replace a fixture; fires an initial emission unconditionally."""
        self._registry[node_id] = (inp, parent)
        obs = self._to_obs(node_id, inp)
        self._last_flags[node_id] = obs.flags
        self._emit(node_id, obs)

    def update(self, node_id: str, inp: StyleInput) -> None:
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

    def observe(self, node_id: str) -> StyleObservation | None:
        entry = self._registry.get(node_id)
        return None if entry is None else self._to_obs(node_id, entry[0])

    def observe_tree(self, root_id: str) -> list[StyleObservation]:
        if root_id not in self._registry:
            return []
        children: dict[str, list[str]] = {}
        for node_id, (_, parent) in self._registry.items():
            if parent is not None:
                children.setdefault(parent, []).append(node_id)
        acc: list[StyleObservation] = []
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

    def register(self, node_id: str, element: Any = None) -> None:
        """Bare register with no fixture creates a baseline entry so a mount hook doesn't crash."""
        if node_id not in self._registry:
            self.register_fixture(node_id, baseline_style_input())

    def unregister(self, node_id: str) -> None:
        self._registry.pop(node_id, None)
        self._last_flags.pop(node_id, None)


# ── CSS colour parsing (for the live snapshot) ─────────────────────────────────


def parse_css_color(raw: str | None) -> Rgba:
    """Parse a computed ``color`` / ``background-color`` string to an ``Rgba``.

    Computed values come back as ``rgb(r, g, b)`` / ``rgba(r, g, b, a)`` (or
    ``transparent``). Anything unrecognised becomes transparent so the layer is
    skipped by the composite walk.
    """
    if raw is None or raw in ("", "transparent", "none"):
        return TRANSPARENT
    lower = raw.strip().lower()
    if lower.startswith("rgba("):
        body = lower[5:].rstrip(")")
    elif lower.startswith("rgb("):
        body = lower[4:].rstrip(")")
    else:
        return TRANSPARENT
    try:
        parts = [float(p.strip()) for p in body.split(",")]
    except ValueError:
        return TRANSPARENT
    if len(parts) == 3:
        return rgb(parts[0], parts[1], parts[2])
    if len(parts) == 4:
        return rgba(parts[0], parts[1], parts[2], parts[3])
    return TRANSPARENT


# ── BrowserStyleObserver (Pyodide live read-back) ──────────────────────────────


@dataclass
class BrowserDeps:
    """Injectable browser-surface dependencies — the default reads the live DOM via Pyodide."""

    snapshot: Callable[[Any], StyleInput]
    query_by_node_id: Callable[[str], Any]
    query_descendants: Callable[[Any], list[tuple[str, Any]]]


def _pyodide_snapshot(element: Any) -> StyleInput:
    """Read a :class:`StyleInput` from a live DOM element via Pyodide ``getComputedStyle``."""
    import js  # noqa: PLC0415 — lazy; only importable under Pyodide.

    style = js.window.getComputedStyle(element)
    layers: list[Rgba] = []
    node = element
    while node is not None:
        layers.append(parse_css_color(js.window.getComputedStyle(node).backgroundColor))
        node = node.parentElement
    family = style.fontFamily
    font_family = None if family in (None, "") else str(family)
    tone_attr = element.getAttribute("data-fuaran-tone")
    emitted_tone = None if tone_attr in (None, "") else str(tone_attr)
    return StyleInput(
        foreground=parse_css_color(style.color),
        background_layers=layers,
        font_family=font_family,
        emitted_tone=emitted_tone,
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


class BrowserStyleObserver:
    """Reads live ``getComputedStyle`` under Pyodide (client-side Python in the browser).

    ``observe`` / ``observe_tree`` perform a live DOM read-back via the injectable
    :class:`BrowserDeps` (default: the ``js`` interop module). ``subscribe`` +
    ``refresh`` give a manual push channel; :meth:`connect` wires a live
    ``MutationObserver`` (Pyodide-only) so a theme toggle re-derives automatically.
    """

    def __init__(
        self,
        options: StyleObserverOptions = DEFAULT_OPTIONS,
        manifest: ThemeManifest | None = None,
        deps: BrowserDeps | None = None,
    ) -> None:
        self._options = options
        self._manifest = manifest
        self._deps = deps if deps is not None else _pyodide_deps()
        self._last_flags: dict[str, list] = {}
        self._subscribers: list[Subscriber] = []
        self._mutation_observer: Any = None

    def _to_obs(self, node_id: str, element: Any) -> StyleObservation:
        return _with_manifest(
            self._manifest, to_style_observation(self._options, node_id, self._deps.snapshot(element))
        )

    def observe(self, node_id: str) -> StyleObservation | None:
        """Read back the resolved styles for a single node from the live DOM."""
        element = self._deps.query_by_node_id(node_id)
        return None if element is None else self._to_obs(node_id, element)

    def observe_tree(self, root_id: str) -> list[StyleObservation]:
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

    def refresh_all(self, root_id: str) -> None:
        """Re-read every node under ``root_id`` and emit changed observations."""
        for obs in self.observe_tree(root_id):
            previous = self._last_flags.get(obs.node_id)
            initial = previous is None
            self._last_flags[obs.node_id] = obs.flags
            if initial or not self._options.emit_on_flag_change_only or not flags_equal(obs.flags, previous or []):
                for sub in self._subscribers:
                    try:
                        sub(obs.node_id, obs)
                    except Exception:  # noqa: BLE001
                        pass

    def connect(self, root_id: str) -> None:
        """Wire a live ``MutationObserver`` (Pyodide-only) that re-derives on class/style/tone mutations."""
        import js  # noqa: PLC0415 — Pyodide-only.
        from pyodide.ffi import create_proxy, to_js  # noqa: PLC0415

        root = self._deps.query_by_node_id(root_id)
        if root is None:
            return

        def _on_mutation(_records: Any, _observer: Any) -> None:
            self.refresh_all(root_id)

        callback = create_proxy(_on_mutation)
        self._mutation_observer = js.MutationObserver.new(callback)
        options = to_js(
            {
                "childList": True,
                "subtree": True,
                "attributes": True,
                "attributeFilter": ["class", "style", "data-fuaran-tone"],
            }
        )
        self._mutation_observer.observe(root, options)
        self.refresh_all(root_id)

    def disconnect(self) -> None:
        """Disconnect the live ``MutationObserver`` if one was wired via :meth:`connect`."""
        if self._mutation_observer is not None:
            self._mutation_observer.disconnect()
            self._mutation_observer = None
