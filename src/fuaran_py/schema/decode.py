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

import math
from collections.abc import Callable
from typing import cast

from ..limits import MAX_NODE_DEPTH, MAX_NODES
from ..model import Arr, Node, Obj, Value, from_json
from ..result import (
    EMPTY_NODE_ID,
    LIMIT_EXCEEDED,
    MISSING_FIELD,
    UNKNOWN_DU_CASE,
    WRONG_NODE_KIND,
    WRONG_TYPE,
    DecodeError,
    DecodeResult,
    Err,
    Ok,
)
from ..shapeguard import check_shape, load_bounded

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

# The legal `ToneVariant` names in declaration order, extracted because two positions now
# teach them — a `tone` field and (Phase 750) a `TonedPill` tone-map value. A second inline
# copy is exactly how one of them comes to name six tones. `TONE` derives from it, so the
# membership test and the taught vocabulary cannot drift.
TONE_NAMES = ("Default", "Subdued", "Brand", "Success", "Warning", "Critical", "Info")
TONE = frozenset(TONE_NAMES)
WEIGHT = frozenset({"Compact", "Standard", "Spacious"})
EMPHASIS = frozenset({"Quiet", "Normal", "Loud"})
TEXT_ANCHOR = frozenset({"Start", "Middle", "End"})
ORIENTATION = frozenset({"Vertical", "Horizontal"})
BADGE_VARIANT = frozenset({"Neutral", "Brand", "Success", "Warning", "Critical", "Info"})
HEADING_VARIANT = frozenset({"Standard", "Eyebrow", "Caption", "Lead"})
STYLE_ROLE = frozenset({"None", "Eyebrow", "Data", "Lede", "Caption"})
FONT_VOICE = frozenset({"Default", "Display", "Structural"})
LIVE_REGION = frozenset({"polite", "assertive", "off"})
IMAGE_VARIANT = frozenset({"Default", "Avatar", "Rounded"})
# fuaran#1077 — the three `Image` presentation slots (WIRE_FORMAT §3.6.2). All
# three are CLOSED TOKEN vocabularies, never CSS values: `aspectRatio` names one
# of four ratios and carries no number, pair or stylesheet spelling ("16 / 9",
# "16:9", 1.7778), because admitting an arbitrary ratio would put an
# author-supplied value in a style attribute — the free-form escape this format
# does not have. Each is omitted at its identity default on BOTH boundaries.
IMAGE_FIT = frozenset({"Natural", "Cover", "Contain"})
IMAGE_ASPECT = frozenset({"Natural", "Square", "FourThree", "ThreeTwo", "SixteenNine"})
IMAGE_LOADING = frozenset({"Eager", "Lazy"})
# fuaran#1076 — the `MediaKind` variant set (WIRE_FORMAT §3.6.6), CLOSED at two.
# `$type`-discriminated rather than a bare enum, so an unknown case reports at
# `<path>.$type`; a third surface is an ADDITION later, never a spelling a
# decoder may guess at today.
MEDIA_KIND_CASES = frozenset({"Video", "Audio"})
SCROLL_ORIENTATION = frozenset({"Vertical", "Horizontal", "Both"})
DATE_VARIANT = frozenset({"Date", "Time", "DateTime"})
MATH_DISPLAY = frozenset({"Inline", "Block"})
BOX_ROLE = frozenset({"Group", "Card", "Dashboard", "Separator"})  # Phase 390
BOX_LAYOUT_CASES = frozenset({"Flex", "Grid", "Auto"})  # Phase 390
BUTTON_VARIANT = frozenset({"Primary", "Secondary", "Tertiary", "Destructive"})
LINK_PROTECTION = frozenset({"email"})  # Phase 812 — anti-scraper render strategy
# Phase 819 — the Duration / RelativeTime format enums (shared by CellFormat
# and the Binding.Format vocabulary).
DURATION_UNIT = frozenset({"Seconds", "Minutes", "Hours"})
DURATION_STYLE = frozenset({"Compact", "Clock", "Long"})
RELATIVE_TIME_UNIT = frozenset({"Second", "Minute", "Hour", "Day", "Week", "Month", "Year"})
ICON_SIZE = frozenset({"Small", "Medium", "Large"})  # Phase 821 — the Icon display kind
# fuaran#867 — `Metric.trendPolarity`: which direction of movement is an
# improvement. `Neutral` is RESERVED and deliberately NOT a case — that is the
# whole reason the slot is a two-case enum rather than an `inverted: bool`, since
# a later admission is then a bare-string addition and not a type replacement.
TREND_POLARITY = frozenset({"HigherIsBetter", "LowerIsBetter"})

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
        "Now",
        "I18n",
        "Local",
        "Format",
        "Data",
        "Transform",
        "Invoke",
    }
)
CELL_FORMAT_CASES = frozenset(
    {"None", "Number", "Currency", "Percent", "SignificantDigits", "Date", "Duration", "RelativeTime", "Custom"}
)

# Every recognised node-kind discriminator (WIRE_FORMAT.md §3.2). A kind not in
# this set is WRONG_NODE_KIND; a kind in this set but absent from KIND_SCHEMAS is
# accepted structurally.
KNOWN_KINDS = frozenset(
    {
        # Layout
        "Box",  # Phase 390 — the unified container
        "SplitPanel",
        "Tabs",
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
        "Icon",  # Phase 821 — the standalone icon-only display kind
        "LabelValueRow",
        "Link",
        "Image",
        "Media",  # fuaran#1076 — the playback surface (Video | Audio)
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
        # Phase 677 — absence is structural: a MISSING `value` means the binding
        # carries none, and the legacy `"value": null` spelling normalises to the
        # same thing (§16 shorthand). Neither emits a key on re-encode.
        raw = obj.get("value")
        if "value" not in obj or raw is None:
            return Obj("Static", {})
        return Obj("Static", {"value": from_json(raw)})
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
# / ``lenient-null-static-*`` fixtures pin each). fuaran#665 added Chart/DataGrid
# ROWS to this family (see :func:`_decode_grid_source`), leaving only genuinely
# host-typed payloads (Mount inputs, ``PropValue.Native``) on the residual
# ``"<opaque>"`` seam and the plain ``_decode_binding`` above.


def _typed_static_binding(
    value: object,
    path: str,
    on_typed: Callable[[object, str], Value],
    on_opaque: Value,
    on_null: Value,
    *,
    typed_default: bool = False,
) -> Value:
    """Decode a ``Binding`` whose ``Static`` payload is a typed position.

    ``Static`` normalises per the three input forms (typed / ``"<opaque>"`` /
    ``null``); a bare array/scalar coerces to ``Static`` (§3.6) and a ``Bound``
    wrapper unwraps (Phase 633); every other binding case passes through
    structurally (validated discriminator), exactly as :func:`_decode_binding`.

    ``typed_default`` extends the same normalisation to the *other* value-carrying
    binding arm — ``State``/``Selection``'s ``defaultValue`` — which the reference
    hosts route through the slot's typed parser too. Opt-in per slot rather than
    global: only the rows slot (fuaran#665) has a fixture pinning it, and flipping
    the pre-429 typed slots onto it is a behaviour change of its own.
    """
    if isinstance(value, list) or isinstance(value, (str, int, float, bool)):
        return Obj("Static", {"value": on_typed(value, path)})
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, BINDING_CASES | {"Bound"})
    if tag == "Static":
        # Phase 677 — a MISSING `value` and an explicit `null` both mean "no
        # payload" and share the slot's `on_null` normalisation. Where that
        # normalisation is itself absence (`on_null=None`, e.g. the string slot)
        # the key is omitted entirely; where the slot has a typed empty (options
        # normalise to `[]`) that empty is emitted, so "no selection" and
        # "selected nothing" stay distinguishable.
        raw = obj.get("value")
        if "value" not in obj or raw is None:
            normalised = on_null
        elif raw == OPAQUE:
            normalised = on_opaque
        else:
            normalised = on_typed(raw, f"{path}.value")
        return Obj("Static", {} if normalised is None else {"value": normalised})
    if tag == "Bound":
        return _typed_static_binding(
            _require(obj, "binding", path),
            f"{path}.binding",
            on_typed,
            on_opaque,
            on_null,
            typed_default=typed_default,
        )
    if typed_default:

        def _default(raw: object, p: str) -> Value:
            return on_opaque if raw == OPAQUE else on_typed(raw, p)

        return _normalise_binding_obj(obj, path, on_default=_default)
    return _normalise_binding_obj(obj, path)


def _decode_binding_scalar(value: object, path: str, expect: Callable[[object, str], Value], what: str) -> Value:
    """A ``Binding<str>`` / ``Binding<bool>`` slot — the typed SCALAR positions.

    Written out rather than routed through :func:`_typed_static_binding` for one
    reason: an absent / ``null`` ``Static`` payload at a scalar slot is a REFUSAL
    on the reference host (``requireString``/``requireBool`` reject ``JNull``),
    and that helper's ``on_null`` parameter can only normalise, never refuse.

    §3.6's bare-scalar coercion is about SHAPE — every ``Binding`` case is a
    ``$type``-discriminated object, so a bare scalar can only mean ``Static`` —
    and the slot's own ``'T`` still governs the VALUE. Without the type check
    ``{"hidden": "yes"}`` decoded happily with its value preserved, which is why
    this host answered none of the corpus's a11y reject vectors.

    The non-``Static`` arms keep the untyped ``defaultValue`` handling the
    pre-429 typed slots already have (see :func:`_typed_static_binding`'s
    ``typed_default`` note): routing those onto the slot parser is a separate
    behaviour change with no fixture pinning it.
    """
    if isinstance(value, list) or isinstance(value, (str, int, float, bool)):
        return Obj("Static", {"value": expect(value, path)})
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, BINDING_CASES | {"Bound"})
    if tag == "Static":
        raw = obj.get("value")
        if "value" not in obj or raw is None:
            _fail(WRONG_TYPE, f"{path}.value", f"expected {what} at {path}.value")
        return Obj("Static", {"value": expect(raw, f"{path}.value")})
    if tag == "Bound":
        return _decode_binding_scalar(_require(obj, "binding", path), f"{path}.binding", expect, what)
    return _normalise_binding_obj(obj, path)


def _decode_binding_string(value: object, path: str) -> Value:
    return _decode_binding_scalar(value, path, _expect_string, "a string")


def _decode_binding_bool(value: object, path: str) -> Value:
    return _decode_binding_scalar(value, path, _expect_bool, "a boolean")


def _decode_binding_float(value: object, path: str) -> Value:
    """A ``Binding<float>`` slot — §7's FLOAT accept set at a binding position.

    Same machinery as the string/bool slots above, and deliberately so: §3.6's
    bare-scalar coercion decides only that a bare scalar can only mean ``Static``
    — the slot's own ``'T`` still governs the VALUE — so both arms (the
    ``{"$type":"Static","value":X}`` envelope AND the bare scalar) route through
    :func:`_decode_number`, which is the one place the accept set is written down.
    """
    return _decode_binding_scalar(value, path, _decode_number, "a number")


def _decode_binding_int(value: object, path: str) -> Value:
    """A ``Binding<int>`` slot — §7's INTEGER accept set at a binding position.

    Its parser is :func:`_decode_integer`, NOT :func:`_decode_number`: the two
    numeric slot classes accept different sets, and routing both through one
    parser is precisely the defect this pair exists to prevent.
    """
    return _decode_binding_scalar(value, path, _decode_integer, "an integer")


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
    tag = _dispatch(obj, path, CELL_FORMAT_CASES)
    if tag == "Duration":
        # Phase 819 — trendable duration cells: raw float counts `unit`s,
        # rendered per `style`. Both fields required, typed against the closed
        # enums (the canonical encoder emits alphabetical: style before unit).
        unit = _enum(_require(obj, "unit", path), f"{path}.unit", DURATION_UNIT, "DurationUnit")
        style = _enum(_require(obj, "style", path), f"{path}.style", DURATION_STYLE, "DurationStyle")
        return Obj("Duration", {"style": style, "unit": unit})
    if tag == "RelativeTime":
        # Phase 819 — cell-vocabulary parity with `Format.RelativeTime`.
        unit = _enum(_require(obj, "unit", path), f"{path}.unit", RELATIVE_TIME_UNIT, "RelativeTimeUnit")
        return Obj("RelativeTime", {"unit": unit})
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


# ── Image srcSet + MediaKind (WIRE_FORMAT §3.6.4 / §3.6.6) ─────────────────


def _decode_srcset_entry(value: object, path: str) -> Value:
    """One ``SrcSetEntry`` — ``{"src":<Binding<string>>,"width":<positive int>}``.

    Both members are required *within* the entry. ``width`` is the ``w``
    descriptor a client selects on, and the POSITIVE floor is a decode rule
    rather than a validator one: zero is refused as firmly as a negative,
    because a ``0w`` candidate is not a small image but one a client can never
    select — admitting it would let the wire state a rendition no host can
    render. The published schema says the same thing as ``minimum: 1``.
    """
    obj = _expect_object(value, path)
    src = _decode_binding_string(_require(obj, "src", path), f"{path}.src")
    width = _expect_int(_require(obj, "width", path), f"{path}.width")
    if width <= 0:
        _fail(
            WRONG_TYPE,
            f"{path}.width",
            f"srcSet width must be a POSITIVE integer pixel width, got {width}",
            "JSON number (positive integer pixel width)",
        )
    return Obj(None, {"src": src, "width": width})


def _decode_srcset(value: object, path: str) -> object:
    """``Image.srcSet`` — the MISSING-LIST-FIELD decode class, and the one slot in
    this decoder most worth reading.

    An ABSENT ``srcSet`` is the EMPTY LIST: on the structural model that is the
    key simply not being carried, which is exactly what the empty list denotes,
    so the two spellings are one document by construction. An EMPTY array
    therefore ``_DROP``s to the same shape (the encode half of the rule), and a
    present ``null`` is REFUSED rather than read as absence — absence already has
    a spelling, and admitting a second would let two conformant hosts emit
    different canonical bytes for one document.

    The array ORDER is the author's and is preserved verbatim. Canonicalisation
    sorts object KEYS (§2) and never array elements; a codec that sorted here
    would emit bytes it did not decode. Ascending-by-width is the RENDERER's
    canonicalisation, and putting the sort there is what lets both rules be true.
    """
    items = _expect_array(value, path)
    if not items:
        return _DROP
    return Arr([_decode_srcset_entry(entry, f"{path}[{i}]") for i, entry in enumerate(items)])


def _decode_media_kind(value: object, path: str) -> Value:
    """``MediaSpec.kind`` — which playback surface this is.

    ``$type``-discriminated, so an unknown case reports at ``<path>.$type`` (the
    ``Binding`` / ``TextSource`` position) rather than at the bare slot.

    ``Audio`` declares NO fields, and the absence of an autoplay slot is stronger
    than a default of ``false``: a slot that defaults to off is one a document
    can switch on, and there is no document this format wants to be able to state
    in which a page begins making sound unbidden. A carried
    ``{"$type":"Audio","autoplay":true}`` therefore decodes to an audio surface
    that does not autoplay, because the value has nowhere to land — it is dropped
    rather than preserved structurally, since carrying it forward would re-mint
    on the wire exactly the pathway the case exists not to have.
    """
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, MEDIA_KIND_CASES)
    if tag == "Audio":
        return Obj("Audio", {})
    fields: dict[str, Value] = {}
    # Omitted at `false`, and a present non-boolean is refused rather than
    # coerced — the `Image.expandable` ruling, on the slot where getting it wrong
    # starts playing a video the document says not to.
    if "autoplay" in obj:
        autoplay = _expect_bool(obj["autoplay"], f"{path}.autoplay")
        if autoplay:
            fields["autoplay"] = True
    if "poster" in obj:
        fields["poster"] = _decode_binding_string(obj["poster"], f"{path}.poster")
    return Obj("Video", fields)


def _alias_get(obj: dict, canonical: str, aliases: tuple[str, ...]) -> tuple[object, bool]:
    """Field-name aliasing (WIRE_FORMAT §3.6, decode-only): the canonical name wins
    when both are present; otherwise the first present alias supplies the value."""
    if canonical in obj:
        return obj[canonical], True
    for a in aliases:
        if a in obj:
            return obj[a], True
    return None, False


def _normalise_binding_obj(
    obj: dict,
    path: str,
    *,
    on_default: Callable[[object, str], Value] | None = None,
) -> Value:
    """Normalise a non-``Static`` binding case to its 0.2.0 canonical shape.

    Query: ``dependsOn`` ← ``deps`` / ``dependencies`` (§3.6), omitted when
    empty; the retired ``accessor`` sentinel is dropped (0.2.0). Selection:
    ``accessor`` dropped; ``defaultValue`` (0.2.9) + ``field`` (Phase 632)
    preserved. State: ``defaultValue`` ← ``initialValue`` / ``default``. Now:
    tag-only (``{"$type":"Now"}`` — no fields survive re-encode).
    Transform: ``params`` map form coerces to the canonical ``[{from,name}]``
    array (name-keyed set, §3.6), ``value`` aliases ``from`` at the element,
    and the embedded source + pipeline normalise through the columnar codec.
    Everything else passes through structurally (validated discriminator).

    ``on_default`` decodes the ``defaultValue`` payload of the value-carrying arms
    with a slot's typed parser instead of the structural :func:`from_json` — see
    :func:`_typed_static_binding`'s ``typed_default``."""
    decode_default = on_default if on_default is not None else (lambda raw, _p: from_json(raw))
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
            fields["defaultValue"] = decode_default(obj["defaultValue"], f"{path}.defaultValue")
        if "field" in obj:
            fields["field"] = _expect_string(obj["field"], f"{path}.field")
        fields["nodeId"] = node_id
        return Obj("Selection", fields)
    if tag == "State":
        key = _expect_string(_require(obj, "key", path), f"{path}.key")
        fields = {}
        default_raw, default_present = _alias_get(obj, "defaultValue", ("initialValue", "default"))
        # Phase 677 — an explicit null default is absence, same as omitting it.
        if default_present and default_raw is not None:
            fields["defaultValue"] = decode_default(default_raw, f"{path}.defaultValue")
        fields["key"] = key
        return Obj("State", fields)
    if tag == "Now":
        # The host-furnished current instant (ISO-8601 UTC). The wire form is
        # `{"$type":"Now"}` — no fields: the clock lives in the HOST, resolved
        # once per render pass, never on the wire.
        return Obj("Now", {})
    if tag == "Transform":
        return _decode_transform_binding(obj, path)
    return from_json(obj)


def _normalise_transform_source(raw: object) -> object:
    """fuaran#815 — organic-demand leniencies for the Transform ``source`` slot,
    both observed cross-family (the Tier-D pilot, 2026-08-13): models bind a
    derived value to a Transform whose source is
    ``{"$type":"State","defaultValue":[{row},…]}``. Two universal priors,
    accommodated as typed data at THIS host bridge, before the columnar codec
    sees the value (the Phase 633 ``Bound``-unwrap precedent — no wire-spec
    change, no new key). Mirror of the F# ``normaliseTransformSource``:

    1. a ``State``/``Static``/``Bound`` binding WRAPPER around the data unwraps
       to its ``defaultValue``/``value`` (initial-snapshot semantics — a LIVE
       state-sourced Transform is deliberately not this). A wrapper carrying
       neither passes through UNCHANGED and fails in the columnar decode (the
       ``reject-transform-source-empty-wrapper`` fixture pins it);
    2. ROW-MAJOR data (an array of row objects) transposes to the canonical
       columnar ``{"columns": …}`` shape — FIRST-row key set (sorted ordinal
       ascending, the F# Map ordering), absent cells (and non-object rows)
       filled with JSON null. Canonical columnar and ``ref`` sources pass
       through untouched, so existing fixtures stay byte-identical.

    Ragged / mixed-type rows may still fail downstream — deliberately not
    special-cased."""
    unwrapped = raw
    if isinstance(raw, dict) and raw.get("$type") in ("State", "Static", "Bound"):
        if "defaultValue" in raw:
            unwrapped = raw["defaultValue"]
        elif "value" in raw:
            unwrapped = raw["value"]
    if isinstance(unwrapped, list) and unwrapped and isinstance(unwrapped[0], dict):
        rows = unwrapped
        columns = {k: [row.get(k) if isinstance(row, dict) else None for row in rows] for k in sorted(rows[0])}
        return {"columns": columns}
    return unwrapped


def _decode_transform_binding(obj: dict, path: str) -> Value:
    """The `Binding.Transform` case (Phase 282/424): `source` + `pipeline`
    normalise through the columnar codec (`fuaran_py.dataframe`), which owns the
    lenient columnar/expression ingest; `params` carries the §3.6 map coercion +
    the `value` ← `from` element alias. The `source` value first rounds through
    the fuaran#815 wrapper/row-major normalisation (`_normalise_transform_source`).

    fuaran#818 — a binding-shaped source (State / Selection / Query ``$type``) is
    PRESERVED as the live source: the decoded binding sits in the ``source`` slot
    verbatim (canonical re-encode is byte-for-byte — one wire dialect) and the
    compute evaluator re-derives against the current store, falling back to the
    initial snapshot from the binding's carried default data. A State wrapper
    carrying NO data still errors didactically through the columnar codec (the
    815 posture), and a State wrapper's carried data is snapshot-VALIDATED here
    so the ragged-rows didactic stays byte-identical to the snapshot era."""
    source_raw_orig = _require(obj, "source", path)
    pipeline_raw = _require(obj, "pipeline", path)
    live_tag = source_raw_orig.get("$type") if isinstance(source_raw_orig, dict) else None
    preserved = live_tag in ("State", "Selection", "Query")
    has_carried = isinstance(source_raw_orig, dict) and "defaultValue" in source_raw_orig
    # §16 / §24.4 — an EMPTY carried array is the EMPTY TABLE, not a malformed
    # source. An initially-empty live collection ("count the requests in an
    # empty log") is a complete intent with zero rows and no columns to infer,
    # and under §24.4 it is also how a reader spells "I read this key and carry
    # no data of my own" while a SIBLING reader's declaration seeds the slot.
    # Sending it through the columnar codec instead ACCUSES it: a bare ``[]``
    # has no first row to transpose from, so it reaches ``decode_source_json``
    # as an array and surfaces ``MALFORMED_SHAPE: expected object, got array``
    # against a document the reference hosts decode — which is what kept
    # ``nodes/shared-source-seeded-pair`` red on this host. The binding is
    # preserved exactly as the carried-data arm preserves it; only the snapshot
    # VALIDATION is skipped, because an empty array has nothing to validate.
    empty_carried = has_carried and source_raw_orig["defaultValue"] == []  # type: ignore[index]
    if preserved and live_tag == "State" and not has_carried:
        # No carried data — the 815 path (unwrap a legacy `value` payload, or
        # surface the columnar codec's own missing-field didactic).
        preserved = False
    if preserved:
        assert isinstance(source_raw_orig, dict)
        source = _decode_binding(source_raw_orig, f"{path}.source")
        if live_tag == "State" and not empty_carried:
            # Validate the carried data as the initial snapshot (didactics
            # byte-identical to the 815 snapshot decode); the preserved binding
            # stays the stored source.
            from ..dataframe.codec import decode_source_json

            snapshot = decode_source_json(_normalise_transform_source(source_raw_orig))
            if not snapshot.ok:
                _fail(WRONG_TYPE, f"{path}.source", f"{snapshot.error.code}: {snapshot.error.detail}")
        from ..dataframe.codec import decode_pipeline_json, encode_transform_value

        pipe_result = decode_pipeline_json(pipeline_raw)
        if not pipe_result.ok:
            _fail(WRONG_TYPE, f"{path}.pipeline", f"{pipe_result.error.code}: {pipe_result.error.detail}")
            raise AssertionError("unreachable")
        pipeline: Value = Arr([encode_transform_value(t) for t in pipe_result.value])
    else:
        source_raw = _normalise_transform_source(source_raw_orig)
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
    elif tag == "SetState":
        # fuaran#818 — `value` (a literal JSON value, written verbatim) XOR
        # `valueFrom` (a Binding evaluated at dispatch time inside the existing
        # gate). Exactly one must be present; both / neither error didactically
        # naming both fields. A present `valueFrom` decodes through the binding
        # decoder (the typed default-deny surface); the literal `value` keeps
        # the structural null-strict pass-through below.
        has_value = "value" in obj
        has_from = "valueFrom" in obj
        if has_value and has_from:
            _fail(
                WRONG_TYPE,
                f"{path}.valueFrom",
                "SetState carries both 'value' and 'valueFrom' — exactly one is allowed: "
                "'value' is a literal JSON value written verbatim; 'valueFrom' derives the "
                "written value from a Binding at dispatch time; remove one",
            )
        if not has_value and not has_from:
            _fail(
                MISSING_FIELD,
                f"{path}.value",
                "missing required field 'value' — provide 'value' (a literal JSON value) "
                "or 'valueFrom' (a Binding evaluated at dispatch time)",
            )
        if has_from:
            value_from = _decode_binding(obj["valueFrom"], f"{path}.valueFrom")
            rest = {k: _from_json_strict(v, f"{path}.{k}") for k, v in obj.items() if k not in ("$type", "valueFrom")}
            return Obj("SetState", {**rest, "valueFrom": value_from})
    # Structural (validated discriminator) but NULL-STRICT: the action payload
    # positions (SetState.value / Notify.payload / AiTool.args) are structured
    # JVal positions per rule 12, and no action case carries a §5 opaque seam —
    # so a null anywhere in an action rejects at its exact path, matching the
    # F# reference and the corpus reject-null-action-* fixtures.
    return _from_json_strict(obj, path)


#: The three §5/§7 quoted sentinels for a non-finite number, mapped to the value
#: they denote. §7 is symmetric with §5: a float slot accepts BOTH a JSON number
#: and the quoted spelling this host itself emits for a non-finite. Without them
#: a document this host encodes is one it cannot read back — ``decode → encode →
#: decode`` does not close on any non-finite number, and a peer host's canonical
#: output is undecodable here.
_NON_FINITE_SENTINELS: dict[str, float] = {
    "NaN": float("nan"),
    "Infinity": float("inf"),
    "-Infinity": float("-inf"),
}


def _decode_number(value: object, path: str) -> Value:
    # A JSON number — an ``int`` or ``float`` (e.g. Date.step in seconds) — or one
    # of the three §7 non-finite sentinel strings. bool is an int subclass; reject
    # it as it is never a numeric value here.
    #
    # This is the FLOAT-slot choke point. Integer slots go through ``_expect_int``
    # (a bare int position) or ``_decode_integer`` (a ``Binding<int>`` slot), both
    # unreachable from here, so a sentinel at an integer slot stays a WRONG_TYPE —
    # §7 widens float slots and nothing else.
    #
    # The float is returned rather than the sentinel string so decode is idempotent
    # at the TREE level and not merely at the byte level: ``json.loads`` already
    # turns the bare overflowing literal ``-1e999`` into ``-inf``, so answering a
    # re-decode of its own canonical form with a ``str`` would hand a consumer a
    # float the first time and a string the second.
    value = _unwrap_static_envelope(value)
    if isinstance(value, str):
        sentinel = _NON_FINITE_SENTINELS.get(value)
        if sentinel is not None:
            return sentinel
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(WRONG_TYPE, path, f"expected a number at {path}")
    return value  # type: ignore[return-value]


def _decode_integer(value: object, path: str) -> Value:
    """The INTEGER-slot choke point — §7's *other* numeric accept set.

    §7 is asymmetric on purpose, and the asymmetry is the whole content of this
    function::

        a FLOAT slot accepts  { JSON number } ∪ { "NaN", "Infinity", "-Infinity" }
        an INT  slot accepts  { JSON number }                     (truncating)

    An integer has no non-finite form, so a *correctly spelled* sentinel string
    here is a ``WRONG_TYPE`` — the case that makes the two sets distinguishable
    rather than merely stated. The truncating integer cast mirrors the reference
    host's ``requireInt`` (``JNumber n -> Ok(int n)``).

    Three Python-specific traps, all of them silent if missed:

    * ``bool`` is an ``int`` subclass, so it is tested FIRST — ``True`` would
      otherwise satisfy ``isinstance(v, int)`` and truncate to ``1``.
    * The sentinel widening is a *membership test against three exact strings*
      (:data:`_NON_FINITE_SENTINELS`) and lives in :func:`_decode_number` alone;
      a ``float(s)`` in a ``try`` would accept ``"nan"``, ``"inf"``, ``"1e5"``
      and ``"  NaN "`` at both slot classes.
    * A non-finite ``float`` — reachable only through ``json.loads``'s default
      acceptance of the bare ``NaN`` / ``Infinity`` *tokens*, a §20
      decode-determinism question this does not reopen — is refused rather than
      cast, because ``int(float("nan"))`` raises and decoding is TOTAL: a typed
      refusal, never an exception.
    """
    value = _unwrap_static_envelope(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(WRONG_TYPE, path, f"expected an integer at {path}")
    if isinstance(value, float) and not math.isfinite(value):
        _fail(WRONG_TYPE, path, f"expected a finite integer at {path} — an integer slot has no non-finite form")
    return int(cast(float, value))


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
            # `fill` / `stroke` are Binding<string> (a colour token); `opacity` /
            # `strokeWidth` are Binding<float> — typed here, closing the numeric
            # half of the sweep Phase 956 opened on the scalar slots.
            decoder = _decode_binding_string if key in ("fill", "stroke") else _decode_binding_float
            fields[key] = decoder(obj[key], f"{path}.{key}")
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
    # Phase 642 — keyed mark identity (`data-fuaran-mark` at render time); optional.
    if "markId" in obj:
        fields["markId"] = _expect_string(obj["markId"], f"{path}.markId")
    # Phase 877 — Label text rotation in degrees, clockwise; optional with no
    # default (absent = upright). Keys off PRESENCE in the object: an explicit
    # 0 is a distinct present value, so a truthiness test here would drop it and
    # re-encode to different bytes.
    if "rotation" in obj:
        fields["rotation"] = _decode_number(obj["rotation"], f"{path}.rotation")
    # Phase 883 — the per-mark hover readout, a full TextSource (so a `Bound`
    # envelope decodes here as well as the canonical bare-string `Literal`).
    # Optional, absent = untipped. Keys off PRESENCE for the same reason
    # `rotation` does, and here the trap is sharper: an explicitly EMPTY tip is
    # a distinct present value, and `if obj.get("tip"):` would silently drop it.
    if "tip" in obj:
        fields["tip"] = _decode_text_source(obj["tip"], f"{path}.tip")
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
        ("value", True, _decode_binding_float, ("data",)),
        ("tone", False, _omit_default_enum(TONE, TONE_ALIASES, "Default", "tone")),
        ("weight", False, _omit_default_enum(WEIGHT, {}, "Standard", "weight")),
        ("icon", False, _decode_string),
        ("subtext", False, _decode_text_source),
        ("trend", False, _decode_binding_float),
        ("trendFormat", False, _decode_cell_format),
        # fuaran#867 — `trendPolarity` is omitted-when-`HigherIsBetter` (§3.6's
        # omit-when-default table). An absent `trend` makes the slot inert: a
        # Metric with no trend that declares a polarity is legal and says nothing,
        # so nothing here couples the two.
        ("trendPolarity", False, _omit_default_enum(TREND_POLARITY, {}, "HigherIsBetter", "trendPolarity")),
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
        ("fraction", True, _decode_binding_float),
        # 0.2.0 — omitted-when-false on both boundaries.
        ("indeterminate", False, _omit_default_bool(False)),
        ("tone", False, _omit_default_enum(TONE, TONE_ALIASES, "Default", "tone")),
        ("label", False, _decode_text_source),
        ("caveat", False, _decode_text_source),
    ],
    "Skeleton": [
        ("rows", True, _decode_int),
    ],
    # Phase 821 — the standalone icon-only display kind: `size` omitted-when-
    # `Medium`, `tone` omitted-when-`Default` (the Phase 460 discipline),
    # `label` omitted-when-decorative.
    "Icon": [
        ("icon", True, _decode_string),
        ("label", False, _decode_string),
        ("size", False, _omit_default_enum(ICON_SIZE, {}, "Medium", "IconSize")),
        ("tone", False, _omit_default_enum(TONE, TONE_ALIASES, "Default", "tone")),
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
        ("value", True, _decode_binding_float, ("data",)),
        ("help", False, _decode_text_source),
    ],
    "Link": [
        ("download", True, _decode_bool),
        ("href", True, _decode_binding_string),
        ("label", True, _decode_text_source),
        # Phase 812 — optional closed enumeration; unknown case rejects
        # UNKNOWN_DU_CASE at $.kind.protection via the _enum default-deny.
        ("protection", False, _enum_decoder(LINK_PROTECTION, "protection")),
        ("rel", False, _decode_string),
        ("target", False, _decode_string),
    ],
    "Image": [
        ("alt", True, _decode_text_source),
        # fuaran#1077 — the three presentation slots, omitted at their identity
        # defaults on BOTH boundaries, so a document written before they existed
        # decodes to today's behaviour and re-encodes to the bytes it already had.
        ("aspectRatio", False, _omit_default_enum(IMAGE_ASPECT, {}, "Natural", "aspectRatio")),
        # fuaran#1078 — a caption is CONTENT, so it is NOT an identity default:
        # there is no default caption the way there is a default fit, and the slot
        # takes the ordinary optional-field posture (omitted when absent). It is a
        # full `TextSource`, not a string — the rule a second host is most likely
        # to break, because a caption reads like a string and narrowing the slot
        # costs nothing until somebody needs a locale.
        ("caption", False, _decode_text_source),
        # fuaran#1079 — an ordinary omit-at-`false` bool. A present non-boolean is
        # a WRONG_TYPE, never a truthiness coercion: a decoder that guessed at
        # `"true"` would have to rule on `"false"` and `""` too, at which point two
        # conformant hosts can disagree about whether the document declares an
        # affordance at all.
        ("expandable", False, _omit_default_bool(False)),
        ("fit", False, _omit_default_enum(IMAGE_FIT, {}, "Natural", "fit")),
        ("loading", False, _omit_default_enum(IMAGE_LOADING, {}, "Eager", "loading")),
        ("src", True, _decode_binding_string),
        # fuaran#1080 — absent MEANS the empty list and `null` is refused; see
        # `_decode_srcset`.
        ("srcSet", False, _decode_srcset),
        ("variant", True, _enum_decoder(IMAGE_VARIANT, "variant")),
    ],
    # fuaran#1076 — the playback surface. ONE kind, two variants: everything a
    # video surface and an audio surface SHARE is stated once on the record (the
    # source, the accessible name, whether the transport is shown, whether
    # playback repeats), and only the slots that genuinely differ live in the
    # `$type`-discriminated variant at `kind`.
    #
    # `label` is REQUIRED, and this is the one place the media contract differs
    # from `Image`'s: an image can honestly be decorative and say so with an empty
    # `alt`, but a media element is a TRANSPORT — a control a reader focuses,
    # plays, pauses and seeks — so it is never decorative, and there is no value
    # to default to that would not be a fabricated name for someone else's
    # recording.
    #
    # `controls` is omitted at TRUE — the second such slot in the vocabulary after
    # `Toast.dismissable`, and the polarity is deliberate: a media element without
    # a transport cannot be paused, seeked or muted by a keyboard user at all, so
    # the accessible setting is what a document gets for free and taking it away
    # is the deviation that costs a key. `loop` takes the ordinary polarity.
    "Media": [
        ("controls", False, _omit_default_bool(True)),
        ("kind", True, _decode_media_kind),
        ("label", True, _decode_text_source),
        ("loop", False, _omit_default_bool(False)),
        ("src", True, _decode_binding_string),
    ],
    "List": [
        ("items", True, _decode_text_source_array),
        ("ordered", True, _decode_bool),
    ],
    "Toast": [
        # 0.2.0 — the one omit-when-TRUE (a toast is dismissable unless said otherwise).
        ("dismissable", False, _omit_default_bool(True)),
        ("message", True, _decode_text_source),
        ("open", True, _decode_binding_bool),
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
        ("disabled", False, _decode_binding_bool),
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
        ("open", True, _decode_binding_bool),
        ("heading", False, _decode_text_source, ("title",)),
    ],
    "ScrollArea": [
        ("children", True, _decode_children),
        ("orientation", True, _enum_decoder(SCROLL_ORIENTATION, "orientation")),
        ("maxHeight", False, _decode_int),
        ("maxWidth", False, _decode_int),
    ],
    # Tabs / Stepper carry the language's only two ``Binding<int>`` slots, and
    # had no typed schema at all — so the whole kind reached the structural
    # pass-through and `activeIndex: true` decoded happily. These entries are
    # DELIBERATELY MINIMAL: they type the numeric slot and nothing else, leaving
    # every other key (`children`, `tabHeaders`, `tabTags`, `activeTag`,
    # `onSelect`, …) to the same structural preservation it had before, so the
    # blast radius is the slot this phase is about. Requiredness follows the
    # reference host: `activeIndex` is optional (`tryField`), `activeStep`
    # required (`requireField`).
    "Tabs": [
        ("activeIndex", False, _decode_binding_int),
    ],
    "Stepper": [
        ("activeStep", True, _decode_binding_int),
    ],
    # Box (Phase 390) — decoded by a dedicated builder (`_decode_box`), not a flat
    # field schema, because it re-nests `layout` and role-validates.
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
    # Switch (Phase 392, selector widened Phase 768) — decoded by a dedicated
    # builder (`_decode_switch`), not a flat field schema, because the selector
    # is one-of `stateKey` / `on` with the Phase 768 collapse rule.
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


def _decode_switch(obj: dict, path: str) -> Obj:
    """Switch — the binding-selected conditional child (Phase 392; the selector
    widened to any Binding by Phase 768).

    ``cases`` is an array of ``{child,match}`` objects; ``default`` a Node —
    both required. Duplicate ``match`` values are NOT a decode error
    (first-match-wins keeps decode structural; the validator flags them,
    FUARAN082). The selector is one of two spellings: ``on`` (any Binding —
    wins when both are present) or the compact ``stateKey`` string, the
    canonical spelling of the ``State(key)`` form. Both absent keeps the
    ``stateKey`` MISSING_FIELD, so the reject fixture's error is unchanged.
    The Phase 768 collapse rule: an ``on`` that decodes to a default-free
    ``State`` normalises to ``stateKey``, so the canonical bytes carry ``on``
    only for a selector the compact form cannot spell."""
    fields: dict[str, Value] = {
        "cases": _decode_switch_cases(_require(obj, "cases", path), f"{path}.cases"),
        "default": _decode_single_node(_require(obj, "default", path), f"{path}.default"),
    }
    if "on" in obj:
        selector = _decode_binding(obj["on"], f"{path}.on")
        if isinstance(selector, Obj) and selector.tag == "State" and "defaultValue" not in selector.fields:
            fields["stateKey"] = selector.fields["key"]
        else:
            fields["on"] = selector
    else:
        fields["stateKey"] = _decode_string(_require(obj, "stateKey", path), f"{path}.stateKey")
    known = frozenset({"$type", "cases", "default", "on", "stateKey"})
    for key, raw in obj.items():
        if key not in known:
            fields[key] = from_json(raw)
    return Obj("Switch", fields)


def _decode_kind(value: object, path: str) -> Obj:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, KNOWN_KINDS, code_unknown=WRONG_NODE_KIND)
    if tag == "Box":
        return _decode_box(obj, path)
    if tag == "Switch":
        return _decode_switch(obj, path)
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


# ── TonedPill (WIRE_FORMAT §3.6 + §16, Phase 750) ───────────────────────────
# The tone-map field names a `TonedPill` cell accepts (canonical first). `map` is the
# shortest honest name for a value→tone dictionary and the least descriptive one.
_TONE_MAP_KEYS = ("toneMap", "tones")


def _decode_tone_map(value: object, path: str) -> Obj:
    """A ``TonedPill``'s ``map``: a string-keyed object whose VALUES are ``ToneVariant``s.

    Routed through the ordinary tone reader per entry, which buys two things deliberately
    rather than by accident: the §3.6 tone aliases work inside the map exactly as they do
    at a ``tone`` field, and an unrecognised value is refused rather than carried forward.
    A second, private tone reader here is precisely how this position would come to accept
    a vocabulary the ``tone`` field does not.

    The refusal is RE-ISSUED rather than passed through: the shared reader reports
    ``unrecognised tone '…'`` with the enum's own sorted hint, which does not say *which
    map entry* is wrong — and "one of your tones is wrong" is not an actionable report
    when the map has nine entries. The re-issue keeps the code, names the offending KEY
    and value in the terms the author wrote them, and teaches the seven legal names.
    """
    obj = _expect_object(value, path)
    fields: dict[str, Value] = {}
    for key, raw in obj.items():
        entry_path = f"{path}.{key}"
        try:
            fields[key] = _enum_aliased(raw, entry_path, TONE, TONE_ALIASES, "tone")
        except _Fail as f:
            # A non-string value is a WRONG_TYPE and already reports at the right path.
            if f.error.code != UNKNOWN_DU_CASE:
                raise
            got = raw if isinstance(raw, str) else ""
            _fail(
                UNKNOWN_DU_CASE,
                entry_path,
                f"tone-map value '{got}' for '{key}' is not a ToneVariant",
                " | ".join(TONE_NAMES),
            )
    return Obj(None, fields)


def _decode_toned_pill(obj: dict, path: str) -> Obj:
    """The shared body of the canonical ``TonedPill`` case and the ``Pill``-tagged §16
    shorthand below — one reader, so the two spellings cannot drift apart in what they
    accept."""
    fields: dict[str, Value] = {}
    field_raw, field_present = _alias_get(obj, "field", ())
    if not field_present:
        _fail(
            MISSING_FIELD,
            f"{path}.field",
            "missing required field 'field'",
            "TonedPill row-field name (drives the label and the map key)",
        )
    fields["field"] = _expect_string(field_raw, f"{path}.field")
    map_raw, map_present = _alias_get(obj, "map", _TONE_MAP_KEYS)
    if not map_present:
        _fail(
            MISSING_FIELD,
            f"{path}.map",
            "missing required field 'map'",
            "TonedPill value→ToneVariant map",
        )
    fields["map"] = _decode_tone_map(map_raw, f"{path}.map")
    # `default` is omitted-when-`Default` (Phase 460); an absent key restores the
    # identity, and an aliased `Neutral` normalises to `Default` and then omits.
    if "default" in obj:
        tone = _enum_aliased(obj["default"], f"{path}.default", TONE, TONE_ALIASES, "tone")
        if tone != "Default":
            fields["default"] = tone
    return Obj("TonedPill", fields)


def _decode_cell_kind(value: object, path: str) -> Value:
    """A DataGrid column's cell kind — a `$type`-discriminated case, preserved
    structurally (the closure/handler payloads are host-side) except for the one case
    that carries no closure and so survives the wire: `TonedPill` (Phase 750)."""
    obj = _expect_object(value, path)
    if "$type" not in obj:
        _fail(MISSING_FIELD, f"{path}.$type", "missing $type discriminator")
    tag = obj["$type"]
    if tag == "TonedPill":
        return _decode_toned_pill(obj, path)
    # Lenient-ingest (WIRE_FORMAT §16, Phase 750): "pill" is the WORD for the thing, so a
    # declarative tone rule arrives tagged `Pill` more often than tagged `TonedPill`.
    # Before this phase the extra keys fell through the structural pass-through and the
    # author's whole intent was carried as a closure pill's dead payload. Presence of a
    # tone map is the unambiguous tell — a closure `Pill` carries only `labelFn`/`toneFn`
    # and can never carry one.
    if tag == "Pill" and any(k in obj for k in ("map", *_TONE_MAP_KEYS)):
        return _decode_toned_pill(obj, path)
    return from_json(value)


def _decode_row_cell(value: object, path: str) -> Value:
    """One cell of a typed row (fuaran#665) — the residual-opaque boundary, narrowed
    from the whole rows payload to the cell seam.

    The §2 rule-11 recognised scalars (string / bool / number) carry faithfully; a
    nested array or object is display-opaque and normalises to the ``"<opaque>"``
    sentinel, which is what the reference hosts *re-encode* such a cell as — so this
    host's decode-time normalisation keeps the round-trip byte-stable in one pass
    rather than two (the established ``_typed_static_binding`` idiom above).
    """
    del path  # every JSON value is representable here; nothing rejects
    if isinstance(value, bool) or isinstance(value, (int, float, str)):
        return value
    return OPAQUE


def _decode_row(value: object, path: str) -> Value:
    """One row: an *open* name→value record of scalar cells. A ``null`` cell is
    OMITTED (rule 4 — absence is structural, never ``"k":null``), matching what the
    reference encoders emit. Built structurally rather than via :func:`from_json` so
    a cell named ``$type`` stays a cell, never a discriminator."""
    obj = _expect_object(value, path)
    return Obj(None, {k: _decode_row_cell(v, f"{path}.{k}") for k, v in obj.items() if v is not None})


def _decode_row_array(value: object, path: str) -> Value:
    arr = _expect_array(value, path)
    return Arr([_decode_row(item, f"{path}[{i}]") for i, item in enumerate(arr)])


def _decode_grid_source(value: object, path: str) -> Value:
    """A DataGrid/Chart data source. fuaran#665 moved its rows off the §5 host-typed
    opaque seam: a ``Static``/``State`` payload is a typed array of row objects, and a
    bare JSON array coerces to ``Static`` of the same (§3.6). Both legacy spellings —
    the ``"<opaque>"`` sentinel a pre-typed host emitted, and an absent/``null``
    payload — normalise to the empty feed ``[]`` (read-compat, indefinitely: that
    *was* the whole value the sentinel carried). Every other binding case
    (Transform/Query/…) normalises through the binding decoder."""
    # `typed_default` — the editable-grid authoring shape is a `State`-sourced rows
    # array (the write-back floor), so `defaultValue` carries rows just as `value`
    # does and must take the same normalisation.
    return _typed_static_binding(value, path, _decode_row_array, Arr([]), Arr([]), typed_default=True)


def _check_near_misses(
    obj: dict,
    path: str,
    candidates: tuple[tuple[str, str], ...],
    vocabulary: str = "grid",
    consequence: str = "",
) -> None:
    """fuaran#863 — decode-time didactics for the grid-behaviour family's NEAR MISSES
    (the fuaran#860 charter's rejected-spellings deliverable).

    Every spelling below decoded SILENTLY before: WIRE_FORMAT §2 rule 2 tolerates unknown
    keys, so a model that reached for the wrong name got a tree that decoded, validated and
    rendered while the declaration did nothing — the fake-affordance failure in a new guise,
    and tolerance is what hid it. The narrowing is an ENUMERATED set with an unambiguous
    canonical form each; rule 2 holds for everything else. Walked in declaration order, so
    which defect surfaces first is deterministic across hosts.

    ``consequence`` is an optional trailing clause naming what the silence costs in
    that particular vocabulary (fuaran#959) — the refusal is didactic, and the
    didactic is sharper when it says what was lost, not only what was ignored.
    Empty for the grid, whose message the four other hosts pin unchanged.
    """
    for found, canonical in candidates:
        if found in obj:
            _fail(
                WRONG_TYPE,
                f"{path}.{found}",
                f"'{found}' is not part of the {vocabulary} vocabulary — it would be ignored, "
                f"not honoured{consequence}",
                canonical,
            )


# Named by the census row itself. Deliberately NOT aliased to `editable: false`: an
# inverting alias that guesses wrong makes a read-only column editable.
_COLUMN_NEAR_MISSES: tuple[tuple[str, str], ...] = (
    ("readOnly", "editable: false — the column flag NARROWS the grid's editable capability"),
)

_GRID_NEAR_MISSES: tuple[tuple[str, str], ...] = (
    # The sharpest of them: a LITERAL page number is not expressible at all, because the
    # position lives in State so a control can move it.
    (
        "currentPage",
        'pageStateKey — the page POSITION lives in State as {"page": N} so the pager can move it; '
        "a literal page number is not expressible",
    ),
    (
        "page",
        'pageStateKey — the page POSITION lives in State as {"page": N} so the pager can move it; '
        "a literal page number is not expressible",
    ),
    ("pageIndex", 'pageStateKey — the page POSITION lives in State as {"page": N}, 1-based (not a zero-based index)'),
    (
        "sortable",
        "sortStateKey on the grid + sortable on each COLUMN — grid-wide sortable is the staticRows "
        "spelling; a data-bound grid narrows per column",
    ),
    (
        "onEdit",
        "editStateKey — the edit DESTINATION is a State key on the grid; onEdit is a per-cell host "
        "closure and carries no destination across the wire",
    ),
    (
        "behaviour",
        "sibling fields on the grid (sortStateKey / pageStateKey / pageSize / editStateKey / "
        "defaultSort) — grid behaviour is not a nested record",
    ),
    (
        "behavior",
        "sibling fields on the grid (sortStateKey / pageStateKey / pageSize / editStateKey / "
        "defaultSort) — grid behaviour is not a nested record",
    ),
)


def _decode_column(value: object, path: str) -> Value:
    """A DataGrid ``ColumnErased`` record (WIRE_FORMAT §3.6): ``kind`` ← ``type``,
    ``label`` ← ``header`` / ``title``, ``format`` / ``width`` omitted-when-default
    (``CellFormat.None`` / ``ColumnWidth.Auto``). ``value`` (closure) + ``field``
    (declarative) are sibling optional slots preserved structurally."""
    obj = _expect_object(value, path)
    _check_near_misses(obj, path, _COLUMN_NEAR_MISSES)
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


_SORT_DIRECTIONS = frozenset({"asc", "desc"})


def _validate_static_rows(raw: object, path: str) -> None:
    """Phase 801 — check the two declarative sort-intent slots on ``staticRows``.

    ``staticRows`` itself still passes through structurally (``from_json``), which is
    what makes the round-trip byte-identical for free. What structure cannot do is
    REFUSE: a direction outside the closed pair and a negative header index are both
    well-formed JSON, so without this check they would decode silently and the corpus's
    reject fixtures would pass as accepts. Validation only — nothing is rewritten, so
    the passthrough encoding is untouched.
    """
    if not isinstance(raw, dict):
        return
    sortable = raw.get("sortable")
    if sortable is not None and not isinstance(sortable, bool):
        _fail(WRONG_TYPE, f"{path}.sortable", "sortable must be a boolean")
    default_sort = raw.get("defaultSort")
    if default_sort is None:
        return
    _validate_default_sort(default_sort, f"{path}.defaultSort")


def _validate_default_sort(default_sort: object, ds_path: str) -> None:
    """Phase 801 / fuaran#861 — the ``{column, direction}`` initial-order declaration.

    ONE checker, shared by the ``staticRows`` spelling and the bound grid's own slot: same
    record, same bound, same message at a different path. ``column`` is a NON-NEGATIVE
    index; a negative (or non-integral) value is WRONG_TYPE, which is also what
    ``schema.json``'s ``minimum: 0`` says. An index PAST the end is deliberately accepted —
    a relation between sibling values is not something a per-object codec judges.
    """
    ds = _expect_object(default_sort, ds_path)
    if "column" not in ds:
        _fail(MISSING_FIELD, f"{ds_path}.column", "missing required field 'column'", "non-negative header index")
    column = ds["column"]
    # `bool` is a subclass of `int` in Python — exclude it explicitly, or `true` would
    # decode as column 1.
    if isinstance(column, bool) or not isinstance(column, int) or column < 0:
        _fail(
            WRONG_TYPE,
            f"{ds_path}.column",
            "column must be a non-negative integer header index",
            "JSON number (non-negative integer header index)",
        )
    if "direction" not in ds:
        _fail(MISSING_FIELD, f"{ds_path}.direction", "missing required field 'direction'", "asc | desc")
    _enum(ds["direction"], f"{ds_path}.direction", _SORT_DIRECTIONS, "SortDirection")


def _decode_datagrid(obj: dict, path: str) -> Obj:
    """DataGrid (GridSpec, WIRE_FORMAT §3.6): ``source`` ← ``data`` / ``rows`` (the
    rows are opaque-erased), typed ``columns``. Remaining fields (``editable`` /
    ``rowKey`` / ``rowKeyField`` / ``staticRows`` / ``onRowClick``) pass through
    structurally, as the pre-typed decoder did — with the Phase 801 sort-intent slots
    on ``staticRows`` validated in passing (see ``_validate_static_rows``)."""
    fields: dict[str, Value] = {}
    if "staticRows" in obj:
        _validate_static_rows(obj["staticRows"], f"{path}.staticRows")
    # fuaran#862 — `pageSize` is how many rows a page holds. A page of zero or fewer rows
    # names no page at all, so it is WRONG_TYPE — which is also what schema.json's
    # `minimum: 1` says. Validation only: the field still passes through structurally
    # below, which is what keeps the round-trip byte-identical for free.
    if "pageSize" in obj:
        page_size = obj["pageSize"]
        if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
            _fail(
                WRONG_TYPE,
                f"{path}.pageSize",
                "pageSize must be an integer page size of 1 or more",
                "JSON number (integer page size of 1 or more)",
            )
    # fuaran#861 — the bound path's declared initial order, checked by the SAME function
    # the staticRows spelling uses.
    if "defaultSort" in obj:
        _validate_default_sort(obj["defaultSort"], f"{path}.defaultSort")
    _check_near_misses(obj, path, _GRID_NEAR_MISSES)
    src_raw, src_present = _alias_get(obj, "source", ("data", "rows"))
    if src_present:
        fields["source"] = _decode_grid_source(src_raw, f"{path}.source")
    if "columns" in obj:
        arr = _expect_array(obj["columns"], f"{path}.columns")
        fields["columns"] = Arr([_decode_column(c, f"{path}.columns[{i}]") for i, c in enumerate(arr)])
    # 0.2.0 — `editable` omitted-when-false on both boundaries.
    if "editable" in obj and _expect_bool(obj["editable"], f"{path}.editable"):
        fields["editable"] = True
    # fuaran#818 — the grid-sort header affordance: `sortStateKey` names the
    # State key carrying the `{column, direction}` sort descriptor a data-bound
    # grid's runtime sorts by. Typed as a string; encode-omitted when absent.
    if "sortStateKey" in obj:
        fields["sortStateKey"] = _expect_string(obj["sortStateKey"], f"{path}.sortStateKey")
    _grid_known = frozenset({"$type", "source", "data", "rows", "columns", "editable", "sortStateKey"})
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
    {
        "Text",
        "Number",
        "Checkbox",
        "Toggle",
        "Choice",
        "RangedNumber",
        "SegmentedChoice",
        "TextArea",
        "Range",
        "Date",
        "DateRange",
    }
)

# The 0.2.1 typed placeholders (the F# `ControlValueDefaults`): the values the
# form-field auto-binding `State(field id, <placeholder>)` carries per control.
_AUTO_TEXT: tuple[Value, ...] = ("",)
_AUTO_NUMBER: tuple[Value, ...] = (0, 0.0)
_AUTO_CHECKBOX: tuple[Value, ...] = (False,)
_AUTO_CHOICE: tuple[Value, ...] = (None,)
_AUTO_RANGE: tuple[Value, ...] = (Obj(None, {"max": 0, "min": 0}), Obj(None, {"max": 0.0, "min": 0.0}))
# 0.7.0 — the DateRange placeholder is the ISO-empty pair at both ends.
_AUTO_DATE_RANGE: tuple[Value, ...] = (Obj(None, {"from": "", "to": ""}),)


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


def _ordered_date_pair(lo: str, hi: str, path: str) -> Value:
    """The DateRange ordered-pair rule (WIRE_FORMAT §3.6, 0.7.0).

    A *literal* pair must satisfy ``from <= to``. Same-variant ISO-8601 strings
    sort lexicographically in chronological order, so Python's ordinal string
    compare (the `String.CompareOrdinal` twin) is total here — no date parsing,
    no locale. Only a literal pair is checked; a bound pair's ordering is a
    runtime concern."""
    if lo > hi:
        _fail(
            WRONG_TYPE,
            path,
            f"date-range start '{lo}' is after end '{hi}' — a DateRange pair is ordered (from <= to); "
            "ISO-8601 strings of one variant compare lexicographically, so swap the two values",
            'ordered ISO-8601 pair ({"from": <iso>, "to": <iso>} with from <= to)',
        )
    return Obj(None, {"from": lo, "to": hi})


def _decode_date_range_pair_value(value: object, path: str) -> Value:
    """A `FormFieldKind.DateRange` value: the canonical Static pair rides as the
    BARE `{"from":…,"to":…}` object (no envelope — the `Range` posture); a
    `[from,to]` two-element array and the enveloped `Static` form decode
    leniently (§3.6); any other binding case passes through the normal binding
    decode. A literal pair is ordered-checked; a bound one is not."""
    raw: object = value
    if isinstance(raw, dict) and raw.get("$type") == "Static" and "value" in raw:
        raw = raw["value"]
    if isinstance(raw, list):
        if len(raw) != 2:
            _fail(WRONG_TYPE, path, "a date-range value array must carry exactly [from, to]")
        return _ordered_date_pair(
            _expect_string(raw[0], f"{path}[0]"),
            _expect_string(raw[1], f"{path}[1]"),
            path,
        )
    if isinstance(raw, dict) and "$type" not in raw and "from" in raw and "to" in raw:
        return _ordered_date_pair(
            _expect_string(raw["from"], f"{path}.from"),
            _expect_string(raw["to"], f"{path}.to"),
            path,
        )
    return _decode_binding(value, path)


def _decode_form_field_kind(value: object, path: str, auto: tuple[str, str] | None) -> Obj:
    obj = _expect_object(value, path)
    tag = _dispatch(obj, path, FORM_FIELD_KIND_CASES)
    fields: dict[str, Value] = {}

    handler_key = "onToggle" if tag in ("Checkbox", "Toggle") else "onChange"
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
        # Binding<float> on the reference host — the numeric control's value is a
        # float slot, so it takes the §7 sentinels and refuses everything else.
        value_slot(_decode_binding_float, _AUTO_NUMBER)
        if tag == "RangedNumber":
            bound("min", _decode_number)
            bound("max", _decode_number)
            bound("step", _decode_number)
    elif tag in ("Checkbox", "Toggle"):
        # Toggle (Phase 766) — the switch-styled boolean control: Checkbox's
        # bool mechanics under a distinct tag-only discriminator.
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
    elif tag == "DateRange":
        # 0.7.0 — the single-control date range: `Range`'s pair mechanics with
        # `Date`'s value conventions. `min` / `max` (ISO strings) + `step`
        # (seconds) are flat — they bound BOTH ends — with `Date`'s
        # omit-when-absent discipline.
        if "value" in obj:
            decoded = _decode_date_range_pair_value(obj["value"], f"{path}.value")
            if not _is_auto_value(decoded, auto, _AUTO_DATE_RANGE):
                fields["value"] = decoded
        fields["variant"] = _enum(_require(obj, "variant", path), f"{path}.variant", DATE_VARIANT, "variant")
        bound("min", _decode_string)
        bound("max", _decode_string)
        bound("step", _decode_number)
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


TEXT_FORMATS = frozenset({"email", "url", "tel"})
COMPARE_OPS = frozenset({"eq", "neq", "lt", "lte", "gt", "gte"})

#: The rule slot's rejected spellings. Small and enumerated for the same reason the
#: grid's set is: rule 2's tolerance of unknown keys is right for a field a future
#: profile may add and wrong for a near miss of one that exists, because the tree
#: then decodes and renders while constraining nothing.
FORM_FIELD_NEAR_MISSES: tuple[tuple[str, str], ...] = (
    ("validation", "rule"),
    ("constraints", "rule"),
    ("validate", "rule"),
)

#: The ``Accessibility`` trait's near-miss set (fuaran#959 — the fuaran#863 discipline
#: applied to the §3.1 trait).
#:
#: Rule 2's tolerance of unknown keys is right for a slot a future profile may add and
#: wrong for a near miss of one that exists. That silence is sharper here than anywhere
#: else in the vocabulary, for a reason peculiar to this trait: it has NO VISIBLE OUTPUT.
#: A mislabelled column is on screen; an ignored ``ariaLabel`` looks identical to an
#: honoured one from every side, so the refusal is the only feedback that can ever arrive.
#:
#: Refused rather than aliased. ``ariaLabel`` IS an unambiguous synonym, so admission
#: turns on §16's other half — a shorthand earns its place by being a genuine assist to
#: the emitting model, and a six-character key rename is not one. ``live`` settles it:
#: the HTML idiom it comes from also spells a BOOLEAN, so an alias would bind a
#: possibly-boolean prior onto a closed token set.
#:
#: ``live`` and ``ariaLabel`` are named by MEASURED evidence (6 and 1 emissions against
#: ``liveRegion``'s 12 and ``label``'s 44, across 12,722 language-tier emissions); the
#: rest of their families ride in with them. Declaration order is identical in all five
#: hosts, so which defect surfaces first is deterministic.
A11Y_NEAR_MISSES: tuple[tuple[str, str], ...] = (
    ("aria-label", "label — the accessible name, a Binding<string> (a bare string is the §3.6 shorthand)"),
    ("ariaLabel", "label — the accessible name, a Binding<string> (a bare string is the §3.6 shorthand)"),
    ("aria-labelledby", "labelledBy — the id of a sibling node whose text carries the name"),
    ("ariaLabelledBy", "labelledBy — the id of a sibling node whose text carries the name"),
    ("labelledby", "labelledBy — the slot name is camelCase on the wire, not the ARIA attribute spelling"),
    ("aria-describedby", "describedBy — the id of a sibling node whose text carries the description"),
    ("ariaDescribedBy", "describedBy — the id of a sibling node whose text carries the description"),
    ("describedby", "describedBy — the slot name is camelCase on the wire, not the ARIA attribute spelling"),
    ("aria-role", "role — the ARIA role NAME as a bare string"),
    ("ariaRole", "role — the ARIA role NAME as a bare string"),
    ("aria-live", 'liveRegion — the closed token set "polite" / "assertive" / "off"'),
    ("ariaLive", 'liveRegion — the closed token set "polite" / "assertive" / "off"'),
    ("live", 'liveRegion — the closed token set "polite" / "assertive" / "off"'),
    ("liveregion", 'liveRegion — the closed token set "polite" / "assertive" / "off"'),
    ("aria-hidden", "hidden — a Binding<bool> (a bare bool is the §3.6 shorthand)"),
    ("ariaHidden", "hidden — a Binding<bool> (a bare bool is the §3.6 shorthand)"),
)

#: What the silence costs at this position, appended to the refusal message.
A11Y_NEAR_MISS_CONSEQUENCE = ", and the intent would reach assistive technology as nothing at all"


def _decode_compare_rule(value: object, path: str) -> Value:
    """The cross-field operand. ``against`` is a ``Binding``, and that IS the
    cross-field mechanism rather than an accident of typing: any read slot may take a
    Binding, and the auto-bind rule already puts every form field's value in State
    under the field's own id, so ``{"$type":"State","key":"<sibling id>"}`` reads the
    sibling with no coordination vocabulary at all."""
    obj = _expect_object(value, path)
    return Obj(
        None,
        {
            "against": _decode_binding(_require(obj, "against", path), f"{path}.against"),
            "op": _enum(_require(obj, "op", path), f"{path}.op", COMPARE_OPS, "CompareOp"),
        },
    )


def _decode_field_rule(value: object, path: str) -> Value:
    """A field's declared constraint — ``FormFieldKind`` names the CONTROL, this names
    the ACCEPTED SET. Every slot is optional structurally, and two shapes are refused
    here as POLICY:

    * a rule with every slot absent. A rule that constrains nothing is a defect, not a
      no-op: it decodes, validates and renders while declaring nothing, which is the
      fake-affordance shape the near-miss table also forecloses, arriving through an
      empty object instead of a wrong key. ``message`` alone does not rescue it — the
      message is the prose shown when some OTHER slot is unmet, so a message-only rule
      is the help-text failure wearing the new vocabulary's clothes.
    * ``minLength`` above ``maxLength``. The ordered-pair rule applied to a length
      pair: an inverted bound admits no value at all, so the field can never be
      submitted and the form is dead on arrival.

    Neither is a shape — both are relations BETWEEN slots — which is why they live here
    rather than in the structural layer.
    """
    obj = _expect_object(value, path)
    out: dict[str, Value] = {}
    if "format" in obj:
        out["format"] = _enum(obj["format"], f"{path}.format", TEXT_FORMATS, "TextFormat")
    if "pattern" in obj:
        out["pattern"] = _expect_string(obj["pattern"], f"{path}.pattern")
    if "minLength" in obj:
        out["minLength"] = _expect_int(obj["minLength"], f"{path}.minLength")
    if "maxLength" in obj:
        out["maxLength"] = _expect_int(obj["maxLength"], f"{path}.maxLength")
    if "compare" in obj:
        out["compare"] = _decode_compare_rule(obj["compare"], f"{path}.compare")
    if "message" in obj:
        out["message"] = _decode_text_source(obj["message"], f"{path}.message")

    if not any(k in out for k in ("format", "pattern", "minLength", "maxLength", "compare")):
        _fail(
            WRONG_TYPE,
            path,
            "a rule that constrains nothing is a defect, not a no-op — declare at least one of "
            "format / pattern / minLength / maxLength / compare, or omit 'rule' entirely",
            "FieldRule with at least one constraint slot",
        )

    lo, hi = out.get("minLength"), out.get("maxLength")
    if isinstance(lo, int) and isinstance(hi, int) and lo > hi:
        _fail(
            WRONG_TYPE,
            path,
            f"minLength {lo} is above maxLength {hi} — an inverted length bound admits no value "
            "at all, so the field could never be submitted",
            "minLength <= maxLength",
        )
    return Obj(None, out)


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
            # The near-miss check runs BEFORE the rule decode, so a field carrying
            # both `validation` and a well-formed `rule` still names the ignored key.
            _check_near_misses(fobj, fpath, FORM_FIELD_NEAR_MISSES, "form field")
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
            if "rule" in fobj:
                ffields["rule"] = _decode_field_rule(fobj["rule"], f"{fpath}.rule")
            for key, raw in fobj.items():
                if key not in ("id", "name", "kind", "label", "required", "help", "rule"):
                    ffields[key] = from_json(raw)
            norm.append(Obj(None, ffields))
        fields["fields"] = Arr(norm)
    if "onSubmit" in obj:
        fields["onSubmit"] = _decode_action(obj["onSubmit"], f"{path}.onSubmit")
    if "submitLabel" in obj:
        fields["submitLabel"] = _decode_text_source(obj["submitLabel"], f"{path}.submitLabel")
    if "disabled" in obj:
        fields["disabled"] = _decode_binding_bool(obj["disabled"], f"{path}.disabled")
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


def _decode_accessibility(value: object, path: str) -> Obj:
    """The §3.1 Accessibility trait.

    This was ``from_json`` — a structural pass-through, so every malformed
    payload decoded with its value preserved verbatim and this host answered
    none of the corpus's six a11y reject vectors. ``label`` / ``hidden`` are
    ordinary ``Binding`` slots (the 2026-08-25 §3.1 ruling), so the §3.6
    bare-scalar coercion applies to their SHAPE while the slot's own type still
    governs the value; ``liveRegion`` is a closed token set; ``role`` is open to
    any role NAME but not to any VALUE.
    """
    obj = _expect_object(value, path)
    # fuaran#959 — the near-miss check runs BEFORE the slot reads, matching the
    # ``FormField`` ordering, so a trait carrying both ``ariaLabel`` and a well-formed
    # ``label`` still names the ignored key rather than decoding half the intent silently.
    _check_near_misses(obj, path, A11Y_NEAR_MISSES, "accessibility", A11Y_NEAR_MISS_CONSEQUENCE)
    fields: dict[str, Value] = {}
    if "label" in obj:
        fields["label"] = _decode_binding_string(obj["label"], f"{path}.label")
    if "labelledBy" in obj:
        fields["labelledBy"] = _expect_string(obj["labelledBy"], f"{path}.labelledBy")
    if "describedBy" in obj:
        fields["describedBy"] = _expect_string(obj["describedBy"], f"{path}.describedBy")
    if "role" in obj:
        fields["role"] = _expect_string(obj["role"], f"{path}.role")
    if "liveRegion" in obj:
        fields["liveRegion"] = _enum(obj["liveRegion"], f"{path}.liveRegion", LIVE_REGION, "liveRegion")
    if "hidden" in obj:
        fields["hidden"] = _decode_binding_bool(obj["hidden"], f"{path}.hidden")
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


# ── §21 walk bounds for the structural decoder ───────────────────────
#
# Node depth and total node count are enforced HERE, on the way down (§21.2
# rule 4), rather than measured afterwards from the tree that was built. A check
# that runs after the walk it is meant to bound has already paid the cost it
# exists to refuse.
#
# Module-level counters rather than threaded parameters: `_decode_node_value` is
# reached from the per-kind field decoders through a table of callables whose
# signature is `(value, path)`, so threading a depth argument would mean changing
# every entry in that table and every decoder it names. Sound because decoding is
# synchronous; `_reset_walk` is called by the public entry points, so a walk that
# raised part-way through never leaves a counter poisoned for the next caller.
_walk_depth = 0
_walk_nodes = 0


def _reset_walk() -> None:
    global _walk_depth, _walk_nodes
    _walk_depth = 0
    _walk_nodes = 0


def _decode_node_value(value: object, path: str) -> Node:
    global _walk_depth, _walk_nodes

    if _walk_depth >= MAX_NODE_DEPTH:
        _fail(
            LIMIT_EXCEEDED,
            path,
            f"node nesting deeper than the wire limit MAX_NODE_DEPTH = {MAX_NODE_DEPTH}",
        )
    _walk_nodes += 1
    if _walk_nodes > MAX_NODES:
        _fail(
            LIMIT_EXCEEDED,
            path,
            f"the document holds more than the wire limit MAX_NODES = {MAX_NODES} nodes",
        )

    _walk_depth += 1
    try:
        return _decode_node_value_inner(value, path)
    finally:
        _walk_depth -= 1


def _decode_node_value_inner(value: object, path: str) -> Node:
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
        extras["accessibility"] = _decode_accessibility(obj["accessibility"], f"{path}.accessibility")

    return Node(raw_id, kind, extras)  # type: ignore[arg-type]


def decode_node(text: str) -> DecodeResult[Node]:
    """Decode a canonical-wire ``Node`` document into a :class:`~fuaran_py.model.Node`."""
    parsed, error = load_bounded(text)
    if error is not None:
        return Err(error)
    shape = check_shape(parsed)
    if shape is not None:
        return Err(shape)
    _reset_walk()
    try:
        return Ok(_decode_node_value(parsed, "$"))
    except _Fail as fail:
        return Err(fail.error)
