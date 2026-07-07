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
from fuaran_py.schema import types as t
from fuaran_py.ui import accessibility, action, binding, encode, fuaran, node


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
        "split-1": fuaran.split_panel("split-1", children=[metric_1, markdown_1], weight=0.6),
        "card-1": node.bare(fuaran.card("card-1", children=[metric_1], heading="Insights")),
        "step-1": node.bare(fuaran.stepper("step-1", children=[markdown_1, markdown_1], active_step=1)),
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
                source=binding.opaque(),
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
        "grid-1": node.bare(fuaran.grid("grid-1", source=binding.opaque(), columns=[t.Column(label="Channel")])),
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
    assert lvr.kind.source == t.Static(10)  # type: ignore[union-attr]

    metric = fuaran.metric("m", label="Sales", value="£42k")
    assert metric.kind.source == t.Static(42.0)  # type: ignore[union-attr]


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
