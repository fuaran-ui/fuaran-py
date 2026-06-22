"""Quickstart — author a reactive data app in Python, emit a static artifact.

Run it::

    python examples/quickstart_reactive_data_app.py

A data scientist writes the transform pipeline in idiomatic, polars-shaped Python. It
serialises to the **canonical** ``Transform`` wire form — the same bytes the F# and
TypeScript hosts produce — so the app runs the pipeline client-side at native speed as
*data*, no Python in the browser. The script writes a single static artifact
(``dist/sales-dashboard.json``): a Fuaran node tree whose data-grid + chart are bound to
the authored pipeline. Nothing here imports a third-party dependency.

The genuinely-arbitrary remainder (a model call, custom numpy) is a *capability* — a
scoped, host-registered body the wire references by id. The host-registration seam is
shown at the end; its canonical wire lands with the Capability/Invoke phase.
"""

from __future__ import annotations

import json
from pathlib import Path

from fuaran_py.ui import col, encode, frame, node, transform
from fuaran_py.ui import fuaran as F
from fuaran_py.ui.capability import CapabilityRegistry, capability, float_range


def build_pipeline() -> object:
    """The notebook-style authoring: raw rows → revenue-by-region, sorted."""
    raw = {
        "region": ["EMEA", "EMEA", "AMER", "AMER", "APAC", "APAC"],
        "product": ["A", "B", "A", "B", "A", "B"],
        "revenue": [120.0, 80.0, 200.0, None, 60.0, 90.0],
        "units": [12, 8, 20, 0, 6, 9],
    }
    schema = {"region": "string", "product": "string", "revenue": "float", "units": "int"}

    return (
        frame(raw, schema=schema)
        .filter(col("revenue") > 0)  # drop the null-revenue row
        .with_column("avg_price", col("revenue") / col("units"))
        .group_by("region")
        .agg(
            col("revenue").sum().alias("total_revenue"),
            col("avg_price").mean().alias("mean_price"),
            col("units").sum().alias("total_units"),
        )
        .sort("total_revenue", descending=True)
    )


def build_artifact() -> str:
    """A Fuaran dashboard whose grid + chart bind to the authored pipeline."""
    fr = build_pipeline()
    source = transform(fr)  # the Binding.Transform — a node data source

    # A local preview (the SAME reference evaluator the JS host runs) — purely for the
    # author's confidence; the artifact ships the pipeline, not this table.
    preview = fr.collect()
    print("Local preview (region -> total_revenue):")
    region = next(c for c in preview.columns if c.name == "region")
    total = next(c for c in preview.columns if c.name == "total_revenue")
    for r, t in zip([c.value for c in region.cells], [c.value for c in total.cells], strict=True):
        print(f"  {r:5} {t}")

    dashboard = F.dashboard(
        "sales-dashboard",
        children=[
            F.heading("title", "Revenue by region", level=1),
            F.grid("revenue-grid", source=source),
            node.bare(
                F.chart(
                    "revenue-chart",
                    source=source,
                    x_field="region",
                    y_fields=["total_revenue"],
                    kind="Bar",
                    title="Total revenue",
                )
            ),
        ],
    )
    return encode(dashboard)


def show_capability_seam() -> None:
    """The §3 hard-stuff seam: a Python capability body, registered + invoked host-side.

    (The capability *wire* — declaring it on a node + Binding.Invoke — lands with the
    Capability/Invoke phase; this is the wire-independent host registration.)"""
    reg = CapabilityRegistry()
    reg.register(
        capability(
            "forecast.next_quarter",
            body=lambda a: round(a["last"] * (1.0 + a["growth"]), 2),
            holes=[("last", float_range(0.0, 1e9)), ("growth", float_range(-1.0, 1.0))],
        )
    )
    projected = reg.invoke("forecast.next_quarter", {"last": 200.0, "growth": 0.15})
    print(f"\nCapability forecast.next_quarter(last=200, growth=0.15) = {projected}")


def main() -> None:
    wire = build_artifact()
    out = Path(__file__).parent / "dist" / "sales-dashboard.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(wire, encoding="utf-8")
    print(f"\nWrote static artifact: {out}  ({len(wire)} bytes)")

    # The artifact is canonical JSON — re-parse to prove it is well-formed wire.
    parsed = json.loads(wire)
    assert parsed["id"] == "sales-dashboard"

    show_capability_seam()


if __name__ == "__main__":
    main()
