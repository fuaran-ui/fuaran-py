"""Grid edits as columnar ops (WIRE_FORMAT.md §3.2, "Edits as column ops — GridEdit").

A grid edit carried as the columnar op it is, beside the whole-rows write: the ``GridEdit``
codec, the op's application to a table (a typed refusal, never a raise), its inverse read from
the table it applies to, the row delta it induces under the grid's declared ``rowKeyField``, and
an immutable edit history (apply, undo, redo). A twin of the reference's columnar op algebra
(``Fuaran.Compute.ColumnOps``) and its server-driven edit log, certified against the corpus's
``grid-edit/`` family (Phase 2500).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Literal

from ...canonical import encode_value
from ...model import Arr, Obj, Value
from . import decimal_text
from .codec import decode_pipeline_json, encode_transform_value
from .evaluate import cell_token, eval_pipeline
from .model import (
    COLUMN_TYPES,
    DECIMAL,
    FLOAT,
    INT,
    NULL,
    TIMESTAMP,
    TIMESTAMP_UNIT_DIGITS,
    Cell,
    Column,
    Err,
    Ok,
    Result,
    Table,
    Transform,
    is_null,
)

# ── the op, the edit, the refusals ───────────────────────────────────────────


@dataclass(frozen=True)
class SetCell:
    col: str
    row: int
    value: Cell


@dataclass(frozen=True)
class SetColumn:
    column: Column


@dataclass(frozen=True)
class InsertColumn:
    index: int
    column: Column


@dataclass(frozen=True)
class RemoveColumn:
    name: str


@dataclass(frozen=True)
class AppendRows:
    rows: list[list[tuple[str, Cell]]]


@dataclass(frozen=True)
class ApplyTransform:
    pipeline: list[Transform]


ColumnOp = SetCell | SetColumn | InsertColumn | RemoveColumn | AppendRows | ApplyTransform


@dataclass(frozen=True)
class GridEdit:
    """A grid edit: the State key it commits to, the op, and the grid's declared row identity
    (``None`` — the member absent — means no identity is declared)."""

    key: str
    op: ColumnOp
    row_key_field: str | None = None


#: The refusal tokens the corpus names.
NO_SUCH_COLUMN = "noSuchColumn"
DUPLICATE_COLUMN = "duplicateColumn"
ROW_OUT_OF_RANGE = "rowOutOfRange"
CELL_TYPE_MISMATCH = "cellTypeMismatch"
COLUMN_LENGTH_MISMATCH = "columnLengthMismatch"
ROW_SHAPE_UNKNOWN_COLUMN = "rowShapeUnknownColumn"
TRANSFORM_REJECTED = "transformRejected"
NOT_INVERTIBLE = "notInvertible"
NOTHING_TO_UNDO = "nothingToUndo"
NOTHING_TO_REDO = "nothingToRedo"


@dataclass(frozen=True)
class Refusal:
    """Why an op (or a history gesture) was refused: the corpus token, and a reason."""

    token: str
    detail: str


# ── table helpers ────────────────────────────────────────────────────────────


def _row_count(t: Table) -> int:
    return len(t.columns[0].cells) if t.columns else 0


def _names(t: Table) -> str:
    return ", ".join(c.name for c in t.columns)


def _find(t: Table, name: str) -> Column | None:
    return next((c for c in t.columns if c.name == name), None)


def _instant_digits(text: str) -> int:
    dot = text.find(".")
    return 0 if dot < 0 else len(text) - dot - 2  # the digits between "." and the closing "Z"


def _fits(ty: str, c: Cell) -> bool:
    """A cell fits a column when it is null or of a type that widens into the column's: the
    type itself, an int into a float or a decimal column, a coarser instant into a finer unit."""
    if is_null(c) or c.kind == ty:
        return True
    if c.kind == INT:
        return ty in (FLOAT, DECIMAL)
    if c.kind == TIMESTAMP and ty in TIMESTAMP_UNIT_DIGITS:
        d = _instant_digits(str(c.value))
        unit = 0 if d == 0 else 3 if d <= 3 else 6 if d <= 6 else 9  # noqa: PLR2004
        return unit <= TIMESTAMP_UNIT_DIGITS[ty]
    return False


def _with_column(t: Table, col: Column) -> Table:
    """``t`` with the column named ``col.name`` replaced; a retyped column states no metadata."""
    retyped = any(n == col.name and ty != col.type for n, ty in t.schema)
    metadata = {k: v for k, v in t.metadata.items() if not (retyped and k == col.name)}
    return Table(
        [(n, col.type if n == col.name else ty) for n, ty in t.schema],
        [col if c.name == col.name else c for c in t.columns],
        metadata,
    )


def _refuse(token: str, detail: str) -> Err[Refusal]:
    return Err(Refusal(token, detail))


# ── apply / invert ───────────────────────────────────────────────────────────


def apply_op(op: ColumnOp, t: Table) -> Result[Table, Refusal]:  # noqa: C901, PLR0911
    """Apply a columnar op to a table — total: a typed refusal, never a raise."""
    if isinstance(op, SetCell):
        col = _find(t, op.col)
        if col is None:
            return _refuse(NO_SUCH_COLUMN, f"no such column '{op.col}'; available: {_names(t)}")
        rc = _row_count(t)
        if op.row < 0 or op.row >= rc:
            return _refuse(ROW_OUT_OF_RANGE, f"row {op.row} out of range (row count {rc})")
        if not _fits(col.type, op.value):
            return _refuse(CELL_TYPE_MISMATCH, f"column '{op.col}': expected {col.type} cell")
        cells = list(col.cells)
        cells[op.row] = op.value
        return Ok(_with_column(t, Column(col.name, col.type, cells)))
    if isinstance(op, SetColumn):
        if _find(t, op.column.name) is None:
            return _refuse(NO_SUCH_COLUMN, f"no such column '{op.column.name}'; available: {_names(t)}")
        rc, n = _row_count(t), len(op.column.cells)
        if n != rc:
            return _refuse(COLUMN_LENGTH_MISMATCH, f"column '{op.column.name}': length {n} != row count {rc}")
        return Ok(_with_column(t, op.column))
    if isinstance(op, InsertColumn):
        if _find(t, op.column.name) is not None:
            return _refuse(DUPLICATE_COLUMN, f"duplicate column: {op.column.name}")
        rc, n = _row_count(t), len(op.column.cells)
        if t.columns and n != rc:
            return _refuse(COLUMN_LENGTH_MISMATCH, f"column '{op.column.name}': length {n} != row count {rc}")
        i = max(0, min(op.index, len(t.columns)))
        schema = [*t.schema[:i], (op.column.name, op.column.type), *t.schema[i:]]
        return Ok(Table(schema, [*t.columns[:i], op.column, *t.columns[i:]], dict(t.metadata)))
    if isinstance(op, RemoveColumn):
        if _find(t, op.name) is None:
            return _refuse(NO_SUCH_COLUMN, f"no such column '{op.name}'; available: {_names(t)}")
        return Ok(
            Table(
                [(n, ty) for n, ty in t.schema if n != op.name],
                [c for c in t.columns if c.name != op.name],
                {k: v for k, v in t.metadata.items() if k != op.name},
            )
        )
    if isinstance(op, AppendRows):
        for row in op.rows:
            for name, value in row:
                col = _find(t, name)
                if col is None:
                    return _refuse(
                        ROW_SHAPE_UNKNOWN_COLUMN, f"row references unknown column '{name}'; available: {_names(t)}"
                    )
                if not _fits(col.type, value):
                    return _refuse(CELL_TYPE_MISMATCH, f"column '{name}': expected {col.type} cell")
        columns = [
            Column(
                col.name,
                col.type,
                [*col.cells, *(next((v for n, v in row if n == col.name), NULL) for row in op.rows)],
            )
            for col in t.columns
        ]
        return Ok(Table(t.schema, columns, dict(t.metadata)))
    r = eval_pipeline(op.pipeline, t)
    return Ok(r.value) if isinstance(r, Ok) else _refuse(TRANSFORM_REJECTED, r.error.detail)


def invert_op(op: ColumnOp, t: Table) -> Result[ColumnOp, Refusal]:
    """The op that undoes ``op`` applied to the PRE-state ``t``. An op the table would refuse has
    no inverse (its refusal is returned); ``AppendRows`` and ``ApplyTransform`` have none."""
    if isinstance(op, AppendRows | ApplyTransform):
        return _refuse(NOT_INVERTIBLE, f"{type(op).__name__} is not invertible")
    applied = apply_op(op, t)
    if isinstance(applied, Err):
        return applied
    if isinstance(op, SetCell):
        col = _find(t, op.col)
        assert col is not None  # noqa: S101 — the apply above found it
        return Ok(SetCell(op.col, op.row, col.cells[op.row]))
    if isinstance(op, SetColumn):
        old = _find(t, op.column.name)
        assert old is not None  # noqa: S101
        return Ok(SetColumn(old))
    if isinstance(op, InsertColumn):
        return Ok(RemoveColumn(op.column.name))
    index = next(i for i, c in enumerate(t.columns) if c.name == op.name)
    return Ok(InsertColumn(index, t.columns[index]))


# ── the row delta ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FullRefresh:
    """The top delta: the schema or the whole table moved, or rows have no identity."""


@dataclass(frozen=True)
class RowSet:
    """The rows a change touched, by key, under ``scheme`` — rows in ordinal key order."""

    scheme: str
    rows: list[tuple[str, Literal["added", "changed", "removed"]]] = field(default_factory=list)


TableDelta = FullRefresh | RowSet


def _keys(t: Table, key_field: str) -> list[str | None]:
    col = _find(t, key_field)
    n = _row_count(t)
    if col is None:
        return [None] * n
    return [None if is_null(c) else cell_token(c) for c in col.cells]


def _same(a: Cell, b: Cell) -> bool:
    """Cell equality as the reference decides "unchanged": a NaN never equals itself."""
    if a.kind != b.kind:
        return False
    if a.kind == FLOAT and (math.isnan(float(a.value)) or math.isnan(float(b.value))):  # type: ignore[arg-type]
        return False
    return a.value == b.value


def _row_set(scheme: str, rows: list[tuple[str, Literal["added", "changed", "removed"]]]) -> RowSet:
    return RowSet(scheme, sorted(rows, key=lambda r: r[0].encode("utf-16-be")))


def delta_of(key_field: str, before: Table, op: ColumnOp) -> TableDelta:  # noqa: PLR0911
    """The row delta ``op`` induces on ``before`` under identity by the column ``key_field``
    (scheme ``column:<field>``), built from the op and the before table, never by diffing two
    tables. A schema or whole-table op, an op that does not apply, and a row with no key are the
    top delta."""
    scheme = "column:" + key_field
    if isinstance(op, InsertColumn | RemoveColumn | ApplyTransform):
        return FullRefresh()
    applied = apply_op(op, before)
    if isinstance(applied, Err):
        return FullRefresh()
    k0, k1 = _keys(before, key_field), _keys(applied.value, key_field)
    if isinstance(op, SetCell):
        a, b = k0[op.row], k1[op.row]
        if a is None or b is None:
            return FullRefresh()
        old = _find(before, op.col)
        if old is not None and _same(old.cells[op.row], op.value):
            return RowSet(scheme)
        return _row_set(scheme, [(a, "changed")] if a == b else [(a, "removed"), (b, "added")])
    if isinstance(op, SetColumn):
        if k0 != k1 or any(k is None for k in k0):
            return FullRefresh()
        old = _find(before, op.column.name)
        assert old is not None  # noqa: S101
        return _row_set(
            scheme,
            [(k, "changed") for k, x, y in zip(k0, old.cells, op.column.cells, strict=True) if not _same(x, y)],  # type: ignore[misc]
        )
    added = k1[len(k0) :]
    existing = {k for k in k0 if k is not None}
    fresh = all(k is not None for k in added) and len(set(added)) == len(added) and not (existing & set(added))
    return _row_set(scheme, [(k, "added") for k in added]) if fresh else FullRefresh()  # type: ignore[misc]


# ── the codecs ───────────────────────────────────────────────────────────────


def _cell_value(c: Cell) -> Obj:
    """A cell in the columnar op codec's form: ``{"$type":"Int","v":21}``."""
    if is_null(c):
        return Obj("Null", {})
    tag = {"int": "Int", "float": "Float", "bool": "Bool", "string": "Str", "date": "Date"}.get(c.kind)
    if c.kind == DECIMAL:
        return Obj("Decimal", {"v": decimal_text.try_canonical(str(c.value)) or str(c.value)})
    if c.kind == TIMESTAMP:
        return Obj("Timestamp", {"v": str(c.value)})
    if c.kind == FLOAT:
        return Obj("Float", {"v": float(c.value)})  # type: ignore[arg-type]
    return Obj(tag, {"v": c.value})


def _column_value(c: Column) -> Obj:
    return Obj(None, {"name": c.name, "type": c.type, "cells": Arr([_cell_value(x) for x in c.cells])})


def op_value(op: ColumnOp) -> Obj:
    """A columnar op in the structural model the canonical encoder writes."""
    if isinstance(op, SetCell):
        return Obj("setCell", {"col": op.col, "row": op.row, "value": _cell_value(op.value)})
    if isinstance(op, SetColumn):
        return Obj("setColumn", {"column": _column_value(op.column)})
    if isinstance(op, InsertColumn):
        return Obj("insertColumn", {"index": op.index, "column": _column_value(op.column)})
    if isinstance(op, RemoveColumn):
        return Obj("removeColumn", {"name": op.name})
    if isinstance(op, AppendRows):
        rows: list[Value] = [Arr([Obj(None, {"col": n, "value": _cell_value(v)}) for n, v in row]) for row in op.rows]
        return Obj("appendRows", {"rows": Arr(rows)})
    return Obj("applyTransform", {"pipeline": Arr([encode_transform_value(s) for s in op.pipeline])})


def encode_op(op: ColumnOp) -> str:
    """The canonical wire string for a columnar op."""
    return encode_value(op_value(op))


def encode_grid_edit(edit: GridEdit) -> str:
    """The canonical wire string for a grid edit; an undeclared ``rowKeyField`` is omitted."""
    fields: dict[str, Value] = {"key": edit.key, "op": op_value(edit.op)}
    if edit.row_key_field is not None:
        fields["rowKeyField"] = edit.row_key_field
    return encode_value(Obj("GridEdit", fields))


def encode_delta(d: TableDelta) -> str:
    """The canonical wire string for a row delta."""
    if isinstance(d, FullRefresh):
        return encode_value(Obj("fullRefresh", {}))
    rows: list[Value] = [Obj(change, {"key": key}) for key, change in d.rows]
    return encode_value(Obj("rowSet", {"scheme": d.scheme, "rows": Arr(rows), "columns": Arr([])}))


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _decode_cell(el: object) -> Result[Cell, str]:  # noqa: PLR0911
    if not isinstance(el, dict):
        return Err("cell: expected an object")
    kind = el.get("$type")
    if kind == "Null":
        return Ok(NULL)
    if "v" not in el:
        return Err("cell: missing field: v")
    v = el["v"]
    if kind == "Int":
        return Ok(Cell(INT, v)) if _is_int(v) and -(2**31) <= v < 2**31 else Err("cell: expected int")
    if kind == "Float":
        return (
            Ok(Cell(FLOAT, float(v)))
            if isinstance(v, int | float) and not isinstance(v, bool)
            else Err("cell: expected float")
        )
    if kind == "Bool":
        return Ok(Cell("bool", v)) if isinstance(v, bool) else Err("cell: expected bool")
    if kind in ("Str", "Date", "Timestamp"):
        ty = {"Str": "string", "Date": "date", "Timestamp": TIMESTAMP}[kind]
        return Ok(Cell(ty, v)) if isinstance(v, str) else Err("cell: expected string")
    if kind == "Decimal":
        text = decimal_text.try_canonical(v) if isinstance(v, str) else str(v) if _is_int(v) else None
        return Ok(Cell(DECIMAL, text)) if text is not None else Err("cell: not decimal text")
    return Err(f"unknown cell kind: {kind}")


def _decode_column(el: object) -> Result[Column, str]:
    if not isinstance(el, dict) or not isinstance(el.get("name"), str) or not isinstance(el.get("type"), str):
        return Err("column: expected {name, type, cells}")
    name, ty, cells_el = el["name"], el["type"], el.get("cells")
    if ty not in COLUMN_TYPES:
        return Err(f"unknown column type: {ty}")
    if not isinstance(cells_el, list):
        return Err("column: cells is an array")
    cells: list[Cell] = []
    for c in cells_el:
        r = _decode_cell(c)
        if isinstance(r, Err):
            return r
        if not _fits(ty, r.value):
            return Err(f"column '{name}': a {r.value.kind} cell in a {ty} column")
        cells.append(r.value)
    return Ok(Column(name, ty, cells))


def decode_op_json(el: object) -> Result[ColumnOp, str]:  # noqa: C901, PLR0911, PLR0912
    """Decode a columnar op from parsed JSON."""
    if not isinstance(el, dict):
        return Err("op: expected an object")
    kind = el.get("$type")
    if kind == "setCell":
        col, row, value = el.get("col"), el.get("row"), el.get("value")
        if not isinstance(col, str) or not _is_int(row) or value is None:
            return Err("setCell: expected {col, row, value}")
        cell = _decode_cell(value)
        return Ok(SetCell(col, row, cell.value)) if isinstance(cell, Ok) else cell  # type: ignore[arg-type]
    if kind in ("setColumn", "insertColumn"):
        column = _decode_column(el.get("column"))
        if isinstance(column, Err):
            return column
        if kind == "setColumn":
            return Ok(SetColumn(column.value))
        index = el.get("index")
        return Ok(InsertColumn(index, column.value)) if _is_int(index) else Err("insertColumn: expected an int index")  # type: ignore[arg-type]
    if kind == "removeColumn":
        name = el.get("name")
        return Ok(RemoveColumn(name)) if isinstance(name, str) else Err("removeColumn: expected a name")
    if kind == "appendRows":
        rows_el = el.get("rows")
        if not isinstance(rows_el, list):
            return Err("appendRows: expected a rows array")
        rows: list[list[tuple[str, Cell]]] = []
        for row_el in rows_el:
            if not isinstance(row_el, list):
                return Err("appendRows: a row is an array")
            cells: list[tuple[str, Cell]] = []
            for cell_el in row_el:
                if not isinstance(cell_el, dict) or not isinstance(cell_el.get("col"), str) or "value" not in cell_el:
                    return Err("appendRows: expected {col, value}")
                cell = _decode_cell(cell_el["value"])
                if isinstance(cell, Err):
                    return cell
                cells.append((cell_el["col"], cell.value))
            rows.append(cells)
        return Ok(AppendRows(rows))
    if kind == "applyTransform":
        if "pipeline" not in el:
            return Err("applyTransform: missing field: pipeline")
        pipeline = decode_pipeline_json(el["pipeline"])
        return Ok(ApplyTransform(pipeline.value)) if isinstance(pipeline, Ok) else Err(pipeline.error.detail)
    return Err(f"unknown columnar op: {kind}")


_MEMBERS = frozenset({"$type", "key", "op", "rowKeyField"})


def decode_grid_edit_json(el: object) -> Result[GridEdit, str]:  # noqa: PLR0911
    """Decode a ``GridEdit`` from parsed JSON. STRICT: a member outside the four, an empty
    ``key`` or ``rowKeyField``, or an op the codec does not know is refused, because an edit
    applied differently from the one sent is worse than one refused."""
    if not isinstance(el, dict):
        return Err("GridEdit: expected an object")
    unknown = sorted(set(el) - _MEMBERS)
    if unknown:
        return Err(f"GridEdit: unknown member '{unknown[0]}'")
    if el.get("$type") != "GridEdit":
        return Err('GridEdit: $type must be "GridEdit"')
    key = el.get("key")
    if not isinstance(key, str) or key == "":
        return Err("GridEdit: key must be a non-empty string")
    if "op" not in el:
        return Err("GridEdit: missing op")
    rk = el.get("rowKeyField")
    if "rowKeyField" in el and (not isinstance(rk, str) or rk == ""):
        return Err("GridEdit: rowKeyField must be a non-empty string")
    op = decode_op_json(el["op"])
    if isinstance(op, Err):
        return Err("GridEdit: op: " + op.error)
    return Ok(GridEdit(key, op.value, rk))  # type: ignore[arg-type]


def decode_grid_edit(text: str) -> Result[GridEdit, str]:
    """Decode a ``GridEdit`` from its wire text."""
    try:
        el = json.loads(text)
    except ValueError:
        return Err("GridEdit: not json")
    return decode_grid_edit_json(el)


# ── the edit history ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GridEditHistory:
    """An edit history over one table. Immutable: every gesture returns a new history. An edit's
    inverse is read from the table it is applied to; an edit with none (``AppendRows``,
    ``ApplyTransform``) applies and clears what could be undone; any new edit clears what could
    be redone. Bounded: past ``capacity`` edits the oldest is no longer undoable."""

    table: Table
    capacity: int = 100
    undo_stack: tuple[tuple[ColumnOp, ColumnOp], ...] = ()
    redo_stack: tuple[tuple[ColumnOp, ColumnOp], ...] = ()

    @property
    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    def apply(self, op: ColumnOp) -> Result[GridEditHistory, Refusal]:
        applied = apply_op(op, self.table)
        if isinstance(applied, Err):
            return applied
        inverse = invert_op(op, self.table)
        undo = ((op, inverse.value), *self.undo_stack)[: max(1, self.capacity)] if isinstance(inverse, Ok) else ()
        return Ok(GridEditHistory(applied.value, self.capacity, undo, ()))

    def undo(self) -> Result[GridEditHistory, Refusal]:
        if not self.undo_stack:
            return _refuse(NOTHING_TO_UNDO, "nothing to undo")
        top, rest = self.undo_stack[0], self.undo_stack[1:]
        applied = apply_op(top[1], self.table)
        if isinstance(applied, Err):
            return applied
        return Ok(GridEditHistory(applied.value, self.capacity, rest, (top, *self.redo_stack)))

    def redo(self) -> Result[GridEditHistory, Refusal]:
        if not self.redo_stack:
            return _refuse(NOTHING_TO_REDO, "nothing to redo")
        top, rest = self.redo_stack[0], self.redo_stack[1:]
        applied = apply_op(top[0], self.table)
        if isinstance(applied, Err):
            return applied
        undo = (top, *self.undo_stack)[: max(1, self.capacity)]
        return Ok(GridEditHistory(applied.value, self.capacity, undo, rest))


__all__ = [
    "CELL_TYPE_MISMATCH",
    "COLUMN_LENGTH_MISMATCH",
    "DUPLICATE_COLUMN",
    "NOTHING_TO_REDO",
    "NOTHING_TO_UNDO",
    "NOT_INVERTIBLE",
    "NO_SUCH_COLUMN",
    "ROW_OUT_OF_RANGE",
    "ROW_SHAPE_UNKNOWN_COLUMN",
    "TRANSFORM_REJECTED",
    "AppendRows",
    "ApplyTransform",
    "ColumnOp",
    "FullRefresh",
    "GridEdit",
    "GridEditHistory",
    "InsertColumn",
    "Refusal",
    "RemoveColumn",
    "RowSet",
    "SetCell",
    "SetColumn",
    "TableDelta",
    "apply_op",
    "decode_grid_edit",
    "decode_grid_edit_json",
    "decode_op_json",
    "delta_of",
    "encode_delta",
    "encode_grid_edit",
    "encode_op",
    "invert_op",
    "op_value",
]
