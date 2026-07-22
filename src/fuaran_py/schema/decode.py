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
from typing import cast

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


def _unwrap_static_envelope(value: object) -> object:
    """Lenient AI-ingest (WIRE_FORMAT §3.6, generalised): a ``Static`` envelope
    wrapped around a PLAIN scalar unwraps before the scalar readers — the
    inverse of the bare-scalar-in-Binding-slot confusion, applied at every
    plain-scalar position in one place (mirrors the F# ``unwrapStaticEnvelope``).
    Objects that are not a well-formed Static envelope pass through untouched
    and fail with the normal error."""
    if isinstance(value, dict) and value.get("$type") == "Static" and "value" in value:
        return value["value"]
    return value


def _expect_object(value: object, path: str) -> dict:
    if not isinstance(value, dict):
        _fail(WRONG_TYPE, path, f"expected an object at {path}")
    return value  # type: ignore[return-value]


def _expect_string(value: object, path: str) -> str:
    value = _unwrap_static_envelope(value)
    if not isinstance(value, str):
        _fail(WRONG_TYPE, path, f"expected a string at {path}")
    return value  # type: ignore[return-value]


def _expect_int(value: object, path: str) -> int:
    value = _unwrap_static_envelope(value)
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(WRONG_TYPE, path, f"expected an integer at {path}")
    return value  # type: ignore[return-value]


def _expect_bool(value: object, path: str) -> bool:
    value = _unwrap_static_envelope(value)
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


def _enum_aliased(value: object, path: str, allowed: frozenset[str], aliases: dict[str, str], name: str) -> str:
    """Decode a bare-string enum, accepting the WIRE_FORMAT §3.6 lenient-ingest aliases.

    Decode-only: the canonical DU-case names always win (they are in ``allowed``, so
    the alias table is only consulted for a non-canonical input); the encoder never
    emits an alias, and a re-encode normalises to the canonical case name. An input
    that is neither canonical nor a curated alias still fails ``UNKNOWN_DU_CASE``.
    """
    if not isinstance(value, str):
        _fail(WRONG_TYPE, path, f"{name} must be a string")
    if value in allowed:
        return value  # type: ignore[return-value]
    if value in aliases:
        return aliases[value]
    _fail(
        UNKNOWN_DU_CASE,
        path,
        f"unrecognised {name} '{value}'",
        "one of: " + ", ".join(sorted(allowed)),
    )
    return value  # type: ignore[return-value]  # unreachable — _fail raises


# ── Bare-string enum vocabularies (WIRE_FORMAT.md §3.5) ─────────────────────

TONE = frozenset({"Default", "Subdued", "Brand", "Success", "Warning", "Critical", "Info"})
WEIGHT = frozenset({"Compact", "Standard", "Spacious"})
EMPHASIS = frozenset({"Quiet", "Normal", "Loud"})
TEXT_ANCHOR = frozenset({"Start", "Middle", "End"})
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
BUTTON_VARIANT = frozenset({"Primary", "Secondary", "Tertiary", "Destructive"})

# ── Lenient-ingest enum aliases (WIRE_FORMAT.md §3.6, decode-only) ──────────
# The encoder never emits an alias; a re-encode normalises to the canonical DU
# case name. Canonical values always win (they are in the enum's `allowed` set,
# so the alias table is only consulted for a non-canonical input). `StyleWeight`
# is deliberately NOT aliased — `Bold`/`Heavy` is font-weight intent, but the
# language's `weight` means density (Compact|Standard|Spacious).
TONE_ALIASES = {"Positive": "Success", "Danger": "Critical", "Negative": "Critical", "Neutral": "Default"}
EMPHASIS_ALIASES = {"Strong": "Loud", "Bold": "Loud", "Subtle": "Quiet", "Muted": "Quiet"}
HEADING_VARIANT_ALIASES = {"Default": "Standard"}
BADGE_VARIANT_ALIASES = {"Default": "Neutral", "Danger": "Critical"}
BUTTON_VARIANT_ALIASES = {"Danger": "Destructive"}
ORIENTATION_ALIASES = {"Row": "Horizontal", "row": "Horizontal", "Column": "Vertical", "column": "Vertical"}

# 0.2.0 cross-vocabulary coercion (2026-07-19 sweep, both directions): the
# `emphasis` name collides across two vocabularies — the style ENUM
# (Quiet|Normal|Loud) on SemanticStyle/Metric and the behavioural BOOL on
# Fact/LabelValueRow. A bool in the enum slot projects one-to-one
# (true ⇒ Loud, false ⇒ Normal); the enum (and its §3.6 aliases) in the bool
# slot projects Loud/Strong/Bold ⇒ true, Normal/Quiet/Subtle/Muted ⇒ false.
_EMPHASIS_TRUE = frozenset({"Loud", "Strong", "Bold"})
_EMPHASIS_FALSE = frozenset({"Normal", "Quiet", "Subtle", "Muted"})

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
        "Fact",
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
        "Drawing",
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
        "Switch",
        "FragmentDecl",
        "FragmentRef",
        "Mount",
    }
)


# ── Nested-position decoders ───────────────────────────────────────────────


def _decode_text_source(value: object, path: str) -> Value:
    # 0.2.0 — the bare JSON string IS the canonical `TextSource.Literal` form;
    # the `{"$type":"Literal","text":…}` envelope stays decode-accepted (§16)
    # and normalises down to the bare string on re-encode.
    if isinstance(value, str):
        return value
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, TEXT_SOURCE_CASES)
    if tag == "Literal":
        return _expect_string(_require(obj, "text", path), f"{path}.text")
    if tag == "I18n":
        # I18n args are structured JVal positions (rule 12: no null) — the
        # structural pass-through goes null-strict, rejecting at the null's
        # exact path (`$.….args.<name>`), byte-behaviour otherwise unchanged.
        return _from_json_strict(value, path)
    # Bound — decode the wrapped binding so it picks up the same normalisation
    # (accessor sentinels dropped, aliases folded) as any bare-Binding slot.
    # NOT null-strict — a Bound binding may carry a Static whose obj-erased
    # value is null (the deliberate §5 opaque-seam exception).
    binding = _decode_binding(_require(obj, "binding", path), f"{path}.binding")
    return Obj("Bound", {"binding": binding})


def _decode_binding(value: object, path: str) -> Value:
    # §3.6 lenient shape coercion: a bare JSON array or scalar where a Binding
    # is expected is `Static` with that value (every Binding case is a
    # `$type`-discriminated object, so an array/scalar can only mean Static).
    if isinstance(value, list) or isinstance(value, (str, int, float, bool)):
        return Obj("Static", {"value": from_json(value)})
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, BINDING_CASES | {"Bound"})
    if tag == "Static":
        if "value" not in obj:
            _fail(MISSING_FIELD, f"{path}.value", "Static binding missing value")
        return Obj("Static", {"value": from_json(obj["value"])})
    if tag == "Bound":
        # Phase 633 — the `TextSource.Bound` wrapper convention transferred to a
        # bare-Binding slot unwraps one-to-one: decode the inner binding in place.
        return _decode_binding(_require(obj, "binding", path), f"{path}.binding")
    return _normalise_binding_obj(obj, path)


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
    ``null``); a bare array/scalar coerces to ``Static`` (§3.6) and a ``Bound``
    wrapper unwraps (Phase 633); every other binding case passes through
    structurally (validated discriminator), exactly as :func:`_decode_binding`.
    """
    if isinstance(value, list) or isinstance(value, (str, int, float, bool)):
        return Obj("Static", {"value": on_typed(value, path)})
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, BINDING_CASES | {"Bound"})
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
    if tag == "Bound":
        return _typed_static_binding(_require(obj, "binding", path), f"{path}.binding", on_typed, on_opaque, on_null)
    return _normalise_binding_obj(obj, path)


def _decode_select_option(value: object, path: str) -> Value:
    """A single SelectOption record: ``{"label":<TextSource>,"value":"<str>"}``.

    §3.6 lenient shape coercion: a bare JSON string ``"A"`` is the HTML
    ``<select>`` prior and coerces to ``{"label":"A","value":"A"}``."""
    if isinstance(value, str):
        return Obj(None, {"label": value, "value": value})
    obj = _expect_object(value, path)
    label = _decode_text_source(_require(obj, "label", path), f"{path}.label")
    opt_value = _expect_string(_require(obj, "value", path), f"{path}.value")
    return Obj(None, {"label": label, "value": opt_value})


def _decode_select_option_array(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_decode_select_option(item, f"{path}[{i}]") for i, item in enumerate(arr)])


def _decode_binding_select_options(value: object, path: str) -> Value:
    # `<opaque>` → a tagged one-element placeholder; `null` → the empty typed array.
    opaque_placeholder = Arr([Obj(None, {"label": OPAQUE, "value": OPAQUE})])
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


def _enum_aliased_decoder(
    allowed: frozenset[str], aliases: dict[str, str], name: str
) -> Callable[[object, str], Value]:
    """A required bare-enum decoder that also accepts the §3.6 lenient-ingest aliases."""

    def dec(value: object, path: str) -> Value:
        return _enum_aliased(value, path, allowed, aliases, name)

    return dec


# ── Phase 460 omit-when-default (WIRE_FORMAT.md §3.6, decode-only) ──────────
# A field whose absence restores an identity default. On the generic structural
# model the encoder re-emits exactly the fields present, so byte-minimal canonical
# output is achieved by DROPPING the field when it is absent OR carries the
# identity default (a present explicit-default value still decodes — read-compat).
# The `_DROP` sentinel tells `_decode_kind` to omit the field from the model.
_DROP = object()


def _omit_default_enum(
    allowed: frozenset[str], aliases: dict[str, str], default: str, name: str
) -> Callable[[object, str], object]:
    def dec(value: object, path: str) -> object:
        v = _enum_aliased(value, path, allowed, aliases, name)
        return _DROP if v == default else v

    return dec


def _omit_default_format(value: object, path: str) -> object:
    v = _decode_cell_format(value, path)
    return _DROP if isinstance(v, Obj) and v.tag == "None" else v


def _omit_default_bool(default: bool) -> Callable[[object, str], object]:
    """0.2.0 behavioural omit-when-default: the flag is omitted at its default
    on BOTH boundaries (`Toast.dismissable` is the one omit-when-TRUE)."""

    def dec(value: object, path: str) -> object:
        b = _expect_bool(value, path)
        return _DROP if b == default else b

    return dec


def _decode_emphasis_enum(value: object, path: str) -> str:
    """The `Emphasis` style ENUM, with the §3.6 aliases and the cross-vocabulary
    bool projection (true ⇒ Loud, false ⇒ Normal)."""
    value = _unwrap_static_envelope(value)
    if isinstance(value, bool):
        return "Loud" if value else "Normal"
    return _enum_aliased(value, path, EMPHASIS, EMPHASIS_ALIASES, "emphasis")


def _omit_default_emphasis_enum(value: object, path: str) -> object:
    v = _decode_emphasis_enum(value, path)
    return _DROP if v == "Normal" else v


def _decode_emphasis_flag(value: object, path: str) -> object:
    """The behavioural `emphasis` BOOL (Fact / LabelValueRow) — the other half
    of the same-name collision with the `Emphasis` style enum: booleans pass
    through; the enum AND its aliases project one-to-one; any other string is
    the didactic reject naming both vocabularies. 0.2.2 — omitted-when-false."""
    value = _unwrap_static_envelope(value)
    if isinstance(value, str):
        if value in _EMPHASIS_TRUE:
            b = True
        elif value in _EMPHASIS_FALSE:
            b = False
        else:
            _fail(
                WRONG_TYPE,
                path,
                f"expected JSON boolean, got '{value}' — this `emphasis` is a BOOL (is this an "
                "emphasised row/fact?); the Emphasis style enum (Quiet|Normal|Loud) lives on "
                "style/Metric.emphasis. Write true or false",
                "JSON boolean",
            )
            raise AssertionError("unreachable")
    else:
        b = _expect_bool(value, path)
    return _DROP if not b else True


def _omit_default_width(value: object, path: str) -> object:
    # ColumnWidth is a closed `$type` DU; `Auto` is the identity. Non-Auto widths
    # pass through structurally (validated discriminator).
    obj = _expect_object(value, path)
    if "$type" not in obj:
        _fail(MISSING_FIELD, f"{path}.$type", "missing $type discriminator")
    if obj.get("$type") == "Auto":
        return _DROP
    return from_json(value)


def _alias_get(obj: dict, canonical: str, aliases: tuple[str, ...]) -> tuple[object, bool]:
    """Field-name aliasing (WIRE_FORMAT §3.6, decode-only): the canonical name wins
    when both are present; otherwise the first present alias supplies the value."""
    if canonical in obj:
        return obj[canonical], True
    for a in aliases:
        if a in obj:
            return obj[a], True
    return None, False


def _normalise_binding_obj(obj: dict, path: str) -> Value:
    """Normalise a non-``Static`` binding case to its 0.2.0 canonical shape.

    Query: ``dependsOn`` ← ``deps`` / ``dependencies`` (§3.6), omitted when
    empty; the retired ``accessor`` sentinel is dropped (0.2.0). Selection:
    ``accessor`` dropped; ``defaultValue`` (0.2.9) + ``field`` (Phase 632)
    preserved. State: ``defaultValue`` ← ``initialValue`` / ``default``.
    Transform: ``params`` map form coerces to the canonical ``[{from,name}]``
    array (name-keyed set, §3.6), ``value`` aliases ``from`` at the element,
    and the embedded source + pipeline normalise through the columnar codec.
    Everything else passes through structurally (validated discriminator)."""
    tag = obj.get("$type")
    if not isinstance(tag, str):
        return from_json(obj)
    if tag == "Query":
        name = _expect_string(_require(obj, "name", path), f"{path}.name")
        fields: dict[str, Value] = {}
        depends_raw, depends_present = _alias_get(obj, "dependsOn", ("deps", "dependencies"))
        if depends_present:
            arr = _expect_array(depends_raw, f"{path}.dependsOn")
            if arr:
                fields["dependsOn"] = Arr([_expect_string(d, f"{path}.dependsOn[{i}]") for i, d in enumerate(arr)])
        fields["name"] = name
        return Obj("Query", fields)
    if tag == "Selection":
        node_id = _expect_string(_require(obj, "nodeId", path), f"{path}.nodeId")
        fields = {}
        if "defaultValue" in obj:
            fields["defaultValue"] = from_json(obj["defaultValue"])
        if "field" in obj:
            fields["field"] = _expect_string(obj["field"], f"{path}.field")
        fields["nodeId"] = node_id
        return Obj("Selection", fields)
    if tag == "State":
        key = _expect_string(_require(obj, "key", path), f"{path}.key")
        fields = {}
        default_raw, default_present = _alias_get(obj, "defaultValue", ("initialValue", "default"))
        if default_present:
            fields["defaultValue"] = from_json(default_raw)
        fields["key"] = key
        return Obj("State", fields)
    if tag == "Transform":
        return _decode_transform_binding(obj, path)
    return from_json(obj)


def _decode_transform_binding(obj: dict, path: str) -> Value:
    """The `Binding.Transform` case (Phase 282/424): `source` + `pipeline`
    normalise through the columnar codec (`fuaran_py.dataframe`), which owns the
    lenient columnar/expression ingest; `params` carries the §3.6 map coercion +
    the `value` ← `from` element alias."""
    source_raw = _require(obj, "source", path)
    pipeline_raw = _require(obj, "pipeline", path)
    source, pipeline = _normalise_transform_payload(source_raw, pipeline_raw, path)
    fields: dict[str, Value] = {}
    if "params" in obj:
        raw_params = obj["params"]
        entries: list[Value] = []
        if isinstance(raw_params, dict):
            # Map form — a name-keyed set, coerced to the canonical array in
            # key order (deterministic: F# Map.toList is key-sorted).
            for name in sorted(raw_params):
                binding = _decode_binding(raw_params[name], f"{path}.params.{name}.from")
                entries.append(Obj(None, {"from": binding, "name": name}))
        else:
            arr = _expect_array(raw_params, f"{path}.params")
            for i, el in enumerate(arr):
                el_obj = _expect_object(el, f"{path}.params[{i}]")
                name = _expect_string(_require(el_obj, "name", f"{path}.params[{i}]"), f"{path}.params[{i}].name")
                from_raw, from_present = _alias_get(el_obj, "from", ("value",))
                if not from_present:
                    _fail(MISSING_FIELD, f"{path}.params[{i}].from", "missing required field 'from'")
                binding = _decode_binding(from_raw, f"{path}.params.{name}.from")
                entries.append(Obj(None, {"from": binding, "name": name}))
        if entries:
            fields["params"] = Arr(entries)
    fields["pipeline"] = pipeline
    fields["source"] = source
    return Obj("Transform", fields)


def _normalise_transform_payload(source_raw: object, pipeline_raw: object, path: str) -> tuple[Value, Value]:
    """Round the Transform `source` + `pipeline` sub-trees through the typed
    columnar codec so lenient columnar/expression input re-encodes canonical.
    The codec owns the lenient ingest (schemaless inference, bare-array
    columns, flat predicate spellings, step aliases, …)."""
    from ..dataframe.codec import (
        decode_pipeline_json,
        decode_source_json,
        encode_source_value,
        encode_transform_value,
    )

    src_result = decode_source_json(source_raw)
    if not src_result.ok:
        _fail(WRONG_TYPE, f"{path}.source", f"{src_result.error.code}: {src_result.error.detail}")
        raise AssertionError("unreachable")
    pipe_result = decode_pipeline_json(pipeline_raw)
    if not pipe_result.ok:
        _fail(WRONG_TYPE, f"{path}.pipeline", f"{pipe_result.error.code}: {pipe_result.error.detail}")
        raise AssertionError("unreachable")
    source = encode_source_value(src_result.value)
    pipeline = Arr([encode_transform_value(t) for t in pipe_result.value])
    return source, pipeline


def _decode_children(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_decode_node_value(item, f"{path}.{i}") for i, item in enumerate(arr)])


def _decode_switch_case(value: object, path: str) -> Value:
    # One Switch case (Phase 392): ``{"child":<Node>,"match":<string>}``.
    obj = _expect_object(value, path)
    child = _decode_node_value(_require(obj, "child", path), f"{path}.child")
    match = _expect_string(_require(obj, "match", path), f"{path}.match")
    return Obj(None, {"child": child, "match": match})


def _decode_switch_cases(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_decode_switch_case(item, f"{path}[{i}]") for i, item in enumerate(arr)])


def _decode_single_node(value: object, path: str) -> Value:
    # Deferred wrapper so KIND_SCHEMAS (built before `_decode_node_value` is
    # defined) can decode a single-Node field; the call resolves at decode time.
    return _decode_node_value(value, path)


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
    tag = _dispatch(obj, path, ACTION_CASES)
    # Field-name aliases (WIRE_FORMAT §3.6, decode-only): Call.endpoint ← url;
    # Navigate.route ← href / url / to. The canonical name wins.
    if tag == "Call" and "endpoint" not in obj and "url" in obj:
        obj = {**obj, "endpoint": obj["url"]}
        del obj["url"]
    elif tag == "Navigate" and "route" not in obj:
        for a in ("href", "url", "to"):
            if a in obj:
                obj = {**obj, "route": obj[a]}
                del obj[a]
                break
    elif tag == "Dispatch" and "msg" in obj:
        # 0.2.0 — the `msg` closure sentinel is off the wire (no decoder ever
        # read it); a pre-0.2.0 input normalises to the bare `{"$type":"Dispatch"}`.
        obj = {k: v for k, v in obj.items() if k != "msg"}
    elif tag == "Chain":
        # Recurse so nested actions pick up the same normalisation.
        ops_arr = _expect_array(_require(obj, "ops", path), f"{path}.ops")
        decoded_ops = Arr([_decode_action(o, f"{path}.ops[{i}]") for i, o in enumerate(ops_arr)])
        rest = {k: _from_json_strict(v, f"{path}.{k}") for k, v in obj.items() if k not in ("$type", "ops")}
        return Obj("Chain", {"ops": decoded_ops, **rest})
    # Structural (validated discriminator) but NULL-STRICT: the action payload
    # positions (SetState.value / Notify.payload / AiTool.args) are structured
    # JVal positions per rule 12, and no action case carries a §5 opaque seam —
    # so a null anywhere in an action rejects at its exact path, matching the
    # F# reference and the corpus reject-null-action-* fixtures.
    return _from_json_strict(obj, path)


def _decode_number(value: object, path: str) -> Value:
    # A JSON number — an ``int`` or ``float`` (e.g. Date.step in seconds). bool is an
    # int subclass; reject it as it is never a numeric value here.
    value = _unwrap_static_envelope(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(WRONG_TYPE, path, f"expected a number at {path}")
    return value  # type: ignore[return-value]


# ── Drawing (Phase 524) ────────────────────────────────────────────────────
#
# A bounded, typed vector-graphics primitive. Geometry is static numbers (a
# Drawing is a resolved artefact); only DrawStyle carries Bindings. The Shape
# and CurveCommand DUs are closed + typed — an unrecognised discriminator is
# UNKNOWN_DU_CASE via ``_dispatch`` (the typed-surface default-deny). Array
# positions use ``[i]`` bracket paths to match the F# reference reject paths.

DRAW_SHAPE_CASES = frozenset(
    {"Group", "Rectangle", "Line", "Polyline", "Polygon", "Curve", "Circle", "Ellipse", "Label"}
)
CURVE_COMMAND_CASES = frozenset({"MoveTo", "LineTo", "CubicTo", "QuadraticTo", "Close"})


def _decode_view_box(value: object, path: str) -> Value:
    obj = _expect_object(value, path)
    return Obj(
        None,
        {
            "height": _decode_number(_require(obj, "height", path), f"{path}.height"),
            "minX": _decode_number(_require(obj, "minX", path), f"{path}.minX"),
            "minY": _decode_number(_require(obj, "minY", path), f"{path}.minY"),
            "width": _decode_number(_require(obj, "width", path), f"{path}.width"),
        },
    )


def _decode_draw_point(value: object, path: str) -> Value:
    obj = _expect_object(value, path)
    return Obj(
        None,
        {
            "x": _decode_number(_require(obj, "x", path), f"{path}.x"),
            "y": _decode_number(_require(obj, "y", path), f"{path}.y"),
        },
    )


def _decode_draw_style(value: object, path: str) -> Value:
    obj = _expect_object(value, path)
    fields: dict[str, Value] = {}
    for key in ("fill", "opacity", "stroke", "strokeWidth"):
        if key in obj:
            fields[key] = _decode_binding(obj[key], f"{path}.{key}")
    # Text-only fields (Phase 528.1) — bare enum / number / string, not bindings;
    # all optional, omitted when unset (byte-unchanged for non-text shapes).
    if "textAnchor" in obj:
        fields["textAnchor"] = _enum(obj["textAnchor"], f"{path}.textAnchor", TEXT_ANCHOR, "textAnchor")
    if "fontSize" in obj:
        fields["fontSize"] = _decode_number(obj["fontSize"], f"{path}.fontSize")
    if "emphasis" in obj:
        fields["emphasis"] = _enum(obj["emphasis"], f"{path}.emphasis", EMPHASIS, "emphasis")
    if "fontFamily" in obj:
        fields["fontFamily"] = _expect_string(obj["fontFamily"], f"{path}.fontFamily")
    return Obj(None, fields)


def _decode_draw_point_array(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_decode_draw_point(item, f"{path}[{i}]") for i, item in enumerate(arr)])


def _decode_curve_command(value: object, path: str) -> Value:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, CURVE_COMMAND_CASES)
    if tag in ("MoveTo", "LineTo"):
        return Obj(tag, {"to": _decode_draw_point(_require(obj, "to", path), f"{path}.to")})
    if tag == "CubicTo":
        return Obj(
            tag,
            {
                "control1": _decode_draw_point(_require(obj, "control1", path), f"{path}.control1"),
                "control2": _decode_draw_point(_require(obj, "control2", path), f"{path}.control2"),
                "to": _decode_draw_point(_require(obj, "to", path), f"{path}.to"),
            },
        )
    if tag == "QuadraticTo":
        return Obj(
            tag,
            {
                "control": _decode_draw_point(_require(obj, "control", path), f"{path}.control"),
                "to": _decode_draw_point(_require(obj, "to", path), f"{path}.to"),
            },
        )
    return Obj("Close", {})  # tag == "Close"


def _decode_curve_command_array(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_decode_curve_command(item, f"{path}[{i}]") for i, item in enumerate(arr)])


def _decode_shape(value: object, path: str) -> Value:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, DRAW_SHAPE_CASES)
    style = _decode_draw_style(obj["style"], f"{path}.style") if "style" in obj else Obj(None, {})
    if tag == "Group":
        return Obj(
            tag,
            {
                "children": _decode_shape_array(_require(obj, "children", path), f"{path}.children"),
                "style": style,
            },
        )
    if tag == "Rectangle":
        fields: dict[str, Value] = {
            "height": _decode_number(_require(obj, "height", path), f"{path}.height"),
            "style": style,
            "width": _decode_number(_require(obj, "width", path), f"{path}.width"),
            "x": _decode_number(_require(obj, "x", path), f"{path}.x"),
            "y": _decode_number(_require(obj, "y", path), f"{path}.y"),
        }
        if "cornerRadius" in obj:
            fields["cornerRadius"] = _decode_number(obj["cornerRadius"], f"{path}.cornerRadius")
        return Obj(tag, fields)
    if tag == "Line":
        return Obj(
            tag,
            {
                "style": style,
                "x1": _decode_number(_require(obj, "x1", path), f"{path}.x1"),
                "x2": _decode_number(_require(obj, "x2", path), f"{path}.x2"),
                "y1": _decode_number(_require(obj, "y1", path), f"{path}.y1"),
                "y2": _decode_number(_require(obj, "y2", path), f"{path}.y2"),
            },
        )
    if tag in ("Polyline", "Polygon"):
        return Obj(
            tag,
            {
                "points": _decode_draw_point_array(_require(obj, "points", path), f"{path}.points"),
                "style": style,
            },
        )
    if tag == "Curve":
        return Obj(
            tag,
            {
                "commands": _decode_curve_command_array(_require(obj, "commands", path), f"{path}.commands"),
                "style": style,
            },
        )
    if tag == "Circle":
        return Obj(
            tag,
            {
                "cx": _decode_number(_require(obj, "cx", path), f"{path}.cx"),
                "cy": _decode_number(_require(obj, "cy", path), f"{path}.cy"),
                "r": _decode_number(_require(obj, "r", path), f"{path}.r"),
                "style": style,
            },
        )
    if tag == "Ellipse":
        return Obj(
            tag,
            {
                "cx": _decode_number(_require(obj, "cx", path), f"{path}.cx"),
                "cy": _decode_number(_require(obj, "cy", path), f"{path}.cy"),
                "rx": _decode_number(_require(obj, "rx", path), f"{path}.rx"),
                "ry": _decode_number(_require(obj, "ry", path), f"{path}.ry"),
                "style": style,
            },
        )
    # Label
    return Obj(
        tag,
        {
            "style": style,
            "text": _decode_text_source(_require(obj, "text", path), f"{path}.text"),
            "x": _decode_number(_require(obj, "x", path), f"{path}.x"),
            "y": _decode_number(_require(obj, "y", path), f"{path}.y"),
        },
    )


def _decode_shape_array(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_decode_shape(item, f"{path}[{i}]") for i, item in enumerate(arr)])


# ── Per-kind field schemas: (field, required, decoder[, aliases]) ──────────
# A decoder returns a wire :data:`~fuaran_py.model.Value`, or the `_DROP` sentinel
# (an `object`) for a Phase 460 omit-when-default field. An optional 4th tuple
# element lists the field's decode-only name aliases (WIRE_FORMAT §3.6).

# 0.2.0 rename law — retired names are a clean break: not aliased, not
# preserved (`Metric.source` / `LabelValueRow.source`; `source` is reserved
# for collection feeds).
_RETIRED_FIELDS: dict[str, frozenset[str]] = {
    "Metric": frozenset({"source"}),
    "LabelValueRow": frozenset({"source"}),
}

FieldDecoder = Callable[[object, str], object]
SchemaEntry = tuple[str, bool, FieldDecoder] | tuple[str, bool, FieldDecoder, tuple[str, ...]]


def _unpack_schema(entry: SchemaEntry) -> tuple[str, bool, FieldDecoder, tuple[str, ...]]:
    """Normalise a 3- or 4-tuple schema entry to (name, required, decoder, aliases)."""
    if len(entry) == 4:
        return entry  # type: ignore[return-value]
    return entry[0], entry[1], entry[2], ()


KIND_SCHEMAS: dict[str, list[SchemaEntry]] = {
    "Heading": [
        ("level", True, _decode_int),
        ("text", True, _decode_text_source),
        ("variant", True, _enum_aliased_decoder(HEADING_VARIANT, HEADING_VARIANT_ALIASES, "variant")),
    ],
    "Markdown": [
        ("text", True, _decode_text_source),
    ],
    # Phase 460 — `format` / `tone` / `weight` / `emphasis` are omitted-when-default.
    # 0.2.0 rename law (clean break): the scalar displayed value is `value`
    # (`data` stays a web-prior alias); the retired `source` is NOT accepted.
    "Metric": [
        ("emphasis", False, _omit_default_emphasis_enum),
        ("format", False, _omit_default_format),
        ("label", True, _decode_text_source),
        ("value", True, _decode_binding, ("data",)),
        ("tone", False, _omit_default_enum(TONE, TONE_ALIASES, "Default", "tone")),
        ("weight", False, _omit_default_enum(WEIGHT, {}, "Standard", "weight")),
        ("icon", False, _decode_string),
        ("subtext", False, _decode_text_source),
        ("trend", False, _decode_binding),
        ("trendFormat", False, _decode_cell_format),
    ],
    # The labeled TEXT fact (2026-07-17) — Metric's complementary kind: only
    # `label` + `value` required; `tone` / `emphasis` omitted-when-default on
    # BOTH boundaries; optional `help` / `icon`. `emphasis` is the behavioural
    # BOOL (cross-vocab coercion via `_decode_emphasis_flag`).
    "Fact": [
        ("emphasis", False, _decode_emphasis_flag),
        ("help", False, _decode_text_source),
        ("icon", False, _decode_string),
        ("label", True, _decode_text_source),
        ("tone", False, _omit_default_enum(TONE, TONE_ALIASES, "Default", "tone")),
        ("value", True, _decode_text_source),
    ],
    "Badge": [
        ("label", True, _decode_text_source),
        ("variant", True, _enum_aliased_decoder(BADGE_VARIANT, BADGE_VARIANT_ALIASES, "variant")),
    ],
    "Callout": [
        ("body", True, _decode_text_source),
        # 0.2.0 — omitted-when-false on both boundaries.
        ("dismissable", False, _omit_default_bool(False)),
        ("tone", False, _omit_default_enum(TONE, TONE_ALIASES, "Default", "tone")),
        ("heading", False, _decode_text_source, ("title",)),
        ("icon", False, _decode_string),
    ],
    "Progress": [
        ("fraction", True, _decode_binding),
        # 0.2.0 — omitted-when-false on both boundaries.
        ("indeterminate", False, _omit_default_bool(False)),
        ("tone", False, _omit_default_enum(TONE, TONE_ALIASES, "Default", "tone")),
        ("label", False, _decode_text_source),
        ("caveat", False, _decode_text_source),
    ],
    "Skeleton": [
        ("rows", True, _decode_int),
    ],
    "Sparkline": [
        # Phase 429 — `source` is a typed Static float-series position; `data` alias (§3.6).
        ("source", True, _decode_binding_float_seq, ("data",)),
    ],
    # Phase 429 — Map `source` is a typed Static marker-list position. The three
    # numeric envelope fields pass through structurally like any unlisted key.
    # `source` aliases `data` / `markers` (§3.6).
    "Map": [
        ("source", True, _decode_binding_marker_seq, ("data", "markers")),
    ],
    "LabelValueRow": [
        # `emphasis` is the behavioural bool (cross-vocab coerced) — 0.2.2:
        # omitted-when-false. `format` omitted-when-default. 0.2.0 rename law:
        # the scalar value is `value` (`data` alias; retired `source` NOT accepted).
        ("emphasis", False, _decode_emphasis_flag),
        ("format", False, _omit_default_format),
        ("label", True, _decode_text_source),
        ("value", True, _decode_binding, ("data",)),
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
        # 0.2.0 — the one omit-when-TRUE (a toast is dismissable unless said otherwise).
        ("dismissable", False, _omit_default_bool(True)),
        ("message", True, _decode_text_source),
        ("open", True, _decode_binding),
        ("tone", False, _omit_default_enum(TONE, TONE_ALIASES, "Default", "tone")),
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
    "Drawing": [
        # Phase 524 — geometry static; the closed Shape / CurveCommand DUs
        # default-deny an unknown discriminator; DrawStyle carries the bindings.
        ("description", False, _decode_text_source),
        ("shapes", True, _decode_shape_array),
        ("style", True, _decode_draw_style),
        ("title", False, _decode_text_source),
        ("viewBox", True, _decode_view_box),
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
        # `source` aliases `options` / `data` (§3.6).
        ("source", True, _decode_binding_select_options, ("options", "data")),
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
        ("heading", False, _decode_text_source, ("title",)),
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
        # `variant` alias-decoded (Danger→Destructive, §3.6); other fields
        # (icon / disabled / tooltip / …) pass through structurally.
        ("variant", False, _enum_aliased_decoder(BUTTON_VARIANT, BUTTON_VARIANT_ALIASES, "variant")),
    ],
    "Custom": [
        ("moduleId", True, _decode_string),
        ("componentId", True, _decode_string),
        ("props", False, _decode_json_value),
        ("contentHash", False, _decode_json_value),
        ("exposedNodeIds", False, _decode_json_value),
    ],
    # State-bound conditional child (Phase 392). `cases` is an array of
    # `{child,match}` objects; `default` a Node; `stateKey` a string — all
    # required (the reject fixtures pin MISSING_FIELD at each). Duplicate `match`
    # values are NOT a decode error (first-match-wins; the validator flags them).
    "Switch": [
        ("cases", True, _decode_switch_cases),
        ("default", True, _decode_single_node),
        ("stateKey", True, _decode_string),
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
            "direction": _enum_aliased(
                _require(obj, "direction", path), f"{path}.direction", ORIENTATION, ORIENTATION_ALIASES, "direction"
            ),
            "wrap": _expect_bool(_require(obj, "wrap", path), f"{path}.wrap"),
        }
        if "gap" in obj:
            fields["gap"] = _expect_int(obj["gap"], f"{path}.gap")
        return Obj("Flex", fields)
    if tag == "Grid":
        # `cols` aliases `columns` (§3.6).
        cols_raw, cols_present = _alias_get(obj, "cols", ("columns",))
        if not cols_present and "templateColumns" not in obj:
            # §3.6 lenient shape coercion: a Grid with NO cols/columns/
            # templateColumns is the CSS auto-grid prior — coerce to the
            # responsive `Auto` layout (accept-and-canonicalise).
            return Obj("Auto", {})
        # 0.1.7 — a Grid with `templateColumns` but no column count defaults
        # `cols` to 1 (a `Some templateColumns` supersedes `cols`).
        cols = _expect_int(cols_raw, f"{path}.cols") if cols_present else 1
        gfields: dict[str, Value] = {"cols": cols}
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
    # `heading` aliases `title` (§3.6, scoped to container kinds).
    heading_raw, heading_present = _alias_get(obj, "heading", ("title",))
    if heading_present:
        fields["heading"] = _decode_text_source(heading_raw, f"{path}.heading")
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


def _decode_legacy_table(obj: dict, path: str) -> Obj:
    """Decode-upgrade a retired ``Table`` tag to a static read-only ``DataGrid`` (Phase 393).

    The static text table becomes the ``staticRows`` mode of ``DataGrid``; it is accepted on
    read but never re-encodes as ``Table`` (the resulting Obj is a ``DataGrid``). Byte-parity
    with the F#/TS static grid: an empty column set + an opaque ``Static`` source that
    re-encodes to ``{"$type":"Static","value":"<opaque>"}``.
    """
    headers = _decode_text_source_array(_require(obj, "headers", path), f"{path}.headers")
    rows_arr = _expect_array(_require(obj, "rows", path), f"{path}.rows")
    rows = Arr([_decode_text_source_array(row, f"{path}.rows[{i}]") for i, row in enumerate(rows_arr)])
    static_rows = Obj(None, {"headers": headers, "rows": rows})
    return Obj(
        "DataGrid",
        {
            "columns": Arr([]),
            "source": Obj("Static", {"value": "<opaque>"}),
            "staticRows": static_rows,
        },
    )


def _decode_kind(value: object, path: str) -> Obj:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, KNOWN_KINDS, code_unknown=WRONG_NODE_KIND)
    if tag == "Box":
        return _decode_box(obj, path)
    if tag in _LEGACY_CONTAINER_TAGS:
        return _decode_legacy_container(tag, obj, path)
    if tag == "Table":
        return _decode_legacy_table(obj, path)
    if tag == "DataGrid":
        return _decode_datagrid(obj, path)
    if tag == "Form":
        return _decode_form(obj, path)
    if tag == "Filters":
        return _decode_filters(obj, path)
    if tag == "Chart":
        return _decode_chart(obj, path)
    if tag in _TITLE_TO_HEADING_KINDS:
        obj = _title_to_heading(obj)
    schema = KIND_SCHEMAS.get(tag)
    if schema is None:
        # Recognised kind without a typed schema yet — accept structurally.
        return Obj(tag, {k: from_json(v) for k, v in obj.items() if k != "$type"})

    # Schema entries are (name, required, decoder) or (name, required, decoder, aliases).
    known: set[str] = set()
    for entry in schema:
        name, _, _, aliases = _unpack_schema(entry)
        known.add(name)
        known.update(aliases)
    fields: dict[str, Value] = {}
    for entry in schema:
        name, required, dec, aliases = _unpack_schema(entry)
        raw, present = _alias_get(obj, name, aliases)
        if present:
            decoded = dec(raw, f"{path}.{name}")
            # Phase 460 omit-when-default: a `_DROP` result is omitted from the model
            # (so the generic encoder re-emits the byte-minimal canonical form).
            if decoded is not _DROP:
                fields[name] = cast(Value, decoded)
        elif required:
            _fail(MISSING_FIELD, f"{path}.{name}", f"missing required field '{name}'")
    # Preserve any extra (unknown) keys structurally so the round-trip is lossless
    # and tolerant of fields a later spec version adds (decoder tolerance, §2 rule 2).
    # Retired vocabulary (0.2.0 clean break) is NEVER preserved — the reference
    # decoder does not read it, so carrying it forward would mint a second dialect.
    retired = _RETIRED_FIELDS.get(tag, frozenset())
    for key, raw in obj.items():
        if key != "$type" and key not in known and key not in retired:
            fields[key] = from_json(raw)
    return Obj(tag, fields)


# ── Scoped `title` → `heading` alias (WIRE_FORMAT §3.6, decode-only) ─────────
# Box / Modal / Disclosure / SummaryList / Callout name their heading slot
# `heading`; the `title` alias is the common author prior. SCOPED: Chart.title
# and Drawing.title are real canonical fields and are never aliased.
_TITLE_TO_HEADING_KINDS = frozenset({"Disclosure", "SummaryList"})


def _title_to_heading(obj: dict) -> dict:
    if "heading" not in obj and "title" in obj:
        out = {k: v for k, v in obj.items() if k != "title"}
        out["heading"] = obj["title"]
        return out
    return obj


def _decode_cell_kind(value: object, path: str) -> Value:
    """A DataGrid column's cell kind — a `$type`-discriminated case, preserved
    structurally (the closure/handler payloads are host-side)."""
    obj = _expect_object(value, path)
    if "$type" not in obj:
        _fail(MISSING_FIELD, f"{path}.$type", "missing $type discriminator")
    return from_json(value)


def _decode_grid_source(value: object, path: str) -> Value:
    """A DataGrid/Chart data source. Its rows are the §5 host-typed opaque seam: a
    ``Static`` payload erases to ``"<opaque>"`` (byte-stable + value-faithful) —
    a bare JSON array coerces to that same erased ``Static`` (§3.6); every other
    binding case (Transform/Query/…) normalises through the binding decoder."""
    if isinstance(value, list):
        return Obj("Static", {"value": OPAQUE})
    if isinstance(value, dict) and value.get("$type") == "Static":
        return Obj("Static", {"value": OPAQUE})
    if isinstance(value, dict) and value.get("$type") == "Bound" and "binding" in value:
        return _decode_grid_source(value["binding"], f"{path}.binding")
    obj = _expect_object(value, path)
    _dispatch(obj, path, BINDING_CASES | {"Bound"})
    return _normalise_binding_obj(obj, path)


def _decode_column(value: object, path: str) -> Value:
    """A DataGrid ``ColumnErased`` record (WIRE_FORMAT §3.6): ``kind`` ← ``type``,
    ``label`` ← ``header`` / ``title``, ``format`` / ``width`` omitted-when-default
    (``CellFormat.None`` / ``ColumnWidth.Auto``). ``value`` (closure) + ``field``
    (declarative) are sibling optional slots preserved structurally."""
    obj = _expect_object(value, path)
    fields: dict[str, Value] = {}
    kind_raw, kind_present = _alias_get(obj, "kind", ("type",))
    if kind_present:
        fields["kind"] = _decode_cell_kind(kind_raw, f"{path}.kind")
    label_raw, label_present = _alias_get(obj, "label", ("header", "title"))
    if label_present:
        fields["label"] = _expect_string(label_raw, f"{path}.label")
    if "format" in obj:
        fv = _omit_default_format(obj["format"], f"{path}.format")
        if fv is not _DROP:
            fields["format"] = cast(Value, fv)
    if "width" in obj:
        wv = _omit_default_width(obj["width"], f"{path}.width")
        if wv is not _DROP:
            fields["width"] = cast(Value, wv)
    _column_known = frozenset({"kind", "type", "label", "header", "title", "format", "width"})
    for key, raw in obj.items():
        if key not in _column_known:
            fields[key] = from_json(raw)
    return Obj(None, fields)


def _decode_datagrid(obj: dict, path: str) -> Obj:
    """DataGrid (GridSpec, WIRE_FORMAT §3.6): ``source`` ← ``data`` / ``rows`` (the
    rows are opaque-erased), typed ``columns``. Remaining fields (``editable`` /
    ``rowKey`` / ``rowKeyField`` / ``staticRows`` / ``onRowClick``) pass through
    structurally, as the pre-typed decoder did."""
    fields: dict[str, Value] = {}
    src_raw, src_present = _alias_get(obj, "source", ("data", "rows"))
    if src_present:
        fields["source"] = _decode_grid_source(src_raw, f"{path}.source")
    if "columns" in obj:
        arr = _expect_array(obj["columns"], f"{path}.columns")
        fields["columns"] = Arr([_decode_column(c, f"{path}.columns[{i}]") for i, c in enumerate(arr)])
    # 0.2.0 — `editable` omitted-when-false on both boundaries.
    if "editable" in obj and _expect_bool(obj["editable"], f"{path}.editable"):
        fields["editable"] = True
    _grid_known = frozenset({"$type", "source", "data", "rows", "columns", "editable"})
    for key, raw in obj.items():
        if key not in _grid_known:
            fields[key] = from_json(raw)
    return Obj("DataGrid", fields)


def _decode_chart(obj: dict, path: str) -> Obj:
    """Chart (ChartSpec, WIRE_FORMAT §3.6): ``source`` ← ``data`` (opaque-erased
    rows). ``title`` is a real canonical field here (NOT the `heading` alias).
    Remaining fields pass through structurally."""
    fields: dict[str, Value] = {}
    src_raw, src_present = _alias_get(obj, "source", ("data",))
    if src_present:
        fields["source"] = _decode_grid_source(src_raw, f"{path}.source")
    _chart_known = frozenset({"$type", "source", "data"})
    for key, raw in obj.items():
        if key not in _chart_known:
            fields[key] = from_json(raw)
    return Obj("Chart", fields)


# ── FormFieldKind — the unified control vocabulary (0.2.0 filters-unification) ──
#
# One decoder covers form fields AND filter chips. The auto-bind context
# (`FilterChip name` | `FormFieldId id` | none) mirrors the F# `ControlAutoBind`:
# a `value` that is exactly the context's auto-binding — `Filter(name)` on a
# chip, `State(field id, typed placeholder)` on a form field (0.2.1) — is
# OMITTED from the model, so the canonical minimal control carries no `value`
# key at all; an absent `value` simply stays absent (the canonical bytes).

FORM_FIELD_KIND_CASES = frozenset(
    {"Text", "Number", "Checkbox", "Choice", "RangedNumber", "SegmentedChoice", "TextArea", "Range", "Date"}
)

# The 0.2.1 typed placeholders (the F# `ControlValueDefaults`): the values the
# form-field auto-binding `State(field id, <placeholder>)` carries per control.
_AUTO_TEXT: tuple[Value, ...] = ("",)
_AUTO_NUMBER: tuple[Value, ...] = (0, 0.0)
_AUTO_CHECKBOX: tuple[Value, ...] = (False,)
_AUTO_CHOICE: tuple[Value, ...] = (None,)
_AUTO_RANGE: tuple[Value, ...] = (Obj(None, {"max": 0, "min": 0}), Obj(None, {"max": 0.0, "min": 0.0}))


def _is_auto_value(decoded: Value, auto: tuple[str, str] | None, placeholders: tuple[Value, ...]) -> bool:
    """Is `decoded` exactly the context's auto-binding (drop it from the model)?"""
    if auto is None or not isinstance(decoded, Obj):
        return False
    context, name = auto
    if context == "filter":
        return decoded == Obj("Filter", {"name": name})
    # form field: State(field id, typed placeholder)
    if decoded.tag != "State" or decoded.fields.get("key") != name:
        return False
    if "defaultValue" not in decoded.fields:
        return False
    return any(decoded.fields["defaultValue"] == p for p in placeholders)


def _decode_range_pair_value(value: object, path: str) -> Value:
    """A `FormFieldKind.Range` value: the canonical Static pair rides as the
    BARE `{"max":…,"min":…}` object (no envelope); a `[min,max]` two-element
    array and the enveloped `Static` form decode leniently (§3.6); any other
    binding case passes through the normal binding decode."""
    raw: object = value
    if isinstance(raw, dict) and raw.get("$type") == "Static" and "value" in raw:
        raw = raw["value"]
    if isinstance(raw, list):
        if len(raw) != 2:
            _fail(WRONG_TYPE, path, "a range value array must carry exactly [min, max]")
        lo = _decode_number(raw[0], f"{path}[0]")
        hi = _decode_number(raw[1], f"{path}[1]")
        return Obj(None, {"max": hi, "min": lo})
    if isinstance(raw, dict) and "$type" not in raw and "min" in raw and "max" in raw:
        return Obj(
            None,
            {
                "max": _decode_number(raw["max"], f"{path}.max"),
                "min": _decode_number(raw["min"], f"{path}.min"),
            },
        )
    return _decode_binding(value, path)


def _decode_form_field_kind(value: object, path: str, auto: tuple[str, str] | None) -> Obj:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, FORM_FIELD_KIND_CASES)
    fields: dict[str, Value] = {}

    handler_key = "onToggle" if tag == "Checkbox" else "onChange"
    if handler_key in obj:
        # A present handler (any spelling) decodes to the closure placeholder
        # and re-encodes as the sentinel; an absent one arms the write-back default.
        fields[handler_key] = "<closure>"

    def value_slot(dec: Callable[[object, str], Value], placeholders: tuple[Value, ...]) -> None:
        if "value" in obj:
            decoded = dec(obj["value"], f"{path}.value")
            if not _is_auto_value(decoded, auto, placeholders):
                fields["value"] = decoded
        # absent: stays absent — the canonical minimal control (auto-bound at
        # run time to $filters.<name> / $state.<field id>).

    def bound(key: str, dec: Callable[[object, str], Value]) -> None:
        if key in obj:
            fields[key] = dec(obj[key], f"{path}.{key}")

    if tag in ("Text", "TextArea", "Date"):
        value_slot(_decode_binding, _AUTO_TEXT)
        if tag == "TextArea":
            fields["rows"] = _expect_int(_require(obj, "rows", path), f"{path}.rows")
        if tag == "Date":
            fields["variant"] = _enum(_require(obj, "variant", path), f"{path}.variant", DATE_VARIANT, "variant")
            bound("min", _decode_string)
            bound("max", _decode_string)
            bound("step", _decode_number)
    elif tag in ("Number", "RangedNumber"):
        value_slot(_decode_binding, _AUTO_NUMBER)
        if tag == "RangedNumber":
            bound("min", _decode_number)
            bound("max", _decode_number)
            bound("step", _decode_number)
    elif tag == "Checkbox":
        value_slot(_decode_binding, _AUTO_CHECKBOX)
    elif tag in ("Choice", "SegmentedChoice"):
        fields["options"] = _decode_binding_select_options(_require(obj, "options", path), f"{path}.options")
        value_slot(_decode_binding_string_opt, _AUTO_CHOICE)
        if tag == "SegmentedChoice":
            # §3.6 — an absent `orientation` restores the language default
            # `Horizontal` (the universal segmented-control prior); the
            # canonical encoder always emits it.
            if "orientation" in obj:
                fields["orientation"] = _enum_aliased(
                    obj["orientation"], f"{path}.orientation", ORIENTATION, ORIENTATION_ALIASES, "orientation"
                )
            else:
                fields["orientation"] = "Horizontal"
    else:  # Range (0.2.0 — absorbed the retired FilterKind.RangeFilter)
        if "value" in obj:
            decoded = _decode_range_pair_value(obj["value"], f"{path}.value")
            if not _is_auto_value(decoded, auto, _AUTO_RANGE):
                fields["value"] = decoded
        bound("min", _decode_number)
        bound("max", _decode_number)
        bound("step", _decode_number)

    known = {"$type", "value", "options", "orientation", "rows", "variant", "min", "max", "step", handler_key}
    for key, raw in obj.items():
        if key not in known:
            fields[key] = from_json(raw)
    return Obj(tag, fields)


def _decode_form(obj: dict, path: str) -> Obj:
    """Form: typed fields (id ← name, WIRE_FORMAT §3.6; kind through the shared
    FormFieldKind decoder with the 0.2.1 `FormFieldId` auto-bind context),
    `submitLabel` TextSource, `onSubmit` Action, optional `disabled` binding."""
    fields: dict[str, Value] = {}
    if "fields" in obj:
        arr = _expect_array(obj["fields"], f"{path}.fields")
        norm: list[Value] = []
        for i, fld in enumerate(arr):
            fpath = f"{path}.fields[{i}]"
            fobj = _expect_object(fld, fpath)
            id_raw, id_present = _alias_get(fobj, "id", ("name",))
            if not id_present:
                _fail(MISSING_FIELD, f"{fpath}.id", "missing required field 'id'")
            fid = _expect_string(id_raw, f"{fpath}.id")
            ffields: dict[str, Value] = {"id": fid}
            ffields["kind"] = _decode_form_field_kind(_require(fobj, "kind", fpath), f"{fpath}.kind", ("state", fid))
            ffields["label"] = _decode_text_source(_require(fobj, "label", fpath), f"{fpath}.label")
            ffields["required"] = _expect_bool(_require(fobj, "required", fpath), f"{fpath}.required")
            if "help" in fobj:
                ffields["help"] = _decode_text_source(fobj["help"], f"{fpath}.help")
            for key, raw in fobj.items():
                if key not in ("id", "name", "kind", "label", "required", "help"):
                    ffields[key] = from_json(raw)
            norm.append(Obj(None, ffields))
        fields["fields"] = Arr(norm)
    if "onSubmit" in obj:
        fields["onSubmit"] = _decode_action(obj["onSubmit"], f"{path}.onSubmit")
    if "submitLabel" in obj:
        fields["submitLabel"] = _decode_text_source(obj["submitLabel"], f"{path}.submitLabel")
    if "disabled" in obj:
        fields["disabled"] = _decode_binding(obj["disabled"], f"{path}.disabled")
    for key, raw in obj.items():
        if key not in ("$type", "fields", "onSubmit", "submitLabel", "disabled"):
            fields[key] = from_json(raw)
    return Obj("Form", fields)


def _decode_filters(obj: dict, path: str) -> Obj:
    """Filters (0.2.0 unification): each item is `{kind:<FormFieldKind>, label,
    name}` — the chip's control is an ordinary form control; an absent `value`
    auto-binds `Filter(<the chip's own name>)`, and the encoder symmetrically
    omits a `value` that is exactly that auto binding."""
    fields: dict[str, Value] = {}
    items_raw = _require(obj, "items", path)
    arr = _expect_array(items_raw, f"{path}.items")
    items: list[Value] = []
    for i, item in enumerate(arr):
        ipath = f"{path}.items[{i}]"
        iobj = _expect_object(item, ipath)
        name = _expect_string(_require(iobj, "name", ipath), f"{ipath}.name")
        ifields: dict[str, Value] = {
            "kind": _decode_form_field_kind(_require(iobj, "kind", ipath), f"{ipath}.kind", ("filter", name)),
            "label": _decode_text_source(_require(iobj, "label", ipath), f"{ipath}.label"),
            "name": name,
        }
        for key, raw in iobj.items():
            if key not in ("kind", "label", "name"):
                ifields[key] = from_json(raw)
        items.append(Obj(None, ifields))
    fields["items"] = Arr(items)
    for key, raw in obj.items():
        if key not in ("$type", "items"):
            fields[key] = from_json(raw)
    return Obj("Filters", fields)


def _decode_style(value: object, path: str) -> Obj:
    # Phase 460 / Phase 147 — every SemanticStyle field is omitted-when-default on
    # the wire (`Emphasis.Normal` / `ToneVariant.Default` / `StyleWeight.Standard`
    # / `StyleRole.None` / `FontVoice.Default`); the decoder restores each default
    # on absence and drops explicit-default values, so an all-default style decodes
    # to an EMPTY object (which the caller omits entirely). tone/weight/emphasis
    # accept the §3.6 lenient-ingest aliases; role/voice do not.
    obj = _expect_object(value, path)
    fields: dict[str, Value] = {}
    if "emphasis" in obj:
        v = _decode_emphasis_enum(obj["emphasis"], f"{path}.emphasis")
        if v != "Normal":
            fields["emphasis"] = v
    if "tone" in obj:
        v = _enum_aliased(obj["tone"], f"{path}.tone", TONE, TONE_ALIASES, "tone")
        if v != "Default":
            fields["tone"] = v
    if "weight" in obj:
        v = _enum_aliased(obj["weight"], f"{path}.weight", WEIGHT, {}, "weight")
        if v != "Standard":
            fields["weight"] = v
    if "role" in obj:
        r = _enum(obj["role"], f"{path}.role", STYLE_ROLE, "role")
        if r != "None":
            fields["role"] = r
    if "voice" in obj:
        vo = _enum(obj["voice"], f"{path}.voice", FONT_VOICE, "voice")
        if vo != "Default":
            fields["voice"] = vo
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
        style = _decode_style(obj["style"], f"{path}.style")
        # An all-default (empty) SemanticStyle is omitted entirely (§3.1 / Phase 460).
        if style.fields:
            extras["style"] = style
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
