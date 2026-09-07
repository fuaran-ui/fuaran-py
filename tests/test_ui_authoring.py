"""Phase 278 — the ergonomic authoring surface round-trips byte-exact to the corpus.

Each tree below is authored with the typed smart constructors (``fuaran.*`` +
``binding`` / ``action`` / ``format``), exactly as a human developer would. The
assertion is the Phase 278 acceptance bar: ``encode(tree)`` is **byte-identical**
to the canonical wire-format corpus fixture — proving the typed authoring model
lowers to the same canonical JSON the F# and TypeScript tiers produce.

The smart constructors inject per-kind ARIA (the parity feature); the corpus
fixtures were authored without it, so ARIA-bearing kinds are wrapped in
``node.bare(...)`` to match — the same way the F# corpus fixtures use a low-level
builder rather than the ARIA-injecting smart constructors. ARIA injection itself is
asserted separately below.
"""

from __future__ import annotations

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py.canonical import encode_value
from fuaran_py.dataframe.codec import encode_expr_value
from fuaran_py.dataframe.model import ApplyFn
from fuaran_py.model import Arr, Obj
from fuaran_py.schema import types as t
from fuaran_py.ui import (
    Expr,
    Frame,
    ParamDecl,
    accessibility,
    action,
    binding,
    col,
    encode,
    frame,
    fuaran,
    lit,
    node,
    param,
    rule,
    track,
)


def _expr_predicate() -> Obj:
    """``switch-predicate``'s second case: a ``Binding.Expr`` over one param.

    Assembled from the pieces the compute surface already carries — the expression
    DSL for the ``ColExpr``, :class:`ParamDecl` for the ``params`` entry — because
    the ``binding`` namespace has no ``Expr`` constructor of its own. That gap is
    real and wider than the switch case (``Query`` / ``Computed`` / ``I18n`` /
    ``Data`` are missing the same way); it is NOT what this test is about, and the
    structural ``Obj`` is the ``Value``-typed ``when`` slot's ordinary escape.
    """
    return Obj(
        "Expr",
        {
            "expr": encode_expr_value((param("itemCount") > lit(3)).colexpr),
            "params": Arr([ParamDecl("itemCount", t.State("cart.itemCount", None)).to_wire()]),
        },
    )


def _dept_frame() -> object:
    """``multiselect-chip-list-param``'s pipeline: a LIST parameter fed by the
    chip's own ``Filter`` slot, so the grid re-derives when the reader selects."""
    return (
        frame(
            {"dept": ["eng", "sales", "ops"], "amount": [100, 90, 70]},
            schema=[("dept", "string"), ("amount", "int")],
        )
        .filter(col("dept").is_in(param("depts")))
        .bind(ParamDecl("depts", t.Filter("depts")))
    )


def _content_frame() -> object:
    """``filterable-static-dashboard``'s pipeline, shared by its chart and grid.

    The schema order is the DECLARED one and the fixture keeps it, so a frame
    built from an unordered mapping alone would encode a different `schema` array
    from the same columns.
    """
    return (
        frame(
            {
                "region": ["emea", "amer"],
                "genre": ["drama", "docs"],
                "month": ["jan", "jan"],
                "retention": [0.62, 0.55],
            },
            schema=[("region", "string"), ("genre", "string"), ("month", "string"), ("retention", "float")],
        )
        .filter(col("region").eq(param("region")))
        .filter(col("genre").eq(param("genre")))
        .bind(ParamDecl("region", t.Filter("region")), ParamDecl("genre", t.Filter("genre")))
    )


# ── Phase 1577 — the field-widening wave's shared row sets ───────────────────
#
# Three fixtures each reuse one of these verbatim, so a single copy is what keeps
# the annotation fixtures' grounding (the category keys an address must name)
# reading off the same rows the corpus carries.

_LEDGER_ROWS = [{"month": "Jan", "revenue": 980}, {"month": "Feb", "revenue": 1105}]
_QUARTER_ROWS = [
    {"quarter": "Q1", "revenue": 120},
    {"quarter": "Q2", "revenue": 150},
    {"quarter": "Q3", "revenue": 90},
    {"quarter": "Q4", "revenue": 175},
]
_DAY_ROWS = [
    {"day": "2026-01-05", "sessions": 40},
    {"day": "2026-02-05", "sessions": 65},
    {"day": "2026-03-05", "sessions": 55},
]


def _numeric_col(label: str, field_name: str) -> t.Column:
    return t.Column(label=label, field_name=field_name, kind=t.ColumnKind("Numeric"))


def _board_column(node_id: str, key: str, rows: list[dict[str, object]], *, releases: bool) -> t.UiNode:
    """One column of ``transfer-board`` — a reorderable grid on the shared
    ``board`` transfer key. The archive column ACCEPTS and never releases, which
    is why the two ends are separate members rather than one symmetric key."""
    return node.bare(
        fuaran.grid(
            node_id,
            source=t.State(key, rows),
            columns=[t.Column(label="Card", field_name="card")],
            row_key_field="card",
            reorderable=True,
            transfer_in_key="board",
            transfer_out_key="board" if releases else None,
        )
    )


# ── Phase 1579 — the Drawing / Fact / Mount builders ─────────────────────────


def _mark_style(fill: str, stroke: str) -> t.DrawStyle:
    """``drawing-1``'s per-mark style: the root style's four slots, restated with
    the mark's own colours. A shape emits only what differs from the inherited
    default, so a mark that restates all four carries all four."""
    return t.DrawStyle(fill=t.Static(fill), opacity=t.Static(0.9), stroke=t.Static(stroke), stroke_width=t.Static(1.5))


def _label_style(
    *, rotation: float | None = None, anchor: t.TextAnchor = "Middle", tip: str | None = None
) -> t.DrawStyle:
    """The axis-label text cluster. ``rotation=0`` is a DOCUMENT (``drawing-rotated-labels``
    carries an explicit zero beside an absent one), so only ``None`` omits the key."""
    return t.DrawStyle(
        emphasis="Loud",
        fill=t.Static("#111111"),
        font_family="system-ui, sans-serif",
        font_size=14,
        rotation=rotation,
        text_anchor=anchor,
        tip=t.LiteralText(tip) if tip is not None else None,
    )


def _days_overdue() -> Expr:
    """``dateDiffDays(param 'today', col 'due')`` — the derive step the two ``Now``
    fixtures share. Assembled from :class:`ApplyFn` directly because the scalar-fn
    surface exposes the one-argument verbs as ``Expr`` methods and this one takes
    two roots, neither of which is a receiver. The same escape ``_expr_predicate``
    takes above, and for the same reason."""
    return Expr(ApplyFn("dateDiffDays", [param("today").colexpr, col("due").colexpr]))


#: The invoice grid both ``Now`` fixtures render, including the derived column.
_OVERDUE_COLUMNS = [
    t.Column(label="Invoice", field_name="id"),
    t.Column(label="Due", field_name="due"),
    t.Column(label="Days overdue", field_name="daysOverdue"),
]


def _overdue_frame(ids: list[str], dues: list[str], grain: t.TimeGrain | None) -> object:
    return (
        frame({"id": ids, "due": dues}, schema=[("id", "string"), ("due", "string")])
        .derive("daysOverdue", _days_overdue())
        .bind(ParamDecl("today", t.Now(grain)))
    )


def _ticket_columns(*names: str) -> list[t.Column]:
    labels = {"id": "Ticket", "priority": "Priority", "assignee": "Assignee", "note": "Note"}
    return [t.Column(label=labels[n], field_name=n) for n in names]


def _two_ticket_frame(*, with_assignee: bool = False) -> Frame:
    """The two-row ticket table the first two master-detail fixtures select over.

    ``master-detail-multi-field`` carries an extra ``assignee`` column and nothing
    else differs, so the pair share one declaration; the three-row table below is
    a genuinely different one (its own priorities, its own note column) and is
    kept apart rather than derived by slicing.
    """
    columns: dict[str, list[object]] = {"id": ["TCK-2041", "TCK-2042"], "priority": ["high", "low"]}
    schema = [("id", "string"), ("priority", "string")]
    if with_assignee:
        columns["assignee"] = ["R. Okafor", "M. Lindqvist"]
        schema.append(("assignee", "string"))
    return frame(columns, schema=schema)


def _three_ticket_frame() -> Frame:
    """``master-detail-preselected-second-row``'s table: three rows, id + priority + note."""
    return frame(
        {
            "id": ["TCK-2041", "TCK-2042", "TCK-2043"],
            "priority": ["high", "medium", "low"],
            "note": ["Payment gateway timeout", "Search index stale", "Avatar upload fails"],
        },
        schema=[("id", "string"), ("priority", "string"), ("note", "string")],
    )


def _picked_ticket_frame() -> Frame:
    """The three-row table filtered to the SELECTED ticket. Two consumers — the
    related grid and the note callout — build from the same declaration rather
    than from two that could drift."""
    return (
        _three_ticket_frame()
        .filter(col("id").eq(param("ticketId")))
        .bind(ParamDecl("ticketId", _selected("id", "TCK-2042")))
    )


#: ``btn-json-payloads``'s payload, authored ONCE and handed to all three of the
#: format's JSON-carrying actions, exactly as the fixture does. Its contents are
#: the canonical writer's hard cases in one value: a whole-valued float, an
#: exponent that must not round-trip as a decimal, a raw control character, an
#: astral-plane character, an escaped quote and backslash, an empty object and an
#: empty array, and keys whose authored order is not their sorted order.
_JSON_PAYLOAD: dict[str, object] = {
    "alpha": 1,
    "escapes": 'quote" back\\slash \x01 astral-\U0001f600',
    "float-whole": 2,
    "nested": [{"a": 1e-07, "b": True}, [0, 3], {}, []],
    "zeta": "last key authored first",
}


def _overview_panel() -> t.UiNode:
    """``composite-tabs-panels``'s first tab — a ``Card``-role box with a GRID
    layout, which ``fuaran.card`` cannot reach (it fixes the layout), so the
    record is built directly. That is the intended escape hatch, not a gap: the
    smart constructors are the common shapes, and :class:`~fuaran_py.schema.types.Box`
    is the whole of what the wire allows."""
    return t.UiNode(
        "overview-panel",
        t.Box(
            (
                node.bare(fuaran.sparkline("spark-1", source=binding.static([1.0, 2.0, 3.0, 2.0, 4.0]))),
                node.bare(fuaran.badge("badge-1", label="Beta", variant="Info")),
            ),
            t.GridTemplate(cols=2, gap=16),
            "Card",
            t.LiteralText("This month"),
        ),
    )


def _settings_panel() -> t.UiNode:
    """``composite-tabs-panels``'s second tab — the form whose ``onSubmit`` is the
    ``Action.Call`` that kept this fixture quarantined, over two controls that
    declare no handler (``on_change=False``)."""
    form = node.bare(
        fuaran.form(
            "preferences-form",
            submit_label="Save preferences",
            on_submit=action.call("/api/preferences"),
            fields=[
                t.FormField(
                    "displayName",
                    t.LiteralText("Display name"),
                    t.TextField(value=t.Static("Ada Lovelace"), on_change=False),
                    True,
                ),
                t.FormField(
                    "theme",
                    t.LiteralText("Theme"),
                    t.ChoiceField(
                        options=t.Static([t.SelectOption("Light", "light"), t.SelectOption("Dark", "dark")]),
                        value=t.Static("dark"),
                        on_change=False,
                    ),
                    True,
                ),
            ],
        )
    )
    return t.UiNode(
        "settings-panel",
        t.Box((form,), t.FlexLayout("Vertical", False, 12), "Card", t.LiteralText("Preferences")),
    )


def _selected(field: str, default: str) -> t.Selection:
    """``Binding.Selection`` on the master grid, projecting one row field. The
    ``default_value`` is what the detail pane reads BEFORE the first click, which
    is the whole of what ``master-detail-preselected`` pins."""
    return t.Selection("ticket-grid", default, field)


# Authored trees keyed by their corpus fixture id. Built lazily inside a function
# so the import-time module body stays readable.
def _authored() -> dict[str, t.UiNode]:
    from fuaran_py.ui import format

    metric_1 = node.bare(
        fuaran.metric(
            "metric-1",
            label="Revenue",
            value=1234.5,
            format=format.currency("GBP"),
            tone="Brand",
            icon="trending-up",
            subtext="vs last month",
            trend=0.07,
            trend_format=format.percent(1),
        )
    )
    markdown_1 = fuaran.markdown("markdown-1", "Updated hourly.")
    # WIRE_FORMAT.md §8.1 — NodeIds are unique within a tree, so a sample child
    # placed twice in one document needs a distinct id at each site.
    markdown_2 = fuaran.markdown("markdown-2", "Updated hourly.")
    spark_1 = fuaran.sparkline("spark-1", source=binding.static([1.0, 2.0, 3.0, 2.0, 4.0]))
    lvr_1 = fuaran.label_value_row(
        "lvr-1", label="Total", value=42, format=format.number(2), emphasis=True, help="Last 30 days"
    )

    return {
        # ── Display ──────────────────────────────────────────────────────────
        "metric-1": metric_1,
        "heading-1": fuaran.heading("heading-1", "Channel performance"),
        "markdown-1": markdown_1,
        "badge-1": fuaran.badge("badge-1", label="Beta", variant="Info"),
        "link-1": fuaran.link("link-1", href="/about", label="About us", rel="noopener", target="_blank"),
        "spark-1": spark_1,
        "skel-1": fuaran.skeleton("skel-1", 3),
        "lvr-1": lvr_1,
        "callout-1": node.bare(
            fuaran.callout(
                "callout-1",
                body="Live data is delayed.",
                tone="Warning",
                heading="Heads up",
                icon="alert",
                dismissable=True,
            )
        ),
        "progress-1": node.bare(fuaran.progress("progress-1", fraction=0.42, label="Loading...", tone="Brand")),
        # ── Layout ───────────────────────────────────────────────────────────
        "dash-empty": node.bare(fuaran.dashboard("dash-empty")),
        "stack-1": fuaran.stack("stack-1", children=[metric_1, markdown_1]),
        "glayout-1": fuaran.grid_layout("glayout-1", children=[metric_1], cols=12),
        # WIRE_FORMAT §3.6.7 — the column-fill mode. `gap` is otherwise
        # unreachable on the wire, so both fixtures are authored: without and
        # with it.
        "masonry-1": fuaran.masonry_layout("masonry-1", children=[metric_1, markdown_1], cols=3),
        "masonry-gap": fuaran.masonry_layout("masonry-gap", children=[metric_1, markdown_1], cols=4, gap=16),
        "split-1": fuaran.split_panel("split-1", children=[metric_1, markdown_1], weight=0.6),
        "card-1": node.bare(fuaran.card("card-1", children=[metric_1], heading="Insights")),
        "step-1": node.bare(fuaran.stepper("step-1", children=[markdown_1, markdown_2], active_step=1)),
        "summary-1": node.bare(fuaran.summary_list("summary-1", children=[lvr_1], heading="Stats")),
        "discl-1": node.bare(
            fuaran.disclosure(
                "discl-1",
                children=[markdown_1],
                heading="Additional entitlements",
                open=False,
                default_open=True,
            )
        ),
        "tabs-1": node.bare(fuaran.tabs("tabs-1", children=[metric_1], active_index=0)),
        "tabs-explicit-1": node.bare(
            fuaran.tabs(
                "tabs-explicit-1",
                children=[markdown_1, spark_1],
                active_index=1,
                active_tag=t.Static("overview"),
                tab_headers=[
                    t.TabHeader(label=t.LiteralText("Overview"), icon="overview-glyph"),
                    t.TabHeader(label=t.LiteralText("Detail"), disabled=t.Static(False)),
                ],
                tab_tags=["overview", "detail"],
            )
        ),
        # ── Input ────────────────────────────────────────────────────────────
        "btn-1": node.bare(
            fuaran.button(
                "btn-1",
                label="Refresh",
                icon="refresh",
                variant="Primary",
                disabled=binding.state("loading", False),
            )
        ),
        "select-1": node.bare(
            fuaran.select(
                "select-1",
                label="Region",
                source=binding.static([t.SelectOption("UK", "uk")]),
                value=binding.static("uk"),
                placeholder="Choose one",
                disabled=binding.state("selectBusy", False),
            )
        ),
        # ── Visualisation ────────────────────────────────────────────────────
        "table-1": fuaran.table(
            "table-1",
            headers=["Term", "Definition"],
            rows=[["MVU", "Model-View-Update"], ["DSL", "Domain-specific language"]],
        ),
        "chart-1": node.bare(
            fuaran.chart(
                "chart-1",
                # fuaran#665 — a rows feed is a typed Static payload: a list of
                # name→scalar cells, canonically key-sorted on the way out.
                source=binding.static(
                    [
                        {"month": "Jan", "revenue": 980, "cost": 420},
                        {"month": "Feb", "revenue": 1105, "cost": 390},
                    ]
                ),
                x_field="month",
                y_fields=["revenue", "cost"],
                kind="Line",
                title="Channel mix",
                stacked=True,
            )
        ),
        # ── Structural ───────────────────────────────────────────────────────
        "custom-1": fuaran.custom("custom-1", module_id="analytics", component_id="trend-card"),
        "boundary-1": fuaran.error_boundary(
            "boundary-1",
            child=fuaran.markdown("boundary-child", "Child body"),
            fallback=node.bare(
                fuaran.callout(
                    "boundary-fallback",
                    body="Fallback rendered",
                    tone="Warning",
                    heading="Couldn't render",
                )
            ),
        ),
        "frag-decl-1": fuaran.fragment_decl(
            "frag-decl-1", name="card-template", body=fuaran.markdown("frag-body", "Template body")
        ),
        "frag-ref-1": fuaran.fragment_ref("frag-ref-1", name="card-template"),
        # ── Style trait ──────────────────────────────────────────────────────
        "style-role-voice-1": node.with_voice(
            "Display", node.with_role("Data", fuaran.markdown("style-role-voice-1", "Q3 revenue"))
        ),
        # ── Full-parity surface (the Phase 278 → full-coverage expansion) ─────
        "btn-copy-link": node.bare(
            fuaran.button(
                "btn-copy-link",
                label="Copy share link",
                variant="Secondary",
                on_click=action.chain(
                    [action.write_to_clipboard("https://example.com/share/abc123"), action.dispatch()]
                ),
            )
        ),
        "btn-read-workbook": node.bare(
            fuaran.button(
                "btn-read-workbook",
                label="Load workbook",
                variant="Primary",
                on_click=action.read_file_body("workbook-upload:0", "Base64"),
            )
        ),
        "custom-bounded-1": fuaran.custom(
            "custom-bounded-1",
            module_id="deal-flow",
            component_id="QualityRing",
            content_hash=t.ContentHash("SHA256", "abc123def456", "StrictReplay"),
            exposed_node_ids=["quality-ring-segment-1", "quality-ring-segment-2"],
        ),
        "custom-bounded-advisory": fuaran.custom(
            "custom-bounded-advisory",
            module_id="deal-flow",
            component_id="TrendCard",
            content_hash=t.ContentHash("SHA256", "fedcba654321", "AdvisoryWarning"),
        ),
        "grid-1": node.bare(
            fuaran.grid(
                "grid-1",
                source=binding.static(
                    [{"channel": "Direct", "revenue": 1200}, {"channel": "Referral", "revenue": 830}]
                ),
                columns=[t.Column(label="Channel")],
            )
        ),
        "filters-1": fuaran.filters(
            "filters-1",
            items=[
                t.FilterSpec("q", t.LiteralText("Search"), t.TextFilter(t.Static(""))),
                t.FilterSpec(
                    "tier",
                    t.LiteralText("Tier"),
                    t.ChoiceFilter(t.Static([t.SelectOption("All", "all")]), t.Static("all")),
                ),
            ],
        ),
        "filters-segmented": fuaran.filters(
            "filters-segmented",
            items=[
                t.FilterSpec(
                    "view",
                    t.LiteralText("View"),
                    t.SegmentedFilter(
                        t.Static([t.SelectOption("Table", "table"), t.SelectOption("Chart", "chart")]),
                        t.Static("table"),
                        "Horizontal",
                    ),
                )
            ],
        ),
        "form-1": node.bare(
            fuaran.form(
                "form-1",
                disabled=binding.state("formBusy", False),
                submit_label="Save",
                fields=[
                    t.FormField(
                        "name", t.LiteralText("Name"), t.TextField(t.Static("")), True, t.LiteralText("Full legal name")
                    ),
                    t.FormField("age", t.LiteralText("Age"), t.NumberField(t.Static(0)), False),
                    t.FormField("agree", t.LiteralText("I agree"), t.CheckboxField(t.Static(False)), True),
                    t.FormField(
                        "tier",
                        t.LiteralText("Tier"),
                        t.ChoiceField(
                            t.Static([t.SelectOption("Basic", "basic"), t.SelectOption("Pro", "pro")]),
                            t.Static("basic"),
                        ),
                        False,
                    ),
                    t.FormField("notes", t.LiteralText("Notes"), t.TextAreaField(t.Static(""), 5), False),
                ],
            )
        ),
        "form-ranged": node.bare(
            fuaran.form(
                "form-ranged",
                submit_label="Save",
                fields=[
                    t.FormField("year", t.LiteralText("Year"), t.RangedNumber(t.Static(2024), 1979, 2028, 1), True),
                    t.FormField("years", t.LiteralText("Years contributed"), t.RangedNumber(t.Static(10), 0), False),
                    t.FormField("amount", t.LiteralText("Amount"), t.RangedNumber(t.Static(100)), False),
                ],
            )
        ),
        "form-segmented": node.bare(
            fuaran.form(
                "form-segmented",
                submit_label="Save",
                fields=[
                    t.FormField(
                        "metric",
                        t.LiteralText("Metric"),
                        t.SegmentedChoice(
                            t.Static(
                                [
                                    t.SelectOption("Effective", "effective"),
                                    t.SelectOption("Marginal", "marginal"),
                                    t.SelectOption("Take-home", "takeHome"),
                                ]
                            ),
                            t.Static("effective"),
                            "Horizontal",
                        ),
                        False,
                    ),
                    t.FormField(
                        "tier",
                        t.LiteralText("Tier"),
                        t.SegmentedChoice(
                            t.Static([t.SelectOption("Low", "low"), t.SelectOption("High", "high")]),
                            t.Static(None),
                            "Vertical",
                        ),
                        True,
                    ),
                ],
            )
        ),
        "form-local-1": node.bare(
            fuaran.form(
                "form-local-1",
                submit_label="Save",
                fields=[
                    t.FormField(
                        "salary-input",
                        t.LiteralText("Salary"),
                        t.TextField(t.Local(t.State("salary", ""), t.OnBlur())),
                        False,
                    )
                ],
            )
        ),
        "upload-1": node.bare(
            fuaran.file_upload(
                "upload-1",
                label="Upload CSV",
                accept=[".csv", "text/csv"],
                disabled=binding.state("uploadBusy", False),
            )
        ),
        "form-local-debounce": node.bare(
            fuaran.form(
                "form-local-debounce",
                submit_label="Save",
                fields=[
                    t.FormField(
                        "email-input",
                        t.LiteralText("Email"),
                        t.TextField(t.Local(t.Static("draft@example.com"), t.OnDebounce(250))),
                        True,
                    )
                ],
            )
        ),
        "frag-decl-param": fuaran.fragment_decl(
            "frag-decl-param",
            name="stat-card",
            body=fuaran.markdown("param-body", "Parameterised body"),
            effect=t.EffectClass("ReadsHost", "Clock"),
            holes=[
                t.ValueHole("title", t.StringLen(1, 40), t.ScalarStr("Untitled")),
                t.ValueHole("count", t.IntRange(0, 100)),
                t.SlotHole("content", "Display"),
                t.RepeatHole("rows", t.IntRange(1, 12)),
            ],
        ),
        "frag-ref-args": fuaran.fragment_ref(
            "frag-ref-args",
            name="stat-card",
            args={
                "content": t.SlotArg(fuaran.markdown("slot-tree", "Bound slot")),
                "count": t.ScalarInt(7),
            },
        ),
        # fuaran#1110 — the tracks and the transcript, authored rather than
        # decoded. The codec fixtures prove decode→encode byte parity; these
        # three prove the AUTHORING surface reaches the same bytes, which is the
        # half `to_wire` owns: the omit-at-empty on `tracks`, the omit-at-false
        # on a track's `default`, and the ordinary optional on `transcript`.
        "media-video-captions-1": fuaran.video(
            "media-video-captions-1",
            src="/walkthrough.mp4",
            label="Studio walkthrough",
            tracks=[track.captions(src="/walkthrough.en.vtt", src_lang="en", label="English captions", default=True)],
        ),
        # Authored in an order no sort produces (a `gd` subtitles track ahead of
        # two `en` captions ones), so a codec that sorted the array would fail
        # here rather than pass by accident — the `srcSet` rule's opposite.
        "media-video-tracks-2": fuaran.video(
            "media-video-tracks-2",
            src="/restoration-2.mp4",
            label="Harbour restoration, part two",
            tracks=[
                track.subtitles(src="/restoration-2.gd.vtt", src_lang="gd", label="Gàidhlig"),
                track.captions(src="/restoration-2.en.vtt", src_lang="en", label="English captions", default=True),
                track.captions(
                    src="/restoration-2.en-verbose.vtt",
                    src_lang="en",
                    label="English captions (verbose)",
                    default=True,
                ),
            ],
        ),
        "media-audio-transcript-1": fuaran.audio(
            "media-audio-transcript-1",
            src="/commentary.mp3",
            label="Curator's commentary",
            transcript="The harbour was rebuilt twice: once after the storm of 1908, and again in 1953.",
        ),
        # fuaran#1535 — a Switch case selects on a string `match` XOR a `when`
        # predicate. Both spellings in one authored tree, so a constructor that
        # dropped either would fail here rather than pass on the half it kept.
        "switch-predicate": fuaran.switch(
            "switch-predicate",
            state_key="view",
            cases=[
                (
                    binding.state("cart.empty", None),
                    fuaran.markdown("switch-predicate-empty", "Your basket is empty"),
                ),
                (_expr_predicate(), fuaran.markdown("switch-predicate-many", "Plenty in your basket")),
                ("summary", fuaran.markdown("switch-predicate-summary", "Summary view")),
            ],
            default=fuaran.markdown("switch-predicate-default", "A few things in your basket"),
        ),
        # The degenerate shape: every case is a predicate, so the selector is
        # never read and the compact `stateKey` is the empty string.
        "switch-predicate-only": fuaran.switch(
            "switch-predicate-only",
            state_key="",
            cases=[
                (
                    binding.state("form.valid", None),
                    fuaran.markdown("switch-predicate-only-ready", "Ready to send"),
                )
            ],
            default=fuaran.markdown("switch-predicate-only-default", "Fill in the form to continue"),
        ),
        # WIRE_FORMAT §3.3.3 — `Binding.Local`'s DECLARATIVE half: the flush
        # writes a State key (`commitTo`) through a codec with a total inverse,
        # instead of calling a host closure (`onCommit`). The two are mutually
        # exclusive on the wire, so the authoring surface has to be able to spell
        # this one without emitting the other.
        "form-local-declared": node.bare(
            fuaran.form(
                "form-local-declared",
                submit_label="Save",
                fields=[
                    t.FormField(
                        "unit-price",
                        t.LiteralText("Unit price"),
                        t.NumberField(
                            binding.local(
                                binding.state("order.unitPrice", 0),
                                t.OnBlur(),
                                commit_to="order.unitPrice",
                                codec=format.number(2),
                            ),
                            on_change=False,
                        ),
                        False,
                    )
                ],
            )
        ),
        # ── Phase 1576 — the handler and the value are OPTIONAL on every control ─
        #
        # Nineteen corpus fixtures the authoring surface could not reach while
        # `TextField` / `CheckboxField` / `ChoiceField` / `DateField` /
        # `DateRangeField` / `ComboboxField` / `TokensField` / `RatingField` /
        # `ColorField` / `Tabs` / `Modal` / `Disclosure` / `Select` hard-coded a
        # handler or a value. Each is authored here in the spelling the fixture
        # carries, so the flags are pinned to bytes rather than to intent: an
        # `on_change=False` that stopped omitting would fail HERE, and an
        # `on_change` default that stopped emitting would fail on the fixtures
        # above (`tabs-1`, `step-1`, `form-1`), which is the other half.
        "form-declarative": node.bare(
            fuaran.form(
                "form-declarative",
                submit_label="Save",
                fields=[
                    t.FormField(
                        "profile-name",
                        t.LiteralText("Name"),
                        t.TextField(binding.state("profileName", ""), on_change=False),
                        True,
                    ),
                    t.FormField(
                        "profile-age",
                        t.LiteralText("Age"),
                        t.NumberField(binding.state("profileAge", 0), on_change=False),
                        False,
                    ),
                    t.FormField(
                        "profile-agree",
                        t.LiteralText("I agree"),
                        t.CheckboxField(binding.state("profileAgree", False), on_toggle=False),
                        True,
                    ),
                    t.FormField(
                        "profile-tier",
                        t.LiteralText("Tier"),
                        t.ChoiceField(
                            binding.static([t.SelectOption("Basic", "basic"), t.SelectOption("Pro", "pro")]),
                            t.State("profileTier", None),
                            on_change=False,
                        ),
                        False,
                    ),
                ],
            )
        ),
        # The canonical MINIMAL control, four times over: `{"$type":"Text"}` is a
        # bound control, not an empty one — the decoder synthesises the form
        # field's own `State(id, <placeholder>)` for it.
        "form-declarative-minimal": node.bare(
            fuaran.form(
                "form-declarative-minimal",
                submit_label="Book",
                fields=[
                    t.FormField("guest-name", t.LiteralText("Name"), t.TextField(on_change=False), True),
                    t.FormField("party-size", t.LiteralText("Party size"), t.NumberField(on_change=False), False),
                    t.FormField(
                        "seating",
                        t.LiteralText("Seating"),
                        t.ChoiceField(
                            binding.static([t.SelectOption("Indoor", "indoor"), t.SelectOption("Terrace", "terrace")]),
                            on_change=False,
                        ),
                        False,
                    ),
                    t.FormField("visit-date", t.LiteralText("Date"), t.DateField(on_change=False), True),
                ],
            )
        ),
        "form-field-rules": node.bare(
            fuaran.form(
                "form-field-rules",
                submit_label="Save",
                fields=[
                    t.FormField(
                        "work-email",
                        t.LiteralText("Work email"),
                        t.TextField(on_change=False),
                        True,
                        rule=rule.format("email"),
                    ),
                    t.FormField(
                        "postcode",
                        t.LiteralText("Postcode"),
                        t.TextField(on_change=False),
                        True,
                        rule=rule.pattern(
                            "[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}", "Enter a UK postcode, e.g. EH1 1YZ"
                        ),
                    ),
                    t.FormField(
                        "username",
                        t.LiteralText("Username"),
                        t.TextField(on_change=False),
                        True,
                        rule=rule.length(3, 24),
                    ),
                    t.FormField("hire-start-date", t.LiteralText("Start date"), t.DateField(on_change=False), True),
                    t.FormField(
                        "hire-end-date",
                        t.LiteralText("End date"),
                        t.DateField(on_change=False),
                        True,
                        rule=rule.compare(
                            t.State("hire-start-date", None), "gte", "End date must be on or after the start date"
                        ),
                    ),
                ],
            )
        ),
        "form-toggle": node.bare(
            fuaran.form(
                "form-toggle",
                submit_label="Save",
                fields=[
                    t.FormField("irrigation-running", t.LiteralText("Irrigation"), t.ToggleField(), False),
                    t.FormField(
                        "accept-terms", t.LiteralText("I accept the terms"), t.CheckboxField(on_toggle=False), True
                    ),
                ],
            )
        ),
        # Three DateRanges, and the middle one is the discriminator: two carry the
        # handler and one omits it, so a record that emitted `onChange`
        # unconditionally and one that omitted it unconditionally both fail here.
        "form-date-range": node.bare(
            fuaran.form(
                "form-date-range",
                submit_label="Book",
                fields=[
                    t.FormField(
                        "stay",
                        t.LiteralText("Stay"),
                        t.DateRangeField(("2026-03-01", "2026-03-08"), min="2026-01-01", max="2026-12-31"),
                        True,
                    ),
                    t.FormField(
                        "shift",
                        t.LiteralText("Shift"),
                        t.DateRangeField(
                            t.State("shift", Obj(None, {"from": "08:00", "to": "17:00"})),
                            "Time",
                            step=900,
                            on_change=False,
                        ),
                        False,
                    ),
                    t.FormField(
                        "window",
                        t.LiteralText("Window"),
                        t.DateRangeField(("2026-03-01T09:00", "2026-03-01T17:00"), "DateTime"),
                        False,
                    ),
                ],
            )
        ),
        # `Range` — the PAIR-valued numeric chip, and the one control in the
        # schema's `FormFieldKind` list that had no record here at all.
        "filters-declarative": fuaran.filters(
            "filters-declarative",
            items=[
                t.FilterSpec("q", t.LiteralText("Search"), t.TextFilter(on_change=False)),
                t.FilterSpec(
                    "tier",
                    t.LiteralText("Tier"),
                    t.ChoiceFilter(binding.static([t.SelectOption("All", "all")]), on_change=False),
                ),
                t.FilterSpec("age", t.LiteralText("Age"), t.RangeFilter((0, 100), on_change=False)),
            ],
        ),
        "filters-date-range": fuaran.filters(
            "filters-date-range",
            items=[t.FilterSpec("stay", t.LiteralText("Stay"), t.DateRangeField(on_change=False))],
        ),
        "filters-rating-colour": fuaran.filters(
            "filters-rating-colour",
            items=[
                t.FilterSpec("stars", t.LiteralText("At least"), t.RatingField(5, on_change=False)),
                t.FilterSpec("swatch", t.LiteralText("Colour"), t.ColorField(on_change=False)),
            ],
        ),
        "filters-tokens": fuaran.filters(
            "filters-tokens",
            items=[
                t.FilterSpec(
                    "labels",
                    t.LiteralText("Labels"),
                    t.TokensField(
                        allow_free_text=False,
                        suggestions=binding.static(
                            [t.SelectOption("Urgent", "urgent"), t.SelectOption("Blocked", "blocked")]
                        ),
                        on_change=False,
                    ),
                )
            ],
        ),
        "form-combobox-freetext": node.bare(
            fuaran.form(
                "form-combobox-freetext",
                submit_label="Save",
                fields=[
                    t.FormField(
                        "tag",
                        t.LiteralText("Tag"),
                        t.ComboboxField(
                            binding.static([t.SelectOption("Urgent", "urgent"), t.SelectOption("Blocked", "blocked")]),
                            binding.static("needs-a-second-look"),
                            allow_free_text=True,
                            on_change=False,
                        ),
                        False,
                    )
                ],
            )
        ),
        "form-rating-halves": node.bare(
            fuaran.form(
                "form-rating-halves",
                submit_label="Save",
                fields=[
                    t.FormField(
                        "stars",
                        t.LiteralText("Your rating"),
                        t.RatingField(5, binding.static(3.5), allow_half=True, on_change=False),
                        False,
                    ),
                    t.FormField("average", t.LiteralText("Average rating"), t.RatingField(10, on_change=False), False),
                ],
            )
        ),
        "form-tokens-freetext": node.bare(
            fuaran.form(
                "form-tokens-freetext",
                submit_label="Save",
                fields=[t.FormField("labels", t.LiteralText("Labels"), t.TokensField(on_change=False), False)],
            )
        ),
        # `on_dismiss=None` is the declaration of NO handler — distinct from the
        # omitted argument, which keeps the no-op `Chain` `modal-1` carries.
        "popover-open-1": node.bare(
            fuaran.modal(
                "popover-open-1",
                children=[fuaran.markdown("markdown-1", "Updated hourly.")],
                open=True,
                dismissable=True,
                on_dismiss=None,
                modality="Popover",
                anchor="help-trigger",
            )
        ),
        "popover-anchored-1": node.bare(
            fuaran.modal(
                "popover-anchored-1",
                children=[fuaran.markdown("markdown-1", "Updated hourly.")],
                open=binding.state("swatchOpen", False),
                dismissable=True,
                on_dismiss=None,
                heading="Choose a colour",
                modality="Popover",
                anchor="swatch",
            )
        ),
        "frag-stdlib-filter-bar": fuaran.fragment_decl(
            "frag-stdlib-filter-bar",
            name="filter-bar",
            body=fuaran.filters(
                "filter-bar",
                items=[
                    t.FilterSpec(
                        "search", t.Bound(binding.state("searchLabel", "Search")), t.TextFilter(on_change=False)
                    ),
                    t.FilterSpec(
                        "status",
                        t.Bound(binding.state("statusLabel", "Status")),
                        t.ChoiceFilter(
                            binding.static([t.SelectOption("Open", "open"), t.SelectOption("Closed", "closed")]),
                            on_change=False,
                        ),
                    ),
                ],
            ),
            effect=t.EffectClass("ReadsHost", "Deterministic"),
            holes=[
                t.ValueHole("searchLabel", t.StringLen(1, 32), t.ScalarStr("Search")),
                t.ValueHole("statusLabel", t.StringLen(1, 32), t.ScalarStr("Status")),
            ],
        ),
        # The declarative / closure PAIR, and reading them together is the point:
        # every handler slot this phase touched appears absent in one and present
        # in the other, over the same four controls.
        "controls-declarative": fuaran.stack(
            "controls-declarative",
            children=[
                node.bare(
                    fuaran.tabs(
                        "decl-tabs",
                        children=[fuaran.markdown("markdown-1", "Updated hourly.")],
                        active_index=binding.state("activePane", 0),
                        on_select=False,
                    )
                ),
                node.bare(
                    fuaran.modal(
                        "decl-modal",
                        children=[fuaran.markdown("markdown-2", "Updated hourly.")],
                        open=binding.state("modalOpen", False),
                        dismissable=True,
                        on_dismiss=None,
                        heading="Confirm",
                    )
                ),
                node.bare(
                    fuaran.disclosure(
                        "decl-disclosure",
                        children=[fuaran.markdown("markdown-3", "Updated hourly.")],
                        heading="Advanced",
                        open=binding.state("advancedOpen", False),
                    )
                ),
                node.bare(
                    fuaran.select(
                        "decl-select",
                        label="Region",
                        source=binding.static([t.SelectOption("UK", "uk")]),
                        value=t.State("region", None),
                        placeholder="Choose one",
                        on_change=False,
                    )
                ),
            ],
        ),
        "controls-closure": fuaran.stack(
            "controls-closure",
            children=[
                node.bare(
                    fuaran.tabs(
                        "closure-tabs",
                        children=[
                            fuaran.markdown("markdown-1", "Updated hourly."),
                            fuaran.sparkline("spark-1", source=binding.static([1.0, 2.0, 3.0, 2.0, 4.0])),
                        ],
                        active_index=0,
                        active_tag=binding.static("overview"),
                        tab_tags=["overview", "detail"],
                        on_select_tag=True,
                    )
                ),
                node.bare(
                    fuaran.disclosure(
                        "closure-disclosure",
                        children=[fuaran.markdown("markdown-2", "Updated hourly.")],
                        heading="Advanced",
                        open=False,
                        on_toggle=True,
                    )
                ),
                node.bare(
                    fuaran.select(
                        "closure-multiselect",
                        label="Tags",
                        source=binding.static([t.SelectOption("Red", "red")]),
                        value=t.Static(None),
                        multiple=True,
                        values=binding.static(["red"]),
                        on_change_multi=True,
                    )
                ),
            ],
        ),
        "multiselect-chip-list-param": node.bare(
            fuaran.dashboard(
                "multiselect-chip-list-param",
                heading="Spend by department",
                children=[
                    node.bare(
                        fuaran.select(
                            "dept-chip",
                            label="Departments",
                            source=binding.static(
                                [
                                    t.SelectOption("Engineering", "eng"),
                                    t.SelectOption("Sales", "sales"),
                                    t.SelectOption("Operations", "ops"),
                                ]
                            ),
                            value=t.Static(None),
                            multiple=True,
                            values=binding.filter("depts"),
                            on_change=False,
                        )
                    ),
                    node.bare(
                        fuaran.grid(
                            "dept-grid",
                            source=_dept_frame().to_transform_binding(),
                            columns=[
                                t.Column(label="Department", field_name="dept"),
                                t.Column(label="Spend", field_name="amount"),
                            ],
                            row_key_field="dept",
                        )
                    ),
                ],
            )
        ),
        "filterable-static-dashboard": node.bare(
            fuaran.dashboard(
                "filterable-static-dashboard",
                heading="Content performance",
                children=[
                    fuaran.filters(
                        "content-filters",
                        items=[
                            t.FilterSpec(
                                "region",
                                t.LiteralText("Region"),
                                t.ChoiceFilter(
                                    binding.static(
                                        [t.SelectOption("EMEA", "emea"), t.SelectOption("Americas", "amer")]
                                    ),
                                    on_change=False,
                                ),
                            ),
                            t.FilterSpec(
                                "genre",
                                t.LiteralText("Genre"),
                                t.ChoiceFilter(
                                    binding.static(
                                        [t.SelectOption("Drama", "drama"), t.SelectOption("Documentary", "docs")]
                                    ),
                                    on_change=False,
                                ),
                            ),
                        ],
                    ),
                    node.bare(
                        fuaran.chart(
                            "retention-chart",
                            source=_content_frame().to_transform_binding(),
                            x_field="month",
                            y_fields=["retention"],
                            kind="Line",
                            title="Retention",
                        )
                    ),
                    node.bare(
                        fuaran.grid(
                            "episode-grid",
                            source=_content_frame().to_transform_binding(),
                            columns=[
                                t.Column(label="Month", field_name="month"),
                                t.Column(label="Retention", field_name="retention"),
                            ],
                            row_key_field="month",
                        )
                    ),
                ],
            )
        ),
        "format-bindings": fuaran.stack(
            "format-bindings",
            children=[
                fuaran.markdown(
                    "fmt-number",
                    t.Bound(binding.format(t.Static(1234.5), t.FmtNumber(2), t.Explicit("en-US"))),
                ),
                fuaran.markdown(
                    "fmt-currency",
                    t.Bound(binding.format(t.Static(1234.5), t.FmtCurrency("GBP"), t.Explicit("en-GB"))),
                ),
                fuaran.markdown(
                    "fmt-percent",
                    t.Bound(binding.format(t.Static(0.42), t.FmtPercent(), t.Ambient())),
                ),
                fuaran.markdown(
                    "fmt-date",
                    t.Bound(binding.format(t.Static(1700000000), t.FmtDate("Medium"), t.Explicit("fr-FR"))),
                ),
                fuaran.markdown(
                    "fmt-relative",
                    t.Bound(binding.format(t.Static(-3), t.FmtRelativeTime("Day"), t.Explicit("en-US"))),
                ),
            ],
        ),
        # ── Phase 1577 — the field-widening wave ─────────────────────────────
        #
        # Seventeen fixtures the typed surface could not reach because the record
        # was narrower than the wire: the grid's declarative sort / page / edit /
        # reorder slots, the chart's eight, the node-level tooltip, the link's
        # protection and the static table's sort pair.
        "grid-bound-sort": node.bare(
            fuaran.grid(
                "grid-bound-sort",
                source=t.State("ledger", _LEDGER_ROWS),
                columns=[
                    t.Column(label="Month", field_name="month"),
                    t.Column(label="Revenue", field_name="revenue"),
                    # The free-text note column opts OUT of a sort the grid offers.
                    t.Column(label="Note", field_name="note", sortable=False),
                ],
                row_key_field="month",
                sort_state_key="ledger-sort",
                default_sort=t.DefaultSort(1, "desc"),
            )
        ),
        "grid-sort-state-key": node.bare(
            fuaran.grid(
                "grid-sort-state-key",
                source=t.State("inventory", _LEDGER_ROWS),
                columns=[t.Column(label="Month", field_name="month"), _numeric_col("Revenue", "revenue")],
                row_key_field="month",
                sort_state_key="inventory-sort",
            )
        ),
        "grid-paged": node.bare(
            fuaran.grid(
                "grid-paged",
                source=t.State("members", _LEDGER_ROWS),
                columns=[t.Column(label="Month", field_name="month"), _numeric_col("Revenue", "revenue")],
                row_key_field="month",
                page_size=20,
                page_state_key="members-page",
            )
        ),
        "grid-paged-sorted": node.bare(
            fuaran.grid(
                "grid-paged-sorted",
                source=t.State("ledger", _LEDGER_ROWS),
                columns=[t.Column(label="Month", field_name="month"), _numeric_col("Revenue", "revenue")],
                row_key_field="month",
                page_size=10,
                page_state_key="ledger-page",
                sort_state_key="ledger-sort",
            )
        ),
        "grid-reorderable": node.bare(
            fuaran.grid(
                "grid-reorderable",
                source=t.State(
                    "sprint-order",
                    [{"rank": 1, "task": "Design"}, {"rank": 2, "task": "Build"}, {"rank": 3, "task": "Verify"}],
                ),
                columns=[t.Column(label="Task", field_name="task"), _numeric_col("Rank", "rank")],
                row_key_field="task",
                edit_state_key="sprint-order",
                reorderable=True,
            )
        ),
        "transfer-board": t.UiNode(
            "transfer-board",
            t.Box(
                children=(
                    _board_column(
                        "todo",
                        "board-todo",
                        [{"card": "Draft the brief"}, {"card": "Size the work"}],
                        releases=True,
                    ),
                    _board_column("doing", "board-doing", [{"card": "Write the walk"}], releases=True),
                    _board_column("archive", "board-archive", [], releases=False),
                ),
                layout=t.AutoLayout(),
                role="Group",
                heading=t.LiteralText("Sprint board"),
            ),
        ),
        "table-sortable-1": node.bare(
            fuaran.table(
                "table-sortable-1",
                headers=["Region", "Revenue"],
                rows=[["North", "1200"], ["South", "980"]],
                sortable=True,
                default_sort=t.DefaultSort(1, "desc"),
            )
        ),
        "link-protected-1": node.bare(
            fuaran.link(
                "link-protected-1",
                href="mailto:contact@example.com",
                label="Email us",
                protection="email",
            )
        ),
        "chart-axis-titles": node.bare(
            fuaran.chart(
                "chart-axis-titles",
                source=t.Static([{"quarter": "Q1", "revenue": 12500000}, {"quarter": "Q2", "revenue": 15200000}]),
                x_field="quarter",
                y_fields=["revenue"],
                kind="Bar",
                title="Revenue by quarter",
                subtitle="Millions of £",
                x_title="Quarter",
                y_title="Revenue",
                value_format=t.FmtCurrency("GBP"),
            )
        ),
        "chart-value-format": node.bare(
            fuaran.chart(
                "chart-value-format",
                source=t.Static([{"month": "Jan", "revenue": 12500000}, {"month": "Feb", "revenue": 15200000}]),
                x_field="month",
                y_fields=["revenue"],
                kind="Bar",
                title="Revenue",
                value_format=t.FmtCurrency("GBP"),
            )
        ),
        "chart-data-labels": node.bare(
            fuaran.chart(
                "chart-data-labels",
                source=t.Static([{"quarter": "Q1", "revenue": 120}, {"quarter": "Q2", "revenue": 150}]),
                x_field="quarter",
                y_fields=["revenue"],
                kind="Bar",
                title="Revenue by quarter",
                data_labels="Ends",
            )
        ),
        "chart-legend-position": node.bare(
            fuaran.chart(
                "chart-legend-position",
                source=t.Static(
                    [
                        {"region": "North", "sales": 80, "target": 100},
                        {"region": "South", "sales": 130, "target": 110},
                    ]
                ),
                x_field="region",
                y_fields=["sales", "target"],
                kind="Bar",
                title="Sales vs target",
                legend_position="Bottom",
            )
        ),
        "chart-temporal-x": node.bare(
            fuaran.chart(
                "chart-temporal-x",
                source=t.Static(
                    [
                        {"day": "2026-01-05", "sessions": 1200},
                        {"day": "2026-01-12", "sessions": 1450},
                        {"day": "2026-01-19", "sessions": 1310},
                        {"day": "2026-01-26", "sessions": 1580},
                    ]
                ),
                x_field="day",
                y_fields=["sessions"],
                kind="Line",
                title="Sessions by week",
                x_scale="Temporal",
            )
        ),
        "chart-annotations": node.bare(
            fuaran.chart(
                "chart-annotations",
                source=t.Static(_QUARTER_ROWS),
                x_field="quarter",
                y_fields=["revenue"],
                kind="Bar",
                title="Revenue by quarter",
                annotations=[t.ReferenceLine(140, "Target"), t.ReferenceLine(0)],
            )
        ),
        "chart-annotation-bands": node.bare(
            fuaran.dashboard(
                "chart-annotation-bands",
                children=[
                    node.bare(
                        fuaran.chart(
                            "bands-value",
                            source=t.Static(
                                [
                                    {"latencyMs": 180, "week": "W1"},
                                    {"latencyMs": 240, "week": "W2"},
                                    {"latencyMs": 210, "week": "W3"},
                                ]
                            ),
                            x_field="week",
                            y_fields=["latencyMs"],
                            kind="Line",
                            title="p95 latency",
                            annotations=[
                                t.RangeBand(t.ValueRange(200, 260), "Tolerance"),
                                t.RangeBand(t.ValueRange(0, 100)),
                            ],
                        )
                    ),
                    node.bare(
                        fuaran.chart(
                            "bands-category",
                            source=t.Static(_QUARTER_ROWS),
                            x_field="quarter",
                            y_fields=["revenue"],
                            kind="Bar",
                            title="Revenue by quarter",
                            annotations=[
                                t.RangeBand(t.XRange(t.AnnotationCategory("Q2"), t.AnnotationCategory("Q3")), "Freeze")
                            ],
                        )
                    ),
                    node.bare(
                        fuaran.chart(
                            "bands-temporal",
                            source=t.Static(_DAY_ROWS),
                            x_field="day",
                            y_fields=["sessions"],
                            kind="Line",
                            title="Sessions by day",
                            x_scale="Temporal",
                            annotations=[
                                t.RangeBand(
                                    t.XRange(t.AnnotationDate("2026-01-20"), t.AnnotationDate("2026-02-20")),
                                    "Incident",
                                )
                            ],
                        )
                    ),
                ],
            )
        ),
        "chart-annotation-events": node.bare(
            fuaran.dashboard(
                "chart-annotation-events",
                children=[
                    node.bare(
                        fuaran.chart(
                            "events-band",
                            source=t.Static(_QUARTER_ROWS),
                            x_field="quarter",
                            y_fields=["revenue"],
                            kind="Bar",
                            title="Revenue by quarter",
                            annotations=[t.EventMarker(t.AnnotationCategory("Q3"), "Repricing")],
                        )
                    ),
                    node.bare(
                        fuaran.chart(
                            "events-temporal",
                            source=t.Static(_DAY_ROWS),
                            x_field="day",
                            y_fields=["sessions"],
                            kind="Line",
                            title="Sessions by day",
                            x_scale="Temporal",
                            annotations=[
                                t.EventMarker(t.AnnotationDate("2026-02-14"), "Launch"),
                                t.EventMarker(t.AnnotationDate("2026-03-01")),
                            ],
                        )
                    ),
                ],
            )
        ),
        "tooltip-button-1": node.with_tooltip(
            "Re-reads every document; takes about a minute on this corpus.",
            node.bare(
                fuaran.button(
                    "tooltip-button-1",
                    label="Rebuild index",
                    on_click=action.notify("rebuild", {}),
                    variant="Secondary",
                )
            ),
        ),
        "tooltip-icon-button-1": fuaran.stack(
            "tooltip-icon-button-1",
            orientation="Horizontal",
            children=[
                node.with_tooltip(
                    "Exports the rows currently shown, not the whole table.",
                    fuaran.button(
                        "tooltip-icon-button-control",
                        label="",
                        on_click=action.notify("export", {}),
                        variant="Tertiary",
                        icon="download",
                    ).replace(
                        # The tooltip is the HINT; the accessible NAME is the ARIA
                        # label beside it. An icon-only control whose only name is
                        # a tooltip has no name.
                        accessibility=t.Accessibility(
                            label=t.Static("Download CSV"), described_by="tooltip-icon-button-note"
                        )
                    ),
                ),
                fuaran.markdown("tooltip-icon-button-note", "Updated hourly."),
            ],
        ),
        # ── Phase 1579 — Drawing (5) ─────────────────────────────────────────
        "drawing-1": fuaran.drawing(
            "drawing-1",
            view_box=t.ViewBox(0, 0, 200, 100),
            style=_mark_style("#ffffff", "#000000"),
            title="Quarterly revenue chart",
            description="A bar and line chart of revenue by quarter.",
            shapes=[
                t.Rectangle(10, 10, 80, 40, corner_radius=4, style=_mark_style("#3366cc", "#102040")),
                t.Line(0, 0, 200, 100),
                t.Polyline((t.DrawPoint(0, 0), t.DrawPoint(10, 20), t.DrawPoint(20, 5))),
                t.Polygon(
                    (t.DrawPoint(100, 10), t.DrawPoint(120, 30), t.DrawPoint(90, 40)),
                    style=_mark_style("#cc6633", "#402010"),
                ),
                t.Curve(
                    (
                        t.MoveTo(t.DrawPoint(0, 0)),
                        t.LineTo(t.DrawPoint(10, 10)),
                        t.CubicTo(t.DrawPoint(20, 0), t.DrawPoint(30, 20), t.DrawPoint(40, 10)),
                        t.QuadraticTo(t.DrawPoint(50, 0), t.DrawPoint(60, 10)),
                        t.Close(),
                    )
                ),
                t.Circle(150, 50, 20, style=_mark_style("#33aa55", "#0a2010")),
                t.Ellipse(50, 80, 30, 15),
                t.Label(100, 90, t.LiteralText("Revenue"), style=_label_style()),
                t.Group((t.Circle(5, 5, 2), t.Line(0, 0, 10, 10)), style=_mark_style("#999999", "#333333")),
            ],
        ),
        "drawing-empty": fuaran.drawing("drawing-empty", view_box=t.ViewBox(0, 0, 100, 100)),
        "drawing-nonfinite-sentinels": fuaran.drawing(
            "drawing-nonfinite-sentinels",
            # The §7 sentinels reach the typed float slots as ordinary Python
            # non-finites; the canonical encoder writes the quoted tokens.
            view_box=t.ViewBox(float("-inf"), float("nan"), float("inf"), 120),
            title="Non-finite sentinels at typed float slots",
            shapes=[t.Circle(float("nan"), 50, 20)],
        ),
        "drawing-rotated-labels": fuaran.drawing(
            "drawing-rotated-labels",
            view_box=t.ViewBox(0, 0, 200, 120),
            title="Rotated axis labels",
            shapes=[
                t.Label(30, 100, t.LiteralText("Q1 2026"), style=_label_style(rotation=-30)),
                t.Label(70, 100, t.LiteralText("Q2 2026"), style=_label_style(rotation=-90, anchor="End")),
                t.Label(8, 60, t.LiteralText("Revenue"), style=_label_style(rotation=90)),
                t.Label(110, 100, t.LiteralText("Fractional"), style=_label_style(rotation=12.34, anchor="Start")),
                t.Label(150, 100, t.LiteralText("Explicit zero"), style=_label_style(rotation=0)),
                t.Label(180, 100, t.LiteralText("Hairline"), style=_label_style(rotation=-0.5, anchor="End")),
                t.Label(100, 20, t.LiteralText("Upright"), style=_label_style()),
            ],
        ),
        "drawing-tipped-shapes": fuaran.drawing(
            "drawing-tipped-shapes",
            view_box=t.ViewBox(0, 0, 200, 120),
            title="Tipped marks",
            shapes=[
                t.Rectangle(
                    10,
                    40,
                    30,
                    60,
                    style=t.DrawStyle(fill=t.Static("#3366cc"), tip=t.LiteralText("revenue · Q1 2026 · 1,234,567.89")),
                ),
                t.Circle(
                    70,
                    60,
                    5,
                    style=t.DrawStyle(fill=t.Static("#3366cc"), tip=t.LiteralText("revenue · Q2 2026 · -0.5%")),
                ),
                t.Curve(
                    (t.MoveTo(t.DrawPoint(100, 60)), t.LineTo(t.DrawPoint(130, 60)), t.Close()),
                    style=t.DrawStyle(fill=t.Static("#3366cc"), tip=t.LiteralText("share · Other · £42.00")),
                ),
                t.Polyline(
                    (t.DrawPoint(140, 20), t.DrawPoint(160, 80)),
                    style=t.DrawStyle(stroke=t.Static("#cc6633"), tip=t.LiteralText("revenue")),
                ),
                # A BOUND tip: the text resolves at render time, so the wire
                # carries the binding rather than a string.
                t.Group(
                    (t.Circle(170, 100, 3),),
                    style=t.DrawStyle(tip=t.Bound(t.Static("resolved at render time"))),
                ),
                t.Label(
                    100,
                    110,
                    t.LiteralText("Hover me"),
                    style=_label_style(tip="<script>alert(\"xss\") & 'done'</script>"),
                ),
                # An EMPTY tip is a document, not an absence — it survives the
                # round trip as the empty string.
                t.Ellipse(30, 110, 6, 3, style=t.DrawStyle(tip=t.LiteralText(""))),
                t.Line(0, 0, 200, 0),
            ],
        ),
        # ── Phase 1579 — Fact (6) ────────────────────────────────────────────
        "fact-1": fuaran.fact(
            "fact-1",
            label="Patient",
            value="Alice Smith",
            tone="Brand",
            emphasis=True,
            help="Primary insured",
            icon="user",
        ),
        "now-environment-binding": node.bare(
            fuaran.dashboard(
                "now-environment-binding",
                children=[
                    fuaran.fact("today-fact", label="Today", value=t.Bound(t.Now())),
                    node.bare(
                        fuaran.grid(
                            "overdue-grid",
                            source=_overdue_frame(
                                ["INV-1001", "INV-1002"], ["2026-07-01", "2026-07-28"], None
                            ).to_transform_binding(),
                            columns=_OVERDUE_COLUMNS,
                            row_key_field="id",
                        )
                    ),
                ],
            )
        ),
        "now-grain": node.bare(
            fuaran.dashboard(
                "now-grain",
                children=[
                    fuaran.fact("asof-minute", label="As of (minute)", value=t.Bound(t.Now("Minute"))),
                    fuaran.fact("asof-hour", label="As of (hour)", value=t.Bound(t.Now("Hour"))),
                    fuaran.fact("asof-day", label="Today", value=t.Bound(t.Now("Day"))),
                    node.bare(
                        fuaran.grid(
                            "overdue-grid-day-grain",
                            source=_overdue_frame(["INV-2001"], ["2026-07-01"], "Day").to_transform_binding(),
                            columns=_OVERDUE_COLUMNS,
                            row_key_field="id",
                        )
                    ),
                ],
            )
        ),
        "master-detail-multi-field": node.bare(
            fuaran.dashboard(
                "master-detail-multi-field",
                children=[
                    node.bare(
                        fuaran.grid(
                            "ticket-grid",
                            source=_two_ticket_frame(with_assignee=True).to_transform_binding(),
                            columns=_ticket_columns("id", "priority", "assignee"),
                            row_key_field="id",
                        )
                    ),
                    node.bare(
                        fuaran.card(
                            "ticket-detail",
                            heading="Ticket detail",
                            children=[
                                fuaran.fact(
                                    "detail-ticket",
                                    label="Selected ticket",
                                    value=t.Bound(_selected("id", "TCK-2041")),
                                ),
                                fuaran.fact(
                                    "detail-priority",
                                    label="Priority",
                                    value=t.Bound(_selected("priority", "TCK-2041")),
                                ),
                                fuaran.fact(
                                    "detail-assignee",
                                    label="Assignee",
                                    value=t.Bound(_selected("assignee", "TCK-2041")),
                                ),
                                node.bare(
                                    fuaran.callout(
                                        "detail-note",
                                        body=t.Bound(_selected("assignee", "R. Okafor")),
                                        heading="Assigned to",
                                        tone="Info",
                                    )
                                ),
                            ],
                        )
                    ),
                ],
            )
        ),
        "master-detail-preselected": node.bare(
            fuaran.dashboard(
                "master-detail-preselected",
                children=[
                    node.bare(
                        fuaran.grid(
                            "ticket-grid",
                            source=_two_ticket_frame().to_transform_binding(),
                            columns=_ticket_columns("id", "priority"),
                            row_key_field="id",
                        )
                    ),
                    node.bare(
                        fuaran.card(
                            "ticket-detail",
                            heading="Ticket detail",
                            children=[
                                fuaran.fact(
                                    "detail-ticket",
                                    label="Selected ticket",
                                    emphasis=True,
                                    value=t.Bound(_selected("id", "TCK-2041")),
                                )
                            ],
                        )
                    ),
                    node.bare(
                        fuaran.grid(
                            "related-grid",
                            source=_two_ticket_frame()
                            .filter(col("id").eq(param("ticketId")))
                            .bind(ParamDecl("ticketId", _selected("id", "TCK-2041")))
                            .to_transform_binding(),
                            columns=_ticket_columns("id", "priority"),
                            row_key_field="id",
                        )
                    ),
                ],
            )
        ),
        "master-detail-preselected-second-row": node.bare(
            fuaran.dashboard(
                "master-detail-preselected-second-row",
                children=[
                    node.bare(
                        fuaran.grid(
                            "ticket-grid",
                            source=_three_ticket_frame().to_transform_binding(),
                            columns=_ticket_columns("id", "priority", "note"),
                            row_key_field="id",
                        )
                    ),
                    node.bare(
                        fuaran.card(
                            "ticket-detail",
                            heading="Ticket detail",
                            children=[
                                fuaran.fact(
                                    "detail-ticket",
                                    label="Selected ticket",
                                    emphasis=True,
                                    value=t.Bound(_selected("id", "TCK-2042")),
                                )
                            ],
                        )
                    ),
                    node.bare(
                        fuaran.grid(
                            "related-grid",
                            source=_picked_ticket_frame().to_transform_binding(),
                            columns=_ticket_columns("id", "priority", "note"),
                            row_key_field="id",
                        )
                    ),
                    node.bare(
                        fuaran.callout(
                            "detail-note",
                            # The SAME selection, projected to one cell: filter to the
                            # selected row, keep `note`, take one. A Callout body is a
                            # TextSource, so the pipeline rides a `Bound`.
                            body=t.Bound(_picked_ticket_frame().select("note").limit(1).to_transform_binding()),
                            heading="Ticket note",
                            tone="Info",
                        )
                    ),
                ],
            )
        ),
        # ── Phase 1579 — Mount (2) ───────────────────────────────────────────
        "mount-1": fuaran.mount("mount-1", scope_id="guest-sidebar"),
        "mount-2": fuaran.mount(
            "mount-2",
            scope_id="guest-metrics",
            channel=t.GuestChannel("TwoWay", "MetricsMsg"),
            capabilities=["notify", "call:reports.*"],
            inputs={
                "seed": t.SlotArg(fuaran.markdown("seed-tree", "Initial guest state")),
                "title": t.ScalarStr("Metrics"),
            },
        ),
        # ── Phase 1580 — Query / Invoke / Call / AiTool / I18n (15) ──────────
        #
        # The census in tests/test_bindings_actions.py computes this set from the
        # corpus and names the two fixtures it deliberately leaves out, so a new
        # fixture carrying one of these constructs reddens there rather than
        # sitting silently unauthored here.
        #
        # Binding.Query (7 — the six the phase names, plus grid-declared-edit).
        "query-dependson": node.bare(
            fuaran.metric(
                "query-dependson",
                label="Revenue",
                value=binding.query("orders", "status", "region"),
                format=format.currency("GBP"),
                tone="Brand",
                icon="trending-up",
                subtext="vs last month",
            )
        ),
        "form-combobox-query": node.bare(
            fuaran.form(
                "form-combobox-query",
                submit_label="Search",
                fields=[
                    t.FormField(
                        "city",
                        t.LiteralText("City"),
                        # `on_change=False`: the canonical control declares no
                        # handler, and this record CAN say so — unlike the four
                        # whose `to_wire` writes the sentinel unconditionally.
                        t.ComboboxField(options=binding.query("cities", "country"), on_change=False),
                    )
                ],
            )
        ),
        "form-tokens-query": node.bare(
            fuaran.form(
                "form-tokens-query",
                submit_label="Save",
                fields=[
                    t.FormField(
                        "skills",
                        t.LiteralText("Skills"),
                        t.TokensField(suggestions=binding.query("skills", "role"), on_change=False),
                    )
                ],
            )
        ),
        "grid-exportable-1": node.bare(
            fuaran.grid(
                "grid-exportable-1",
                source=binding.query("settlements"),
                row_key_field="reference",
                exportable=True,
                columns=[
                    t.Column("Reference", field_name="reference"),
                    t.Column(
                        "Amount",
                        format=format.currency("GBP"),
                        kind=t.ColumnKind("Numeric"),
                        field_name="amount",
                    ),
                ],
            )
        ),
        "grid-keep-rows-together-1": node.bare(
            fuaran.grid(
                "grid-keep-rows-together-1",
                source=binding.query("notes"),
                row_key_field="note",
                keep_rows_together=True,
                columns=[t.Column("Note", field_name="note")],
            )
        ),
        "grid-repeat-header-1": node.bare(
            fuaran.grid(
                "grid-repeat-header-1",
                source=binding.query("lines"),
                row_key_field="line",
                repeat_header=True,
                columns=[t.Column("Line", field_name="line")],
            )
        ),
        "grid-declared-edit": node.bare(
            fuaran.grid(
                "grid-declared-edit",
                source=binding.query("stock"),
                row_key_field="month",
                editable=True,
                edit_state_key="stock-adjustments",
                columns=[
                    t.Column("Month", field_name="month"),
                    t.Column("Revenue", field_name="revenue"),
                    # The per-column opt-OUT: an explicit False on one column of
                    # an editable grid is the whole point of the tri-state slot.
                    t.Column("Note", field_name="note", editable=False),
                ],
            )
        ),
        # Binding.Invoke / Action.Invoke (2). Both encoded byte-identically
        # before this phase — through `capability.Invoke`, a record that lowered
        # to the right wire while sitting outside the `Binding` / `Action`
        # unions. They join the table here because 1580 made the record a MEMBER
        # of both, and this is where the estate reads which fixtures a construct
        # covers.
        "metric-invoke": node.bare(
            fuaran.metric(
                "metric-invoke",
                label="Revenue",
                value=binding.invoke("forecast.revenue", horizon="12", scenario="base"),
                format=format.currency("GBP"),
                tone="Brand",
                icon="trending-up",
                subtext="vs last month",
            )
        ),
        "btn-invoke": node.bare(
            fuaran.button(
                "btn-invoke",
                label="Run model",
                on_click=action.invoke("model.score", rows="all"),
                variant="Primary",
            )
        ),
        # TextSource.I18n (2) — a caption and a node-level tooltip.
        "image-caption-i18n-1": fuaran.image(
            "image-caption-i18n-1",
            src="/harbour.jpg",
            alt="Fishing boats moored at first light",
            caption=binding.i18n("gallery.caption.harbour", year=1908),
        ),
        "tooltip-metric-1": node.with_tooltip(
            # An EMPTY args bag, written rather than omitted — see the omission
            # tests in tests/test_bindings_actions.py.
            binding.i18n("metric.latency.hint"),
            node.bare(
                fuaran.metric(
                    "tooltip-metric-1",
                    label="Median latency",
                    value=128,
                    format=None,
                    trend_polarity="LowerIsBetter",
                )
            ),
        ),
        # Action.Call (3) — all three reachable shapes: the handler spelling, the
        # two declarative targets, and neither.
        "call-into": node.bare(
            fuaran.stack(
                "call-into",
                children=[
                    node.bare(
                        fuaran.button(
                            "btn-call-closure",
                            label="Refresh (closure)",
                            on_click=action.call("/api/refresh", on_result=True),
                            variant="Secondary",
                        )
                    ),
                    node.bare(
                        fuaran.button(
                            "btn-fetch-total",
                            label="Fetch total",
                            on_click=action.call("/api/total", into=action.into_state("total")),
                            variant="Primary",
                        )
                    ),
                    node.bare(
                        fuaran.metric("total-metric", label="Total", value=binding.state("total", 0), format=None)
                    ),
                    node.bare(
                        fuaran.button(
                            "btn-fetch-orders",
                            label="Fetch orders",
                            on_click=action.call("/api/orders", into=action.into_query("orders")),
                            variant="Primary",
                        )
                    ),
                    node.bare(
                        fuaran.metric("orders-metric", label="Orders", value=binding.query("orders"), format=None)
                    ),
                ],
            )
        ),
        "action-confirm": node.bare(
            fuaran.button(
                "action-confirm",
                label="Delete",
                variant="Secondary",
                on_click=t.Confirm(
                    t.Bound(binding.selection("orders-grid", field="reference")),
                    action.call("/orders/delete", into=action.into_state("delete-result")),
                ),
            )
        ),
        "composite-tabs-panels": node.bare(
            fuaran.tabs(
                "composite-tabs-panels",
                children=[_overview_panel(), _settings_panel()],
                active_index=0,
                active_tag=t.Static("overview"),
                tab_headers=[
                    t.TabHeader(label=t.LiteralText("Overview"), icon="chart-glyph"),
                    t.TabHeader(label=t.LiteralText("Settings"), disabled=t.Static(False)),
                ],
                tab_tags=["overview", "settings"],
                on_select=False,
            )
        ),
        # Action.AiTool (1) — the same JSON payload through all three of the
        # format's JSON-carrying actions, which is what the fixture is FOR: one
        # value, three arms, and any divergence in the canonical writer shows up
        # as a byte difference between two arms of one document.
        "btn-json-payloads": node.bare(
            fuaran.button(
                "btn-json-payloads",
                label="Fire the JSON-payload actions",
                on_click=action.chain(
                    [
                        action.notify("audit.channel", _JSON_PAYLOAD),
                        action.set_state("draft", _JSON_PAYLOAD),
                        action.ai_tool("summarise", _JSON_PAYLOAD),
                    ]
                ),
                variant="Primary",
            )
        ),
    }


def _expected(fixture_id: str) -> str:
    text = (CORPUS_ROOT / "nodes" / f"{fixture_id}.json").read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


@corpus_required
@pytest.mark.parametrize("fixture_id", sorted(_authored().keys()))
def test_authored_tree_is_byte_identical_to_corpus(fixture_id: str) -> None:
    tree = _authored()[fixture_id]
    assert encode(tree) == _expected(fixture_id)


def test_smart_constructors_inject_per_kind_aria() -> None:
    """The parity feature: interactive / notification / region kinds carry ARIA."""
    assert fuaran.button("b", label="Go").accessibility == accessibility.button
    assert fuaran.metric("m", label="X", value=1).accessibility == accessibility.metric
    assert fuaran.callout("c", body="hi").accessibility == accessibility.callout
    assert fuaran.dashboard("d").accessibility == accessibility.dashboard
    assert fuaran.tabs("t").accessibility == accessibility.tabs
    # Decorative / structural kinds default to no ARIA.
    assert fuaran.markdown("md", "body").accessibility is None
    assert fuaran.divider("dv").accessibility is None


def test_ergonomic_coercions() -> None:
    """Bare ``str`` → Literal text; bare number → Static binding; lenient KPI parse."""
    md = fuaran.markdown("md", "hello")
    assert isinstance(md.kind, t.Markdown)
    assert md.kind.text == t.LiteralText("hello")

    lvr = fuaran.label_value_row("lvr", label="Net", value=10)
    assert lvr.kind.value == t.Static(10)  # type: ignore[union-attr]

    metric = fuaran.metric("m", label="Sales", value="£42k")
    assert metric.kind.value == t.Static(42.0)  # type: ignore[union-attr]


def test_aria_bearing_node_encodes_canonically() -> None:
    """A node that *keeps* its injected ARIA still encodes to canonical JSON and
    survives a decode→encode round-trip byte-stably (the conformance invariant)."""
    from fuaran_py import decode_node, encode_node

    tree = fuaran.metric("m", label="Revenue", value=1, format=None)
    wire = encode(tree)
    assert '"accessibility":{"liveRegion":"polite"}' in wire
    decoded = decode_node(wire)
    assert decoded.ok
    assert encode_node(decoded.value) == wire


# ── The two silent drops the authoring surface used to make ──────────────────
#
# Both defects had the same shape: a wire member the codec decodes and the corpus
# carries had no spelling in the smart constructor, so a tree that meant to state
# it reached the encoder stating nothing — no error, wrong bytes. The parity-table
# entries above are the positive half; these are the refusals, which are the half
# a byte-comparison cannot make. Every message names the fields, because "invalid"
# leaves the author guessing which of the two spellings they were supposed to pick.


def _md(node_id: str) -> t.UiNode:
    return fuaran.markdown(node_id, "body")


def test_switch_case_refuses_both_match_and_when() -> None:
    with pytest.raises(ValueError, match="'match'.*'when'"):
        t.SwitchCase(child=_md("c"), match="summary", when=t.State("cart.empty", None))


def test_switch_case_refuses_neither_match_nor_when() -> None:
    """The one that used to encode silently: `{child}` alone is a case with no
    condition, which no host can render — it is neither taken nor skippable."""
    with pytest.raises(ValueError, match="'match'.*'when'"):
        t.SwitchCase(child=_md("c"))


def test_switch_refuses_a_case_selector_that_is_neither_a_string_nor_a_binding() -> None:
    with pytest.raises(TypeError, match="'match'.*'when'"):
        fuaran.switch("s", cases=[(3, _md("c"))], default=_md("d"), state_key="view")  # type: ignore[list-item]


def test_switch_accepts_a_switch_case_directly() -> None:
    """A case built by hand is a case: the constructor coerces the pair spellings
    but never refuses the model it coerces to."""
    tree = fuaran.switch(
        "s",
        cases=[t.SwitchCase(child=_md("c"), when=t.State("form.valid", None))],
        default=_md("d"),
        state_key="",
    )
    assert '"when":{"$type":"State","key":"form.valid"}' in encode(tree)


def test_local_refuses_two_commit_destinations() -> None:
    """`onCommit` + `commitTo` is a decode refusal (WIRE_FORMAT §3.3.3), so the
    authoring surface refuses to build it rather than emitting an unreadable tree."""
    with pytest.raises(ValueError, match="on_commit.*commit_to"):
        binding.local(t.State("k", ""), t.OnBlur(), commit_to="order.total", on_commit=True)


def test_local_refuses_a_declarative_buffer_with_no_commit_key() -> None:
    with pytest.raises(ValueError, match="commit_to"):
        binding.local(t.State("k", ""), t.OnBlur(), on_commit=False)


def test_local_refuses_a_codec_with_no_total_inverse() -> None:
    """Only `Number` has a locale-independent inverse; a `Currency` buffer would
    format one way and parse another, which is the hole the codec member closes."""
    with pytest.raises(ValueError, match="codec"):
        binding.local(t.State("k", 0), t.OnBlur(), commit_to="k", codec=t.Currency("GBP"))


def test_local_keeps_the_handler_spelling_by_default() -> None:
    """The pre-existing calls do not move: no `commit_to` means the closure
    sentinel, exactly as `form-local-1` and `form-local-debounce` carry it."""
    wire = encode(fuaran.markdown("m", t.Bound(binding.local(t.State("salary", ""), t.OnBlur()))))
    assert '"onCommit":"<closure>"' in wire
    assert "commitTo" not in wire


# ── Phase 1576 — what a byte comparison cannot say ───────────────────────────
#
# The parity entries above pin the SHAPES the corpus happens to carry. These pin
# the RULE those shapes are instances of, in both directions: that the handler
# and the value are each optional, that omitting the argument still emits what it
# always emitted, and that an omitted handler leaves a control the host still
# treats as live rather than as broken.

#: Every control record this phase made handler-optional, with the handler's
#: keyword, the wire key it drives, and the arguments the record requires.
_HANDLER_RECORDS = [
    (t.TextField, "on_change", "onChange", ()),
    (t.NumberField, "on_change", "onChange", ()),
    (t.CheckboxField, "on_toggle", "onToggle", ()),
    (t.TextAreaField, "on_change", "onChange", (None, 4)),
    (t.RangedNumber, "on_change", "onChange", ()),
    (t.RangeField, "on_change", "onChange", ()),
    (t.DateField, "on_change", "onChange", ()),
    (t.DateRangeField, "on_change", "onChange", ()),
    (t.ChoiceField, "on_change", "onChange", (t.Static([]),)),
    (t.SegmentedChoice, "on_change", "onChange", (t.Static([]),)),
    (t.ComboboxField, "on_change", "onChange", (t.Static([]),)),
    (t.TokensField, "on_change", "onChange", ()),
    (t.RatingField, "on_change", "onChange", (5,)),
    (t.ColorField, "on_change", "onChange", ()),
]


@pytest.mark.parametrize("record,flag,key,args", _HANDLER_RECORDS, ids=lambda v: getattr(v, "__name__", ""))
def test_a_control_declaring_no_handler_carries_none_on_the_wire(
    record: object, flag: str, key: str, args: tuple
) -> None:
    """The 1170 rule, generalised: the ABSENT key is what arms a renderer's
    write-back default, so every control must be able to reach it."""
    wire = encode_value(record(*args, **{flag: False}).to_wire())  # type: ignore[operator]
    assert key not in wire


@pytest.mark.parametrize("record,flag,key,args", _HANDLER_RECORDS, ids=lambda v: getattr(v, "__name__", ""))
def test_a_controls_handler_is_present_by_default(record: object, flag: str, key: str, args: tuple) -> None:
    """The other half, and the reason the default is `True` rather than the
    reference host's `None`: every tree authored against the pre-phase surface
    said nothing about the handler and got one, so silence has to keep meaning
    what it meant. Reaching the shorter document is an explicit `False`."""
    del flag
    wire = encode_value(record(*args).to_wire())  # type: ignore[operator]
    assert f'"{key}":"<closure>"' in wire


@pytest.mark.parametrize("record,flag,key,args", _HANDLER_RECORDS, ids=lambda v: getattr(v, "__name__", ""))
def test_a_control_declaring_no_value_carries_none_on_the_wire(
    record: object, flag: str, key: str, args: tuple
) -> None:
    """`{"$type":"Text"}` is the canonical MINIMAL control, and it is a BOUND one:
    the decoder synthesises the context's auto-binding for it. A record that
    emitted `value` unconditionally could not reach it at all."""
    del flag, key
    # Read the control's OWN fields rather than the encoded string: an options
    # binding is itself a `Static` carrying a `value`, so a substring test would
    # pass on `Choice` for the wrong reason.
    assert "value" not in record(*args).to_wire().fields  # type: ignore[operator]


def test_a_declared_local_binding_is_honoured_rather_than_defaulted_away() -> None:
    """Making `value` optional must not make a DECLARED value optional: the
    buffer the author wrote is the one that ships."""
    wire = encode_value(t.TextField(t.Local(t.State("salary", ""), t.OnBlur())).to_wire())
    assert '"value":{"$type":"Local"' in wire
    assert '"onCommit":"<closure>"' in wire


def test_text_area_refuses_a_missing_rows() -> None:
    """`rows` is the one control member the schema requires beyond `$type`. It
    carries a default only so `value` can precede it and every positional
    `TextAreaField(value, rows)` call keeps working — the refusal is what stops
    that convenience becoming a control no schema accepts."""
    with pytest.raises(ValueError, match="rows"):
        t.TextAreaField(t.Static(""))


def test_range_lowers_a_literal_pair_as_the_bare_object() -> None:
    """`Range`'s pair rides the wire without a `Static` envelope — the
    `DateRange` posture, and the shape `filters-declarative` carries."""
    assert '"value":{"max":100,"min":0}' in encode_value(t.RangeField((0, 100), on_change=False).to_wire())


def test_modal_omitting_the_argument_keeps_the_no_op_chain() -> None:
    """`modal-1` carries `"onDismiss":{"$type":"Chain","ops":[]}`, and an author
    who says nothing must still get it — which is why `None` had to become a
    DISTINCT declaration rather than the parameter's default."""
    assert '"onDismiss":{"$type":"Chain","ops":[]}' in encode(fuaran.modal("m", dismissable=True))


def test_modal_declaring_no_dismiss_handler_omits_the_key() -> None:
    assert "onDismiss" not in encode(fuaran.modal("m", dismissable=True, on_dismiss=None))


def test_the_two_tab_selection_channels_arm_independently() -> None:
    """A tab strip may dispatch the tag while writing back the index, which is
    what `controls-closure` does — so one flag could not have served both."""
    index_only = encode(fuaran.tabs("t", tab_tags=["a", "b"]))
    assert '"onSelect":"<closure>"' in index_only and "onSelectTag" not in index_only

    tag_only = encode(fuaran.tabs("t", tab_tags=["a", "b"], on_select=False, on_select_tag=True))
    assert '"onSelectTag":"<closure>"' in tag_only and '"onSelect"' not in tag_only


def test_select_multi_channel_arms_independently_of_the_single_one() -> None:
    both = encode(
        fuaran.select(
            "s",
            label="Tags",
            source=binding.static([]),
            value=t.Static(None),
            multiple=True,
            values=binding.static([]),
            on_change_multi=True,
        )
    )
    assert '"onChange":"<closure>"' in both and '"onChangeMulti":"<closure>"' in both


@corpus_required
def test_a_handler_less_control_over_a_state_slot_is_LIVE_not_inert() -> None:
    """The acceptance criterion's second half, through the host's own validator:
    omitting the handler is the DECLARATIVE shape, and the write-back default is
    what carries the interaction. The falsifier is the same control over a
    `Static` value, which genuinely cannot act — without it this test would pass
    on a validator that had stopped checking."""
    from fuaran_py import decode_node
    from fuaran_py.validator import validate_node

    def findings(tree: object) -> list[str]:
        decoded = decode_node(encode(tree))  # type: ignore[arg-type]
        assert decoded.ok, decoded
        return [f.code for f in validate_node(decoded.value)]

    live = fuaran.form(
        "f",
        submit_label="Save",
        fields=[
            t.FormField(
                "email", t.LiteralText("Email"), t.TextField(t.State("draft.email", ""), on_change=False), False
            )
        ],
    )
    assert findings(live) == []

    inert = fuaran.form(
        "f",
        submit_label="Save",
        fields=[t.FormField("email", t.LiteralText("Email"), t.TextField(t.Static(""), on_change=False), False)],
    )
    assert findings(inert) == ["FUARAN069"]
