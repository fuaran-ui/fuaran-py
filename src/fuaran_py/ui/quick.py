"""``fuaran_py.ui.quick`` — the terse, notebook-grade authoring layer.

:mod:`fuaran_py.ui` is **id-first**: every constructor's first positional argument is
the node id, because an app shell, a fragment library or a golden fixture wants ids
it chose and can address later. A notebook cell wants the opposite. A data scientist
holding a list of records wants to name the dashboard, hand over the rows, and get a
tree — and then to re-run the cell and have the host *patch* the rendered page rather
than rebuild it, which requires the ids to come out the same the second time::

    from fuaran_py.ui import quick

    app = quick.dashboard(
        "Regional revenue",
        quick.metric_strip(rows, label="region", value="revenue"),
        quick.chart(rows, x="region", y="revenue", kind="Bar", title="Revenue by region"),
        quick.grid(rows),
    )

This layer is **title-first, records-in, ids derived**. It composes the typed
per-kind constructors in :mod:`fuaran_py.ui` — it is a layer *over* that surface,
never a second one beside it, so there is no second encoder, no second set of
per-kind defaults, and no second ARIA table to drift from the corpus. Everything it
returns is an ordinary :class:`~fuaran_py.schema.types.UiNode`; mix the two surfaces
freely, and reach for the id-first one the moment you need to address a node.

Derived ids — the discipline
----------------------------

An id is derived from the node's **kind**, its **label** (the human-meaningful text
that names it — a metric's label, a heading's text, a chart's title, a grid's column
signature), and an **occurrence index** that disambiguates two otherwise-identical
siblings. Those three are hashed; the id is the kind, a slug of the label, and the
first :data:`_HASH_LEN` hex digits of that hash::

    quick.metric("Revenue", 1284.5)   # → id "metric-revenue-893567"

Three properties follow, and they are the reason the derivation is shaped this way:

* **Same input → same ids.** The derivation reads nothing but its arguments — no
  counter, no clock, no object identity — so a re-run of an unchanged cell produces
  a byte-identical tree, and :func:`fuaran_py.ops.diff` yields the empty op script.
* **A changed label moves only the nodes it names.** Renaming one metric changes one
  hash; every sibling's id is computed from its own label and is untouched.
* **Changed data moves no id at all.** A value, a trend, a row, a whole re-loaded
  frame — none of them feed the derivation, so a re-run over fresh numbers is a
  short, typed ``UpdateProp`` / ``EditNode`` script against the *same* nodes.

**Absolute position is deliberately NOT part of the derivation**, though it is the
obvious thing to hash. It would make every insertion renumber the ids of everything
after it, so adding one metric to a strip would re-key the rest of the dashboard and
turn a one-op patch into a rebuild — the exact outcome the derivation exists to
avoid. The occurrence index is a *relative* position (the nth node sharing a kind and
a label), which disambiguates without that coupling.

A node whose text changes between runs — a narrative line recomputed from the data —
should carry an explicit ``name=``, which is then what the id derives from::

    quick.markdown(f"Revenue rose {pct:.0%} this quarter.", name="insight")

Without it the id derives from the body, so re-running with new prose removes one
node and inserts another rather than updating one.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace as _replace
from typing import cast

from ..schema import types as t
from ..schema.types import Binding, CellFormat, UiNode
from . import fuaran
from .compute import frame

__all__ = [
    "derive_id",
    "dashboard",
    "heading",
    "markdown",
    "metric",
    "metric_strip",
    "grid",
    "chart",
]

# The hash is a disambiguator on a human-readable slug, not a content address: six
# hex digits (24 bits) keep an id readable in an op ticker while making an accidental
# collision between two differently-labelled nodes in one dashboard implausible. The
# occurrence index below removes the only collision that is *not* accidental.
_HASH_LEN = 6

# The row-identity column `grid` synthesises. Named with a leading underscore because
# it is machinery rather than data: it carries the row key and is never displayed.
_ROW_KEY = "_row"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower()).strip("-")


def derive_id(kind: str, label: str, occurrence: int = 0) -> str:
    """The derived node id for ``kind`` labelled ``label``.

    ``occurrence`` disambiguates two nodes that share both — the second
    ``quick.metric("Revenue", …)`` in one strip is occurrence 1. Pure: the same three
    arguments always yield the same id, on any machine and in any process.
    """
    digest = hashlib.sha256(f"{kind}\x1f{label}\x1f{occurrence}".encode()).hexdigest()[:_HASH_LEN]
    slug = _slug(label)
    return f"{kind}-{slug}-{digest}" if slug else f"{kind}-{digest}"


# ── Duplicate resolution ──────────────────────────────────────────────────────


def _children_of(node: UiNode) -> tuple[UiNode, ...] | None:
    """The structural children of a container this layer builds, or ``None`` for a leaf.

    Only :class:`~fuaran_py.schema.types.Box` is walked, because ``dashboard`` and
    ``metric_strip`` are the only containers ``quick`` constructs and both lower to a
    Box. A node reached from the id-first surface is treated as a leaf: this pass
    resolves collisions *between derived ids*, and an id the author chose is theirs.
    """
    kind = node.kind
    return kind.children if isinstance(kind, t.Box) else None


def _resolve_duplicates(node: UiNode, seen: set[str]) -> UiNode:
    """Rename any node whose id is already taken, in document order.

    A collision means two nodes were built with the same kind *and* the same label in
    separate calls, so neither could see the other's occurrence index. The rename
    appends the lowest free ordinal, which is deterministic for a given tree — and it
    fires only on an actual clash, so the "a changed label moves only the nodes it
    names" property is untouched in every tree that has none.
    """
    node_id = node.id
    if node_id in seen:
        ordinal = 2
        while f"{node_id}-{ordinal}" in seen:
            ordinal += 1
        node_id = f"{node_id}-{ordinal}"
    seen.add(node_id)

    children = _children_of(node)
    if children is None:
        return node if node_id == node.id else _replace(node, id=node_id)
    resolved = tuple(_resolve_duplicates(child, seen) for child in children)
    kind = _replace(node.kind, children=resolved)  # type: ignore[type-var]
    return _replace(node, id=node_id, kind=kind)


def _compose(children: Sequence[UiNode]) -> tuple[UiNode, ...]:
    seen: set[str] = set()
    return tuple(_resolve_duplicates(child, seen) for child in children)


# ── Records → an embedded frame ───────────────────────────────────────────────


def _scalar(value: object) -> object:
    """Normalise one record cell to a plain Python scalar.

    A dataframe library's records carry its own scalar wrappers (a numpy ``int64``, a
    ``Timestamp``); ``.item()`` is the interchange spelling every one of them honours,
    and unwrapping through it keeps this module dependency-free while still accepting
    ``df.to_dict("records")`` unchanged. Anything left that is not a JSON scalar is
    rendered as its string form rather than refused — a notebook cell should not fail
    on a column it was not asked about.
    """
    if hasattr(value, "item"):
        try:
            value = value.item()  # type: ignore[union-attr]
        except (AttributeError, ValueError, TypeError):
            pass
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _columns_of(records: Sequence[Mapping[str, object]]) -> list[str]:
    """The union of the records' keys, first-seen order — the column order a reader
    expects, and stable under a record that happens to omit an optional key."""
    ordered: list[str] = []
    for record in records:
        for key in record:
            name = str(key)
            if name not in ordered:
                ordered.append(name)
    return ordered


def _as_records(records: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    return list(records)


def _frame_binding(records: Sequence[Mapping[str, object]], keys: Sequence[str], *, with_row_key: bool) -> Binding:
    """The ``Binding.Transform`` carrying these records as an embedded table.

    Built through :func:`fuaran_py.ui.frame`, so the columnar encoding, the type
    inference and the null handling are the shipped Compute-layer ones rather than a
    second implementation. The pipeline is empty: the rows travel as data, and an
    empty pipeline over an embedded table is also the one shape the pre-emit
    validator can ground a chart's field references against.

    The cast is a layering fact, not a fudge: ``Binding`` is a closed union in
    :mod:`fuaran_py.schema.types`, and ``TransformBinding`` — the seventh wire case —
    lives a layer above it in :mod:`fuaran_py.ui.compute`, because it needs the
    dataframe codec that types.py must not depend on. It lowers through the same
    ``to_wire`` protocol as every other binding, which is what the node constructors
    actually consume; widening the union would invert the dependency.
    """
    data: dict[str, list[object]] = {}
    if with_row_key:
        data[_ROW_KEY] = list(range(len(records)))
    for key in keys:
        data[key] = [_scalar(record.get(key)) for record in records]
    return cast("Binding", frame(data).to_transform_binding())


def _title_case(field_name: str) -> str:
    return field_name.replace("_", " ").title()


# ── Constructors ──────────────────────────────────────────────────────────────


def dashboard(title: str, *children: UiNode) -> UiNode:
    """A dashboard headed by ``title``, holding ``children`` in order.

    This is where cross-call id collisions are resolved (see
    :func:`_resolve_duplicates`), so it is the constructor to make the root of a cell.
    """
    return fuaran.dashboard(
        derive_id("dashboard", title),
        children=list(_compose(children)),
        heading=title,
    )


def heading(text: str, *, level: int = 2, name: str | None = None) -> UiNode:
    """A heading. The id derives from ``name`` when given, else from ``text``."""
    return fuaran.heading(derive_id("heading", name if name is not None else text), text, level=level)


def markdown(body: str, *, name: str | None = None) -> UiNode:
    """A markdown block. Pass ``name`` whenever the prose is recomputed per run, so
    the id stays put and a re-run updates the node instead of replacing it."""
    return fuaran.markdown(derive_id("markdown", name if name is not None else body), body)


def metric(
    label: str,
    value: str | float | Binding,
    *,
    format: CellFormat | None = None,  # noqa: A002 — mirrors the id-first surface's slot name
    tone: t.Tone = "Default",
    subtext: str | None = None,
    trend: float | Binding | None = None,
    trend_format: CellFormat | None = None,
    trend_polarity: t.TrendPolarity = "HigherIsBetter",
    occurrence: int = 0,
) -> UiNode:
    """A KPI tile. ``occurrence`` disambiguates a second tile with the same label."""
    return fuaran.metric(
        derive_id("metric", label, occurrence),
        label=label,
        value=value,
        format=format,
        tone=tone,
        subtext=subtext,
        trend=trend,
        trend_format=trend_format,
        trend_polarity=trend_polarity,
    )


def metric_strip(
    data: Mapping[str, object] | Iterable[Mapping[str, object]] | Iterable[tuple[str, object]],
    *,
    label: str | None = None,
    value: str | None = None,
    format: CellFormat | None = None,  # noqa: A002 — mirrors the id-first surface's slot name
    name: str = "metrics",
) -> UiNode:
    """A horizontal strip of KPI tiles, from records, pairs, or a mapping.

    Three input shapes, one for each way a notebook already holds the numbers:

    * **records** — ``metric_strip(rows, label="region", value="revenue")`` reads the
      two named columns out of a list of records (``df.to_dict("records")``);
    * **a mapping** — ``metric_strip({"Revenue": 1200, "Orders": 48})``;
    * **pairs** — ``metric_strip([("Revenue", 1200), ("Orders", 48)])``.
    """
    if label is not None or value is not None:
        if label is None or value is None:
            raise ValueError("metric_strip: pass both `label` and `value`, or neither")
        pairs = [(str(_scalar(r.get(label))), _scalar(r.get(value))) for r in _as_records(data)]  # type: ignore[arg-type]
    elif isinstance(data, Mapping):
        pairs = [(str(k), _scalar(v)) for k, v in data.items()]
    else:
        pairs = [(str(k), _scalar(v)) for k, v in data]  # type: ignore[misc]

    seen: dict[str, int] = {}
    tiles: list[UiNode] = []
    for tile_label, tile_value in pairs:
        occurrence = seen.get(tile_label, 0)
        seen[tile_label] = occurrence + 1
        if not isinstance(tile_value, (bool, int, float, str)):
            tile_value = str(tile_value)
        tiles.append(metric(tile_label, tile_value, format=format, occurrence=occurrence))
    return fuaran.stack(
        derive_id("strip", name),
        children=list(_compose(tiles)),
        orientation="Horizontal",
        wrap=True,
    )


def grid(
    records: Iterable[Mapping[str, object]],
    *,
    columns: Sequence[str] | None = None,
    labels: Mapping[str, str] | None = None,
    name: str | None = None,
) -> UiNode:
    """A data grid over a list of records.

    Columns default to the records' own keys in first-seen order; ``columns`` narrows
    or reorders them and ``labels`` overrides a header. Each column is *declarative*
    (it names a row field), so the grid a host decodes can actually project it — the
    closure spelling of the same slot cannot survive the wire.

    The id derives from the column signature rather than the rows, so re-running the
    cell over fresh data patches this grid instead of replacing it.
    """
    rows = _as_records(records)
    keys = list(columns) if columns is not None else _columns_of(rows)
    if not keys:
        return markdown("_(no columns)_", name=name if name is not None else "empty-grid")
    header = labels or {}
    return fuaran.grid(
        derive_id("grid", name if name is not None else ",".join(keys)),
        source=_frame_binding(rows, keys, with_row_key=True),
        columns=[t.Column(label=header.get(k, _title_case(k)), field_name=k) for k in keys],
        row_key_field=_ROW_KEY,
    )


def chart(
    records: Iterable[Mapping[str, object]],
    *,
    x: str,
    y: str | Sequence[str],
    kind: t.ChartKind = "Line",
    title: str | None = None,
    stacked: bool = False,
    name: str | None = None,
) -> UiNode:
    """A chart over a list of records: ``x`` names the category column, ``y`` one or
    more series columns.

    The id derives from ``name``, else ``title``, else the field signature — so the
    numbers may change every run without moving the node.
    """
    rows = _as_records(records)
    y_fields = [y] if isinstance(y, str) else list(y)
    label = name if name is not None else title if title is not None else f"{x}~{','.join(y_fields)}"
    return fuaran.chart(
        derive_id("chart", label),
        source=_frame_binding(rows, [x, *y_fields], with_row_key=False),
        x_field=x,
        y_fields=y_fields,
        kind=kind,
        title=title,
        stacked=stacked,
    )
