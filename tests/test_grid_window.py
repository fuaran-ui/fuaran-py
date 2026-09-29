"""The DataGrid row window and the declared total (Phase 1892).

Two halves. The corpus half runs the ``grid-window/`` behaviour vectors — whose
expected answers the corpus computes from the specification's rules, not from any
host — through THIS host's own grid sort, page slice, descriptor reader and
window function, exactly as the family's description prescribes. The unit half
pins what the vectors cannot reach: the declared-total resolution over binding
shapes, and the static renderer's ARIA annotations and declared-total pager.

The vector family is self-enumerated and is not part of the bundled snapshot, so
it is read from the AUTHORITATIVE corpus only: absent that corpus the half skips,
and with it present a missing family file FAILS — a behaviour family cannot be
certified by reading nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from _corpus import AUTHORITY_ROOT
from fuaran_ui import decode_node
from fuaran_ui.model import Arr, Obj, Value
from fuaran_ui.renderer import render_html
from fuaran_ui.renderer.bindings import BindingSources
from fuaran_ui.renderer.grid import (
    PresentedWindow,
    declared_page_count,
    present_window,
    read_window_descriptor,
    resolve_row_total,
    slice_rows_to_page,
    sort_rows,
    window_of_value,
    window_row_count,
    window_row_index,
)

_AUTHORITY_PRESENT = (AUTHORITY_ROOT / "manifest.json").is_file()
_VECTORS_FILE: Path = AUTHORITY_ROOT / "grid-window" / "grid-window-vectors.json"


def _vectors() -> list[dict[str, Any]]:
    assert _VECTORS_FILE.is_file(), (
        f"the corpus at {AUTHORITY_ROOT} holds no grid-window/grid-window-vectors.json — "
        "a behaviour family cannot be certified by reading nothing"
    )
    vectors: list[dict[str, Any]] = json.loads(_VECTORS_FILE.read_text(encoding="utf-8"))["vectors"]
    return vectors


def _run(vector_input: dict[str, Any]) -> PresentedWindow[dict[str, Any]]:
    """One vector through this host's own pieces, in the family's order."""
    slicing = vector_input["slicing"]
    rows: list[dict[str, Any]] = vector_input["rows"]
    sort = vector_input.get("sort")
    if isinstance(sort, dict):
        rows = sort_rows(vector_input["columns"], (sort["column"], sort["direction"]), rows)
    page = vector_input.get("page")
    if slicing == "client" and isinstance(page, dict):
        rows = slice_rows_to_page(page["size"], page["page"], rows)
    # The raw descriptor and total go through the STATE read, as the renderer's do.
    state: dict[str, object] = {}
    if "window" in vector_input:
        state["vector-window"] = vector_input["window"]
    if "rowTotal" in vector_input:
        state["vector-total"] = vector_input["rowTotal"]
    sources = BindingSources(values=state)
    host_windows = slicing == "hostWindows"
    declared = resolve_row_total(Obj("State", {"key": "vector-total"}), sources) if host_windows else None
    return present_window(host_windows, declared, read_window_descriptor(sources, "vector-window"), rows)


@pytest.mark.skipif(not _AUTHORITY_PRESENT, reason=f"authoritative corpus not found at {AUTHORITY_ROOT}")
def test_the_grid_window_vectors_agree_with_this_hosts_window_function() -> None:
    vectors = _vectors()
    assert len(vectors) >= 20, "the family carries its full vector set"
    for vector in vectors:
        vid, expected = vector["id"], vector["expected"]
        got = _run(vector["input"])
        assert got.windowed == expected["windowed"], f"{vid}: windowed"
        assert got.offset == expected["offset"], f"{vid}: offset"
        assert [r["id"] for r in got.rows] == expected["rowIds"], f"{vid}: presented rows"
        assert got.total == expected["total"], f"{vid}: total"


# ── Descriptor and declared total ────────────────────────────────────────────


def test_a_decoded_descriptor_object_is_usable() -> None:
    held = Obj(None, {"offset": 3, "count": 2.0})
    assert window_of_value(held) is not None
    assert read_window_descriptor(BindingSources(values={"w": held}), "w") == window_of_value(held)
    assert read_window_descriptor(BindingSources(), "w") is None


def test_a_boolean_member_is_not_an_integer() -> None:
    assert window_of_value({"offset": True, "count": 5}) is None


@pytest.mark.parametrize(
    ("binding", "values", "expected"),
    [
        (Obj("Static", {"value": 120}), {}, 120),
        (Obj("Query", {"name": "orders.total"}), {"orders.total": 120.0}, 120),
        (Obj("Query", {"name": "orders.total"}), {}, None),
        (Obj("State", {"key": "t", "defaultValue": 7}), {}, 7),
        (Obj("State", {"key": "t"}), {"t": -1}, None),
        (Obj("State", {"key": "t"}), {"t": 2.5}, None),
        (Obj("State", {"key": "t"}), {"t": "12"}, None),
        (Obj("State", {"key": "t"}), {"t": True}, None),
    ],
)
def test_the_declared_total_is_an_integer_at_least_zero_or_unknown(
    binding: Value, values: dict[str, object], expected: int | None
) -> None:
    assert resolve_row_total(binding, BindingSources(values=values)) == expected


def test_the_aria_annotations_exist_only_where_a_window_is_in_effect() -> None:
    unwindowed = present_window(False, None, None, [1, 2, 3])
    assert window_row_count(unwindowed) is None
    assert window_row_index(unwindowed, 0) is None
    host = present_window(True, None, window_of_value({"offset": 40, "count": 10}), [1, 2])
    assert window_row_count(host) == -1
    assert window_row_index(host, 1) == 43


def test_the_declared_page_count_is_at_least_one() -> None:
    assert declared_page_count(20, None) is None
    assert declared_page_count(20, 0) == 1
    assert declared_page_count(20, 41) == 3


# ── The static renderer ──────────────────────────────────────────────────────

_ROWS = [{"id": f"r{i:02d}", "score": (i * 37) % 101} for i in range(12)]


def _grid(**extra: object) -> dict[str, object]:
    return {
        "id": "g",
        "kind": {
            "$type": "DataGrid",
            "columns": [
                {"field": "id", "kind": {"$type": "Text"}, "label": "Id"},
                {"field": "score", "kind": {"$type": "Numeric"}, "label": "Score"},
            ],
            "rowKeyField": "id",
            "source": {"$type": "State", "key": "rows", "defaultValue": _ROWS},
            **extra,
        },
    }


def _render(doc: dict[str, object], values: dict[str, object] | None = None) -> str:
    decoded = decode_node(json.dumps(doc))
    assert decoded.ok, decoded
    return render_html(decoded.value, BindingSources(values=values or {}))


def test_an_unwindowed_grid_carries_no_row_annotations() -> None:
    html = _render(_grid())
    assert "aria-rowcount" not in html
    assert "aria-rowindex" not in html
    assert html.count('<tr class="fuaran-grid-row">') == 12


def test_a_window_key_with_no_descriptor_renders_what_the_grid_rendered_before() -> None:
    assert _render(_grid(windowStateKey="w")) == _render(_grid())


def test_a_client_window_presents_its_slice_with_row_annotations() -> None:
    html = _render(_grid(windowStateKey="w"), {"w": Obj(None, {"offset": 4, "count": 3})})
    assert '<table class="fuaran-grid" aria-rowcount="13">' in html
    assert html.count("aria-rowindex=") == 3
    assert 'aria-rowindex="6"' in html and 'aria-rowindex="8"' in html
    assert "<span>r04</span>" in html and "<span>r06</span>" in html
    assert "<span>r03</span>" not in html and "<span>r07</span>" not in html


def test_a_window_ranges_over_the_sorted_rows() -> None:
    html = _render(
        _grid(windowStateKey="w", defaultSort={"column": 1, "direction": "desc"}),
        {"w": Obj(None, {"offset": 0, "count": 1})},
    )
    top = max(range(len(_ROWS)), key=lambda i: (i * 37) % 101)
    assert f"<span>r{top:02d}</span>" in html
    assert html.count("aria-rowindex=") == 1


def test_a_window_within_a_client_page_has_an_inert_pager() -> None:
    html = _render(
        _grid(windowStateKey="w", pageStateKey="p", pageSize=5),
        {"w": Obj(None, {"offset": 0, "count": 2}), "p": Obj(None, {"page": 3})},
    )
    # Page 3 of 12 rows at 5 a page holds r10, r11; the window takes both.
    assert "<span>r10</span>" in html and "<span>r11</span>" in html
    assert '<div class="fuaran-grid-paged">' in html
    assert "Page 3 of 3" in html
    assert html.count('disabled=""') == 2


def _host_paged(total: dict[str, object] | None) -> dict[str, object]:
    doc = _grid(
        windowStateKey="w",
        pageStateKey="p",
        pageSize=5,
        **({"rowTotal": total} if total is not None else {}),
    )
    kind = doc["kind"]
    assert isinstance(kind, dict)
    kind["source"] = {"$type": "Query", "name": "orders", "dependsOn": ["p"]}
    return doc


def _page_rows() -> Arr:
    return Arr([Obj(None, dict(r)) for r in _ROWS[:5]])


def test_a_host_paged_grid_with_a_declared_total_states_and_clamps_its_page_count() -> None:
    html = _render(
        _host_paged({"$type": "Query", "name": "orders.total"}),
        {"orders": _page_rows(), "orders.total": 42, "p": Obj(None, {"page": 99})},
    )
    assert "Page 9 of 9" in html
    # The host returned the page, so the grid does not slice it again.
    assert html.count('<tr class="fuaran-grid-row">') == 5


def test_a_host_paged_grid_without_a_declared_total_keeps_to_previous_and_next() -> None:
    html = _render(_host_paged(None), {"orders": _page_rows(), "p": Obj(None, {"page": 4})})
    assert ">Page 4<" in html
    assert " of " not in html


def test_a_host_windowed_grid_slices_nothing_and_reports_the_declared_total() -> None:
    doc = _grid(windowStateKey="w", rowTotal={"$type": "Query", "name": "orders.total"})
    kind = doc["kind"]
    assert isinstance(kind, dict)
    kind["source"] = {"$type": "Query", "name": "orders", "dependsOn": ["w"]}
    html = _render(doc, {"orders": _page_rows(), "orders.total": 5000, "w": Obj(None, {"offset": 100, "count": 5})})
    assert '<table class="fuaran-grid" aria-rowcount="5001">' in html
    assert 'aria-rowindex="102"' in html and 'aria-rowindex="106"' in html
    assert html.count("aria-rowindex=") == 5


# ── Phase 1912: every bound grid sorts, pages and carries the pager ──────────
#
# Phase 1892 ran the sort / page / window pipeline only for a grid naming a
# `windowStateKey`; a merely sorted or paged grid on this host presented its
# authored rows in full. The reference bound grid sorts and pages every grid, so
# these pin the same for a grid with no window key at all.


def _row_ids(html: str) -> list[str]:
    return re.findall(r"<td class=\"fuaran-grid-cell\"><span>(r\d\d)</span>", html)


def test_a_sorted_unwindowed_grid_presents_its_rows_in_the_effective_order() -> None:
    html = _render(_grid(defaultSort={"column": 1, "direction": "asc"}))
    expected = [r["id"] for r in sorted(_ROWS, key=lambda r: r["score"])]
    assert _row_ids(html) == expected
    assert "aria-rowindex" not in html


def test_the_sort_state_overrides_the_declared_default_on_an_unwindowed_grid() -> None:
    html = _render(
        _grid(sortStateKey="s", defaultSort={"column": 1, "direction": "asc"}),
        {"s": Obj(None, {"column": 1, "direction": "desc"})},
    )
    expected = [r["id"] for r in sorted(_ROWS, key=lambda r: r["score"], reverse=True)]
    assert _row_ids(html) == expected


def test_a_client_paged_unwindowed_grid_presents_its_page_and_an_inert_pager() -> None:
    html = _render(_grid(pageStateKey="p", pageSize=5), {"p": Obj(None, {"page": 2})})
    assert _row_ids(html) == ["r05", "r06", "r07", "r08", "r09"]
    assert '<div class="fuaran-grid-paged">' in html
    assert "Page 2 of 3" in html
    assert html.count('disabled=""') == 2


def test_a_page_past_the_end_clamps_to_the_last_page() -> None:
    html = _render(_grid(pageStateKey="p", pageSize=5), {"p": Obj(None, {"page": 40})})
    assert _row_ids(html) == ["r10", "r11"]
    assert "Page 3 of 3" in html


def test_a_grid_that_neither_sorts_nor_pages_is_unchanged() -> None:
    html = _render(_grid())
    assert _row_ids(html) == [r["id"] for r in _ROWS]
    assert "fuaran-grid-paged" not in html
