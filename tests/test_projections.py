"""The two static projections — the markdown document and the email-safe digest (fuaran#1176).

Three families of assertion, and they prove different things:

* **Scope completeness**, measured against the render-fidelity manifest rather than against a
  list in this file. A new ``NodeKind`` cannot arrive with no declared posture in either
  projection, and every kind the manifest calls ``behavioural`` must be ``openLive`` in both —
  the derivation that stops a dead control reaching an inbox.
* **Output properties**, each with its falsifier: determinism run twice, the email lint run
  BOTH ways (clean over real output, and reddening on a planted construct — a lint that could
  not fail would be decoration), and the markdown escape checked by pushing the output through
  the host's own §14 renderer and asserting the hostile text came back as text.
* **The three-projection agreement** the phase's acceptance criterion names: one tree, three
  outputs, each byte-stable.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT
from fuaran_py import decode_node, encode_node
from fuaran_py.model import Node
from fuaran_py.renderer import (
    DEFAULT_EMAIL_OPTIONS,
    EmailOptions,
    MarkdownOptions,
    lint,
    render_email,
    render_email_document,
    render_html,
    render_markdown,
)
from fuaran_py.renderer import document as doc
from fuaran_py.renderer import email as digest
from fuaran_py.renderer import markdown as gfm
from fuaran_py.renderer.projection import (
    behavioural_wire_kinds,
    duplicate_kinds,
    missing_kinds,
    unknown_kinds,
)
from fuaran_py.ui import binding, node
from fuaran_py.ui import fuaran as F

_FIDELITY = CORPUS_ROOT / "render-fidelity.json"

# Gated on the ARTEFACT, not on `corpus_required`. The committed offline snapshot pins the
# certification families and deliberately does not carry `render-fidelity.json`, so a checkout
# with no sibling authority has a corpus and no manifest — a state `corpus_required` reports as
# "present" and these tests would then fail on a missing file rather than skip.
fidelity_required = pytest.mark.skipif(
    not _FIDELITY.is_file(),
    reason=f"render-fidelity manifest not found at {_FIDELITY}",
)

_PROJECTIONS = [
    pytest.param(digest.SCOPE, digest._DISPATCH, id="email"),
    pytest.param(doc.SCOPE, doc._DISPATCH, id="document"),
]


def _manifest() -> dict:
    return json.loads(_FIDELITY.read_text(encoding="utf-8"))


def _canonical_kinds() -> list[str]:
    return [row["kind"] for row in _manifest()["kinds"] if isinstance(row.get("kind"), str)]


def _decoded(tree) -> Node:
    result = decode_node(encode_node(tree.to_wire()))
    assert result.ok, result.error
    return result.value


# ── Scope completeness (the contract half) ──────────────────────────────────


@fidelity_required
@pytest.mark.parametrize(("scope", "dispatch"), _PROJECTIONS)
def test_scope_declares_every_canonical_kind(scope, dispatch) -> None:
    """A new NodeKind cannot arrive with no declared posture.

    Measured against the manifest, not against a list here: a second enumeration would drift
    from the artefact silently, and the drift would look exactly like coverage.
    """
    assert missing_kinds(scope, _canonical_kinds()) == []


@fidelity_required
@pytest.mark.parametrize(("scope", "dispatch"), _PROJECTIONS)
def test_scope_declares_nothing_the_wire_format_lacks(scope, dispatch) -> None:
    """The converse: a row kept for a renamed or withdrawn kind is dead weight reading as coverage."""
    assert unknown_kinds(scope, _canonical_kinds()) == []


@pytest.mark.parametrize(("scope", "dispatch"), _PROJECTIONS)
def test_scope_has_no_duplicate_rows(scope, dispatch) -> None:
    assert duplicate_kinds(scope) == []


@pytest.mark.parametrize(("scope", "dispatch"), _PROJECTIONS)
def test_every_row_carries_its_reasoning(scope, dispatch) -> None:
    """A disposition with no note records a verdict and none of the argument for it."""
    assert [kind for kind, d in scope if len(d.note) < 20] == []


@fidelity_required
@pytest.mark.parametrize(("scope", "dispatch"), _PROJECTIONS)
def test_every_behavioural_kind_projects_to_open_live(scope, dispatch) -> None:
    """The derivation that stops a dead control reaching a static target.

    ``behavioural`` means "inert server-side, gains behaviour at hydration"; a target that
    hydrates nothing must therefore link to it rather than draw it. Derived from the manifest,
    so a NEW interactive kind reddens here rather than shipping.
    """
    interactive = behavioural_wire_kinds(_manifest())
    assert interactive, "the manifest declares no behavioural kind — this check would be vacuous"
    declared = dict(scope)
    assert [k for k in interactive if declared[k].kind != "openLive"] == []


@fidelity_required
@pytest.mark.parametrize(("scope", "dispatch"), _PROJECTIONS)
def test_every_canonical_kind_has_a_dispatch_arm(scope, dispatch) -> None:
    """A declared posture with no arm behind it renders the unprojected note, not the posture."""
    assert [k for k in _canonical_kinds() if k not in dispatch] == []


# ── The tree the output assertions are made against ─────────────────────────


def _broad_tree():
    """One tree exercising a rendered, a structural, an open-live and an omitted kind."""
    rows = [{"region": "EMEA", "revenue": 8300.0}, {"region": "APAC", "revenue": 6800.0}]
    return F.dashboard(
        "dash",
        children=[
            F.heading("title", "Revenue by region", level=1),
            F.stack(
                "kpis",
                orientation="Horizontal",
                children=[
                    F.metric("m-total", label="Total revenue", value=15100.0, subtext="week to date"),
                    F.metric("m-units", label="Units", value=1700),
                ],
            ),
            F.callout("note", body="Africa is 8% of revenue.", tone="Info", heading="Watch"),
            F.list("bullets", items=["First point", "Second point"]),
            F.table("rows", headers=["Region", "Revenue"], rows=[["EMEA", "8,300"], ["APAC", "6,800"]]),
            F.link("more", href="/reports/weekly", label="Full report"),
            node.bare(
                F.chart(
                    "revenue-chart",
                    source=binding.static(rows),
                    x_field="region",
                    y_fields=["revenue"],
                    kind="Bar",
                    title="Revenue",
                )
            ),
            F.button("refresh", label="Refresh"),
            F.icon("deco", name="star"),
        ],
    )


# ── Determinism ─────────────────────────────────────────────────────────────


def test_all_three_projections_are_byte_stable_across_runs() -> None:
    """The acceptance criterion, stated as a test.

    Run twice from the same tree and the same options: identical bytes. The failure this
    catches is a projection that reached for a clock, a fresh id, or an unordered collection —
    none of which announce themselves in a single run's output.
    """
    tree = _decoded(_broad_tree())
    for render in (
        lambda: render_markdown(tree, title="Weekly"),
        lambda: render_email_document(tree, "Weekly"),
        lambda: render_html(tree),
    ):
        assert render() == render()


def test_the_document_and_the_digest_read_the_same_figures() -> None:
    """A digest that disagreed with the page about a number would be worse than no digest.

    Both projections resolve through the renderer's own number formatting, so the same value
    appears in both — this pins the shared path rather than two coincidentally-equal formats.
    """
    tree = _decoded(_broad_tree())
    md = render_markdown(tree)
    html = render_email(tree)
    for figure in ("15100", "1700"):
        assert figure in md
        assert figure in html


# ── The email lint, both ways ───────────────────────────────────────────────


def test_the_digest_trips_no_hostile_construct() -> None:
    tree = _decoded(_broad_tree())
    assert lint(render_email_document(tree, "Weekly")) == []


def test_the_digest_lint_reddens_on_a_planted_construct() -> None:
    """The go-red half.

    A lint that has only ever been observed passing is indistinguishable from one that cannot
    fail, and every construct below is one this projection is specifically supposed never to
    emit.
    """
    for planted, expected in (
        ('<div style="display:flex">x</div>', {"EMAIL-DIV-LAYOUT", "EMAIL-FLEX"}),
        ("<script>x()</script>", {"EMAIL-SCRIPT"}),
        ('<link rel="stylesheet" href="x.css">', {"EMAIL-EXTERNAL-CSS"}),
        ('<button type="button">Go</button>', {"EMAIL-CONTROL"}),
        ('<svg viewBox="0 0 1 1"></svg>', {"EMAIL-EMBED"}),
        ("<style>p{color:red}</style>", {"EMAIL-EXTERNAL-CSS"}),
    ):
        codes = {f.code for f in lint(planted)}
        assert expected <= codes, f"{planted!r} produced {codes}"


def test_the_digest_emits_no_control_for_an_interactive_kind() -> None:
    """The scope line, checked in the bytes rather than in the table.

    A ``Button`` in the tree must reach the inbox as a labelled affordance and never as a
    control — which is the one thing the table could declare correctly while the renderer did
    something else.
    """
    tree = _decoded(F.dashboard("d", children=[F.button("go", label="Refresh")]))
    html = render_email(tree)
    assert "<button" not in html
    # "Action", not "Refresh": the affordance's words are the node's declared ACCESSIBLE
    # label where it has one, and the kind name otherwise — the reference host's rule, kept
    # rather than improved on, because a button's `label` is a verb for a control that is not
    # there ("Refresh — open live" promises the digest can refresh) while the accessible label
    # names the thing.
    assert "Action — available in the live view" in html


def test_a_declared_live_surface_turns_the_note_into_a_link() -> None:
    tree = _decoded(F.dashboard("d", children=[F.button("go", label="Refresh")]))
    html = render_email(tree, options=EmailOptions(live_url="https://app.example/weekly"))
    assert 'href="https://app.example/weekly#go"' in html
    assert "Action — open live" in html


def test_a_closed_toast_is_omitted_from_both_projections() -> None:
    """OMITTED rather than hidden: a notification that leaks into a projection it was closed
    in is a disclosure bug, and neither target honours ``hidden`` reliably."""
    tree = _decoded(F.dashboard("d", children=[F.toast("t", message="Deploy failed", open=False, tone="Critical")]))
    assert "Deploy failed" not in render_email(tree)
    assert "Deploy failed" not in render_markdown(tree)


# ── The markdown escape, checked through the host's own §14 renderer ────────

_HOSTILE_TEXT = "# not a heading, - not a list, 1. not ordered, > not a quote, *not emphasis*, [not](a link)"


def test_no_resolved_text_lands_at_block_start() -> None:
    """The invariant the narrowed escape set rests on.

    ``_INLINE_SPECIALS`` deliberately omits the leading-position constructs, on the grounds
    that no arm ever emits resolved text as a block's first character. This is the falsifier:
    a tree whose every text slot begins with a block construct, pushed through the host's own
    §14 renderer, must come back with that text as TEXT — no heading, no list, no blockquote
    the tree did not declare.
    """
    tree = _decoded(
        F.dashboard(
            "d",
            children=[
                F.metric("m", label=_HOSTILE_TEXT, value=1.0),
                F.label_value_row("lv", label=_HOSTILE_TEXT, value=2.0),
                F.progress("p", fraction=0.5, label=_HOSTILE_TEXT),
                F.list("l", items=[_HOSTILE_TEXT]),
                F.callout("c", body=_HOSTILE_TEXT),
                F.badge("b", label=_HOSTILE_TEXT),
            ],
        )
    )
    html = gfm.to_html(render_markdown(tree))
    # The literal text survived: no emphasis was manufactured out of the asterisks and no
    # anchor out of the brackets.
    assert "not emphasis" in html
    assert "<em>not emphasis</em>" not in html
    assert "<a href" not in html
    # And nothing became a heading it was not: the tree declares no heading at all.
    assert "<h1" not in html and "<h2" not in html and "<h3" not in html


def test_a_table_cell_survives_a_pipe_and_a_newline() -> None:
    """A raw pipe ends the cell and a newline ends the row, and a ragged GFM table is silently
    read as a paragraph — so the data would simply be gone rather than mis-rendered."""
    tree = _decoded(
        F.dashboard(
            "d",
            children=[F.table("t", headers=["a|b"], rows=[["one|two"], ["line\nbreak"]])],
        )
    )
    md = render_markdown(tree)
    html = gfm.to_html(md)
    assert "<table" in html
    assert md.count("\n|") >= 3  # header, delimiter, and each row on its own line
    assert "one|two" not in md  # the raw pipe never reaches the cell


# ── Charts: the two honest readings ─────────────────────────────────────────


def test_a_chart_carries_its_picture_under_svg_mode() -> None:
    tree = _decoded(_broad_tree())
    md = render_markdown(tree, options=MarkdownOptions(charts="svg"))
    assert "<svg" in md
    # The bare element, with none of the page's wrapper markup around it.
    assert "<div><svg" not in md


def test_a_chart_carries_its_data_under_table_mode() -> None:
    tree = _decoded(_broad_tree())
    md = render_markdown(tree, options=MarkdownOptions(charts="table"))
    assert "<svg" not in md
    assert "| region | revenue |" in md
    assert "| EMEA | 8300 |" in md


def test_table_mode_output_survives_the_section_14_renderer() -> None:
    """The reason ``table`` exists: §14 escapes raw HTML by construction, so an SVG-carrying
    document prints tag soup through it while the table-carrying one renders."""
    tree = _decoded(_broad_tree())
    html = gfm.to_html(render_markdown(tree, options=MarkdownOptions(charts="table")))
    assert "<table" in html
    assert "&lt;svg" not in html


# ── Destination policy ──────────────────────────────────────────────────────


def test_an_undeclared_destination_is_refused_in_both_projections() -> None:
    """Deny-non-local is ambient here as everywhere else in this renderer.

    In a digest an undeclared image ``src`` IS the tracking pixel — fetched on open, reporting
    that this named person read this message — so the default matters more here than on a page
    the reader chose to load.
    """
    tree = _decoded(
        F.dashboard(
            "d",
            children=[
                F.link("l", href="https://collector.example/?s=1", label="Report"),
                F.image("i", src="https://collector.example/pixel.gif", alt="pixel"),
            ],
        )
    )
    html = render_email(tree)
    md = render_markdown(tree)
    for output in (html, md):
        assert "collector.example" not in output
        assert "about:blank#fuaran-egress-refused" in output
    # The `data-*` marker has no markdown spelling and does not survive a mail sanitiser, so
    # neither projection emits one — the refusal travels as the inert URL alone.
    assert "data-fuaran-egress-refused" not in html
    assert "data-fuaran-egress-refused" not in md


def test_the_digest_defaults_are_the_documented_ones() -> None:
    """The option defaults are part of the cross-host agreement, so they are pinned rather
    than left to whatever the dataclass happens to say."""
    assert DEFAULT_EMAIL_OPTIONS.max_width_px == 600
    assert DEFAULT_EMAIL_OPTIONS.live_url is None
    assert "'" not in DEFAULT_EMAIL_OPTIONS.font_stack, "a quoted family is entity-escaped and then printed literally"


def test_the_document_offsets_headings_beneath_its_title() -> None:
    """A tree's own level-1 heading must not compete with the document's title: exactly one
    ``#`` line, or a reader's outline has two roots."""
    tree = _decoded(F.dashboard("d", children=[F.heading("h", "Section", level=1)]))
    md = render_markdown(tree, title="Weekly")
    assert md.startswith("# Weekly\n")
    assert len([line for line in md.splitlines() if line.startswith("# ")]) == 1
    assert "## Section" in md
