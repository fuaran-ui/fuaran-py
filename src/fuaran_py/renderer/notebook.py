"""Rich display of a Fuaran tree in a notebook front end (fuaran#1161).

The Jupyter display protocol is a **method name**, not an import: a front end
that follows it calls ``_repr_mimebundle_(include, exclude)`` on the value a cell
evaluates to and renders the richest representation it understands. So this host
renders a dashboard inline in Jupyter, JupyterLab, VS Code or marimo with nothing
added to its dependency set — the package stays standard-library-only, and
``ipython`` is not imported here or anywhere else::

    from fuaran_py.ui import quick

    quick.dashboard("Regional revenue", quick.metric("Revenue", 1284.5))
    # ← the last expression of a cell renders as HTML, in place

Three representations, and each answers a different consumer:

``text/html``
    the shipped server renderer's body fragment (:func:`fuaran_py.renderer.render_html`)
    wrapped in a scoping container, with the reference stylesheet inlined **once
    per output** and rewritten to apply only inside that container. Self-contained
    by construction: an output copied out of its notebook still carries the styles
    it needs, and nothing it carries reaches the page around it.

``application/vnd.fuaran.ui+json``
    the canonical wire JSON, as the **string** :func:`fuaran_py.encode_node`
    produced. A vendor media type rather than ``application/json`` so a front end
    or a later widget can recognise the payload as a Fuaran tree; a string rather
    than a decoded object because this host's contract is byte-identity with the
    corpus, and handing a front end a ``dict`` to re-serialise discards exactly
    the property the bytes are evidence of.

``text/plain``
    a one-line summary, for a consumer with no HTML (a terminal REPL, a diff of a
    recorded notebook, a log). It names the tree rather than reproducing it —
    ``repr()`` remains the full structural view and is untouched.

**Static and read-only, deliberately.** The emitted HTML carries no script and no
channel back to the kernel: a ``Button`` is inert and a non-``Static`` binding
shows the renderer's placeholder, exactly as they do in server-side output.
Interactivity and in-place patching of an already-rendered cell are a different
problem with different dependencies, and they are not solved here by half-measure.

**And it fetches nothing this display path introduced.** The stated bound is
narrower than "no external fetch", because the wider claim would not be true of
every tree: the inlined stylesheet carries no ``url(...)`` and no ``@import``, so
the wrapper itself contacts nothing — while a destination the *tree* declares (an
``Image`` source, an ``Embed`` frame) is subject to the ambient destination
policy below, which refuses a non-local one unless a host has named a wider
posture.

**Sanitisation is the renderer's, not a second one.** The HTML is whatever
:func:`~fuaran_py.renderer.render_html` returns, byte for byte, so every ``href``
and ``src`` has passed the ambient destination policy — which defaults to
deny-non-local (WIRE_FORMAT §14.1). Nothing here re-escapes, post-processes or
widens that output; a policy is declared the same way it is for any other render,
by name, through :func:`mimebundle`.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

from ..model import Arr, Node, Obj, Value
from ..schema.encode import encode_node
from .bindings import BindingSources
from .egress import DENY_NON_LOCAL_EGRESS, EgressPolicy
from .render import render_html

# The vendor media type the canonical wire travels under. Registered nowhere and
# deliberately vendor-scoped: `application/json` would invite a front end's
# generic JSON viewer to claim the output, and this payload is a Fuaran tree
# first and JSON second.
FUARAN_UI_MIME = "application/vnd.fuaran.ui+json"

# The wrapper's marker attribute, and the selector every rule of the inlined
# stylesheet is rewritten to sit under. An ATTRIBUTE rather than a class: the
# reference `fuaran-*` CLASS vocabulary is parity-locked to the reference host
# (tests/test_render_parity.py), and a wrapper this host invents has no business
# appearing in it.
NOTEBOOK_OUTPUT_ATTR = "data-fuaran-notebook-output"
_SCOPE = f"[{NOTEBOOK_OUTPUT_ATTR}]"

# At-rules that DEFINE rather than SELECT: their bodies carry no page selectors,
# so scoping them would corrupt them (`@keyframes` bodies are percentages).
_VERBATIM_AT = frozenset({"keyframes", "font-face", "property", "counter-style", "page", "charset", "namespace"})

# At-rules that WRAP style rules: recurse, so the rules inside are scoped.
_NESTED_AT = frozenset({"media", "supports", "container", "layer", "scope"})


class UnscopableCss(ValueError):
    """The stylesheet contains a construct this scoper will not silently pass through.

    Raised rather than skipped, and that is the point. The reference stylesheet is
    a byte-for-byte copy of the canonical one, so a new construct in it is a
    deliberate cross-host change — and the two failure modes of quietly copying an
    unrecognised at-rule are that it escapes the container (a rule that styles the
    notebook around the output) or that it fetches (``@import``). Either is worse
    than a loud failure in a test the moment the copy is re-synced.
    """


# ── Scoping the reference stylesheet ─────────────────────────────────────────
#
# The reference stylesheet is written for a whole document: it sets custom
# properties on `:root`, a background and font on `body`, and a handful of
# document-level state hooks. Injected raw into a notebook it would restyle the
# page around the output. Scoping rewrites every selector so it can only match
# inside the wrapper, and substitutes the wrapper for the document-level subjects
# so the custom properties still cascade to the rendered tree.


def _skip_string(css: str, i: int) -> int:
    """Index just past the string literal opening at ``i``."""
    quote = css[i]
    i += 1
    while i < len(css):
        if css[i] == "\\":
            i += 2
            continue
        if css[i] == quote:
            return i + 1
        i += 1
    raise UnscopableCss("unterminated string literal in the stylesheet")


def _skip_comment(css: str, i: int) -> int:
    end = css.find("*/", i + 2)
    if end == -1:
        raise UnscopableCss("unterminated comment in the stylesheet")
    return end + 2


def _scan_to_delimiter(css: str, i: int) -> tuple[int, str]:
    """Index and kind of the next top-level ``{`` or ``;``, skipping strings/comments."""
    depth = 0
    while i < len(css):
        ch = css[i]
        if ch in "\"'":
            i = _skip_string(css, i)
            continue
        if css.startswith("/*", i):
            i = _skip_comment(css, i)
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and ch in "{;":
            return i, ch
        i += 1
    raise UnscopableCss("stylesheet ends inside a selector or at-rule prelude")


def _matching_brace(css: str, open_index: int) -> int:
    """Index of the ``}`` closing the ``{`` at ``open_index``."""
    depth = 0
    i = open_index
    while i < len(css):
        ch = css[i]
        if ch in "\"'":
            i = _skip_string(css, i)
            continue
        if css.startswith("/*", i):
            i = _skip_comment(css, i)
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise UnscopableCss("unbalanced braces in the stylesheet")


def _split_top_level(text: str, separator: str) -> list[str]:
    """Split on ``separator`` at bracket/paren depth 0, outside strings and comments."""
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'":
            i = _skip_string(text, i)
            continue
        if text.startswith("/*", i):
            i = _skip_comment(text, i)
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and ch == separator:
            parts.append(text[start:i])
            start = i + 1
        i += 1
    parts.append(text[start:])
    return parts


def _split_first_compound(selector: str) -> tuple[str, str]:
    """The selector's first compound, and the remainder including its combinator.

    ``'[dir="rtl"] .fuaran-x::after'`` → ``('[dir="rtl"]', ' .fuaran-x::after')``;
    ``'html[data-x]::before'`` → ``('html[data-x]::before', '')``.
    """
    depth = 0
    i = 0
    while i < len(selector):
        ch = selector[i]
        if ch in "\"'":
            i = _skip_string(selector, i)
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and (ch.isspace() or ch in ">+~"):
            return selector[:i], selector[i:]
        i += 1
    return selector, ""


def _is_type_token(compound: str, name: str) -> bool:
    """Does ``compound`` open with the element type selector ``name``?"""
    if not compound.lower().startswith(name):
        return False
    tail = compound[len(name) :]
    return not (tail and (tail[0].isalnum() or tail[0] in "-_"))


def _scope_selector(selector: str, scope: str) -> str:
    """Rewrite one selector so its subject can only be inside ``scope``."""
    stripped = selector.strip()
    if not stripped:
        return selector
    first, rest = _split_first_compound(stripped)
    # A document-level SUBJECT (`:root`, `html`, `body`) becomes the wrapper, so
    # the custom properties, background and font those rules carry land on the
    # container and cascade into the rendered tree exactly as they would in a page.
    for token in (":root", "html", "body"):
        if _is_type_token(first, token):
            return scope + first[len(token) :] + rest
    # A bare attribute selector leading a DESCENDANT selector is a document-level
    # state hook the rule reads as context (`[dir="rtl"] .fuaran-…`), not as its
    # subject. Prefixing it would move the hook inside the container, where it
    # never appears, and silently kill the rule. Insert the scope AFTER it: the
    # subject is still confined to the container, which is the property that
    # matters; what it gains is the ability to answer a page-level flag.
    if rest and first.startswith("["):
        return f"{first} {scope}{rest}"
    return f"{scope} {stripped}"


def _scope_selector_list(selectors: str, scope: str) -> str:
    return ", ".join(_scope_selector(part, scope) for part in _split_top_level(selectors, ","))


def _scope_block(css: str, scope: str, out: list[str]) -> None:
    i = 0
    n = len(css)
    while i < n:
        ch = css[i]
        if ch.isspace():
            out.append(ch)
            i += 1
            continue
        if css.startswith("/*", i):
            end = _skip_comment(css, i)
            out.append(css[i:end])
            i = end
            continue
        delimiter, kind = _scan_to_delimiter(css, i)
        prelude = css[i:delimiter].strip()
        if kind == ";":
            # A statement at-rule. `@import` FETCHES, which this output promises
            # not to do; anything else unrecognised cannot be shown to be inert.
            raise UnscopableCss(f"unsupported statement at-rule in the stylesheet: {prelude[:60]!r}")
        close = _matching_brace(css, delimiter)
        body = css[delimiter + 1 : close]
        if prelude.startswith("@"):
            name = prelude[1:].split(None, 1)[0].split("(", 1)[0].lower()
            if name in _VERBATIM_AT:
                out.append(f"{prelude} {{{body}}}")
            elif name in _NESTED_AT:
                out.append(f"{prelude} {{")
                _scope_block(body, scope, out)
                out.append("}")
            else:
                raise UnscopableCss(f"unsupported at-rule in the stylesheet: {prelude[:60]!r}")
        else:
            out.append(f"{_scope_selector_list(prelude, scope)} {{{body}}}")
        i = close + 1


def scope_css(css: str, scope: str = _SCOPE) -> str:
    """Rewrite ``css`` so every rule applies only inside an element matching ``scope``.

    Raises :class:`UnscopableCss` for any construct that cannot be shown to be
    both inert and confined — see that class for why a refusal is preferable to a
    pass-through.
    """
    out: list[str] = []
    _scope_block(css, scope, out)
    return "".join(out)


@lru_cache(maxsize=1)
def scoped_reference_css() -> str:
    """The reference stylesheet, scoped to the notebook-output wrapper.

    Cached: the input is a file shipped inside the package and the transform is
    pure, so every output in a session inlines byte-identical CSS.
    """
    from . import reference_css

    scoped = scope_css(reference_css(), _SCOPE)
    # `<style>` is an HTML raw-text element: its content ends at the first
    # `</style`, and ONLY there — a `</span>` in a comment (the stylesheet has
    # one) is ordinary text and is why this guard is not the broader `</`. If the
    # terminator ever does appear in the canonical copy, the inline form stops
    # being safe and this must fail rather than emit markup that closes early.
    if "</style" in scoped.lower():
        raise UnscopableCss("the stylesheet contains '</style', which cannot be inlined in a <style> element")
    return scoped


# ── The bundle ───────────────────────────────────────────────────────────────


def _count_nodes(value: Value) -> int:
    if isinstance(value, Node):
        return 1 + _count_nodes(value.kind) + sum(_count_nodes(v) for v in value.extras.values())
    if isinstance(value, Obj):
        return sum(_count_nodes(v) for v in value.fields.values())
    if isinstance(value, Arr):
        return sum(_count_nodes(item) for item in value.items)
    return 0


def summary_line(node: Node) -> str:
    """The one-line ``text/plain`` summary: what the tree is, not what it contains."""
    kind = node.kind.tag or "?"
    count = _count_nodes(node)
    nodes = "1 node" if count == 1 else f"{count} nodes"
    return f"Fuaran UI tree {node.id!r} — {kind}, {nodes} (repr() for the full structure)"


def display_html(
    node: Node,
    sources: BindingSources | None = None,
    egress_policy: EgressPolicy = DENY_NON_LOCAL_EGRESS,
) -> str:
    """The ``text/html`` representation: the scoped stylesheet plus the render, wrapped.

    The fragment is :func:`~fuaran_py.renderer.render_html`'s output unchanged —
    concatenated, never rewritten — so the byte-for-byte relationship between what
    a server serves and what a notebook shows is a property of the code rather
    than a claim about it.
    """
    fragment = render_html(node, sources, egress_policy=egress_policy)
    return f"<div {NOTEBOOK_OUTPUT_ATTR}><style>{scoped_reference_css()}</style>{fragment}</div>"


def mimebundle(
    node: Node,
    include: Iterable[str] | None = None,
    exclude: Iterable[str] | None = None,
    *,
    sources: BindingSources | None = None,
    egress_policy: EgressPolicy = DENY_NON_LOCAL_EGRESS,
) -> dict[str, str]:
    """The Jupyter display bundle for a decoded tree.

    ``include`` / ``exclude`` are the display protocol's own filters, applied in
    that order: a front end that wants only one representation asks for it, and
    the ones it did not ask for are not built.
    """
    wanted = {"text/html", FUARAN_UI_MIME, "text/plain"}
    if include is not None:
        wanted &= set(include)
    if exclude is not None:
        wanted -= set(exclude)
    bundle: dict[str, str] = {}
    if "text/html" in wanted:
        bundle["text/html"] = display_html(node, sources, egress_policy)
    if FUARAN_UI_MIME in wanted:
        bundle[FUARAN_UI_MIME] = encode_node(node)
    if "text/plain" in wanted:
        bundle["text/plain"] = summary_line(node)
    return bundle


__all__ = [
    "FUARAN_UI_MIME",
    "NOTEBOOK_OUTPUT_ATTR",
    "UnscopableCss",
    "display_html",
    "mimebundle",
    "scope_css",
    "scoped_reference_css",
    "summary_line",
]
