"""The tree-op apply engine — the reducer half of "the op-stream is a journal".

``apply(op, tree)`` folds one of the 11 ``TreeOp`` cases over a decoded ``Node``
tree, returning either the new tree (:class:`~fuaran_py.result.Ok`) or a typed,
recoverable :class:`ApplyError` (:class:`ApplyErr`) — it never throws on any path,
and on any error the input tree is returned untouched (so "revert" is implicit;
wrap an op list in ``Batch`` for all-or-nothing atomicity).

This is a sibling port of the F# (``Fuaran.UI.Ops.Apply``) and TypeScript
(``@fuaran-ui/ops`` ``apply``) engines, built to match their semantics: structural
child ops (``InsertChild`` / ``RemoveNode`` / ``MoveNode`` / ``ReorderChildren``)
address layout kinds only (every layout spec carries an ordered ``children`` list);
``UpdateProp`` paths follow the WIRE_FORMAT.md §3.4 grammar — dot-separated field
segments with optional 0-based ``[i]`` list indices, traversed through the same
per-kind nested surface the sibling engines implement (grid ``Columns[i]``, chart
``YFields[i]``, tabs ``TabHeaders[i]``, form ``Fields[i]``); ``ReplaceRoot`` is the
only op that may change the root id; ``Batch`` is recursive and all-or-nothing.

The engine folds over the **generic structural model** (:mod:`fuaran_py.model`) the
codec produces — the wire is flat, so a layout's children are just the ``children``
array hoisted under the kind discriminator.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..model import Arr, Node, Obj, Value
from ..result import Ok
from ..schema.decode import (
    BADGE_VARIANT,
    BINDING_CASES,
    CELL_FORMAT_CASES,
    EMPHASIS,
    HEADING_VARIANT,
    ORIENTATION,
    TEXT_SOURCE_CASES,
    TONE,
    WEIGHT,
)

# ── Error + result surface ──────────────────────────────────────────────────

# Apply-error codes (parity with the F#/TS engines).
NODE_NOT_FOUND = "NodeNotFound"
PARENT_NOT_FOUND = "ParentNotFound"
CHILDLESS_KIND = "ChildlessKind"
POSITION_OUT_OF_RANGE = "PositionOutOfRange"
DUPLICATE_NODE_ID = "DuplicateNodeId"
FIELD_NOT_FOUND = "FieldNotFound"
SLOT_NOT_FOUND = "SlotNotFound"
KIND_MISMATCH = "KindMismatch"
PATH_INVALID = "PathInvalid"
PATH_NOT_SUPPORTED_YET = "PathNotSupportedYet"
ORDERING_MISMATCH = "OrderingMismatch"
BATCH_ABORTED = "BatchAborted"


@dataclass(frozen=True)
class ApplyError:
    """A structured, recoverable apply failure (never raised)."""

    code: str
    message: str
    batch_index: int | None = None  # inner-op index when code == BatchAborted


@dataclass(frozen=True)
class ApplyErr:
    """A failed apply carrying the :class:`ApplyError` (mirrors :class:`~fuaran_py.result.Err`)."""

    error: ApplyError

    @property
    def ok(self) -> bool:
        return False


type ApplyResult = Ok[Node] | ApplyErr


def _ok(tree: Node) -> ApplyResult:
    return Ok(tree)


def _fail(code: str, message: str, batch_index: int | None = None) -> ApplyErr:
    return ApplyErr(ApplyError(code, message, batch_index))


# ── The layout-kind set (the kinds that carry an ordered `children` array) ───

LAYOUT_KINDS = frozenset({"Box", "SplitPanel", "Tabs", "Stepper", "SummaryList", "Disclosure"})


# ── Structural node helpers (over the flat generic model) ────────────────────


def _set_kind_field(node: Node, key: str, value: Value) -> Node:
    fields = dict(node.kind.fields)
    fields[key] = value
    return Node(node.id, Obj(node.kind.tag, fields), node.extras)


def _set_extra(node: Node, key: str, value: Value) -> Node:
    extras = dict(node.extras)
    extras[key] = value
    return Node(node.id, node.kind, extras)


def _set_state_child(node: Node, key: str, child: Node) -> Node:
    state = node.extras.get("state")
    fields = dict(state.fields) if isinstance(state, Obj) else {}
    fields[key] = child
    return _set_extra(node, "state", Obj(None, fields))


def _layout_children(node: Node) -> list[Node] | None:
    """The ordered child list for a layout node, else ``None`` (childless kind)."""
    if node.kind.tag in LAYOUT_KINDS:
        children = node.kind.fields.get("children")
        if isinstance(children, Arr):
            return [c for c in children.items if isinstance(c, Node)]
    return None


def _with_layout_children(node: Node, children: list[Node]) -> Node:
    return _set_kind_field(node, "children", Arr(list(children)))


ChildSlot = tuple[Node, Callable[[Node], Node]]


def _child_slots(node: Node) -> list[ChildSlot]:
    """Every immediate sub-node position, each with a rebuild that swaps it."""
    slots: list[ChildSlot] = []
    tag = node.kind.tag
    fields = node.kind.fields

    children = _layout_children(node)
    if children is not None:
        for i, child in enumerate(children):

            def rebuild(c: Node, i: int = i, children: list[Node] = children) -> Node:
                swapped = list(children)
                swapped[i] = c
                return _with_layout_children(node, swapped)

            slots.append((child, rebuild))
    elif tag == "ErrorBoundary":
        eb_child = fields.get("child")
        eb_fallback = fields.get("fallback")
        if isinstance(eb_child, Node):
            slots.append((eb_child, lambda c: _set_kind_field(node, "child", c)))
        if isinstance(eb_fallback, Node):
            slots.append((eb_fallback, lambda c: _set_kind_field(node, "fallback", c)))
    elif tag == "Switch":
        # Phase 392: the default child + each case child are editable slots
        # (mirrors the ErrorBoundary handling above); a case's `match` is
        # preserved on rebuild.
        default_node = fields.get("default")
        if isinstance(default_node, Node):
            slots.append((default_node, lambda c: _set_kind_field(node, "default", c)))
        cases = fields.get("cases")
        if isinstance(cases, Arr):
            for i, case in enumerate(cases.items):
                if isinstance(case, Obj):
                    case_child = case.fields.get("child")
                    if isinstance(case_child, Node):

                        def rebuild_case(c: Node, i: int = i, cases: Arr = cases) -> Node:
                            new_items = list(cases.items)
                            old = new_items[i]
                            assert isinstance(old, Obj)
                            new_items[i] = Obj(old.tag, {**old.fields, "child": c})
                            return _set_kind_field(node, "cases", Arr(new_items))

                        slots.append((case_child, rebuild_case))
    elif tag == "FragmentDecl":
        body = fields.get("body")
        if isinstance(body, Node):
            slots.append((body, lambda c: _set_kind_field(node, "body", c)))

    state = node.extras.get("state")
    if isinstance(state, Obj):
        on_loading = state.fields.get("onLoading")
        if isinstance(on_loading, Node):
            slots.append((on_loading, lambda c: _set_state_child(node, "onLoading", c)))
        on_empty = state.fields.get("onEmpty")
        if isinstance(on_empty, Node):
            slots.append((on_empty, lambda c: _set_state_child(node, "onEmpty", c)))

    return slots


def _find(target: str, node: Node) -> Node | None:
    if node.id == target:
        return node
    for child, _ in _child_slots(node):
        found = _find(target, child)
        if found is not None:
            return found
    return None


def _map(target: str, f: Callable[[Node], Node], node: Node) -> Node | None:
    if node.id == target:
        return f(node)
    for child, rebuild in _child_slots(node):
        mapped = _map(target, f, child)
        if mapped is not None:
            return rebuild(mapped)
    return None


def _all_ids(node: Node) -> list[str]:
    ids = [node.id]
    for child, _ in _child_slots(node):
        ids.extend(_all_ids(child))
    return ids


def _find_layout_parent(target: str, node: Node) -> Node | None:
    children = _layout_children(node)
    if children is not None and any(c.id == target for c in children):
        return node
    for child, _ in _child_slots(node):
        found = _find_layout_parent(target, child)
        if found is not None:
            return found
    return None


def _is_ancestor(ancestor_id: str, descendant_id: str, root: Node) -> bool:
    ancestor = _find(ancestor_id, root)
    if ancestor is None:
        return False
    return any(descendant_id in _all_ids(child) for child, _ in _child_slots(ancestor))


# ── UpdateProp field-coercion surface ────────────────────────────────────────


@dataclass(frozen=True)
class _Coerced:
    ok: bool
    value: Value = None
    detail: str = ""


def _coerce_text_source(v: Value) -> _Coerced:
    if isinstance(v, str):
        return _Coerced(True, Obj("Literal", {"text": v}))
    if isinstance(v, Obj) and v.tag in TEXT_SOURCE_CASES:
        return _Coerced(True, v)
    return _Coerced(False, detail="expected a string or TextSource")


def _coerce_binding(expected: str, py_types: tuple[type, ...]) -> Callable[[Value], _Coerced]:
    def coerce(v: Value) -> _Coerced:
        if isinstance(v, bool) and bool not in py_types:
            return _Coerced(False, detail=f"expected a {expected} or Binding")
        if isinstance(v, py_types):
            return _Coerced(True, Obj("Static", {"value": v}))
        if isinstance(v, Obj) and v.tag in BINDING_CASES:
            return _Coerced(True, v)
        return _Coerced(False, detail=f"expected a {expected} or Binding")

    return coerce


_coerce_binding_number = _coerce_binding("number", (int, float))
_coerce_binding_string = _coerce_binding("string", (str,))
_coerce_binding_int = _coerce_binding("integer", (int,))
_coerce_binding_bool = _coerce_binding("boolean", (bool,))


def _coerce_cell_format(v: Value) -> _Coerced:
    if isinstance(v, Obj) and v.tag in CELL_FORMAT_CASES:
        return _Coerced(True, v)
    return _Coerced(False, detail="expected a CellFormat object")


def _coerce_enum(allowed: frozenset[str]) -> Callable[[Value], _Coerced]:
    def coerce(v: Value) -> _Coerced:
        if isinstance(v, str) and v in allowed:
            return _Coerced(True, v)
        return _Coerced(False, detail="one of: " + ", ".join(sorted(allowed)))

    return coerce


def _coerce_int(v: Value) -> _Coerced:
    if isinstance(v, bool) or not isinstance(v, int):
        return _Coerced(False, detail="expected an integer")
    return _Coerced(True, v)


def _coerce_float(v: Value) -> _Coerced:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return _Coerced(False, detail="expected a number")
    return _Coerced(True, float(v))


def _coerce_bool(v: Value) -> _Coerced:
    if not isinstance(v, bool):
        return _Coerced(False, detail="expected a boolean")
    return _Coerced(True, v)


def _coerce_string(v: Value) -> _Coerced:
    if not isinstance(v, str):
        return _Coerced(False, detail="expected a string")
    return _Coerced(True, v)


_Coercer = Callable[[Value], _Coerced]
_NOT_SUPPORTED = "__not_supported__"

# (PascalCase op path) -> (camelCase wire key, coercer | _NOT_SUPPORTED) per kind.
# Children entries map to _NOT_SUPPORTED (use the structural child ops instead);
# a kind absent from this table has no field-level surface (PathNotSupportedYet).
_FIELDS: dict[str, dict[str, tuple[str, _Coercer | str]]] = {
    # Display
    "Metric": {
        "Label": ("label", _coerce_text_source),
        "Source": ("source", _coerce_binding_number),
        "Format": ("format", _coerce_cell_format),
        "Tone": ("tone", _coerce_enum(TONE)),
        "Weight": ("weight", _coerce_enum(WEIGHT)),
        "Emphasis": ("emphasis", _coerce_enum(EMPHASIS)),
        "Trend": ("trend", _coerce_binding_number),
        "TrendFormat": ("trendFormat", _coerce_cell_format),
        "Icon": ("icon", _coerce_string),
        "Subtext": ("subtext", _coerce_text_source),
    },
    "Heading": {
        "Level": ("level", _coerce_int),
        "Text": ("text", _coerce_text_source),
        "Variant": ("variant", _coerce_enum(HEADING_VARIANT)),
    },
    "Markdown": {"Text": ("text", _coerce_text_source)},
    "Badge": {
        "Label": ("label", _coerce_text_source),
        "Variant": ("variant", _coerce_enum(BADGE_VARIANT)),
    },
    "Skeleton": {"Rows": ("rows", _coerce_int)},
    "Callout": {
        "Tone": ("tone", _coerce_enum(TONE)),
        "Body": ("body", _coerce_text_source),
        "Dismissable": ("dismissable", _coerce_bool),
        "Heading": ("heading", _coerce_text_source),
        "Icon": ("icon", _coerce_string),
    },
    "Progress": {
        "Fraction": ("fraction", _coerce_binding_number),
        "Indeterminate": ("indeterminate", _coerce_bool),
        "Tone": ("tone", _coerce_enum(TONE)),
        "Label": ("label", _coerce_text_source),
        "Caveat": ("caveat", _coerce_text_source),
    },
    "LabelValueRow": {
        "Label": ("label", _coerce_text_source),
        "Source": ("source", _coerce_binding_number),
        "Format": ("format", _coerce_cell_format),
        "Emphasis": ("emphasis", _coerce_bool),
        "Help": ("help", _coerce_text_source),
    },
    "Link": {
        "Href": ("href", _coerce_binding_string),
        "Label": ("label", _coerce_text_source),
        "Rel": ("rel", _coerce_string),
        "Target": ("target", _coerce_string),
        "Download": ("download", _coerce_bool),
    },
    # Layout (Children -> structural child ops)
    # Box (Phase 390) is NOT in this flat table — its legacy-compat field surface
    # (Orientation / Wrap / Cols / TemplateColumns / Heading) mutates the *nested*
    # `layout` object (or top-level `heading`) and is layout-mode-sensitive, so it
    # is handled by `_update_box` in `_update_field`.
    "SplitPanel": {
        "Weight": ("weight", _coerce_float),
        "Children": ("children", _NOT_SUPPORTED),
    },
    "Tabs": {
        "Orientation": ("orientation", _coerce_enum(ORIENTATION)),
        "Children": ("children", _NOT_SUPPORTED),
    },
    "Stepper": {
        "ActiveStep": ("activeStep", _coerce_binding_int),
        "Children": ("children", _NOT_SUPPORTED),
    },
    "SummaryList": {
        "Heading": ("heading", _coerce_text_source),
        "Children": ("children", _NOT_SUPPORTED),
    },
    "Disclosure": {
        "Heading": ("heading", _coerce_text_source),
        "Open": ("open", _coerce_binding_bool),
        "DefaultOpen": ("defaultOpen", _coerce_bool),
        "Children": ("children", _NOT_SUPPORTED),
    },
    # Structural
    "FragmentDecl": {"Name": ("name", _coerce_string)},
    "FragmentRef": {"Name": ("name", _coerce_string)},
}


@dataclass(frozen=True)
class _Outcome:
    tag: str  # "updated" | "unknownField" | "notSupported" | "typeMismatch"
    kind: Obj | None = None
    detail: str = ""


def _update_box(field: str, value: Value, kind: Obj) -> _Outcome:
    """Box (Phase 390) UpdateProp — mirrors F# ``updateBox``.

    The retired kinds' field names stay addressable so pre-merge op-streams
    replaying against an upgraded Box keep working: ``Orientation`` / ``Wrap``
    mutate a Flex box's nested layout; ``Cols`` / ``TemplateColumns`` mutate a
    Grid box's; ``Heading`` sets the top-level heading. A field that does not
    match the box's current layout mode is UnknownField.
    """
    layout = kind.fields.get("layout")
    layout_obj = layout if isinstance(layout, Obj) else None
    mode = layout_obj.tag if layout_obj is not None else None

    def with_layout_field(camel: str, coerced_value: Value) -> _Outcome:
        assert layout_obj is not None
        lf = dict(layout_obj.fields)
        lf[camel] = coerced_value
        fields = dict(kind.fields)
        fields["layout"] = Obj(layout_obj.tag, lf)
        return _Outcome("updated", Obj(kind.tag, fields))

    if field == "Heading":
        result = _coerce_text_source(value)
        if not result.ok:
            return _Outcome("typeMismatch", detail=result.detail)
        fields = dict(kind.fields)
        fields["heading"] = result.value
        return _Outcome("updated", Obj(kind.tag, fields))
    if field == "Orientation" and mode == "Flex":
        result = _coerce_enum(ORIENTATION)(value)
        return (
            with_layout_field("direction", result.value)
            if result.ok
            else _Outcome("typeMismatch", detail=result.detail)
        )
    if field == "Wrap" and mode == "Flex":
        result = _coerce_bool(value)
        return with_layout_field("wrap", result.value) if result.ok else _Outcome("typeMismatch", detail=result.detail)
    if field == "Cols" and mode == "Grid":
        result = _coerce_int(value)
        return with_layout_field("cols", result.value) if result.ok else _Outcome("typeMismatch", detail=result.detail)
    if field == "TemplateColumns" and mode == "Grid":
        result = _coerce_string(value)
        return (
            with_layout_field("templateColumns", result.value)
            if result.ok
            else _Outcome("typeMismatch", detail=result.detail)
        )
    if field == "Children":
        return _Outcome("notSupported")
    return _Outcome("unknownField")


def _update_field(field: str, value: Value, kind: Obj) -> _Outcome:
    if kind.tag == "Box":
        return _update_box(field, value, kind)
    table = _FIELDS.get(kind.tag or "")
    if table is None:
        return _Outcome("notSupported")
    entry = table.get(field)
    if entry is None:
        return _Outcome("unknownField")
    camel, coercer = entry
    if coercer == _NOT_SUPPORTED:
        return _Outcome("notSupported")
    assert callable(coercer)
    result = coercer(value)
    if not result.ok:
        return _Outcome("typeMismatch", detail=result.detail)
    fields = dict(kind.fields)
    fields[camel] = result.value
    return _Outcome("updated", Obj(kind.tag, fields))


# ── UpdateProp path parser (WIRE_FORMAT.md §3.4 grammar — Phase-364 parity) ──
#
#   path     := segment ( "." segment )*
#   segment  := field ( "[" index "]" )?
#   field    := [A-Za-z_][A-Za-z0-9_]*
#   index    := "0" | [1-9][0-9]*
#
# Hand-rolled (no regex) to mirror the F#/TS reference parsers exactly;
# character classes are strict ASCII per the grammar.


@dataclass(frozen=True)
class _PathSeg:
    field: str
    index: int | None = None


def _is_field_start(c: str) -> bool:
    return "A" <= c <= "Z" or "a" <= c <= "z" or c == "_"


def _is_field_char(c: str) -> bool:
    return _is_field_start(c) or "0" <= c <= "9"


def _parse_segment(raw: str) -> _PathSeg | str:
    """Parse one path segment; returns the segment or an error-reason string."""
    if raw == "":
        return "empty segment"
    bracket = raw.find("[")
    field_part = raw if bracket < 0 else raw[:bracket]
    if field_part == "" or not _is_field_start(field_part[0]) or not all(_is_field_char(c) for c in field_part):
        return f"segment '{raw}' is not a field name"
    if bracket < 0:
        return _PathSeg(field_part)
    index_part = raw[bracket:]
    if len(index_part) < 3 or index_part[-1] != "]":
        return f"malformed index in segment '{raw}'"
    digits = index_part[1:-1]
    if digits == "" or not all("0" <= c <= "9" for c in digits):
        return f"index in segment '{raw}' must be a non-negative decimal integer"
    if len(digits) > 1 and digits[0] == "0":
        return f"index in segment '{raw}' has a leading zero"
    return _PathSeg(field_part, int(digits))


def _parse_path(path: str) -> list[_PathSeg] | str:
    """Parse a full path; returns the segments or an error-reason string."""
    if path.strip() == "":
        return "empty path"
    segs: list[_PathSeg] = []
    for raw in path.split("."):
        seg = _parse_segment(raw)
        if isinstance(seg, str):
            return seg
        segs.append(seg)
    return segs


# ── UpdateProp nested surface (Phase-364 parity) ─────────────────────────────
#
# Mirrors the F#/TS nested legs: grid ``Columns[i].{Label,Format,Width}``,
# chart ``YFields[i]`` (indexed scalar leaf), tabs
# ``TabHeaders[i].{Label,Icon,Disabled}``, form
# ``Fields[i].{Label,Required,Help}``. Closure-bearing sub-fields
# (``Value`` / ``Kind`` / ``Id``) are never addressable.

_COLUMN_WIDTH_CASES = frozenset({"Auto", "Fixed", "Flex"})


def _coerce_column_width(v: Value) -> _Coerced:
    if isinstance(v, Obj) and v.tag in _COLUMN_WIDTH_CASES:
        return _Coerced(True, v)
    return _Coerced(False, detail="expected a ColumnWidth object (Auto | Fixed | Flex)")


@dataclass(frozen=True)
class _NestedOutcome:
    tag: str  # "updated" | "fieldNotFound" | "missingIndex" | "indexOutOfRange" | "notSupported" | "typeMismatch"
    kind: Obj | None = None
    detail: str = ""
    list_field: str = ""
    count: int = 0
    requested: int = 0
    segment: str = ""
    available: tuple[str, ...] = ()


# (PascalCase list field) per kind tag -> (camelCase wire key,
#   { PascalCase leaf: (camelCase wire key, coercer) }, closure-bearing leaves).
_NESTED_RECORD_LISTS: dict[str, dict[str, tuple[str, dict[str, tuple[str, _Coercer]], frozenset[str]]]] = {
    "DataGrid": {
        "Columns": (
            "columns",
            {
                "Label": ("label", _coerce_string),
                "Format": ("format", _coerce_cell_format),
                "Width": ("width", _coerce_column_width),
            },
            frozenset({"Value", "Kind"}),
        )
    },
    "Tabs": {
        "TabHeaders": (
            "tabHeaders",
            {
                "Label": ("label", _coerce_text_source),
                "Icon": ("icon", _coerce_string),
                "Disabled": ("disabled", _coerce_binding_bool),
            },
            frozenset(),
        )
    },
    "Form": {
        "Fields": (
            "fields",
            {
                "Label": ("label", _coerce_text_source),
                "Required": ("required", _coerce_bool),
                "Help": ("help", _coerce_text_source),
            },
            frozenset({"Id", "Kind"}),
        )
    },
}


def _update_nested(segs: list[_PathSeg], value: Value, kind: Obj) -> _NestedOutcome:
    head, rest = segs[0], segs[1:]

    # Chart YFields — the indexed scalar-leaf case (the element IS the string).
    if kind.tag == "Chart":
        if head.field != "YFields":
            return _NestedOutcome("fieldNotFound", segment=head.field, available=("YFields",))
        y = kind.fields.get("yFields")
        items = list(y.items) if isinstance(y, Arr) else []
        if head.index is None:
            return _NestedOutcome("missingIndex", list_field="YFields", count=len(items))
        if head.index >= len(items):
            return _NestedOutcome("indexOutOfRange", list_field="YFields", count=len(items), requested=head.index)
        if rest:
            return _NestedOutcome("notSupported")
        coerced = _coerce_string(value)
        if not coerced.ok:
            return _NestedOutcome("typeMismatch", detail=coerced.detail)
        items[head.index] = coerced.value
        fields = dict(kind.fields)
        fields["yFields"] = Arr(items)
        return _NestedOutcome("updated", Obj(kind.tag, fields))

    table = _NESTED_RECORD_LISTS.get(kind.tag or "")
    if table is None:
        return _NestedOutcome("notSupported")
    entry = table.get(head.field)
    if entry is None:
        return _NestedOutcome("fieldNotFound", segment=head.field, available=tuple(table))
    wire_key, leaves, closure_leaves = entry
    raw_list = kind.fields.get(wire_key)
    # An absent optional list (e.g. tabHeaders) addresses like an empty one.
    items = list(raw_list.items) if isinstance(raw_list, Arr) else []
    if head.index is None:
        return _NestedOutcome("missingIndex", list_field=head.field, count=len(items))
    if head.index >= len(items):
        return _NestedOutcome("indexOutOfRange", list_field=head.field, count=len(items), requested=head.index)
    leaf = rest[0].field if len(rest) == 1 and rest[0].index is None else None
    if leaf is None:
        return _NestedOutcome("notSupported")
    if leaf in closure_leaves:
        return _NestedOutcome("notSupported")
    leaf_entry = leaves.get(leaf)
    if leaf_entry is None:
        return _NestedOutcome("fieldNotFound", segment=leaf, available=tuple(leaves))
    leaf_key, coercer = leaf_entry
    coerced = coercer(value)
    if not coerced.ok:
        return _NestedOutcome("typeMismatch", detail=coerced.detail)
    element = items[head.index]
    if not isinstance(element, Obj):
        return _NestedOutcome("notSupported")
    element_fields = dict(element.fields)
    element_fields[leaf_key] = coerced.value
    items[head.index] = Obj(element.tag, element_fields)
    fields = dict(kind.fields)
    fields[wire_key] = Arr(items)
    return _NestedOutcome("updated", Obj(kind.tag, fields))


# ── ReplaceBinding slot surface ──────────────────────────────────────────────

# (kind tag, op slot) -> camelCase binding-bearing wire key.
_BINDING_SLOTS: dict[tuple[str, str], str] = {
    ("Metric", "Source"): "source",
    ("Metric", "Trend"): "trend",
    ("Sparkline", "Source"): "source",
    ("Progress", "Fraction"): "fraction",
    ("LabelValueRow", "Source"): "source",
    ("Stepper", "ActiveStep"): "activeStep",
    ("Button", "Disabled"): "disabled",
    ("Select", "Disabled"): "disabled",
    ("Form", "Disabled"): "disabled",
    ("FileUpload", "Disabled"): "disabled",
    ("DataGrid", "Source"): "source",
    ("Chart", "Source"): "source",
    ("Map", "Source"): "source",
}


def _replace_binding(slot: str, binding: Value, kind: Obj) -> Obj | None:
    camel = _BINDING_SLOTS.get((kind.tag or "", slot))
    if camel is None:
        return None
    fields = dict(kind.fields)
    fields[camel] = binding
    return Obj(kind.tag, fields)


# ── Single-op apply ──────────────────────────────────────────────────────────


def _apply_one(op: Obj, root: Node) -> ApplyResult:
    tag = op.tag
    fields = op.fields

    if tag == "EditNode":
        new_kind = fields["newKind"]
        target = _as_str(fields["target"])
        tree = _map(target, lambda n: Node(n.id, _as_obj(new_kind), n.extras), root)
        if tree is None:
            return _fail(NODE_NOT_FOUND, f"Node '{target}' not found in tree.")
        return _ok(tree)

    if tag == "UpdateProp":
        path = _as_str(fields["path"])
        target = _as_str(fields["target"])
        value = fields["value"]
        segs = _parse_path(path)
        if isinstance(segs, str):
            return _fail(PATH_INVALID, f"Path '{path}' is structurally invalid: {segs}.")
        node = _find(target, root)
        if node is None:
            return _fail(NODE_NOT_FOUND, f"Node '{target}' not found in tree.")

        def _finish(nk: Obj) -> ApplyResult:
            def _retag(n: Node) -> Node:
                return Node(n.id, nk, n.extras)

            tree = _map(target, _retag, root)
            assert tree is not None
            return _ok(tree)

        if len(segs) == 1 and segs[0].index is None:
            # Top-level path — the original per-kind field dispatch.
            outcome = _update_field(path, value, node.kind)
            if outcome.tag == "updated":
                assert outcome.kind is not None
                return _finish(outcome.kind)
            if outcome.tag == "unknownField":
                return _fail(FIELD_NOT_FOUND, f"Field '{path}' not found on node '{target}'.")
            if outcome.tag == "notSupported":
                return _fail(
                    PATH_NOT_SUPPORTED_YET,
                    f"Path '{path}' on node '{target}' is not yet supported by the apply engine.",
                )
            return _fail(
                KIND_MISMATCH,
                f"UpdateProp value for '{path}' on '{target}' does not match the field's "
                f"expected type: {outcome.detail}",
            )

        # Nested path (WIRE_FORMAT.md §3.4) — the per-kind typed traversal.
        nested = _update_nested(segs, value, node.kind)
        if nested.tag == "updated":
            assert nested.kind is not None
            return _finish(nested.kind)
        if nested.tag == "missingIndex":
            return _fail(
                PATH_INVALID,
                f"Field '{nested.list_field}' on node '{target}' is a list — address an element with a "
                f"0-based index (the list has {nested.count} element(s)).",
            )
        if nested.tag == "indexOutOfRange":
            bounds = "the list is empty" if nested.count == 0 else f"valid: 0..{nested.count - 1}"
            return _fail(
                POSITION_OUT_OF_RANGE,
                f"Index {nested.requested} is out of range for '{nested.list_field}' on node '{target}' ({bounds}).",
            )
        if nested.tag == "fieldNotFound":
            return _fail(
                FIELD_NOT_FOUND,
                f"Field '{nested.segment}' (in path '{path}') not found on node '{target}'. "
                f"Available at this segment: {', '.join(nested.available)}.",
            )
        if nested.tag == "notSupported":
            return _fail(
                PATH_NOT_SUPPORTED_YET,
                f"Path '{path}' on node '{target}' is not yet supported by the apply engine.",
            )
        return _fail(
            KIND_MISMATCH,
            f"UpdateProp value for '{path}' on '{target}' does not match the field's expected type: {nested.detail}",
        )

    if tag == "ReplaceBinding":
        target = _as_str(fields["target"])
        slot = _as_str(fields["slot"])
        node = _find(target, root)
        if node is None:
            return _fail(NODE_NOT_FOUND, f"Node '{target}' not found in tree.")
        new_kind = _replace_binding(slot, fields["binding"], node.kind)
        if new_kind is None:
            return _fail(SLOT_NOT_FOUND, f"Binding slot '{slot}' not found on node '{target}'.")
        tree = _map(target, lambda n: Node(n.id, new_kind, n.extras), root)
        assert tree is not None
        return _ok(tree)

    if tag == "UpdateStyle":
        target = _as_str(fields["target"])
        style = fields["style"]
        tree = _map(target, lambda n: _set_extra(n, "style", style), root)
        if tree is None:
            return _fail(NODE_NOT_FOUND, f"Node '{target}' not found in tree.")
        return _ok(tree)

    if tag == "UpdateState":
        target = _as_str(fields["target"])
        state = fields["state"]
        tree = _map(target, lambda n: _set_extra(n, "state", state), root)
        if tree is None:
            return _fail(NODE_NOT_FOUND, f"Node '{target}' not found in tree.")
        return _ok(tree)

    if tag == "InsertChild":
        parent_id = _as_str(fields["parentId"])
        position = _as_int(fields["position"])
        child = _as_node(fields["child"])
        parent = _find(parent_id, root)
        if parent is None:
            return _fail(PARENT_NOT_FOUND, f"Parent node '{parent_id}' not found in tree.")
        children = _layout_children(parent)
        if children is None:
            return _fail(
                CHILDLESS_KIND,
                f"Node '{parent_id}' (kind={parent.kind.tag}) has no children field — "
                "only layout kinds accept structural child ops.",
            )
        if position < 0 or position > len(children):
            return _fail(
                POSITION_OUT_OF_RANGE,
                f"Position {position} is out of range for parent '{parent_id}' (valid: 0..{len(children)}).",
            )
        existing = set(_all_ids(root))
        duplicate = next((cid for cid in _all_ids(child) if cid in existing), None)
        if duplicate is not None:
            return _fail(DUPLICATE_NODE_ID, f"NodeId '{duplicate}' is already present in the tree; ids must be unique.")
        new_children = children[:position] + [child] + children[position:]
        tree = _map(parent_id, lambda n: _with_layout_children(n, new_children), root)
        assert tree is not None
        return _ok(tree)

    if tag == "RemoveNode":
        target = _as_str(fields["target"])
        if root.id == target:
            return _fail(KIND_MISMATCH, "Cannot RemoveNode the root.")
        parent = _find_layout_parent(target, root)
        if parent is None:
            return _fail(NODE_NOT_FOUND, f"Node '{target}' not found in tree.")
        kept = [c for c in (_layout_children(parent) or []) if c.id != target]
        tree = _map(parent.id, lambda n: _with_layout_children(n, kept), root)
        assert tree is not None
        return _ok(tree)

    if tag == "MoveNode":
        target = _as_str(fields["target"])
        new_parent_id = _as_str(fields["newParentId"])
        new_position = _as_int(fields["newPosition"])
        if target == new_parent_id:
            return _fail(KIND_MISMATCH, "Cannot move a node into itself.")
        if _is_ancestor(target, new_parent_id, root):
            return _fail(KIND_MISMATCH, "Cannot move a node into its own descendant (would create a cycle).")
        moving = _find(target, root)
        if moving is None:
            return _fail(NODE_NOT_FOUND, f"Node '{target}' not found in tree.")
        new_parent = _find(new_parent_id, root)
        if new_parent is None:
            return _fail(PARENT_NOT_FOUND, f"Parent node '{new_parent_id}' not found in tree.")
        if _layout_children(new_parent) is None:
            return _fail(CHILDLESS_KIND, f"Node '{new_parent_id}' (kind={new_parent.kind.tag}) has no children field.")
        after_remove = _apply_one(Obj("RemoveNode", {"target": target}), root)
        if not after_remove.ok:
            return after_remove
        assert isinstance(after_remove, Ok)
        inserted = _apply_one(
            Obj("InsertChild", {"child": moving, "parentId": new_parent_id, "position": new_position}),
            after_remove.value,
        )
        return inserted

    if tag == "ReorderChildren":
        parent_id = _as_str(fields["parentId"])
        new_order = [_as_str(x) for x in _as_arr(fields["newOrder"])]
        parent = _find(parent_id, root)
        if parent is None:
            return _fail(PARENT_NOT_FOUND, f"Parent node '{parent_id}' not found in tree.")
        children = _layout_children(parent)
        if children is None:
            return _fail(CHILDLESS_KIND, f"Node '{parent_id}' (kind={parent.kind.tag}) has no children field.")
        if sorted(c.id for c in children) != sorted(new_order):
            return _fail(
                ORDERING_MISMATCH,
                f"ReorderChildren for '{parent_id}' did not list exactly the current child ids.",
            )
        by_id = {c.id: c for c in children}
        reordered = [by_id[cid] for cid in new_order]
        tree = _map(parent_id, lambda n: _with_layout_children(n, reordered), root)
        assert tree is not None
        return _ok(tree)

    if tag == "ReplaceRoot":
        # The whole-tree swap: the only op that legally changes the root node id.
        return _ok(_as_node(fields["node"]))

    if tag == "Batch":
        state = root
        for i, inner in enumerate(_as_arr(fields["ops"])):
            result = _apply_one(_as_obj(inner), state)
            if not result.ok:
                assert isinstance(result, ApplyErr)
                # All-or-nothing: discard partial state, surface the inner failure.
                return _fail(BATCH_ABORTED, f"Batch aborted at inner op #{i}: {result.error.message}", i)
            assert isinstance(result, Ok)
            state = result.value
        return _ok(state)

    return _fail(KIND_MISMATCH, f"unrecognised op kind '{tag}'")


# ── Narrowing helpers (the decoded op fields are typed `Value`) ──────────────


def _as_str(v: Value) -> str:
    assert isinstance(v, str)
    return v


def _as_int(v: Value) -> int:
    assert isinstance(v, int) and not isinstance(v, bool)
    return v


def _as_obj(v: Value) -> Obj:
    assert isinstance(v, Obj)
    return v


def _as_node(v: Value) -> Node:
    assert isinstance(v, Node)
    return v


def _as_arr(v: Value) -> list[Value]:
    assert isinstance(v, Arr)
    return v.items


# ── Public entry ─────────────────────────────────────────────────────────────


def apply(op: Obj, tree: Node) -> ApplyResult:
    """Apply a single decoded ``TreeOp`` to ``tree``.

    Returns :class:`~fuaran_py.result.Ok` carrying the new tree, or
    :class:`ApplyErr` carrying a typed :class:`ApplyError`. Never throws; on any
    error the original ``tree`` is left untouched (revert is implicit). Fold across
    an op list to apply many, or wrap them in a ``Batch`` op for atomicity.
    """
    return _apply_one(op, tree)
