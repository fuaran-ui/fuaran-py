"""The data-bound grid's row pipeline: sort, then page, then window (Phase 1892).

A ``DataGrid`` naming a ``windowStateKey`` presents a WINDOW of its rows — the
slice a viewport can show — and may declare the size of the whole result set
through ``rowTotal`` (WIRE_FORMAT.md, "Row window and declared total"). These are
the pure pieces that decide which rows that is, transcribed from the
specification's rules so the corpus's ``grid-window/`` behaviour vectors run
through exactly the functions the renderer calls:

* :func:`effective_sort` / :func:`sort_rows` — the grid's order (``sortStateKey``
  over ``defaultSort``), stable, empty cells last in both directions;
* :func:`grid_page` / :func:`slice_rows_to_page` — the 1-based page, clamped, and
  sliced only where the grid holds its whole set;
* :func:`window_of_value` / :func:`read_window_descriptor` — the validated
  ``{"offset", "count"}`` descriptor, or no window;
* :func:`resolve_row_total` — the declared total, an integer >= 0 or unknown;
* :func:`present_window` — the window itself, clamped where the grid slices;
* :func:`window_row_count` / :func:`window_row_index` — the ARIA annotations a
  windowed grid carries.

Who slices is the SOURCE shape, never a second declaration: a ``Query`` whose
``dependsOn`` names the page key returns the page, and one naming the window key
returns the window — in which case the grid slices nothing at all. This host is a
static renderer: it performs the slice the seeded State determines and writes
nothing back.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cmp_to_key

from ..model import Arr, Obj, Value
from .bindings import BindingSourcesLike, as_sources, resolve_scalar_number


@dataclass(frozen=True)
class RowWindow:
    """A usable window descriptor: the 0-based offset of the first row and how
    many rows the window holds."""

    offset: int
    count: int


@dataclass(frozen=True)
class PresentedWindow[R]:
    """What a grid presents once the window is applied: the rows, the index of the
    first of them in the range the window moves over, that range's size where it
    is known, and whether a window is in effect at all."""

    rows: list[R]
    offset: int
    total: int | None
    windowed: bool


def _json_int(value: object) -> int | None:
    """A JSON integer from either spelling a store can hold: an int, or a float
    with no fractional part (``2.0`` is ``2``). ``bool`` is not an integer."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return None


def _members(value: object) -> Mapping[str, object] | None:
    """An object's members, from a decoded ``Obj`` or a host-supplied mapping."""
    if isinstance(value, Obj):
        return value.fields
    if isinstance(value, Mapping):
        return value
    return None


def _state_value(sources: BindingSourcesLike | None, key: str) -> tuple[bool, object]:
    values = as_sources(sources).values
    return (key in values, values.get(key))


# ── Sort ─────────────────────────────────────────────────────────────────────


def read_sort_descriptor(value: object) -> tuple[int, str] | None:
    """A ``{"column", "direction"}`` descriptor, validated: a column index >= 0 and
    a direction of ``asc`` / ``desc``. Anything else is no sort."""
    members = _members(value)
    if members is None:
        return None
    column = _json_int(members.get("column"))
    direction = members.get("direction")
    if column is not None and column >= 0 and direction in ("asc", "desc"):
        assert isinstance(direction, str)
        return (column, direction)
    return None


def effective_sort(
    sort_state_key: object, default_sort: object, sources: BindingSourcesLike | None
) -> tuple[int, str] | None:
    """The grid's effective order: the state slot decides, and a declared
    ``defaultSort`` fills only the not-yet-sorted case (nothing held at the key).
    A key holding something that is not a usable descriptor is the AUTHORED
    order."""
    declared = read_sort_descriptor(default_sort)
    if not isinstance(sort_state_key, str):
        return declared
    present, held = _state_value(sources, sort_state_key)
    if not present:
        return declared
    return read_sort_descriptor(held)


def _cell_rank(value: object) -> int:
    if value is None:
        return 4
    if isinstance(value, bool):
        return 1
    if isinstance(value, (int, float)):
        return 0
    if isinstance(value, str):
        return 3
    return 4


def _compare_cells(a: object, b: object) -> int:
    ra, rb = _cell_rank(a), _cell_rank(b)
    if ra != rb:
        return (ra > rb) - (ra < rb)
    if ra == 3:
        assert isinstance(a, str) and isinstance(b, str)
        la, lb = a.lower(), b.lower()
        return (la > lb) - (la < lb)
    if ra in (0, 1):
        assert isinstance(a, (int, float)) and isinstance(b, (int, float))
        return (a > b) - (a < b)
    return 0


def _row_cell(row: object, field: str) -> object:
    members = _members(row)
    return None if members is None else members.get(field)


def sort_rows[R](fields: Sequence[str | None], descriptor: tuple[int, str] | None, rows: Sequence[R]) -> list[R]:
    """Sort rows by a descriptor over the columns' ``field`` names. A column index
    outside the set, or a column with no field, leaves the authored order
    standing. Empty cells sort LAST in both directions; the sort is stable."""
    if descriptor is None:
        return list(rows)
    column, direction = descriptor
    if column >= len(fields) or fields[column] is None:
        return list(rows)
    field = fields[column]
    assert field is not None
    sign = -1 if direction == "desc" else 1

    def compare(a: R, b: R) -> int:
        ka, kb = _row_cell(a, field), _row_cell(b, field)
        empty_a, empty_b = _cell_rank(ka) == 4, _cell_rank(kb) == 4
        if empty_a or empty_b:
            return int(empty_a) - int(empty_b)
        return sign * _compare_cells(ka, kb)

    return sorted(rows, key=cmp_to_key(compare))


# ── Page ─────────────────────────────────────────────────────────────────────


def source_host_slices_on(source: Value | None, key: str) -> bool:
    """Does this source slice HOST-side on ``key``? True for a ``Query`` whose
    ``dependsOn`` names it: the query re-runs on a change and returns the slice."""
    if not isinstance(source, Obj) or source.tag != "Query":
        return False
    depends_on = source.fields.get("dependsOn")
    return isinstance(depends_on, Arr) and key in depends_on.items


def read_page_descriptor(sources: BindingSourcesLike | None, key: str) -> int:
    """The 1-based page held at ``key`` as ``{"page": N}``; anything unusable is
    page 1."""
    members = _members(_state_value(sources, key)[1])
    page = None if members is None else _json_int(members.get("page"))
    return page if page is not None and page >= 1 else 1


def page_count_of(page_size: int, row_count: int) -> int:
    """How many pages a row count divides into — always at least one."""
    if page_size <= 0:
        return 1
    return max(1, (row_count + page_size - 1) // page_size)


def slice_rows_to_page[R](page_size: int, page: int, rows: Sequence[R]) -> list[R]:
    """The rows on ``page`` (1-based) at ``page_size``, after clamping to the last page."""
    if page_size <= 0:
        return list(rows)
    clamped = min(max(1, page), page_count_of(page_size, len(rows)))
    start = min(len(rows), (clamped - 1) * page_size)
    return list(rows[start : start + page_size])


def declared_page_count(page_size: int, declared_total: int | None) -> int | None:
    """The page count a HOST-paged grid can state once its total is declared."""
    return None if declared_total is None else page_count_of(page_size, declared_total)


@dataclass(frozen=True)
class GridPage:
    """The page a paged grid shows, and the last page it can name (``None`` where a
    host-paged grid declares no total, so its pager keeps to previous/next)."""

    size: int
    page: int
    host_pages: bool
    last_page: int | None


def grid_page(sources: BindingSourcesLike | None, grid: Mapping[str, Value], row_count: int) -> GridPage | None:
    """The page rule plus the declared total, or ``None`` for a grid that does not
    page. A client-paged grid counts its own rows; a host-paged grid clamps and
    states a page count only when the document declares ``rowTotal``."""
    key, size = grid.get("pageStateKey"), grid.get("pageSize")
    if not isinstance(key, str) or isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        return None
    host_pages = source_host_slices_on(grid.get("source"), key)
    requested = read_page_descriptor(sources, key)
    last_page = (
        declared_page_count(size, resolve_row_total(grid.get("rowTotal"), sources))
        if host_pages
        else page_count_of(size, row_count)
    )
    page = min(max(1, requested), last_page) if last_page is not None else max(1, requested)
    return GridPage(size, page, host_pages, last_page)


# ── Window ───────────────────────────────────────────────────────────────────


def window_of_value(value: object) -> RowWindow | None:
    """Validate a window descriptor: usable only as an object whose ``offset`` is an
    integer >= 0 and whose ``count`` an integer >= 1. Every other shape is NO
    window, so no malformed value can hide a row."""
    members = _members(value)
    if members is None:
        return None
    offset, count = _json_int(members.get("offset")), _json_int(members.get("count"))
    if offset is not None and count is not None and offset >= 0 and count >= 1:
        return RowWindow(offset, count)
    return None


def read_window_descriptor(sources: BindingSourcesLike | None, key: str) -> RowWindow | None:
    """The window descriptor held at ``key`` in State, validated."""
    return window_of_value(_state_value(sources, key)[1])


def resolve_row_total(row_total: Value | None, sources: BindingSourcesLike | None) -> int | None:
    """A declared ``rowTotal``: an integer >= 0, or ``None`` — no declared total, so
    the total is unknown rather than guessed. A fractional or negative value is
    refused."""
    if row_total is None:
        return None
    number = resolve_scalar_number(row_total, sources)
    if number is None or not math.isfinite(number) or not number.is_integer() or number < 0:
        return None
    return int(number)


def present_window[R](
    host_windows: bool, declared_total: int | None, window: RowWindow | None, rows: Sequence[R]
) -> PresentedWindow[R]:
    """The window a grid presents over ``rows`` (the range it holds after sort and
    page).

    A host-windowed grid slices nothing: the rows ARE the window, its position is
    the descriptor's offset (0 where there is none yet) and its total the declared
    one. Otherwise the offset clamps to ``min(offset, max(0, n - count))`` — a
    window past the end presents the last full window — and the total is the
    range's own row count.
    """
    if host_windows:
        return PresentedWindow(list(rows), window.offset if window is not None else 0, declared_total, True)
    n = len(rows)
    if window is None:
        return PresentedWindow(list(rows), 0, n, False)
    offset = min(window.offset, max(0, n - window.count))
    return PresentedWindow(list(rows[offset : offset + window.count]), offset, n, True)


def grid_host_windows(grid: Mapping[str, Value]) -> bool:
    """Does this grid's HOST return the window? Then the grid slices nothing."""
    key = grid.get("windowStateKey")
    return isinstance(key, str) and source_host_slices_on(grid.get("source"), key)


def grid_window[R](
    sources: BindingSourcesLike | None, grid: Mapping[str, Value], rows: Sequence[R]
) -> PresentedWindow[R] | None:
    """The window a grid presents over ``rows``, or ``None`` for a grid naming no
    window key. The declared total is read only where the host windows."""
    key = grid.get("windowStateKey")
    if not isinstance(key, str):
        return None
    host_windows = source_host_slices_on(grid.get("source"), key)
    declared = resolve_row_total(grid.get("rowTotal"), sources) if host_windows else None
    return present_window(host_windows, declared, read_window_descriptor(sources, key), rows)


def window_row_count[R](window: PresentedWindow[R] | None) -> int | None:
    """The table's ``aria-rowcount``: the total plus the header row, ``-1`` where
    the total is unknown; ``None`` where no window is in effect."""
    if window is None or not window.windowed:
        return None
    return window.total + 1 if window.total is not None else -1


def window_row_index[R](window: PresentedWindow[R] | None, row_index: int) -> int | None:
    """A presented row's ``aria-rowindex`` (its 0-based index in the range plus 2,
    the header being row 1); ``None`` where no window is in effect."""
    if window is None or not window.windowed:
        return None
    return window.offset + row_index + 2
