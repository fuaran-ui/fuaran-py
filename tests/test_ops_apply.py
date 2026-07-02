"""Phase 279 — the tree-op apply engine folds every op with F#/TS-parity semantics.

Base trees are authored with the typed surface (`fuaran_py.ui`), the op is the
canonical corpus fixture (decoded through the real `decode_op`), and the result is
checked by re-encoding the new tree and comparing to a separately-authored expected
tree — so a pass proves the apply fold produces the same canonical tree the other
hosts produce. Error paths assert the typed `ApplyError` codes (parity with the
F#/TS engines); no apply path throws.
"""

from __future__ import annotations

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import decode_node, encode_node
from fuaran_py.model import Arr, Node, Obj
from fuaran_py.ops import apply, decode_op
from fuaran_py.ops.apply import ApplyErr
from fuaran_py.schema import types as t
from fuaran_py.ui import binding, encode, fuaran, node


def _full_metric(node_id: str = "metric-1", *, label: str = "Revenue", source: t.Binding | None = None) -> t.UiNode:
    from fuaran_py.ui import format

    return node.bare(
        fuaran.metric(
            node_id,
            label=label,
            value=source if source is not None else 1234.5,  # type: ignore[arg-type]
            format=format.currency("GBP"),
            tone="Brand",
            icon="trending-up",
            subtext="vs last month",
            trend=0.07,
            trend_format=format.percent(1),
        )
    )


_MARKDOWN_1 = fuaran.markdown("markdown-1", "Updated hourly.")


def _op_text(name: str) -> str:
    return (CORPUS_ROOT / "ops" / f"{name}.json").read_text(encoding="utf-8")


def _decode_tree(tree: t.UiNode) -> Node:
    result = decode_node(encode(tree))
    assert result.ok, result
    return result.value


def _apply_fixture(name: str, base: t.UiNode) -> object:
    op = decode_op(_op_text(name))
    assert op.ok, op
    return apply(op.value, _decode_tree(base))


def _apply_op(op: Obj, base: t.UiNode) -> object:
    return apply(op, _decode_tree(base))


def _assert_tree(result: object, expected: t.UiNode) -> None:
    assert getattr(result, "ok", False), result
    assert encode_node(result.value) == encode(expected)  # type: ignore[attr-defined]


# ── Happy paths — one per op, driven by the corpus fixture ───────────────────


@corpus_required
def test_edit_node() -> None:
    base = node.bare(fuaran.dashboard("root", children=[_full_metric()]))
    expected = node.bare(fuaran.dashboard("root", children=[fuaran.markdown("metric-1", "Edited")]))
    _assert_tree(_apply_fixture("op-editnode", base), expected)


@corpus_required
def test_update_prop() -> None:
    base = node.bare(fuaran.dashboard("root", children=[_full_metric()]))
    expected = node.bare(fuaran.dashboard("root", children=[_full_metric(label="Updated revenue")]))
    _assert_tree(_apply_fixture("op-updateprop", base), expected)


@corpus_required
def test_replace_binding() -> None:
    base = node.bare(fuaran.dashboard("root", children=[_full_metric()]))
    expected = node.bare(fuaran.dashboard("root", children=[_full_metric(source=t.Static(99.5))]))
    _assert_tree(_apply_fixture("op-replacebinding", base), expected)


@corpus_required
def test_update_style() -> None:
    base = node.bare(fuaran.dashboard("root", children=[_full_metric()]))
    styled = _full_metric().replace(style=t.SemanticStyle(emphasis="Loud", tone="Success", weight="Spacious"))
    expected = node.bare(fuaran.dashboard("root", children=[styled]))
    _assert_tree(_apply_fixture("op-updatestyle", base), expected)


@corpus_required
def test_update_state() -> None:
    base = node.bare(fuaran.dashboard("root", children=[_full_metric()]))
    with_state = _full_metric().replace(state=t.StateBehaviour(on_loading=fuaran.skeleton("skel-1", 3)))
    expected = node.bare(fuaran.dashboard("root", children=[with_state]))
    _assert_tree(_apply_fixture("op-updatestate", base), expected)


@corpus_required
def test_insert_child() -> None:
    base = node.bare(fuaran.dashboard("dash-empty"))
    expected = node.bare(fuaran.dashboard("dash-empty", children=[_full_metric()]))
    _assert_tree(_apply_fixture("op-insertchild", base), expected)


@corpus_required
def test_remove_node() -> None:
    base = fuaran.stack("stack-1", children=[_full_metric(), _MARKDOWN_1])
    expected = fuaran.stack("stack-1", children=[_MARKDOWN_1])
    _assert_tree(_apply_fixture("op-removenode", base), expected)


@corpus_required
def test_move_node() -> None:
    base = node.bare(
        fuaran.dashboard(
            "root",
            children=[
                fuaran.stack("stack-1", children=[_full_metric(), _MARKDOWN_1]),
                node.bare(fuaran.card("card-1")),
            ],
        )
    )
    expected = node.bare(
        fuaran.dashboard(
            "root",
            children=[
                fuaran.stack("stack-1", children=[_MARKDOWN_1]),
                node.bare(fuaran.card("card-1", children=[_full_metric()])),
            ],
        )
    )
    _assert_tree(_apply_fixture("op-movenode", base), expected)


@corpus_required
def test_reorder_children() -> None:
    base = fuaran.stack("stack-1", children=[_full_metric(), _MARKDOWN_1])
    expected = fuaran.stack("stack-1", children=[_MARKDOWN_1, _full_metric()])
    _assert_tree(_apply_fixture("op-reorderchildren", base), expected)


@corpus_required
def test_replace_root() -> None:
    base = fuaran.markdown("anything", "placeholder")
    result = _apply_fixture("op-replaceroot", base)
    assert getattr(result, "ok", False), result
    composite = (CORPUS_ROOT / "nodes" / "composite-root.json").read_text(encoding="utf-8")
    composite = composite[:-1] if composite.endswith("\n") else composite
    assert encode_node(result.value) == composite  # type: ignore[attr-defined]


@corpus_required
def test_batch() -> None:
    # Batch = UpdateStyle(metric-1) then RemoveNode(metric-1): the style change is
    # discarded with the node, markdown-1 is untouched.
    base = fuaran.stack("stack-1", children=[_full_metric(), _MARKDOWN_1])
    expected = fuaran.stack("stack-1", children=[_MARKDOWN_1])
    _assert_tree(_apply_fixture("op-batch", base), expected)


# ── Error paths — typed ApplyError codes (parity with F#/TS) ─────────────────


def _err(result: object) -> str:
    assert isinstance(result, ApplyErr), result
    return result.error.code


def test_node_not_found() -> None:
    base = fuaran.markdown("a", "x")
    assert _err(_apply_op(Obj("RemoveNode", {"target": "ghost"}), base)) == "NodeNotFound"


def test_remove_root_is_kind_mismatch() -> None:
    base = fuaran.markdown("root", "x")
    assert _err(_apply_op(Obj("RemoveNode", {"target": "root"}), base)) == "KindMismatch"


def test_insert_into_childless_kind() -> None:
    base = fuaran.markdown("md", "x")
    child = _decode_tree(fuaran.markdown("new", "y"))
    op = Obj("InsertChild", {"child": child, "parentId": "md", "position": 0})
    assert _err(_apply_op(op, base)) == "ChildlessKind"


def test_insert_position_out_of_range() -> None:
    base = fuaran.stack("s", children=[fuaran.markdown("a", "x")])
    child = _decode_tree(fuaran.markdown("new", "y"))
    op = Obj("InsertChild", {"child": child, "parentId": "s", "position": 5})
    assert _err(_apply_op(op, base)) == "PositionOutOfRange"


def test_insert_duplicate_id() -> None:
    base = fuaran.stack("s", children=[fuaran.markdown("dup", "x")])
    child = _decode_tree(fuaran.markdown("dup", "y"))
    op = Obj("InsertChild", {"child": child, "parentId": "s", "position": 0})
    assert _err(_apply_op(op, base)) == "DuplicateNodeId"


def test_unknown_field() -> None:
    base = fuaran.markdown("md", "x")
    op = Obj("UpdateProp", {"path": "Nope", "target": "md", "value": "v"})
    assert _err(_apply_op(op, base)) == "FieldNotFound"


def test_nested_path_without_a_nested_surface_not_supported() -> None:
    base = fuaran.markdown("md", "x")
    op = Obj("UpdateProp", {"path": "Spec.Text", "target": "md", "value": "v"})
    assert _err(_apply_op(op, base)) == "PathNotSupportedYet"


def test_update_prop_type_mismatch() -> None:
    base = fuaran.heading("h", "Title")
    op = Obj("UpdateProp", {"path": "Level", "target": "h", "value": "not-an-int"})
    assert _err(_apply_op(op, base)) == "KindMismatch"


def test_slot_not_found() -> None:
    base = fuaran.markdown("md", "x")
    op = Obj("ReplaceBinding", {"binding": Obj("Static", {"value": 1}), "slot": "Source", "target": "md"})
    assert _err(_apply_op(op, base)) == "SlotNotFound"


def test_reorder_mismatch() -> None:
    base = fuaran.stack("s", children=[fuaran.markdown("a", "x"), fuaran.markdown("b", "y")])
    op = Obj("ReorderChildren", {"newOrder": Arr(["a", "z"]), "parentId": "s"})
    assert _err(_apply_op(op, base)) == "OrderingMismatch"


def test_move_into_own_descendant_is_cycle() -> None:
    base = fuaran.stack("outer", children=[fuaran.stack("inner", children=[fuaran.markdown("leaf", "x")])])
    op = Obj("MoveNode", {"newParentId": "inner", "newPosition": 0, "target": "outer"})
    assert _err(_apply_op(op, base)) == "KindMismatch"


def test_batch_aborts_and_reverts() -> None:
    base = fuaran.stack("s", children=[fuaran.markdown("a", "x")])
    good = Obj("UpdateProp", {"path": "Text", "target": "a", "value": "changed"})
    bad = Obj("RemoveNode", {"target": "ghost"})
    result = _apply_op(Obj("Batch", {"ops": Arr([good, bad])}), base)
    assert isinstance(result, ApplyErr)
    assert result.error.code == "BatchAborted"
    assert result.error.batch_index == 1


@corpus_required
@pytest.mark.parametrize(
    "name",
    [
        "op-editnode",
        "op-updateprop",
        "op-replacebinding",
        "op-updatestyle",
        "op-updatestate",
        "op-removenode",
        "op-reorderchildren",
        "op-batch",
    ],
)
def test_every_op_fixture_applies_against_a_stack(name: str) -> None:
    """Smoke: each metric-1/stack-1-targeting fixture applies cleanly to a base
    tree carrying the ids it references."""
    base = fuaran.stack("stack-1", children=[_full_metric(), _MARKDOWN_1])
    result = _apply_fixture(name, base)
    assert getattr(result, "ok", False), result


# ── Nested paths (WIRE_FORMAT.md §3.4 — Phase-364 parity) ────────────────────


def _channel_grid(*, first_label: str = "Channel") -> t.UiNode:
    return node.bare(
        fuaran.grid(
            "grid-1",
            source=binding.opaque(),
            columns=[t.Column(first_label), t.Column("Spend")],
        )
    )


def _mix_chart(*, y_fields: list[str]) -> t.UiNode:
    return node.bare(fuaran.chart("chart-1", source=binding.opaque(), x_field="month", y_fields=y_fields))


def _signup_form(*, name_required: bool) -> t.UiNode:
    return node.bare(
        fuaran.form(
            "form-1",
            fields=[
                t.FormField("name", t.LiteralText("Name"), t.TextField(t.Static("")), name_required),
                t.FormField("age", t.LiteralText("Age"), t.NumberField(t.Static(0)), False),
            ],
        )
    )


@corpus_required
def test_nested_column_label_renames_a_grid_column() -> None:
    base = _channel_grid()
    expected = _channel_grid(first_label="Channel name")
    _assert_tree(_apply_fixture("op-updateprop-nested-column0-label", base), expected)


@corpus_required
def test_nested_yfield_rewrites_an_indexed_scalar_leaf() -> None:
    base = _mix_chart(y_fields=["revenue", "cost"])
    expected = _mix_chart(y_fields=["revenue", "profit"])
    _assert_tree(_apply_fixture("op-updateprop-nested-yfield1", base), expected)


@corpus_required
def test_nested_field_required_flips_a_form_field_flag() -> None:
    base = _signup_form(name_required=False)
    expected = _signup_form(name_required=True)
    _assert_tree(_apply_fixture("op-updateprop-nested-field0-required", base), expected)


@corpus_required
def test_nested_bad_index_is_position_out_of_range() -> None:
    op = decode_op(_op_text("op-updateprop-nested-badindex"))
    assert op.ok, op
    assert _err(apply(op.value, _decode_tree(_channel_grid()))) == "PositionOutOfRange"


@corpus_required
def test_nested_bad_field_is_field_not_found() -> None:
    op = decode_op(_op_text("op-updateprop-nested-badfield"))
    assert op.ok, op
    assert _err(apply(op.value, _decode_tree(_channel_grid()))) == "FieldNotFound"


@corpus_required
def test_nested_malformed_index_is_path_invalid() -> None:
    op = decode_op(_op_text("op-updateprop-nested-malformed"))
    assert op.ok, op
    assert _err(apply(op.value, _decode_tree(_channel_grid()))) == "PathInvalid"


def test_nested_tab_header_label_renames_the_second_header() -> None:
    def tabs_with(second_label: str) -> t.UiNode:
        return node.bare(
            fuaran.tabs(
                "analysis-tabs",
                children=[fuaran.markdown("tab-a", "A"), fuaran.markdown("tab-b", "B")],
                tab_headers=[
                    t.TabHeader(label=t.LiteralText("Overview")),
                    t.TabHeader(label=t.LiteralText(second_label)),
                ],
            )
        )

    op = Obj(
        "UpdateProp",
        {
            "path": "TabHeaders[1].Label",
            "target": "analysis-tabs",
            "value": Obj("Literal", {"text": "Breakdown"}),
        },
    )
    result = _apply_op(op, tabs_with("Detail"))
    _assert_tree(result, tabs_with("Breakdown"))


def test_nested_tab_headers_absent_is_position_out_of_range() -> None:
    base = fuaran.tabs("bare-tabs", children=[fuaran.markdown("only", "x")])
    op = Obj(
        "UpdateProp",
        {"path": "TabHeaders[0].Label", "target": "bare-tabs", "value": Obj("Literal", {"text": "X"})},
    )
    assert _err(_apply_op(op, node.bare(base))) == "PositionOutOfRange"


def test_nested_list_segment_without_index_is_path_invalid() -> None:
    op = Obj("UpdateProp", {"path": "Columns.Label", "target": "grid-1", "value": "X"})
    assert _err(_apply_op(op, _channel_grid())) == "PathInvalid"


def test_nested_closure_leaf_is_path_not_supported() -> None:
    op = Obj("UpdateProp", {"path": "Columns[0].Kind", "target": "grid-1", "value": "Text"})
    assert _err(_apply_op(op, _channel_grid())) == "PathNotSupportedYet"
