"""A tiny string-HTML builder + the escaping floor.

The renderer emits HTML *strings* — no DOM, no template engine, stdlib only.
This module is the single seam where a Python string becomes HTML, so the
escaping floor lives here (mirroring the React-escaping floor the F#/TS
renderers lean on; see ``fuaran/SANITIZATION.md`` "React's escaping floor").

* :func:`escape_text` — text content (``&`` ``<`` ``>``), the ``prop.text``
  analogue.
* :func:`escape_attr` — attribute values (adds ``"`` ``'``), the typed-attribute
  encoder analogue.
* :func:`element` / :func:`void_element` — compose a tag from an *ordered* list
  of ``(name, value)`` attribute pairs and pre-rendered inner HTML. Attribute
  order is insertion order so output is deterministic and diffable.

Attribute *values* are escaped here; attribute *keys* the caller passes are
assumed renderer-controlled (the class vocabulary, ``data-*`` markers, ARIA
keys) — untrusted keys never reach this module (the ``ExtraAttributes`` /
URL seams are filtered upstream in :mod:`fuaran_py.renderer.sanitize`).
"""

from __future__ import annotations

from collections.abc import Iterable

# HTML void elements — no closing tag, no children (WHATWG §12.1.2).
VOID_ELEMENTS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)


def escape_text(value: str) -> str:
    """Escape a string for HTML *text* content (the ``prop.text`` floor)."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(value: str) -> str:
    """Escape a string for an HTML double-quoted *attribute* value."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


Attrs = Iterable[tuple[str, str]]


def _attr_str(attrs: Attrs) -> str:
    return "".join(f' {name}="{escape_attr(value)}"' for name, value in attrs)


def element(tag: str, attrs: Attrs = (), inner: str = "") -> str:
    """Render a non-void element with pre-escaped/pre-rendered ``inner`` HTML."""
    return f"<{tag}{_attr_str(attrs)}>{inner}</{tag}>"


def void_element(tag: str, attrs: Attrs = ()) -> str:
    """Render a void element (``input`` / ``img`` / …) — self-closing, no children."""
    return f"<{tag}{_attr_str(attrs)} />"


def text_element(tag: str, attrs: Attrs, text: str) -> str:
    """Render an element whose only child is escaped text content."""
    return element(tag, attrs, escape_text(text))
