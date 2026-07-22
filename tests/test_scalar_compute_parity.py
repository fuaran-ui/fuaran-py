"""Phase 648 — render-time compute resolution + SSR↔interactive parity.

The renderer wires the corpus-certified compute evaluator into its scalar and
row slots: a ``Bound`` ``Transform`` in a scalar slot resolves to its 1×1 result
cell (the Phase 632 law), a ``Selection`` with a ``defaultValue`` resolves its
declared default (Phase 629), and a data-bearing ``source`` resolves to the
transformed rows. These tests pin the resolved values the three corpus fixtures
carry AND assert the two Python render surfaces — the server-HTML
``render_html`` and the interactive ``FuaranRuntime.render`` — agree byte-for-byte,
so a future divergence between them fails loudly rather than silently.

Skipped when the workspace corpus is absent (a standalone ``fuaran-py``
checkout), mirroring the other corpus-backed suites.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import decode_node
from fuaran_py.model import Node
from fuaran_py.renderer import render_html
from fuaran_py.runtime import BrowserDeps, FuaranRuntime


def _decode(name: str) -> Node:
    text = (CORPUS_ROOT / "nodes" / f"{name}.json").read_text(encoding="utf-8")
    result = decode_node(text)
    assert result.ok, getattr(result, "error", result)
    return result.value


def _fake_deps() -> BrowserDeps:
    """A substrate-free `BrowserDeps` — never touches the Pyodide `js` path, so
    the interactive runtime constructs + renders under plain CPython."""

    class _El:
        inner_html: str = ""

    store: dict[str, _El] = {}

    def get_element_by_id(element_id: str) -> Any:
        return store.setdefault(element_id, _El())

    def set_inner_html(element: Any, html: str) -> None:
        element.inner_html = html

    def add_event_listener(element: Any, event: str, handler: Callable[[Any], None]) -> Callable[[], None]:
        return lambda: None

    return BrowserDeps(get_element_by_id, set_inner_html, add_event_listener)


def _both_surfaces(tree: Node) -> tuple[str, str]:
    """Render the tree through both Python surfaces: the server-HTML renderer and
    the interactive runtime's render pass."""
    server = render_html(tree)
    interactive = FuaranRuntime(tree, deps=_fake_deps()).render()
    return server, interactive


@corpus_required
def test_scalar_transform_composition_resolves_on_both_surfaces() -> None:
    # Badge label: a Transform ending in a global single-`count` agg — the 1×1
    # scalar law resolves the lone cell to the count "2". Callout body: a
    # Transform (project alert + limit 1) resolves the row's alert text.
    server, interactive = _both_surfaces(_decode("scalar-transform-composition"))
    assert server == interactive, "server-HTML and interactive surfaces diverged"
    for html in (server, interactive):
        assert 'fuaran-badge-critical">2<' in html
        assert 'fuaran-callout-body">TCK-2041 breaches SLA in 2 hours<' in html


@corpus_required
def test_master_detail_preselected_resolves_selection_default() -> None:
    # The detail Fact binds a `Selection` with a `defaultValue`; with no
    # selection written, resolution-time defaulting (Phase 629) yields "TCK-2041".
    server, interactive = _both_surfaces(_decode("master-detail-preselected"))
    assert server == interactive, "server-HTML and interactive surfaces diverged"
    for html in (server, interactive):
        assert "fuaran-fact-value" in html
        assert ">TCK-2041<" in html


@corpus_required
def test_filterable_static_dashboard_prunes_unset_filters_stably() -> None:
    # No scalar slot resolves (the Transform params come from Filters with no
    # defaults), so unset filters PRUNE their pipeline stages: the unfiltered
    # frame flows through. The Line chart's Transform source resolves to all rows
    # and lowers to inline Drawing SVG; the grid placeholder reports the full
    # row count of 2. (The Filters block itself is a baseline stub in this host,
    # unrelated to the compute wiring under test.)
    server, interactive = _both_surfaces(_decode("filterable-static-dashboard"))
    assert server == interactive, "server-HTML and interactive surfaces diverged"
    for html in (server, interactive):
        assert "fuaran-filters" in html
        assert "fuaran-drawing" in html and "<svg" in html
        assert 'data-fuaran-ssr-placeholder="DataGrid"' in html
        assert 'data-fuaran-row-count="2"' in html


@corpus_required
def test_scalar_law_is_loud_on_ambiguity() -> None:
    # The 1×1 law: a >1-row result in a scalar slot is a loud miss (renders
    # absence — never a silent first cell). The scalar-transform grid's source
    # (empty pipeline, 3 rows) in a text slot resolves to nothing, not "TCK-2041".
    from fuaran_py.renderer.bindings import _scalar_cell  # noqa: PLC0415
    from fuaran_py.schema import decode_node as _dn

    # A Transform whose result is 3 rows × 3 cols → ambiguous → ("error", None).
    text = (CORPUS_ROOT / "nodes" / "scalar-transform-composition.json").read_text(encoding="utf-8")
    tree = _dn(text).value
    # Reach the grid node's Transform source and confirm it is loud, not a guess.
    grid = tree.kind.fields["children"].items[0]
    transform = grid.kind.fields["source"]
    outcome, value = _scalar_cell(transform, None)
    assert outcome == "error" and value is None
