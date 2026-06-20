"""Render-time injection-safety floor — the Python port of ``Sanitize.fs``.

The wire decoder is best-effort: a malicious AI emission can smuggle an
``ExtraAttributes`` key, an ``onerror=`` handler inside markdown source, or a
``javascript:`` href through the decode path. This module is the last line of
defence before bytes reach a browser's HTML parser, and it mirrors the F#/TS
renderers' posture seam-for-seam so the three hosts cannot drift on safety
(see ``fuaran/SANITIZATION.md``):

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

# ── ExtraAttributes key/value sanitization ─────────────────────────────────

_CONTROL_OR_ANGLE = re.compile(r"[\x00-\x08\x0a-\x1f<>]")


def is_allowed_extra_attribute_key(key: str) -> bool:
    """The ``data-*`` / ``aria-*`` allowlist, with an explicit ``on*`` / ``style`` reject."""
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
    return trimmed.startswith("data-") or trimmed.startswith("aria-")


def is_safe_extra_attribute_value(value: str) -> bool:
    """Reject control / NUL bytes and angle brackets (``\\t`` is allowed)."""
    if value is None:
        return False
    return _CONTROL_OR_ANGLE.search(value) is None


def sanitize_extra_attributes(attrs: dict[str, str]) -> dict[str, str]:
    """Filter a candidate attribute map down to entries that pass both predicates."""
    return {k: v for k, v in attrs.items() if is_allowed_extra_attribute_key(k) and is_safe_extra_attribute_value(v)}


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


def sanitize_url(url: str) -> str | None:
    """Return the URL if its scheme is accepted, else ``None`` (default-deny)."""
    if url is None:
        return None
    trimmed = url.strip()
    if trimmed == "":
        # Empty href/src — pass through (a same-page link, documented HTML behaviour).
        return trimmed
    scheme = _extract_scheme(trimmed)
    if scheme is None:
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
_DANGEROUS_PROTOCOL = re.compile(r"(?i)(javascript|vbscript):")


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
            i = result.lower().find(open_tag)
            if i < 0:
                break
            j = result.lower().find(close_tag, i)
            if j >= 0:
                result = result[:i] + result[j + len(close_tag) :]
            else:
                end = result.find(">", i)
                result = result[:i] + (result[end + 1 :] if end >= 0 else "")
    result = _EVENT_HANDLER.sub("", result)
    result = _DANGEROUS_PROTOCOL.sub("about:blank", result)
    return result
