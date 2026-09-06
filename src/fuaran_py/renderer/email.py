"""The email-safe render projection — the digest.

HTML email is the most hostile render target in computing: no JavaScript, no external
stylesheet, no flexbox or grid worth relying on, and a rendering engine per client
(Outlook desktop still lays out through Word). It is also a projection of the same tree
everything else renders — a scheduled digest is not a fork of the application, it is
another emission of it.

**THE SCOPE LINE IS THE FEATURE.** The projection is bounded hard to the *Display
subset* — the kinds that carry information rather than interaction: headings and text,
Metric tiles, Facts and label-value rows, badges, callouts, lists, summary lists, static
tables, and the typed crawlable ``Link``. Everything interactive projects to a labelled
"open live" link. That rule is not a nicety: a ``<button>`` in an email is a control that
looks live and cannot be, and a ``<form>`` that posts nowhere is worse than an absent one.
The projection never emits a half-working control. :data:`SCOPE` is that decision, machine
readable, one row per canonical wire kind.

**It is cross-checked against the fidelity manifest, not asserted.** WIRE_FORMAT §13's
per-kind render-fidelity declaration marks a kind ``behavioural`` to mean exactly "this
control renders inert server-side and gains its behaviour at hydration" — which is the
definition of a kind that must NOT be rendered into an email.
:func:`~fuaran_py.renderer.projection.behavioural_wire_kinds` derives that set from the
manifest rather than restating it, and the test corpus asserts every member of it has an
``openLive`` row here. A new interactive kind therefore fails the suite rather than
silently shipping a dead button to an inbox. :data:`SCOPE`'s completeness is measured
against the same manifest's kind list, so a new ``NodeKind`` cannot arrive with no
declared email posture.

**Determinism.** Same tree, same options ⇒ same bytes. There is no clock, no identifier
minting, and no iteration over an unordered collection anywhere in this file; text
resolves through the renderer's own :func:`~fuaran_py.renderer.bindings.render_text` and
figures through :func:`~fuaran_py.renderer.bindings.format_number` — the same functions the
HTML document uses, so a digest and the page it links to cannot disagree about what a
number is.

**What this is NOT.** It is not parity with the HTML renderer, and must not be read as a
second conformant rendering surface: the class-vocabulary parity lock covers the
``fuaran-*`` vocabulary the browser renderers share, and this projection deliberately
emits none of it — an email has no stylesheet to key those classes off. The two renderers
answer different questions about one tree.

**Nor is it byte-parity with the reference host's projection**, and that limit is worth
stating plainly rather than leaving a reader to infer it. The reference implementation
(``Fuaran.UI.Renderer.Server.Email``) is pinned by a golden corpus that lives inside that
host's own test project rather than in the shared conformance corpus, so no cross-host
gate exists for this surface in either direction. What IS ported, deliberately and
checkably, is the part that would be worth a corpus: the four dispositions, the scope
table row for row with its reasoning, the derivation of the interactive set from the
fidelity manifest, the inline style vocabulary's literal values, the option defaults, and
the hostile-construct lint's code and token set. Two hosts agreeing about what an email
projection IS is the property that matters; two hosts agreeing about which pixel a padding
lands on is not, and claiming it without a gate would be the worse failure.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from ..limits import MAX_NODE_DEPTH
from ..model import Arr, Node, Obj, Value
from . import markdown as _markdown
from .bindings import (
    BindingSources,
    format_number,
    is_node_visible,
    render_text,
    resolve_binding,
    resolve_scalar_number,
    resolve_scalar_text,
    resolve_source,
    select_switch_case,
)
from .egress import (
    DENY_NON_LOCAL_EGRESS,
    EGRESS_REFUSAL_ATTRIBUTE,
    EgressClass,
    EgressPolicy,
    sanitize_url_for_egress,
)
from .html import element, escape_text, text_element, void_element
from .projection import Disposition, disposition_of, omitted, open_live, rendered, structural

# The tree-walking helpers and the em-dash are reached from the HTML renderer rather than
# re-implemented. This is the package-internal reuse the reference host performs for the
# same reason — its email projection compiles AFTER the SSR renderer precisely so it can
# call that module's internal text and number projections, "so a digest cannot disagree
# with the page it links to". A second copy of `_as_node` here would be a second answer to
# what counts as a child node.
from .render import _EM_DASH, _a11y_name, _as_node, _child_nodes, _collect_fragments
from .sanitize import sanitize_markdown_html, sanitize_url_or_blank
from .seeds import with_state_seeds

# ─── The declared Display-subset scope ──────────────────────────────────────

#: The projection's fidelity declaration: one row per canonical wire kind, ordered by wire
#: name, so a new kind lands as one clean insert.
SCOPE: Final[tuple[tuple[str, Disposition], ...]] = (
    ("Badge", rendered("an inline pill: the resolved label in a bordered, tone-coloured span")),
    ("Box", structural("the container becomes a layout table; a Card gains a bordered heading row")),
    (
        "Button",
        open_live(
            "a button in an inbox is a control that cannot fire - the declared action needs a runtime that is not there"
        ),
    ),
    ("Callout", rendered("a bordered, tone-tinted notice table carrying the heading and body")),
    (
        "Chart",
        open_live(
            "even a server-lowered chart emits SVG, which Outlook's Word engine does not draw; a "
            "broken picture is worse than a link"
        ),
    ),
    (
        "CodeBlock",
        rendered("the deterministic escaped `<pre><code>` floor, restyled inline; no highlighting pass exists here"),
    ),
    (
        "Custom",
        open_live(
            "a host renderer's output is outside this projection's email audit, so it is linked "
            "rather than embedded unchecked"
        ),
    ),
    (
        "DataGrid",
        rendered(
            "the `staticRows` form renders as a real bordered `<table>`; a bound grid whose rows "
            "resolve renders them, and only a grid with nothing server-side to draw projects to "
            "open-live"
        ),
    ),
    (
        "Disclosure",
        structural(
            "always EXPANDED - `<details>` does not open in Outlook, and a permanently-shut "
            "section is content the reader silently loses"
        ),
    ),
    ("Drawing", open_live("inline SVG, for the same reason as Chart")),
    (
        "Embed",
        open_live(
            "a third-party browsing context. Every mainstream client strips `<iframe>` outright, "
            "so there is no degraded projection to attempt - and unlike a media element there is "
            "not even a poster frame to reach for, because the framed document has never been "
            "fetched. The mandatory title is the link text, which is exactly what it was written "
            "to be"
        ),
    ),
    ("ErrorBoundary", structural("the protected child renders; the fallback is a client-runtime path")),
    ("Fact", rendered("a label/value tile with its optional help text")),
    ("FileUpload", open_live("interactive (fidelity manifest: behavioural)")),
    ("Filters", open_live("interactive (fidelity manifest: behavioural)")),
    (
        "Form",
        open_live(
            "interactive (fidelity manifest: behavioural) - a form that posts nowhere is worse than an absent one"
        ),
    ),
    ("FragmentDecl", omitted("a template declaration paints nothing, here as everywhere")),
    (
        "FragmentRef",
        structural("expanded against the tree's fragment registry; an unresolved reference renders a labelled note"),
    ),
    ("Heading", rendered("a real `<h1>`-`<h6>` with an inline type scale")),
    (
        "Icon",
        omitted(
            "the uniform icon hook carries the name in a data attribute and relies on host CSS for "
            "the glyph; an email has neither, so the hook would paint an empty box"
        ),
    ),
    ("Image", rendered("a real `<img>` with the sanitised src, `alt` always present, and no CSS-dependent shaping")),
    ("LabelValueRow", rendered("a two-cell row, the value formatted through the shared projection")),
    (
        "Link",
        rendered(
            "the point of the whole exercise: a real crawlable `<a href>`, the one interaction email genuinely has"
        ),
    ),
    ("List", rendered("a real `<ol>`/`<ul>` with inline item spacing; no CSS list resets to rely on")),
    ("Map", open_live("a client map library draws it; the server has markers, not a picture")),
    (
        "Markdown",
        rendered(
            "the deterministic GFM render, which escapes raw HTML by construction; task-list "
            "checkboxes become ballot glyphs, since a disabled `<input>` is a control"
        ),
    ),
    (
        "Math",
        rendered(
            "NARROWED to the escaped source in a monospace span. The MathML floor is correct in a "
            "browser and blank in Outlook, so the projection prefers readable LaTeX to invisible "
            "mathematics"
        ),
    ),
    (
        "Media",
        open_live(
            "a transport, not a picture. No mainstream mail client plays inline media, and the "
            "fallbacks are all worse than a link: a `<video>` degrades to a blank rectangle, and a "
            "poster frame rendered as a bare `<img>` is a still image that silently looks like a "
            "broken player. The label is the link text, which is exactly what it was written to be"
        ),
    ),
    ("Metric", rendered("the KPI tile: label, formatted value, trend and subtext, stacked in a cell")),
    (
        "Modal",
        open_live(
            "interactive (fidelity manifest: behavioural) - an overlay has no meaning in a document with no viewport"
        ),
    ),
    ("Mount", open_live("the guest tree attaches client-side; there is nothing to project")),
    (
        "Progress",
        rendered(
            "a two-cell table bar plus the percentage as text, so the figure survives a client that drops backgrounds"
        ),
    ),
    (
        "ScrollArea",
        structural("children render in full - an email has no clipping, and hidden content is lost content"),
    ),
    ("Select", open_live("interactive (fidelity manifest: behavioural)")),
    ("Skeleton", omitted("a loading placeholder describes a state a delivered email is never in")),
    (
        "Sparkline",
        open_live("the polyline is drawn as inline SVG, which is the Chart argument at a smaller size"),
    ),
    (
        "SplitPanel",
        structural("a two-cell row at the declared weights, which collapses acceptably on narrow clients"),
    ),
    ("Stepper", open_live("interactive (fidelity manifest: behavioural)")),
    ("SummaryList", structural("the heading plus its children, stacked")),
    (
        "Switch",
        structural("the case matching the resolved selector, else the default - the same branch the HTML render picks"),
    ),
    (
        "Tabs",
        open_live(
            "interactive (fidelity manifest: behavioural). Deliberately NOT 'render the active "
            "panel': a digest that silently drops the other panels is a lie about how much it "
            "contains"
        ),
    ),
    (
        "Toast",
        rendered(
            "an OPEN toast renders as a static notice; a CLOSED one is OMITTED rather than hidden, "
            "because the `hidden` attribute is not honoured everywhere and a leaked notification "
            "is a disclosure bug"
        ),
    ),
    (
        "Tree",
        open_live(
            "interactive (fidelity manifest: behavioural). The alternative was considered and "
            "declined: nested `<ul>`s would render perfectly well in a mail client, but only by "
            "showing every row - including the branches the document says are CLOSED - because an "
            "email can toggle nothing. That is the `Tabs` argument at a different slot"
        ),
    ),
)


def disposition_for(wire_kind: str) -> Disposition | None:
    """The declared email posture of a wire kind, or ``None`` for a kind with no row."""
    return disposition_of(SCOPE, wire_kind)


# ─── Options ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EmailOptions:
    """Host-supplied knobs.

    Deliberately small: an email projection with a theme engine is a CSS framework, and
    the client fragmentation this module exists to survive is exactly what defeats one.
    """

    #: The live surface the "open live" affordances point at. ``None`` emits the label
    #: WITHOUT an anchor — a dangling href is a broken promise, whereas a labelled note is
    #: honest about what is not in the email.
    live_url: str | None = None
    #: The content column width. 600px is the conventional safe maximum: it is what the
    #: Outlook reading pane fits without horizontal scroll.
    max_width_px: int = 600
    #: The inline font stack. Every text element carries it, because email has no cascade
    #: to inherit from.
    #:
    #: Deliberately UNQUOTED. CSS permits a multi-word family name as a sequence of
    #: identifiers, and quoting it would be escaped into an ``&#x27;`` entity on the way
    #: into the style attribute — which HTML4-era mail parsers render as literal text
    #: rather than decoding, so the whole declaration is discarded and the message falls
    #: back to the client's default serif.
    font_stack: str = "-apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif"
    #: The destination policy every TREE-AUTHORED ``href`` / ``src`` in the projection is
    #: checked against (WIRE_FORMAT §14.1). Defaults to deny-non-local, as everywhere else
    #: in this renderer.
    #:
    #: :attr:`live_url` is deliberately NOT subject to it: that URL is supplied by the host
    #: in this very record, so checking a host's own declaration against the host's own
    #: allowlist tests nothing and would make the common case ("point at my app") fail
    #: unless the host remembered to allowlist itself.
    egress_policy: EgressPolicy = DENY_NON_LOCAL_EGRESS


#: The conventional defaults: no live surface declared, a 600px column, a webfont-free
#: system stack (a webfont is an external asset request most clients refuse), deny-non-local.
DEFAULT_EMAIL_OPTIONS: Final = EmailOptions()

# ─── The inline style vocabulary ────────────────────────────────────────────
#
# Literal strings, not a token system. An email client that supports CSS variables is not
# the one the projection has to survive, and every declaration below is chosen for the
# intersection of what Gmail (which strips a great deal) and Outlook desktop (which lays
# out through Word) both honour.

INK_COLOUR: Final = "#1c1f23"
MUTED_COLOUR: Final = "#5b6570"
RULE_COLOUR: Final = "#dfe3e8"
PANEL_COLOUR: Final = "#f6f7f9"
LINK_COLOUR: Final = "#1f4f82"

_TONE_COLOURS: Final = {
    "Default": INK_COLOUR,
    "Subdued": MUTED_COLOUR,
    "Brand": "#1f4f82",
    "Success": "#1c6b45",
    "Warning": "#8a5a06",
    "Critical": "#9b2226",
    "Info": "#1f4f82",
}

_BADGE_COLOURS: Final = {
    "Neutral": MUTED_COLOUR,
    "Brand": "#1f4f82",
    "Success": "#1c6b45",
    "Warning": "#8a5a06",
    "Critical": "#9b2226",
    "Info": "#1f4f82",
}


def _tone_colour(tone: Value) -> str:
    return _TONE_COLOURS.get(tone, INK_COLOUR) if isinstance(tone, str) else INK_COLOUR


def _badge_colour(variant: Value) -> str:
    return _BADGE_COLOURS.get(variant, MUTED_COLOUR) if isinstance(variant, str) else MUTED_COLOUR


def _chunk(n: int, items: Sequence[str]) -> list[list[str]]:
    size = max(1, n)
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def _table_attrs(extra_style: str) -> list[tuple[str, str]]:
    """A presentation table.

    ``role="presentation"`` keeps a screen reader from announcing layout scaffolding as
    data; the three zeroed attributes are the HTML-4 forms Outlook honours where the CSS
    equivalents are ignored.
    """
    return [
        ("role", "presentation"),
        ("cellpadding", "0"),
        ("cellspacing", "0"),
        ("border", "0"),
        ("width", "100%"),
        ("style", "border-collapse:collapse;width:100%;" + extra_style),
    ]


def _layout_table(extra_style: str, rows: str) -> str:
    return element("table", _table_attrs(extra_style), element("tbody", [], rows))


def _stacked_row(cell_style: str, child: str) -> str:
    return element("tr", [], element("td", [("style", cell_style)], child))


def _stacked(children: Sequence[str]) -> str:
    """Vertical stacking, as one row per child in a layout table.

    Margins between block elements are the first thing a mail client's own reset takes
    away, so vertical rhythm is a table row with padding rather than a `margin-bottom`.
    """
    present = [c for c in children if c != ""]
    if not present:
        return ""
    return _layout_table("", "".join(_stacked_row("padding:0 0 12px 0;", c) for c in present))


# ─── Markdown, made email-safe ──────────────────────────────────────────────


def _strip_egress_markers(html: str) -> str:
    """Drop the egress-refusal markers, leaving the refused destination itself in place.

    ``data-*`` attributes do not survive the sanitisers most mail clients run, so a marker
    here is a signal that cannot be relied on to arrive. The refusal is NOT dropped — the
    destination is still the inert refusal URL, which is the half that stops it being
    reached.

    String surgery over a KNOWN shape: the marker's value is a class name and a normalised
    host, so it never contains a quote, and the renderer emits the attribute in exactly one
    spelling.
    """
    needle = " " + EGRESS_REFUSAL_ATTRIBUTE + '="'
    out = html
    while True:
        i = out.find(needle)
        if i < 0:
            return out
        close = out.find('"', i + len(needle))
        if close < 0:
            return out
        out = out[:i] + out[close + 1 :]


#: The task-list control the GFM renderer emits, and the ballot glyph it becomes.
#:
#: A disabled ``<input type="checkbox">`` is a form control — invisible in several clients,
#: and against this projection's own rule — so it is substituted for a glyph every client
#: draws. Pinned as data so the lint's ``EMAIL-CONTROL`` rule and this substitution cannot
#: drift apart silently.
_TASK_CHECKBOX_SUBSTITUTIONS: Final = (
    ('<input class="fuaran-task-checkbox" checked="" disabled="" type="checkbox" /> ', "&#9745; "),
    ('<input class="fuaran-task-checkbox" disabled="" type="checkbox" /> ', "&#9744; "),
)


def email_safe_markdown(policy: EgressPolicy, source: str) -> str:
    """Render a markdown body to email-safe HTML.

    The GFM renderer escapes raw HTML by construction, so its output is a closed tag set —
    with the one task-list exception above. The body's own link and image destinations are
    policy-checked with the digest's declared policy, with the marker stripped per the rule
    above: a digest is the surface where an undeclared markdown image IS the tracking pixel,
    so leaving this one unchecked would be the largest remaining hole rather than the
    smallest.
    """
    html = _strip_egress_markers(sanitize_markdown_html(_markdown.to_html_with_egress(policy, source)))
    for control, glyph in _TASK_CHECKBOX_SUBSTITUTIONS:
        html = html.replace(control, glyph)
    return html


# ─── The renderer ───────────────────────────────────────────────────────────

_HEADING_SCALE: Final = {
    1: ("26px", "700"),
    2: ("22px", "700"),
    3: ("18px", "600"),
    4: ("16px", "600"),
    5: ("15px", "600"),
}


class _EmailRenderer:
    """The per-render context: options, host binding sources, and the fragment registry."""

    def __init__(self, options: EmailOptions, sources: BindingSources | None, fragments: dict[str, Node]) -> None:
        self.options = options
        self.sources = sources
        self.fragments = fragments

    # ── text + style helpers ────────────────────────────────────────────────

    def _text(self, ts: Value) -> str:
        return render_text(ts, self.sources)

    def _text_in(self, colour: str, extra: str) -> str:
        """The base text declarations every text-bearing element carries, in an explicit colour.

        Email has no inheritance worth relying on, so this is repeated on each element
        rather than hoisted to a parent. Colour is a PARAMETER rather than something a
        caller appends, so a declaration block carries exactly one ``color:``. Two would be
        legal CSS — last wins — but this is the one place in the stack where "legal CSS" and
        "what the client does" part company: several mail parsers keep the FIRST
        declaration, which would silently invert the intended emphasis.
        """
        return f"margin:0;font-family:{self.options.font_stack};color:{colour};{extra}"

    def _text_style(self, extra: str) -> str:
        return self._text_in(INK_COLOUR, extra)

    # ── the open-live affordance ────────────────────────────────────────────

    def _live_label(self, node: Node, fallback: str) -> str:
        """The label an open-live affordance carries.

        The node's declared accessible label wins where it resolves — it is the author's
        own words for what this is — and the kind name is the honest fallback.
        """
        a11y = node.extras.get("accessibility")
        if isinstance(a11y, Obj):
            label = _a11y_name(a11y.fields.get("label"), self.sources)
            if label:
                return label
        return fallback

    def _open_live(self, node: Node, fallback: str) -> str:
        label = self._live_label(node, fallback)
        style = self._text_in(
            LINK_COLOUR, "display:inline-block;font-size:14px;line-height:20px;text-decoration:underline;"
        )
        if self.options.live_url is not None:
            href = sanitize_url_or_blank(self.options.live_url + "#" + node.id)
            return text_element(
                "a",
                [("style", style), ("href", href), ("data-fuaran-email-open-live", node.id)],
                label + " — open live",
            )
        # No live surface declared. Say what is not here rather than emitting a dead
        # anchor: the reader learns the digest is partial, which is true.
        return text_element(
            "span",
            [
                ("style", self._text_in(MUTED_COLOUR, "font-size:14px;line-height:20px;")),
                ("data-fuaran-email-open-live", node.id),
            ],
            label + " — available in the live view",
        )

    # ── the walk ────────────────────────────────────────────────────────────

    def render(self, node: Node, depth: int = 1) -> str:
        if depth > MAX_NODE_DEPTH:
            return text_element(
                "p",
                [("style", self._text_in(MUTED_COLOUR, "font-size:13px;"))],
                f"[subtree omitted: nesting exceeds the wire limit MaxDepth = {MAX_NODE_DEPTH}]",
            )
        # fuaran#1535 — conditional presence. A digest that shows a node the page
        # removed is not a digest of the page.
        if not is_node_visible(node.extras.get("visible"), self.sources):
            return ""
        kind = node.kind
        handler = _DISPATCH.get(kind.tag or "")
        if handler is None:
            # A kind with no row is outside the canonical set the completeness test pins.
            # A labelled note, never a blank: an unknown kind is a fact worth carrying.
            return text_element(
                "p",
                [("style", self._text_in(MUTED_COLOUR, "font-size:13px;"))],
                f"[fuaran:unprojected kind '{kind.tag or ''}']",
            )
        return handler(self, node, kind.fields, depth)

    def _children(self, fields: dict[str, Value], depth: int) -> list[str]:
        return [self.render(child, depth + 1) for child in _child_nodes(fields)]

    # ── Structure ───────────────────────────────────────────────────────────

    def _box(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        kids = self._children(fields, depth)
        role = fields.get("role")
        layout = fields.get("layout")
        layout_obj = layout if isinstance(layout, Obj) else None
        layout_mode = layout_obj.tag if layout_obj is not None else None
        layout_fields = layout_obj.fields if layout_obj is not None else {}

        if role == "Separator":
            # A bordered, zero-height cell rather than `<hr>`: Word's engine gives `<hr>` a
            # margin no declaration reliably removes.
            return _layout_table(
                "",
                element(
                    "tr",
                    [],
                    element(
                        "td",
                        [("style", f"border-top:1px solid {RULE_COLOUR};font-size:0;line-height:0;height:1px;")],
                        " ",
                    ),
                ),
            )
        if role == "Card":
            heading = fields.get("heading")
            heading_row = ""
            if heading is not None:
                heading_row = element(
                    "tr",
                    [],
                    element(
                        "td",
                        [
                            (
                                "style",
                                f"padding:12px 16px;border-bottom:1px solid {RULE_COLOUR};background:{PANEL_COLOUR};",
                            )
                        ],
                        text_element(
                            "p",
                            [("style", self._text_style("font-size:15px;line-height:20px;font-weight:600;"))],
                            self._text(heading),
                        ),
                    ),
                )
            body_row = element("tr", [], element("td", [("style", "padding:16px;")], _stacked(kids)))
            return _layout_table(f"border:1px solid {RULE_COLOUR};", heading_row + body_row)
        if role == "Group" and layout_mode in ("Grid", "Masonry"):
            # The one place a table earns its keep: N-across KPI rows, which is exactly the
            # shape a digest opens with, and which flex/grid cannot express in an inbox at
            # all. Masonry rides the same emission: column-fill is a browser capability with
            # no email equivalent, and an N-across grid is the closest honest reading.
            cols = layout_fields.get("cols")
            columns = max(1, cols if isinstance(cols, int) and not isinstance(cols, bool) else 1)
            width = f"{100 // columns}%"
            rows = "".join(
                element(
                    "tr",
                    [],
                    "".join(
                        element(
                            "td",
                            [
                                ("width", width),
                                ("valign", "top"),
                                ("style", f"padding:0 8px 12px 0;vertical-align:top;width:{width}"),
                            ],
                            kid,
                        )
                        for kid in row_kids
                    ),
                )
                for row_kids in _chunk(columns, kids)
            )
            return _layout_table("", rows)
        if role == "Group" and layout_mode == "Flex" and layout_fields.get("direction") == "Horizontal":
            count = max(1, len(kids))
            width = f"{100 // count}%"
            cells = "".join(
                element(
                    "td",
                    [
                        ("width", width),
                        ("valign", "top"),
                        ("style", f"padding:0 8px 0 0;vertical-align:top;width:{width}"),
                    ],
                    kid,
                )
                for kid in kids
            )
            return _layout_table("", element("tr", [], cells))
        return _stacked(kids)

    def _split_panel(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        kids = self._children(fields, depth)
        raw_weight = fields.get("weight")
        weight = raw_weight if isinstance(raw_weight, (int, float)) and not isinstance(raw_weight, bool) else 0.5
        left_pct = int(round(max(0.0, min(1.0, float(weight))) * 100.0))
        left, right = (kids[:1], kids[1:]) if kids else ([], [])
        return _layout_table(
            "",
            element(
                "tr",
                [],
                element(
                    "td",
                    [("width", f"{left_pct}%"), ("valign", "top"), ("style", "padding:0 8px 0 0;vertical-align:top;")],
                    _stacked(left),
                )
                + element(
                    "td",
                    [("width", f"{100 - left_pct}%"), ("valign", "top"), ("style", "padding:0;vertical-align:top;")],
                    _stacked(right),
                ),
            ),
        )

    def _summary_list(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        kids = self._children(fields, depth)
        heading = fields.get("heading")
        head = (
            [
                text_element(
                    "p",
                    [("style", self._text_style("font-size:15px;line-height:20px;font-weight:600;"))],
                    self._text(heading),
                )
            ]
            if heading is not None
            else []
        )
        return _stacked(head + kids)

    def _disclosure(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        # Always expanded. `<details>` is inert in Outlook, so a collapsed section would be
        # content the reader never learns exists.
        heading = text_element(
            "p",
            [("style", self._text_style("font-size:15px;line-height:20px;font-weight:600;"))],
            self._text(fields.get("heading")),
        )
        return _stacked([heading, *self._children(fields, depth)])

    def _scroll_area(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        return _stacked(self._children(fields, depth))

    def _error_boundary(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        child = _as_node(fields.get("child"))
        return self.render(child, depth + 1) if child is not None else ""

    def _switch(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        # The same branch the HTML render picks, resolved through the same sources, so the
        # digest and the page it links to show the same case.
        state_key = fields.get("stateKey")
        current: object | None = None
        if isinstance(state_key, str) and self.sources is not None and state_key in self.sources:
            current = self.sources[state_key]
        elif "on" in fields:
            # fuaran#1535 — the Phase-768 ``on`` form, resolved through the SCALAR
            # path exactly as the HTML renderer does, so this projection picks the
            # same branch the page shows.
            current = resolve_scalar_text(fields["on"], self.sources)
        value_str = None if current is None else str(current)
        cases = fields.get("cases")
        # fuaran#1535 — the one shared case-selection definition, so a predicate
        # case selects here too and this projection cannot drift from the page.
        child = _as_node(select_switch_case(cases, value_str, self.sources))
        if child is not None:
            return self.render(child, depth + 1)
        default = _as_node(fields.get("default"))
        return self.render(default, depth + 1) if default is not None else ""

    def _fragment_ref(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        name = fields.get("name")
        if isinstance(name, str):
            body = self.fragments.get(name)
            if body is not None:
                return self.render(body, depth + 1)
            return text_element(
                "p",
                [("style", self._text_in(MUTED_COLOUR, "font-size:13px;"))],
                f"[fuaran:fragment unresolved '{name}']",
            )
        return ""

    # ── Display ─────────────────────────────────────────────────────────────

    def _heading(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        raw_level = fields.get("level")
        level = raw_level if isinstance(raw_level, int) and not isinstance(raw_level, bool) else 2
        level = max(1, min(6, level))
        size, weight = _HEADING_SCALE.get(level, ("14px", "600"))
        variant = fields.get("variant")
        if variant == "Eyebrow":
            style = self._text_in(
                MUTED_COLOUR,
                "font-size:12px;line-height:16px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;",
            )
        elif variant == "Caption":
            style = self._text_in(MUTED_COLOUR, "font-size:13px;line-height:18px;font-weight:400;")
        elif variant == "Lead":
            style = self._text_style("font-size:18px;line-height:26px;font-weight:400;")
        else:
            style = self._text_style(f"font-size:{size};line-height:1.3;font-weight:{weight};")
        return text_element(f"h{level}", [("style", style)], self._text(fields.get("text")))

    def _markdown(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        body = email_safe_markdown(self.options.egress_policy, self._text(fields.get("text")))
        return _layout_table(
            "",
            element(
                "tr",
                [],
                element("td", [("style", self._text_style("font-size:15px;line-height:22px;"))], body),
            ),
        )

    def _metric(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        colour = _tone_colour(fields.get("tone"))
        value = resolve_scalar_number(fields.get("value"), self.sources)
        value_text = format_number(fields.get("format"), value) if value is not None else _EM_DASH
        parts = [
            text_element(
                "p",
                [
                    (
                        "style",
                        self._text_in(
                            MUTED_COLOUR,
                            "font-size:12px;line-height:16px;letter-spacing:0.06em;text-transform:uppercase;",
                        ),
                    )
                ],
                self._text(fields.get("label")),
            ),
            text_element(
                "p",
                [("style", self._text_in(colour, "font-size:28px;line-height:34px;font-weight:700;padding-top:4px;"))],
                value_text,
            ),
        ]
        trend_binding = fields.get("trend")
        if trend_binding is not None:
            trend = resolve_scalar_number(trend_binding, self.sources)
            if trend is not None:
                parts.append(
                    text_element(
                        "p",
                        [("style", self._text_in(colour, "font-size:13px;line-height:18px;"))],
                        format_number(fields.get("trendFormat"), trend),
                    )
                )
        subtext = fields.get("subtext")
        if subtext is not None:
            parts.append(
                text_element(
                    "p",
                    [("style", self._text_in(MUTED_COLOUR, "font-size:13px;line-height:18px;"))],
                    self._text(subtext),
                )
            )
        return _layout_table(
            f"border:1px solid {RULE_COLOUR};background:{PANEL_COLOUR};",
            element("tr", [], element("td", [("style", "padding:14px 16px;")], "".join(parts))),
        )

    def _fact(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        weight = "700" if fields.get("emphasis") is True else "600"
        parts = [
            text_element(
                "p",
                [("style", self._text_in(MUTED_COLOUR, "font-size:12px;line-height:16px;"))],
                self._text(fields.get("label")),
            ),
            text_element(
                "p",
                [
                    (
                        "style",
                        self._text_in(
                            _tone_colour(fields.get("tone")), f"font-size:16px;line-height:22px;font-weight:{weight};"
                        ),
                    )
                ],
                self._text(fields.get("value")),
            ),
        ]
        help_text = fields.get("help")
        if help_text is not None:
            parts.append(
                text_element(
                    "p",
                    [("style", self._text_in(MUTED_COLOUR, "font-size:12px;line-height:16px;"))],
                    self._text(help_text),
                )
            )
        return _layout_table("", element("tr", [], element("td", [("style", "padding:0;")], "".join(parts))))

    def _label_value_row(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        value = resolve_scalar_number(fields.get("value"), self.sources)
        value_text = format_number(fields.get("format"), value) if value is not None else _EM_DASH
        weight = "700" if fields.get("emphasis") is True else "400"
        label_cell = text_element(
            "td",
            [
                (
                    "style",
                    f"padding:6px 0;border-bottom:1px solid {RULE_COLOUR};"
                    + self._text_style("font-size:14px;line-height:20px;"),
                )
            ],
            self._text(fields.get("label")),
        )
        value_cell = text_element(
            "td",
            [
                ("align", "right"),
                (
                    "style",
                    f"padding:6px 0;text-align:right;border-bottom:1px solid {RULE_COLOUR};"
                    + self._text_style(f"font-size:14px;line-height:20px;font-weight:{weight};"),
                ),
            ],
            value_text,
        )
        return _layout_table("", element("tr", [], label_cell + value_cell))

    def _badge(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        colour = _badge_colour(fields.get("variant"))
        return text_element(
            "span",
            [
                (
                    "style",
                    self._text_in(
                        colour,
                        f"display:inline-block;padding:2px 8px;border:1px solid {colour};"
                        "font-size:12px;line-height:18px;",
                    ),
                )
            ],
            self._text(fields.get("label")),
        )

    def _callout(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        colour = _tone_colour(fields.get("tone"))
        heading = fields.get("heading")
        parts = []
        if heading is not None:
            parts.append(
                text_element(
                    "p",
                    [("style", self._text_in(colour, "font-size:15px;line-height:20px;font-weight:600;"))],
                    self._text(heading),
                )
            )
        parts.append(
            text_element(
                "p",
                [("style", self._text_style("font-size:14px;line-height:20px;padding-top:2px;"))],
                self._text(fields.get("body")),
            )
        )
        return _layout_table(
            f"border:1px solid {RULE_COLOUR};border-left:4px solid {colour};background:{PANEL_COLOUR};",
            element("tr", [], element("td", [("style", "padding:12px 16px;")], "".join(parts))),
        )

    def _list(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        raw = fields.get("items")
        items = raw.items if isinstance(raw, Arr) else []
        body = "".join(
            text_element(
                "li",
                [("style", self._text_style("font-size:15px;line-height:22px;padding-bottom:4px;"))],
                self._text(item),
            )
            for item in items
        )
        tag = "ol" if fields.get("ordered") is True else "ul"
        return element(tag, [("style", "margin:0;padding:0 0 0 22px;")], body)

    def _link(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        # The one genuine interaction an email has, and it passes the same gate the HTML
        # anchor does — the ambient destination policy, read off the digest's options.
        #
        # The refusal ATTRIBUTE is deliberately dropped here where the HTML anchor keeps it,
        # per `_strip_egress_markers`' rule. The refusal itself is not dropped — the href
        # still becomes the inert refusal URL, which is the part that stops the destination
        # being reached.
        resolved = resolve_binding(fields.get("href"), self.sources)
        safe_href, _ = sanitize_url_for_egress(
            self.options.egress_policy, EgressClass.HYPERLINK, resolved if isinstance(resolved, str) else ""
        )
        # A protected email link entity-encodes its address so no plaintext address sits in
        # the document source. That defence is aimed at crawlers over a PUBLIC page; an
        # email is already addressed to one reader, and several clients rewrite anchors on
        # delivery. The projection therefore emits the ordinary anchor and keeps the
        # sanitisation, rather than shipping an entity blob a client may mangle.
        return text_element(
            "a",
            [
                ("style", self._text_in(LINK_COLOUR, "font-size:15px;line-height:22px;text-decoration:underline;")),
                ("href", safe_href),
            ],
            self._text(fields.get("label")),
        )

    def _image(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        # In a digest this is precisely the tracking pixel: an undeclared `src` here is
        # fetched by each recipient's client on open, reporting that this named person read
        # this message. Hence the policy, not merely the scheme floor.
        resolved = resolve_binding(fields.get("src"), self.sources)
        safe_src, _ = sanitize_url_for_egress(
            self.options.egress_policy, EgressClass.MEDIA, resolved if isinstance(resolved, str) else ""
        )
        return void_element(
            "img",
            [
                ("src", safe_src),
                ("alt", self._text(fields.get("alt"))),
                ("border", "0"),
                ("style", "display:block;max-width:100%;height:auto;border:0;outline:none;"),
            ],
        )

    def _progress(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        raw = resolve_binding(fields.get("fraction"), self.sources)
        fraction = (
            max(0.0, min(1.0, float(raw))) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0.0
        )
        pct = int(round(fraction * 100.0))
        colour = _tone_colour(fields.get("tone"))
        label = fields.get("label")
        caption = f"{self._text(label)} — {pct}%" if label is not None else f"{pct}%"
        label_el = text_element(
            "p", [("style", self._text_style("font-size:13px;line-height:18px;padding-bottom:4px;"))], caption
        )
        # The percentage rides as TEXT as well as bar geometry: a client that strips
        # background colours then still conveys the figure.
        cells = ""
        if pct > 0:
            cells += element(
                "td",
                [
                    ("width", f"{pct}%"),
                    ("style", f"width:{pct}%;background:{colour};font-size:0;line-height:0;height:8px;"),
                ],
                " ",
            )
        if pct < 100:
            cells += element(
                "td",
                [
                    ("width", f"{100 - pct}%"),
                    ("style", f"width:{100 - pct}%;background:{PANEL_COLOUR};font-size:0;line-height:0;height:8px;"),
                ],
                " ",
            )
        bar = _layout_table(f"border:1px solid {RULE_COLOUR};", element("tr", [], cells))
        return _stacked([label_el, bar])

    def _code_block(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        code = fields.get("code")
        return element(
            "pre",
            [
                (
                    "style",
                    f"margin:0;padding:12px;background:{PANEL_COLOUR};border:1px solid {RULE_COLOUR};"
                    "font-family:Consolas, Courier New, monospace;font-size:13px;line-height:18px;"
                    f"color:{INK_COLOUR};white-space:pre-wrap;",
                )
            ],
            text_element("code", [], code if isinstance(code, str) else ""),
        )

    def _math(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        # The declared narrowing: readable LaTeX rather than MathML no Word-engine client
        # will draw.
        source = fields.get("source")
        source = source if isinstance(source, str) else ""
        return text_element(
            "span",
            [
                (
                    "style",
                    self._text_style("font-family:Consolas, Courier New, monospace;font-size:14px;line-height:20px;"),
                ),
                ("data-fuaran-math-src", source),
            ],
            source,
        )

    def _toast(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        if resolve_binding(fields.get("open"), self.sources) is not True:
            # OMITTED, not the `hidden` attribute. `hidden` is unreliable across clients,
            # and a notification that leaks into a digest it was closed in is a disclosure
            # bug, not a cosmetic one.
            return ""
        colour = _tone_colour(fields.get("tone"))
        return _layout_table(
            f"border:1px solid {colour};background:{PANEL_COLOUR};",
            element(
                "tr",
                [],
                element(
                    "td",
                    [("style", "padding:10px 14px;")],
                    text_element(
                        "p",
                        [("style", self._text_in(colour, "font-size:14px;line-height:20px;"))],
                        self._text(fields.get("message")),
                    ),
                ),
            ),
        )

    def _static_table(self, headers: Sequence[Value], rows: Sequence[Value]) -> str:
        header_cells = "".join(
            text_element(
                "th",
                [
                    ("align", "left"),
                    (
                        "style",
                        f"padding:8px 10px;text-align:left;border:1px solid {RULE_COLOUR};background:{PANEL_COLOUR};"
                        + self._text_style("font-size:13px;line-height:18px;font-weight:600;"),
                    ),
                ],
                self._text(h),
            )
            for h in headers
        )
        body_rows = "".join(
            element(
                "tr",
                [],
                "".join(
                    text_element(
                        "td",
                        [
                            (
                                "style",
                                f"padding:8px 10px;border:1px solid {RULE_COLOUR};"
                                + self._text_style("font-size:13px;line-height:18px;"),
                            )
                        ],
                        self._text(cell),
                    )
                    for cell in row.items
                ),
            )
            for row in rows
            if isinstance(row, Arr)
        )
        return element(
            "table",
            _table_attrs(""),
            element("thead", [], element("tr", [], header_cells)) + element("tbody", [], body_rows),
        )

    def _data_grid(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        static_rows = fields.get("staticRows")
        if isinstance(static_rows, Obj):
            return self._table(node, static_rows.fields, depth)
        # A bound grid whose rows and field-projected columns both resolve renders them —
        # the completeness posture the HTML renderer takes, and a digest is precisely the
        # no-JS surface that can never recover what a placeholder withholds. A closure
        # projected cell cannot survive the wire, so it renders empty rather than absent.
        resolved = resolve_source(fields.get("source"), self.sources)
        columns = fields.get("columns")
        cols = [c for c in (columns.items if isinstance(columns, Arr) else []) if isinstance(c, Obj)]
        if isinstance(resolved, Arr) and any(isinstance(c.fields.get("field"), str) for c in cols):
            bound_headers: list[Value] = [c.fields.get("header") for c in cols]
            bound_rows: list[Value] = []
            for row in resolved.items:
                cells: list[Value] = []
                for c in cols:
                    field_name = c.fields.get("field")
                    cell = row.fields.get(field_name) if isinstance(row, Obj) and isinstance(field_name, str) else None
                    cells.append("" if cell is None else cell)
                bound_rows.append(Arr(cells))
            return self._static_table(bound_headers, bound_rows)
        # Nothing server-side to draw. Link.
        return self._open_live(node, "Table")

    def _table(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        headers = fields.get("headers")
        rows = fields.get("rows")
        return self._static_table(
            headers.items if isinstance(headers, Arr) else [],
            rows.items if isinstance(rows, Arr) else [],
        )

    # ── Interactive + client-drawn: labelled open-live links, never controls ─

    def _chart(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        title = fields.get("title")
        label = self._text(title) if title is not None else ""
        return self._open_live(node, label if label != "" else "Chart")

    def _media(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        kind = fields.get("kind")
        surface = "Audio" if isinstance(kind, Obj) and kind.tag == "Audio" else "Video"
        # The node's mandatory `label` is a sentence describing what the player plays,
        # which is what an "open live" link wants to say anyway.
        label = self._text(fields.get("label"))
        return self._open_live(node, label if label != "" else surface)

    def _embed(self, node: Node, fields: dict[str, Value], depth: int) -> str:
        title = self._text(fields.get("title"))
        return self._open_live(node, title if title != "" else "Embedded view")


def _live(fallback: str):
    """A handler that projects the node to a labelled open-live affordance."""

    def handler(renderer: _EmailRenderer, node: Node, fields: dict[str, Value], depth: int) -> str:
        return renderer._open_live(node, fallback)

    return handler


def _nothing(renderer: _EmailRenderer, node: Node, fields: dict[str, Value], depth: int) -> str:
    return ""


_DISPATCH: Final = {
    # Structure
    "Box": _EmailRenderer._box,
    "SplitPanel": _EmailRenderer._split_panel,
    "SummaryList": _EmailRenderer._summary_list,
    "Disclosure": _EmailRenderer._disclosure,
    "ScrollArea": _EmailRenderer._scroll_area,
    "ErrorBoundary": _EmailRenderer._error_boundary,
    "Switch": _EmailRenderer._switch,
    "FragmentRef": _EmailRenderer._fragment_ref,
    "FragmentDecl": _nothing,
    # Display
    "Heading": _EmailRenderer._heading,
    "Markdown": _EmailRenderer._markdown,
    "Metric": _EmailRenderer._metric,
    "Fact": _EmailRenderer._fact,
    "LabelValueRow": _EmailRenderer._label_value_row,
    "Badge": _EmailRenderer._badge,
    "Callout": _EmailRenderer._callout,
    "List": _EmailRenderer._list,
    "Link": _EmailRenderer._link,
    "Image": _EmailRenderer._image,
    "Progress": _EmailRenderer._progress,
    "CodeBlock": _EmailRenderer._code_block,
    "Math": _EmailRenderer._math,
    "Toast": _EmailRenderer._toast,
    "DataGrid": _EmailRenderer._data_grid,
    # `Table` is not a canonical wire kind — it was folded into `DataGrid.staticRows` —
    # but this host still renders a decoded `Table` for the trees that predate the fold,
    # so the digest answers it the same way rather than falling to the unprojected note.
    "Table": _EmailRenderer._table,
    # Interactive + client-drawn
    "Button": _live("Action"),
    "Form": _live("Form"),
    "Select": _live("Selection"),
    "FileUpload": _live("File upload"),
    "Filters": _live("Filters"),
    "Tabs": _live("Tabbed section"),
    "Stepper": _live("Step-by-step section"),
    "Modal": _live("Dialog"),
    "Chart": _EmailRenderer._chart,
    "Map": _live("Map"),
    "Media": _EmailRenderer._media,
    "Embed": _EmailRenderer._embed,
    "Tree": _live("Hierarchy"),
    "Sparkline": _live("Trend"),
    "Drawing": _live("Diagram"),
    "Custom": _live("Component"),
    "Mount": _live("Embedded view"),
    # Zero-paint
    "Icon": _nothing,
    "Skeleton": _nothing,
}

# ─── Email-hostile-construct lint ───────────────────────────────────────────
#
# The client-matrix question ("does Outlook draw this?") cannot be answered offline, and
# this lint does not pretend to answer it. What it DOES answer is the falsifiable half:
# whether the emitted HTML contains a construct the client matrix is already known to break
# on. A clean lint is not a claim of email-safety; a dirty one is proof of the opposite, and
# that asymmetry is worth automating.


@dataclass(frozen=True)
class LintFinding:
    """One finding.

    ``construct`` is the literal token matched, so a finding is reproducible by grep rather
    than by re-running the scanner.
    """

    code: str
    construct: str
    detail: str


HOSTILE_CONSTRUCTS: Final[tuple[tuple[str, str, str], ...]] = (
    (
        "EMAIL-FLEX",
        "display:flex",
        "flexbox is unsupported in Outlook's Word engine and unreliable in several webmail clients",
    ),
    ("EMAIL-FLEX", "display:inline-flex", "as EMAIL-FLEX"),
    ("EMAIL-FLEX", "flex-direction", "a flex declaration implies a flex container"),
    ("EMAIL-GRID", "display:grid", "CSS grid has no meaningful support in email; lay out with tables"),
    ("EMAIL-GRID", "grid-template", "a grid declaration implies a grid container"),
    ("EMAIL-GRID", "gap:", "the gap shorthand only applies to flex/grid containers, so its presence implies one"),
    ("EMAIL-POSITION", "position:fixed", "positioning is stripped or ignored; content escapes the message body"),
    ("EMAIL-POSITION", "position:absolute", "as EMAIL-POSITION"),
    ("EMAIL-POSITION", "position:sticky", "as EMAIL-POSITION"),
    ("EMAIL-EXTERNAL-CSS", "<link", "external stylesheets are not fetched; every rule must be inline"),
    (
        "EMAIL-EXTERNAL-CSS",
        "<style",
        "embedded style blocks are stripped by Gmail and others; every rule must be inline",
    ),
    ("EMAIL-EXTERNAL-CSS", "@import", "as EMAIL-EXTERNAL-CSS"),
    ("EMAIL-EXTERNAL-CSS", "var(--", "CSS custom properties do not resolve in the clients this projection targets"),
    ("EMAIL-SCRIPT", "<script", "scripts never execute in an email client and mark the message as suspicious"),
    ("EMAIL-SCRIPT", "javascript:", "as EMAIL-SCRIPT"),
    ("EMAIL-SCRIPT", " onclick=", "inline event handlers never fire"),
    ("EMAIL-SCRIPT", " onload=", "as EMAIL-SCRIPT"),
    ("EMAIL-SCRIPT", " onerror=", "as EMAIL-SCRIPT"),
    ("EMAIL-CONTROL", "<form", "a form that cannot post is worse than an absent one - project to an open-live link"),
    ("EMAIL-CONTROL", "<button", "a button that cannot fire is a half-working control"),
    ("EMAIL-CONTROL", "<input", "as EMAIL-CONTROL"),
    ("EMAIL-CONTROL", "<select", "as EMAIL-CONTROL"),
    ("EMAIL-CONTROL", "<textarea", "as EMAIL-CONTROL"),
    ("EMAIL-EMBED", "<iframe", "embedded documents are stripped by every mainstream client"),
    ("EMAIL-EMBED", "<svg", "Outlook's Word engine does not draw inline SVG - link to the live view instead"),
    ("EMAIL-EMBED", "<canvas", "as EMAIL-EMBED"),
    ("EMAIL-EMBED", "<video", "as EMAIL-EMBED"),
    ("EMAIL-EMBED", "<audio", "as EMAIL-EMBED"),
    ("EMAIL-EMBED", "<object", "as EMAIL-EMBED"),
    ("EMAIL-EMBED", "<embed", "as EMAIL-EMBED"),
    (
        "EMAIL-DIV-LAYOUT",
        "<div",
        "this projection lays out entirely in tables, so a div is evidence of an unaudited emission path",
    ),
    # Found by reading the reference host's first generated golden rather than by
    # reasoning: a quoted CSS font family is escaped to an apostrophe entity on the way
    # into the style attribute, and an HTML4-era mail parser prints that literally instead
    # of decoding it — which invalidates the whole declaration and drops the message to the
    # client's default serif.
    (
        "EMAIL-ENTITY-QUOTE",
        "&#x27;",
        "an apostrophe entity inside a style attribute is not decoded by HTML4-era mail parsers; "
        "use an unquoted CSS identifier sequence",
    ),
    ("EMAIL-ENTITY-QUOTE", "&apos;", "as EMAIL-ENTITY-QUOTE"),
    ("EMAIL-ENTITY-QUOTE", "&#39;", "as EMAIL-ENTITY-QUOTE"),
)


def lint(html: str) -> list[LintFinding]:
    """Scan emitted HTML for constructs the client matrix is known to break on.

    An ordinal, case-insensitive substring scan — deliberately, not a parser. It is a smoke
    detector over output THIS module produced, not a sanitiser for arbitrary HTML, and it is
    sound in that direction only: it cannot certify a document, and every finding is a
    genuine construct present in the bytes.
    """
    if not html:
        return []
    lowered = html.lower()
    return [LintFinding(code, token, detail) for code, token, detail in HOSTILE_CONSTRUCTS if token.lower() in lowered]


# ─── Public entry points ────────────────────────────────────────────────────


def render_email(
    node: Node,
    sources: BindingSources | None = None,
    options: EmailOptions = DEFAULT_EMAIL_OPTIONS,
) -> str:
    """Render a tree to an email-safe body fragment — the content column, ready to drop
    inside a host's own ``<body>`` or a mock-inbox frame.

    ``sources`` resolves non-``Static`` bindings exactly as
    :func:`~fuaran_py.renderer.render_html` does, including the state seeds a tree declares
    for itself, so a digest and the page it links to read the same values.
    """
    fragments: dict[str, Node] = {}
    _collect_fragments(node, fragments)
    seeded = with_state_seeds(node, sources)
    body = _EmailRenderer(options, seeded, fragments).render(node)

    column = element(
        "table",
        [
            ("role", "presentation"),
            ("cellpadding", "0"),
            ("cellspacing", "0"),
            ("border", "0"),
            ("width", str(options.max_width_px)),
            (
                "style",
                "border-collapse:collapse;width:100%;"
                f"max-width:{options.max_width_px}px;margin:0 auto;background:#ffffff;",
            ),
        ],
        element("tbody", [], element("tr", [], element("td", [("style", "padding:24px;")], body))),
    )
    # The outer 100%-width table is the conventional centring wrapper: `margin:auto` alone
    # does not centre in Outlook, and `align="center"` does.
    return element(
        "table",
        _table_attrs(f"background:{PANEL_COLOUR};"),
        element(
            "tbody",
            [],
            element(
                "tr",
                [],
                element("td", [("align", "center"), ("style", "padding:16px;text-align:center;")], column),
            ),
        ),
    )


def render_email_document(
    node: Node,
    subject: str,
    sources: BindingSources | None = None,
    options: EmailOptions = DEFAULT_EMAIL_OPTIONS,
) -> str:
    """A complete, sendable email document: doctype, the two meta tags every client wants,
    a ``<title>``, and the body fragment.

    A fragment is not an email; this is the entry a digest sender uses. The subject rides
    the ``<title>`` only — the envelope's Subject header is the sender's concern, not the
    renderer's.
    """
    head = (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f"<title>{escape_text(subject)}</title>\n"
        f'</head>\n<body style="margin:0;padding:0;background:{PANEL_COLOUR};">\n'
    )
    return head + render_email(node, sources, options) + "\n</body>\n</html>"


__all__ = [
    "DEFAULT_EMAIL_OPTIONS",
    "HOSTILE_CONSTRUCTS",
    "INK_COLOUR",
    "LINK_COLOUR",
    "MUTED_COLOUR",
    "PANEL_COLOUR",
    "RULE_COLOUR",
    "SCOPE",
    "EmailOptions",
    "LintFinding",
    "disposition_for",
    "email_safe_markdown",
    "lint",
    "render_email",
    "render_email_document",
]
