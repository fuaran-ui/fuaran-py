"""Decode canonical wire JSON into the structural model (WIRE_FORMAT.md §3, §6-§8).

``decode_node`` validates the node envelope, the ``kind`` discriminator, and —
for the implemented node kinds — every required field, surfacing the six
canonical :mod:`fuaran_py.result` codes with ``$``-rooted paths. Node kinds that
are recognised by the wire spec but not yet given a typed schema here are
accepted structurally (pass-through), so the codec round-trips the full corpus
while typed validation is filled in incrementally. An unrecognised kind is a
``WRONG_NODE_KIND``.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from ..model import Arr, Node, Obj, Value, from_json
from ..result import (
    EMPTY_NODE_ID,
    INVALID_JSON,
    MISSING_FIELD,
    UNKNOWN_DU_CASE,
    WRONG_NODE_KIND,
    WRONG_TYPE,
    DecodeError,
    DecodeResult,
    Err,
    Ok,
)


class _Fail(Exception):
    """Internal short-circuit carrying a :class:`DecodeError`."""

    def __init__(self, error: DecodeError) -> None:
        self.error = error


def _fail(code: str, path: str, message: str, expected: str | None = None) -> None:
    raise _Fail(DecodeError(code, path, message, expected))


# ── Primitive expectations ─────────────────────────────────────────────────


def _expect_object(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        _fail(WRONG_TYPE, path, f"expected an object at {path}")
    return value  # type: ignore[return-value]


def _expect_string(value: object, path: str) -> str:
    if not isinstance(value, str):
        _fail(WRONG_TYPE, path, f"expected a string at {path}")
    return value  # type: ignore[return-value]


def _expect_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(WRONG_TYPE, path, f"expected an integer at {path}")
    return value  # type: ignore[return-value]


def _expect_bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(WRONG_TYPE, path, f"expected a boolean at {path}")
    return value  # type: ignore[return-value]


def _expect_array(value: object, path: str) -> list:
    if not isinstance(value, list):
        _fail(WRONG_TYPE, path, f"expected an array at {path}")
    return value  # type: ignore[return-value]


def _require(obj: dict, key: str, path: str) -> object:
    if key not in obj:
        _fail(MISSING_FIELD, f"{path}.{key}", f"missing required field '{key}'")
    return obj[key]


def _dispatch(obj: dict, path: str, valid: frozenset[str], code_unknown: str = UNKNOWN_DU_CASE) -> str:
    """Read + validate a ``$type`` discriminator, returning the case name."""
    if "$type" not in obj:
        _fail(MISSING_FIELD, f"{path}.$type", "missing $type discriminator")
    tag = obj["$type"]
    if not isinstance(tag, str):
        _fail(WRONG_TYPE, f"{path}.$type", "$type must be a string")
    if tag not in valid:
        _fail(
            code_unknown,
            f"{path}.$type",
            f"unrecognised case '{tag}'",
            "one of: " + ", ".join(sorted(valid)),
        )
    return tag  # type: ignore[return-value]


def _enum(value: object, path: str, allowed: frozenset[str], name: str) -> str:
    if not isinstance(value, str):
        _fail(WRONG_TYPE, path, f"{name} must be a string")
    if value not in allowed:
        _fail(
            UNKNOWN_DU_CASE,
            path,
            f"unrecognised {name} '{value}'",
            "one of: " + ", ".join(sorted(allowed)),
        )
    return value  # type: ignore[return-value]


# ── Bare-string enum vocabularies (WIRE_FORMAT.md §3.5) ─────────────────────

TONE = frozenset({"Default", "Subdued", "Brand", "Success", "Warning", "Critical", "Info"})
WEIGHT = frozenset({"Compact", "Standard", "Spacious"})
EMPHASIS = frozenset({"Quiet", "Normal", "Loud"})
ORIENTATION = frozenset({"Vertical", "Horizontal"})
SPACER_SIZE = frozenset({"Small", "Medium", "Large"})
BADGE_VARIANT = frozenset({"Neutral", "Brand", "Success", "Warning", "Critical", "Info"})
HEADING_VARIANT = frozenset({"Standard", "Eyebrow", "Caption", "Lead"})
STYLE_ROLE = frozenset({"None", "Eyebrow", "Data", "Lede", "Caption"})
FONT_VOICE = frozenset({"Default", "Display", "Structural"})

TEXT_SOURCE_CASES = frozenset({"Literal", "Bound", "I18n"})
BINDING_CASES = frozenset({"Static", "Query", "Filter", "Selection", "State", "Computed", "I18n", "Local", "Format"})
CELL_FORMAT_CASES = frozenset({"None", "Number", "Currency", "Percent", "SignificantDigits", "Date", "Custom"})

# Every recognised node-kind discriminator (WIRE_FORMAT.md §3.2). A kind not in
# this set is WRONG_NODE_KIND; a kind in this set but absent from KIND_SCHEMAS is
# accepted structurally.
KNOWN_KINDS = frozenset(
    {
        # Layout
        "Dashboard",
        "Stack",
        "GridLayout",
        "SplitPanel",
        "Tabs",
        "Card",
        "Stepper",
        "SummaryList",
        "Disclosure",
        # Display
        "Heading",
        "Markdown",
        "Metric",
        "Badge",
        "Sparkline",
        "Spacer",
        "Callout",
        "Progress",
        "Skeleton",
        "LabelValueRow",
        "Link",
        # Input
        "Form",
        "Button",
        "FileUpload",
        "Select",
        "Filters",
        # Visualisation
        "DataGrid",
        "Chart",
        "Table",
        "Map",
        # Structural
        "Custom",
        "ErrorBoundary",
        "FragmentDecl",
        "FragmentRef",
    }
)


# ── Nested-position decoders ───────────────────────────────────────────────


def _decode_text_source(value: object, path: str) -> Value:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, TEXT_SOURCE_CASES)
    if tag == "Literal":
        text = _expect_string(_require(obj, "text", path), f"{path}.text")
        return Obj("Literal", {"text": text})
    return from_json(value)  # Bound / I18n: structural (validated discriminator)


def _decode_binding(value: object, path: str) -> Value:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, BINDING_CASES)
    if tag == "Static":
        if "value" not in obj:
            _fail(MISSING_FIELD, f"{path}.value", "Static binding missing value")
        return Obj("Static", {"value": from_json(obj["value"])})
    return from_json(value)  # other binding cases: structural (validated discriminator)


def _decode_cell_format(value: object, path: str) -> Value:
    obj = _expect_object(value, path)
    _dispatch(obj, path, CELL_FORMAT_CASES)
    return from_json(value)


def _decode_json_value(value: object, path: str) -> Value:
    return from_json(value)


def _decode_string(value: object, path: str) -> Value:
    return _expect_string(value, path)


def _decode_int(value: object, path: str) -> Value:
    return _expect_int(value, path)


def _decode_bool(value: object, path: str) -> Value:
    return _expect_bool(value, path)


def _enum_decoder(allowed: frozenset[str], name: str) -> Callable[[object, str], Value]:
    def dec(value: object, path: str) -> Value:
        return _enum(value, path, allowed, name)

    return dec


def _decode_children(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_decode_node_value(item, f"{path}.{i}") for i, item in enumerate(arr)])


# ── Per-kind field schemas: (field, required, decoder) ─────────────────────

FieldDecoder = Callable[[object, str], Value]

KIND_SCHEMAS: dict[str, list[tuple[str, bool, FieldDecoder]]] = {
    "Heading": [
        ("level", True, _decode_int),
        ("text", True, _decode_text_source),
        ("variant", True, _enum_decoder(HEADING_VARIANT, "variant")),
    ],
    "Markdown": [
        ("text", True, _decode_text_source),
    ],
    "Metric": [
        ("emphasis", True, _enum_decoder(EMPHASIS, "emphasis")),
        ("format", True, _decode_cell_format),
        ("label", True, _decode_text_source),
        ("source", True, _decode_binding),
        ("tone", True, _enum_decoder(TONE, "tone")),
        ("weight", True, _enum_decoder(WEIGHT, "weight")),
        ("icon", False, _decode_string),
        ("subtext", False, _decode_text_source),
        ("trend", False, _decode_binding),
        ("trendFormat", False, _decode_cell_format),
    ],
    "Badge": [
        ("label", True, _decode_text_source),
        ("variant", True, _enum_decoder(BADGE_VARIANT, "variant")),
    ],
    "Callout": [
        ("body", True, _decode_text_source),
        ("dismissable", True, _decode_bool),
        ("tone", True, _enum_decoder(TONE, "tone")),
        ("heading", False, _decode_text_source),
        ("icon", False, _decode_string),
    ],
    "Progress": [
        ("fraction", True, _decode_binding),
        ("indeterminate", True, _decode_bool),
        ("tone", True, _enum_decoder(TONE, "tone")),
        ("label", False, _decode_text_source),
        ("caveat", False, _decode_text_source),
    ],
    "Spacer": [
        ("size", True, _enum_decoder(SPACER_SIZE, "size")),
    ],
    "Skeleton": [
        ("rows", True, _decode_int),
    ],
    "Sparkline": [
        ("source", True, _decode_binding),
    ],
    "LabelValueRow": [
        ("emphasis", True, _decode_bool),
        ("format", True, _decode_cell_format),
        ("label", True, _decode_text_source),
        ("source", True, _decode_binding),
        ("help", False, _decode_text_source),
    ],
    "Link": [
        ("download", True, _decode_bool),
        ("href", True, _decode_binding),
        ("label", True, _decode_text_source),
        ("rel", False, _decode_string),
        ("target", False, _decode_string),
    ],
    "Dashboard": [
        ("children", True, _decode_children),
    ],
    "Stack": [
        ("children", True, _decode_children),
        ("orientation", True, _enum_decoder(ORIENTATION, "orientation")),
        ("wrap", True, _decode_bool),
    ],
    "Card": [
        ("children", True, _decode_children),
        ("heading", False, _decode_text_source),
    ],
    "Custom": [
        ("moduleId", True, _decode_string),
        ("componentId", True, _decode_string),
        ("props", False, _decode_json_value),
        ("contentHash", False, _decode_json_value),
        ("exposedNodeIds", False, _decode_json_value),
    ],
}


def _decode_kind(value: object, path: str) -> Obj:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, KNOWN_KINDS, code_unknown=WRONG_NODE_KIND)
    schema = KIND_SCHEMAS.get(tag)
    if schema is None:
        # Recognised kind without a typed schema yet — accept structurally.
        return Obj(tag, {k: from_json(v) for k, v in obj.items() if k != "$type"})

    known = {name for name, _, _ in schema}
    fields: dict[str, Value] = {}
    for name, required, dec in schema:
        if name in obj:
            fields[name] = dec(obj[name], f"{path}.{name}")
        elif required:
            _fail(MISSING_FIELD, f"{path}.{name}", f"missing required field '{name}'")
    # Preserve any extra (unknown) keys structurally so the round-trip is lossless
    # and tolerant of fields a later spec version adds (decoder tolerance, §2 rule 2).
    for key, raw in obj.items():
        if key != "$type" and key not in known:
            fields[key] = from_json(raw)
    return Obj(tag, fields)


def _decode_style(value: object, path: str) -> Obj:
    obj = _expect_object(value, path)
    fields: dict[str, Value] = {
        "emphasis": _enum(_require(obj, "emphasis", path), f"{path}.emphasis", EMPHASIS, "emphasis"),
        "tone": _enum(_require(obj, "tone", path), f"{path}.tone", TONE, "tone"),
        "weight": _enum(_require(obj, "weight", path), f"{path}.weight", WEIGHT, "weight"),
    }
    if "role" in obj:
        fields["role"] = _enum(obj["role"], f"{path}.role", STYLE_ROLE, "role")
    if "voice" in obj:
        fields["voice"] = _enum(obj["voice"], f"{path}.voice", FONT_VOICE, "voice")
    return Obj(None, fields)


def _decode_state(value: object, path: str) -> Obj:
    obj = _expect_object(value, path)
    fields: dict[str, Value] = {}
    if "onLoading" in obj:
        fields["onLoading"] = _decode_node_value(obj["onLoading"], f"{path}.onLoading")
    if "onEmpty" in obj:
        fields["onEmpty"] = _decode_node_value(obj["onEmpty"], f"{path}.onEmpty")
    if "onError" in obj:
        fields["onError"] = from_json(obj["onError"])  # closure sentinel
    return Obj(None, fields)


def _decode_node_value(value: object, path: str) -> Node:
    obj = _expect_object(value, path)

    if "id" not in obj:
        _fail(MISSING_FIELD, f"{path}.id", "missing required field 'id'")
    raw_id = obj["id"]
    if not isinstance(raw_id, str):
        _fail(WRONG_TYPE, f"{path}.id", "id must be a string")
    if raw_id == "":
        _fail(EMPTY_NODE_ID, f"{path}.id", "id must be a non-empty string")

    if "kind" not in obj:
        _fail(MISSING_FIELD, f"{path}.kind", "missing required field 'kind'")
    kind = _decode_kind(obj["kind"], f"{path}.kind")

    extras: dict[str, Value] = {}
    if "state" in obj:
        extras["state"] = _decode_state(obj["state"], f"{path}.state")
    if "style" in obj:
        extras["style"] = _decode_style(obj["style"], f"{path}.style")
    if "accessibility" in obj:
        extras["accessibility"] = from_json(obj["accessibility"])

    return Node(raw_id, kind, extras)  # type: ignore[arg-type]


def decode_node(text: str) -> DecodeResult[Node]:
    """Decode a canonical-wire ``Node`` document into a :class:`~fuaran_py.model.Node`."""
    try:
        parsed = json.loads(text)
    except ValueError:
        return Err(DecodeError(INVALID_JSON, "$", "input is not syntactically valid JSON"))
    try:
        return Ok(_decode_node_value(parsed, "$"))
    except _Fail as fail:
        return Err(fail.error)
