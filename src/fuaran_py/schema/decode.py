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

# ── Reserved unobservable-slot sentinels (WIRE_FORMAT.md §4 / §5) ───────────
OPAQUE = "<opaque>"
"""A ``Binding.Static`` payload the encoder cannot decompose (the §5 obj-erased seam)."""


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
BADGE_VARIANT = frozenset({"Neutral", "Brand", "Success", "Warning", "Critical", "Info"})
HEADING_VARIANT = frozenset({"Standard", "Eyebrow", "Caption", "Lead"})
STYLE_ROLE = frozenset({"None", "Eyebrow", "Data", "Lede", "Caption"})
FONT_VOICE = frozenset({"Default", "Display", "Structural"})
IMAGE_VARIANT = frozenset({"Default", "Avatar", "Rounded"})
SCROLL_ORIENTATION = frozenset({"Vertical", "Horizontal", "Both"})
DATE_VARIANT = frozenset({"Date", "Time", "DateTime"})
MATH_DISPLAY = frozenset({"Inline", "Block"})
BOX_ROLE = frozenset({"Group", "Card", "Dashboard", "Separator"})  # Phase 390
BOX_LAYOUT_CASES = frozenset({"Flex", "Grid", "Auto"})  # Phase 390

TEXT_SOURCE_CASES = frozenset({"Literal", "Bound", "I18n"})
# The Compute-layer binding cases are recognised so a data-bound node's source round-trips
# byte-exactly: ``Transform`` (the dataframe-pipeline source) + the ``Data`` embedded-source
# and ``Invoke`` capability bindings. They decode *structurally* (validated discriminator, fields
# preserved) — the same pass-through every non-``Static`` binding case takes here. A fully typed
# ``Invoke`` decode (capabilityId + typed args) lands with the capability/invoke wire surface.
BINDING_CASES = frozenset(
    {
        "Static",
        "Query",
        "Filter",
        "Selection",
        "State",
        "Computed",
        "I18n",
        "Local",
        "Format",
        "Data",
        "Transform",
        "Invoke",
    }
)
CELL_FORMAT_CASES = frozenset({"None", "Number", "Currency", "Percent", "SignificantDigits", "Date", "Custom"})

# Every recognised node-kind discriminator (WIRE_FORMAT.md §3.2). A kind not in
# this set is WRONG_NODE_KIND; a kind in this set but absent from KIND_SCHEMAS is
# accepted structurally.
KNOWN_KINDS = frozenset(
    {
        # Layout
        "Box",  # Phase 390 — the unified container
        # The four retired container tags stay recognised for legacy
        # decode-upgrade (they never re-encode to their old form → Box).
        "Dashboard",
        "Stack",
        "GridLayout",
        "SplitPanel",
        "Tabs",
        "Card",
        "Stepper",
        "SummaryList",
        "Disclosure",
        "Modal",
        "ScrollArea",
        # Display
        "Heading",
        "Markdown",
        "Metric",
        "Badge",
        "Sparkline",
        "Callout",
        "Progress",
        "Skeleton",
        "LabelValueRow",
        "Link",
        "Image",
        "List",
        "Toast",
        "CodeBlock",
        "Math",
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
        "Mount",
    }
)


# ── Nested-position decoders ───────────────────────────────────────────────


def _decode_text_source(value: object, path: str) -> Value:
    # §16.1 lenient shorthand (WIRE_FORMAT §16, normative — MUST accept): a bare
    # JSON string in any TextSource position IS TextSource.Literal. It decodes to
    # exactly the value the verbose form denotes and re-encodes to the verbose
    # canonical bytes (the corpus lenient-accept family asserts this).
    if isinstance(value, str):
        return Obj("Literal", {"text": value})
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, TEXT_SOURCE_CASES)
    if tag == "Literal":
        text = _expect_string(_require(obj, "text", path), f"{path}.text")
        return Obj("Literal", {"text": text})
    if tag == "I18n":
        # I18n args are structured JVal positions (rule 12: no null) — the
        # structural pass-through goes null-strict, rejecting at the null's
        # exact path (`$.….args.<name>`), byte-behaviour otherwise unchanged.
        return _from_json_strict(value, path)
    # Bound: structural (validated discriminator). NOT null-strict — a Bound
    # binding may carry a Static whose obj-erased value is null (the deliberate
    # §5 opaque-seam exception, mirrored by every host).
    return from_json(value)


def _decode_binding(value: object, path: str) -> Value:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, BINDING_CASES)
    if tag == "Static":
        if "value" not in obj:
            _fail(MISSING_FIELD, f"{path}.value", "Static binding missing value")
        return Obj("Static", {"value": from_json(obj["value"])})
    return from_json(value)  # other binding cases: structural (validated discriminator)


# ── Typed Binding.Static positions (WIRE_FORMAT.md §"Typed Static payloads", Phase 429) ─
#
# A handful of ``Binding.Static`` positions carry a *typed* payload rather than the
# ``"<opaque>"`` obj-erased seam: a Select/Choice/Filter options list, a scalar
# string option, a string list, a Sparkline float series, a Map marker list. The
# encoder emits the typed form; the decoder mirrors it, and — crucially —
# *normalises* the two legacy inputs each such position may still carry:
#
#   * a legacy ``"value":"<opaque>"`` sentinel (the pre-429 obj-erased placeholder), and
#   * a legacy ``"value":null`` (the pre-429 ``box []`` / ``box None`` null-reference form),
#
# into the typed form the corpus now expects, so a round-trip is byte-stable AND
# value-faithful. The normalisation is per-position (the ``lenient-opaque-static-*``
# / ``lenient-null-static-*`` fixtures pin each). Positions whose payload is a
# genuinely host-typed value (Chart/DataGrid rows, Mount inputs) keep the residual
# ``"<opaque>"`` seam and use the plain ``_decode_binding`` above.


def _typed_static_binding(
    value: object,
    path: str,
    on_typed: Callable[[object, str], Value],
    on_opaque: Value,
    on_null: Value,
) -> Value:
    """Decode a ``Binding`` whose ``Static`` payload is a typed position.

    ``Static`` normalises per the three input forms (typed / ``"<opaque>"`` /
    ``null``); every other binding case passes through structurally (validated
    discriminator), exactly as :func:`_decode_binding`.
    """
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, BINDING_CASES)
    if tag == "Static":
        if "value" not in obj:
            _fail(MISSING_FIELD, f"{path}.value", "Static binding missing value")
        raw = obj["value"]
        if raw == OPAQUE:
            normalised = on_opaque
        elif raw is None:
            normalised = on_null
        else:
            normalised = on_typed(raw, f"{path}.value")
        return Obj("Static", {"value": normalised})
    return from_json(value)


def _decode_select_option(value: object, path: str) -> Value:
    """A single SelectOption record: ``{"label":<TextSource>,"value":"<str>"}``."""
    obj = _expect_object(value, path)
    label = _decode_text_source(_require(obj, "label", path), f"{path}.label")
    opt_value = _expect_string(_require(obj, "value", path), f"{path}.value")
    return Obj(None, {"label": label, "value": opt_value})


def _decode_select_option_array(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_decode_select_option(item, f"{path}[{i}]") for i, item in enumerate(arr)])


def _decode_binding_select_options(value: object, path: str) -> Value:
    # `<opaque>` → a tagged one-element placeholder; `null` → the empty typed array.
    opaque_placeholder = Arr([Obj(None, {"label": Obj("Literal", {"text": OPAQUE}), "value": OPAQUE})])
    return _typed_static_binding(value, path, _decode_select_option_array, opaque_placeholder, Arr([]))


def _decode_binding_string_opt(value: object, path: str) -> Value:
    # `<opaque>` → the scalar sentinel string; `null` → null (a genuine `None` option).
    return _typed_static_binding(value, path, lambda v, p: _expect_string(v, p), OPAQUE, None)


def _decode_string_array(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_expect_string(item, f"{path}[{i}]") for i, item in enumerate(arr)])


def _decode_binding_string_list(value: object, path: str) -> Value:
    # `<opaque>` → a one-element placeholder list; `null` → the empty typed array.
    return _typed_static_binding(value, path, _decode_string_array, Arr([OPAQUE]), Arr([]))


def _decode_float_array(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_decode_number(item, f"{path}[{i}]") for i, item in enumerate(arr)])


def _decode_binding_float_seq(value: object, path: str) -> Value:
    # Both `<opaque>` and `null` → the empty typed array (a seq has no placeholder element).
    return _typed_static_binding(value, path, _decode_float_array, Arr([]), Arr([]))


def _decode_map_marker(value: object, path: str) -> Value:
    obj = _expect_object(value, path)
    label = _decode_text_source(_require(obj, "label", path), f"{path}.label")
    lat = _decode_number(_require(obj, "latitude", path), f"{path}.latitude")
    lon = _decode_number(_require(obj, "longitude", path), f"{path}.longitude")
    return Obj(None, {"label": label, "latitude": lat, "longitude": lon})


def _decode_marker_array(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_decode_map_marker(item, f"{path}[{i}]") for i, item in enumerate(arr)])


def _decode_binding_marker_seq(value: object, path: str) -> Value:
    # Both `<opaque>` and `null` → the empty typed array.
    return _typed_static_binding(value, path, _decode_marker_array, Arr([]), Arr([]))


def _decode_cell_format(value: object, path: str) -> Value:
    obj = _expect_object(value, path)
    _dispatch(obj, path, CELL_FORMAT_CASES)
    return from_json(value)


GUEST_CHANNEL_DIRECTION = frozenset({"OutOnly", "TwoWay"})


def _decode_guest_channel(value: object, path: str) -> Value:
    """Mount's guest channel: ``direction`` is a closed DU (OutOnly | TwoWay);
    ``messageShape`` is an optional string riding on TwoWay."""
    obj = _expect_object(value, path)
    direction = _expect_string(_require(obj, "direction", path), f"{path}.direction")
    if direction not in GUEST_CHANNEL_DIRECTION:
        _fail(
            UNKNOWN_DU_CASE,
            f"{path}.direction",
            f"unknown channel direction '{direction}'",
            "OutOnly | TwoWay",
        )
    result: dict[str, Value] = {"direction": direction}
    if "messageShape" in obj:
        result["messageShape"] = _expect_string(obj["messageShape"], f"{path}.messageShape")
    return Obj(None, result)


def _decode_json_passthrough(value: object, path: str) -> Value:
    # Structural pass-through WITHOUT null-strictness — for positions that can
    # legitimately carry a §5 obj-erased opaque seam (Mount inputs embed whole
    # node trees, whose Binding.Static values may be null).
    return from_json(value)


def _from_json_strict(value: object, path: str) -> Value:
    """``from_json`` for structured JVal positions (rule 12: the wire model has
    no null). A JSON null at ANY depth rejects as ``WRONG_TYPE`` at the null's
    exact path — matching the F# reference (``jsonToJValStrict``) and the
    corpus ``reject-null-*`` fixtures. The plain ``from_json`` stays available
    for the §5 obj-erased opaque seams (``Binding.Static.value``), where a
    boxed null legitimately occurs."""
    if value is None:
        _fail(
            WRONG_TYPE,
            path,
            "null is not representable in the Fuaran wire model — omit the field instead",
            "any JSON value except null (rule 12: the wire model has no null)",
        )
    if isinstance(value, bool) or isinstance(value, (int, float, str)):
        return value
    if isinstance(value, list):
        return Arr([_from_json_strict(item, f"{path}[{i}]") for i, item in enumerate(value)])
    if isinstance(value, dict):
        tag = value.get("$type")
        if isinstance(tag, str):
            return Obj(
                tag,
                {k: _from_json_strict(v, f"{path}.{k}") for k, v in value.items() if k != "$type"},
            )
        return Obj(None, {k: _from_json_strict(v, f"{path}.{k}") for k, v in value.items()})
    raise TypeError(f"value is not a JSON-shaped object: {type(value)!r}")


def _decode_json_value(value: object, path: str) -> Value:
    # Custom props / contentHash / exposedNodeIds — structured JVal positions,
    # null-strict per rule 12.
    return _from_json_strict(value, path)


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


def _decode_text_source_array(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_decode_text_source(item, f"{path}.{i}") for i, item in enumerate(arr)])


def _decode_int_array(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_expect_int(item, f"{path}.{i}") for i, item in enumerate(arr)])


# Action cases (WIRE_FORMAT.md §3.3 / §4). Wire-survivable actions (e.g. a Modal's
# ``onDismiss``) carry a real ``$type`` — validated here, then preserved structurally
# (the same pass-through Form.onSubmit takes, which has no typed schema).
ACTION_CASES = frozenset(
    {
        "Chain",
        "Dispatch",
        "Navigate",
        "SetState",
        "Notify",
        "WriteToClipboard",
        "ReadFileBody",
        "Call",
        "AiTool",
        "CommitLocal",
        "Invoke",
    }
)


def _decode_action(value: object, path: str) -> Value:
    obj = _expect_object(value, path)
    _dispatch(obj, path, ACTION_CASES)
    # Structural (validated discriminator) but NULL-STRICT: the action payload
    # positions (SetState.value / Notify.payload / AiTool.args) are structured
    # JVal positions per rule 12, and no action case carries a §5 opaque seam —
    # so a null anywhere in an action rejects at its exact path, matching the
    # F# reference and the corpus reject-null-action-* fixtures.
    return _from_json_strict(value, path)


def _decode_number(value: object, path: str) -> Value:
    # A JSON number — an ``int`` or ``float`` (e.g. Date.step in seconds). bool is an
    # int subclass; reject it as it is never a numeric value here.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(WRONG_TYPE, path, f"expected a number at {path}")
    return value  # type: ignore[return-value]


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
    "Skeleton": [
        ("rows", True, _decode_int),
    ],
    "Sparkline": [
        # Phase 429 — `source` is a typed Static float-series position.
        ("source", True, _decode_binding_float_seq),
    ],
    # Phase 429 — Map `source` is a typed Static marker-list position. The three
    # numeric envelope fields pass through structurally like any unlisted key.
    "Map": [
        ("source", True, _decode_binding_marker_seq),
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
    "Image": [
        ("alt", True, _decode_text_source),
        ("src", True, _decode_binding),
        ("variant", True, _enum_decoder(IMAGE_VARIANT, "variant")),
    ],
    "List": [
        ("items", True, _decode_text_source_array),
        ("ordered", True, _decode_bool),
    ],
    "Toast": [
        ("dismissable", True, _decode_bool),
        ("message", True, _decode_text_source),
        ("open", True, _decode_binding),
        ("tone", True, _enum_decoder(TONE, "tone")),
    ],
    "CodeBlock": [
        ("code", True, _decode_string),
        ("copyable", True, _decode_bool),
        ("highlightLines", True, _decode_int_array),
        ("language", True, _decode_string),
        ("lineNumbers", True, _decode_bool),
    ],
    "Math": [
        ("display", True, _enum_decoder(MATH_DISPLAY, "display")),
        ("source", True, _decode_string),
    ],
    "Select": [
        ("label", True, _decode_text_source),
        # Phase 426 — the handler fields are OPTIONAL: omitted on the wire when the
        # control is declarative (AI-authored), where the renderer arms a write-back
        # default against the paired `value` slot. Present → the `"<closure>"`
        # sentinel; absent → decodes to nothing (the field simply isn't carried).
        ("onChange", False, _decode_string),
        ("onChangeMulti", False, _decode_string),
        # Phase 429 — `source`/`value`/`values` are typed Static positions: a
        # SelectOption list, a scalar string option, a string list respectively.
        ("source", True, _decode_binding_select_options),
        ("value", True, _decode_binding_string_opt),
        ("disabled", False, _decode_binding),
        ("placeholder", False, _decode_text_source),
        # Multi-select (Phase 291) — both optional; omitted on a single-select.
        ("multiple", False, _decode_bool),
        ("values", False, _decode_binding_string_list),
    ],
    "Modal": [
        ("children", True, _decode_children),
        ("dismissable", True, _decode_bool),
        # Phase 426 — `onDismiss` is OPTIONAL (omitted when declarative). Unlike the
        # closure-sentinel handlers it is a genuine wire-survivable Action, so it
        # decodes through the null-strict action decoder when present.
        ("onDismiss", False, _decode_action),
        ("open", True, _decode_binding),
        ("heading", False, _decode_text_source),
    ],
    "ScrollArea": [
        ("children", True, _decode_children),
        ("orientation", True, _enum_decoder(SCROLL_ORIENTATION, "orientation")),
        ("maxHeight", False, _decode_int),
        ("maxWidth", False, _decode_int),
    ],
    # Box (Phase 390) — decoded by a dedicated builder (`_decode_box`), not a flat
    # field schema, because it re-nests `layout` and role-validates. The four
    # retired container tags (Dashboard / Stack / GridLayout / Card) are handled
    # by `_decode_legacy_container`, which decode-upgrades each to a `Box`.
    # Button gets a (minimal) typed schema so its two contract-bearing fields
    # route through the typed decoders: `label` picks up the §16 bare-string
    # leniency, and `onClick` goes through the null-strict action decoder
    # (rule 12 — the corpus reject-null-action-* fixtures pin the paths).
    # The remaining fields (variant / icon / disabled / tooltip / …) pass
    # through structurally like any unlisted key.
    "Button": [
        ("label", True, _decode_text_source),
        ("onClick", True, _decode_action),
    ],
    "Custom": [
        ("moduleId", True, _decode_string),
        ("componentId", True, _decode_string),
        ("props", False, _decode_json_value),
        ("contentHash", False, _decode_json_value),
        ("exposedNodeIds", False, _decode_json_value),
    ],
    # Isolation/embedding boundary (WIRE_FORMAT §4o). scopeId + channel +
    # capabilities + the onBubble closure sentinel are always present on the
    # canonical wire; inputs (a FragmentArg map, additive) passes through
    # structurally WITHOUT null-strictness (it embeds whole node trees whose
    # Binding.Static values are §5 opaque seams).
    "Mount": [
        ("scopeId", True, _decode_string),
        ("channel", True, _decode_guest_channel),
        ("capabilities", True, _decode_json_value),
        ("onBubble", True, _decode_string),
        ("inputs", False, _decode_json_passthrough),
    ],
}


# ── Box (Phase 390) — the unified container + legacy decode-upgrade ─────────
#
# The wire is: {"$type":"Box","children":[…],"heading":<TextSource>?,
#   "layout":{…},"role":"Group|Card|Dashboard|Separator"}. The nested `layout`
# is `$type`-discriminated (Flex | Grid | Auto). Mirrors the F# `decodeLayoutKind`
# "Box" branch: role-validated, layout re-built, heading optional. The four
# retired container tags decode-upgrade to the equivalent Box on read (a legacy
# tag never re-encodes to its old form — it round-trips as Box).


def _decode_box_layout(value: object, path: str) -> Obj:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, BOX_LAYOUT_CASES)
    if tag == "Flex":
        fields: dict[str, Value] = {
            "direction": _enum(_require(obj, "direction", path), f"{path}.direction", ORIENTATION, "direction"),
            "wrap": _expect_bool(_require(obj, "wrap", path), f"{path}.wrap"),
        }
        if "gap" in obj:
            fields["gap"] = _expect_int(obj["gap"], f"{path}.gap")
        return Obj("Flex", fields)
    if tag == "Grid":
        gfields: dict[str, Value] = {"cols": _expect_int(_require(obj, "cols", path), f"{path}.cols")}
        if "gap" in obj:
            gfields["gap"] = _expect_int(obj["gap"], f"{path}.gap")
        if "templateColumns" in obj:
            gfields["templateColumns"] = _expect_string(obj["templateColumns"], f"{path}.templateColumns")
        return Obj("Grid", gfields)
    # Auto
    return Obj("Auto", {})


def _decode_box(obj: dict, path: str) -> Obj:
    children = _decode_children(_require(obj, "children", path), f"{path}.children")
    role = _enum(_require(obj, "role", path), f"{path}.role", BOX_ROLE, "role")
    layout = _decode_box_layout(_require(obj, "layout", path), f"{path}.layout")
    fields: dict[str, Value] = {"children": children}
    if "heading" in obj:
        fields["heading"] = _decode_text_source(obj["heading"], f"{path}.heading")
    fields["layout"] = layout
    fields["role"] = role
    return Obj("Box", fields)


def _decode_legacy_container(tag: str, obj: dict, path: str) -> Obj:
    """Decode-upgrade a retired container tag to the equivalent Box (Phase 390)."""
    children = _decode_children(_require(obj, "children", path), f"{path}.children")
    if tag == "Dashboard":
        return Obj("Box", {"children": children, "layout": Obj("Auto", {}), "role": "Dashboard"})
    if tag == "Stack":
        direction = _enum(_require(obj, "orientation", path), f"{path}.orientation", ORIENTATION, "orientation")
        wrap = _expect_bool(_require(obj, "wrap", path), f"{path}.wrap")
        layout = Obj("Flex", {"direction": direction, "wrap": wrap})
        return Obj("Box", {"children": children, "layout": layout, "role": "Group"})
    if tag == "GridLayout":
        gfields: dict[str, Value] = {"cols": _expect_int(_require(obj, "cols", path), f"{path}.cols")}
        if "templateColumns" in obj:
            gfields["templateColumns"] = _expect_string(obj["templateColumns"], f"{path}.templateColumns")
        return Obj("Box", {"children": children, "layout": Obj("Grid", gfields), "role": "Group"})
    # Card
    fields: dict[str, Value] = {"children": children}
    if "heading" in obj:
        fields["heading"] = _decode_text_source(obj["heading"], f"{path}.heading")
    fields["layout"] = Obj("Flex", {"direction": "Vertical", "wrap": False})
    fields["role"] = "Card"
    return Obj("Box", fields)


_LEGACY_CONTAINER_TAGS = frozenset({"Dashboard", "Stack", "GridLayout", "Card"})


def _decode_kind(value: object, path: str) -> Obj:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, KNOWN_KINDS, code_unknown=WRONG_NODE_KIND)
    if tag == "Box":
        return _decode_box(obj, path)
    if tag in _LEGACY_CONTAINER_TAGS:
        return _decode_legacy_container(tag, obj, path)
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
