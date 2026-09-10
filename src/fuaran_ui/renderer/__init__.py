"""Headless server-HTML renderer for the Fuaran UI wire format (Phase 239).

Render a decoded :class:`~fuaran_ui.model.Node` tree to a body-fragment HTML
string from Python, emitting the reference ``fuaran-*`` class vocabulary so the
output is visually parity-locked to the F# and TypeScript hosts. The renderer is
the no-dependency baseline that makes a Python web host render Fuaran chrome
end-to-end with no client runtime::

    from fuaran_ui import decode_node
    from fuaran_ui.renderer import render_html, reference_css_path

    decoded = decode_node(wire_json)
    if decoded.ok:
        body = render_html(decoded.value)          # body-fragment HTML
        css = reference_css_path().read_text()      # the canonical stylesheet

The host owns the document shell (``<html>`` / ``<head>`` / the ``<link>`` to
the reference CSS); this renderer emits the body fragment only.

Every ``href`` / ``src`` the render emits is checked against an **ambient
destination policy** (WIRE_FORMAT §14.1) that **defaults to deny-non-local** — a
decoded tree cannot declare its own egress, so absent a host's declaration it
gets none. A host declares a wider posture by name, as a keyword argument::

    from fuaran_ui.renderer import DENY_NON_LOCAL_EGRESS, EgressClass, HostSuffix, allow_origin

    policy = allow_origin(HostSuffix("cdn.example"), [EgressClass.MEDIA], DENY_NON_LOCAL_EGRESS)
    body = render_html(decoded.value, egress_policy=policy)

The policy vocabulary is re-exported here so declaring one needs no submodule
import; :mod:`fuaran_ui.renderer.egress` carries the full model.

**Binding resolution has an error channel, and it can reach a caller of
:func:`render_html`** (Phase 1667). A slot the document asks for that no decoded
tree can ever answer — a ``Binding.Computed``, whose whole payload is a host
closure that crosses the wire as ``"<closure>"`` — raises
:exc:`~fuaran_ui.renderer.bindings.WireSurvivabilityError` rather than resolving
to the slot's empty state, because an empty slot reads as an answer.
WIRE_FORMAT §5 states the rule; :data:`DECODED_COMPUTED_MESSAGE` is the sentence
every host carries, and it names the cases that DO cross the wire::

    from fuaran_ui.renderer import WireSurvivabilityError, render_html

    try:
        body = render_html(tree)
    except WireSurvivabilityError as e:
        # e names the remedy: Binding.Expr / Transform / State
        ...
"""

from __future__ import annotations

from pathlib import Path

from .bindings import DECODED_COMPUTED_MESSAGE, WireSurvivabilityError
from .egress import (
    DENY_NON_LOCAL_EGRESS,
    PERMISSIVE_EGRESS,
    EgressClass,
    EgressPolicy,
    ExactHost,
    HostSuffix,
    allow_origin,
)
from .render import Renderer, render_html
from .seeds import HOST_RESERVED_STATE_PREFIX, collect_state_seeds, with_state_seeds

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


# fuaran#1161 — the notebook display protocol. Imported at the module FOOT
# because `notebook` resolves the stylesheet through `reference_css` above; a
# head import would reach for it before this module has defined it.
# fuaran#1176 — the two STATIC projections of the same tree: a crawlable markdown
# document and an email-safe digest. At the module foot beside `notebook` for the
# same mechanical reason (`email` reaches this module's `render` internals), and
# grouped because they answer one question the HTML renderer never asks: what does
# a kind become when nothing can execute? `fuaran_ui.renderer.projection` carries
# the shared vocabulary; each module carries its own scope table.
from .document import (  # noqa: E402  — append-only re-export at module foot
    DEFAULT_MARKDOWN_OPTIONS,
    MarkdownOptions,
    render_markdown,
)
from .email import (  # noqa: E402  — append-only re-export at module foot
    DEFAULT_EMAIL_OPTIONS,
    EmailOptions,
    LintFinding,
    lint,
    render_email,
    render_email_document,
)
from .notebook import (  # noqa: E402  — append-only re-export at module foot
    FUARAN_UI_MIME,
    NOTEBOOK_OUTPUT_ATTR,
    UnscopableCss,
    display_html,
    mimebundle,
    scope_css,
    scoped_reference_css,
)

__all__ = [
    "DECODED_COMPUTED_MESSAGE",
    "DEFAULT_EMAIL_OPTIONS",
    "DEFAULT_MARKDOWN_OPTIONS",
    "DENY_NON_LOCAL_EGRESS",
    "FUARAN_UI_MIME",
    "HOST_RESERVED_STATE_PREFIX",
    "NOTEBOOK_OUTPUT_ATTR",
    "PERMISSIVE_EGRESS",
    "EgressClass",
    "EgressPolicy",
    "EmailOptions",
    "ExactHost",
    "HostSuffix",
    "LintFinding",
    "MarkdownOptions",
    "Renderer",
    "UnscopableCss",
    "WireSurvivabilityError",
    "allow_origin",
    "collect_state_seeds",
    "display_html",
    "lint",
    "mimebundle",
    "reference_css",
    "reference_css_path",
    "render_email",
    "render_email_document",
    "render_html",
    "render_markdown",
    "scope_css",
    "scoped_reference_css",
    "with_state_seeds",
]
