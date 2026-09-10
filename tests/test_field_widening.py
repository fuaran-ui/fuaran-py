"""Phase 1577 — the field-widening wave: DataGrid, Chart, tooltip, Link, Table.

Twenty quarantined corpus fixtures name a field this phase adds, and every one of
them was unreachable from the typed authoring surface for one reason: the record
was NARROWER THAN THE WIRE. (Two of the twenty carried a SECOND cause behind the
field gap — a binding and a text source the typed unions did not spell — and were
held back here until Phase 1580 added them. The count is asserted below, not
stated here alone.) The grid carried none of the
declarative sort / page / edit-state slots and no ``reorderable``; ``Chart``
reached eight slots fewer than the wire, including the whole §4l annotation
family; ``UiNode`` had no ``tooltip``, ``Link`` no ``protection``, and the static
table neither ``sortable`` nor ``defaultSort``.

The per-fixture byte-identity assertions live where the host already keeps them —
``tests/test_ui_authoring.py``'s single parity table — because a second table is
how two of them end up disagreeing about what the corpus says. What lives here is
everything a fixture set cannot pin on its own:

* the OMISSION rule each new slot obeys, per record, so a later default that
  starts emitting is caught by something other than a fixture that happens to
  cover it;
* the §4l grounding the ``chart-annotations`` requirement asks for
  (FUARAN137-141), each with the clean case beside it — an assertion that a
  defect is raised passes on a validator that raises everything;
* the RENDER PROJECTION's treatment of every new field, as a declared table:
  rendered, or exempt BY NAME.
"""

from __future__ import annotations

import math

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_ui import decode_node
from fuaran_ui.renderer import render_email, render_html, render_markdown
from fuaran_ui.schema import types as t
from fuaran_ui.ui import encode, fuaran, node
from fuaran_ui.validator import validate_node

# ── The omission rule, per record ────────────────────────────────────────────
#
# Every slot this phase added is ABSENT by default, so a tree authored before it
# existed encodes byte-for-byte as it did. Three shapes, and the difference
# matters to a host reading the document back:
#
#   * OPTIONAL (`None`) — absent unless the author named it. `sortStateKey`,
#     `pageSize`, `defaultSort`, `protection`, `tooltip`, every Chart slot.
#   * TRI-STATE (`bool | None`) — absent, `true`, or `false`, and the third is
#     not the first: a column that explicitly declines a sort has said something
#     a column that was never asked has not. `Column.sortable` / `.editable`,
#     `Table.sortable`.
#   * OMITTED-AT-FALSE (`bool`) — `reorderable`, joining `editable` and the
#     fuaran#1125 / #1473 flags on exactly their terms.


def _grid(**kwargs: object) -> dict[str, object]:
    return t.DataGrid(t.Static([]), **kwargs).to_wire().fields  # type: ignore[arg-type]


def test_every_new_grid_slot_is_absent_by_default() -> None:
    keys = set(_grid())
    assert keys.isdisjoint(
        {"sortStateKey", "defaultSort", "pageSize", "pageStateKey", "editStateKey", "reorderable"}
    ), "a grid that declares no behaviour must encode exactly the pre-phase document"


@pytest.mark.parametrize(
    ("kwargs", "key", "expected"),
    [
        ({"sort_state_key": "k"}, "sortStateKey", "k"),
        ({"page_size": 20}, "pageSize", 20),
        ({"page_state_key": "p"}, "pageStateKey", "p"),
        ({"edit_state_key": "e"}, "editStateKey", "e"),
        ({"reorderable": True}, "reorderable", True),
    ],
)
def test_a_declared_grid_slot_rides(kwargs: dict[str, object], key: str, expected: object) -> None:
    assert _grid(**kwargs)[key] == expected


def test_reorderable_is_omitted_at_false_not_written_as_false() -> None:
    """The flag family's rule: every host's decoder restores ``False`` on absence,
    so a grid that does not reorder pays no key for saying so."""
    assert "reorderable" not in _grid(reorderable=False)


def test_grid_default_sort_lowers_to_the_bare_pair() -> None:
    sort = _grid(default_sort=t.DefaultSort(1, "desc"))["defaultSort"]
    assert sort.tag is None and sort.fields == {"column": 1, "direction": "desc"}  # type: ignore[union-attr]


@pytest.mark.parametrize("slot", ["sortable", "editable"])
def test_a_column_opt_out_is_tri_state(slot: str) -> None:
    """Absent, ``true`` and ``false`` are three different documents. The middle
    one is what ``grid-bound-sort``'s note column and ``grid-declared-edit``'s
    read-only column each declare, and neither is expressible as a plain bool."""
    assert slot not in t.Column(label="L", field_name="f").to_wire().fields  # type: ignore[union-attr]
    assert t.Column(label="L", field_name="f", **{slot: False}).to_wire().fields[slot] is False  # type: ignore[union-attr]
    assert t.Column(label="L", field_name="f", **{slot: True}).to_wire().fields[slot] is True  # type: ignore[union-attr]


def test_static_rows_omits_its_two_optional_slots_rather_than_nulling_them() -> None:
    """``staticRows`` is a nested object, and the nesting is exactly where a
    ``None`` would have survived as a JSON ``null`` — ``_lower`` keeps every key a
    plain dict carries, so the record builds it through the omitting helper."""
    plain = t.Table((t.LiteralText("A"),), ((t.LiteralText("1"),),)).to_wire().fields["staticRows"]
    assert set(plain.fields) == {"headers", "rows"}  # type: ignore[union-attr]

    declared = (
        t.Table((t.LiteralText("A"),), ((t.LiteralText("1"),),), sortable=False, default_sort=t.DefaultSort(0, "asc"))
        .to_wire()
        .fields["staticRows"]
    )
    assert declared.fields["sortable"] is False  # type: ignore[union-attr]
    assert declared.fields["defaultSort"].fields == {"column": 0, "direction": "asc"}  # type: ignore[union-attr]


def test_link_protection_is_absent_unless_declared() -> None:
    plain = t.Link(t.Static("https://example.com"), t.LiteralText("Home")).to_wire()
    assert "protection" not in plain.fields
    assert (
        t.Link(t.Static("mailto:a@b.c"), t.LiteralText("Mail"), protection="email").to_wire().fields["protection"]
        == "email"
    )


def test_node_tooltip_is_absent_unless_declared() -> None:
    plain = fuaran.markdown("m", "hi")
    assert "tooltip" not in plain.to_wire().extras
    assert node.with_tooltip("A hint", plain).to_wire().extras["tooltip"] == "A hint"


def test_a_bound_tooltip_keeps_its_binding() -> None:
    """The slot is a ``TextSource``, not a ``str``: a hint that must be resolved
    or localised is the ordinary case, and a bare string is only its canonical
    literal form."""
    bound = fuaran.markdown("m", "hi").replace(tooltip=t.Bound(t.State("hint", "")))
    assert bound.to_wire().extras["tooltip"].tag == "Bound"


_CHART_SLOTS = {
    "value_format": ("valueFormat", t.FmtCurrency("GBP")),
    "x_title": ("xTitle", "Quarter"),
    "y_title": ("yTitle", "Revenue"),
    "subtitle": ("subtitle", "Millions"),
    "legend_position": ("legendPosition", "Bottom"),
    "data_labels": ("dataLabels", "Ends"),
    "x_scale": ("xScale", "Temporal"),
    "annotations": ("annotations", [t.ReferenceLine(1)]),
}


def test_every_new_chart_slot_is_absent_by_default() -> None:
    plain = fuaran.chart("c", source=t.Static([]), x_field="x", y_fields=["y"]).kind.to_wire()  # type: ignore[union-attr]
    assert set(plain.fields).isdisjoint({wire for wire, _ in _CHART_SLOTS.values()})


@pytest.mark.parametrize("member", sorted(_CHART_SLOTS))
def test_a_declared_chart_slot_rides(member: str) -> None:
    wire_key, value = _CHART_SLOTS[member]
    spec = fuaran.chart("c", source=t.Static([]), x_field="x", y_fields=["y"], **{member: value}).kind
    assert wire_key in spec.to_wire().fields  # type: ignore[union-attr]


def test_an_empty_annotation_list_is_not_the_same_document_as_no_list() -> None:
    """Both draw nothing, and they are still two documents. Absence is what keeps
    a pre-§4l chart byte-identical; an empty array is an author who declared the
    slot and put nothing in it."""
    absent = fuaran.chart("c", source=t.Static([]), x_field="x", y_fields=["y"]).kind.to_wire()  # type: ignore[union-attr]
    empty = fuaran.chart("c", source=t.Static([]), x_field="x", y_fields=["y"], annotations=[]).kind.to_wire()  # type: ignore[union-attr]
    assert "annotations" not in absent.fields
    assert empty.fields["annotations"].items == []  # type: ignore[union-attr]


def test_the_three_annotation_members_lower_to_their_wire_tags() -> None:
    """The union is CLOSED at three, and each lowers to the tag the lowering
    dispatches on (``fuaran_ui.charts`` reads ``ReferenceLine`` / ``EventMarker``
    / ``RangeBand`` straight off the tag)."""
    assert t.ReferenceLine(140, "Target").to_wire().tag == "ReferenceLine"  # type: ignore[union-attr]
    assert t.EventMarker(t.AnnotationCategory("Q3")).to_wire().tag == "EventMarker"  # type: ignore[union-attr]
    assert t.RangeBand(t.ValueRange(0, 1)).to_wire().tag == "RangeBand"  # type: ignore[union-attr]
    assert t.AnnotationDate("2026-01-01").to_wire().fields == {"iso": "2026-01-01"}  # type: ignore[union-attr]
    assert t.XRange(t.AnnotationCategory("a"), t.AnnotationCategory("b")).to_wire().tag == "XRange"  # type: ignore[union-attr]


def test_an_unlabelled_annotation_omits_the_label_key() -> None:
    assert "label" not in t.ReferenceLine(0).to_wire().fields  # type: ignore[union-attr]
    assert t.ReferenceLine(0, "Zero").to_wire().fields["label"] == "Zero"  # type: ignore[union-attr]


# ── The §4l grounding (FUARAN137-141) ────────────────────────────────────────
#
# The `chart-annotations` requirement binds every conformant host, and its
# grounding half is what makes the declared-not-sniffed posture a REFUSAL rather
# than a convention. Each defect below is asserted beside the clean tree it is a
# mutation of: a test that only asserts a code is raised passes on a validator
# that raises it unconditionally.

_QUARTERS = [
    {"quarter": "Q1", "revenue": 120},
    {"quarter": "Q2", "revenue": 150},
    {"quarter": "Q3", "revenue": 90},
    {"quarter": "Q4", "revenue": 175},
]


def _codes(
    *, kind: str = "Bar", x_scale: str | None = None, annotations: list[object], rows: object = None
) -> list[str]:
    chart = fuaran.chart(
        "c",
        source=t.Static(_QUARTERS if rows is None else rows),
        x_field="quarter",
        y_fields=["revenue"],
        kind=kind,  # type: ignore[arg-type]
        x_scale=x_scale,  # type: ignore[arg-type]
        annotations=annotations,  # type: ignore[arg-type]
    )
    return [f.code for f in validate_node(chart.to_wire())]


def test_a_grounded_annotation_set_raises_nothing() -> None:
    """The falsifier for every assertion below."""
    assert (
        _codes(
            annotations=[
                t.ReferenceLine(140, "Target"),
                t.EventMarker(t.AnnotationCategory("Q3"), "Repricing"),
                t.RangeBand(t.XRange(t.AnnotationCategory("Q2"), t.AnnotationCategory("Q3"))),
                t.RangeBand(t.ValueRange(0, 100)),
            ]
        )
        == []
    )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_fuaran137_refuses_a_non_finite_reference_line(value: float) -> None:
    assert _codes(annotations=[t.ReferenceLine(value)]) == ["FUARAN137"]


def test_fuaran137_reaches_a_value_bands_ends_too() -> None:
    """The band arm's ENDS each carry their own subject, so a pair with one bad
    end names that end rather than the pair."""
    codes = _codes(annotations=[t.RangeBand(t.ValueRange(0, math.nan))])
    assert codes == ["FUARAN137"]


def test_fuaran137_subjects_are_per_case_ordinals() -> None:
    """Counted over ALL of the case's members, not the surviving ones: a defect
    is something to repair, not a member to renumber around."""
    findings = [
        f
        for f in validate_node(
            fuaran.chart(
                "c",
                source=t.Static(_QUARTERS),
                x_field="quarter",
                y_fields=["revenue"],
                kind="Bar",
                annotations=[t.ReferenceLine(1), t.ReferenceLine(math.nan)],
            ).to_wire()
        )
    ]
    assert len(findings) == 1
    assert "reference line 1" in findings[0].message


def test_fuaran138_refuses_a_category_no_row_carries() -> None:
    codes = _codes(annotations=[t.EventMarker(t.AnnotationCategory("Q9"))])
    assert codes == ["FUARAN138"]


def test_fuaran138_refuses_a_duplicated_category_with_its_own_wording() -> None:
    """Absent and duplicated are different repairs — a different key, versus
    aggregating the rows — so they are different messages under one code."""
    rows = [{"quarter": "Q1", "revenue": 1}, {"quarter": "Q1", "revenue": 2}]
    findings = [
        f
        for f in validate_node(
            fuaran.chart(
                "c",
                source=t.Static(rows),
                x_field="quarter",
                y_fields=["revenue"],
                kind="Bar",
                annotations=[t.EventMarker(t.AnnotationCategory("Q1"))],
            ).to_wire()
        )
        if f.code == "FUARAN138"
    ]
    assert len(findings) == 1
    assert "2 rows carry" in findings[0].message


def test_fuaran138_stands_down_on_a_source_it_cannot_read() -> None:
    """The FUARAN086 posture: a ``State`` source's rows are not in the tree, so
    the key cannot be shown to be wrong and is not refused."""
    assert _codes(annotations=[t.EventMarker(t.AnnotationCategory("Q9"))], rows=None, kind="Bar") == ["FUARAN138"]
    chart = fuaran.chart(
        "c",
        source=t.State("rows", []),
        x_field="quarter",
        y_fields=["revenue"],
        kind="Bar",
        annotations=[t.EventMarker(t.AnnotationCategory("Q9"))],
    )
    assert [f.code for f in validate_node(chart.to_wire())] == []


def test_fuaran139_refuses_a_date_on_a_band_axis() -> None:
    assert _codes(annotations=[t.EventMarker(t.AnnotationDate("2026-02-14"))]) == ["FUARAN139"]


def test_fuaran139_refuses_a_category_on_a_temporal_axis() -> None:
    rows = [{"quarter": "2026-01-05", "revenue": 1}]
    assert _codes(x_scale="Temporal", rows=rows, annotations=[t.EventMarker(t.AnnotationCategory("Q1"))]) == [
        "FUARAN139"
    ]


def test_fuaran140_and_139_are_both_reported_when_both_hold() -> None:
    """A date that does not parse AND sits under a band axis has two separate
    repairs; naming one would leave the author fixing it twice."""
    assert sorted(_codes(annotations=[t.EventMarker(t.AnnotationDate("15/01/2026"))])) == [
        "FUARAN139",
        "FUARAN140",
    ]


def test_fuaran140_refuses_a_date_that_names_no_calendar_day() -> None:
    rows = [{"quarter": "2026-01-05", "revenue": 1}]
    assert _codes(x_scale="Temporal", rows=rows, annotations=[t.EventMarker(t.AnnotationDate("2026-02-30"))]) == [
        "FUARAN140"
    ]


def test_fuaran141_refuses_a_backwards_value_band() -> None:
    assert _codes(annotations=[t.RangeBand(t.ValueRange(260, 200))]) == ["FUARAN141"]


def test_fuaran141_refuses_a_backwards_category_band() -> None:
    assert _codes(annotations=[t.RangeBand(t.XRange(t.AnnotationCategory("Q3"), t.AnnotationCategory("Q2")))]) == [
        "FUARAN141"
    ]


def test_fuaran141_refuses_a_backwards_date_band() -> None:
    rows = [{"quarter": "2026-01-05", "revenue": 1}, {"quarter": "2026-03-05", "revenue": 2}]
    assert _codes(
        x_scale="Temporal",
        rows=rows,
        annotations=[t.RangeBand(t.XRange(t.AnnotationDate("2026-03-01"), t.AnnotationDate("2026-01-01")))],
    ) == ["FUARAN141"]


def test_fuaran141_is_silent_when_an_end_is_itself_ungrounded() -> None:
    """An interval with one end nowhere has no order to be wrong about, and
    reporting both would be two findings for one repair."""
    assert _codes(annotations=[t.RangeBand(t.XRange(t.AnnotationCategory("Q9"), t.AnnotationCategory("Q2")))]) == [
        "FUARAN138"
    ]


def test_a_pie_is_silent_about_every_address() -> None:
    """The polar arm has no x axis, so there is no axis form for an address to
    match or mismatch — a code that fired there would report the absence of a
    space rather than a defect in the document."""
    assert _codes(kind="Pie", annotations=[t.EventMarker(t.AnnotationDate("2026-02-14"))]) == []


def test_a_pie_still_refuses_a_non_finite_value() -> None:
    """FUARAN137 reads the spec's own literal, so it needs no axis — and the
    damage it names is the axis DOMAIN, which the value would poison wherever the
    lowering used it."""
    assert _codes(kind="Pie", annotations=[t.ReferenceLine(math.nan)]) == ["FUARAN137"]


@corpus_required
@pytest.mark.parametrize(
    "fixture_id", ["chart-annotations", "chart-annotation-bands", "chart-annotation-events", "chart-temporal-x"]
)
def test_the_corpus_annotation_fixtures_are_grounded(fixture_id: str) -> None:
    """The corpus is the byte authority AND, here, the ground truth for the rules:
    a grounding that refused a fixture would be refusing a document every other
    host draws."""
    decoded = decode_node((CORPUS_ROOT / "nodes" / f"{fixture_id}.json").read_text(encoding="utf-8"))
    assert decoded.ok, decoded
    assert [f.code for f in validate_node(decoded.value)] == []


# ── The render projection's treatment of every new field ─────────────────────
#
# The phase's bar is that the projection TOLERATES every new field — renders it,
# or declares the exemption by name. This is that declaration, and it is checked
# rather than asserted: the rendered rows must appear in the emitted HTML, and
# every field must survive all three projections without raising.

#: field → the marker its rendered form leaves in the HTML.
RENDERED_FIELDS = {
    "UiNode.tooltip": "fuaran-tooltip",
    "Table.sortable": 'data-fuaran-sortable="true"',
    "Table.defaultSort": 'data-fuaran-sort-column="1"',
    "Chart.annotations": "annotation|",
}

#: field → why the renderer carries it without drawing it. Each is a DECLARATIVE
#: behaviour whose realisation needs a client runtime this server-side renderer
#: does not have: the key names where the live sort / page / edit state lives,
#: and a static HTML fragment has no state to read it from. They are decoded,
#: re-encoded byte-exact, and reach a client host intact — which is the whole
#: point of the slot being a State key rather than a closure.
TOLERATED_FIELDS = {
    "DataGrid.sortStateKey": "the live sort lives in host State; SSR emits the rows in source order",
    "DataGrid.defaultSort": "same seam — the pre-click order is applied by whoever owns the State key",
    "DataGrid.pageSize": "paging is a client affordance; the fragment carries every row",
    "DataGrid.pageStateKey": "as above — the page position is a State key no static render reads",
    "DataGrid.editStateKey": "edits accumulate in host State; the server renderer is read-only",
    "DataGrid.reorderable": "a drag affordance needs a client runtime",
    "Column.sortable": "a per-column opt-out from an affordance this renderer does not offer",
    "Column.editable": "a per-column opt-out from an affordance this renderer does not offer",
    "Link.protection": "the anti-scraper emission strategy is not implemented here; the plain "
    "mailto: href is emitted, which is the pre-812 behaviour and no worse",
    "Chart.subtitle": "consumed by the chart lowering, not by an HTML element",
    "Chart.xTitle": "consumed by the chart lowering",
    "Chart.yTitle": "consumed by the chart lowering",
    "Chart.valueFormat": "consumed by the chart lowering",
    "Chart.legendPosition": "consumed by the chart lowering",
    "Chart.dataLabels": "consumed by the chart lowering",
    "Chart.xScale": "consumed by the chart lowering",
}

#: Every fixture this phase brought into reach, so the tolerance sweep below runs
#: over the real documents rather than over a constructed sample of them.
WIDENED_FIXTURES = [
    "chart-annotation-bands",
    "chart-annotation-events",
    "chart-annotations",
    "chart-axis-titles",
    "chart-data-labels",
    "chart-legend-position",
    "chart-temporal-x",
    "chart-value-format",
    "grid-bound-sort",
    "grid-paged",
    "grid-paged-sorted",
    "grid-reorderable",
    "grid-sort-state-key",
    "link-protected-1",
    "table-sortable-1",
    "tooltip-button-1",
    "tooltip-icon-button-1",
    "transfer-board",
    # The two the field widening alone could not reach. Each needed a SECOND
    # construct — a `Binding.Query` grid source, a `TextSource.I18n` tooltip
    # value — which Phase 1580 added to the typed unions. They were recorded here
    # by name rather than by a count, with a falsifier that reddened the moment
    # the construct landed, and this is that falsifier having fired.
    "grid-declared-edit",
    "tooltip-metric-1",
]


def test_every_new_field_has_exactly_one_declared_disposition() -> None:
    """A field in neither table is one nobody decided about, and a field in both
    is two answers to one question."""
    assert set(RENDERED_FIELDS).isdisjoint(TOLERATED_FIELDS)
    assert len(RENDERED_FIELDS) + len(TOLERATED_FIELDS) == 20


@corpus_required
@pytest.mark.parametrize("fixture_id", WIDENED_FIXTURES)
def test_all_three_projections_tolerate_the_widened_fixture(fixture_id: str) -> None:
    """HTML, markdown and the email digest each walk the whole tree. A field a
    projection could not carry would raise here, not degrade quietly."""
    decoded = decode_node((CORPUS_ROOT / "nodes" / f"{fixture_id}.json").read_text(encoding="utf-8"))
    assert decoded.ok, decoded
    for projection in (render_html, render_markdown, render_email):
        assert isinstance(projection(decoded.value), str)


@corpus_required
@pytest.mark.parametrize(
    ("fixture_id", "field_name"),
    [
        ("tooltip-button-1", "UiNode.tooltip"),
        ("table-sortable-1", "Table.sortable"),
        ("table-sortable-1", "Table.defaultSort"),
        ("chart-annotations", "Chart.annotations"),
    ],
)
def test_a_rendered_field_actually_reaches_the_html(fixture_id: str, field_name: str) -> None:
    decoded = decode_node((CORPUS_ROOT / "nodes" / f"{fixture_id}.json").read_text(encoding="utf-8"))
    assert decoded.ok, decoded
    assert RENDERED_FIELDS[field_name] in render_html(decoded.value)


def test_the_widened_set_is_the_one_the_parity_table_carries() -> None:
    """The two lists are kept in different files on purpose — the byte assertions
    belong to the host's one parity table — so this pins them to each other."""
    from test_ui_authoring import _authored

    assert set(WIDENED_FIXTURES) <= set(_authored())


def test_the_census_is_computed_rather_than_recited() -> None:
    """The module docstring's numbers, asserted. Prose carrying a count is wrong
    out loud the first time the list moves and nobody re-reads the paragraph;
    this is the same discipline the quarantine's own census keeps."""
    assert len(WIDENED_FIXTURES) == 20
    assert len(set(WIDENED_FIXTURES)) == len(WIDENED_FIXTURES)


@corpus_required
@pytest.mark.parametrize("fixture_id", WIDENED_FIXTURES)
def test_the_widened_fixture_encodes_byte_identically(fixture_id: str) -> None:
    """A thin restatement of the parity table's own assertion, kept because this
    module is where a reader looks for the phase's acceptance bar."""
    from test_ui_authoring import _authored

    text = (CORPUS_ROOT / "nodes" / f"{fixture_id}.json").read_text(encoding="utf-8")
    assert encode(_authored()[fixture_id]) == (text[:-1] if text.endswith("\n") else text)
