"""The emittable-surface catalog + provider-agnostic tool schemas.

So a Python agent can **discover what it may emit** (the recognised ``NodeKind`` /
``Binding`` / ``Action`` / ``TextSource`` cases and the bounded value-space
projections) and **validate its intent** against the typed surface, instead of
blind-emitting JSON. The catalog is derived from the codec's own recognised-case
sets + the bounded ``Literal`` enums, so it can never drift from what the decoder
accepts.

The tool schemas are **provider-agnostic** function/tool definitions (``name`` +
``description`` + JSON-Schema ``parameters``) an agent registers with any provider.
The primary constrained-emission tool takes the caller-supplied canonical wire
schema (``schema.json``) as its input schema, so the package stays corpus-free and
standard-library-only at import time.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass
from typing import Any

from ..result import Err
from ..schema import types as t
from ..schema.decode import ACTION_CASES, BINDING_CASES, KNOWN_KINDS, TEXT_SOURCE_CASES, decode_node

# The bounded value-space projections — each bare-string ``Literal`` enum an agent
# must pick within (WIRE_FORMAT §3.5). Derived from the typed surface via
# ``typing.get_args`` so the projection cannot drift from the codec.
_VALUE_SPACE_ENUMS: dict[str, Any] = {
    "Tone": t.Tone,
    "Weight": t.Weight,
    "Emphasis": t.Emphasis,
    "Orientation": t.Orientation,
    "BadgeVariant": t.BadgeVariant,
    "HeadingVariant": t.HeadingVariant,
    "ButtonVariant": t.ButtonVariant,
    "ChartKind": t.ChartKind,
    "StyleRole": t.StyleRole,
    "FontVoice": t.FontVoice,
    "LiveRegion": t.LiveRegion,
    "ImageVariant": t.ImageVariant,
    "ScrollOrientation": t.ScrollOrientation,
    "DateVariant": t.DateVariant,
    "MathDisplay": t.MathDisplay,
    "BoxRole": t.BoxRole,
    "FileReadEncoding": t.FileReadEncoding,
}


def emittable_kinds() -> list[str]:
    """The recognised ``NodeKind`` discriminators an agent may emit."""
    return sorted(KNOWN_KINDS)


def binding_cases() -> list[str]:
    """The recognised ``Binding`` value-source discriminators."""
    return sorted(BINDING_CASES)


def action_cases() -> list[str]:
    """The recognised ``Action`` effect discriminators."""
    return sorted(ACTION_CASES)


def text_source_cases() -> list[str]:
    """The recognised ``TextSource`` discriminators."""
    return sorted(TEXT_SOURCE_CASES)


def value_space() -> dict[str, list[str]]:
    """Each bounded ``Literal`` enum → its allowed values (the within-bounds
    choices an agent picks from), derived from the typed surface."""
    return {name: list(typing.get_args(alias)) for name, alias in _VALUE_SPACE_ENUMS.items()}


def describe_surface() -> dict[str, Any]:
    """A machine-readable snapshot of the whole emittable surface — the payload the
    ``list_surface`` tool returns to an agent."""
    return {
        "kinds": emittable_kinds(),
        "bindings": binding_cases(),
        "actions": action_cases(),
        "textSources": text_source_cases(),
        "valueSpace": value_space(),
    }


# ── Validation of an agent's emission against the typed surface ───────────────


@dataclass(frozen=True)
class ValidationResult:
    """The verdict for a candidate wire tree: ``ok`` plus the canonical decode
    error (code / path / message) when it is rejected."""

    ok: bool
    code: str | None = None
    path: str | None = None
    message: str | None = None


def validate_emission(wire_json: str) -> ValidationResult:
    """Validate an agent's candidate ``Node`` wire JSON against the typed surface via
    the Phase 234 codec — the canonical accept/reject decision + error location, so
    the agent repairs intent instead of shipping malformed JSON downstream."""
    result = decode_node(wire_json)
    if isinstance(result, Err):
        e = result.error
        return ValidationResult(ok=False, code=e.code, path=e.path, message=e.message)
    return ValidationResult(ok=True)


# ── Provider-agnostic tool/function-call schemas ─────────────────────────────


def emit_tool_schema(wire_schema: dict[str, Any]) -> dict[str, Any]:
    """The constrained-emission tool: emit a canonical Fuaran wire tree. Takes the
    caller-supplied canonical wire schema (``wire-format-fixtures/schema.json``) as
    its ``parameters`` so a provider constrains generation to schema-valid wire.
    Passed in (not bundled) so this package stays corpus-free + stdlib-only."""
    return {
        "name": "fuaran_emit_tree",
        "description": (
            "Emit a Fuaran UI as a canonical wire-format JSON tree (a Node). The tree "
            "renders on any conformant host. Emit only recognised kinds/bindings/actions "
            "and pick bounded enum values within their value-space (call fuaran_list_surface)."
        ),
        "parameters": wire_schema,
    }


def tool_schemas() -> list[dict[str, Any]]:
    """The provider-agnostic discovery/validation tool definitions an agent registers.
    (The constrained-emission tool is :func:`emit_tool_schema` — it needs the wire
    schema passed in.) Each is a neutral ``{name, description, parameters}`` object
    usable with any provider's function-calling API."""
    return [
        {
            "name": "fuaran_list_surface",
            "description": (
                "List the emittable Fuaran surface: recognised NodeKind / Binding / Action / "
                "TextSource discriminators and the bounded value-space (enum -> allowed values). "
                "Call before emitting to stay within the typed surface."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "fuaran_validate_tree",
            "description": (
                "Validate a candidate Fuaran wire tree against the typed surface. Returns ok, or "
                "the canonical decode error code + path so the emission can be repaired."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tree": {
                        "type": "string",
                        "description": "The candidate Node wire JSON to validate.",
                    }
                },
                "required": ["tree"],
                "additionalProperties": False,
            },
        },
        {
            "name": "fuaran_inspect_tree",
            "description": (
                "Introspect an emitted Fuaran wire tree: report each node's id, kind, bound "
                "binding slots (with their $state/$queries/... expression), and structural children."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tree": {
                        "type": "string",
                        "description": "The Node wire JSON to introspect.",
                    }
                },
                "required": ["tree"],
                "additionalProperties": False,
            },
        },
    ]
