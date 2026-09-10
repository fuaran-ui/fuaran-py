"""Render-time injection-safety floor — the Python port of ``Sanitize.fs``.

The wire decoder is best-effort: a malicious AI emission can smuggle an
``ExtraAttributes`` key, an ``onerror=`` handler inside markdown source, or a
``javascript:`` href through the decode path. This module is the last line of
defence before bytes reach a browser's HTML parser, and it mirrors the F#/TS
renderers' posture seam-for-seam so the three hosts cannot drift on safety
(see ``fuaran-dotnet/SANITIZATION.md``):

1. **ExtraAttributes** — drop ``on*`` handlers, ``style``, and anything outside
   the ``data-*`` / ``aria-*`` allowlist; reject values carrying control bytes
   or angle brackets.
2. **URL props** — block ``javascript:`` / ``vbscript:`` / ``file:`` and any
   unknown scheme; allow ``http`` / ``https`` / ``mailto`` / ``tel`` / ``ftp`` /
   ``sftp`` + same-origin relative paths.
3. **Markdown raw HTML** — strip dangerous element blocks, inline ``on*=``
   handlers, and ``javascript:`` / ``vbscript:`` URLs from rendered markdown.

The ``Custom`` host-renderer registry is a host trust boundary, not an
AI-emission surface — this module does not police it (and ``fuaran-py``'s
baseline renderer ships no host-registry seam; ``Custom`` renders an inert
labelled placeholder).
"""

from __future__ import annotations

import re
import string

# ── Case folding ────────────────────────────────────────────────────────────

_ASCII_FOLD = str.maketrans(string.ascii_uppercase, string.ascii_lowercase)


def _ascii_lower(s: str) -> str:
    """ASCII-only lowercase — length-preserving, which ``str.lower()`` is not.

    ``"İ".lower()`` is two code points (U+0069 U+0307), so every place below
    that searches a case-folded COPY and splices the resulting indices back
    into the ORIGINAL string depends on a fold that cannot change length. A
    locale-aware fold silently shifts the removal window and leaves a fragment
    of the element it meant to remove. The tag / scheme / protocol vocabulary
    this module matches is ASCII, so an ASCII-only fold loses no matches.
    """
    return s.translate(_ASCII_FOLD)


# ── ExtraAttributes key/value sanitization ─────────────────────────────────

_CONTROL_OR_ANGLE = re.compile(r"[\x00-\x08\x0a-\x1f<>]")


_SAFE_ATTRIBUTE_NAME_CHARS = frozenset(string.ascii_letters + string.digits + "-")


def is_safe_attribute_name(name: str) -> bool:
    """Positive character allowlist for an HTML attribute NAME: ``[A-Za-z0-9-]``.

    Everything else — ``=``, quotes, backtick, ``<``, ``>``, ``/``, space, tab,
    newline, C0 controls, any non-ASCII byte — is rejected.

    This is a **rejection** gate, not an escape, because HTML has no escape for an
    illegal character in an attribute name: a space inside a name simply starts a
    NEW attribute and an ``=`` starts its value. So
    ``data-x=1 onmouseover=alert(1) z`` is not a mangled attribute name — it is
    three attributes, one of them a live event handler. Renderers escape attribute
    *values*, never *names*, so dropping the entry is the only sound response.

    Exported so an emission site can re-check it as defence in depth rather than
    trusting upstream validation alone.
    """
    if not name:
        return False
    return all(ch in _SAFE_ATTRIBUTE_NAME_CHARS for ch in name)


def is_allowed_extra_attribute_key(key: str) -> bool:
    """The ``data-*`` / ``aria-*`` allowlist, with an explicit ``on*`` / ``style`` reject.

    Plus :func:`is_safe_attribute_name` over the whole trimmed key — without it a
    key like ``data-x=1 onmouseover=alert(1) z`` satisfies the ``data-`` prefix and
    smuggles a live event handler into rendered HTML.

    The predicate judges the **trimmed** form, so a caller using it directly must
    trim before emission too.
    """
    if key is None:
        return False
    trimmed = key.strip()
    if trimmed == "":
        return False
    if trimmed.lower().startswith("on"):
        # Any `on*` event-handler attribute, even hand-constructed.
        return False
    if trimmed.lower() == "style":
        # CSS-injection vector (`expression()`, `url(javascript:…)`).
        return False
    if not is_safe_attribute_name(trimmed):
        # Attribute-NAME injection: any character that could terminate the name
        # and open a second attribute at the emission site.
        return False
    return trimmed.startswith("data-") or trimmed.startswith("aria-")


def is_safe_extra_attribute_value(value: str) -> bool:
    """Reject control / NUL bytes and angle brackets (``\\t`` is allowed)."""
    if value is None:
        return False
    return _CONTROL_OR_ANGLE.search(value) is None


def sanitize_extra_attributes(attrs: dict[str, str]) -> dict[str, str]:
    """Filter a candidate attribute map down to entries that pass both predicates.

    The re-key is load-bearing: the predicate judges ``key.strip()``, so emitting
    the untrimmed key would emit something the gate never inspected.
    """
    return {
        k.strip(): v for k, v in attrs.items() if is_allowed_extra_attribute_key(k) and is_safe_extra_attribute_value(v)
    }


# ── URL-scheme sanitization ─────────────────────────────────────────────────

_ALLOWED_URL_SCHEMES = frozenset({"http", "https", "mailto", "tel", "ftp", "sftp"})
_REJECTED_URL_SCHEMES = frozenset({"javascript", "vbscript", "file"})


def _extract_scheme(url: str) -> str | None:
    """Return the lowercased scheme, or ``None`` for a relative / fragment URL.

    Looks for the first ``:`` before any ``/`` ``?`` ``#``. Whitespace and
    control chars inside the scheme region are stripped first so ``java\\tscript``,
    ``  javascript`` and ``JAVASCRIPT`` all classify as ``javascript``.
    """
    colon_idx = -1
    slash_idx = -1
    for i, ch in enumerate(url):
        if ch == ":":
            colon_idx = i
            break
        if ch in "/?#":
            slash_idx = i
            break
    if colon_idx < 0 or (0 <= slash_idx < colon_idx):
        return None
    raw = url[:colon_idx]
    cleaned = "".join(ch for ch in raw if ord(ch) > 0x20)
    return cleaned.strip().lower()


def _is_protocol_relative(url: str) -> bool:
    """A protocol-relative URL: ``//host/path`` and the forms browsers fold into it.

    WHATWG URL parsing treats ``\\`` as ``/`` for special schemes, so ``\\\\host``,
    ``/\\host`` and ``\\/host`` all resolve exactly as ``//host`` does.

    These carry no scheme, so the schemeless branch below would otherwise admit
    them — but the browser resolves them against the CURRENT page's scheme and
    lands on an OFF-ORIGIN host, defeating the same-origin intent that makes a
    schemeless URL safe. On an ``href`` that is off-origin navigation; on an
    image ``src`` it is an off-origin request that leaks the Referer.
    """
    return len(url) >= 2 and url[0] in "/\\" and url[1] in "/\\"


_TAB_LF_CR = ("\t", "\n", "\r")


def _normalize_url_for_floor(url: str) -> str:
    """§19 rule 1 — normalise exactly as the WHATWG URL Standard's basic URL parser does.

    Two ordered, ASCII-exact steps applied before the parser parses anything:

    1. remove leading and trailing **C0 control or space** — all of U+0000–U+0020,
       not merely the whitespace subset;
    2. remove every U+0009 / U+000A / U+000D from anywhere in what remains.

    Deliberately **not** :meth:`str.strip`. A native trim answers a different
    question in every language — ``str.strip`` also removes U+001C–U+001F where
    .NET, JS, Go and Rust do not; JS alone keeps U+0085 NEL where the other four
    drop it — and all of them remove non-ASCII whitespace (U+00A0, U+2028, …) that
    the parser keeps. The floor's whole purpose is that a tree vetted on one host
    is safe on another, so the normalisation is defined by the parser that will
    actually consume the string, not by the host's standard library.

    Step 2 is those three code points **only**: the parser removes U+000B and
    U+000C at the edges (step 1) and *keeps* them in the interior, so
    ``/<VT>/host/x`` is an ordinary same-origin path and must stay one.
    """
    lo, hi = 0, len(url)
    while lo < hi and url[lo] <= " ":
        lo += 1
    while hi > lo and url[hi - 1] <= " ":
        hi -= 1
    return "".join(ch for ch in url[lo:hi] if ch not in _TAB_LF_CR)


def sanitize_url(url: str) -> str | None:
    """Return the URL if its scheme is accepted, else ``None`` (default-deny).

    The input is first normalised per §19 rule 1 (see :func:`_normalize_url_for_floor`),
    and that normalised form is also what is **emitted** on acceptance — so an
    accepted URL carrying an interior tab loses it, which is what the browser would
    have parsed anyway.
    """
    if url is None:
        return None
    trimmed = _normalize_url_for_floor(url)
    if trimmed == "":
        # Empty href/src — pass through (a same-page link, documented HTML behaviour).
        return trimmed
    scheme = _extract_scheme(trimmed)
    if scheme is None:
        if _is_protocol_relative(trimmed):
            # Off-origin despite carrying no scheme.
            return None
        # No scheme → relative / fragment / same-origin. Allowed.
        return trimmed
    if scheme in _REJECTED_URL_SCHEMES:
        return None
    if scheme in _ALLOWED_URL_SCHEMES:
        return trimmed
    # Unknown scheme — reject by default (conservative; adding one is additive).
    return None


def sanitize_url_or_blank(url: str) -> str:
    """The URL if accepted, else the literal ``"about:blank"`` (keeps the link valid)."""
    result = sanitize_url(url)
    return result if result is not None else "about:blank"


# ── Markdown raw-HTML sanitization ──────────────────────────────────────────

_DANGEROUS_ELEMENTS = ("script", "iframe", "object", "embed", "form", "link", "meta")
_EVENT_HANDLER = re.compile(r"\son[a-zA-Z]+(\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]*))?", re.IGNORECASE)
# A `<...>` start-tag span. The event-handler sweep runs only *inside* these, never
# over body text — see `_strip_event_handlers`.
_TAG_SPAN = re.compile(r"<[^>]*>")
_DANGEROUS_PROTOCOL = re.compile(r"(?i)(javascript|vbscript):")


def _strip_event_handlers(html: str) -> str:
    """Remove ``on*=`` handlers, but only inside tag interiors.

    The tag-interior anchor is load-bearing: the ``\\son<letter>`` pattern also
    matches the leading-whitespace-``on`` of ordinary English words — "one",
    "only", "once", "onto", "online", … — so running the regex globally deletes
    those words from body text. Because the render path constrains the input to
    the deterministic markdown emitter's output (raw HTML already escaped), a
    real event-handler attribute only appears inside a tag the renderer emitted,
    so scoping the sweep to ``<...>`` spans is both correct and false-positive-free.
    """
    return _TAG_SPAN.sub(lambda m: _EVENT_HANDLER.sub("", m.group(0)), html)


def _is_tag_name_boundary(s: str, index: int) -> bool:
    """Does ``index`` mark the end of a tag NAME?

    An HTML tag name ends at whitespace, `/` or `>`, so a match on the bare
    prefix is a match on a DIFFERENT element: `<metadata>` is not `<meta>`, and
    `<linearGradient>` is not `<link>`. Both are real SVG elements the drawing
    builder emits, and with the bare prefix the first of them lost its opening
    tag to this sweep, leaving the provenance document's text loose in the
    figure.

    Requiring the boundary narrows only false positives: no spelling of a real
    `<meta>` element survives it, because the name has to be delimited for a
    parser to read it as that element in the first place. End of input counts as
    a boundary, so a truncated `...<script` is still stripped.

    Parity-locked with the F# ``Sanitize.sanitizeMarkdownHtml`` and the
    TypeScript renderer's ``sanitize.ts``.
    """
    if index >= len(s):
        return True
    return s[index] in " \t\n\r/>"


def _index_of_element_open(s: str, open_tag: str) -> int:
    """First ``<tag`` whose name is DELIMITED, or ``-1``.

    That is, the first position where ``open_tag`` names the element rather than
    merely prefixing a longer name.
    """
    from_index = 0
    while from_index <= len(s) - len(open_tag):
        i = s.find(open_tag, from_index)
        if i < 0:
            return -1
        if _is_tag_name_boundary(s, i + len(open_tag)):
            return i
        from_index = i + 1
    return -1


def _strip_dangerous_protocols(html: str) -> str:
    """Rewrite ``javascript:`` / ``vbscript:`` URLs, but only inside tag interiors.

    The tag-interior anchor is the same discipline ``_strip_event_handlers``
    already keeps, and it is here for the same reason. Unanchored, this sweep
    rewrote VISIBLE PROSE: the markdown source ``Never write `javascript:` in an
    href`` renders to a ``<code>`` element whose TEXT is the literal token, and
    the substitution replaced it with ``about:blank`` — so a document explaining
    the hazard could not state it, and the reader was shown a sentence the author
    never wrote.

    A real ``javascript:`` URL can only do harm as the VALUE of an attribute —
    ``href``, ``src``, ``action``, ``formaction``, ``xlink:href``, ``data``,
    ``poster`` — and every one of those sits inside a ``<…>`` tag. So restricting
    the scan to tag interiors is not a heuristic narrowing: it is the precise set
    of positions where the token is a URL rather than a word. Outside a tag the
    token is text the markdown renderer has already escaped by construction.

    Reusing ``_TAG_SPAN`` keeps this byte-for-byte with the handler sweep beside
    it, and inherits the same approximation: a ``>`` inside a quoted attribute
    value ends the span early, which SKIPS a rewrite — the direction of error
    that leaves prose intact.
    """
    return _TAG_SPAN.sub(lambda m: _DANGEROUS_PROTOCOL.sub("about:blank", m.group(0)), html)


def sanitize_markdown_html(html: str) -> str:
    """Strip dangerous element blocks, ``on*=`` handlers, and script-scheme URLs.

    Approximate (not a full HTML parser): the render path constrains the input
    to the baseline markdown emitter's output, so a substring/regex sweep is
    sufficient defence in depth. Hosts needing DOMPurify-grade sanitization
    layer it consumer-side — this is the floor, not the ceiling.
    """
    if not html:
        return ""
    result = html
    for tag in _DANGEROUS_ELEMENTS:
        open_tag = "<" + tag
        close_tag = "</" + tag + ">"
        while True:
            # ASCII-only fold: the indices below splice into `result`, so the
            # searched copy must stay index-aligned with it (see `_ascii_lower`).
            folded = _ascii_lower(result)
            i = _index_of_element_open(folded, open_tag)
            if i < 0:
                break
            j = folded.find(close_tag, i)
            if j >= 0:
                result = result[:i] + result[j + len(close_tag) :]
            else:
                end = result.find(">", i)
                result = result[:i] + (result[end + 1 :] if end >= 0 else "")
    result = _strip_event_handlers(result)
    result = _strip_dangerous_protocols(result)
    return result


# ─── Emission grammar for string-typed slots (the CSS / anchor-token half) ───
#
# The Python host's copy of the rule the F# tier declares in
# ``Fuaran.UI.EmissionGrammar``. It sits beside the URL floor above because it
# is the same KIND of rule and reaches the same sinks: a value the type says is
# a ``str`` and the document says is CSS, or an anchor token.
#
# Why every host needs its own copy and why they must agree. ``templateColumns``
# is a free string on the wire, and this renderer concatenated it into
# ``style="grid-template-columns:…"`` with no rule at all — so a value carrying
# a semicolon closed the declaration, opened a second one the document never
# wrote, and (with ``url(…)``) fetched on RENDER, with no user act, outside the
# egress policy that governs every href and src in the same document. The React
# client assigned a style OBJECT and the browser dropped the identical value
# silently. Same tree, exfiltration channel here, inert there. The wire format
# exists to rule exactly that out.

_CSS_FORBIDDEN_CHARS = frozenset(";{}" + chr(92))
_CSS_FORBIDDEN_FUNCTIONS = ("url(", "expression(")

CSS_REFUSAL_VALUE = ""
CSS_REFUSAL_ATTRIBUTE = "data-fuaran-css-refused"


def is_safe_css_value(value: str) -> bool:
    """Is this string safe to concatenate into a CSS declaration?

    A DENYLIST, deliberately, where the URL and token rules above are
    allowlists: a CSS value's grammar is genuinely open (the property set grows,
    the function set grows, and a positive list would refuse ``clamp()`` the day
    CSS shipped it), while the set of characters that let a value LEAVE its
    declaration or reach the network is small, stable and enumerable.

    What it does NOT promise: it is not a CSS parser and says nothing about
    whether the surviving string is a VALID value for the property it lands in.
    An invalid value is dropped by the browser's own parser, which is a
    rendering defect and not a security one. This bounds what a value can REACH.

    ``None`` and the empty string are SAFE — they contribute nothing to the
    declaration, and refusing them would make an absent value indistinguishable
    from a hostile one.
    """
    if not value:
        return True
    for ch in value:
        if ch < " " or ch == "\x7f" or ch in _CSS_FORBIDDEN_CHARS:
            return False
    # Whitespace-tolerant and case-insensitive on the CSS side: ``URL (`` and
    # ``url\n(`` are one token to a CSS tokenizer, so a scan for the literal
    # lowercase spelling alone is a scan a payload walks past.
    squashed = "".join(ch for ch in value if not ch.isspace()).lower()
    return not any(fn in squashed for fn in _CSS_FORBIDDEN_FUNCTIONS)


def sanitize_css_value(value: str) -> str:
    """The CSS value to emit — the value when it passes, empty when it does not.

    Empty rather than a substitute: an empty declaration value is dropped by
    every CSS parser, so the element falls back to the stylesheet's own rule,
    which is what an author who wrote nothing would have got. A substitute would
    be the renderer inventing a layout the document never declared.
    """
    return value if is_safe_css_value(value) else CSS_REFUSAL_VALUE


def sanitize_css_value_for_slot(slot: str, value: str) -> tuple[str, list[tuple[str, str]]]:
    """The CSS value plus the refusal attributes to splice, given the SLOT name.

    The slot name — never the value — rides the marker, the same discipline the
    egress refusal marker keeps and for the same reason: a refused value is the
    payload.
    """
    if is_safe_css_value(value):
        return value or "", []
    return CSS_REFUSAL_VALUE, [(CSS_REFUSAL_ATTRIBUTE, slot)]


def _is_css_ident(value: str) -> bool:
    """Is this a bare CSS IDENT — an ASCII letter or ``-`` then letters, digits, ``-``, ``_``?

    This is what admits the 148 named colours (``red``, ``steelblue``,
    ``rebeccapurple``), the universal keywords (``none``, ``transparent``,
    ``currentColor``), the inheritance keywords, the SVG2 paint keywords
    (``context-fill``, ``context-stroke``) and every colour keyword CSS has not
    shipped yet — as ONE rule rather than as a list somebody has to keep.

    Enumerating the keywords instead is wrong, because the two ways of being
    wrong here are not symmetric. A missing keyword produces no error an author
    can see: the paint is replaced by ``none``, so a document that was correct
    yesterday silently renders a differently-coloured picture. Meanwhile an
    ident buys an attacker nothing at all — it cannot fetch, cannot leave its
    declaration and cannot name a paint server, because every one of those needs
    punctuation this test refuses.
    """
    if not value:
        return False
    head = value[0]
    if not (("a" <= head <= "z") or ("A" <= head <= "Z") or head == "-"):
        return False
    return all(("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9") or c in "-_" for c in value)


_COLOUR_FUNCTIONS = (
    "rgb(",
    "rgba(",
    "hsl(",
    "hsla(",
    "oklch(",
    "oklab(",
    "lch(",
    "lab(",
    "color(",
)
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")


def is_colour_value(value: str) -> bool:
    """Is this a CSS colour in the closed grammar?

    A paint slot needs a POSITIVE grammar where a generic CSS value needs only a
    denylist, and that asymmetry is the finding: ``url(https://collector/x)``
    contains no forbidden character, and in an SVG ``fill`` it names a paint
    server the user agent FETCHES. Only naming what a colour may BE excludes it.
    """
    if not value:
        return False
    t = value.strip()
    if not t:
        return False
    if t.startswith("#"):
        digits = t[1:]
        return len(digits) in (3, 4, 6, 8) and all(c in _HEX_DIGITS for c in digits)
    if _is_css_ident(t):
        return True
    lower = t.lower()
    return any(lower.startswith(fn) for fn in _COLOUR_FUNCTIONS) and lower.endswith(")") and is_safe_css_value(t)


def sanitize_paint_value(value: str) -> str:
    """The SVG paint to emit — the value when it is a colour, ``"none"`` when not.

    ``"none"`` rather than empty, because an EMPTY ``fill`` / ``stroke``
    INHERITS the enclosing group's paint instead of clearing it, so an empty
    refusal would silently paint the shape a different colour rather than
    leaving it unpainted.
    """
    return value.strip() if is_colour_value(value) else "none"


ALLOWED_LINK_TARGETS = frozenset({"_self", "_blank"})

# Every member describes THIS link's relationship to its destination and changes
# nothing about the opener's capabilities in the wrong direction. The one
# deliberate absence is the finding: ``opener`` RE-ENABLES ``window.opener`` on a
# ``_blank`` link, handing the opened document a live reference to the opening
# one — the capability ``noopener`` exists to remove, and one no rendered tree
# has any reason to ask for.
ALLOWED_LINK_REL_TOKENS = frozenset(
    {
        "alternate",
        "author",
        "bookmark",
        "external",
        "help",
        "license",
        "next",
        "nofollow",
        "noopener",
        "noreferrer",
        "prev",
        "privacy-policy",
        "search",
        "tag",
        "terms-of-service",
        "ugc",
    }
)


def sanitize_link_target(target: str | None) -> str | None:
    """The ``target`` to emit, or ``None`` to omit the attribute.

    ``_self`` and ``_blank`` only. ``_parent`` / ``_top`` are meaningful only
    when the document is FRAMED, and a framed document navigating its embedder
    is frame-busting the embedding host did not consent to; a NAMED frame
    addresses a browsing context by name, so a decoded tree can navigate a window
    it did not create and whose contents it cannot see.

    An unrecognised value degrades to ``None`` rather than to ``_self``: the two
    are the same navigation, and omitting says truthfully that the document
    declared nothing this renderer could honour.
    """
    if not isinstance(target, str):
        return None
    t = target.strip().lower()
    return t if t in ALLOWED_LINK_TARGETS else None


def sanitize_link_rel(rel: str | None, sanitized_target: str | None) -> list[str]:
    """The ``rel`` tokens to emit, given the declared value and the SANITISED target.

    Two rules, in order: every declared token outside the closed set is dropped;
    then ``noopener`` and ``noreferrer`` are FORCED when the target is
    ``_blank``, whether or not the document asked and whether or not it declared
    a ``rel`` at all.

    Rule 2 closes the finding. Browsers imply ``noopener`` there, which is
    exactly why the omission is dangerous rather than untidy: the behaviour is a
    user-agent DEFAULT, an explicit ``rel="opener"`` overrides it, and no
    document can know its reader's version floor. Emitting the tokens makes the
    property a fact about the document rather than about the user agent.

    The result is ORDERED — surviving declared tokens first, in declared order,
    then the forced pair if absent — so two hosts given one document emit one
    byte sequence.
    """
    declared: list[str] = []
    if isinstance(rel, str):
        for token in rel.split():
            lowered = token.lower()
            if lowered in ALLOWED_LINK_REL_TOKENS and lowered not in declared:
                declared.append(lowered)
    forced = [t for t in ("noopener", "noreferrer") if t not in declared] if sanitized_target == "_blank" else []
    return declared + forced


def sanitize_link_anchor(target: str | None, rel: str | None) -> tuple[str | None, str | None]:
    """The two anchor attributes, resolved TOGETHER.

    One call, because the ``rel`` rule DEPENDS on the sanitised target (the
    forced ``noopener noreferrer``), so a site that sanitised them independently
    would get the dependency wrong in exactly the case that matters.
    """
    safe_target = sanitize_link_target(target)
    tokens = sanitize_link_rel(rel, safe_target)
    return safe_target, (" ".join(tokens) if tokens else None)
