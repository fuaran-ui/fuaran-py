"""A minimal, injection-safe markdown → HTML renderer (stdlib only).

The F# renderer uses Markdig; the dependency-light Python baseline ships a
small subset — paragraphs, headings, bold / italic / inline-code, and links —
implemented **escape-first**: the source is HTML-escaped before any inline
markup is applied, so no author-supplied raw HTML can survive into the output
(the ``<script>`` smuggling threat in ``SANITIZATION.md`` is neutralised by
construction). Link targets are run through the URL sanitiser, and the whole
result passes through :func:`sanitize_markdown_html` as defence in depth.

This is the floor, not Markdig parity: a host wanting full CommonMark layers a
real markdown library over the decoded ``text`` itself.
"""

from __future__ import annotations

import re

from .html import escape_text
from .sanitize import sanitize_markdown_html, sanitize_url_or_blank

_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_CODE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """Apply inline markup to already-escaped text."""

    # Links first — the URL is sanitised; the visible text is already escaped.
    def link_sub(m: re.Match[str]) -> str:
        label = m.group(1)
        # The captured href fragment was HTML-escaped upstream; unescape the
        # `&amp;` so the sanitiser sees the real scheme, then re-escape as an attr.
        raw = m.group(2).replace("&amp;", "&")
        href = sanitize_url_or_blank(raw)
        return f'<a href="{escape_text(href)}">{label}</a>'

    text = _LINK.sub(link_sub, text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    text = _CODE.sub(r"<code>\1</code>", text)
    return text


def to_html(source: str) -> str:
    """Render a markdown ``source`` string to sanitised HTML."""
    if not source:
        return ""
    blocks = re.split(r"\n\s*\n", source.strip())
    rendered: list[str] = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", block)
        if heading:
            level = len(heading.group(1))
            inner = _inline(escape_text(heading.group(2).strip()))
            rendered.append(f"<h{level}>{inner}</h{level}>")
        else:
            inner = _inline(escape_text(block))
            rendered.append(f"<p>{inner}</p>")
    return sanitize_markdown_html("".join(rendered))
