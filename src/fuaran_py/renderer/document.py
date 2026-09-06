"""The markdown render projection — the crawlable document.

The third thing a tree can be. The HTML renderer produces a page a browser paints and a
client hydrates; :mod:`fuaran_py.renderer.email` produces a digest an inbox survives; this
produces **markdown** — the form a tree takes when it has to be read, diffed, committed,
pasted into an issue, or indexed by something that will never run JavaScript.

**No reference host has one.** The audit that opened this work found a markdown projection
in none of the language tiers: what every host carries is the *opposite* direction —
WIRE_FORMAT §14's deterministic GFM **→ HTML** renderer for the ``Markdown`` node's own
body. So there is nothing to port and nothing to be byte-parity with; this module is the
first, and it is written to be the thing a second host ports rather than a convenience that
would have to be re-derived. The declared :data:`SCOPE` table is the load-bearing half of
that: it is what a second implementation would agree with.

**What it emits is §14's own IN bucket, and that is a constraint rather than a coincidence.**
The markdown this projection produces is CommonMark core plus GFM tables, so
``markdown.to_html`` — the renderer every host already certifies against the shared corpus —
is a valid reader of it. That closes the loop: a document projection whose output the
project's own markdown renderer could not read would be markdown in name only.

**Charts, and the one place the loop does not close.** §14 escapes raw HTML by construction
— there is no passthrough — so an ``<svg>`` element embedded in a markdown body renders as
visible angle brackets through that renderer, and through GitHub's, and through most others.
That is exactly why :class:`MarkdownOptions` carries ``charts``:

* ``"svg"`` (the default) lowers a resolved chart through the host's own ``Chart`` →
  ``Drawing`` lowering and inlines the resulting SVG as raw HTML. Right for a document
  destined for a renderer that passes HTML through, and the only projection that carries the
  picture itself.
* ``"table"`` emits the chart's resolved rows as a GFM table under an italic caption naming
  it. Right for §14, for a plain-text reader, and for a diff. Nothing is lost that the tree
  did not already carry as data — which is the whole argument for lowering charts from data
  in the first place.

Neither is the "safe" one and neither is a fallback: they are two honest readings of a
picture, and the caller knows which reader is downstream. What the module refuses to do is
pick silently and leave a document that looks fine in one pipeline and prints tag soup in
another.

**Determinism.** Same tree, same options, same sources ⇒ same bytes. No clock, no id
minting, no iteration over an unordered collection. Text resolves through
:func:`~fuaran_py.renderer.bindings.render_text` and figures through
:func:`~fuaran_py.renderer.bindings.format_number` — the same functions the HTML page and
the digest use, so the three projections cannot disagree about what a number is.

**Destination policy applies here too.** A markdown link is a destination like any other,
so every ``href`` and image ``src`` is checked against the ambient policy (WIRE_FORMAT
§14.1), which defaults to deny-non-local exactly as it does everywhere else in this
renderer. A refused destination becomes the inert refusal URL; the ``data-*`` marker has no
markdown spelling, so the refusal travels as the URL alone, which is the half that stops the
destination being reached.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from ..limits import MAX_NODE_DEPTH
from ..model import Arr, Node, Obj, Value
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
    EgressClass,
    EgressPolicy,
    sanitize_url_for_egress,
)
from .projection import Disposition, disposition_of, omitted, open_live, rendered, structural

# Package-internal reuse, for the reason stated in :mod:`fuaran_py.renderer.email`: a second
# copy of the tree-walking helpers here would be a second answer to what counts as a child
# node, and the three projections must agree about that before they can agree about anything.
from .render import _EM_DASH, Renderer, _a11y_name, _as_node, _child_nodes, _collect_fragments
from .sanitize import sanitize_url_or_blank
from .seeds import with_state_seeds

#: How a chart is carried into a markdown document. See the module docstring — neither
#: value is a fallback for the other.
ChartMode = Literal["svg", "table"]

# ─── The declared scope ─────────────────────────────────────────────────────

#: The projection's fidelity declaration: one row per canonical wire kind, ordered by wire
#: name, so a new kind lands as one clean insert.
#:
#: It disagrees with the digest's table often, and every disagreement is a real one: a
#: document is read in a renderer that draws SVG and has no 90s mail parser to survive, so
#: pictures stay pictures here and become links there. Where the two agree — every
#: ``behavioural`` kind is ``openLive`` in both — the agreement is derived from the fidelity
#: manifest rather than coincidental.
SCOPE: Final[tuple[tuple[str, Disposition], ...]] = (
    ("Badge", rendered("bold inline text: markdown has no pill, and a bracketed word reads as a tag anyway")),
    (
        "Box",
        structural(
            "a container is a sequence of blocks; a Card contributes its heading as a level-3 "
            "heading and a Separator a thematic break"
        ),
    ),
    ("Button", open_live("an action needs a runtime; a document has none, so the affordance is a link or a note")),
    ("Callout", rendered("a blockquote led by a bold tone word - the closest CommonMark has to a notice")),
    (
        "Chart",
        rendered(
            "per `MarkdownOptions.charts`: inline SVG through the host's own Chart -> Drawing "
            "lowering, or the resolved rows as a GFM table under a caption. Both carry the "
            "chart; neither is a placeholder"
        ),
    ),
    ("CodeBlock", rendered("a fenced block carrying the declared language as its info string")),
    (
        "Custom",
        open_live(
            "a host renderer's output is outside this projection's audit, and a document cannot "
            "run one to find out what it would have drawn"
        ),
    ),
    ("DataGrid", rendered("a GFM table: `staticRows` verbatim, a bound grid's resolved rows through its columns")),
    (
        "Disclosure",
        structural(
            "always EXPANDED, as a level-3 heading plus its children. A collapsed section in a "
            "document is content the reader never learns exists, and markdown has no toggle "
            "that survives every renderer"
        ),
    ),
    (
        "Drawing",
        rendered(
            "inline SVG under `charts='svg'`; under `charts='table'` a captioned note, since a "
            "drawing carries no rows to tabulate"
        ),
    ),
    (
        "Embed",
        open_live(
            "a third-party browsing context that has never been fetched. The mandatory title is "
            "the link text, which is what it was written to be"
        ),
    ),
    ("ErrorBoundary", structural("the protected child renders; the fallback is a client-runtime path")),
    ("Fact", rendered("a bold label, the value, and the help text in italics on its own line")),
    ("FileUpload", open_live("interactive (fidelity manifest: behavioural)")),
    ("Filters", open_live("interactive (fidelity manifest: behavioural)")),
    ("Form", open_live("interactive (fidelity manifest: behavioural) - a document cannot post anything")),
    ("FragmentDecl", omitted("a template declaration paints nothing, here as everywhere")),
    (
        "FragmentRef",
        structural("expanded against the tree's fragment registry; an unresolved reference renders a labelled note"),
    ),
    (
        "Heading",
        rendered(
            "an ATX heading at the node's declared level, OFFSET by the document's nesting so a "
            "level-1 heading inside a card does not compete with the document's own title - but "
            "ONLY for the Standard variant. An Eyebrow or Caption is italic running text and a "
            "Lead a plain paragraph, because a subtitle promoted to a heading invents an outline "
            "entry the author never declared"
        ),
    ),
    (
        "Icon",
        omitted(
            "the icon hook relies on host CSS for the glyph; a document has no stylesheet, so "
            "the hook would carry nothing"
        ),
    ),
    (
        "Image",
        rendered("a markdown image with the sanitised src and the alt text, which markdown makes mandatory anyway"),
    ),
    ("LabelValueRow", rendered("a bold label and the formatted value on one line")),
    ("Link", rendered("an inline markdown link - the crawlable destination this projection exists to preserve")),
    ("List", rendered("an ordered or bulleted list, one item per line")),
    ("Map", open_live("a client map library draws it; the document has markers, not a picture")),
    (
        "Markdown",
        rendered(
            "carried through VERBATIM. This is the one kind whose content is already the target "
            "language, so re-rendering it to HTML and back would be a lossy round-trip of the "
            "author's own bytes"
        ),
    ),
    (
        "Math",
        rendered(
            "the source in a `$$` display block (or `$` inline), which is what every markdown maths extension reads"
        ),
    ),
    (
        "Media",
        open_live(
            "a transport. A document cannot play it, and a poster frame rendered as an image is a "
            "still that silently looks like a broken player"
        ),
    ),
    ("Metric", rendered("a bold label, the formatted value, and the trend and subtext beneath it")),
    (
        "Modal",
        open_live(
            "interactive (fidelity manifest: behavioural) - an overlay has no meaning in a document with no viewport"
        ),
    ),
    ("Mount", open_live("the guest tree attaches client-side; there is nothing to project")),
    (
        "Progress",
        rendered("the percentage as text. A bar drawn in block characters would be a picture markdown cannot size"),
    ),
    (
        "ScrollArea",
        structural("children render in full - a document has no clipping, and hidden content is lost content"),
    ),
    ("Select", open_live("interactive (fidelity manifest: behavioural)")),
    ("Skeleton", omitted("a loading placeholder describes a state a written document is never in")),
    (
        "Sparkline",
        rendered(
            "inline SVG under `charts='svg'` through the same lowering the Chart takes; under "
            "`charts='table'` the resolved series as a one-column table"
        ),
    ),
    (
        "SplitPanel",
        structural(
            "both panels in order. A document is one column, and side-by-side is a viewport property it does not have"
        ),
    ),
    ("Stepper", open_live("interactive (fidelity manifest: behavioural)")),
    ("SummaryList", structural("the heading as a level-3 heading, then its children")),
    (
        "Switch",
        structural("the case matching the resolved selector, else the default - the same branch the HTML render picks"),
    ),
    (
        "Tabs",
        open_live(
            "interactive (fidelity manifest: behavioural). Deliberately NOT 'render the active "
            "panel': a document that silently drops the other panels is a lie about how much it "
            "contains"
        ),
    ),
    (
        "Toast",
        rendered(
            "an OPEN toast renders as a blockquote; a CLOSED one is OMITTED, because a "
            "notification that leaks into a document it was closed in is a disclosure bug"
        ),
    ),
    (
        "Tree",
        open_live(
            "interactive (fidelity manifest: behavioural). Nested lists would render, but only by "
            "showing every row including the branches the document says are CLOSED - the `Tabs` "
            "argument at a different slot"
        ),
    ),
)


def disposition_for(wire_kind: str) -> Disposition | None:
    """The declared document posture of a wire kind, or ``None`` for a kind with no row."""
    return disposition_of(SCOPE, wire_kind)


# ─── Options ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MarkdownOptions:
    """Host-supplied knobs. Small, for the same reason the digest's are."""

    #: How a chart, sparkline or drawing is carried. See the module docstring.
    charts: ChartMode = "svg"
    #: The live surface an "open live" affordance points at. ``None`` emits an italic note
    #: naming what is not in the document rather than a dangling link — a document that
    #: promises a destination it has not got is worse than one that says so.
    live_url: str | None = None
    #: The destination policy every tree-authored ``href`` / image ``src`` is checked
    #: against (WIRE_FORMAT §14.1). Deny-non-local, as everywhere else in this renderer.
    egress_policy: EgressPolicy = DENY_NON_LOCAL_EGRESS


#: The conventional defaults: charts as inline SVG, no live surface declared, deny-non-local.
DEFAULT_MARKDOWN_OPTIONS: Final = MarkdownOptions()


# ─── Markdown text escaping ─────────────────────────────────────────────────

#: The characters that can begin a CommonMark inline construct ANYWHERE in a line, and are
#: therefore escaped wherever resolved text lands. CommonMark defines a backslash escape as
#: producing the literal character for every ASCII punctuation mark, so the escape is
#: lossless and a round-trip through ``markdown.to_html`` yields the text the tree carried.
#:
#: The alternative — escaping all ASCII punctuation unconditionally — is safe, and was
#: rejected on purpose: it puts a backslash before the full stop at the end of every
#: sentence, and a document nobody wants to read has failed at the one thing distinguishing
#: it from the wire JSON. The narrowing is paid for by :data:`_LEADING_SPECIALS` below rather
#: than by hoping.
_INLINE_SPECIALS: Final = "\\`*_[]<&|~"

#: The characters that begin a construct only at the START of a block — an ATX heading, a
#: blockquote, a bullet, a setext underline.
#:
#: They need escaping less often, and the "less often" is precisely where this got caught:
#: the first draft assumed no resolved string ever lands at a block start, because every arm
#: prefixes what it emits. That is true of a PARAGRAPH and false of a CONTAINER — a list
#: item's content and a blockquote's content are each a fresh block, so ``- `` and ``> `` are
#: openers rather than protection, and ``- # not a heading`` really does produce an ``<h1>``.
#: The falsifier that found it is ``test_no_resolved_text_lands_at_block_start``, which
#: pushes hostile text through the host's own §14 renderer instead of asserting about the
#: markdown by eye.
_LEADING_SPECIALS: Final = "#>-+=!"


def escape_inline(text: str, *, block_start: bool = False) -> str:
    """Escape a resolved string so it survives as literal text in a markdown document.

    ``block_start=True`` where the string is the first thing in a block — a list item's
    content, a blockquote line — and additionally neutralises the leading-position
    constructs. Every other call site prefixes its text with a literal (``**`` for a label,
    ``# `` for a heading, ``[`` for a link), where those characters are ordinary.
    """
    escaped = "".join("\\" + ch if ch in _INLINE_SPECIALS else ch for ch in text)
    return _escape_block_leader(escaped) if block_start else escaped


def _escape_block_leader(text: str) -> str:
    """Neutralise a leading block-construct opener in already-inline-escaped text.

    Two shapes, because CommonMark has two: a single opening character, and an ordered-list
    marker — a run of digits followed by ``.`` or ``)``, which no single-character rule
    catches and which is exactly what a resolved figure at the head of a line looks like.
    """
    if not text:
        return text
    if text[0] in _LEADING_SPECIALS:
        return "\\" + text
    digits = 0
    while digits < len(text) and text[digits].isascii() and text[digits].isdigit():
        digits += 1
    if 0 < digits < len(text) and text[digits] in ".)":
        return text[:digits] + "\\" + text[digits:]
    return text


def escape_cell(text: str) -> str:
    """Escape a resolved string for a GFM table cell.

    A newline would end the row and a pipe would end the cell, and neither has a backslash
    escape a table parser honours — so both become spaces / the HTML entity respectively,
    which is what GFM's own specification recommends for a literal pipe.
    """
    return escape_inline(" ".join(text.split())).replace("\\|", "&#124;")


# ─── The renderer ───────────────────────────────────────────────────────────


class _MarkdownRenderer:
    """The per-render context: options, host binding sources, and the fragment registry.

    Each arm returns a list of BLOCKS — already-formed markdown strings with no trailing
    newline. Blank-line separation is applied once, at the join, so no arm can produce a
    document whose block spacing depends on which arm ran before it. That is the whole
    reason blocks are a list rather than a string.
    """

    def __init__(self, options: MarkdownOptions, sources: BindingSources | None, fragments: dict[str, Node]) -> None:
        self.options = options
        self.sources = sources
        self.fragments = fragments
        # The HTML renderer, held solely for its drawing/lowering geometry. It shares this
        # projection's sources and policy so a chart lowered here is the chart the page
        # shows; nothing else on it is called.
        self._html = Renderer(sources, fragments, options.egress_policy)

    # ── text helpers ────────────────────────────────────────────────────────

    def _text(self, ts: Value) -> str:
        return render_text(ts, self.sources)

    def _inline(self, ts: Value, *, block_start: bool = False) -> str:
        return escape_inline(self._text(ts), block_start=block_start)

    def _live_label(self, node: Node, fallback: str) -> str:
        a11y = node.extras.get("accessibility")
        if isinstance(a11y, Obj):
            label = _a11y_name(a11y.fields.get("label"), self.sources)
            if label:
                return label
        return fallback

    def _open_live(self, node: Node, fallback: str) -> list[str]:
        label = self._live_label(node, fallback)
        if self.options.live_url is not None:
            # The scheme floor, NOT the destination policy. `live_url` is supplied by the host
            # in its own options record, so checking it against the host's own allowlist tests
            # nothing and would make the common case ("point at my app") fail unless the host
            # remembered to allowlist itself — which is the reference host's reasoning, and the
            # digest keeps the same rule. The floor still applies: a `javascript:` live URL is
            # neutered like any other.
            href = sanitize_url_or_blank(self.options.live_url + "#" + node.id)
            return [f"[{escape_inline(label)} — open live]({_escape_destination(href)})"]
        return [f"*{escape_inline(label)} — available in the live view*"]

    def _safe(self, cls: EgressClass, url: str) -> str:
        safe, _ = sanitize_url_for_egress(self.options.egress_policy, cls, url)
        return safe

    # ── the walk ────────────────────────────────────────────────────────────

    def render(self, node: Node, depth: int, level: int) -> list[str]:
        """Blocks for ``node``.

        ``level`` is the heading offset: the depth of the nearest enclosing section, so a
        heading nested inside a card lands below the card's own heading rather than
        competing with the document title. ``depth`` is the wire nesting guard.
        """
        if depth > MAX_NODE_DEPTH:
            return [f"*[subtree omitted: nesting exceeds the wire limit MaxDepth = {MAX_NODE_DEPTH}]*"]
        # fuaran#1535 — conditional presence, honoured here for the reason this
        # projection exists at all: a document that omits a node on the page and
        # prints it here is not a projection of the page.
        if not is_node_visible(node.extras.get("visible"), self.sources):
            return []
        handler = _DISPATCH.get(node.kind.tag or "")
        if handler is None:
            return [f"*[fuaran:unprojected kind '{escape_inline(node.kind.tag or '')}']*"]
        return handler(self, node, node.kind.fields, depth, level)

    def _children(self, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        out: list[str] = []
        for child in _child_nodes(fields):
            out.extend(self.render(child, depth + 1, level))
        return out

    # ── Structure ───────────────────────────────────────────────────────────

    def _box(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        role = fields.get("role")
        if role == "Separator":
            return ["---"]
        if role == "Card":
            heading = fields.get("heading")
            if heading is not None:
                head = _heading_block(level + 1, self._inline(heading))
                return [head, *self._children(fields, depth, level + 1)]
        return self._children(fields, depth, level)

    def _summary_list(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        heading = fields.get("heading")
        if heading is not None:
            return [_heading_block(level + 1, self._inline(heading)), *self._children(fields, depth, level + 1)]
        return self._children(fields, depth, level)

    def _disclosure(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        # Always expanded, for the reason the digest expands it: a section a reader cannot
        # open is content they never learn exists.
        return [
            _heading_block(level + 1, self._inline(fields.get("heading"))),
            *self._children(fields, depth, level + 1),
        ]

    def _pass_through(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        return self._children(fields, depth, level)

    def _error_boundary(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        child = _as_node(fields.get("child"))
        return self.render(child, depth + 1, level) if child is not None else []

    def _switch(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
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
            return self.render(child, depth + 1, level)
        default = _as_node(fields.get("default"))
        return self.render(default, depth + 1, level) if default is not None else []

    def _fragment_ref(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        name = fields.get("name")
        if isinstance(name, str):
            body = self.fragments.get(name)
            if body is not None:
                return self.render(body, depth + 1, level)
            return [f"*[fuaran:fragment unresolved '{escape_inline(name)}']*"]
        return []

    # ── Display ─────────────────────────────────────────────────────────────

    def _heading(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        """An ATX heading — but ONLY for the ``Standard`` variant.

        A markdown document's outline is the thing it has that the page does not, and the
        other three variants are running text occupying a heading's slot rather than sections:
        an ``Eyebrow`` is a kicker above a title, a ``Caption`` a subtitle beneath one, a
        ``Lead`` an introductory paragraph. Promoting any of them to ``##`` invents structure
        the author did not declare and puts a subtitle in the table of contents.

        The digest reaches the same conclusion by a different route — it styles those three as
        muted or normal-weight text rather than at the type scale — so the two projections
        agree about which of these is a section, which is the property that matters.
        """
        variant = fields.get("variant")
        text = self._inline(fields.get("text"))
        if variant == "Eyebrow" or variant == "Caption":
            return [f"*{text}*"] if text else []
        if variant == "Lead":
            return [text] if text else []
        raw = fields.get("level")
        declared = raw if isinstance(raw, int) and not isinstance(raw, bool) else 2
        return [_heading_block(level + declared - 1, text)]

    def _markdown(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        # Verbatim. This is the one kind whose content is already the target language;
        # escaping it would turn the author's own markdown into literal text, and rendering
        # it to HTML and back would be a lossy round-trip of bytes that need no trip at all.
        body = self._text(fields.get("text")).strip("\n")
        return [body] if body else []

    def _metric(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        value = resolve_scalar_number(fields.get("value"), self.sources)
        value_text = format_number(fields.get("format"), value) if value is not None else _EM_DASH
        lines = [f"**{self._inline(fields.get('label'))}** — {escape_inline(value_text)}"]
        trend_binding = fields.get("trend")
        if trend_binding is not None:
            trend = resolve_scalar_number(trend_binding, self.sources)
            if trend is not None:
                lines.append(escape_inline(format_number(fields.get("trendFormat"), trend)))
        subtext = fields.get("subtext")
        if subtext is not None:
            lines.append(f"*{self._inline(subtext)}*")
        return ["  \n".join(lines)]

    def _fact(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        lines = [f"**{self._inline(fields.get('label'))}** — {self._inline(fields.get('value'))}"]
        help_text = fields.get("help")
        if help_text is not None:
            lines.append(f"*{self._inline(help_text)}*")
        return ["  \n".join(lines)]

    def _label_value_row(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        value = resolve_scalar_number(fields.get("value"), self.sources)
        value_text = format_number(fields.get("format"), value) if value is not None else _EM_DASH
        return [f"**{self._inline(fields.get('label'))}** — {escape_inline(value_text)}"]

    def _badge(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        return [f"**{self._inline(fields.get('label'))}**"]

    def _callout(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        tone = fields.get("tone")
        tone_word = tone if isinstance(tone, str) else "Info"
        lines = [f"**{escape_inline(tone_word)}**"]
        heading = fields.get("heading")
        if heading is not None:
            lines.append(f"**{self._inline(heading)}**")
        lines.append(self._inline(fields.get("body"), block_start=True))
        return ["\n".join(f"> {line}" for line in lines)]

    def _list(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        raw = fields.get("items")
        items = raw.items if isinstance(raw, Arr) else []
        ordered = fields.get("ordered") is True
        lines = [
            f"{index + 1}. {self._inline(item, block_start=True)}"
            if ordered
            else f"- {self._inline(item, block_start=True)}"
            for index, item in enumerate(items)
        ]
        return ["\n".join(lines)] if lines else []

    def _link(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        resolved = resolve_binding(fields.get("href"), self.sources)
        href = self._safe(EgressClass.HYPERLINK, resolved if isinstance(resolved, str) else "")
        return [f"[{self._inline(fields.get('label'))}]({_escape_destination(href)})"]

    def _image(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        resolved = resolve_binding(fields.get("src"), self.sources)
        src = self._safe(EgressClass.MEDIA, resolved if isinstance(resolved, str) else "")
        return [f"![{self._inline(fields.get('alt'))}]({_escape_destination(src)})"]

    def _progress(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        raw = resolve_binding(fields.get("fraction"), self.sources)
        fraction = (
            max(0.0, min(1.0, float(raw))) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0.0
        )
        pct = int(round(fraction * 100.0))
        label = fields.get("label")
        return [f"**{self._inline(label)}** — {pct}%" if label is not None else f"{pct}%"]

    def _code_block(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        code = fields.get("code")
        code = code if isinstance(code, str) else ""
        language = fields.get("language")
        info = language if isinstance(language, str) else ""
        # A fence longer than any backtick run inside the body, so a code block whose content
        # is itself fenced markdown survives — the rule CommonMark states and the one a naive
        # three-backtick fence gets wrong on exactly the documents most likely to contain it.
        fence = "`" * max(3, _longest_backtick_run(code) + 1)
        return [f"{fence}{info}\n{code}\n{fence}"]

    def _math(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        source = fields.get("source")
        source = source if isinstance(source, str) else ""
        if fields.get("display") == "Inline":
            return [f"${source}$"]
        return [f"$$\n{source}\n$$"]

    def _toast(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        if resolve_binding(fields.get("open"), self.sources) is not True:
            return []
        return [f"> {self._inline(fields.get('message'), block_start=True)}"]

    # ── Tables ──────────────────────────────────────────────────────────────

    def _gfm_table(self, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
        """A GFM table from already-escaped cells.

        Column count is the header count: a row longer than the header is truncated and a
        shorter one padded, because a ragged GFM table is not a table at all — every parser
        reads it as a paragraph, silently, and the data is simply gone from the document.
        """
        width = len(headers)
        if width == 0:
            return []
        lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
        for row in rows:
            cells = list(row[:width]) + [""] * max(0, width - len(row))
            lines.append("| " + " | ".join(cells) + " |")
        return ["\n".join(lines)]

    def _table(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        headers = fields.get("headers")
        rows = fields.get("rows")
        return self._gfm_table(
            [escape_cell(self._text(h)) for h in (headers.items if isinstance(headers, Arr) else [])],
            [
                [escape_cell(self._text(c)) for c in row.items]
                for row in (rows.items if isinstance(rows, Arr) else [])
                if isinstance(row, Arr)
            ],
        )

    def _data_grid(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        static_rows = fields.get("staticRows")
        if isinstance(static_rows, Obj):
            return self._table(node, static_rows.fields, depth, level)
        resolved = resolve_source(fields.get("source"), self.sources)
        columns = fields.get("columns")
        cols = [c for c in (columns.items if isinstance(columns, Arr) else []) if isinstance(c, Obj)]
        if isinstance(resolved, Arr) and any(isinstance(c.fields.get("field"), str) for c in cols):
            headers = [escape_cell(self._text(c.fields.get("header"))) for c in cols]
            rows: list[list[str]] = []
            for row in resolved.items:
                cells: list[str] = []
                for c in cols:
                    field_name = c.fields.get("field")
                    raw = row.fields.get(field_name) if isinstance(row, Obj) and isinstance(field_name, str) else None
                    cells.append("" if raw is None else escape_cell(_cell_text(raw)))
                rows.append(cells)
            return self._gfm_table(headers, rows)
        return self._open_live(node, "Table")

    # ── Pictures ────────────────────────────────────────────────────────────

    def _chart(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        title = fields.get("title")
        caption = self._text(title) if title is not None else ""
        if self.options.charts == "svg":
            svg = self._html.chart_svg(node, fields)
            if svg is not None:
                return [svg]
        rows = self._chart_rows(fields)
        if rows is not None:
            return rows
        return self._open_live(node, caption if caption != "" else "Chart")

    def _chart_rows(self, fields: dict[str, Value]) -> list[str] | None:
        """The chart's resolved rows as a GFM table — the data the picture is OF.

        ``None`` when the source does not resolve to rows, which is the same condition under
        which the HTML renderer declines to lower: there is nothing to draw and nothing to
        tabulate, and both projections say so rather than printing an empty frame.
        """
        resolved = resolve_source(fields.get("source"), self.sources)
        if not isinstance(resolved, Arr) or not resolved.items:
            return None
        x_field = fields.get("xField")
        y_fields_raw = fields.get("yFields")
        if not isinstance(x_field, str) or not isinstance(y_fields_raw, Arr):
            return None
        y_fields = [y for y in y_fields_raw.items if isinstance(y, str)]
        names = [x_field, *y_fields]
        rows = [
            [escape_cell(_cell_text(row.fields.get(name))) for name in names]
            for row in resolved.items
            if isinstance(row, Obj)
        ]
        table = self._gfm_table([escape_cell(n) for n in names], rows)
        title = fields.get("title")
        caption = self._text(title) if title is not None else ""
        return [f"*{escape_inline(caption)}*", *table] if caption else table

    def _sparkline(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        if self.options.charts == "svg":
            svg = self._html.sparkline_svg(node, fields)
            if svg is not None:
                return [svg]
        series = resolve_binding(fields.get("source"), self.sources)
        if isinstance(series, Arr) and series.items:
            return self._gfm_table(["value"], [[escape_cell(_cell_text(v))] for v in series.items])
        return self._open_live(node, "Trend")

    def _drawing(self, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        if self.options.charts == "svg":
            return [self._html.bare_drawing_svg(fields)]
        # A drawing carries geometry, not rows: there is nothing to tabulate, so the
        # table mode says what is missing rather than inventing a shape for it.
        return self._open_live(node, "Diagram")


def _live(fallback: str):
    """A handler that projects the node to a labelled open-live affordance."""

    def handler(renderer: _MarkdownRenderer, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
        return renderer._open_live(node, fallback)

    return handler


def _nothing(renderer: _MarkdownRenderer, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
    return []


def _media(renderer: _MarkdownRenderer, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
    kind = fields.get("kind")
    surface = "Audio" if isinstance(kind, Obj) and kind.tag == "Audio" else "Video"
    label = renderer._text(fields.get("label"))
    return renderer._open_live(node, label if label != "" else surface)


def _embed(renderer: _MarkdownRenderer, node: Node, fields: dict[str, Value], depth: int, level: int) -> list[str]:
    title = renderer._text(fields.get("title"))
    return renderer._open_live(node, title if title != "" else "Embedded view")


_DISPATCH: Final = {
    # Structure
    "Box": _MarkdownRenderer._box,
    "SplitPanel": _MarkdownRenderer._pass_through,
    "SummaryList": _MarkdownRenderer._summary_list,
    "Disclosure": _MarkdownRenderer._disclosure,
    "ScrollArea": _MarkdownRenderer._pass_through,
    "ErrorBoundary": _MarkdownRenderer._error_boundary,
    "Switch": _MarkdownRenderer._switch,
    "FragmentRef": _MarkdownRenderer._fragment_ref,
    "FragmentDecl": _nothing,
    # Display
    "Heading": _MarkdownRenderer._heading,
    "Markdown": _MarkdownRenderer._markdown,
    "Metric": _MarkdownRenderer._metric,
    "Fact": _MarkdownRenderer._fact,
    "LabelValueRow": _MarkdownRenderer._label_value_row,
    "Badge": _MarkdownRenderer._badge,
    "Callout": _MarkdownRenderer._callout,
    "List": _MarkdownRenderer._list,
    "Link": _MarkdownRenderer._link,
    "Image": _MarkdownRenderer._image,
    "Progress": _MarkdownRenderer._progress,
    "CodeBlock": _MarkdownRenderer._code_block,
    "Math": _MarkdownRenderer._math,
    "Toast": _MarkdownRenderer._toast,
    "DataGrid": _MarkdownRenderer._data_grid,
    # `Table` is not a canonical wire kind — it was folded into `DataGrid.staticRows` — but
    # this host still renders a decoded `Table`, so the document answers it the same way.
    "Table": _MarkdownRenderer._table,
    # Pictures
    "Chart": _MarkdownRenderer._chart,
    "Sparkline": _MarkdownRenderer._sparkline,
    "Drawing": _MarkdownRenderer._drawing,
    # Interactive
    "Button": _live("Action"),
    "Form": _live("Form"),
    "Select": _live("Selection"),
    "FileUpload": _live("File upload"),
    "Filters": _live("Filters"),
    "Tabs": _live("Tabbed section"),
    "Stepper": _live("Step-by-step section"),
    "Modal": _live("Dialog"),
    "Map": _live("Map"),
    "Media": _media,
    "Embed": _embed,
    "Tree": _live("Hierarchy"),
    "Custom": _live("Component"),
    "Mount": _live("Embedded view"),
    # Zero-paint
    "Icon": _nothing,
    "Skeleton": _nothing,
}


# ─── Small pure helpers ─────────────────────────────────────────────────────


def _heading_block(level: int, text: str) -> str:
    """An ATX heading, clamped to markdown's six levels.

    Clamped rather than refused: a deeply nested section is a real document, and a seventh
    ``#`` is a paragraph beginning with hashes in every reader. Six is the floor the format
    imposes, not a decision this projection gets to make.
    """
    return "#" * max(1, min(6, level)) + " " + text


def _longest_backtick_run(text: str) -> int:
    longest = 0
    run = 0
    for ch in text:
        run = run + 1 if ch == "`" else 0
        longest = max(longest, run)
    return longest


def _escape_destination(url: str) -> str:
    """Make a URL safe to sit inside a markdown ``(…)`` destination.

    Parentheses and spaces are what end a destination early; angle-bracket form is the
    CommonMark answer for a URL containing spaces, and percent-encoding is the answer for
    parentheses. The URL has already passed the scheme floor and the destination policy
    before it reaches here — this is a syntax obligation, not a safety one.
    """
    escaped = url.replace("(", "%28").replace(")", "%29")
    return f"<{escaped}>" if " " in escaped else escaped


def _cell_text(value: Value) -> str:
    """A resolved data cell as display text.

    ``bool`` before ``int`` — Python's ``bool`` IS an ``int``, and a grid printing ``1``
    where the data said ``True`` is the kind of quiet wrongness a table makes impossible to
    notice.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return format_number(None, float(value))
    return ""


# ─── Public entry point ─────────────────────────────────────────────────────


def render_markdown(
    node: Node,
    sources: BindingSources | None = None,
    options: MarkdownOptions = DEFAULT_MARKDOWN_OPTIONS,
    *,
    title: str | None = None,
) -> str:
    """Render a decoded tree to a markdown document.

    :param node: the decoded tree.
    :param sources: the host binding map, resolved exactly as
        :func:`~fuaran_py.renderer.render_html` resolves it — including the state seeds the
        tree declares for itself, so the document and the page read the same values.
    :param options: chart carriage, the live surface, and the destination policy.
    :param title: an optional level-1 heading placed above the tree's own content. The tree's
        headings are offset beneath it, so a document with a title has exactly one ``#``.
    :returns: the document, blocks separated by a blank line, ending in a single newline.

    Byte-stable: the same arguments produce the same bytes on every run and every platform.
    """
    fragments: dict[str, Node] = {}
    _collect_fragments(node, fragments)
    seeded = with_state_seeds(node, sources)
    renderer = _MarkdownRenderer(options, seeded, fragments)
    blocks = renderer.render(node, 1, 1 if title is None else 2)
    if title is not None:
        blocks = [_heading_block(1, escape_inline(title)), *blocks]
    body = "\n\n".join(b for b in blocks if b != "")
    return body + "\n" if body else ""


__all__ = [
    "DEFAULT_MARKDOWN_OPTIONS",
    "SCOPE",
    "ChartMode",
    "MarkdownOptions",
    "disposition_for",
    "escape_cell",
    "escape_inline",
    "render_markdown",
]
