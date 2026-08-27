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

    if node.kind.tag == "Form":
        _check_field_rules(node, node.kind, f"{path}.kind", findings)

    if node.kind.tag == "Media":
        _check_media_label(node, node.kind, f"{path}.kind", findings)

    _check_inert_control(node, node.kind, f"{path}.kind", findings)

    for child, child_path in _child_nodes(node.kind, f"{path}.kind"):
        _walk(child, child_path, findings, seen_ids)


def _check_media_label(node: Node, kind: Obj, path: str, findings: list[Finding]) -> None:
    """FUARAN108 (fuaran#1076) — a media transport with no accessible name.

    Only a LITERAL is judged. A ``Bound`` or ``I18n`` label resolves at render
    time from data this walk cannot see, so calling it empty would be a guess.
    What is left is the case that is decidable and is also the case that actually
    happens: an author who fills the source and forgets the name.

    Whitespace counts as empty. A label of ``" "`` is not a name a listener can
    act on, and admitting it would make the rule trivially evadable by a space —
    which is worse than not having the rule, because the document would then carry
    a green gate saying it had been checked.

    Error rather than Warning because there is no legitimate shape it refuses: a
    media element is a transport, not a picture, so unlike an image's ``alt``
    there is no honest empty case for it to have.
    """
    label = kind.fields.get("label")
    # A decoded `TextSource.Literal` IS the bare string on this host's structural
    # model; every other case is an `Obj` and is deliberately not judged.
    if isinstance(label, str) and label.strip() == "":
        findings.append(
            Finding(
                "FUARAN108",
                f"{path}.label",
                f"media node '{node.id}' has an EMPTY label — a media element is a transport, not a "
                "picture, so it is never decorative and there is no honest empty case the way there is "
                'for an image\'s alt; without a name it is announced to a screen reader as "video" or '
                '"audio" and nothing more, telling the reader that a player exists and not what it '
                "plays. Give 'label' the text a listener needs to decide whether to play it",
            )
        )


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
                                "makes the later case dead; give each case a distinct match value",
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
                    f"chart field '{field_name}' is a '{ty}' column — the lowering reads non-numeric "
                    "cells as 0.0, a silently flat series",
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


# ── The declared-rule family (fuaran#864, FUARAN100 / FUARAN101) ─────────────
#
# `FormField.rule` declares the ACCEPTED SET where `FormFieldKind` names the
# CONTROL, so the two can disagree, and the two ways they disagree are both
# decidable from the field alone: a slot the control cannot honour, and a literal
# operand duplicating a bound the control already declares. Neither needs the
# tree, which is why they are here while FUARAN099 — the third rule of the same
# phase — is a declared abstention (see `validator-coverage.json`): that one asks
# whether ANYTHING IN THE TREE writes a state key, and this host has no
# tree-wide write-key projection to answer with.

_RULE_SLOTS: tuple[tuple[str, str], ...] = (
    ("format", "rule.format"),
    ("pattern", "rule.pattern"),
    ("minLength", "rule.minLength"),
    ("maxLength", "rule.maxLength"),
)

#: Controls that can honour `rule.format` (the `email` / `url` / `tel`
#: shorthands): a free-text single-line control and nothing else. `TextArea` is
#: excluded deliberately — the reference host's table, where a multi-line control
#: is not a place an email shorthand applies.
_HONOURS_FORMAT = frozenset({"Text"})

#: Controls that can honour the text bounds (`pattern` / `minLength` /
#: `maxLength`): the two that carry a string value.
_HONOURS_TEXT_BOUNDS = frozenset({"Text", "TextArea"})

#: Controls declaring their own numeric / temporal bounds, which a LITERAL
#: compare operand can duplicate (FUARAN101). `compare` itself is absent from the
#: unhonourable table on purpose: it compares the field's VALUE, which every
#: control has.
_BOUNDED_CONTROLS = frozenset({"RangedNumber", "Range", "Date", "DateRange"})

#: Which of a control's declared bounds each comparison operator duplicates.
#: `eq` / `neq` duplicate neither and are silent.
_DUPLICATED_BOUND: dict[str, str] = {"gt": "min", "gte": "min", "lt": "max", "lte": "max"}


def _check_field_rules(node: Node, kind: Obj, path: str, findings: list[Finding]) -> None:
    """FUARAN100 (Warning) — a rule slot the field's control cannot honour, so the
    constraint is carried and never applied (dead intent).

    FUARAN101 (Warning) — a compare against a LITERAL while the control already
    declares the equivalent bound: two sources for one bound, free to disagree,
    and nothing decides which wins.
    """
    fields = kind.fields.get("fields")
    if not isinstance(fields, Arr):
        return
    for i, item in enumerate(fields.items):
        if not isinstance(item, Obj):
            continue
        rule = item.fields.get("rule")
        if not isinstance(rule, Obj):
            continue
        field_id = item.fields.get("id")
        field_id_s = field_id if isinstance(field_id, str) else "?"
        control_obj = item.fields.get("kind")
        control = control_obj.tag if isinstance(control_obj, Obj) and control_obj.tag is not None else "?"
        rule_path = f"{path}.fields.{i}.rule"

        for slot, label in _RULE_SLOTS:
            if slot not in rule.fields:
                continue
            honoured = control in (_HONOURS_FORMAT if slot == "format" else _HONOURS_TEXT_BOUNDS)
            if not honoured:
                findings.append(
                    Finding(
                        "FUARAN100",
                        f"{rule_path}.{slot}",
                        f"form '{node.id}' field '{field_id_s}' declares {label} on a {control} control, "
                        "which cannot honour it — the constraint is carried and never applied (dead "
                        "intent); move the rule to a text control, or drop the slot. If a host you "
                        "target DOES honour it, this warning is expected and can be ignored",
                    )
                )

        compare = rule.fields.get("compare")
        if not isinstance(compare, Obj):
            continue
        against = compare.fields.get("against")
        # Only a LITERAL operand can duplicate a static bound — a `State` read is
        # a value that changes, which is what the rule slot is for.
        if not (isinstance(against, Obj) and against.tag == "Static"):
            continue
        if control not in _BOUNDED_CONTROLS:
            continue
        op = compare.fields.get("op")
        bound = _DUPLICATED_BOUND.get(op) if isinstance(op, str) else None
        if bound is None or bound not in (control_obj.fields if isinstance(control_obj, Obj) else {}):
            continue
        findings.append(
            Finding(
                "FUARAN101",
                f"{rule_path}.compare",
                f"form '{node.id}' field '{field_id_s}' compares against a LITERAL while its control "
                f"already declares {control}.{bound} — two sources for one bound, free to disagree, and "
                "nothing decides which wins; drop the compare and keep the control's bound, or make the "
                'operand read something that changes ({"$type":"State","key":"<sibling field id>"}), '
                "which is what the rule slot is for",
            )
        )


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
