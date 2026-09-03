"""fuaran#1160 — the terse authoring layer and its derived-id discipline.

Three things are pinned here, and they are separable claims:

1. **Every emission is legal.** Each constructor's output passes the pre-emit
   validator and is *already canonical* — the authored bytes survive a strict
   decode → re-encode round trip unchanged. That second half is the standing lock for
   this surface: it is what catches a shape that decodes but is not canonical (a
   surviving text envelope, an explicit default, a retired field spelling), which is
   exactly the drift class a hand-rolled emitter kept re-introducing before this layer
   existed. There is no second encoder here, so the lock lives where the emission does.

2. **The ids are derived, and derivation is a pure function of the arguments.** Same
   input → same ids; a changed label moves only the nodes it names; changed *data*
   moves nothing.

3. **The consequence that makes (2) worth having.** A re-run of the gallery cell over
   fresh numbers diffs to a short, typed op script rather than a rebuild.

The op-count bound is asserted against the gallery cell specifically, because that is
what the bound is *about*: the script is proportional to the number of nodes whose
contents changed, so a bigger dashboard legitimately yields more ops. What is
invariant, and asserted separately, is that no op is a structural one — no node is
removed, inserted or reordered, because no id moved.
"""

from __future__ import annotations

import pytest

from fuaran_py import decode_node, encode_node
from fuaran_py.ops import diff
from fuaran_py.schema import types as t
from fuaran_py.schema.types import UiNode
from fuaran_py.ui import encode, quick
from fuaran_py.validator import validate_node

# The gallery's dataset, as a dataframe hands it over.
ROWS: list[dict[str, object]] = [
    {"region": "EMEA", "product": "Widget", "revenue": 5200, "units": 410},
    {"region": "EMEA", "product": "Gadget", "revenue": 3100, "units": 180},
    {"region": "APAC", "product": "Widget", "revenue": 4200, "units": 330},
    {"region": "APAC", "product": "Gadget", "revenue": 2600, "units": 150},
    {"region": "Americas", "product": "Widget", "revenue": 6750, "units": 520},
    {"region": "Americas", "product": "Gadget", "revenue": 3300, "units": 240},
    {"region": "Africa", "product": "Widget", "revenue": 1480, "units": 110},
    {"region": "Africa", "product": "Gadget", "revenue": 900, "units": 70},
]

MOVED: list[dict[str, object]] = [
    {**row, "revenue": int(row["revenue"]) + delta}  # type: ignore[arg-type]
    for row, delta in zip(ROWS, (310, -140, 260, 90, -420, 175, 55, 20), strict=True)
]

# The structural op tags — the ones that mean "the tree changed shape", which is what
# a stable id set exists to prevent.
STRUCTURAL_OPS = {"RemoveNode", "InsertChild", "ReorderChildren", "MoveNode"}


def totals(rows: list[dict[str, object]]) -> dict[str, object]:
    acc: dict[str, float] = {}
    for row in rows:
        acc[str(row["region"])] = acc.get(str(row["region"]), 0.0) + float(row["revenue"])  # type: ignore[arg-type]
    return dict(sorted(acc.items(), key=lambda kv: kv[1], reverse=True))


def gallery_cell(rows: list[dict[str, object]]) -> UiNode:
    """The gallery's regional-revenue dashboard — six authored lines, records in."""
    by_region = totals(rows)
    return quick.dashboard(
        "Regional revenue",
        quick.metric_strip(by_region),
        quick.markdown(f"**{next(iter(by_region))}** leads on revenue.", name="insight"),
        quick.grid(rows),
    )


def ids_of(node: UiNode) -> list[str]:
    """Every id in the tree, document order."""
    found = [node.id]
    children = node.kind.children if isinstance(node.kind, t.Box) else ()
    for child in children:
        found.extend(ids_of(child))
    return found


def assert_canonical(node: UiNode) -> None:
    """The standing emitter lock: legal, decodable, and already canonical."""
    assert validate_node(node.to_wire()) == []
    wire = encode(node)
    decoded = decode_node(wire)
    assert decoded.__class__.__name__ == "Ok", decoded
    assert encode_node(decoded.value) == wire


# ── 1. Every emission is legal, decodable and already canonical ───────────────


@pytest.mark.parametrize(
    "node",
    [
        pytest.param(quick.heading("Channel performance", level=1), id="heading"),
        pytest.param(quick.markdown("Updated hourly."), id="markdown"),
        pytest.param(quick.metric("Revenue", 1284.5), id="metric"),
        pytest.param(quick.metric_strip({"EMEA": 8300, "APAC": 6800}), id="metric_strip-mapping"),
        pytest.param(quick.metric_strip([("EMEA", 8300), ("APAC", 6800)]), id="metric_strip-pairs"),
        pytest.param(quick.metric_strip(ROWS, label="product", value="revenue"), id="metric_strip-records"),
        pytest.param(quick.grid(ROWS), id="grid"),
        pytest.param(quick.grid(ROWS, columns=["region", "revenue"], labels={"revenue": "£"}), id="grid-narrowed"),
        pytest.param(quick.chart(ROWS, x="product", y="revenue", kind="Bar"), id="chart"),
        pytest.param(quick.chart(ROWS, x="product", y=["revenue", "units"], title="Both"), id="chart-multi"),
        pytest.param(gallery_cell(ROWS), id="gallery-cell"),
    ],
)
def test_every_emission_is_canonical(node: UiNode) -> None:
    assert_canonical(node)


def test_the_grid_projects_its_columns_declaratively() -> None:
    """A decoded grid can only project a column that names a ``field``; the closure
    spelling of the same slot erases on the wire. The row key follows the same rule."""
    grid = quick.grid(ROWS)
    kind = grid.to_wire().kind
    assert kind.fields["rowKeyField"] == "_row"
    assert "rowKey" not in kind.fields
    for column in kind.fields["columns"].items:  # type: ignore[union-attr]
        assert "field" in column.fields  # type: ignore[union-attr]
        assert "value" not in column.fields  # type: ignore[union-attr]


def test_a_records_grid_carries_no_display_column_for_its_row_key() -> None:
    kind = quick.grid(ROWS).to_wire().kind
    labels = [c.fields["label"] for c in kind.fields["columns"].items]  # type: ignore[union-attr]
    assert labels == ["Region", "Product", "Revenue", "Units"]


def test_records_with_ragged_keys_take_the_union_in_first_seen_order() -> None:
    node = quick.grid([{"a": 1}, {"b": 2, "a": 3}])
    labels = [c.fields["label"] for c in node.to_wire().kind.fields["columns"].items]  # type: ignore[union-attr]
    assert labels == ["A", "B"]


def test_an_empty_record_list_is_a_note_rather_than_an_illegal_grid() -> None:
    assert_canonical(quick.grid([]))


def test_metric_strip_refuses_a_half_named_record_projection() -> None:
    with pytest.raises(ValueError, match="both"):
        quick.metric_strip(ROWS, label="region")


# ── 2. The derived-id discipline ──────────────────────────────────────────────


def test_derive_id_is_a_pure_function_of_its_arguments() -> None:
    assert quick.derive_id("metric", "Revenue") == quick.derive_id("metric", "Revenue")
    assert quick.derive_id("metric", "Revenue") != quick.derive_id("metric", "Orders")
    assert quick.derive_id("metric", "Revenue") != quick.derive_id("heading", "Revenue")
    assert quick.derive_id("metric", "Revenue", 0) != quick.derive_id("metric", "Revenue", 1)


def test_a_derived_id_reads_as_its_kind_and_label() -> None:
    """The hash is a disambiguator on a readable slug, not an opaque address — an op
    ticker showing `metric-revenue-…` is the demo this layer exists to make possible."""
    assert quick.metric("Revenue", 1).id == "metric-revenue-893567"
    assert quick.derive_id("metric", "→ ←") == "metric-0f639b"  # no slug survives; the hash still does


def test_the_same_cell_run_twice_produces_the_same_bytes() -> None:
    assert encode(gallery_cell(ROWS)) == encode(gallery_cell(ROWS))


def test_changed_data_moves_no_id() -> None:
    assert ids_of(gallery_cell(ROWS)) == ids_of(gallery_cell(MOVED))


def test_a_changed_label_moves_only_the_node_it_names() -> None:
    before = quick.metric_strip({"EMEA": 8300, "APAC": 6800, "Africa": 2380})
    after = quick.metric_strip({"EMEA": 8300, "Asia-Pacific": 6800, "Africa": 2380})
    gone = set(ids_of(before)) - set(ids_of(after))
    arrived = set(ids_of(after)) - set(ids_of(before))
    assert len(gone) == 1 and len(arrived) == 1
    assert len(set(ids_of(before)) & set(ids_of(after))) == 3  # the strip and two untouched tiles


def test_two_nodes_sharing_a_kind_and_label_get_distinct_ids() -> None:
    """Within one strip the occurrence index separates them; across two calls neither
    can see the other, so the root constructor resolves the clash instead."""
    within = quick.metric_strip([("Revenue", 1), ("Revenue", 2)])
    assert len(set(ids_of(within))) == len(ids_of(within))

    across = quick.dashboard("D", quick.metric_strip([("Revenue", 1)]), quick.metric_strip([("Revenue", 2)]))
    assert len(set(ids_of(across))) == len(ids_of(across))
    assert validate_node(across.to_wire()) == []  # a duplicate id is a validator finding


def test_duplicate_resolution_is_itself_deterministic() -> None:
    def build() -> UiNode:
        return quick.dashboard("D", quick.metric_strip([("Revenue", 1)]), quick.metric_strip([("Revenue", 2)]))

    assert ids_of(build()) == ids_of(build())


def test_recomputed_prose_keeps_its_id_when_it_is_named() -> None:
    """A narrative line rewritten every run is the one node whose *text* is not its
    identity — `name=` is how an author says so, and without it the id follows the body."""
    assert quick.markdown("EMEA leads.", name="insight").id == quick.markdown("APAC leads.", name="insight").id
    assert quick.markdown("EMEA leads.").id != quick.markdown("APAC leads.").id


# ── 3. What the stable ids buy: a short, typed re-run script ──────────────────


def test_an_unchanged_re_run_diffs_to_nothing() -> None:
    assert diff(gallery_cell(ROWS).to_wire(), gallery_cell(ROWS).to_wire()) == []


def test_a_re_run_of_the_gallery_cell_over_fresh_data_is_a_short_op_script() -> None:
    ops = diff(gallery_cell(ROWS).to_wire(), gallery_cell(MOVED).to_wire())
    assert 0 < len(ops) <= 6, [op.tag for op in ops]
    assert not (STRUCTURAL_OPS & {op.tag for op in ops}), [op.tag for op in ops]


def test_the_re_run_script_actually_reproduces_the_new_tree() -> None:
    """The bound above is worthless if the script is short because it is wrong."""
    from fuaran_py.canonical import encode_value
    from fuaran_py.ops import apply

    before, after = gallery_cell(ROWS).to_wire(), gallery_cell(MOVED).to_wire()
    current = before
    for op in diff(before, after):
        result = apply(op, current)
        assert result.__class__.__name__ == "Ok", result
        current = result.value
    assert encode_value(current) == encode_value(after)
