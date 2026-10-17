"""The authoring surface covers every generated field (Phase 2316).

The wire layer is generated from ``idl.json``; the authoring dataclasses are hand-written over it.
This pin is what keeps the hand layer from falling behind unnoticed: every field of every kind,
record, union case and the node envelope is either a parameter of its authoring class or a declared,
reasoned gap in :mod:`fuaran_ui.schema.authoring_dialect`, so a field the IDL gains fails here by
name. The dialect is held honest in the other direction too: a gap that became authorable, a rename
naming no parameter, and an authoring default the wire does not require are each red.

The second half carries fuaran#2025's acceptance: the Button column's label (fuaran#1909) and a
live ``TransformSource`` (fuaran#1100) are authorable, so the four corpus fixtures that need them
are authored here through the public surface and compared with the corpus byte for byte.
"""

from __future__ import annotations

import dataclasses
import importlib
import keyword
import re
import typing
from collections.abc import Callable, Mapping

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui.conformance import host_capability
from fuaran_ui.result import Ok
from fuaran_ui.schema import authoring_dialect as dialect
from fuaran_ui.schema import decode_node, encode_node
from fuaran_ui.schema import generated as g
from fuaran_ui.schema import types as t
from fuaran_ui.ui import compute as cp
from fuaran_ui.ui import encode

# ── Resolution ───────────────────────────────────────────────────────────────────────────────


def _resolve(path: str) -> type:
    module_name, attr = path.split(":")
    return getattr(importlib.import_module(module_name), attr)  # type: ignore[no-any-return]


def _case_classes(union: str) -> dict[str, type]:
    """The host union alias's members, keyed by the tag their ``to_wire`` writes."""
    alias = getattr(t, host_capability.SPEC_UNION_ALIASES.get(union, union), None)
    members = (host_capability._union_members(alias) or ()) + tuple(
        host_capability.SPEC_UNION_EXTRA_MEMBERS.get(union, ())
    )
    by_tag: dict[str, type] = {}
    for member in members:
        verdict, tag = host_capability._discriminator(member)
        if verdict == "literal" and tag:
            by_tag[tag] = member
    return by_tag


def _authoring_class(owner: str) -> type | None:
    if owner in dialect.CLASSES:
        return _resolve(dialect.CLASSES[owner])
    family, _, name = owner.partition(":")
    if family in ("kind", "record"):
        return getattr(t, name, None)
    if family == "case":
        union, _, case = name.partition(".")
        return _case_classes(union).get(case)
    return None


def _parameter(cls: type, owner: str, wire: str) -> dataclasses.Field[object] | None:
    """The authoring parameter for a wire member, by the naming rule or a declared rename."""
    params = {f.name: f for f in dataclasses.fields(cls) if f.init}
    renamed = dialect.RENAMED.get(owner, {}).get(wire)
    if renamed is not None:
        return params.get(renamed)
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", wire).lower()
    for candidate in (snake, f"{snake}_" if keyword.iskeyword(snake) else None):
        if candidate is not None and candidate in params:
            return params[candidate]
    return None


def _has_default(param: dataclasses.Field[object]) -> bool:
    return param.default is not dataclasses.MISSING or param.default_factory is not dataclasses.MISSING


# ── The coverage, as findings (so a falsifier can read them) ────────────────────────────────


def _findings() -> list[str]:
    """Every way the authoring surface and the dialect disagree with the generated layer."""
    findings: list[str] = []
    for owner, fields in sorted(dialect.owners().items()):
        if owner in dialect.UNCLASSED:
            continue
        cls = _authoring_class(owner)
        if cls is None:
            findings.append(f"{owner}: no authoring class and not declared UNCLASSED")
            continue
        if not dataclasses.is_dataclass(cls):
            findings.append(f"{owner}: {cls.__name__} is not a dataclass")
            continue
        gaps = dialect.GAPS.get(owner, {})
        defaults = dialect.AUTHORING_DEFAULTS.get(owner, {})
        for f in fields:
            param = _parameter(cls, owner, f.name)
            if param is None:
                if f.name not in gaps:
                    findings.append(f"{owner}.{f.name}: not a parameter of {cls.__name__} and not a declared gap")
                if f.name in defaults:
                    findings.append(f"{owner}.{f.name}: declared an authoring default but is not a parameter")
                continue
            if f.name in gaps:
                findings.append(f"{owner}.{f.name}: authored as {cls.__name__}.{param.name}; retire its gap")
            defaulted = f.required and _has_default(param)
            if defaulted and f.name not in defaults:
                findings.append(
                    f"{owner}.{f.name}: wire-required, defaulted by {cls.__name__}.{param.name}, undeclared"
                )
            if f.name in defaults and not defaulted:
                findings.append(f"{owner}.{f.name}: declared an authoring default the authoring class does not take")
        for wire, renamed in dialect.RENAMED.get(owner, {}).items():
            if renamed not in {p.name for p in dataclasses.fields(cls)}:
                findings.append(f"{owner}.{wire}: renamed to {renamed!r}, which {cls.__name__} does not have")
    for owner, path in dialect.CLASSES.items():
        try:
            _resolve(path)
        except (ImportError, AttributeError) as exc:
            findings.append(f"{owner}: CLASSES names {path}, which does not resolve ({exc})")
    return findings


def test_every_generated_field_is_authored_or_a_declared_gap() -> None:
    assert _findings() == []


def test_the_pin_covers_every_family_the_generated_layer_has() -> None:
    owners = dialect.owners()
    assert {o for o in owners if o.startswith("kind:")} == {f"kind:{k}" for k in g.KINDS}
    assert {o for o in owners if o.startswith("record:")} == {f"record:{r}" for r in g.RECORD_FIELDS}
    assert sum(1 for o in owners if o.startswith("case:")) == sum(len(c) for c in g.UNION_CASE_FIELDS.values())
    assert "node" in owners


def test_every_declaration_carries_a_reason() -> None:
    reasons = [*dialect.UNCLASSED.values()]
    for table in (dialect.AUTHORING_DEFAULTS, dialect.GAPS):
        reasons.extend(r for members in table.values() for r in members.values())
    assert all(isinstance(r, str) and r.strip() for r in reasons)


# ── Falsifiers: each direction of the pin goes red ──────────────────────────────────────────


def _with_kind_fields(monkeypatch: pytest.MonkeyPatch, kind: str, fields: tuple[g.F, ...]) -> None:
    monkeypatch.setattr(g, "KIND_FIELDS", {**g.KIND_FIELDS, kind: fields})


def test_a_field_the_idl_gains_fails_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_kind_fields(monkeypatch, "Badge", (*g.KIND_FIELDS["Badge"], g.F("pulseRate", "optional", g.T("int"))))
    assert _findings() == ["kind:Badge.pulseRate: not a parameter of Badge and not a declared gap"]


def test_a_gap_that_became_authorable_must_be_retired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dialect, "GAPS", {**dialect.GAPS, "kind:Badge": {"label": "pretend"}})
    assert _findings() == ["kind:Badge.label: authored as Badge.label; retire its gap"]


def test_an_undeclared_authoring_default_is_red(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dialect, "AUTHORING_DEFAULTS", {k: v for k, v in dialect.AUTHORING_DEFAULTS.items() if k != "kind:Badge"}
    )
    assert _findings() == ["kind:Badge.variant: wire-required, defaulted by Badge.variant, undeclared"]


def test_a_declaration_the_idl_no_longer_has_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_kind_fields(monkeypatch, "Button", tuple(f for f in g.KIND_FIELDS["Button"] if f.name != "tooltip"))
    assert dialect.stale_declarations() == ["GAPS kind:Button.tooltip: no such field"]
    # ... and refused at IMPORT, not merely reported. (A reload re-creates the exception class
    # before it raises, so the refusal is matched by its base and its name.)
    with pytest.raises(ValueError, match=r"kind:Button\.tooltip") as refused:
        importlib.reload(dialect)
    assert type(refused.value).__name__ == "StaleDeclaration"
    monkeypatch.undo()
    importlib.reload(dialect)
    assert dialect.stale_declarations() == []


# ── fuaran#2025: the two constructs are authorable, certified against the corpus ────────────


def _rows(n: int, *pairs: tuple[str, list[object]]) -> list[dict[str, object]]:
    return [{name: values[i] for name, values in pairs} for i in range(n)]


def _count(of: str) -> tuple[cp.GroupBy]:
    return (cp.GroupBy([], [cp.Agg("n", "count", of)]),)


def _spend() -> cp.TransformBinding:
    table = cp.Table(
        [("dept", "string"), ("amount", "int")],
        [
            cp.Column("dept", "string", [cp.cell_str("eng"), cp.cell_str("eng"), cp.cell_str("sales")]),
            cp.Column("amount", "int", [cp.cell_int(100), cp.cell_int(120), cp.cell_int(90)]),
        ],
    )
    return cp.TransformBinding(cp.Embedded(table), (cp.GroupBy(["dept"], [cp.Agg("total", "sum", "amount")]),))


def _open_button() -> t.ColumnKind:
    return t.ColumnKind("Button", label=t.LiteralText("Open"), on_click=True)


AUTHORED: Mapping[str, Callable[[], t.UiNode]] = {
    "badge-transform-live": lambda: t.UiNode(
        "badge-transform-live",
        t.Badge(
            t.Bound(
                cp.TransformBinding(
                    t.State(
                        "request-log",
                        _rows(2, ("medication", ["Amoxicillin", "Ibuprofen"]), ("quantity", [20, 50])),
                    ),
                    _count("medication"),
                )
            ),
            "Info",
        ),
    ),
    "shared-source-seeded-pair": lambda: t.UiNode(
        "shared-source-seeded-pair",
        t.Box(
            (
                t.UiNode(
                    "member-grid",
                    t.DataGrid(
                        t.State("members", _rows(2, ("team", ["Ops", "Research"]))),
                        (t.Column("Team", field_name="team"),),
                        row_key_field="team",
                    ),
                ),
                t.UiNode(
                    "member-count",
                    t.Badge(t.Bound(cp.TransformBinding(t.State("members", None), _count("team"))), "Info"),
                ),
            ),
            t.AutoLayout(),
            "Dashboard",
        ),
    ),
    "grid-action-column-clean": lambda: t.UiNode(
        "grid-action-column-clean",
        t.DataGrid(
            _spend(),
            (
                t.Column("Department", field_name="dept"),
                t.Column("Total", kind=t.ColumnKind("Numeric"), field_name="total"),
                t.Column(
                    "Team", kind=t.TonedPillColumnKind("dept", {"eng": "Info", "sales": "Success"}), field_name="dept"
                ),
                t.Column("Open", kind=_open_button(), value=False),
                t.Column(
                    "Review",
                    kind=t.ColumnKind(
                        "ButtonGroup",
                        buttons=(
                            t.ButtonGroupItem(t.LiteralText("Approve"), on_click=True),
                            t.ButtonGroupItem(t.LiteralText("Reject"), on_click=True),
                        ),
                    ),
                    value=False,
                ),
            ),
            row_key_field="dept",
            exportable=True,
            sort_state_key="spend-sort",
        ),
    ),
    "grid-action-column-field": lambda: t.UiNode(
        "grid-action-column-field",
        t.DataGrid(
            _spend(),
            (t.Column("Department", field_name="dept"), t.Column("Actions", kind=_open_button(), field_name="action")),
            row_key_field="dept",
            exportable=True,
            sort_state_key="spend-sort",
        ),
    ),
}


@pytest.mark.parametrize("fixture", sorted(AUTHORED))
def test_the_construct_is_authored_byte_identically_to_the_corpus(fixture: str) -> None:
    expected = (CORPUS_ROOT / "nodes" / f"{fixture}.json").read_text(encoding="utf-8").strip()
    assert encode(AUTHORED[fixture]()) == expected


def test_a_cell_member_is_refused_on_a_cell_kind_that_does_not_declare_it() -> None:
    with pytest.raises(ValueError, match="Button cell"):
        t.ColumnKind("Text", label=t.LiteralText("x"))
    with pytest.raises(ValueError, match="ButtonGroup cell"):
        t.ColumnKind("Button", buttons=(t.ButtonGroupItem(t.LiteralText("x")),))


@pytest.mark.parametrize("live", [t.Selection("grid"), t.Query("rows")], ids=["Selection", "Query"])
def test_every_live_source_the_decoder_preserves_round_trips(live: t.Selection | t.Query) -> None:
    assert set(typing.get_args(cp.TransformSource)) == {cp.Embedded, cp.Ref, t.State, t.Selection, t.Query}
    authored = encode(t.UiNode("b", t.Badge(t.Bound(cp.TransformBinding(live, _count("x"))), "Info")))
    decoded = decode_node(authored)
    assert isinstance(decoded, Ok)
    assert encode_node(decoded.value) == authored
    assert f'"source":{{"$type":"{type(live).__name__}"' in authored
