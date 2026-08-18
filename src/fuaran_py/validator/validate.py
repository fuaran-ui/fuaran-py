"""Pre-emit validation surface (default-deny by shape).

The decoder already rejects malformed *wire* input; this surface validates a
*constructed* tree before it is emitted, catching the structural defects an
author is most likely to introduce — empty node ids, duplicate ids, and
unrecognised node kinds — and returning structured findings rather than throwing.
It is intentionally small for the bootstrap; the full rule set (the analogue of
the language tier's validator framework) is filled in incrementally.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model import Arr, Node, Obj, Value
from ..schema.decode import KNOWN_KINDS


@dataclass(frozen=True)
class Finding:
    """A structural validation finding at a ``$``-rooted path."""

    code: str
    path: str
    message: str


def validate_node(node: Node) -> list[Finding]:
    """Walk a node tree, returning any structural findings (empty list ⇒ clean)."""
    findings: list[Finding] = []
    seen_ids: set[str] = set()
    _walk(node, "$", findings, seen_ids)
    return findings


def _walk(node: Node, path: str, findings: list[Finding], seen_ids: set[str]) -> None:
    if node.id == "":
        findings.append(Finding("FUARAN-EMPTY-ID", f"{path}.id", "a node carries an empty id"))
    elif node.id in seen_ids:
        findings.append(Finding("FUARAN-DUP-ID", f"{path}.id", f"node id '{node.id}' appears more than once"))
    else:
        seen_ids.add(node.id)

    if node.kind.tag not in KNOWN_KINDS:
        findings.append(Finding("UNKNOWN_NODE_KIND", f"{path}.kind.$type", f"unrecognised node kind '{node.kind.tag}'"))

    if node.kind.tag == "Switch":
        _check_switch(node.kind, f"{path}.kind", findings)

    if node.kind.tag == "Chart":
        _check_chart(node, node.kind, f"{path}.kind", findings)

    _check_inert_control(node, node.kind, f"{path}.kind", findings)

    for child, child_path in _child_nodes(node.kind, f"{path}.kind"):
        _walk(child, child_path, findings, seen_ids)


def _check_switch(kind: Obj, path: str, findings: list[Finding]) -> None:
    """Switch-specific structural checks (Phase 392): duplicate match values
    (dead cases, FUARAN082) and an empty/ungrounded state key (FUARAN083)."""
    if kind.fields.get("stateKey") == "":
        findings.append(
            Finding(
                "FUARAN083",
                f"{path}.stateKey",
                "switch has an empty stateKey — it can never resolve a case and is stuck on its "
                "default; name the key it should read",
            )
        )
    cases = kind.fields.get("cases")
    if isinstance(cases, Arr):
        seen: set[str] = set()
        reported: set[str] = set()
        for case in cases.items:
            if isinstance(case, Obj):
                match = case.fields.get("match")
                if isinstance(match, str):
                    if match in seen and match not in reported:
                        findings.append(
                            Finding(
                                "FUARAN082",
                                f"{path}.cases",
                                f"switch has two or more cases matching '{match}' — first-match-wins "
                                "makes the later case dead",
                            )
                        )
                        reported.add(match)
                    seen.add(match)


_NUMERIC_COLUMN_TYPES = frozenset({"int", "float", "bool"})  # bool coerces 1/0 at lowering

_DATED_COLUMN_TYPES = frozenset({"date", "timestamp"})
"""The column types a temporal x-axis can read (FUARAN097, Phase 882). Both are
honoured: a timestamp's time-of-day is DISCARDED by the lowering, which is a
documented narrowing, not a mismatch."""


def _check_chart(node: Node, kind: Obj, path: str, findings: list[Finding]) -> None:
    """Schema-grounded ChartSpec validation (Phase 640, FUARAN086-089; Phase 882,
    FUARAN097).

    An ungrounded field reference is the LANGUAGE's defect to catch before
    lowering — a wrong field name otherwise lowers to a silently flat/empty
    chart. Grounding fires only where the schema is statically known: a
    `Binding.Transform` over an Embedded table with an EMPTY pipeline (a
    non-empty pipeline changes the column set; a Ref/Query/Static source is
    unknowable pre-emit and deliberately passes ungrounded — only refuse what
    is PROVABLY wrong)."""
    chart_kind = kind.fields.get("kind")
    y_fields_raw = kind.fields.get("yFields")
    y_fields = [y for y in y_fields_raw.items if isinstance(y, str)] if isinstance(y_fields_raw, Arr) else []

    # FUARAN088 (Error) — pie needs exactly one series (the Phase 638 lowering
    # refuses multi-series geometry rather than truncating).
    if chart_kind == "Pie" and len(y_fields) != 1:
        findings.append(
            Finding(
                "FUARAN088",
                f"{path}.yFields",
                f"a Pie chart carries {len(y_fields)} series — the lowering refuses other than exactly one "
                "(plot one share column, or switch kind)",
            )
        )

    # FUARAN089 (Warning) — `stacked` is dead intent outside Bar/Area.
    if kind.fields.get("stacked") is True and chart_kind in ("Line", "Scatter", "Pie"):
        findings.append(
            Finding(
                "FUARAN089",
                f"{path}.stacked",
                f"stacked is meaningless on a {chart_kind} chart — the lowering ignores the flag",
            )
        )

    # FUARAN086/087 — grounding, only where the schema is statically known.
    source = kind.fields.get("source")
    if not (isinstance(source, Obj) and source.tag == "Transform"):
        return
    pipeline = source.fields.get("pipeline")
    if not isinstance(pipeline, Arr) or pipeline.items:
        return
    embedded = source.fields.get("source")
    if not isinstance(embedded, Obj) or "ref" in embedded.fields:
        return
    schema = embedded.fields.get("schema")
    if not isinstance(schema, Arr):
        return
    col_types: dict[str, str] = {}
    for entry in schema.items:
        if isinstance(entry, Obj):
            name = entry.fields.get("name")
            ty = entry.fields.get("type")
            if isinstance(name, str) and isinstance(ty, str):
                col_types[name] = ty

    def ground(field_name: str, require_numeric: bool) -> None:
        ty = col_types.get(field_name)
        if ty is None:
            findings.append(
                Finding(
                    "FUARAN086",
                    path,
                    f"chart field '{field_name}' names a column absent from the embedded schema",
                )
            )
        elif require_numeric and ty not in _NUMERIC_COLUMN_TYPES:
            findings.append(
                Finding(
                    "FUARAN087",
                    path,
                    f"chart field '{field_name}' is a '{ty}' column the lowering cannot plot numerically",
                )
            )

    # FUARAN097 (Phase 882) — a temporal x-axis is a DECLARATION, and this is
    # where the language grounds it. A non-date column cannot parse as a date, so
    # every row's x would read as the epoch and the chart would draw every point
    # stacked on one date. Refused, never coerced; and refused only where the
    # schema is statically known (the window above), so an unknowable source
    # passes ungrounded — refuse only what is PROVABLY wrong.
    #
    # Pie is NOT excluded here even though the lowering neutralises the
    # declaration: a dead declaration on a pie is still a claim about the column,
    # and the reference raises it wherever the x field is grounded.
    temporal_x = kind.fields.get("xScale") == "Temporal"

    x_field = kind.fields.get("xField")
    if isinstance(x_field, str):
        x_type = col_types.get(x_field)
        if temporal_x and x_type is not None and x_type not in _DATED_COLUMN_TYPES:
            findings.append(
                Finding(
                    "FUARAN097",
                    path,
                    f"chart declares a temporal x-axis over field '{x_field}' of type '{x_type}' — a date axis "
                    "needs a date column, and every row's x would read as 1970-01-01; give the column type "
                    "'date' (canonical ISO-8601 YYYY-MM-DD cells), or drop xScale to plot the values as "
                    "categories (FUARAN097)",
                )
            )
        # FUARAN087's x arm is NARROWED by a temporal declaration: a temporal
        # Scatter reads its x as dates, so a date column there is correct rather
        # than "not numeric", and FUARAN097 above is the rule that governs it.
        # Without the narrowing a correctly-authored time-series scatter would
        # raise a mismatch about the very column it declared.
        ground(x_field, require_numeric=chart_kind == "Scatter" and not temporal_x)
    for yf in y_fields:
        ground(yf, require_numeric=True)


def _child_nodes(value: Value, path: str) -> list[tuple[Node, str]]:
    """Find directly-nested ``Node`` values (e.g. layout ``children``)."""
    out: list[tuple[Node, str]] = []
    if isinstance(value, Node):
        out.append((value, path))
    elif isinstance(value, Arr):
        for i, item in enumerate(value.items):
            out.extend(_child_nodes(item, f"{path}.{i}"))
    elif isinstance(value, Obj):
        for key, field_value in value.fields.items():
            out.extend(_child_nodes(field_value, f"{path}.{key}"))
    return out


# ── FUARAN069 — the inert-control rule (Phase 426 write-back doctrine) ───────

_WRITABLE_BINDING_TAGS = frozenset({"State", "Local"})


def _is_write_back_target(value: Value) -> bool:
    """Is this binding a slot the write-back default can write *to*?

    `State` and `Local` always are. `Filter` is writable only WITHOUT a default:
    a defaulted filter is a read of a computed value, not a slot. Everything else
    — `Static`, `Query`, `Computed`, `Transform`, `Selection` — is a read.

    Mirrors the reference host's `isWriteBackTarget`; the wire shape is the same
    discriminated union, so the same three cases decide it.
    """
    if not isinstance(value, Obj):
        return False
    tag = value.tag
    if tag in _WRITABLE_BINDING_TAGS:
        return True
    return tag == "Filter" and "default" not in value.fields


def _inert(kind: Obj, handler: str, slot: str) -> bool:
    """No handler and no writable slot: nothing can carry the interaction.

    An omitted handler is the DECLARATIVE shape, not an error — the write-back
    default is supposed to carry it. The defect is omitting the handler *and*
    pointing the value at something unwritable, which leaves the control looking
    interactive and doing nothing.
    """
    return handler not in kind.fields and not _is_write_back_target(kind.fields.get(slot))


def _check_inert_control(node: Node, kind: Obj, path: str, findings: list[Finding]) -> None:
    """FUARAN069 (Warning) — an interactive control that cannot act.

    Reported per control kind with a short descriptor, matching the reference
    host's sites: Tabs, Disclosure, Modal, Select and Form fields.
    """

    def report(control: str) -> None:
        findings.append(
            Finding(
                "FUARAN069",
                path,
                f"{control} on '{node.id}' has no event handler and no writable value binding "
                f"— bind its value to $state.<key> or $filters.<name>, or supply the handler",
            )
        )

    tag = kind.tag
    if tag == "Tabs":
        # The tag overlay is a second way to be live: `activeTag` over a
        # populated `tabTags` carries the selection when `activeIndex` does not.
        tag_live = "onSelectTag" in kind.fields or (
            "tabTags" in kind.fields and _is_write_back_target(kind.fields.get("activeTag"))
        )
        if _inert(kind, "onSelect", "activeIndex") and not tag_live:
            report("Tabs")
    elif tag == "Disclosure":
        if _inert(kind, "onToggle", "open"):
            report("Disclosure")
    elif tag == "Modal":
        # Only a DISMISSABLE modal is defective: one that cannot be dismissed by
        # design is not inert, it is modal.
        if kind.fields.get("dismissable") is True and _inert(kind, "onDismiss", "open"):
            report("Modal")
    elif tag == "Select":
        if kind.fields.get("multiple") is True:
            if _inert(kind, "onChangeMulti", "values"):
                report("Select(multiple)")
        elif _inert(kind, "onChange", "value"):
            report("Select")
    elif tag == "Form":
        fields = kind.fields.get("fields")
        if isinstance(fields, Arr):
            for item in fields.items:
                if isinstance(item, Obj):
                    field_kind = item.fields.get("kind")
                    field_id = item.fields.get("id")
                    if isinstance(field_kind, Obj) and _inert(field_kind, "onChange", "value"):
                        report(f"FormField({field_id if isinstance(field_id, str) else '?'})")
