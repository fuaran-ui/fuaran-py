"""The speech projection — a tree read aloud (fuaran#1913; the reference is Phase 1813).

The third static projection of a decoded tree, beside the markdown document and the
email-safe digest, for a surface with no pixels at all. It walks the same ``Node`` tree the
HTML renderer walks, resolves through the same :mod:`~fuaran_ui.renderer.bindings`
functions, and produces a SPOKEN SCRIPT: ordered utterances carrying a pause / emphasis
vocabulary small enough to lower to plain text or to SSML without losing anything either
can express.

**What is said.** Where a node declares ``accessibility.speak`` (Phase 1812), that is what
is said — exactly, whatever the kind. Where it does not, the projection derives speech from
what the node already carries, per the per-kind ruling table the corpus publishes as the
``speech`` column of ``render-fidelity.json`` (``spoken`` / ``derived`` /
``announced-only`` / ``omitted``). :data:`SCOPE` is this module's declaration of which
ruling each kind's arm implements; ``tests/test_speech.py`` holds it to the corpus table
kind by kind, and reddens when the table gains a kind this module does not answer.

**Nothing is dropped silently.** A node that contributes nothing to the script — hidden, not
visible, a closed toast, a branch not taken, content inside an announced-only surface, a
decorative image, a kind with nothing sayable — appears in the omission list by node id,
with the reason and the id of the node whose state decided it.

**Interactive kinds are announced, never pretended at.** A form is "Form, 4 fields"; a tab
set is a tabbed section with its tab count — never a reading of whichever panel happens to
be active, which would be a lie about how much the section holds.

**Order** is authored order, always: ``accessibility.liveRegion`` does not move a node, since
a script is read once and there is no later update for politeness to schedule.

**Determinism.** Same tree, same sources, same script. There is no clock read (a
``Binding.Now`` resolves through the sources' ``now``), no identifier minting, and no
iteration over an unordered collection.

**Injection.** SSML is markup, and a bound string is data. Every text reaching the SSML
lowering passes through :func:`escape_ssml`, which escapes the five XML metacharacters and
DROPS every character XML 1.0 cannot carry, so no bound value can open an element, close one,
or make the document ill-formed.

The reference host's output for every node fixture in the corpus is committed beside the
tests (``tests/fixtures/speech/reference-speech.json``, with the script that regenerates
it), and this module's output is compared against it byte for byte, in both lowerings.
"""

from __future__ import annotations

import html as _html
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Literal

from ..limits import MAX_NODE_DEPTH
from ..model import Arr, Node, Obj, Value
from . import markdown as _markdown
from .bindings import (
    BindingSourcesLike,
    _expr_as_transform,
    _is_expr,
    _is_transform,
    _scalar_cell,
    format_number,
    is_node_visible,
    render_text,
    resolve_binding,
    resolve_scalar_bool,
    resolve_scalar_number,
)
from .egress import DENY_NON_LOCAL_EGRESS, EgressPolicy
from .email import selected_switch_branch
from .render import _a11y_name, _as_node, _child_nodes, _collect_fragments
from .seeds import with_state_seeds

# ─── The script ─────────────────────────────────────────────────────────────


class Pause(StrEnum):
    """The pause after an utterance.

    Three steps, because both lowerings can carry three: plain text ignores all of them (a
    line break is its only pause), and SSML maps them to ``<break>`` strengths.
    """

    NONE = "none"
    #: Between sentences of one section.
    SHORT = "short"
    #: After a heading: the listener's cue that a section begins.
    LONG = "long"


class UtteranceSource(StrEnum):
    """Where an utterance's words came from — the three sources a listener's trust differs by."""

    #: The node's own ``accessibility.speak``, read exactly.
    SPEAK = "speak"
    #: Derived from the kind's own content (a ``spoken`` or ``derived`` ruling).
    DERIVED = "derived"
    #: An announcement of what the node IS (an ``announced-only`` ruling).
    ANNOUNCED = "announced"


@dataclass(frozen=True)
class Utterance:
    """One thing said. ``text`` is whitespace-normalised: no line break survives inside it,
    which is what lets the plain-text lowering put exactly one utterance on each line."""

    node_id: str
    text: str
    emphasis: bool
    pause_after: Pause
    source: UtteranceSource


@dataclass(frozen=True)
class SpeechScript:
    """The ordered script."""

    utterances: tuple[Utterance, ...]


class OmissionReason(StrEnum):
    """Why a node is absent from the script."""

    #: ``accessibility.hidden`` resolved true on the deciding node.
    HIDDEN = "hidden"
    #: ``visible`` resolved false on the deciding node.
    NOT_VISIBLE = "not-visible"
    #: A closed ``Toast``.
    CLOSED = "closed"
    #: A ``Switch`` case not selected, or an ``ErrorBoundary`` fallback.
    NOT_TAKEN = "not-taken"
    #: Content inside an ``announced-only`` node, which is announced rather than read.
    INSIDE_ANNOUNCED = "inside-announced"
    #: An ``Image`` whose alt is empty: declared decorative by its author.
    DECORATIVE = "decorative"
    #: The node's kind carries nothing a listener could be told.
    NOTHING_SAYABLE = "nothing-sayable"
    #: Nesting beyond the wire limit ``MAX_NODE_DEPTH``.
    DEPTH_EXCEEDED = "depth-exceeded"
    #: A ``FragmentRef`` naming no declared fragment; the name rides on the omission.
    UNRESOLVED_FRAGMENT = "unresolved-fragment"


@dataclass(frozen=True)
class SpeechOmission:
    """One node absent from the script.

    ``decided_at`` is the id of the node whose state caused the omission — the node itself,
    or the ancestor whose subtree was excluded — so a reader can go from any omission to its
    cause. ``fragment`` carries the unresolved name for ``UNRESOLVED_FRAGMENT``.
    """

    node_id: str
    kind: str
    reason: OmissionReason
    decided_at: str
    fragment: str | None = None


# ─── The declared ruling per kind ───────────────────────────────────────────

#: A speech class, as the ``speech.class`` string ``render-fidelity.json`` carries.
SpeechClass = Literal["spoken", "derived", "announced-only", "omitted"]

#: The ruling each kind's arm below IMPLEMENTS, one row per canonical wire kind, ordered by
#: wire name. The corpus table is the authority; this is the claim held to it. A kind the
#: table rules on that is absent here, or a row whose class disagrees with the table, fails
#: ``tests/test_speech.py`` — the row is not a second copy of the decision, it is what the
#: test checks the decision against.
SCOPE: Final[tuple[tuple[str, SpeechClass], ...]] = (
    ("Badge", "derived"),
    ("Box", "derived"),
    ("Button", "announced-only"),
    ("Callout", "spoken"),
    ("Chart", "announced-only"),
    ("CodeBlock", "announced-only"),
    ("Custom", "announced-only"),
    ("DataGrid", "announced-only"),
    ("Disclosure", "derived"),
    ("Drawing", "announced-only"),
    ("Embed", "announced-only"),
    ("ErrorBoundary", "derived"),
    ("Fact", "derived"),
    ("FileUpload", "announced-only"),
    ("Filters", "announced-only"),
    ("Form", "announced-only"),
    ("FragmentDecl", "omitted"),
    ("FragmentRef", "derived"),
    ("Heading", "spoken"),
    ("Icon", "derived"),
    ("Image", "derived"),
    ("LabelValueRow", "derived"),
    ("Link", "derived"),
    ("List", "spoken"),
    ("Map", "announced-only"),
    ("Markdown", "spoken"),
    ("Math", "announced-only"),
    ("Media", "announced-only"),
    ("Metric", "derived"),
    ("Modal", "announced-only"),
    ("Mount", "announced-only"),
    ("Progress", "derived"),
    ("ScrollArea", "derived"),
    ("Select", "announced-only"),
    ("Skeleton", "omitted"),
    ("Sparkline", "announced-only"),
    ("SplitPanel", "derived"),
    ("Stepper", "announced-only"),
    ("SummaryList", "derived"),
    ("Switch", "derived"),
    ("Tabs", "announced-only"),
    ("Toast", "spoken"),
    ("Tree", "announced-only"),
)


def expected_source(speech_class: str) -> UtteranceSource | None:
    """The source a node's OWN utterances carry under a ruling, absent ``speak``.

    ``spoken`` and ``derived`` produce ``DERIVED`` utterances, ``announced-only`` produces
    ``ANNOUNCED``, and ``omitted`` produces none — the conformance check that holds this
    module to the corpus table reads it.
    """
    if speech_class in ("spoken", "derived"):
        return UtteranceSource.DERIVED
    if speech_class == "announced-only":
        return UtteranceSource.ANNOUNCED
    return None


# ─── Text helpers ───────────────────────────────────────────────────────────

#: The characters the reference host's ``Char.IsWhiteSpace`` treats as white space beyond
#: the three separator categories. Spelled out rather than ``str.isspace``, which also
#: counts U+001C..U+001F and would fuse words the reference keeps apart.
_EXTRA_WHITESPACE: Final = frozenset("\t\n\v\f\r \x85\xa0")


def _is_white(c: str) -> bool:
    return c in _EXTRA_WHITESPACE or unicodedata.category(c) in ("Zs", "Zl", "Zp")


def normalise(s: str) -> str:
    """Collapse every whitespace run (line breaks included) to one space, and trim."""
    out: list[str] = []
    pending_space = False
    for c in s:
        if _is_white(c):
            pending_space = len(out) > 0
        else:
            if pending_space:
                out.append(" ")
                pending_space = False
            out.append(c)
    return "".join(out)


def _xml_char_ok(cp: int) -> bool:
    """Is this code point carried by XML 1.0 (``Char`` production)?"""
    return cp in (0x9, 0xA, 0xD) or 0x20 <= cp <= 0xD7FF or 0xE000 <= cp <= 0xFFFD or 0x10000 <= cp <= 0x10FFFF


_SSML_ESCAPES: Final = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;"}


def escape_ssml(s: str) -> str:
    """The projection's own SSML escaper.

    Escapes ``& < > " '`` and DROPS every character XML 1.0 cannot carry (C0 controls, a lone
    surrogate, U+FFFE / U+FFFF), so the result is always well-formed character data: a bound
    string can neither inject an element nor make the document unparseable.
    """
    out: list[str] = []
    for c in s:
        escaped = _SSML_ESCAPES.get(c)
        if escaped is not None:
            out.append(escaped)
        elif _xml_char_ok(ord(c)):
            out.append(c)
    return "".join(out)


#: Closing tags (and void tags) that end a block of the GFM render: each becomes an
#: utterance boundary. Prefix-matched on the lower-cased tag body, as the reference does.
_BLOCK_BOUNDARY_PREFIXES: Final = ("/p", "/li", "/h", "/pre", "/blockquote", "/tr", "br", "hr")


def _markdown_blocks(policy: EgressPolicy, source: str) -> list[str]:
    """Markdown read aloud: the one deterministic GFM render, its markup removed and its
    entities decoded, split into one utterance per block.

    The render escapes raw HTML by construction, so every ``<`` in its output opens a tag
    the renderer itself wrote and stripping by tag is exact.
    """
    rendered = _markdown.to_html_with_egress(policy, source)
    out: list[str] = []
    i = 0
    while i < len(rendered):
        c = rendered[i]
        if c == "<":
            close = rendered.find(">", i)
            if close < 0:
                break
            tag = rendered[i + 1 : close].lower()
            # A block boundary becomes a line break (an utterance boundary); any other tag
            # becomes a space so adjacent words never fuse.
            out.append("\n" if tag.startswith(_BLOCK_BOUNDARY_PREFIXES) else " ")
            i = close + 1
        else:
            out.append(c)
            i += 1
    blocks = (normalise(line) for line in _html.unescape("".join(out)).split("\n"))
    return [b for b in blocks if b != ""]


# ─── The projection ─────────────────────────────────────────────────────────

#: One line a node says of itself: text, emphasis, pause after.
type _Line = tuple[str, bool, Pause]


@dataclass(frozen=True)
class _Own:
    """What a node says of itself (before its children), per its kind's ruling."""

    lines: list[_Line]
    source: UtteranceSource


def _derived(lines: list[_Line]) -> _Own:
    return _Own(lines, UtteranceSource.DERIVED)


def _sentence(s: str) -> _Line:
    return (s, False, Pause.SHORT)


def _heading(s: str) -> _Line:
    return (s, True, Pause.LONG)


def _announcement(noun: str, name: str, detail: str | None = None) -> _Own:
    """ "Noun: name, detail", with each optional part omitted cleanly."""
    head = noun if name == "" else noun + ": " + name
    text = head + ", " + detail if detail else head
    return _Own([_sentence(text)], UtteranceSource.ANNOUNCED)


def _count(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _arr_len(value: Value | None) -> int:
    return len(value.items) if isinstance(value, Arr) else 0


def _structural_children(node: Node) -> list[Node]:
    """The one containment relation omissions are measured over — the reference host's
    ``StructuralQuery.children``: every node a kind holds, taken or not."""
    kind = node.kind
    fields = kind.fields
    tag = kind.tag
    if tag in ("Box", "SplitPanel", "Tabs", "Stepper", "SummaryList", "Disclosure", "Modal", "ScrollArea"):
        return _child_nodes(fields)
    raw: list[Value | None]
    if tag == "ErrorBoundary":
        raw = [fields.get("child"), fields.get("fallback")]
    elif tag == "Switch":
        cases = fields.get("cases")
        raw = [c.fields.get("child") for c in cases.items if isinstance(c, Obj)] if isinstance(cases, Arr) else []
        raw.append(fields.get("default"))
    elif tag == "FragmentDecl":
        raw = [fields.get("body")]
    else:
        return []
    return [n for v in raw if (n := _as_node(v)) is not None]


class _Projector:
    """One projection pass: the sources, the fragment registry, and the two accumulators."""

    def __init__(self, sources: BindingSourcesLike | None, fragments: dict[str, Node], policy: EgressPolicy) -> None:
        self.sources = sources
        self.fragments = fragments
        self.policy = policy
        self.said: list[Utterance] = []
        self.omitted: list[SpeechOmission] = []

    # ── resolution helpers ──────────────────────────────────────────────────

    def text(self, ts: Value | None) -> str:
        return render_text(ts, self.sources)

    def a11y(self, node: Node) -> dict[str, Value]:
        a = node.extras.get("accessibility")
        return a.fields if isinstance(a, Obj) else {}

    def accessible_label(self, node: Node) -> str:
        label = self.a11y(node).get("label")
        if label is None:
            return ""
        name = _a11y_name(label, self.sources)
        return normalise(name) if name is not None else ""

    def label_or(self, node: Node, fallback: str) -> str:
        return self.accessible_label(node) or normalise(fallback)

    def is_hidden(self, node: Node) -> bool:
        hidden = self.a11y(node).get("hidden")
        if hidden is None:
            return False
        if isinstance(hidden, bool):
            return hidden
        return resolve_scalar_bool(hidden, self.sources) is True

    def figure(self, fmt: Value | None, binding: Value | None) -> str:
        """A number in its declared format; "no value" when the slot did not resolve and
        "unavailable" when evaluating it failed — two facts a listener should not confuse."""
        if isinstance(binding, Obj) and (_is_expr(binding) or _is_transform(binding)):
            transform = _expr_as_transform(binding) if _is_expr(binding) else binding
            outcome, _ = _scalar_cell(transform, self.sources)
            if outcome == "error":
                return "unavailable"
        value = resolve_scalar_number(binding, self.sources)
        return "no value" if value is None else format_number(fmt, value)

    # ── the two accumulators ────────────────────────────────────────────────

    def say(self, node_id: str, source: UtteranceSource, line: _Line) -> bool:
        text, emphasis, pause = line
        t = normalise(text)
        if t == "":
            return False
        self.said.append(Utterance(node_id, t, emphasis, pause, source))
        return True

    def omit_one(self, reason: OmissionReason, node: Node, fragment: str | None = None) -> None:
        self.omitted.append(SpeechOmission(node.id, node.kind.tag or "", reason, node.id, fragment))

    def omit_subtree(self, reason: OmissionReason, decided_at: str, node: Node) -> None:
        self.omitted.append(SpeechOmission(node.id, node.kind.tag or "", reason, decided_at))
        for child in _structural_children(node):
            self.omit_subtree(reason, decided_at, child)

    # ── the walk ────────────────────────────────────────────────────────────

    def walk(self, depth: int, node: Node) -> bool:
        """Walk one node. Returns whether anything in its subtree was said."""
        if depth > MAX_NODE_DEPTH:
            self.omit_subtree(OmissionReason.DEPTH_EXCEEDED, node.id, node)
            return False
        if not is_node_visible(node.extras.get("visible"), self.sources):
            self.omit_subtree(OmissionReason.NOT_VISIBLE, node.id, node)
            return False
        if self.is_hidden(node):
            self.omit_subtree(OmissionReason.HIDDEN, node.id, node)
            return False
        return _NodeWalk(self, depth, node).run()

    def walk_children(self, depth: int, kids: list[Node]) -> bool:
        # Every child is walked — its omissions matter even after a sibling spoke — so
        # this never short-circuits.
        spoke = False
        for kid in kids:
            spoke = self.walk(depth + 1, kid) or spoke
        return spoke


class _NodeWalk:
    """The per-node arms: what a visible, unhidden node says, per its kind's ruling."""

    def __init__(self, p: _Projector, depth: int, node: Node) -> None:
        self.p = p
        self.depth = depth
        self.node = node
        speak = p.a11y(node).get("speak")
        self.speak = normalise(p.text(speak)) if speak is not None else ""

    def say_self(self, own: _Own) -> bool:
        """``speak`` REPLACES what the node says of itself. It does not silence a structural
        node's children — an author naming a section still wants the section read — and an
        announced-only node's content stays unread either way."""
        if self.speak != "":
            return self.p.say(self.node.id, UtteranceSource.SPEAK, (self.speak, False, Pause.SHORT))
        spoke = False
        for line in own.lines:
            spoke = self.p.say(self.node.id, own.source, line) or spoke
        return spoke

    def leaf(self, own: _Own) -> bool:
        """A leaf with nothing to say from its own content falls back to its accessible
        label; failing that it is reported as nothing sayable."""
        spoke = self.say_self(own) or (
            self.speak == ""
            and self.p.say(self.node.id, UtteranceSource.DERIVED, _sentence(self.p.accessible_label(self.node)))
        )
        if not spoke:
            self.p.omit_one(OmissionReason.NOTHING_SAYABLE, self.node)
        return spoke

    def container(self, own: _Own, kids: list[Node]) -> bool:
        """A structure carrier: its own lines (a heading), then its children. Reported as
        nothing sayable only when neither it nor any descendant said anything."""
        spoke_self = self.say_self(own)
        from_kids = self.p.walk_children(self.depth, kids)
        if not (spoke_self or from_kids):
            self.p.omit_one(OmissionReason.NOTHING_SAYABLE, self.node)
        return spoke_self or from_kids

    def announced(self, own: _Own) -> bool:
        """An announced-only node: the announcement, and every structural descendant
        reported as inside it."""
        spoke = self.say_self(own)
        for child in _structural_children(self.node):
            self.p.omit_subtree(OmissionReason.INSIDE_ANNOUNCED, self.node.id, child)
        if not spoke:
            self.p.omit_one(OmissionReason.NOTHING_SAYABLE, self.node)
        return spoke

    def silent(self) -> bool:
        """An omitted-by-ruling node: nothing unless ``speak`` supplies words."""
        if self.speak != "":
            return self.p.say(self.node.id, UtteranceSource.SPEAK, (self.speak, False, Pause.SHORT))
        self.p.omit_subtree(OmissionReason.NOTHING_SAYABLE, self.node.id, self.node)
        return False

    def run(self) -> bool:
        arm = _ARMS.get(self.node.kind.tag or "")
        # A kind outside the canonical set (the completeness test pins every canonical
        # kind to an arm) says nothing and is reported, never silently dropped.
        return arm(self, self.node.kind.fields) if arm is not None else self.silent()

    # ── Structure (derived: a heading at most, then the children) ────────────

    def box(self, f: dict[str, Value]) -> bool:
        heading = f.get("heading")
        own = _derived([_heading(self.p.text(heading))] if f.get("role") == "Card" and heading is not None else [])
        return self.container(own, _child_nodes(f))

    def plain_container(self, f: dict[str, Value]) -> bool:
        return self.container(_derived([]), _child_nodes(f))

    def summary_list(self, f: dict[str, Value]) -> bool:
        heading = f.get("heading")
        return self.container(
            _derived([_heading(self.p.text(heading))] if heading is not None else []), _child_nodes(f)
        )

    def disclosure(self, f: dict[str, Value]) -> bool:
        return self.container(_derived([_heading(self.p.text(f.get("heading")))]), _child_nodes(f))

    def error_boundary(self, f: dict[str, Value]) -> bool:
        fallback = _as_node(f.get("fallback"))
        if fallback is not None:
            self.p.omit_subtree(OmissionReason.NOT_TAKEN, self.node.id, fallback)
        child = _as_node(f.get("child"))
        return self.container(_derived([]), [child] if child is not None else [])

    def switch(self, f: dict[str, Value]) -> bool:
        chosen_raw = selected_switch_branch(f, self.p.sources)
        cases = f.get("cases")
        branches: list[Value | None] = (
            [c.fields.get("child") for c in cases.items if isinstance(c, Obj)] if isinstance(cases, Arr) else []
        )
        branches.append(f.get("default"))
        for raw in branches:
            branch = _as_node(raw)
            if branch is not None and raw is not chosen_raw:
                self.p.omit_subtree(OmissionReason.NOT_TAKEN, self.node.id, branch)
        chosen = _as_node(chosen_raw)
        return self.container(_derived([]), [chosen] if chosen is not None else [])

    def fragment_ref(self, f: dict[str, Value]) -> bool:
        name = f.get("name")
        body = self.p.fragments.get(name) if isinstance(name, str) else None
        if body is not None:
            return self.container(_derived([]), [body])
        if self.speak != "":
            return self.p.say(self.node.id, UtteranceSource.SPEAK, (self.speak, False, Pause.SHORT))
        self.p.omit_one(OmissionReason.UNRESOLVED_FRAGMENT, self.node, name if isinstance(name, str) else "")
        return False

    # ── Spoken: authored prose, as written ───────────────────────────────────

    def heading(self, f: dict[str, Value]) -> bool:
        return self.leaf(_derived([_heading(self.p.text(f.get("text")))]))

    def markdown(self, f: dict[str, Value]) -> bool:
        blocks = _markdown_blocks(self.p.policy, self.p.text(f.get("text")))
        return self.leaf(_derived([_sentence(b) for b in blocks]))

    def callout(self, f: dict[str, Value]) -> bool:
        heading = f.get("heading")
        lines = [_heading(self.p.text(heading))] if heading is not None else []
        lines.append(_sentence(self.p.text(f.get("body"))))
        return self.leaf(_derived(lines))

    def list_(self, f: dict[str, Value]) -> bool:
        raw = f.get("items")
        items = raw.items if isinstance(raw, Arr) else []
        ordered = f.get("ordered") is True
        lines = [
            _sentence(f"{i + 1}. {self.p.text(item)}" if ordered else self.p.text(item)) for i, item in enumerate(items)
        ]
        return self.leaf(_derived(lines))

    def toast(self, f: dict[str, Value]) -> bool:
        if resolve_binding(f.get("open"), self.p.sources) is True:
            return self.leaf(_derived([_sentence(self.p.text(f.get("message")))]))
        self.p.omit_one(OmissionReason.CLOSED, self.node)
        return False

    # ── Derived: composed from typed fields ──────────────────────────────────

    def metric(self, f: dict[str, Value]) -> bool:
        value = self.p.figure(f.get("format"), f.get("value"))
        trend_binding = f.get("trend")
        trend = ""
        if trend_binding is not None:
            t = resolve_scalar_number(trend_binding, self.p.sources)
            if t is not None:
                trend = ", trend " + format_number(f.get("trendFormat"), t)
        lines = [_sentence(self.p.text(f.get("label")) + ": " + value + trend)]
        subtext = f.get("subtext")
        if subtext is not None:
            lines.append(_sentence(self.p.text(subtext)))
        return self.leaf(_derived(lines))

    def fact(self, f: dict[str, Value]) -> bool:
        lines = [_sentence(self.p.text(f.get("label")) + ": " + self.p.text(f.get("value")))]
        help_text = f.get("help")
        if help_text is not None:
            lines.append(_sentence(self.p.text(help_text)))
        return self.leaf(_derived(lines))

    def label_value_row(self, f: dict[str, Value]) -> bool:
        line = self.p.text(f.get("label")) + ": " + self.p.figure(f.get("format"), f.get("value"))
        return self.leaf(_derived([_sentence(line)]))

    def badge(self, f: dict[str, Value]) -> bool:
        return self.leaf(_derived([_sentence(self.p.text(f.get("label")))]))

    def link(self, f: dict[str, Value]) -> bool:
        return self.leaf(_derived([_sentence("Link: " + self.p.text(f.get("label")))]))

    def image(self, f: dict[str, Value]) -> bool:
        alt = normalise(self.p.text(f.get("alt")))
        if alt == "" and self.speak == "":
            self.p.omit_one(OmissionReason.DECORATIVE, self.node)
            return False
        return self.leaf(_derived([_sentence("Image: " + alt)]))

    def progress(self, f: dict[str, Value]) -> bool:
        if f.get("indeterminate") is True:
            status = "in progress"
        else:
            v = resolve_binding(f.get("fraction"), self.p.sources)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                status = f"{round(max(0.0, min(1.0, float(v))) * 100.0)} percent"
            else:
                status = "progress unknown"
        label_value = f.get("label")
        label = normalise(self.p.text(label_value)) if label_value is not None else ""
        return self.leaf(_derived([_sentence((label if label != "" else "Progress") + ": " + status)]))

    def icon(self, f: dict[str, Value]) -> bool:
        label = f.get("label")
        return self.leaf(_derived([_sentence(label)] if isinstance(label, str) else []))

    # ── Announced-only: what the node is, never its content ─────────────────

    def _optional_text(self, value: Value | None) -> str:
        return self.p.text(value) if value is not None else ""

    def button(self, f: dict[str, Value]) -> bool:
        return self.announced(_announcement("Button", self.p.label_or(self.node, self.p.text(f.get("label")))))

    def form(self, f: dict[str, Value]) -> bool:
        detail = _count(_arr_len(f.get("fields")), "field", "fields")
        return self.announced(_announcement("Form", self.p.accessible_label(self.node), detail))

    def filters(self, f: dict[str, Value]) -> bool:
        detail = _count(_arr_len(f.get("items")), "field", "fields")
        return self.announced(_announcement("Filters", self.p.accessible_label(self.node), detail))

    def labelled(self, noun: str, key: str) -> Callable[[dict[str, Value]], bool]:
        return lambda f: self.announced(
            _announcement(noun, self.p.label_or(self.node, self._optional_text(f.get(key))))
        )

    def named(self, noun: str) -> bool:
        return self.announced(_announcement(noun, self.p.accessible_label(self.node)))

    def tabs(self, f: dict[str, Value]) -> bool:
        detail = _count(_arr_len(f.get("children")), "tab", "tabs")
        return self.announced(_announcement("Tabbed section", self.p.accessible_label(self.node), detail))

    def stepper(self, f: dict[str, Value]) -> bool:
        detail = _count(_arr_len(f.get("children")), "step", "steps")
        return self.announced(_announcement("Step-by-step section", self.p.accessible_label(self.node), detail))

    def media(self, f: dict[str, Value]) -> bool:
        kind = f.get("kind")
        noun = "Audio" if isinstance(kind, Obj) and kind.tag == "Audio" else "Video"
        return self.announced(_announcement(noun, self.p.label_or(self.node, self.p.text(f.get("label")))))

    def drawing(self, f: dict[str, Value]) -> bool:
        own = _announcement("Diagram", self.p.label_or(self.node, self._optional_text(f.get("title"))))
        description = f.get("description")
        if description is not None:
            own.lines.append(_sentence(self.p.text(description)))
        return self.announced(own)

    def custom(self, f: dict[str, Value]) -> bool:
        component = f.get("componentId")
        fallback = component if isinstance(component, str) else ""
        return self.announced(_announcement("Component", self.p.label_or(self.node, fallback)))

    def code_block(self, f: dict[str, Value]) -> bool:
        code = f.get("code")
        code = code if isinstance(code, str) else ""
        lines = 0 if code == "" else len(code.rstrip("\n\r").split("\n"))
        language = f.get("language")
        language = language if isinstance(language, str) else ""
        noun = "Code block" if language.strip() == "" else "Code block (" + normalise(language) + ")"
        return self.announced(_announcement(noun, self.p.accessible_label(self.node), _count(lines, "line", "lines")))

    def data_grid(self, f: dict[str, Value]) -> bool:
        static_rows = f.get("staticRows")
        detail = None
        if isinstance(static_rows, Obj):
            rows = _count(_arr_len(static_rows.fields.get("rows")), "row", "rows")
            detail = rows + ", " + _count(_arr_len(static_rows.fields.get("headers")), "column", "columns")
        return self.announced(_announcement("Table", self.p.accessible_label(self.node), detail))


def _labelled(noun: str, key: str) -> Callable[[_NodeWalk, dict[str, Value]], bool]:
    return lambda w, f: w.labelled(noun, key)(f)


def _named(noun: str) -> Callable[[_NodeWalk, dict[str, Value]], bool]:
    return lambda w, f: w.named(noun)


def _silent(w: _NodeWalk, f: dict[str, Value]) -> bool:
    return w.silent()


#: Kind → arm. The rows mirror :data:`SCOPE` one for one; ``tests/test_speech.py`` checks
#: that the two key sets agree, so an arm cannot exist without a declared ruling.
_ARMS: Final[dict[str, Callable[[_NodeWalk, dict[str, Value]], bool]]] = {
    "Badge": _NodeWalk.badge,
    "Box": _NodeWalk.box,
    "Button": _NodeWalk.button,
    "Callout": _NodeWalk.callout,
    "Chart": _labelled("Chart", "title"),
    "CodeBlock": _NodeWalk.code_block,
    "Custom": _NodeWalk.custom,
    "DataGrid": _NodeWalk.data_grid,
    "Disclosure": _NodeWalk.disclosure,
    "Drawing": _NodeWalk.drawing,
    "Embed": _labelled("Embedded view", "title"),
    "ErrorBoundary": _NodeWalk.error_boundary,
    "Fact": _NodeWalk.fact,
    "FileUpload": _labelled("File upload", "label"),
    "Filters": _NodeWalk.filters,
    "Form": _NodeWalk.form,
    "FragmentDecl": _silent,
    "FragmentRef": _NodeWalk.fragment_ref,
    "Heading": _NodeWalk.heading,
    "Icon": _NodeWalk.icon,
    "Image": _NodeWalk.image,
    "LabelValueRow": _NodeWalk.label_value_row,
    "Link": _NodeWalk.link,
    "List": _NodeWalk.list_,
    "Map": _named("Map"),
    "Markdown": _NodeWalk.markdown,
    "Math": _named("Formula"),
    "Media": _NodeWalk.media,
    "Metric": _NodeWalk.metric,
    "Modal": _labelled("Dialog", "heading"),
    "Mount": _named("Embedded view"),
    "Progress": _NodeWalk.progress,
    "ScrollArea": _NodeWalk.plain_container,
    "Select": _labelled("Selection", "label"),
    "Skeleton": _silent,
    "Sparkline": _named("Trend line"),
    "SplitPanel": _NodeWalk.plain_container,
    "Stepper": _NodeWalk.stepper,
    "SummaryList": _NodeWalk.summary_list,
    "Switch": _NodeWalk.switch,
    "Tabs": _NodeWalk.tabs,
    "Toast": _NodeWalk.toast,
    "Tree": _named("Hierarchy"),
}


def project(
    node: Node,
    sources: BindingSourcesLike | None = None,
    *,
    egress_policy: EgressPolicy = DENY_NON_LOCAL_EGRESS,
) -> tuple[SpeechScript, list[SpeechOmission]]:
    """Project a tree to a spoken script and the list of nodes absent from it.

    ``sources`` resolves non-``Static`` bindings exactly as
    :func:`~fuaran_ui.renderer.render_html` does, including the state seeds the tree
    declares for itself, so a script and the page it describes read the same values.
    ``egress_policy`` is the policy a ``Markdown`` body is rendered under before its text is
    read — deny-non-local by default, as everywhere in this package.
    """
    fragments: dict[str, Node] = {}
    _collect_fragments(node, fragments)
    projector = _Projector(with_state_seeds(node, sources), fragments, egress_policy)
    projector.walk(1, node)
    return SpeechScript(tuple(projector.said)), projector.omitted


# ─── Lowerings ──────────────────────────────────────────────────────────────


def to_plain_text(script: SpeechScript) -> str:
    """Plain text: one utterance per line, in order, each line ending in ``\\n``.

    Pauses and emphasis have no plain-text form beyond the line break, and are dropped rather
    than approximated with punctuation a synthesiser would read.
    """
    return "".join(u.text + "\n" for u in script.utterances)


_BREAKS: Final = {Pause.NONE: "", Pause.SHORT: '<break strength="medium"/>', Pause.LONG: '<break strength="strong"/>'}


def to_ssml(script: SpeechScript) -> str:
    """SSML: a ``<speak>`` document, one ``<s>`` per utterance, emphasis as
    ``<emphasis level="moderate">``, and pauses as ``<break>`` strengths. Every text passes
    :func:`escape_ssml`; the only markup in the document is written here."""
    out = ["<speak>\n"]
    for u in script.utterances:
        body = escape_ssml(u.text)
        if u.emphasis:
            body = '<emphasis level="moderate">' + body + "</emphasis>"
        out.append("<s>" + body + "</s>" + _BREAKS[u.pause_after] + "\n")
    out.append("</speak>\n")
    return "".join(out)


__all__ = [
    "SCOPE",
    "OmissionReason",
    "Pause",
    "SpeechClass",
    "SpeechOmission",
    "SpeechScript",
    "Utterance",
    "UtteranceSource",
    "escape_ssml",
    "expected_source",
    "normalise",
    "project",
    "to_plain_text",
    "to_ssml",
]
