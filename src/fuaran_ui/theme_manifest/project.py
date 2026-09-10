"""Token-surface projectors — Python port of ``Fuaran.UI.ThemeManifest.Project``.

Lower the adoption floor: project an app's existing token surface into a baseline
:class:`ThemeManifest` (tokens + inferable role bindings) the operator then
enriches with invariants. Three source formats + a merge:

- :func:`project_from_fuaran_tone_vars` — the renderer's
  ``--fuaran-tone-{tone}-{slot}`` contract (role inference is direct).
- :func:`project_from_css_custom_properties` — a generic ``:root`` (+ dark) set;
  roles left unbound; light/dark preserved (``@dark`` suffix).
- :func:`project_from_dtcg` — a DTCG / tokens.json file (values lossless).
- :func:`merge` — combine base + override with last-write-wins precedence.
"""

from __future__ import annotations

import re

from .decode import decode
from .manifest import (
    ANONYMOUS_META,
    EMPTY_MANIFEST,
    ManifestToken,
    RoleBinding,
    ThemeManifest,
    ToneRole,
    tone_of_string,
)

_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


class CssBlock:
    """One selector block — its selector text + the ``--name → value`` declarations."""

    def __init__(self, selector: str, declarations: list[tuple[str, str]]) -> None:
        self.selector = selector
        self.declarations = declarations


def scan_css_blocks(css: str) -> list[CssBlock]:
    """Scan flat ``selector { … }`` blocks, keeping only custom-property (``--``) declarations."""
    cleaned = _COMMENT.sub("", css)
    blocks: list[CssBlock] = []
    for chunk in cleaned.split("}"):
        brace = chunk.find("{")
        if brace < 0:
            continue
        selector = chunk[:brace].strip()
        body = chunk[brace + 1 :]
        decls: list[tuple[str, str]] = []
        for decl in body.split(";"):
            colon = decl.find(":")
            if colon < 0:
                continue
            name = decl[:colon].strip()
            value = decl[colon + 1 :].strip()
            if name.startswith("--"):
                decls.append((name, value))
        if decls:
            blocks.append(CssBlock(selector, decls))
    return blocks


def _infer_type(value: str) -> str:
    v = value.strip().lower()
    if v.startswith(("#", "rgb", "hsl", "oklch", "oklab", "color(")):
        return "color"
    if v.endswith(("px", "rem", "em", "%")):
        return "dimension"
    if v.endswith(("ms", "s")):
        return "duration"
    return ""


def _token(name: str, value: str) -> ManifestToken:
    return ManifestToken(name=name, type=_infer_type(value), value=value)


def _dedupe_tokens(tokens: list[ManifestToken]) -> list[ManifestToken]:
    by_name: dict[str, ManifestToken] = {}
    for t in tokens:
        by_name[t.name] = t  # last-write-wins
    return list(by_name.values())


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:].lower() if s else s


def project_from_fuaran_tone_vars(css: str) -> ThemeManifest:
    """Project a ``--fuaran-tone-{tone}-{slot}`` set into tokens + Tone role bindings.

    Token names are ``tone.{tone}.{slot}``; each tone whose ``bg`` slot is present
    gets a tone role binding to that token. Role inference is direct.
    """
    decls = [d for b in scan_css_blocks(css) for d in b.declarations]
    raw_tokens: list[ManifestToken] = []
    for name, value in decls:
        stripped = name.lstrip("-")
        if not stripped.startswith("fuaran-tone-"):
            continue
        rest = stripped[len("fuaran-tone-") :]
        parts = rest.split("-")
        if len(parts) != 2:
            continue
        tone, slot = parts
        if tone_of_string(_cap(tone)) is None:
            continue
        raw_tokens.append(_token(f"tone.{tone.lower()}.{slot.lower()}", value))
    tokens = _dedupe_tokens(raw_tokens)
    roles: list[RoleBinding] = []
    for t in tokens:
        name_parts = t.name.split(".")
        if len(name_parts) == 3 and name_parts[0] == "tone" and name_parts[2] == "bg":
            bound_tone = tone_of_string(_cap(name_parts[1]))
            if bound_tone is not None:
                roles.append(RoleBinding(role=ToneRole(bound_tone), token_name=t.name))
    return ThemeManifest(tokens=tokens, roles=roles)


def _is_dark_selector(selector: str) -> bool:
    s = selector.lower()
    return "data-theme=dark" in s or 'data-theme="dark"' in s or ".dark" in s


def project_from_css_custom_properties(css: str) -> ThemeManifest:
    """Project a generic ``:root`` block (+ optional dark block) into tokens; roles unbound."""
    blocks = scan_css_blocks(css)
    light = [
        _token(name.lstrip("-"), value)
        for b in blocks
        if not _is_dark_selector(b.selector)
        for name, value in b.declarations
    ]
    dark = [
        _token(name.lstrip("-") + "@dark", value)
        for b in blocks
        if _is_dark_selector(b.selector)
        for name, value in b.declarations
    ]
    return ThemeManifest(tokens=_dedupe_tokens(light + dark))


def project_from_dtcg(json_str: str) -> ThemeManifest:
    """Project a DTCG / tokens.json file into a manifest (values lossless; roles unmined)."""
    return decode(json_str)


def merge(base: ThemeManifest, over: ThemeManifest) -> ThemeManifest:
    """Merge ``over`` onto ``base`` with last-write-wins precedence (the CSS cascade)."""
    over_token_names = {t.name for t in over.tokens}
    tokens = [t for t in base.tokens if t.name not in over_token_names] + list(over.tokens)

    over_roles = {r.role for r in over.roles}
    roles = [r for r in base.roles if r.role not in over_roles] + list(over.roles)

    invariants = list(dict.fromkeys(list(over.invariants) + list(base.invariants)))

    meta = over.meta if over.meta != ANONYMOUS_META else base.meta
    return ThemeManifest(meta=meta, tokens=tokens, roles=roles, invariants=invariants)


# Re-exported for callers that want the empty baseline.
__all__ = [
    "CssBlock",
    "scan_css_blocks",
    "project_from_fuaran_tone_vars",
    "project_from_css_custom_properties",
    "project_from_dtcg",
    "merge",
    "EMPTY_MANIFEST",
]
