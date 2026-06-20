"""Headless server-HTML renderer for the Fuaran UI wire format (Phase 239).

Render a decoded :class:`~fuaran_py.model.Node` tree to a body-fragment HTML
string from Python, emitting the reference ``fuaran-*`` class vocabulary so the
output is visually parity-locked to the F# and TypeScript hosts. The renderer is
the no-dependency baseline that makes a Python web host render Fuaran chrome
end-to-end with no client runtime::

    from fuaran_py import decode_node
    from fuaran_py.renderer import render_html, reference_css_path

    decoded = decode_node(wire_json)
    if decoded.ok:
        body = render_html(decoded.value)          # body-fragment HTML
        css = reference_css_path().read_text()      # the canonical stylesheet

The host owns the document shell (``<html>`` / ``<head>`` / the ``<link>`` to
the reference CSS); this renderer emits the body fragment only.
"""

from __future__ import annotations

from pathlib import Path

from .render import Renderer, render_html

_REFERENCE_CSS = Path(__file__).resolve().parent / "content" / "fuaran-reference.css"


def reference_css_path() -> Path:
    """Absolute path to the byte-copied canonical reference stylesheet.

    The file is a byte-for-byte copy of the F# tier's
    ``Fuaran.UI.Renderer/content/fuaran-reference.css`` — the class vocabulary
    this renderer emits is styled by it, so output is visually consistent across
    every Fuaran host.
    """
    return _REFERENCE_CSS


def reference_css() -> str:
    """The canonical reference stylesheet as a string (UTF-8)."""
    return _REFERENCE_CSS.read_text(encoding="utf-8")


__all__ = ["render_html", "reference_css_path", "reference_css", "Renderer"]
