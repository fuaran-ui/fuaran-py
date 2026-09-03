"""Quickstart — the regional-revenue dashboard, from a list of records, in six lines.

Run it::

    python examples/quickstart_terse_dashboard.py

``fuaran_py.ui.quick`` is the terse, notebook-grade layer over the id-first authoring
surface: **title-first, records-in, ids derived**. A data scientist holding
``df.to_dict("records")`` names the dashboard, hands over the rows, and gets a Fuaran
tree — no ids to invent, no per-kind defaults to remember, no second encoder.

The rows below are a plain list of dicts, which is what every dataframe library's
``to_dict("records")`` returns, and the aggregate is four lines of plain Python; nothing
here imports a third-party dependency. Swap the two for ``pd.read_csv(...)`` and a
``groupby`` and the six authoring lines are unchanged.

The second half of the script is the point that terseness alone would miss. The ids are
**derived** from each node's kind and label, so re-running the cell over fresh numbers
produces the *same* ids — and :func:`fuaran_py.ops.diff` then yields a short, typed op
script against the nodes whose contents moved, rather than a wholesale rebuild. A host
that applies that script patches the rendered page; one that re-decodes a tree with
fresh ids throws the page away and starts again.
"""

from __future__ import annotations

from fuaran_py import decode_node
from fuaran_py.ops import diff
from fuaran_py.schema.types import UiNode
from fuaran_py.ui import encode, quick
from fuaran_py.validator import validate_node

# What a dataframe hands you: `df.to_dict("records")`.
JANUARY: list[dict[str, object]] = [
    {"region": "EMEA", "product": "Widget", "revenue": 5200, "units": 410},
    {"region": "EMEA", "product": "Gadget", "revenue": 3100, "units": 180},
    {"region": "APAC", "product": "Widget", "revenue": 4200, "units": 330},
    {"region": "APAC", "product": "Gadget", "revenue": 2600, "units": 150},
    {"region": "Americas", "product": "Widget", "revenue": 6750, "units": 520},
    {"region": "Americas", "product": "Gadget", "revenue": 3300, "units": 240},
    {"region": "Africa", "product": "Widget", "revenue": 1480, "units": 110},
    {"region": "Africa", "product": "Gadget", "revenue": 900, "units": 70},
]

# The same eight rows, one month on.
FEBRUARY: list[dict[str, object]] = [
    {**row, "revenue": int(row["revenue"]) + delta}  # type: ignore[arg-type]
    for row, delta in zip(JANUARY, (310, -140, 260, 90, -420, 175, 55, 20), strict=True)
]


def revenue_by_region(rows: list[dict[str, object]]) -> dict[str, object]:
    """Total revenue per region, descending — the ``groupby`` a notebook would run."""
    totals: dict[str, float] = {}
    for row in rows:
        totals[str(row["region"])] = totals.get(str(row["region"]), 0.0) + float(row["revenue"])  # type: ignore[arg-type]
    return dict(sorted(totals.items(), key=lambda kv: kv[1], reverse=True))


def dashboard(rows: list[dict[str, object]]) -> UiNode:
    """The whole dashboard: six lines, from a list of records."""
    totals = revenue_by_region(rows)
    return quick.dashboard(
        "Regional revenue",
        quick.metric_strip(totals),
        quick.markdown(f"**{next(iter(totals))}** leads on revenue.", name="insight"),
        quick.grid(rows),
    )


def main() -> None:
    january = dashboard(JANUARY)

    # Every emission passes the pre-emit validator, and is already canonical: the
    # authored bytes survive a decode -> re-encode round trip unchanged.
    findings = validate_node(january.to_wire())
    assert findings == [], findings
    wire = encode(january)
    decoded = decode_node(wire)
    assert decoded.__class__.__name__ == "Ok", decoded
    print(f"authored {len(wire)} bytes of canonical wire; validator clean")

    # Re-running the unchanged cell is the empty op script — the ids derive from the
    # labels, not from a counter, so nothing moved at all.
    assert encode(dashboard(JANUARY)) == wire
    print(f"re-run, unchanged data: {len(diff(january.to_wire(), dashboard(JANUARY).to_wire()))} ops")

    # Re-running over fresh numbers patches the same nodes: four metric tiles and the
    # grid's rows. Data never feeds the id derivation.
    ops = diff(january.to_wire(), dashboard(FEBRUARY).to_wire())
    print(f"re-run, fresh data:     {len(ops)} ops ({', '.join(op.tag or '?' for op in ops)})")

    # Renaming one metric moves exactly one id; its siblings are computed from their
    # own labels and do not notice.
    before = {n.id for n in quick.metric_strip({"EMEA": 8300, "APAC": 6800}).kind.children}  # type: ignore[attr-defined]
    after = {n.id for n in quick.metric_strip({"EMEA": 8300, "Asia-Pacific": 6800}).kind.children}  # type: ignore[attr-defined]
    print(f"renaming one label moved {len(before - after)} of {len(before)} metric ids")

    # A chart is the same shape: records in, fields named, id derived from the title.
    chart = quick.chart(JANUARY, x="product", y="revenue", kind="Bar", title="Revenue by product")
    assert validate_node(chart.to_wire()) == []
    print(f"chart id: {chart.id}")


if __name__ == "__main__":
    main()
