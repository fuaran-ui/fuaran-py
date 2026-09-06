"""Parameter-bound controls (fuaran#1170).

The claim is end-to-end and so is most of what is asserted here: a control declares a
slot, a pipeline reads it, and the SAME evaluator every conformant host runs derives
different rows as the slot changes. What a browser adds to that is a mouse.

Three properties get their own attention because each was a live defect before this
phase, and each is invisible when it is wrong:

* **No `onChange` on the wire.** A renderer arms its write-back default only for a
  control declaring no handler, so a control that emitted the closure sentinel could not
  write to any slot — and looked entirely correct doing it.
* **The declared default seeds the parameter.** The first render of an untouched
  document is the default's view, not an empty one.
* **An unbound parameter is refused where the author is**, with the declared names in
  the message.
"""

from __future__ import annotations

import json

import pytest

from fuaran_py import decode_node
from fuaran_py.compute import evaluate_tree
from fuaran_py.model import Node
from fuaran_py.ui import (
    UnboundParamError,
    col,
    control,
    encode,
    frame,
    fuaran,
    node,
    param,
    quick,
)

ROWS: dict[str, list[object]] = {
    "region": ["EMEA", "APAC", "EMEA", "Americas"],
    "revenue": [8300.0, 6800.0, 10050.0, 2380.0],
    "day": ["2026-01-08", "2026-02-14", "2026-03-02", "2026-01-30"],
}


def _grid(fr: object, node_id: str = "grid") -> object:
    return node.bare(
        fuaran.grid(node_id, source=fr.to_transform_binding(), columns=[], row_key_field="region")  # type: ignore[attr-defined]
    )


def _decoded(*children: object) -> Node:
    decoded = decode_node(encode(quick.dashboard("Revenue", *children)))  # type: ignore[arg-type]
    assert decoded.ok, decoded
    return decoded.value


def _rows(tree: Node, state: dict[str, object], node_id: str = "grid") -> list[dict[str, object]]:
    result = evaluate_tree(tree, state)[node_id]
    assert result.ok, result
    return result.rows


# ── The wire shape the write-back depends on ─────────────────────────────────


def test_a_control_emits_no_change_handler() -> None:
    """The one fact the whole feature rests on.

    A present `onChange` — even as the unobservable-closure sentinel, which is all the
    wire can carry — makes a renderer dispatch an inert placeholder instead of writing
    the value back. The key must be ABSENT, so its absence is asserted rather than
    assumed.
    """
    for control_node in (
        control.select("region", options=["EMEA"]),
        control.multi_select("regions", options=["EMEA"]),
        control.range("revenue", low=0, high=1),
        control.date_range("window", start="2026-01-01", end="2026-12-31"),
    ):
        assert "onChange" not in encode(control_node), control_node.id


def test_the_id_first_surface_still_emits_the_handler_by_default() -> None:
    """The flag is additive: a `Select` authored the way it always was is unchanged.

    Flipping the default would silently rewrite every tree already authored against this
    surface, which is a different change from adding a way to opt out of it.
    """
    from fuaran_py.ui import binding

    unchanged = fuaran.select("s", label="Region", source=binding.static([]), value=binding.state("region", "EMEA"))
    assert '"onChange":"<closure>"' in encode(unchanged)


def test_the_control_slot_and_the_parameter_source_are_the_same_binding() -> None:
    """§24.4 makes a declared default seed the slot for every reader, and the parameter
    is one of them. Declaring the same value twice is the case the rule calls
    unremarkable; declaring two different ones is the defect it names — so the two are
    built from one binding rather than kept in step by hand."""
    region = control.select("region", options=["EMEA"], default="EMEA")
    (declaration,) = region.params
    wire = json.loads(encode(region))
    assert wire["kind"]["value"] == json.loads(json.dumps(_as_json(declaration.source)))


def _as_json(binding: object) -> object:
    from fuaran_py.canonical import encode_value

    return json.loads(encode_value(binding.to_wire()))  # type: ignore[attr-defined]


# ── Recompute: the same evaluator, three states ──────────────────────────────


def test_a_select_scopes_the_derivation_and_the_seed_is_the_first_answer() -> None:
    region = control.select("region", options=["EMEA", "APAC"], default="EMEA")
    fr = frame(ROWS).filter(col("region").eq(param("region"))).bind(region)
    tree = _decoded(region, _grid(fr))

    # Untouched document: the declared default IS the slot's value, so the first render
    # already answers rather than showing everything or nothing.
    assert [r["region"] for r in _rows(tree, {})] == ["EMEA", "EMEA"]
    # …and moving the control re-derives, with no Python in the picture but this one.
    assert [r["region"] for r in _rows(tree, {"region": "APAC"})] == ["APAC"]


def test_an_unseeded_select_is_an_absent_constraint() -> None:
    """No default means the parameter is unbound, so the filter reading it is pruned —
    every row, not no rows. "Nothing chosen" is the absence of a constraint."""
    region = control.select("region", options=["EMEA", "APAC"])
    fr = frame(ROWS).filter(col("region").eq(param("region"))).bind(region)
    assert len(_rows(_decoded(region, _grid(fr)), {})) == len(ROWS["region"])


def test_a_multi_select_reads_as_a_list_parameter_and_empty_means_unfiltered() -> None:
    regions = control.multi_select("regions", options=["EMEA", "APAC", "Americas"])
    fr = frame(ROWS).filter(col("region").is_in(param("regions"))).bind(regions)
    tree = _decoded(regions, _grid(fr))

    assert len(_rows(tree, {})) == len(ROWS["region"])
    assert {r["region"] for r in _rows(tree, {"regions": ["APAC", "Americas"]})} == {"APAC", "Americas"}
    # Deselecting everything shows the unfiltered table, not an empty one.
    assert len(_rows(tree, {"regions": []})) == len(ROWS["region"])


def test_a_range_declares_two_parameters_and_each_end_is_independent() -> None:
    revenue = control.range("revenue", low=0, high=20000)
    assert [d.name for d in revenue.params] == ["revenue_min", "revenue_max"]

    fr = (
        frame(ROWS)
        .filter(col("revenue") >= param("revenue_min"))
        .filter(col("revenue") <= param("revenue_max"))
        .bind(revenue)
    )
    tree = _decoded(revenue, _grid(fr))

    assert len(_rows(tree, {})) == 4
    assert {r["region"] for r in _rows(tree, {"revenue_min": 7000})} == {"EMEA"}
    assert {r["region"] for r in _rows(tree, {"revenue_max": 7000})} == {"APAC", "Americas"}


def test_a_date_range_compares_iso_strings_with_nothing_to_parse() -> None:
    window = control.date_range("window", start="2026-01-01", end="2026-12-31")
    assert [d.name for d in window.params] == ["window_from", "window_to"]

    fr = frame(ROWS).filter(col("day") >= param("window_from")).filter(col("day") <= param("window_to")).bind(window)
    tree = _decoded(window, _grid(fr))

    assert len(_rows(tree, {})) == 4
    assert {r["region"] for r in _rows(tree, {"window_from": "2026-02-01"})} == {"APAC", "EMEA"}


# ── Options derived from the data ────────────────────────────────────────────


def test_options_derived_from_a_column_lower_to_a_transform_the_host_evaluates() -> None:
    """The option list is data, and is derived by the evaluator that derives the rows —
    so a value present in the data is present in the control with no list to maintain."""
    region = control.select("region", options=col("region").unique(), source=frame(ROWS))
    tree = _decoded(region)

    options = evaluate_tree(tree, {})[region.id]
    assert options.ok, options
    assert options.rows == [
        {"value": "APAC", "label": "APAC"},
        {"value": "Americas", "label": "Americas"},
        {"value": "EMEA", "label": "EMEA"},
    ]


def test_a_derived_option_list_needs_the_frame_it_derives_from() -> None:
    with pytest.raises(TypeError) as raised:
        control.select("region", options=col("region").unique())
    assert "source=frame(" in str(raised.value)


def test_unique_is_a_declaration_rather_than_an_expression() -> None:
    """It says where a control's options come from; it is not a predicate, and the type
    is what says so — a checker refuses it in a `filter` before anything runs."""
    from fuaran_py.ui import Expr, OptionSource

    derived = col("region").unique()
    assert isinstance(derived, OptionSource)
    assert not isinstance(derived, Expr)
    with pytest.raises(TypeError):
        col("revenue").abs().unique()  # aggregation-shaped: unique() is a COLUMN's


# ── The author-time refusal ──────────────────────────────────────────────────


def test_an_unbound_parameter_is_refused_by_name_when_the_binding_is_lowered() -> None:
    revenue = control.range("revenue", low=0)
    with pytest.raises(UnboundParamError) as raised:
        frame(ROWS).filter(col("region").eq(param("region"))).bind(revenue).to_transform_binding()
    message = str(raised.value)
    assert "'region'" in message, "the refusal names the parameter the author wrote"
    assert "revenue_min" in message, "…and what IS declared, which is how a typo is corrected"


def test_a_pipeline_with_no_parameters_is_unaffected() -> None:
    """The check quantifies over what the pipeline READS, so every frame authored before
    controls existed lowers exactly as it did — and emits no `params` key."""
    fr = frame(ROWS).sort("revenue", descending=True)
    assert "params" not in json.loads(fr.to_transform_json())


def test_two_controls_may_not_claim_one_parameter_name() -> None:
    with pytest.raises(UnboundParamError) as raised:
        frame(ROWS).bind(
            control.select("region", options=["EMEA"], default="EMEA"),
            control.select("region", options=["APAC"], default="APAC"),
        )
    assert "one parameter names one slot" in str(raised.value)


def test_binding_the_same_control_twice_is_a_no_op() -> None:
    region = control.select("region", options=["EMEA"], default="EMEA")
    once = frame(ROWS).bind(region)
    assert once.bind(region).params == once.params


def test_bind_refuses_something_that_declares_no_parameters() -> None:
    with pytest.raises(TypeError) as raised:
        frame(ROWS).bind(quick.markdown("not a control"))  # type: ignore[arg-type]
    assert "declares no parameters" in str(raised.value)


# ── Identity ─────────────────────────────────────────────────────────────────


def test_a_control_is_a_node_and_its_id_is_derived() -> None:
    """It goes straight into a dashboard, and re-running an unchanged cell yields the
    same id — the same discipline every other terse constructor keeps."""
    from fuaran_py.schema.types import UiNode

    region = control.select("region", options=["EMEA"])
    assert isinstance(region, UiNode)
    assert region.id == control.select("region", options=["EMEA"]).id
    assert region.id.startswith("select-region-")
    assert control.select("region", options=["EMEA"], occurrence=1).id != region.id
