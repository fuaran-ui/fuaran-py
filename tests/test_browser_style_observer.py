"""BrowserStyleObserver — the live read-back logic, driven by an injected fake DOM.

The default deps read the live DOM via Pyodide ``getComputedStyle`` (browser-only,
not unit-testable headlessly). These tests inject a fake DOM through ``BrowserDeps``
to exercise the observer's read-back / tree-walk / subscribe / manifest paths in
plain CPython — the same injectable-deps discipline the TS browser observer uses.
"""

from __future__ import annotations

from fuaran_py.style_observer import (
    BLACK,
    WHITE,
    BrowserDeps,
    BrowserStyleObserver,
    StyleInput,
    StyleObservation,
    TokenResolutionFailed,
    rgb,
)
from fuaran_py.theme_manifest import ManifestToken, RoleBinding, ThemeManifest, ToneRole


def _deps(dom: dict[str, StyleInput], tree: dict[str, list[str]] | None = None) -> BrowserDeps:
    children = tree or {}

    def snapshot(el: object) -> StyleInput:
        return dom[str(el)]

    def query_by_node_id(node_id: str) -> object:
        return node_id if node_id in dom else None

    def query_descendants(root: object) -> list[tuple[str, object]]:
        return [(c, c) for c in children.get(str(root), [])]

    return BrowserDeps(snapshot=snapshot, query_by_node_id=query_by_node_id, query_descendants=query_descendants)


def _invisible() -> StyleInput:
    return StyleInput(foreground=WHITE, background_layers=[WHITE])


def _legible() -> StyleInput:
    return StyleInput(foreground=BLACK, background_layers=[WHITE])


def test_observe_live_read_back() -> None:
    obs = BrowserStyleObserver(deps=_deps({"card-1": _invisible()}))
    snap = obs.observe("card-1")
    assert snap is not None and [type(f).__name__ for f in snap.flags] == ["InvisibleText"]
    assert obs.observe("absent") is None


def test_observe_tree() -> None:
    dom = {"root": _legible(), "child": _invisible()}
    obs = BrowserStyleObserver(deps=_deps(dom, {"root": ["child"]}))
    result = obs.observe_tree("root")
    assert [o.node_id for o in result] == ["root", "child"]
    assert obs.observe_tree("absent") == []


def test_refresh_emits_and_change_only() -> None:
    obs = BrowserStyleObserver(deps=_deps({"card-1": _invisible()}))
    seen: list[StyleObservation] = []
    obs.subscribe(lambda _nid, o: seen.append(o))
    obs.refresh("card-1")
    assert len(seen) == 1
    obs.refresh("card-1")  # unchanged flags → suppressed
    assert len(seen) == 1


def test_manifest_aware_flags_flow_through_observe() -> None:
    manifest = ThemeManifest(
        tokens=[ManifestToken("color.brand.base", "color", "#3b5bdb")],
        roles=[RoleBinding(ToneRole("Brand"), "color.brand.base")],
    )
    dom = {"c1": StyleInput(foreground=BLACK, background_layers=[rgb(200, 0, 0)], emitted_tone="Critical")}
    obs = BrowserStyleObserver(manifest=manifest, deps=_deps(dom))
    snap = obs.observe("c1")
    assert snap is not None and TokenResolutionFailed("Critical") in snap.flags
