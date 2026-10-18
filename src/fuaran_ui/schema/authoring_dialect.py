"""The authoring surface's declared divergences from the generated wire layer (Phase 2316).

The wire layer is GENERATED from ``idl.json`` (:mod:`fuaran_ui.schema.generated`, Phase 2296). The
authoring dataclasses in :mod:`fuaran_ui.schema.types` (and the transform binding in
:mod:`fuaran_ui.ui.compute`) stay hand-written, because they answer a different question: what a
Python author writes, not what the wire carries. They carry authoring defaults the wire does not,
``snake_case`` names, flags for erased closures, and a few records authored some other way.

That split is safe only while it is DECLARED. This module is the declaration: every place the
authoring surface deliberately differs from the generated field tables, with the reason. The
coverage pin (``tests/test_authoring_coverage.py``) holds the two layers to it in both directions:

* every generated field of every kind, record, union case and the node envelope is either a
  parameter of its authoring class (by the naming rule below, or :data:`RENAMED`) or a
  :data:`GAPS` entry, so a field the IDL gains turns the suite red BY NAME until someone decides
  how to author it;
* every wire-required field whose authoring parameter has a default is an
  :data:`AUTHORING_DEFAULTS` entry, and every entry is still such a field;
* every gap still IS a gap: authoring a field retires its declaration, or the pin goes red.

A declaration naming an owner or a field the IDL no longer has is REFUSED here, at import
(:class:`StaleDeclaration`), before any test reads it.

**Owners** are keyed ``kind:<Kind>``, ``record:<Record>``, ``case:<Union>.<Case>`` and ``node``.
**The naming rule:** a wire member ``fooBar`` is the parameter ``foo_bar``; a member that is a Python
keyword takes a trailing underscore (``from`` is ``from_``). **Class resolution:** a kind or record
is the same-named class in :mod:`fuaran_ui.schema.types`; a union case is the member of the host's
union alias whose ``to_wire`` writes that case's tag; :data:`CLASSES` names every exception and
:data:`UNCLASSED` every owner that has no authoring class at all.
"""

from __future__ import annotations

from collections.abc import Mapping

from . import generated as _g

__all__ = [
    "AUTHORING_DEFAULTS",
    "CLASSES",
    "GAPS",
    "RENAMED",
    "StaleDeclaration",
    "UNCLASSED",
    "owners",
    "stale_declarations",
]


class StaleDeclaration(ValueError):
    """A declaration names an owner or a field the generated vocabulary no longer has."""


def owners() -> dict[str, tuple[_g.F, ...]]:
    """Every generated field table, keyed by owner (see the module docstring)."""
    out: dict[str, tuple[_g.F, ...]] = {}
    out.update({f"kind:{name}": fields for name, fields in _g.KIND_FIELDS.items()})
    out.update({f"record:{name}": fields for name, fields in _g.RECORD_FIELDS.items()})
    for union, cases in _g.UNION_CASE_FIELDS.items():
        out.update({f"case:{union}.{case}": fields for case, fields in cases.items()})
    out["node"] = _g.NODE_FIELDS
    return out


# ── Reasons, shared where one reason covers many members ─────────────────────────────────────

_HOST_ONLY = "hostOnly: the member carries nothing observable on the wire"
_ROW_FN = (
    "a (row) -> ... closure of the cell kind; it erases on the wire, so the cell is authored by its "
    "discriminator alone (ColumnKind(kind))"
)
_EMPTY = "an empty list is authored by leaving it out; the wire spells it"
_FALSE = "False is authored by leaving it out; the wire spells it"
_IDENTITY_STYLE = "the all-default style record is authored by leaving it out; the wire spells it"
_COMMON_CASE = "this surface's default is the commonest case; the wire carries it explicitly"
_START_VALUE = "a conventional starting value an author overrides; the wire carries it explicitly"
_ERASED_CELL_BODY = (
    "absent keeps the bare discriminator every release before Phase 2316 encoded; a cell kind's "
    "body is admitted without it on decode, as for every erased cell"
)
_REFUSED_IF_ABSENT = (
    "not a default: None is refused at construction; it exists so earlier positional members keep their positions"
)
_WIDTH_MEMBER = (
    "not a default: one ColumnWidth record authors all three cases, so the member is None on the cases "
    "that lack it, and None is refused at construction on the case that requires it"
)


#: Owners whose authoring class is not found by the default resolution, as ``module:attribute``.
CLASSES: Mapping[str, str] = {
    "node": "fuaran_ui.schema.types:UiNode",
    "record:ColumnErased": "fuaran_ui.schema.types:Column",
    "record:StaticRows": "fuaran_ui.schema.types:Table",
    "record:TransformParam": "fuaran_ui.ui.compute:ParamDecl",
    "case:TextSource.Literal": "fuaran_ui.schema.types:LiteralText",
    # The cell-kind union's members write their tag at run time (``Obj(self.kind, …)``), so no
    # case resolves by its literal tag; every case but the toned pill is the one ColumnKind record.
    **{
        f"case:CellKindErased.{case}": "fuaran_ui.schema.types:ColumnKind"
        for case in _g.UNION_CASES["CellKindErased"]
        if case != "TonedPill"
    },
    "case:CellKindErased.TonedPill": "fuaran_ui.schema.types:TonedPillColumnKind",
    **{f"case:ColumnWidth.{case}": "fuaran_ui.schema.types:ColumnWidth" for case in _g.UNION_CASES["ColumnWidth"]},
}

#: Owners with no authoring class, and why. Their fields need no further declaration.
UNCLASSED: Mapping[str, str] = {
    "record:DateTimeRangePair": "authored as a (from, to) tuple of ISO strings in DateTimeRangeField.value",
    "record:RangePair": "authored as a (min, max) tuple in RangeField.value",
    "record:StyleDelta": "a PatchStyle op payload, written by the tree diff (fuaran_ui.ops.diff), never authored",
    "case:Binding.Computed": "a closure-valued binding; it erases on the wire and has no authoring spelling",
    "case:Binding.Expr": "declined by fuaran#1580: the predicate binding is not modelled by this host",
    "case:Binding.I18n": "authored as the TextSource.I18n case (I18n); a Binding-typed slot does not admit it yet",
    "case:CellFormat.Custom": "a closure-valued cell format; it erases on the wire and has no authoring spelling",
}

#: Wire members authored under a parameter name the naming rule does not produce.
RENAMED: Mapping[str, Mapping[str, str]] = {
    "case:Action.Chain": {"ops": "actions"},
    "record:ColumnErased": {"field": "field_name"},
    "record:TransformParam": {"from": "source"},
}

#: Wire-REQUIRED members whose authoring parameter carries a default, and why.
AUTHORING_DEFAULTS: Mapping[str, Mapping[str, str]] = {
    # Phase 2300 — fifteen members left this table when the IDL declared their default
    # (`omitDefault`): a wire-optional member's authoring default is the ordinary case and
    # needs no declaration here.
    "kind:Box": {"children": _EMPTY, "layout": "a vertical FlexLayout(), the commonest stack", "role": _COMMON_CASE},
    "kind:Button": {"onClick": "the no-op Chain() action"},
    "kind:Chart": {"kind": _COMMON_CASE},
    "kind:Custom": {"props": "an empty props map"},
    "kind:DataGrid": {"columns": _EMPTY},
    "kind:Disclosure": {
        "children": _EMPTY,
        "defaultOpen": _FALSE,
        "heading": "an empty literal heading",
        "open": "a closed Static(False) binding",
    },
    "kind:Drawing": {"shapes": _EMPTY, "style": _IDENTITY_STYLE},
    "kind:FileUpload": {"accept": _EMPTY},
    "kind:Filters": {"items": _EMPTY},
    "kind:Form": {"fields": _EMPTY, "onSubmit": "the no-op Chain() action", "submitLabel": "a literal 'Submit'"},
    "kind:Heading": {"level": _START_VALUE},
    "kind:List": {"items": _EMPTY},
    "kind:Map": {"centreLatitude": _START_VALUE, "centreLongitude": _START_VALUE, "zoom": _START_VALUE},
    "kind:Math": {"display": _COMMON_CASE},
    "kind:Media": {"kind": "a Video that does not autoplay"},
    "kind:Modal": {"children": _EMPTY, "open": "a closed Static(False) binding"},
    "kind:Mount": {"capabilities": _EMPTY, "channel": "a GuestChannel with its own defaults"},
    "kind:ScrollArea": {"children": _EMPTY, "orientation": _COMMON_CASE},
    "kind:SplitPanel": {"children": _EMPTY, "weight": _START_VALUE},
    "kind:Stepper": {"children": _EMPTY},
    "kind:SummaryList": {"children": _EMPTY},
    "kind:Tabs": {"children": _EMPTY},
    "kind:Toast": {"open": "a closed Static(False) binding"},
    "kind:Tree": {"items": _EMPTY},
    "record:ColumnErased": {"kind": "a Text cell, the commonest case; the wire carries it explicitly"},
    "record:GuestChannel": {"direction": _COMMON_CASE},
    "case:Action.Chain": {"ops": "the empty chain, the no-op action"},
    "case:Action.Invoke": {"args": _EMPTY},
    "case:Action.ReadFileBody": {"encoding": _COMMON_CASE},
    "case:Binding.Invoke": {"args": _EMPTY},
    "case:BoxLayout.Flex": {"direction": _COMMON_CASE},
    "case:BoxLayout.Grid": {"cols": _START_VALUE},
    "case:BoxLayout.Masonry": {"cols": _START_VALUE},
    "case:CellKindErased.Button": {"label": _ERASED_CELL_BODY},
    "case:CellKindErased.ButtonGroup": {"buttons": _ERASED_CELL_BODY},
    "case:ColumnWidth.Fixed": {"pixels": _WIDTH_MEMBER},
    "case:ColumnWidth.Flex": {"weight": _WIDTH_MEMBER},
    "case:FormFieldKind.TextArea": {"rows": _REFUSED_IF_ABSENT},
    "case:FormFieldKind.DateTime": {"variant": _COMMON_CASE},
    "case:FormFieldKind.DateTimeRange": {"variant": _COMMON_CASE},
    "case:Shape.Group": {"children": _EMPTY, "style": _IDENTITY_STYLE},
    "case:Shape.Rectangle": {"style": _IDENTITY_STYLE},
    "case:Shape.Line": {"style": _IDENTITY_STYLE},
    "case:Shape.Polyline": {"points": _EMPTY, "style": _IDENTITY_STYLE},
    "case:Shape.Polygon": {"points": _EMPTY, "style": _IDENTITY_STYLE},
    "case:Shape.Curve": {"commands": _EMPTY, "style": _IDENTITY_STYLE},
    "case:Shape.Circle": {"style": _IDENTITY_STYLE},
    "case:Shape.Ellipse": {"style": _IDENTITY_STYLE},
    "case:Shape.Label": {"style": _IDENTITY_STYLE},
    "case:TextSource.I18n": {"args": "an empty argument map"},
}

#: Wire members that are not a parameter of their authoring class, and why.
GAPS: Mapping[str, Mapping[str, str]] = {
    "kind:Button": {"tooltip": _HOST_ONLY},
    "kind:DataGrid": {
        "rowKey": "the erased row-key closure: emitted as <closure> unless row_key_field names the key",
        "staticRows": "authored through the Table record, which lowers to a DataGrid carrying staticRows",
    },
    "node": {"extraAttributes": _HOST_ONLY, "motion": _HOST_ONLY},
    "case:Action.ReadFileBody": {"fileHandle": _HOST_ONLY},
    "case:Binding.Query": {"accessor": _HOST_ONLY},
    "case:Binding.Selection": {"accessor": _HOST_ONLY},
    "case:Binding.Now": {"accessor": _HOST_ONLY},
    "case:Binding.Local": {
        "format": "the format closure: this surface authors the declarative codec in its place",
        "parse": "the parse closure: this surface authors the declarative codec in its place",
    },
    "case:CellKindErased.Editable": {"onEdit": _ROW_FN},
    "case:CellKindErased.Checkbox": {"get": _ROW_FN, "onToggle": _ROW_FN},
    "case:CellKindErased.Link": {"hrefFn": _ROW_FN, "labelFn": _ROW_FN},
    "case:CellKindErased.Pill": {"labelFn": _ROW_FN, "toneFn": _ROW_FN},
    "case:CellKindErased.Progress": {"fractionFn": _ROW_FN, "labelFn": _ROW_FN},
    "case:CellKindErased.Custom": {"fn": _ROW_FN},
}


def stale_declarations() -> list[str]:
    """Every declaration naming an owner or a field the generated vocabulary does not have."""
    tables = owners()
    stale: list[str] = []
    for owner in sorted(set(CLASSES) | set(UNCLASSED)):
        if owner not in tables:
            stale.append(f"{owner}: no such owner")
    for label, table in (("RENAMED", RENAMED), ("AUTHORING_DEFAULTS", AUTHORING_DEFAULTS), ("GAPS", GAPS)):
        for owner, members in sorted(table.items()):
            fields = tables.get(owner)
            if fields is None:
                stale.append(f"{label} {owner}: no such owner")
                continue
            names = {f.name for f in fields}
            stale.extend(
                f"{label} {owner}.{member}: no such field" for member in sorted(members) if member not in names
            )
            if owner in UNCLASSED:
                stale.append(f"{label} {owner}: the owner is declared UNCLASSED")
    return stale


_stale = stale_declarations()
if _stale:
    raise StaleDeclaration("authoring dialect declarations the IDL no longer has: " + "; ".join(_stale))
