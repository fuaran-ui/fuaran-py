"""The pure columnar reference evaluator — the Python leg of cross-host parity.

A faithful port of the canonical dataframe reference evaluator: a fold over the
pipeline threading a row-oriented working frame, with **pinned** semantics that every
host evaluator must reproduce byte-for-byte (certified by the F#-generated parity
fixtures):

* null/NA **propagates** through arithmetic + comparison (any null operand ⇒ null);
  the logical pair is three-valued (Kleene);
* ``int + int`` stays ``int``; any ``float`` operand widens the result to ``float``;
* sort is **stable** and **nulls sort last** regardless of direction;
* groups appear in **first-appearance** order; ``Count`` is the **non-null** count;
* ``Round`` is **round-half-away-from-zero** (never Python's banker's ``round``);
* division by zero ⇒ ``null`` (not an error);
* floats canonicalise through the shared cross-host layout (via the codec).

Totality (GP4): every verb returns ``Ok``/``Err`` and never throws on a malformed
pipeline — an unknown column / type error / arity error is a named ``EvalError``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import cmp_to_key

from ..canonical import format_finite_double
from .model import (
    BOOL,
    FLOAT,
    INT,
    STRING,
    UNBOUND_PARAM,
    UNKNOWN_COLUMN,
    UNRESOLVED_SOURCE,
    Agg,
    ApplyFn,
    Binary,
    Case,
    Cast,
    Cell,
    Coalesce,
    Col,
    ColExpr,
    Column,
    Derive,
    Distinct,
    Embedded,
    Err,
    EvalError,
    Filter,
    GroupBy,
    Join,
    Limit,
    Lit,
    Not,
    Ok,
    Param,
    Pivot,
    Project,
    Ref,
    Result,
    Schema,
    Sort,
    Table,
    Transform,
    Union,
    Unpivot,
    Window,
    WindowSpec,
    cell_bool,
    cell_date,
    cell_float,
    cell_int,
    cell_str,
    cell_timestamp,
    is_null,
    type_of,
)

# A row is a list of cells co-indexed with the frame's columns.
Row = list[Cell]
NULL = Cell("null", None)


# ── the working frame (row-oriented) ─────────────────────────────────────────


class Frame:
    __slots__ = ("cols", "rows")

    def __init__(self, cols: Schema, rows: list[Row]) -> None:
        self.cols = cols
        self.rows = rows


def _to_frame(t: Table) -> Frame:
    by_name = {c.name: c for c in t.columns}
    n = len(t.columns[0].cells) if t.columns else 0
    rows: list[Row] = []
    for i in range(n):
        row: Row = []
        for name, _ in t.schema:
            c = by_name.get(name)
            row.append(c.cells[i] if c is not None and i < len(c.cells) else NULL)
        rows.append(row)
    return Frame(list(t.schema), rows)


def _of_frame(f: Frame) -> Table:
    columns: list[Column] = []
    for ci, (name, ty) in enumerate(f.cols):
        columns.append(Column(name, ty, [row[ci] for row in f.rows]))
    return Table(list(f.cols), columns)


def _col_index(cols: Schema, name: str) -> int | None:
    for i, (n, _) in enumerate(cols):
        if n == name:
            return i
    return None


def _col_type(cols: Schema, name: str) -> str | None:
    for n, ty in cols:
        if n == name:
            return ty
    return None


def _available(cols: Schema) -> list[str]:
    return [n for n, _ in cols]


def _unknown(name: str, cols: Schema) -> Err[EvalError]:
    return Err(EvalError(UNKNOWN_COLUMN, f"unknown column '{name}'; available: {', '.join(_available(cols))}"))


# ── pinned scalar semantics ──────────────────────────────────────────────────


def cell_string(c: Cell) -> str:
    """The canonical string of a cell (float via the shared cross-host layout)."""
    if c.kind == INT:
        return str(c.value)
    if c.kind == FLOAT:
        return format_finite_double(float(c.value))  # type: ignore[arg-type]
    if c.kind == BOOL:
        return "true" if c.value else "false"
    if c.kind in (STRING, "date", "timestamp"):
        return str(c.value)
    return ""  # null


def _as_num(c: Cell) -> float | None:
    if c.kind == INT:
        return float(c.value)  # type: ignore[arg-type]
    if c.kind == FLOAT:
        return float(c.value)  # type: ignore[arg-type]
    return None


def _compare_cells(a: Cell, b: Cell) -> int | None:
    """Total comparison between two present, same-family cells (``None`` ⇒ incomparable)."""
    an, bn = _as_num(a), _as_num(b)
    if an is not None and bn is not None:
        return (an > bn) - (an < bn)
    if a.kind == BOOL and b.kind == BOOL:
        return (a.value > b.value) - (a.value < b.value)  # type: ignore[operator]
    if a.kind == b.kind and a.kind in (STRING, "date", "timestamp"):
        return (a.value > b.value) - (a.value < b.value)  # type: ignore[operator]
    return None


def _cell_eq(a: Cell, b: Cell) -> bool:
    if is_null(a) or is_null(b):
        return False
    return _compare_cells(a, b) == 0


def _arith(op: str, a: Cell, b: Cell) -> Result[Cell, EvalError]:
    if is_null(a) or is_null(b):
        return Ok(NULL)
    x, y = _as_num(a), _as_num(b)
    if x is None or y is None:
        return Err(EvalError("TYPE_ERROR", "arithmetic on a non-numeric operand"))
    both_int = a.kind == INT and b.kind == INT
    if op == "add":
        return Ok(cell_int(int(x) + int(y)) if both_int else cell_float(x + y))
    if op == "sub":
        return Ok(cell_int(int(x) - int(y)) if both_int else cell_float(x - y))
    if op == "mul":
        return Ok(cell_int(int(x) * int(y)) if both_int else cell_float(x * y))
    if op == "div":
        return Ok(NULL if y == 0.0 else cell_float(x / y))
    if op == "mod":
        if not both_int:
            return Err(EvalError("TYPE_ERROR", "mod requires integer operands"))
        if int(y) == 0:
            return Ok(NULL)
        return Ok(cell_int(int(x) % int(y)))
    return Err(EvalError("TYPE_ERROR", "not an arithmetic operator"))


def _comparison(op: str, a: Cell, b: Cell) -> Result[Cell, EvalError]:
    if is_null(a) or is_null(b):
        return Ok(NULL)
    c = _compare_cells(a, b)
    if c is None:
        return Err(EvalError("TYPE_ERROR", "comparison between incompatible types"))
    r = {
        "eq": c == 0,
        "ne": c != 0,
        "lt": c < 0,
        "le": c <= 0,
        "gt": c > 0,
        "ge": c >= 0,
    }.get(op, False)
    return Ok(cell_bool(r))


def _logical(op: str, a: Cell, b: Cell) -> Result[Cell, EvalError]:
    def as_bool(c: Cell) -> Result[bool | None, EvalError]:
        if c.kind == BOOL:
            return Ok(bool(c.value))
        if is_null(c):
            return Ok(None)
        return Err(EvalError("TYPE_ERROR", "logical operator on a non-bool operand"))

    ra, rb = as_bool(a), as_bool(b)
    if not ra.ok:
        return ra  # type: ignore[return-value]
    if not rb.ok:
        return rb  # type: ignore[return-value]
    x, y = ra.value, rb.value
    if op == "and":
        if x is False or y is False:
            return Ok(cell_bool(False))
        if x is True and y is True:
            return Ok(cell_bool(True))
        return Ok(NULL)
    if op == "or":
        if x is True or y is True:
            return Ok(cell_bool(True))
        if x is False and y is False:
            return Ok(cell_bool(False))
        return Ok(NULL)
    return Err(EvalError("TYPE_ERROR", "not a logical operator"))


def _cast_cell(ty: str, c: Cell) -> Result[Cell, EvalError]:
    if is_null(c):
        return Ok(NULL)
    if ty == STRING:
        return Ok(cell_str(cell_string(c)))
    if ty == FLOAT:
        n = _as_num(c)
        if n is not None:
            return Ok(cell_float(n))
        if c.kind == STRING:
            try:
                return Ok(cell_float(float(c.value)))  # type: ignore[arg-type]
            except ValueError:
                return Err(EvalError("TYPE_ERROR", f"cannot cast '{c.value}' to float"))
        return Err(EvalError("TYPE_ERROR", "cannot cast to float"))
    if ty == INT:
        if c.kind == INT:
            return Ok(c)
        if c.kind == FLOAT:
            return Ok(cell_int(int(c.value)))  # type: ignore[arg-type]
        if c.kind == BOOL:
            return Ok(cell_int(1 if c.value else 0))
        if c.kind == STRING:
            try:
                return Ok(cell_int(int(c.value)))  # type: ignore[arg-type]
            except ValueError:
                return Err(EvalError("TYPE_ERROR", f"cannot cast '{c.value}' to int"))
        return Err(EvalError("TYPE_ERROR", "cannot cast to int"))
    if ty == BOOL:
        if c.kind == BOOL:
            return Ok(c)
        if c.kind == INT:
            return Ok(cell_bool(c.value != 0))
        return Err(EvalError("TYPE_ERROR", "cannot cast to bool"))
    if ty == "date":
        if c.kind == "date":
            return Ok(c)
        if c.kind == STRING:
            return Ok(cell_date(str(c.value)))
        return Err(EvalError("TYPE_ERROR", "cannot cast to date"))
    if ty == "timestamp":
        if c.kind == "timestamp":
            return Ok(c)
        if c.kind == STRING:
            return Ok(cell_timestamp(str(c.value)))
        return Err(EvalError("TYPE_ERROR", "cannot cast to timestamp"))
    return Err(EvalError("TYPE_ERROR", f"unknown cast type {ty}"))


def _round_half_away(x: float) -> float:
    """Round half away from zero — host-independent (not banker's rounding)."""
    return math.floor(x + 0.5) if x >= 0.0 else math.ceil(x - 0.5)


def _apply_scalar(fn: str, args: list[Cell]) -> Result[Cell, EvalError]:  # noqa: C901, PLR0911, PLR0912
    def arity(n: int) -> Err[EvalError] | None:
        if len(args) != n:
            return Err(EvalError("ARITY_ERROR", f"function '{fn}' expects {n} args, got {len(args)}"))
        return None

    if fn in ("abs", "round", "floor", "ceil", "length", "lower", "upper"):
        a = arity(1)
        if a is not None:
            return a
        c = args[0]
        if is_null(c):
            return Ok(NULL)
        if fn == "abs":
            if c.kind == INT:
                return Ok(cell_int(abs(c.value)))  # type: ignore[arg-type]
            if c.kind == FLOAT:
                return Ok(cell_float(abs(c.value)))  # type: ignore[arg-type]
            return Err(EvalError("TYPE_ERROR", "abs of a non-numeric"))
        if fn in ("round", "floor", "ceil"):
            n = _as_num(c)
            if n is None:
                return Err(EvalError("TYPE_ERROR", f"{fn} of a non-numeric"))
            if fn == "round":
                return Ok(cell_float(_round_half_away(n)))
            if fn == "floor":
                return Ok(cell_float(math.floor(n)))
            return Ok(cell_float(math.ceil(n)))
        if c.kind != STRING:
            return Err(EvalError("TYPE_ERROR", f"{fn} of a non-string"))
        if fn == "length":
            return Ok(cell_int(len(c.value)))  # type: ignore[arg-type]
        if fn == "lower":
            return Ok(cell_str(c.value.lower()))  # type: ignore[union-attr]
        return Ok(cell_str(c.value.upper()))  # type: ignore[union-attr]
    if fn == "substr":
        a = arity(3)
        if a is not None:
            return a
        s, start, length = args
        if is_null(s):
            return Ok(NULL)
        sv, stv, lnv = s.value, start.value, length.value
        if isinstance(sv, str) and isinstance(stv, int) and isinstance(lnv, int):
            st = max(0, min(stv, len(sv)))
            ln = max(0, min(lnv, len(sv) - st))
            return Ok(cell_str(sv[st : st + ln]))
        return Err(EvalError("TYPE_ERROR", "substr expects (string, int, int)"))
    if fn == "datePart":
        a = arity(2)
        if a is not None:
            return a
        part, val = args
        if is_null(val):
            return Ok(NULL)
        if part.kind == STRING and val.kind in (STRING, "date", "timestamp"):
            ds = str(val.value)
            spans = {"year": (0, 4), "month": (5, 2), "day": (8, 2)}
            span = spans.get(str(part.value))
            if span is None:
                return Err(EvalError("TYPE_ERROR", f"datePart: unknown part '{part.value}'"))
            lo, ln = span
            if len(ds) >= lo + ln:
                try:
                    return Ok(cell_int(int(ds[lo : lo + ln])))
                except ValueError:
                    return Err(EvalError("TYPE_ERROR", f"datePart: unparseable component in '{ds}'"))
            return Err(EvalError("TYPE_ERROR", f"datePart: '{ds}' too short for {part.value}"))
        return Err(EvalError("TYPE_ERROR", "datePart expects (string part, date/timestamp/string)"))
    return Err(EvalError("TYPE_ERROR", f"unknown scalar fn {fn}"))


def _eval_expr(cols: Schema, row: Row, e: ColExpr) -> Result[Cell, EvalError]:  # noqa: C901, PLR0911, PLR0912
    if isinstance(e, Col):
        i = _col_index(cols, e.name)
        return Ok(row[i]) if i is not None else _unknown(e.name, cols)
    if isinstance(e, Lit):
        return Ok(e.cell)
    if isinstance(e, Binary):
        ra = _eval_expr(cols, row, e.left)
        if not ra.ok:
            return ra
        rb = _eval_expr(cols, row, e.right)
        if not rb.ok:
            return rb
        if e.op in ("add", "sub", "mul", "div", "mod"):
            return _arith(e.op, ra.value, rb.value)
        if e.op in ("eq", "ne", "lt", "le", "gt", "ge"):
            return _comparison(e.op, ra.value, rb.value)
        return _logical(e.op, ra.value, rb.value)
    if isinstance(e, Not):
        r = _eval_expr(cols, row, e.expr)
        if not r.ok:
            return r
        c = r.value
        if c.kind == BOOL:
            return Ok(cell_bool(not c.value))
        if is_null(c):
            return Ok(NULL)
        return Err(EvalError("TYPE_ERROR", "not of a non-bool"))
    if isinstance(e, Coalesce):
        for x in e.exprs:
            r = _eval_expr(cols, row, x)
            if not r.ok:
                return r
            if not is_null(r.value):
                return Ok(r.value)
        return Ok(NULL)
    if isinstance(e, Case):
        for when_e, then_e in e.cases:
            rw = _eval_expr(cols, row, when_e)
            if not rw.ok:
                return rw
            if rw.value.kind == BOOL and rw.value.value is True:
                return _eval_expr(cols, row, then_e)
        return _eval_expr(cols, row, e.else_expr)
    if isinstance(e, Cast):
        r = _eval_expr(cols, row, e.expr)
        return r if not r.ok else _cast_cell(e.type, r.value)
    if isinstance(e, ApplyFn):
        argv: list[Cell] = []
        for a in e.args:
            r = _eval_expr(cols, row, a)
            if not r.ok:
                return r
            argv.append(r.value)
        return _apply_scalar(e.fn, argv)
    if isinstance(e, Param):
        # Params are substituted from the host env before evaluation (see the compute
        # layer); one reaching the evaluator is genuinely unbound — a named failure.
        return Err(EvalError(UNBOUND_PARAM, f"unbound parameter '{e.name}'"))
    return Err(EvalError("TYPE_ERROR", f"unknown ColExpr {type(e)!r}"))


# ── type inference / aggregates ──────────────────────────────────────────────


def _infer_type(cells: list[Cell]) -> str:
    for c in cells:
        t = type_of(c)
        if t is not None:
            return t
    return STRING


def _present_nums(cells: list[Cell]) -> list[float]:
    return [n for n in (_as_num(c) for c in cells) if n is not None]


def _agg_cells(fn: str, src_type: str, cells: list[Cell]) -> Cell:  # noqa: C901, PLR0911
    present = [c for c in cells if not is_null(c)]
    if fn == "count":
        return cell_int(len(present))
    if fn == "first":
        return cells[0] if cells else NULL
    if fn == "last":
        return cells[-1] if cells else NULL
    if fn == "sum":
        nums = _present_nums(cells)
        if not nums:
            return NULL
        if src_type == INT:
            return cell_int(sum(int(n) for n in nums))
        return cell_float(sum(nums))
    if fn == "mean":
        nums = _present_nums(cells)
        return NULL if not nums else cell_float(sum(nums) / len(nums))
    if fn == "stddev":
        nums = _present_nums(cells)
        if not nums:
            return NULL
        n = len(nums)
        mean = sum(nums) / n
        var = sum((x - mean) * (x - mean) for x in nums) / n
        return cell_float(math.sqrt(var))
    if fn == "median":
        nums = sorted(_present_nums(cells))
        if not nums:
            return NULL
        n = len(nums)
        mid = n // 2
        if n % 2 == 1:
            return cell_float(nums[mid])
        return cell_float((nums[mid - 1] + nums[mid]) / 2.0)
    # min / max
    if not present:
        return NULL
    is_min = fn == "min"
    acc = present[0]
    for b in present[1:]:
        c = _compare_cells(acc, b)
        if c is not None and not (is_min == (c <= 0)):
            acc = b
    return acc


def _agg_type(fn: str, src_type: str) -> str:
    if fn == "count":
        return INT
    if fn in ("mean", "median", "stddev"):
        return FLOAT
    return src_type


# ── sort comparator (stable; nulls last regardless of direction) ─────────────


def _row_key_compare(cols: Schema, by: list[tuple[str, str]], r1: Row, r2: Row) -> int:
    for name, direction in by:
        i = _col_index(cols, name)
        if i is None:
            continue
        a, b = r1[i], r2[i]
        an, bn = is_null(a), is_null(b)
        if an and bn:
            c = 0
        elif an:
            c = 1  # null sorts last
        elif bn:
            c = -1
        else:
            cc = _compare_cells(a, b)
            c = 0 if cc is None else (cc if direction == "asc" else -cc)
        if c != 0:
            return c
    return 0


# ── per-verb evaluation ──────────────────────────────────────────────────────


def _eval_filter(f: Frame, pred: ColExpr) -> Result[Frame, EvalError]:
    kept: list[Row] = []
    for row in f.rows:
        r = _eval_expr(f.cols, row, pred)
        if not r.ok:
            return r  # type: ignore[return-value]
        if r.value.kind == BOOL and r.value.value is True:
            kept.append(row)
    return Ok(Frame(f.cols, kept))


def _eval_project(f: Frame, pairs: list[tuple[str, str]]) -> Result[Frame, EvalError]:
    resolved: list[tuple[str, str, int]] = []
    for src, out in pairs:
        i = _col_index(f.cols, src)
        if i is None:
            return _unknown(src, f.cols)
        resolved.append((out, f.cols[i][1], i))
    cols: Schema = [(o, ty) for o, ty, _ in resolved]
    rows = [[row[i] for _, _, i in resolved] for row in f.rows]
    return Ok(Frame(cols, rows))


def _eval_derive(f: Frame, name: str, expr: ColExpr) -> Result[Frame, EvalError]:
    new_cells: list[Cell] = []
    for row in f.rows:
        r = _eval_expr(f.cols, row, expr)
        if not r.ok:
            return r  # type: ignore[return-value]
        new_cells.append(r.value)
    ty = _infer_type(new_cells)
    i = _col_index(f.cols, name)
    if i is not None:
        cols = [(n, ty if j == i else t) for j, (n, t) in enumerate(f.cols)]
        rows = [[c if j == i else cell for j, cell in enumerate(row)] for row, c in zip(f.rows, new_cells, strict=True)]
        return Ok(Frame(cols, rows))
    cols = [*f.cols, (name, ty)]
    rows = [[*row, c] for row, c in zip(f.rows, new_cells, strict=True)]
    return Ok(Frame(cols, rows))


def _eval_group_by(f: Frame, keys: list[str], aggs: list[Agg]) -> Result[Frame, EvalError]:
    idxs: list[int] = []
    for k in keys:
        i = _col_index(f.cols, k)
        if i is None:
            return _unknown(k, f.cols)
        idxs.append(i)

    order: list[tuple[Cell, ...]] = []
    groups: dict[tuple[Cell, ...], list[Row]] = {}
    for row in f.rows:
        key = tuple(row[i] for i in idxs)
        if key in groups:
            groups[key].append(row)
        else:
            order.append(key)
            groups[key] = [row]

    resolved: list[tuple[Agg, str, int]] = []
    for a in aggs:
        ty = _col_type(f.cols, a.of)
        if ty is None:
            return _unknown(a.of, f.cols)
        i = _col_index(f.cols, a.of)
        assert i is not None
        resolved.append((a, ty, i))

    key_cols: Schema = [(k, _col_type(f.cols, k) or STRING) for k in keys]
    agg_cols: Schema = [(a.name, _agg_type(a.fn, ty)) for a, ty, _ in resolved]
    rows: list[Row] = []
    for key in order:
        grp = groups[key]
        agg_vals = [_agg_cells(a.fn, ty, [r[ci] for r in grp]) for a, ty, ci in resolved]
        rows.append([*key, *agg_vals])
    return Ok(Frame(key_cols + agg_cols, rows))


def _eval_sort(f: Frame, by: list[tuple[str, str]]) -> Frame:
    def cmp(a: Row, b: Row) -> int:
        return _row_key_compare(f.cols, by, a, b)

    rows = sorted(f.rows, key=cmp_to_key(cmp))
    return Frame(f.cols, rows)


def _eval_distinct(f: Frame) -> Frame:
    seen: set[tuple[Cell, ...]] = set()
    out: list[Row] = []
    for row in f.rows:
        key = tuple(row)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return Frame(f.cols, out)


def _eval_limit(f: Frame, n: int, offset: int) -> Frame:
    start = min(max(0, offset), len(f.rows))
    return Frame(f.cols, f.rows[start : start + max(0, n)])


def _eval_join(f: Frame, right: Frame, on: list[tuple[str, str]], how: str) -> Result[Frame, EvalError]:
    li: list[int] = []
    ri: list[int] = []
    for left_name, right_name in on:
        il = _col_index(f.cols, left_name)
        if il is None:
            return _unknown(left_name, f.cols)
        ir = _col_index(right.cols, right_name)
        if ir is None:
            return _unknown(right_name, right.cols)
        li.append(il)
        ri.append(ir)

    def key_match(lr: Row, rr: Row) -> bool:
        return all(_cell_eq(lr[i], rr[j]) for i, j in zip(li, ri, strict=True))

    left_names = {n for n, _ in f.cols}
    right_cols: Schema = [((n + "_right" if n in left_names else n), ty) for n, ty in right.cols]
    out_cols = list(f.cols) + right_cols
    left_nulls = [NULL for _ in f.cols]
    right_nulls = [NULL for _ in right.cols]

    left_side: list[Row] = []
    for lr in f.rows:
        matches = [rr for rr in right.rows if key_match(lr, rr)]
        if not matches:
            if how in ("left", "outer"):
                left_side.append(lr + right_nulls)
        else:
            for rr in matches:
                left_side.append(lr + rr)

    right_only: list[Row] = []
    if how in ("right", "outer"):
        for rr in right.rows:
            if not any(key_match(lr, rr) for lr in f.rows):
                right_only.append(left_nulls + rr)

    return Ok(Frame(out_cols, left_side + right_only))


def _eval_union(f: Frame, other: Frame) -> Result[Frame, EvalError]:
    if _available(f.cols) != _available(other.cols):
        return Err(EvalError("JOIN_ERROR", "union requires matching column names"))
    return Ok(Frame(f.cols, f.rows + other.rows))


def _eval_window(f: Frame, spec: WindowSpec) -> Result[Frame, EvalError]:  # noqa: C901
    of_idx = _col_index(f.cols, spec.of)
    if of_idx is None and spec.fn not in ("rowNumber", "rank"):
        return _unknown(spec.of, f.cols)

    part_idx = [i for i in (_col_index(f.cols, p) for p in spec.partition_by) if i is not None]

    def part_key(row: Row) -> tuple[Cell, ...]:
        return tuple(row[i] for i in part_idx)

    partitions: dict[tuple[Cell, ...], list[tuple[int, Row]]] = {}
    for i, row in enumerate(f.rows):
        partitions.setdefault(part_key(row), []).append((i, row))

    def value_at(row: Row) -> Cell:
        return row[of_idx] if of_idx is not None else NULL

    def _order_cmp(a: tuple[int, Row], b: tuple[int, Row]) -> int:
        return _row_key_compare(f.cols, spec.order_by, a[1], b[1])

    computed: list[tuple[int, Cell]] = []
    for members in partitions.values():
        ordered = sorted(members, key=cmp_to_key(_order_cmp))
        vals = [value_at(row) for _, row in ordered]
        n = len(ordered)
        outs: list[Cell]
        if spec.fn == "rowNumber":
            outs = [cell_int(i + 1) for i in range(n)]
        elif spec.fn == "rank":
            ranks: list[Cell] = []
            acc = 0
            for i, (_, row) in enumerate(ordered):
                if i == 0:
                    acc += 1
                else:
                    prev = ordered[i - 1][1]
                    acc += 0 if _row_key_compare(f.cols, spec.order_by, prev, row) == 0 else 1
                ranks.append(cell_int(acc))
            outs = ranks
        elif spec.fn == "lag":
            outs = [NULL, *vals[: max(0, n - 1)]]
        elif spec.fn == "lead":
            outs = [*vals[1:], NULL]
        elif spec.fn == "cumulSum":
            outs = []
            total = 0.0
            for v in vals:
                x = _as_num(v)
                if x is not None:
                    total += x
                outs.append(cell_float(total))
        else:  # rollingMean
            outs = []
            for i in range(n):
                lo = max(0, i - 2)
                window = vals[lo : i + 1]
                nums = [x for x in (_as_num(v) for v in window) if x is not None]
                outs.append(NULL if not nums else cell_float(sum(nums) / len(nums)))
        for (orig_i, _), out in zip(ordered, outs, strict=True):
            computed.append((orig_i, out))

    computed.sort(key=lambda t: t[0])
    out_cells = [c for _, c in computed]

    if spec.fn in ("rowNumber", "rank"):
        ty = INT
    elif spec.fn in ("cumulSum", "rollingMean"):
        ty = FLOAT
    else:
        ty = _col_type(f.cols, spec.of) or STRING

    cols = [*f.cols, (spec.as_, ty)]
    rows = [[*row, out] for row, out in zip(f.rows, out_cells, strict=True)]
    return Ok(Frame(cols, rows))


def _eval_pivot(f: Frame, spec: Pivot) -> Result[Frame, EvalError]:
    s = spec.spec

    def need(name: str) -> int | None:
        return _col_index(f.cols, name)

    idx_idx: list[int] = []
    for n in s.index:
        i = need(n)
        if i is None:
            return _unknown(n, f.cols)
        idx_idx.append(i)
    on_idx = need(s.on)
    if on_idx is None:
        return _unknown(s.on, f.cols)
    val_idx = need(s.values)
    if val_idx is None:
        return _unknown(s.values, f.cols)
    val_type = f.cols[val_idx][1]

    def index_key(row: Row) -> tuple[Cell, ...]:
        return tuple(row[i] for i in idx_idx)

    seen_on: list[Cell] = []
    for row in f.rows:
        c = row[on_idx]
        if not is_null(c) and c not in seen_on:
            seen_on.append(c)
    on_values = sorted(seen_on, key=cell_string)

    order: list[tuple[Cell, ...]] = []
    seen_keys: set[tuple[Cell, ...]] = set()
    for row in f.rows:
        k = index_key(row)
        if k not in seen_keys:
            seen_keys.add(k)
            order.append(k)

    idx_cols: Schema = [(n, _col_type(f.cols, n) or STRING) for n in s.index]
    pivot_cols: Schema = [(cell_string(ov), _agg_type(s.agg, val_type)) for ov in on_values]
    rows: list[Row] = []
    for k in order:
        cells: list[Cell] = list(k)
        for ov in on_values:
            matching = [row[val_idx] for row in f.rows if index_key(row) == k and _cell_eq(row[on_idx], ov)]
            cells.append(_agg_cells(s.agg, val_type, matching))
        rows.append(cells)
    return Ok(Frame(idx_cols + pivot_cols, rows))


def _eval_unpivot(f: Frame, id_vars: list[str], value_vars: list[str]) -> Result[Frame, EvalError]:
    id_idx: list[int] = []
    for n in id_vars:
        i = _col_index(f.cols, n)
        if i is None:
            return _unknown(n, f.cols)
        id_idx.append(i)
    val_idx: list[int] = []
    for n in value_vars:
        i = _col_index(f.cols, n)
        if i is None:
            return _unknown(n, f.cols)
        val_idx.append(i)

    id_cols: Schema = [(n, _col_type(f.cols, n) or STRING) for n in id_vars]
    val_type = next((t for t in (_col_type(f.cols, n) for n in value_vars) if t is not None), STRING)
    cols: Schema = [*id_cols, ("variable", STRING), ("value", val_type)]
    rows: list[Row] = []
    for row in f.rows:
        id_cells = [row[i] for i in id_idx]
        for name, vi in zip(value_vars, val_idx, strict=True):
            rows.append([*id_cells, cell_str(name), row[vi]])
    return Ok(Frame(cols, rows))


# ── pipeline driver ──────────────────────────────────────────────────────────

Resolver = Callable[[str], Result[Table, EvalError]]


def no_resolve(r: str) -> Result[Table, EvalError]:
    return Err(EvalError(UNRESOLVED_SOURCE, f"unresolved source ref: {r}"))


def _eval_source(resolve: Resolver, src: object) -> Result[Table, EvalError]:
    if isinstance(src, Embedded):
        return Ok(src.table)
    assert isinstance(src, Ref)
    return resolve(src.name)


def _eval_step(resolve: Resolver, f: Frame, t: Transform) -> Result[Frame, EvalError]:
    if isinstance(t, Filter):
        return _eval_filter(f, t.pred)
    if isinstance(t, Project):
        return _eval_project(f, t.cols)
    if isinstance(t, Derive):
        return _eval_derive(f, t.name, t.expr)
    if isinstance(t, GroupBy):
        return _eval_group_by(f, t.keys, t.aggs)
    if isinstance(t, Sort):
        return Ok(_eval_sort(f, t.by))
    if isinstance(t, Distinct):
        return Ok(_eval_distinct(f))
    if isinstance(t, Limit):
        return Ok(_eval_limit(f, t.n, t.offset))
    if isinstance(t, Window):
        return _eval_window(f, t.spec)
    if isinstance(t, Pivot):
        return _eval_pivot(f, t)
    if isinstance(t, Unpivot):
        return _eval_unpivot(f, t.id_vars, t.value_vars)
    if isinstance(t, Join):
        rsrc = _eval_source(resolve, t.source)
        if not rsrc.ok:
            return rsrc  # type: ignore[return-value]
        return _eval_join(f, _to_frame(rsrc.value), t.on, t.how)
    if isinstance(t, Union):
        rsrc = _eval_source(resolve, t.source)
        if not rsrc.ok:
            return rsrc  # type: ignore[return-value]
        return _eval_union(f, _to_frame(rsrc.value))
    return Err(EvalError("TYPE_ERROR", f"unknown Transform {type(t)!r}"))


def eval_pipeline_with(resolve: Resolver, pipeline: list[Transform], input_table: Table) -> Result[Table, EvalError]:
    """Fold the pipeline over the input table; ``resolve`` provides any ``Ref`` source."""
    f = _to_frame(input_table)
    for step in pipeline:
        r = _eval_step(resolve, f, step)
        if not r.ok:
            return r  # type: ignore[return-value]
        f = r.value
    return Ok(_of_frame(f))


def eval_pipeline(pipeline: list[Transform], input_table: Table) -> Result[Table, EvalError]:
    """The reference evaluator over embedded sources only (``Ref`` ⇒ ``UNRESOLVED_SOURCE``)."""
    return eval_pipeline_with(no_resolve, pipeline, input_table)
