"""Pre-emit validator surface."""

from __future__ import annotations

from fuaran_py import decode_node, validate_node
from fuaran_py.model import Arr, Node, Obj


def test_clean_tree_has_no_findings() -> None:
    result = decode_node('{"id":"a","kind":{"$type":"Markdown","text":{"$type":"Literal","text":"hi"}}}')
    assert result.ok
    assert validate_node(result.value) == []


def test_empty_id_is_flagged() -> None:
    node = Node("", Obj("Markdown", {"text": Obj("Literal", {"text": "x"})}))
    findings = validate_node(node)
    assert [f.code for f in findings] == ["EMPTY_NODE_ID"]
    assert findings[0].path == "$.id"


def test_duplicate_child_id_is_flagged() -> None:
    child_a = Node("dup", Obj("Markdown", {"text": Obj("Literal", {"text": "x"})}))
    child_b = Node("dup", Obj("Markdown", {"text": Obj("Literal", {"text": "y"})}))
    root = Node(
        "root",
        Obj(
            "Box",
            {
                "children": Arr([child_a, child_b]),
                "layout": Obj("Flex", {"direction": "Vertical", "wrap": False}),
                "role": "Group",
            },
        ),
    )
    findings = validate_node(root)
    assert any(f.code == "DUPLICATE_NODE_ID" for f in findings)


def test_unknown_kind_is_flagged() -> None:
    node = Node("a", Obj("Sparkler", {}))
    findings = validate_node(node)
    assert [f.code for f in findings] == ["UNKNOWN_NODE_KIND"]
    assert findings[0].path == "$.kind.$type"


# ── Phase 640 — schema-grounded chart validation (FUARAN086–089) ─────────────


def _chart_node(
    *,
    kind: str = "Bar",
    x_field: str = "quarter",
    y_fields: list[str] | None = None,
    stacked: bool = False,
    schema: list[tuple[str, str]] | None = None,
    pipeline_items: list[Obj] | None = None,
    x_scale: str | None = None,
) -> Node:
    entries = schema or [("quarter", "string"), ("revenue", "float")]
    schema_arr = Arr([Obj(None, {"name": n, "type": t}) for n, t in entries])
    source = Obj(
        "Transform",
        {
            "pipeline": Arr(list(pipeline_items or [])),
            "source": Obj(None, {"schema": schema_arr, "columns": Obj(None, {})}),
        },
    )
    fields: dict[str, object] = {
        "kind": kind,
        "source": source,
        "xField": x_field,
        "yFields": Arr(list(y_fields if y_fields is not None else ["revenue"])),
    }
    if stacked:
        fields["stacked"] = True
    if x_scale is not None:
        fields["xScale"] = x_scale
    return Node("cht", Obj("Chart", fields))  # type: ignore[arg-type]


def test_chart_grounded_fields_are_clean() -> None:
    assert validate_node(_chart_node()) == []


def test_chart_ungrounded_y_field_is_flagged() -> None:
    findings = validate_node(_chart_node(y_fields=["revenu"]))  # typo — absent from the schema
    assert [f.code for f in findings] == ["CHART_FIELD_UNGROUNDED"]


def test_chart_non_numeric_y_field_is_flagged() -> None:
    findings = validate_node(_chart_node(y_fields=["quarter"]))  # a string column
    assert [f.code for f in findings] == ["CHART_FIELD_TYPE_MISMATCH"]


def test_scatter_x_field_must_be_numeric() -> None:
    findings = validate_node(_chart_node(kind="Scatter", x_field="quarter"))
    assert "CHART_FIELD_TYPE_MISMATCH" in [f.code for f in findings]


def test_temporal_x_over_a_non_date_column_is_refused() -> None:
    # FUARAN097 (Phase 882). The declaration is grounded against the column type:
    # a string column cannot parse as a date, so every row's x would read as the
    # epoch and the chart would draw every point stacked on one date.
    findings = validate_node(_chart_node(x_scale="Temporal"))  # `quarter` is a string column
    assert [f.code for f in findings] == ["CHART_TEMPORAL_X_NOT_DATE"]

    # A date column is what the declaration claims, so it passes; and so does a
    # timestamp, whose time-of-day the lowering discards (a documented narrowing,
    # not a mismatch).
    for ty in ("date", "timestamp"):
        assert (
            validate_node(_chart_node(x_field="day", x_scale="Temporal", schema=[("day", ty), ("revenue", "float")]))
            == []
        )

    # Without the declaration the same string column is an ordinary category axis
    # — the rule fires on the DECLARATION, not on the column.
    assert validate_node(_chart_node()) == []

    # An unknowable source passes ungrounded (refuse only what is PROVABLY wrong).
    static_source = Node(
        "cht",
        Obj(
            "Chart",
            {
                "kind": "Line",
                "source": Obj("Static", {"value": Arr([])}),
                "xField": "quarter",
                "yFields": Arr(["revenue"]),
                "xScale": "Temporal",
            },
        ),  # type: ignore[arg-type]
    )
    assert validate_node(static_source) == []


def test_temporal_narrows_the_scatter_x_numeric_arm() -> None:
    # FUARAN087's x arm is NARROWED by a temporal declaration: a temporal scatter
    # reads its x as dates, so a date column there is correct rather than "not
    # numeric". Without the narrowing a correctly-authored time-series scatter
    # would be refused for the very column it declared.
    dated = [("day", "date"), ("revenue", "float")]
    assert validate_node(_chart_node(kind="Scatter", x_field="day", schema=dated, x_scale="Temporal")) == []
    # The negative control: the same scatter WITHOUT the declaration still wants a
    # numeric x, and a date column is not one.
    assert [f.code for f in validate_node(_chart_node(kind="Scatter", x_field="day", schema=dated))] == [
        "CHART_FIELD_TYPE_MISMATCH"
    ]


def test_pie_needs_exactly_one_series() -> None:
    findings = validate_node(_chart_node(kind="Pie", y_fields=["revenue", "cost"]))
    assert "CHART_PIE_SERIES_SHAPE" in [f.code for f in findings]


def test_stacked_is_dead_intent_outside_bar_area() -> None:
    findings = validate_node(_chart_node(kind="Line", stacked=True))
    assert [f.code for f in findings] == ["CHART_STACKED_MEANINGLESS"]


def test_non_empty_pipeline_passes_ungrounded() -> None:
    # A non-empty pipeline changes the column set (Derive adds, Project/GroupBy
    # remove) — no static output-schema derivation exists, so grounding
    # deliberately passes rather than false-positive.
    step = Obj("limit", {"n": 1, "offset": 0})
    findings = validate_node(_chart_node(y_fields=["revenu"], pipeline_items=[step]))
    assert findings == []
