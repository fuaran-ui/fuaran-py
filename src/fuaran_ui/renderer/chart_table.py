"""A lowered chart's data-table alternative, as markup (Phase 2804).

The Python twin of the reference host's ``ElementPlan.ChartTablePlan``. The chart
lowering yields the values a chart's marks were drawn from
(:func:`fuaran_ui.charts.table_model`); this module draws them as the reference
server renderer does: a native ``<details>`` disclosure after the drawing, which
the drawing's ``role="img"`` root names in ``aria-details``, holding a real table
— a ``<caption>``, one ``<th scope="col">`` per column, and each row's first cell
(its x value, the key the row is about) a ``<th scope="row">``. It needs no
script, and the bytes are the reference's.

Host chrome under the render policy, never wire: a document says nothing about
it. ``"Offer"`` (the default) draws it wherever the lowering has values to give;
``"Off"`` never computes it, and the chart renders as it did before.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..charts import ChartTable
    from ..model import Value

#: Whether a lowered chart offers its data table.
ChartTablePolicy = Literal["Offer", "Off"]

#: The disclosure's label.
TOGGLE_TEXT = "View as table"

#: The opening of every drawing's root element, up to its first varying attribute.
_DRAWING_ROOT_OPEN = '<svg class="fuaran-drawing" role="img"'


def chart_table_id(node_id: str) -> str:
    """The DOM id of the disclosure, which the drawing names in ``aria-details``:
    the disclosure rather than the table inside it, because a closed ``<details>``
    keeps its content out of the accessibility tree."""
    return f"{node_id}-data-table"


def _text(s: str) -> str:
    """Text as the reference server renderer escapes it, quotes included."""
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
    )


def with_details(details_id: str, svg: str) -> str:
    """The drawing's root naming ``details_id`` in ``aria-details``; any other string unchanged."""
    if not svg.startswith(_DRAWING_ROOT_OPEN):
        return svg
    return f'{_DRAWING_ROOT_OPEN} aria-details="{_text(details_id)}"{svg[len(_DRAWING_ROOT_OPEN) :]}'


def chart_table_host(text_of: Callable[[Value], str], node_id: str, svg: str, table: ChartTable) -> str:
    """A lowered chart with its table: the drawing in its own element, naming the
    disclosure, then the disclosure, the two in one ``fuaran-chart-table-host``
    element. ``text_of`` resolves the caption's ``TextSource``."""
    disclosure_id = chart_table_id(node_id)
    header = "".join(f'<th scope="col">{_text(c)}</th>' for c in table.columns)

    def row(cells: tuple[str, ...]) -> str:
        return (
            "<tr>"
            + "".join(
                f'<th scope="row">{_text(c)}</th>' if i == 0 else f"<td>{_text(c)}</td>" for i, c in enumerate(cells)
            )
            + "</tr>"
        )

    shown = len(table.rows)
    limit = (
        f'<p class="fuaran-chart-table-limit">Rows 1–{shown} of {table.row_count}</p>'
        if shown < table.row_count
        else ""
    )
    return (
        f'<div class="fuaran-chart-table-host"><div>{with_details(disclosure_id, svg)}</div>'
        f'<details class="fuaran-chart-table" id="{_text(disclosure_id)}">'
        f'<summary class="fuaran-chart-table-toggle">{TOGGLE_TEXT}</summary>'
        f'<table class="fuaran-chart-table-grid"><caption>{_text(text_of(table.caption))}</caption>'
        f"<thead><tr>{header}</tr></thead><tbody>{''.join(row(r) for r in table.rows)}</tbody></table>"
        f"{limit}</details></div>"
    )
