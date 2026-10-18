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

The third half is Phase 2334's: the gaps 2316 declared are authorable, each certified against the
corpus fixture that carries its member where one exists, and the authored row feed follows
WIRE_FORMAT §5's cell rule, pinned against the corpus's row-source fixtures.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import keyword
import re
import typing
from collections.abc import Callable, Mapping

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui.canonical import encode_value
from fuaran_ui.conformance import host_capability
from fuaran_ui.model import Obj, from_json
from fuaran_ui.result import Ok
from fuaran_ui.schema import authoring_dialect as dialect
from fuaran_ui.schema import decode_node, encode_node
from fuaran_ui.schema import generated as g
from fuaran_ui.schema import types as t
from fuaran_ui.ui import action, binding, encode, fuaran
from fuaran_ui.ui import compute as cp

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
    # Phase 2334 — `on_read` is the authored declaration of the read handler.
    "btn-read-workbook": lambda: t.UiNode(
        "btn-read-workbook",
        t.Button(
            t.LiteralText("Load workbook"),
            on_click=t.ReadFileBody("workbook-upload:0", "Base64", on_read=True),
            variant="Primary",
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


# ── Phase 2334: the declared gaps are authorable ─────────────────────────────────────────────


def test_commit_local_is_authored_byte_identically_to_the_op_fixture() -> None:
    expected = (CORPUS_ROOT / "ops" / "op-editnode-action-commitlocal.json").read_text(encoding="utf-8").strip()
    button = t.Button(t.LiteralText("Apply"), on_click=action.commit_local("salary-input"), variant="Primary")
    assert encode_value(Obj("EditNode", {"newKind": button.to_wire(), "target": "btn-apply"})) == expected


def test_an_on_commit_action_buffer_is_authored_byte_identically_to_the_lowers_to_tree() -> None:
    fixture = CORPUS_ROOT / "lowers-to" / "commit-local.json"
    if not fixture.is_file():
        pytest.skip("the lowers-to family is not in this corpus (it is not bundled); set FUARAN_WIRE_FIXTURES")
    document = json.loads(fixture.read_text(encoding="utf-8"))
    salary = t.Local(
        t.State("profile.salary", 0),
        t.OnCommitAction(),
        commit_to="profile.salary",
        codec=t.NumberFormat(2),
    )
    tree = t.UiNode(
        "profile",
        t.Form(
            (t.FormField("salary-input", t.LiteralText("Salary"), t.NumberField(salary, on_change=False)),),
            submit_label=t.LiteralText("Save"),
        ),
    )
    assert encode(tree) == encode_value(from_json(document["tree"]))
    assert encode_value(t.CommitLocal("salary-input").to_wire()) == encode_value(from_json(document["action"]))


def _local_on_submit() -> t.UiNode:
    buffer = t.Local(t.State("name", ""), t.OnSubmit(), commit_to="name")
    field = t.FormField("name", t.LiteralText("Name"), t.TextField(buffer, on_change=False))
    return t.UiNode("f", t.Form((field,)))


#: Members no corpus fixture carries yet, each authored through the public surface with the bytes
#: its member must take. Named for Phase 2328 (the reference emits the remaining behaviour
#: families): until a fixture lands, these pin the authored bytes and this host's round trip.
NO_FIXTURE_YET: Mapping[str, tuple[Callable[[], t.UiNode], str]] = {
    "ColumnWidth.Fixed.pixels": (
        lambda: t.UiNode("g", t.DataGrid(t.Static([]), (t.Column("A", width=t.ColumnWidth("Fixed", pixels=120)),))),
        '"width":{"$type":"Fixed","pixels":120}',
    ),
    "ColumnWidth.Flex.weight": (
        lambda: t.UiNode("g", t.DataGrid(t.Static([]), (t.Column("A", width=t.ColumnWidth("Flex", weight=1.5)),))),
        '"width":{"$type":"Flex","weight":1.5}',
    ),
    "Binding.Filter.defaultValue": (
        lambda: t.UiNode("b", t.Badge(t.Bound(binding.filter("region", default_value="EMEA")), "Info")),
        '{"$type":"Filter","defaultValue":"EMEA","name":"region"}',
    ),
    "LocalFlushTrigger.OnSubmit": (_local_on_submit, '"flushOn":{"$type":"OnSubmit"}'),
    "Chart.onPointClick": (
        lambda: fuaran.chart("c", source=t.Static([]), x_field="x", y_fields=["y"], on_point_click=True),
        '"onPointClick":"<closure>"',
    ),
    "DataGrid.onRowClick": (
        lambda: fuaran.grid("g", source=t.Static([]), row_key_field="id", on_row_click=True),
        '"onRowClick":"<closure>"',
    ),
    "Map.onMarkerClick": (
        lambda: fuaran.map("m", source=t.Static([]), on_marker_click=True),
        '"onMarkerClick":"<closure>"',
    ),
    "ReadFileBody.onRead absent": (
        lambda: t.UiNode(
            "r", t.Button(t.LiteralText("Read"), on_click=action.read_file_body("upload:0", on_read=False))
        ),
        '{"$type":"ReadFileBody","encoding":"Text","fileRef":"upload:0"}',
    ),
}


@pytest.mark.parametrize("member", sorted(NO_FIXTURE_YET))
def test_a_member_with_no_fixture_yet_is_authored_and_round_trips(member: str) -> None:
    author, fragment = NO_FIXTURE_YET[member]
    authored = encode(author())
    assert fragment in authored
    decoded = decode_node(authored)
    assert isinstance(decoded, Ok), decoded
    assert encode_node(decoded.value) == authored


def test_the_handler_flags_are_omitted_by_default() -> None:
    authored = (
        encode(fuaran.chart("c", source=t.Static([]), x_field="x", y_fields=["y"]))
        + encode(fuaran.grid("g", source=t.Static([]), row_key_field="id"))
        + encode(fuaran.map("m", source=t.Static([])))
    )
    assert "onPointClick" not in authored
    assert "onRowClick" not in authored
    assert "onMarkerClick" not in authored


@pytest.mark.parametrize(
    "build",
    [
        lambda: t.ColumnWidth("Fixed"),
        lambda: t.ColumnWidth("Flex"),
        lambda: t.ColumnWidth("Auto", pixels=10),
        lambda: t.ColumnWidth("Flex", pixels=10, weight=1.0),
        lambda: t.ColumnWidth("Fixed", pixels=10, weight=1.0),
    ],
    ids=["Fixed-bare", "Flex-bare", "Auto-pixels", "Flex-pixels", "Fixed-weight"],
)
def test_a_width_member_is_required_on_its_case_and_refused_on_any_other(build: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match="ColumnWidth"):
        build()


# ── Phase 2334, carried from fuaran#1960: the authored row feed follows WIRE_FORMAT §5 ────────
#
# A ``None`` cell omits its key and a nested cell writes the "<opaque>" sentinel. No corpus fixture
# carries either in a row feed (one is named for Phase 2328), so the pin is the row-source fixtures
# §5 names: the same feed with a ``None`` cell added encodes to the fixture's own bytes, and one with
# a nested cell encodes to them with the sentinel in that cell, which is also what this host's
# decoder normalises the raw cell to.

ROW_SOURCE_FIXTURES = ("chart-1", "chart-state-rows", "grid-1", "grid-editable-state")


def _row_feed_slot(document: dict[str, typing.Any]) -> str:
    return "value" if document["kind"]["source"]["$type"] == "Static" else "defaultValue"


def _row_feed_of(document: dict[str, typing.Any]) -> list[dict[str, object]]:
    return document["kind"]["source"][_row_feed_slot(document)]  # type: ignore[no-any-return]


def _with_row_feed(document: dict[str, typing.Any], rows: list[dict[str, object]]) -> dict[str, typing.Any]:
    source = {**document["kind"]["source"], _row_feed_slot(document): rows}
    return {**document, "kind": {**document["kind"], "source": source}}


def _fixture(name: str) -> dict[str, typing.Any]:
    return json.loads((CORPUS_ROOT / "nodes" / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


@pytest.mark.parametrize("fixture", ROW_SOURCE_FIXTURES)
def test_an_authored_none_cell_omits_its_key(fixture: str) -> None:
    rows = _row_feed_of(_fixture(fixture))
    assert t.encode_rows(rows) == encode_value(from_json(rows))
    assert t.encode_rows([{**row, "note": None} for row in rows]) == t.encode_rows(rows)


def test_chart_state_rows_is_authored_byte_identically_with_none_cells() -> None:
    expected = (CORPUS_ROOT / "nodes" / "chart-state-rows.json").read_text(encoding="utf-8").strip()
    rows: list[dict[str, object]] = [
        {"month": "Jan", "revenue": 980, "note": None},
        {"month": "Feb", "revenue": 1105, "note": None},
    ]
    chart = t.Chart(t.State("planRows", rows), "month", ("revenue",), "Bar")
    assert encode(t.UiNode("chart-state-rows", chart)) == expected


@pytest.mark.parametrize("fixture", ROW_SOURCE_FIXTURES)
@pytest.mark.parametrize("nested", [[1, 2], {"a": 1}, []], ids=["list", "dict", "empty-list"])
def test_an_authored_nested_cell_is_the_opaque_sentinel_the_decoder_also_writes(fixture: str, nested: object) -> None:
    document = _fixture(fixture)
    rows = _row_feed_of(document)
    authored = t.encode_rows([{**row, "note": nested} for row in rows])
    assert authored == encode_value(from_json([{**row, "note": t.OPAQUE} for row in rows]))
    decoded = decode_node(json.dumps(_with_row_feed(document, [{**row, "note": nested} for row in rows])))
    assert isinstance(decoded, Ok), decoded
    assert _row_feed_of(json.loads(encode_node(decoded.value))) == json.loads(authored)
