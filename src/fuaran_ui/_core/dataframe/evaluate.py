"""The pure columnar reference evaluator — the Python leg of cross-host parity.

A faithful port of the canonical dataframe reference evaluator: a fold over the
pipeline threading a row-oriented working frame, with **pinned** semantics that every
host evaluator must reproduce byte-for-byte (certified by the reference-generated law
vectors):

* null/NA **propagates** through arithmetic + comparison (any null operand ⇒ null);
  the logical pair is three-valued (Kleene);
* ``int + int`` stays ``int``; any ``float`` operand widens the result to ``float``;
* a ``decimal`` is EXACT: ``add`` / ``sub`` / ``mul`` / ``mod`` over decimals (an ``int``
  promotes) are exact decimals, ``div`` over a decimal is refused naming ``Quotient``, and a
  decimal beside a float is refused naming the ``Cast`` that resolves it;
* sort is **stable** and **nulls sort last** regardless of direction; ``NaN`` is one value
  above every other in the float order, and ``-0`` equals ``0``;
* groups appear in **first-appearance** order, keyed on the canonical cell token;
  ``Count`` is the **non-null** count;
* ``Round`` is **round-half-away-from-zero** (never Python's banker's ``round``);
* division by zero ⇒ ``null`` (not an error);
* a derived column is typed by its EXPRESSION wherever the schema decides it (on an empty
  and an all-null frame alike), and by its cells only where it does not;
* floats canonicalise through the shared cross-host layout (via the codec).

Totality (GP4): every verb returns ``Ok``/``Err`` and never throws on a malformed
pipeline — an unknown column / type error / arity error is a named ``EvalError``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import cmp_to_key

from ...canonical import format_finite_double
from . import decimal_text
from .model import (
    AGG_ERROR,
    BOOL,
    DATE,
    DECIMAL,
    FLOAT,
    INT,
    JOIN_ERROR,
    NO_METADATA,
    STRING,
    TIMESTAMP,
    TIMESTAMP_UNIT_DIGITS,
    TYPE_ERROR,
    UNBOUND_PARAM,
    UNIT_MISMATCH,
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
    Convert,
    Derive,
    Distinct,
    Embedded,
    Err,
    EvalError,
    Except,
    FieldMeta,
    Filter,
    GroupBy,
    InList,
    InParam,
    Intersect,
    IsNull,
    Join,
    Limit,
    Lit,
    Not,
    Ok,
    Param,
    Pivot,
    Project,
    Quotient,
    Ref,
    Result,
    Rounded,
    Rounding,
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
    cell_decimal,
    cell_float,
    cell_int,
    cell_str,
    cell_timestamp,
    is_null,
    type_of,
)
from .unit import (
    DIMENSIONLESS,
    UnitOfMeasure,
    conversion_factor,
    div_unit,
    mul_unit,
    pow_unit,
    ratio_to_float,
    render_unit,
)

# A row is a list of cells co-indexed with the frame's columns.
Row = list[Cell]
NULL = Cell("null", None)

_INT32_MIN, _INT32_MAX = -(2**31), 2**31 - 1


# ── the working frame (row-oriented) ─────────────────────────────────────────


class Frame:
    """The working frame: the ``(name, type)`` schema, the rows, and the metadata of the columns
    that state any, by name (Phase 2208: a verb carries the whole schema entry, not a fresh
    ``(name, type)`` pair)."""

    __slots__ = ("cols", "meta", "rows")

    def __init__(self, cols: Schema, rows: list[Row], meta: dict[str, FieldMeta]) -> None:
        self.cols = cols
        self.rows = rows
        self.meta = {n: m for n, m in meta.items() if not m.is_empty}


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
    return Frame(list(t.schema), rows, t.metadata)


def _of_frame(f: Frame) -> Table:
    columns: list[Column] = []
    for ci, (name, ty) in enumerate(f.cols):
        columns.append(Column(name, ty, [row[ci] for row in f.rows]))
    return Table(list(f.cols), columns, {name: f.meta[name] for name, _ in f.cols if name in f.meta})


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
    if c.kind == DECIMAL:
        return decimal_text.try_canonical(c.value) or str(c.value)
    if c.kind in (STRING, DATE, TIMESTAMP):
        return str(c.value)
    return ""  # null


def cell_token(c: Cell) -> str:
    """The canonical, host-deterministic token for ONE cell — the key ``Distinct``, ``GroupBy``,
    ``Window`` partitions, ``Intersect`` / ``Except`` and ``countDistinct`` partition by. Type-tagged,
    so cells of two types never collide (``i:1`` is not ``f:1`` and neither is ``m:1``); a float's
    NaN is one bucket and ``-0`` is ``0``; a decimal is its CANONICAL text."""
    if c.kind == INT:
        return f"i:{c.value}"
    if c.kind == FLOAT:
        f = float(c.value)  # type: ignore[arg-type]
        if math.isnan(f):
            return "f:NaN"
        if math.isinf(f):
            return "f:Inf" if f > 0 else "f:-Inf"
        return "f:" + format_finite_double(f)
    if c.kind == BOOL:
        return "b:1" if c.value else "b:0"
    if c.kind == STRING:
        return f"s:{c.value}"
    if c.kind == DATE:
        return f"d:{c.value}"
    if c.kind == TIMESTAMP:
        return f"t:{c.value}"
    if c.kind == DECIMAL:
        return "m:" + (decimal_text.try_canonical(c.value) or str(c.value))
    return "n:"


def _row_token(cells: Row) -> tuple[str, ...]:
    return tuple(cell_token(c) for c in cells)


def _as_num(c: Cell) -> float | None:
    if c.kind == INT:
        return float(c.value)  # type: ignore[arg-type]
    if c.kind == FLOAT:
        return float(c.value)  # type: ignore[arg-type]
    return None


def _decimal_of(c: Cell) -> str | None:
    """A cell as exact-decimal text — a decimal's own, or an int's digits (the lossless promotion) —
    or ``None`` for any other cell."""
    if c.kind == DECIMAL:
        return c.value  # type: ignore[return-value]
    if c.kind == INT:
        return str(c.value)
    return None


def _is_exact(c: Cell) -> bool:
    return c.kind in (DECIMAL, INT)


def _compare_float(x: float, y: float) -> int:
    """The column layer's float order: NaN is one value ABOVE every other, ``-0`` equals ``0``."""
    xn, yn = math.isnan(x), math.isnan(y)
    if xn or yn:
        return (xn and not yn) - (yn and not xn)
    return (x > y) - (x < y)


def _compare_cells(a: Cell, b: Cell) -> int | None:
    """Total comparison between two present, same-family cells (``None`` ⇒ incomparable). A decimal
    against a decimal or an int compares EXACTLY; against a float it is incomparable."""
    an, bn = _as_num(a), _as_num(b)
    if an is not None and bn is not None:
        return _compare_float(an, bn)
    if _is_exact(a) and _is_exact(b):
        return decimal_text.compare(_decimal_of(a), _decimal_of(b))  # type: ignore[arg-type]
    if a.kind == BOOL and b.kind == BOOL:
        return (a.value > b.value) - (a.value < b.value)  # type: ignore[operator]
    if a.kind == TIMESTAMP and b.kind == TIMESTAMP:
        x, y = _instant_key(a.value), _instant_key(b.value)  # type: ignore[arg-type]
        return (x > y) - (x < y)
    if a.kind == b.kind and a.kind in (STRING, DATE):
        return (a.value > b.value) - (a.value < b.value)  # type: ignore[operator]
    return None


def _instant_key(s: str) -> str:
    """Canonical instant text keyed so its order is the instants' (WIRE_FORMAT §3.6.25). The text
    drops an all-zero fraction and every trailing zero, so ``…:00Z`` and ``…:00.5Z`` do not order by
    their characters (``.`` sorts below ``Z``); the key pads the fraction to nanoseconds. Text that
    is not canonical keys as itself."""
    if len(s) < 20 or not s.endswith("Z"):
        return s
    if s[19] == ".":
        fraction = s[20:-1]
    elif len(s) == 20:
        fraction = ""
    else:
        return s
    if len(fraction) > 9:
        return s
    return s[:19] + fraction.ljust(9, "0")


def _cell_eq(a: Cell, b: Cell) -> bool:
    if is_null(a) or is_null(b):
        return False
    # A decimal matches only a decimal — a join key never equates a decimal with an int.
    if a.kind == DECIMAL or b.kind == DECIMAL:
        return a.kind == b.kind and _compare_cells(a, b) == 0
    return _compare_cells(a, b) == 0


def _is_decimal_float_pair(a: Cell, b: Cell) -> bool:
    return {a.kind, b.kind} == {DECIMAL, FLOAT}


def _decimal_float_mismatch(what: str) -> Err[EvalError]:
    """The refusal a decimal beside a float meets, naming the ``Cast`` that resolves it: nothing
    converts between the two unless the pipeline says which way."""
    return Err(
        EvalError(
            TYPE_ERROR,
            f"{what} between a decimal and a float: cast one to the other's type first — Cast(decimal, …) "
            "to keep the digits exact, Cast(float, …) to compute approximately",
        )
    )


def _decimal_result(ctx: str, text: str | None) -> Result[Cell, EvalError]:
    if text is None:
        return Err(EvalError(TYPE_ERROR, f"{ctx}: an operand is not decimal text"))
    return Ok(cell_decimal(text))


def _decimal_arith(op: str, x: str, y: str) -> Result[Cell, EvalError]:
    """``add`` / ``sub`` / ``mul`` / ``mod`` over two exact operands, at least one a decimal: exact,
    and a decimal. ``div`` is refused by name — the quotient of decimals is not a finite decimal in
    general, so the pipeline names its rounding through ``Quotient``."""
    if op == "add":
        return _decimal_result("add", decimal_text.add(x, y))
    if op == "sub":
        return _decimal_result("sub", decimal_text.sub(x, y))
    if op == "mul":
        return _decimal_result("mul", decimal_text.mul(x, y))
    if op == "mod":
        if decimal_text.is_zero(y):
            return Ok(NULL)
        return _decimal_result("mod", decimal_text.rem(x, y))
    if op == "div":
        return Err(
            EvalError(
                TYPE_ERROR,
                "division of an exact decimal names its rounding: write Quotient(dividend, divisor, { Scale; Mode })",
            )
        )
    return Err(EvalError(TYPE_ERROR, "not an arithmetic operator"))


def _arith(op: str, a: Cell, b: Cell) -> Result[Cell, EvalError]:
    if is_null(a) or is_null(b):
        return Ok(NULL)
    if _is_exact(a) and _is_exact(b) and DECIMAL in (a.kind, b.kind):
        return _decimal_arith(op, _decimal_of(a), _decimal_of(b))  # type: ignore[arg-type]
    if _is_decimal_float_pair(a, b):
        return _decimal_float_mismatch("arithmetic")
    x, y = _as_num(a), _as_num(b)
    if x is None or y is None:
        return Err(EvalError(TYPE_ERROR, "arithmetic on a non-numeric operand"))
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
            return Err(EvalError(TYPE_ERROR, "mod requires integer operands"))
        if int(y) == 0:
            return Ok(NULL)
        return Ok(cell_int(int(x) % int(y)))
    return Err(EvalError(TYPE_ERROR, "not an arithmetic operator"))


def _comparison(op: str, a: Cell, b: Cell) -> Result[Cell, EvalError]:
    if is_null(a) or is_null(b):
        return Ok(NULL)
    c = _compare_cells(a, b)
    if c is None:
        if _is_decimal_float_pair(a, b):
            return _decimal_float_mismatch("comparison")
        return Err(EvalError(TYPE_ERROR, "comparison between incompatible types"))
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
        return Err(EvalError(TYPE_ERROR, "logical operator on a non-bool operand"))

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
    return Err(EvalError(TYPE_ERROR, "not a logical operator"))


def _cast_to_decimal(c: Cell) -> Result[Cell, EvalError]:
    """An int is exactly a decimal; a string is read by the decimal grammar; a FLOAT is the one
    place an approximation enters a decimal — its shortest round-trip digits."""
    if c.kind in (DECIMAL, STRING):
        canonical = decimal_text.try_canonical(c.value)
        if canonical is None:
            return Err(EvalError(TYPE_ERROR, f"cannot cast '{c.value}' to decimal"))
        return Ok(cell_decimal(canonical))
    if c.kind == INT:
        return Ok(cell_decimal(str(c.value)))
    if c.kind == FLOAT:
        text = decimal_text.of_float(float(c.value))  # type: ignore[arg-type]
        if text is None:
            return Err(EvalError(TYPE_ERROR, "cannot cast a non-finite float to decimal"))
        return Ok(cell_decimal(text))
    return Err(EvalError(TYPE_ERROR, "cannot cast to decimal"))


def _cast_cell(ty: str, c: Cell) -> Result[Cell, EvalError]:  # noqa: C901, PLR0911, PLR0912
    if is_null(c):
        return Ok(NULL)
    if ty == STRING:
        return Ok(cell_str(cell_string(c)))
    if ty == DECIMAL:
        return _cast_to_decimal(c)
    if ty == FLOAT:
        n = _as_num(c)
        if n is not None:
            return Ok(cell_float(n))
        if c.kind == DECIMAL:
            # The nearest float to the decimal — the cast says the pipeline asked for the
            # approximation. A decimal past the float range is refused, never an infinity.
            f = decimal_text.to_float(c.value)  # type: ignore[arg-type]
            if f is None:
                return Err(EvalError(TYPE_ERROR, f"cannot cast decimal '{c.value}' to float: outside the float range"))
            return Ok(cell_float(f))
        if c.kind == STRING:
            try:
                return Ok(cell_float(float(c.value)))  # type: ignore[arg-type]
            except ValueError:
                return Err(EvalError(TYPE_ERROR, f"cannot cast '{c.value}' to float"))
        return Err(EvalError(TYPE_ERROR, "cannot cast to float"))
    if ty == INT:
        if c.kind == INT:
            return Ok(c)
        if c.kind == FLOAT:
            return Ok(cell_int(int(c.value)))  # type: ignore[arg-type]
        if c.kind == DECIMAL:
            # Truncated toward zero, as a float is; outside int32 a named refusal.
            v = decimal_text.truncate_to_int(c.value)  # type: ignore[arg-type]
            if v is None or not _INT32_MIN <= v <= _INT32_MAX:
                return Err(EvalError(TYPE_ERROR, f"decimal→int cast out of int32 range: {c.value}"))
            return Ok(cell_int(v))
        if c.kind == BOOL:
            return Ok(cell_int(1 if c.value else 0))
        if c.kind == STRING:
            try:
                return Ok(cell_int(int(c.value)))  # type: ignore[arg-type]
            except ValueError:
                return Err(EvalError(TYPE_ERROR, f"cannot cast '{c.value}' to int"))
        return Err(EvalError(TYPE_ERROR, "cannot cast to int"))
    if ty == BOOL:
        if c.kind == BOOL:
            return Ok(c)
        if c.kind == INT:
            return Ok(cell_bool(c.value != 0))
        return Err(EvalError(TYPE_ERROR, "cannot cast to bool"))
    if ty == DATE:
        if c.kind == DATE:
            return Ok(c)
        if c.kind == STRING:
            return Ok(cell_date(str(c.value)))
        return Err(EvalError(TYPE_ERROR, "cannot cast to date"))
    if ty in TIMESTAMP_UNIT_DIGITS:
        # Phase 2206 — a cast to any unit keeps the instant's one canonical text; the
        # unit is a statement about the column, not the cell.
        if c.kind == TIMESTAMP:
            return Ok(c)
        if c.kind == STRING:
            return Ok(cell_timestamp(str(c.value)))
        return Err(EvalError(TYPE_ERROR, f"cannot cast to {ty}"))
    return Err(EvalError(TYPE_ERROR, f"unknown cast type {ty}"))


def _round_half_away(x: float) -> float:
    """Round half away from zero — host-independent (not banker's rounding)."""
    return math.floor(x + 0.5) if x >= 0.0 else math.ceil(x - 0.5)


def _scale_refused(scale: int) -> Err[EvalError] | None:
    if decimal_text.in_scale(scale):
        return None
    return Err(EvalError(TYPE_ERROR, f"rounding scale {scale} is outside 0..{decimal_text.MAX_SCALE}"))


def _rounded_cell(mode: str, scale: int, c: Cell) -> Result[Cell, EvalError]:
    """THE rounding primitive over cells: ``Rounded``'s, and through it the decimal arm of ``round``
    / ``floor`` / ``ceil``. The scale is checked first, then a null propagates, then an exact operand
    is brought to the scale; a float names the ``Cast``."""
    refused = _scale_refused(scale)
    if refused is not None:
        return refused
    if is_null(c):
        return Ok(NULL)
    if _is_exact(c):
        return _decimal_result("rounded", decimal_text.quantize(mode, scale, _decimal_of(c)))  # type: ignore[arg-type]
    if c.kind == FLOAT:
        return _decimal_float_mismatch("rounding")
    return Err(EvalError(TYPE_ERROR, "rounding of a non-numeric"))


def _quotient_cell(mode: str, scale: int, a: Cell, b: Cell) -> Result[Cell, EvalError]:
    """``Quotient``'s primitive: the scale first, then a null operand propagates, then two exact
    operands divide, correctly rounded; a zero divisor answers null, as ``div``'s does."""
    refused = _scale_refused(scale)
    if refused is not None:
        return refused
    if is_null(a) or is_null(b):
        return Ok(NULL)
    if _is_exact(a) and _is_exact(b):
        x, y = _decimal_of(a), _decimal_of(b)
        if decimal_text.is_zero(y):  # type: ignore[arg-type]
            return Ok(NULL)
        return _decimal_result("quotient", decimal_text.divide(mode, scale, x, y))  # type: ignore[arg-type]
    if a.kind == FLOAT or b.kind == FLOAT:
        return _decimal_float_mismatch("quotient")
    return Err(EvalError(TYPE_ERROR, "quotient of a non-numeric"))


def _scale_of(r: Rounding) -> Result[int, EvalError]:
    """A rounding's scale slot: the literal, or — a param the host did not substitute — the strict
    unbound-param refusal."""
    if isinstance(r.scale, Param):
        return Err(EvalError(UNBOUND_PARAM, f"unbound parameter '{r.scale.name}'"))
    return Ok(r.scale)


_SCALE0_MODES = {"round": decimal_text.HALF_UP, "floor": decimal_text.FLOOR, "ceil": decimal_text.CEILING}


# ── Phase 2400: the power functions (the reference's D43) ───────────────────
#
# Two guarantees, stated per function. An INTEGER exponent is EXACT on every host: one pinned product
# tree over IEEE-754 multiplication, which every host rounds the same way, so the tree and not the
# host decides the bits. A float exponent, `exp`, `ln` and `log10` are HOST-ROUNDED: the special-value
# table below answers every input the hosts' libraries disagree on BEFORE the host is called, and what
# remains is this host's `math` module — within the bound the transform laws compare in
# (`expected.tolerance`, in ulps).

_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


def _pow_go(b: float, k: int) -> float:
    """Binary exponentiation, the accumulator empty until the first set bit, so a power takes exactly
    the multiplies its bits name: ``x^1`` is ``x``, ``x^2`` is ``x*x``, ``x^3`` is ``x*(x*x)``, ``x^4``
    is ``(x*x)*(x*x)``. ``k`` is the exponent's magnitude, ``k > 0``."""
    acc: float | None = None
    while True:
        if k % 2 == 1:
            acc = b if acc is None else acc * b
        k //= 2
        if k == 0:
            return 1.0 if acc is None else acc
        b = b * b


def _pow_int_float(x: float, n: int) -> Cell:
    """A float base under an integer exponent: ``x^0`` is 1 with no multiply; a negative exponent is
    one reciprocal of the positive power, null where that power is zero (a zero base, or a power that
    underflowed), exactly as a division by zero is null. The caller has answered a NaN base."""
    if n == 0:
        return cell_float(1.0)
    if n > 0:
        return cell_float(_pow_go(x, n))
    p = _pow_go(x, -n)
    return NULL if p == 0.0 else cell_float(1.0 / p)


def _pow_int_exact(x: int, n: int) -> Result[Cell, EvalError]:
    """An int base under a non-negative int exponent: the same product tree, every product that leaves
    the int32 band refused by name — a base square that leaves it with exponent bits still to take
    included, since every value it would still multiply into is at least as large."""
    if n == 0:
        return Ok(cell_int(1))

    def overflow(v: int) -> Err[EvalError]:
        return Err(EvalError(TYPE_ERROR, f"pow overflowed int32: {v}"))

    acc: int | None = None
    b, k = x, n
    while True:
        if k % 2 == 1:
            acc = b if acc is None else acc * b
            if not _INT32_MIN <= acc <= _INT32_MAX:
                return overflow(acc)
        k //= 2
        if k == 0:
            return Ok(cell_int(1 if acc is None else acc))
        b = b * b
        if not _INT32_MIN <= b <= _INT32_MAX:
            return overflow(b)


def _pow_float(x: float, y: float) -> Cell:  # noqa: PLR0911
    """A float exponent: the special-value table, then the host. A NaN operand propagates
    (``pow(1, NaN)`` and ``pow(NaN, 0)`` included); a zero exponent and a base of one answer 1, and so
    does a base of minus one under an infinite exponent; a zero base under a negative exponent and a
    finite negative base under a finite non-integer exponent are outside the reals and answer null."""
    if math.isnan(x) or math.isnan(y):
        return cell_float(math.nan)
    if y == 0.0 or x == 1.0:
        return cell_float(1.0)
    if x == 0.0 and y < 0.0:
        return NULL
    if x == -1.0 and math.isinf(y):
        return cell_float(1.0)
    if x < 0.0 and not math.isinf(x) and not math.isinf(y) and y != math.floor(y):
        return NULL
    try:
        return cell_float(math.pow(x, y))
    except OverflowError:
        # `math.pow` raises where IEEE-754 answers an infinity: a negative base under an odd integer
        # exponent overflows to minus infinity, every other overflow to plus infinity.
        odd = not math.isinf(y) and y == math.floor(y) and math.fmod(y, 2.0) != 0.0
        return cell_float(-math.inf if x < 0.0 and odd else math.inf)


def _exp_host(x: float) -> float:
    """`exp`'s table: a NaN propagates; above the natural log of the largest double the answer is
    +Inf and below the natural log of half the smallest subnormal it is 0 — the two edges where a
    host's last-ulp rounding decides between a finite value and the limit."""
    if math.isnan(x):
        return x
    if x > 709.782712893384:
        return math.inf
    if x < -745.1332191019412:
        return 0.0
    return math.exp(x)


def _log_host(log: Callable[[float], float], x: float) -> Cell:
    """`ln`'s and `log10`'s table over a host logarithm: a NaN propagates, zero and below is null, one
    is exactly 0 and +Inf is +Inf; in between the host answers."""
    if math.isnan(x):
        return cell_float(x)
    if x <= 0.0:
        return NULL
    if x == 1.0:
        return cell_float(0.0)
    if math.isinf(x):
        return cell_float(x)
    return cell_float(log(x))


_SPELLED = {"exp": "Exp", "ln": "Ln", "log10": "Log10"}


def _apply_power(fn: str, args: list[Cell]) -> Result[Cell, EvalError]:  # noqa: PLR0911
    if fn == "pow":
        if len(args) != 2:
            return Err(EvalError("ARITY_ERROR", f"function 'pow' expects 2 args, got {len(args)}"))
        x, y = args
        if is_null(x) or is_null(y):
            return Ok(NULL)
        if DECIMAL in (x.kind, y.kind):
            return Err(EvalError(TYPE_ERROR, "pow of a decimal is not exact: write Pow(Cast(float, x), n)"))
        if x.kind == INT and y.kind == INT:
            n = int(y.value)  # type: ignore[arg-type]
            if n >= 0:
                return _pow_int_exact(int(x.value), n)  # type: ignore[arg-type]
            return Ok(_pow_int_float(float(x.value), n))  # type: ignore[arg-type]
        if y.kind == INT:
            f = _as_num(x)
            if f is None:
                return Err(EvalError(TYPE_ERROR, "pow of a non-numeric"))
            if math.isnan(f):
                return Ok(cell_float(f))
            return Ok(_pow_int_float(f, int(y.value)))  # type: ignore[arg-type]
        f, g = _as_num(x), _as_num(y)
        if f is None or g is None:
            return Err(EvalError(TYPE_ERROR, "pow of a non-numeric"))
        return Ok(_pow_float(f, g))
    if len(args) != 1:
        return Err(EvalError("ARITY_ERROR", f"function '{fn}' expects 1 args, got {len(args)}"))
    c = args[0]
    if is_null(c):
        return Ok(NULL)
    if c.kind == DECIMAL:
        return Err(EvalError(TYPE_ERROR, f"{fn} of a decimal is not exact: write {_SPELLED[fn]}(Cast(float, x))"))
    f = _as_num(c)
    if f is None:
        return Err(EvalError(TYPE_ERROR, f"{fn} of a non-numeric"))
    if fn == "exp":
        return Ok(cell_float(_exp_host(f)))
    return Ok(_log_host(math.log if fn == "ln" else math.log10, f))


def _apply_scalar(fn: str, args: list[Cell]) -> Result[Cell, EvalError]:  # noqa: C901, PLR0911, PLR0912
    def arity(n: int) -> Err[EvalError] | None:
        if len(args) != n:
            return Err(EvalError("ARITY_ERROR", f"function '{fn}' expects {n} args, got {len(args)}"))
        return None

    if fn in ("pow", "exp", "ln", "log10"):
        return _apply_power(fn, args)

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
            if c.kind == DECIMAL:
                return _decimal_result("abs", decimal_text.abs_(c.value))  # type: ignore[arg-type]
            return Err(EvalError(TYPE_ERROR, "abs of a non-numeric"))
        if fn in ("round", "floor", "ceil"):
            # Over a decimal these are scale-0 `Rounded`s under their pinned modes — exact.
            if c.kind == DECIMAL:
                return _rounded_cell(_SCALE0_MODES[fn], 0, c)
            n = _as_num(c)
            if n is None:
                return Err(EvalError(TYPE_ERROR, f"{fn} of a non-numeric"))
            if fn == "round":
                return Ok(cell_float(_round_half_away(n)))
            if fn == "floor":
                return Ok(cell_float(math.floor(n)))
            return Ok(cell_float(math.ceil(n)))
        if c.kind != STRING:
            return Err(EvalError(TYPE_ERROR, f"{fn} of a non-string"))
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
        return Err(EvalError(TYPE_ERROR, "substr expects (string, int, int)"))
    if fn == "datePart":
        a = arity(2)
        if a is not None:
            return a
        part, val = args
        if is_null(val):
            return Ok(NULL)
        if part.kind == STRING and val.kind in (STRING, DATE, TIMESTAMP):
            ds = str(val.value)
            spans = {"year": (0, 4), "month": (5, 2), "day": (8, 2)}
            span = spans.get(str(part.value))
            if span is None:
                return Err(EvalError(TYPE_ERROR, f"datePart: unknown part '{part.value}'"))
            lo, ln = span
            if len(ds) >= lo + ln:
                try:
                    return Ok(cell_int(int(ds[lo : lo + ln])))
                except ValueError:
                    return Err(EvalError(TYPE_ERROR, f"datePart: unparseable component in '{ds}'"))
            return Err(EvalError(TYPE_ERROR, f"datePart: '{ds}' too short for {part.value}"))
        return Err(EvalError(TYPE_ERROR, "datePart expects (string part, date/timestamp/string)"))
    if fn == "concat":
        # fuaran-core#90 — variadic (at least one argument); any null argument propagates
        # (compose ``coalesce`` for treat-as-empty). A non-string argument stringifies through
        # ``cell_string``, the SAME rendering a cast to string uses, so a number concatenates
        # in the shared cross-host layout rather than in Python's own ``str``.
        if not args:
            return Err(EvalError("ARITY_ERROR", "function 'concat' expects at least 1 args, got 0"))
        if any(is_null(c) for c in args):
            return Ok(NULL)
        return Ok(cell_str("".join(cell_string(c) for c in args)))
    if fn in ("least", "greatest"):
        # Variadic (at least one). Any null propagates, as concat's does; incomparable operands are
        # a typed error through the shared cell ordering, the one pinned comparison.
        if not args:
            return Err(EvalError("ARITY_ERROR", f"function '{fn.capitalize()}' expects 1 args, got 0"))
        if any(is_null(c) for c in args):
            return Ok(NULL)
        best = args[0]
        for c in args[1:]:
            k = _compare_cells(best, c)
            if k is None:
                return Err(EvalError(TYPE_ERROR, "least/greatest over incompatible types"))
            if (fn == "least") != (k <= 0):
                best = c
        return Ok(best)
    return Err(EvalError(TYPE_ERROR, f"unknown scalar fn {fn}"))


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
        return Err(EvalError(TYPE_ERROR, "not of a non-bool"))
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
    if isinstance(e, InList):
        # SQL three-valued membership: a null subject ⇒ null; any equal item ⇒ true;
        # no match having seen a null item ⇒ null; else false. An incomparable pair
        # is a loud type error, never a silent "not a member".
        rs = _eval_expr(cols, row, e.expr)
        if not rs.ok:
            return rs
        if is_null(rs.value):
            return Ok(NULL)
        saw_null = False
        for item in e.items:
            ri = _eval_expr(cols, row, item)
            if not ri.ok:
                return ri
            if is_null(ri.value):
                saw_null = True
                continue
            order = _compare_cells(rs.value, ri.value)
            if order is None:
                return Err(EvalError(TYPE_ERROR, "in: comparison between incompatible types"))
            if order == 0:
                return Ok(cell_bool(True))
        return Ok(NULL) if saw_null else Ok(cell_bool(False))
    if isinstance(e, IsNull):
        # TOTAL, and that is the point of the verb rather than an exception to the
        # three-valued rule the operators around it follow: `IsNull` is how a pipeline
        # ASKS about nullity, so it must always answer. It yields `Bool true` for a null
        # subject and `Bool false` otherwise, and it NEVER yields null — a null answer
        # would be unusable, since the only way to test it would be another `IsNull`.
        # Both reference hosts state exactly this at their own arms; matched here.
        r = _eval_expr(cols, row, e.expr)
        return r if not r.ok else Ok(cell_bool(is_null(r.value)))
    if isinstance(e, InParam):
        # A LIST param resolves by SUBSTITUTION before evaluation (the compute layer's
        # `substitute_list_params` rewrites it to the literal `InList` form). One that
        # reaches the evaluator is genuinely unbound — the same strictness a scalar
        # `Param` gets, never a silent "matches nothing".
        return Err(EvalError(UNBOUND_PARAM, f"unbound parameter '{e.param}'"))
    if isinstance(e, ApplyFn):
        argv: list[Cell] = []
        for a in e.args:
            r = _eval_expr(cols, row, a)
            if not r.ok:
                return r
            argv.append(r.value)
        return _apply_scalar(e.fn, argv)
    if isinstance(e, Quotient):
        # Evaluation order: the scale, the dividend, the divisor.
        rscale = _scale_of(e.rounding)
        if not rscale.ok:
            return rscale  # type: ignore[return-value]
        ra = _eval_expr(cols, row, e.dividend)
        if not ra.ok:
            return ra
        rb = _eval_expr(cols, row, e.divisor)
        if not rb.ok:
            return rb
        return _quotient_cell(e.rounding.mode, rscale.value, ra.value, rb.value)
    if isinstance(e, Rounded):
        rscale = _scale_of(e.rounding)
        if not rscale.ok:
            return rscale  # type: ignore[return-value]
        r = _eval_expr(cols, row, e.expr)
        return r if not r.ok else _rounded_cell(e.rounding.mode, rscale.value, r.value)
    if isinstance(e, Param):
        # Params are substituted from the host env before evaluation (see the compute
        # layer); one reaching the evaluator is genuinely unbound — a named failure.
        return Err(EvalError(UNBOUND_PARAM, f"unbound parameter '{e.name}'"))
    if isinstance(e, Convert):
        # Every step that carries an expression (filter, derive) resolves its conversions against
        # the frame's units before any row, so one reaching the evaluator was never checked.
        return Err(EvalError(TYPE_ERROR, "a convert reached evaluation unresolved against its schema's units"))
    return Err(EvalError(TYPE_ERROR, f"unknown ColExpr {type(e)!r}"))


# ── static typing of expressions, and THE derived-column typing rule ─────────
#
# What an expression's PRESENT values can be, decided from a step's schema alone. Three states:
# a column type tag (every present value carries it), ABSENT (no present value is ever produced:
# every row answers null or an error) and UNKNOWN (undecidable from the schema). The join is
# EXACT, not widening: an int beside a float is UNKNOWN here, and the cells decide.

ABSENT = "<absent>"
UNKNOWN = "<unknown>"

_NUMERIC = (INT, FLOAT)
_EXACT = (INT, DECIMAL)
_ORDERED_SAME = (STRING, DATE, TIMESTAMP, BOOL)


def _join(a: str, b: str) -> str:
    if a == ABSENT:
        return b
    if b == ABSENT:
        return a
    if UNKNOWN in (a, b):
        return UNKNOWN
    return a if a == b else UNKNOWN


def _join_all(ts: list[str]) -> str:
    acc = ABSENT
    for t in ts:
        acc = _join(acc, t)
    return acc


def _typing_binary(op: str, a: str, b: str) -> str:  # noqa: PLR0911
    if op in ("and", "or"):
        # Three-valued: an absent operand beside a boolean is still boolean.
        return _join(a, b) if a in (ABSENT, BOOL) and b in (ABSENT, BOOL) else UNKNOWN
    # Every other operator propagates null BEFORE any type is examined.
    if ABSENT in (a, b):
        return ABSENT
    decimal_pair = a in _EXACT and b in _EXACT and DECIMAL in (a, b)
    if op in ("add", "sub", "mul"):
        if a == INT and b == INT:
            return INT
        if a in _NUMERIC and b in _NUMERIC:
            return FLOAT
        return DECIMAL if decimal_pair else UNKNOWN
    if op == "div":
        # A decimal `div` is refused on every row (it names `Quotient`), so no present value.
        return FLOAT if a in _NUMERIC and b in _NUMERIC else UNKNOWN
    if op == "mod":
        if a == INT and b == INT:
            return INT
        return DECIMAL if decimal_pair else UNKNOWN
    if op in ("eq", "ne", "lt", "le", "gt", "ge"):
        comparable = (
            (a in _NUMERIC and b in _NUMERIC) or (a in _EXACT and b in _EXACT) or (a == b and a in _ORDERED_SAME)
        )
        return BOOL if comparable else UNKNOWN
    if op in ("contains", "startsWith", "endsWith"):
        return BOOL if a == STRING and b == STRING else UNKNOWN
    return UNKNOWN


def _typing_apply(fn: str, args: list[str]) -> str:  # noqa: PLR0911
    any_absent = ABSENT in args

    def unary(decide: Callable[[str], str]) -> str:
        if len(args) != 1:
            return ABSENT
        return ABSENT if args[0] == ABSENT else decide(args[0])

    if fn == "abs":
        return unary(lambda t: t if t in (INT, FLOAT, DECIMAL) else UNKNOWN)
    if fn in ("round", "floor", "ceil"):
        # Over a decimal these are scale-0 `Rounded`s; over an undecided operand they cannot be
        # decided either, because that operand may be a decimal.
        return unary(lambda t: t if t in (DECIMAL, UNKNOWN) else FLOAT)
    if fn == "length":
        return unary(lambda _: INT)
    if fn in ("lower", "upper", "trim"):
        return unary(lambda _: STRING)
    if fn == "substr":
        return ABSENT if len(args) != 3 or args[0] == ABSENT else STRING
    if fn == "datePart":
        return ABSENT if len(args) != 2 or args[1] == ABSENT else INT
    if fn == "concat":
        return ABSENT if not args or any_absent else STRING
    if fn == "replace":
        return ABSENT if len(args) != 3 or any_absent else STRING
    if fn == "dateDiffDays":
        return ABSENT if len(args) != 2 or any_absent else INT
    # Phase 2400 (the reference's D43): the transcendentals answer a float, as a root does. A power over
    # an int base and an int exponent is decided by the exponent's SIGN, which a typing does not carry —
    # `_typing_pow_lit` reads a literal exponent, and otherwise the cells decide; an undecided operand may
    # be that pair, so it is undecided too; every other pair is a float.
    if fn in ("exp", "ln", "log10"):
        return unary(lambda _: FLOAT)
    if fn in ("least", "greatest"):
        return ABSENT if not args or any_absent else _join_all(args)
    if fn == "pow":
        if len(args) != 2 or any_absent:
            return ABSENT
        if args[0] == INT and args[1] == INT:
            return UNKNOWN
        return UNKNOWN if UNKNOWN in args else FLOAT
    return UNKNOWN


def _typing_pow_lit(n: int, b: str) -> str:
    """A power under a LITERAL integer exponent: an int base answers an int at or above zero and a
    float below; any other typed base a float."""
    if b in (ABSENT, UNKNOWN):
        return b
    if b == INT:
        return INT if n >= 0 else FLOAT
    return FLOAT


def _typing(cols: Schema, e: ColExpr) -> str:  # noqa: C901, PLR0911
    """The static typing of an expression over a schema, with no env."""
    if isinstance(e, Col):
        return _col_type(cols, e.name) or UNKNOWN
    if isinstance(e, Lit):
        return type_of(e.cell) or ABSENT
    if isinstance(e, Param):
        return UNKNOWN
    if isinstance(e, Binary):
        return _typing_binary(e.op, _typing(cols, e.left), _typing(cols, e.right))
    if isinstance(e, Not):
        t = _typing(cols, e.expr)
        return t if t in (ABSENT, BOOL) else UNKNOWN
    if isinstance(e, Coalesce):
        return _join_all([_typing(cols, x) for x in e.exprs])
    if isinstance(e, Case):
        return _join_all([_typing(cols, e.else_expr), *(_typing(cols, t) for _, t in e.cases)])
    if isinstance(e, Cast):
        # A cast's present values are always of the target type: a value it cannot convert is an
        # error, not a cell of another type.
        return ABSENT if _typing(cols, e.expr) == ABSENT else e.type
    if isinstance(e, ApplyFn):
        if e.fn == "pow" and len(e.args) == 2:
            lit = e.args[1]
            if isinstance(lit, Lit) and lit.cell.kind == INT:
                return _typing_pow_lit(int(lit.cell.value), _typing(cols, e.args[0]))  # type: ignore[arg-type]
        return _typing_apply(e.fn, [_typing(cols, x) for x in e.args])
    if isinstance(e, InList | InParam):
        return ABSENT if _typing(cols, e.expr) == ABSENT else BOOL
    if isinstance(e, IsNull):
        return BOOL
    if isinstance(e, Quotient):
        a, b = _typing(cols, e.dividend), _typing(cols, e.divisor)
        if ABSENT in (a, b):
            return ABSENT
        return DECIMAL if a in _EXACT and b in _EXACT else UNKNOWN
    if isinstance(e, Rounded):
        t = _typing(cols, e.expr)
        if t == ABSENT:
            return ABSENT
        return DECIMAL if t in _EXACT else UNKNOWN
    if isinstance(e, Convert):
        # The float cast the conversion resolves to.
        return ABSENT if _typing(cols, e.expr) == ABSENT else FLOAT
    return UNKNOWN


# THE type of a derived column — one rule, read by `Derive` and `Unpivot`:
#
#  - where the typer decides the column, that type IS its type on every frame — full, empty or
#    all-null alike; a present cell is stored as it was produced (an int in a float or decimal
#    column widens on the wire);
#  - where the typer knows no present value can occur (ABSENT), the type is `string`;
#  - where the typer cannot decide (UNKNOWN), the CELLS decide by the widening join, `string` when
#    none is present;
#  - a float beside a decimal in one column is REFUSED by name, never widened: statically where the
#    expression's own arms carry both, and over the whole column where only the cells show it.

#: A derived typing that refuses the column outright.
REFUSED = "<refused>"


def _arms_have(ty: str, cols: Schema, e: ColExpr) -> bool:
    """Does an arm the derived value is drawn from — through ``Case`` and ``Coalesce``, the two nodes
    whose answer IS one of their operands' — carry the decided type ``ty``?"""
    if isinstance(e, Coalesce):
        return any(_arms_have(ty, cols, x) for x in e.exprs)
    if isinstance(e, Case):
        return _arms_have(ty, cols, e.else_expr) or any(_arms_have(ty, cols, t) for _, t in e.cases)
    return _typing(cols, e) == ty


def _derived_typing(cols: Schema, e: ColExpr) -> str | None:
    """How a ``Derive`` of ``e`` types its column: a column type (decided), ``None`` (the cells
    decide) or :data:`REFUSED` (a float arm beside a decimal arm)."""
    if _arms_have(FLOAT, cols, e) and _arms_have(DECIMAL, cols, e):
        return REFUSED
    t = _typing(cols, e)
    if t == ABSENT:
        return STRING
    return None if t == UNKNOWN else t


def _widens(source: str, target: str) -> bool:
    return source == target or (source == INT and target in (FLOAT, DECIMAL))


def _join_column_type(a: str, b: str) -> str | None:
    """The least column type two present cells' types both widen into — ``int`` and ``float`` join
    at ``float``, ``int`` and ``decimal`` at ``decimal`` — or ``None`` (``float`` beside ``decimal``
    included)."""
    if _widens(a, b):
        return b
    if _widens(b, a):
        return a
    return None


def _float_beside_decimal(column: str) -> Err[EvalError]:
    return Err(
        EvalError(
            TYPE_ERROR,
            f"derived column '{column}' joins a float and a decimal: cast one to the other's type first - "
            "Cast(decimal, ...) to keep the digits exact, Cast(float, ...) to compute approximately",
        )
    )


def _type_from_cells(column: str, cells: list[Cell]) -> Result[str, EvalError]:
    """The cells' half of the rule: the widening join over the present cells, ``string`` when none is
    present, and the named refusals where the cells hold a float beside a decimal, or two types no
    column type holds together."""
    acc: str | None = None
    saw_float = saw_decimal = False
    disjoint: tuple[str, str] | None = None
    for c in cells:
        t = type_of(c)
        if t is None:
            continue
        saw_float = saw_float or t == FLOAT
        saw_decimal = saw_decimal or t == DECIMAL
        if acc is None:
            acc = t
            continue
        joined = _join_column_type(acc, t)
        if joined is None:
            disjoint = disjoint or (acc, t)
        else:
            acc = joined
    if saw_float and saw_decimal:
        return _float_beside_decimal(column)
    if disjoint is not None:
        a, b = disjoint
        return Err(
            EvalError(
                TYPE_ERROR,
                f"derived column '{column}' holds a {a} beside a {b}, which no column type holds together: "
                "cast one to the other's type first",
            )
        )
    return Ok(acc or STRING)


def _unpivot_typing(types: list[str]) -> str | None:
    """An ``Unpivot`` value column's typing over the value columns' declared types: their widening
    join where one exists (an int and a float column melt into a float column on every frame),
    ``string`` with no value column, refused for a float beside a decimal, and left to the cells for
    a pair no widening relates."""
    if FLOAT in types and DECIMAL in types:
        return REFUSED
    acc: str | None = None
    for t in types:
        if acc is None:
            acc = t
            continue
        joined = _join_column_type(acc, t)
        if joined is None:
            return None
        acc = joined
    return acc or STRING


# ── aggregates ───────────────────────────────────────────────────────────────


def _agg_error(detail: str) -> Err[EvalError]:
    return Err(EvalError(AGG_ERROR, detail))


def _left_sum(xs: list[float]) -> float:
    """The float sum folded left to right from ``0.0`` — the pinned order (never a compensated sum,
    whose last bit differs, and with it the wire bytes)."""
    total = 0.0
    for x in xs:
        total += x
    return total


def _scaled_moments(xs: list[float]) -> tuple[float, float]:
    """Mean and population standard deviation computed so no intermediate leaves the float range:
    the values scaled by a power of two until every magnitude is at most one, then Welford's
    recurrence. Only taken where the plain formulas overflowed over finite input."""
    max_abs = max((abs(x) for x in xs), default=0.0)
    scale = 1.0
    while max_abs * scale > 1.0:
        scale *= 0.5
    mean = m2 = 0.0
    for k, x in enumerate(xs, start=1):
        y = x * scale
        d = y - mean
        mean += d / k
        m2 += d * (y - mean)
    return mean / scale, math.sqrt(m2 / len(xs)) / scale


def _finite_or(name: str, what: str, f: float) -> Result[Cell, EvalError]:
    if math.isfinite(f):
        return Ok(cell_float(f))
    return _agg_error(f"{name}: {what} overflowed the float range")


def _float_answer(fn: str, name: str, nums: list[float]) -> Result[Cell, EvalError]:  # noqa: PLR0911
    """``sum`` / ``mean`` / ``stddev`` / ``median`` over the present numbers, in row order. No float
    aggregate answers an infinity over FINITE input: where the plain formula overflows, the median
    halves before it adds and the mean and deviation are recomputed by the scaled recurrence."""
    if not nums:
        return Ok(NULL)
    all_finite = all(math.isfinite(x) for x in nums)
    total = _left_sum(nums)
    n = len(nums)
    if fn == "sum":
        return _finite_or(name, "sum", total) if all_finite else Ok(cell_float(total))
    if fn == "mean":
        mean = total / n
        if math.isfinite(mean) or not all_finite:
            return Ok(cell_float(mean))
        return _finite_or(name, "mean", _scaled_moments(nums)[0])
    if fn == "stddev":
        mean = total / n
        squares = _left_sum([(x - mean) * (x - mean) for x in nums])
        deviation = math.sqrt(squares / n)
        if math.isfinite(deviation) or not all_finite:
            return Ok(cell_float(deviation))
        return _finite_or(name, "stddev", _scaled_moments(nums)[1])
    ordered = sorted(nums, key=cmp_to_key(_compare_float))
    mid = n // 2
    if n % 2 == 1:
        return Ok(cell_float(ordered[mid]))
    a, b = ordered[mid - 1], ordered[mid]
    plain = (a + b) / 2.0
    if math.isfinite(plain) or not (math.isfinite(a) and math.isfinite(b)):
        return Ok(cell_float(plain))
    return _finite_or(name, "median", a / 2.0 + b / 2.0)


def _extreme(fn: str, present: list[Cell]) -> Cell:
    """``min`` keeps the first of equal minima, ``max`` the last of equal maxima, under the column
    layer's order; ``NULL`` when nothing is present."""
    if not present:
        return NULL
    is_min = fn == "min"
    acc = present[0]
    for b in present[1:]:
        c = _compare_cells(acc, b)
        if c is not None and not (is_min == (c <= 0)):
            acc = b
    return acc


def _agg_decimals(fn: str, name: str, cells: list[Cell]) -> Result[Cell, EvalError]:  # noqa: PLR0911
    """Every aggregate over a decimal column. The column is ADMITTED first: a present cell that is
    not decimal text (an int is, by promotion) is refused by name, and that refusal outranks every
    answer. Each admitted value is read canonicalised, so ``1.50`` and ``1.5`` are one value. ``sum``
    is exact and a decimal; ``min`` / ``max`` compare exactly; ``mean`` / ``median`` / ``stddev`` read
    each value at its nearest float, refusing a value past the float range."""
    admitted: list[Cell | None] = []
    for c in cells:
        if is_null(c):
            admitted.append(None)
            continue
        text = decimal_text.try_canonical(_decimal_of(c)) if _is_exact(c) else None
        if text is None:
            return _agg_error(
                f"aggregate over a decimal column met a cell outside its type: {cell_string(c)!r} (not decimal)"
            )
        admitted.append(cell_decimal(text))
    present = [c for c in admitted if c is not None]
    if fn == "count":
        return Ok(cell_int(len(present)))
    if fn in ("first", "last"):
        edge = (admitted[0] if fn == "first" else admitted[-1]) if admitted else None
        return Ok(edge or NULL)
    if fn in ("min", "max"):
        return Ok(_extreme(fn, present))
    if fn == "countDistinct":
        return Ok(cell_int(len({c.value for c in present})))
    if fn == "sum":
        if not present:
            return Ok(NULL)
        total = decimal_text.ZERO
        for c in present:
            total = decimal_text.add(total, c.value) or total  # type: ignore[arg-type]
        return Ok(cell_decimal(total))
    nums: list[float] = []
    for c in present:
        f = decimal_text.to_float(c.value)  # type: ignore[arg-type]
        if f is None:
            return _agg_error(f"{name}: the decimal {c.value} is past the float range, and {fn} is a float")
        nums.append(f)
    return _float_answer(fn, name, nums)


def aggregate(fn: str, column: Column) -> Result[Cell, EvalError]:  # noqa: PLR0911
    """One aggregate over a column, with the pinned null / coercion / float semantics: nulls are
    skipped (``first`` / ``last`` excepted, which keep the edge row's cell); a numeric aggregate
    (``sum`` / ``mean`` / ``median`` / ``stddev``) over a non-numeric column is refused by name;
    ``countDistinct`` counts distinct present values by the canonical cell token."""
    cells = column.cells
    if column.type == DECIMAL:
        return _agg_decimals(fn, column.name, cells)
    present = [c for c in cells if not is_null(c)]
    if fn == "count":
        return Ok(cell_int(len(present)))
    if fn == "first":
        return Ok(cells[0] if cells else NULL)
    if fn == "last":
        return Ok(cells[-1] if cells else NULL)
    if fn == "countDistinct":
        return Ok(cell_int(len({cell_token(c) for c in present})))
    if fn in ("min", "max"):
        return Ok(_extreme(fn, present))
    if column.type not in _NUMERIC:
        return _agg_error(f"aggregate {fn} over a {column.type} column (expected int/float/decimal)")
    nums = [n for n in (_as_num(c) for c in present) if n is not None]
    if fn == "sum" and column.type == INT:
        return Ok(cell_int(sum(int(n) for n in nums))) if nums else Ok(NULL)
    return _float_answer(fn, column.name, nums)


def _agg_type(fn: str, src_type: str) -> str:
    if fn in ("count", "countDistinct"):
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


# ── the unit check (Phase 2208, the reference's Phase 429 / D29) ─────────────
#
# A column's unit is a fact of its schema entry, so the check is a static walk over the step's
# schema before any row: an empty frame is checked exactly as a full one. The walk answers the
# expression's unit — ``None`` is UNCONSTRAINED: a literal, a param, a column that states no unit;
# it composes with anything, so a unitless pipeline passes and evaluates as it always did — and
# rewrites every ``Convert`` to the cast (and factor) it stands for.

#: What a power, product or quotient whose exponent would leave the algebra's range is refused against.
_POWER_RANGE_TEXT = "a power the algebra can carry"


def _col_unit(f: Frame, name: str) -> UnitOfMeasure | None:
    """The unit the frame's schema states for a column, where it states one."""
    return f.meta.get(name, NO_METADATA).unit


def _stated(unit: UnitOfMeasure | None) -> FieldMeta:
    """A verb's own column: the unit it derives, and no label, description or extension."""
    return NO_METADATA if unit is None else FieldMeta(unit=unit)


def _expr_text(e: ColExpr) -> str:  # noqa: C901, PLR0911
    """A compact text of an expression for a refusal — the reference's ``exprText``."""

    def args(xs: list[ColExpr]) -> str:
        return ", ".join(_expr_text(x) for x in xs)

    if isinstance(e, Col):
        return f"'{e.name}'"
    if isinstance(e, Lit):
        return cell_string(e.cell)
    if isinstance(e, Param):
        return "$" + e.name
    if isinstance(e, Binary):
        return f"{e.op.lower()}({_expr_text(e.left)}, {_expr_text(e.right)})"
    if isinstance(e, Not):
        return f"not({_expr_text(e.expr)})"
    if isinstance(e, Coalesce):
        return f"coalesce({args(e.exprs)})"
    if isinstance(e, Case):
        arms = [x for w, t in e.cases for x in (w, t)]
        return f"case({args(arms)}, {_expr_text(e.else_expr)})"
    if isinstance(e, Cast):
        return f"cast({e.type}, {_expr_text(e.expr)})"
    if isinstance(e, ApplyFn):
        return f"{e.fn.lower()}({args(e.args)})"
    if isinstance(e, InList):
        return f"in({_expr_text(e.expr)}; {args(e.items)})"
    if isinstance(e, IsNull):
        return f"isNull({_expr_text(e.expr)})"
    if isinstance(e, InParam):
        return f"in({_expr_text(e.expr)}; ${e.param})"
    if isinstance(e, Quotient):
        return f"quotient({_expr_text(e.dividend)}, {_expr_text(e.divisor)})"
    if isinstance(e, Rounded):
        return f"rounded({_expr_text(e.expr)})"
    return f"convert({_expr_text(e.expr)}, {render_unit(e.unit)})"


def _unit_mismatch(step: str, at: ColExpr, left: str, right: str) -> Err[EvalError]:
    return Err(EvalError(UNIT_MISMATCH, f"unit mismatch in {step}: {left} against {right} in {_expr_text(at)}"))


type _Resolved = tuple[UnitOfMeasure | None, ColExpr]


def _resolve_units(step: str, f: Frame, e: ColExpr) -> Result[_Resolved, EvalError]:  # noqa: C901, PLR0911, PLR0912
    """The unit of ``e`` over the frame's schema, and ``e`` with every ``Convert`` resolved — the
    reference's ``resolveUnitsIn``. ``step`` names the step a refusal is about. Addition,
    subtraction, comparison, ``coalesce``, ``case`` arms, ``least`` / ``greatest`` and ``in``
    require ONE unit among the operands that state one, and the answer carries it; multiplication,
    division, ``quotient`` and ``mod`` compose through the algebra; ``cast``, ``abs``, ``round``,
    ``floor``, ``ceil`` and ``rounded`` keep their operand's unit; a boolean, a text and a date
    carry none; a literal and a param are unconstrained."""

    def mismatch(left: UnitOfMeasure, right: UnitOfMeasure) -> Err[EvalError]:
        return _unit_mismatch(step, e, render_unit(left), render_unit(right))

    def agree(acc: UnitOfMeasure | None, units: list[UnitOfMeasure | None]) -> Result[UnitOfMeasure | None, EvalError]:
        # One unit among the stated ones: an unconstrained operand takes the other's.
        for u in units:
            if u is None:
                continue
            if acc is None:
                acc = u
            elif acc != u:
                return mismatch(acc, u)
        return Ok(acc)

    def many(xs: list[ColExpr]) -> Result[list[_Resolved], EvalError]:
        out: list[_Resolved] = []
        for x in xs:
            r = _resolve_units(step, f, x)
            if not r.ok:
                return r
            out.append(r.value)
        return Ok(out)

    def product(a: UnitOfMeasure | None, b: UnitOfMeasure | None) -> Result[UnitOfMeasure | None, EvalError]:
        if a is None or b is None:
            return Ok(b if a is None else a)
        p = mul_unit(a, b)
        return _unit_mismatch(step, e, render_unit(a), _POWER_RANGE_TEXT) if p is None else Ok(p)

    def quotient(a: UnitOfMeasure | None, b: UnitOfMeasure | None) -> Result[UnitOfMeasure | None, EvalError]:
        # An unconstrained dividend is dimensionless over a stated divisor (1 / s is /s); an
        # unconstrained divisor leaves the dividend's unit.
        if b is None:
            return Ok(a)
        dividend = DIMENSIONLESS if a is None else a
        q = div_unit(dividend, b)
        return _unit_mismatch(step, e, render_unit(dividend), _POWER_RANGE_TEXT) if q is None else Ok(q)

    def first_dimensioned(units: list[UnitOfMeasure | None]) -> UnitOfMeasure | None:
        return next((u for u in units if u is not None and u != DIMENSIONLESS), None)

    if isinstance(e, Col):
        return Ok((_col_unit(f, e.name), e))
    if isinstance(e, Lit | Param):
        return Ok((None, e))
    if isinstance(e, Binary):
        rs = many([e.left, e.right])
        if not rs.ok:
            return rs  # type: ignore[return-value]
        (ua, a), (ub, b) = rs.value
        e2 = Binary(e.op, a, b)
        if e.op in ("add", "sub"):
            u = agree(ua, [ub])
        elif e.op == "mul":
            u = product(ua, ub)
        elif e.op == "div":
            u = quotient(ua, ub)
        elif e.op == "mod":
            u = Ok(ua)
        elif e.op in ("eq", "ne", "lt", "le", "gt", "ge"):
            u = agree(ua, [ub])
            if u.ok:
                u = Ok(None)
        else:
            u = Ok(None)
        return u if not u.ok else Ok((u.value, e2))  # type: ignore[return-value]
    if isinstance(e, Not | IsNull):
        r = _resolve_units(step, f, e.expr)
        return r if not r.ok else Ok((None, type(e)(r.value[1])))
    if isinstance(e, InParam):
        r = _resolve_units(step, f, e.expr)
        return r if not r.ok else Ok((None, InParam(r.value[1], e.param)))
    if isinstance(e, Coalesce):
        rs = many(e.exprs)
        if not rs.ok:
            return rs  # type: ignore[return-value]
        u = agree(None, [x for x, _ in rs.value])
        return u if not u.ok else Ok((u.value, Coalesce([x for _, x in rs.value])))  # type: ignore[return-value]
    if isinstance(e, Case):
        whens = many([w for w, _ in e.cases])
        if not whens.ok:
            return whens  # type: ignore[return-value]
        thens = many([t for _, t in e.cases])
        if not thens.ok:
            return thens  # type: ignore[return-value]
        els = _resolve_units(step, f, e.else_expr)
        if not els.ok:
            return els
        u = agree(els.value[0], [x for x, _ in thens.value])
        if not u.ok:
            return u  # type: ignore[return-value]
        cases = [(w, t) for (_, w), (_, t) in zip(whens.value, thens.value, strict=True)]
        return Ok((u.value, Case(cases, els.value[1])))
    if isinstance(e, Cast):
        r = _resolve_units(step, f, e.expr)
        return r if not r.ok else Ok((r.value[0], Cast(e.type, r.value[1])))
    if isinstance(e, ApplyFn):
        rs = many(e.args)
        if not rs.ok:
            return rs  # type: ignore[return-value]
        units = [x for x, _ in rs.value]
        applied = ApplyFn(e.fn, [x for _, x in rs.value])
        if e.fn in ("abs", "round", "floor", "ceil"):
            return Ok((units[0] if units else None, applied))
        if e.fn in ("least", "greatest"):
            u = agree(None, units)
            return u if not u.ok else Ok((u.value, applied))  # type: ignore[return-value]
        if e.fn == "pow":
            # An integer-LITERAL exponent composes the unit through the algebra (m squared is m2; to
            # the power zero, unconstrained); any other power takes a dimensionless operand only.
            exponent = e.args[1] if len(e.args) == 2 else None
            if isinstance(exponent, Lit) and exponent.cell.kind == INT:
                base, n = units[0], exponent.cell.value
                assert isinstance(n, int)
                if base is None or n == 0:
                    return Ok((None, applied))
                p = pow_unit(base, n)
                return _unit_mismatch(step, e, render_unit(base), _POWER_RANGE_TEXT) if p is None else Ok((p, applied))
            dimensioned = first_dimensioned(units)
            return Ok((None, applied)) if dimensioned is None else mismatch(dimensioned, DIMENSIONLESS)
        if e.fn in ("exp", "ln", "log10"):
            dimensioned = first_dimensioned(units)
            return Ok((None, applied)) if dimensioned is None else mismatch(dimensioned, DIMENSIONLESS)
        return Ok((None, applied))
    if isinstance(e, InList):
        rs = many([e.expr, *e.items])
        if not rs.ok:
            return rs  # type: ignore[return-value]
        u = agree(None, [x for x, _ in rs.value])
        if not u.ok:
            return u  # type: ignore[return-value]
        resolved = [x for _, x in rs.value]
        return Ok((None, InList(resolved[0], resolved[1:])))
    if isinstance(e, Quotient):
        rs = many([e.dividend, e.divisor])
        if not rs.ok:
            return rs  # type: ignore[return-value]
        (ua, a), (ub, b) = rs.value
        u = quotient(ua, ub)
        return u if not u.ok else Ok((u.value, Quotient(a, b, e.rounding)))  # type: ignore[return-value]
    if isinstance(e, Rounded):
        r = _resolve_units(step, f, e.expr)
        return r if not r.ok else Ok((r.value[0], Rounded(r.value[1], e.rounding)))
    # Convert: over an operand stating no unit, a TAG — the operand as a float under the unit; over a
    # compatible unit, the float times the exact factor as a float literal; across dimensions, refused.
    r = _resolve_units(step, f, e.expr)
    if not r.ok:
        return r
    source, inner = r.value
    as_float = Cast(FLOAT, inner)
    if source is None or source == e.unit:
        return Ok((e.unit, as_float))
    factor = conversion_factor(source, e.unit)
    if factor is None:
        return mismatch(source, e.unit)
    return Ok((e.unit, Binary("mul", as_float, Lit(cell_float(ratio_to_float(*factor))))))


def _aggregate_unit(fn: str, source: UnitOfMeasure | None) -> UnitOfMeasure | None:
    """An aggregate's column unit: a count is dimensionless, every other keeps its source's."""
    return None if fn in ("count", "countDistinct") else source


def _window_unit(fn: str, source: UnitOfMeasure | None) -> UnitOfMeasure | None:
    """A window column's unit: the ranking family none, the shifting and running families their source's."""
    return None if fn in ("rowNumber", "rank") else source


def _shared_unit(f: Frame, value_vars: list[str]) -> UnitOfMeasure | None:
    """An unpivot's ``value`` unit: the one unit every value column states, none where they differ
    or any states none."""
    units = [_col_unit(f, n) for n in value_vars]
    first = units[0] if units else None
    return first if first is not None and all(u == first for u in units) else None


def _units_agree(what: str, name: str, left: FieldMeta, right: FieldMeta) -> Result[FieldMeta, EvalError]:
    """Two columns met as one (a union's pair, a set op's pair): the left's metadata under the one
    unit the pair states, or the mismatch."""
    if left.unit is not None and right.unit is not None and left.unit != right.unit:
        return _unit_mismatch(f"{what} '{name}'", Col(name), render_unit(left.unit), render_unit(right.unit))
    if left.unit is None and right.unit is not None:
        return Ok(FieldMeta(right.unit, left.label, left.description, left.ext))
    return Ok(left)


def _eval_filter(f: Frame, written: ColExpr) -> Result[Frame, EvalError]:
    # The predicate's units are checked over the schema before any row; the resolved predicate is
    # what is evaluated.
    checked = _resolve_units("filter", f, written)
    if not checked.ok:
        return checked  # type: ignore[return-value]
    pred = checked.value[1]
    kept: list[Row] = []
    for row in f.rows:
        r = _eval_expr(f.cols, row, pred)
        if not r.ok:
            return r  # type: ignore[return-value]
        if r.value.kind == BOOL and r.value.value is True:
            kept.append(row)
    return Ok(Frame(f.cols, kept, f.meta))


def _repeated_output(verb: str, outs: list[tuple[str, str]]) -> Err[EvalError] | None:
    """Each output column's name beside what produced it, in output order: the first name an earlier
    output already took is refused — a table cannot hold two columns of one name (the reference's D35,
    Phase 468: project, groupBy, unpivot and pivot ask this one check, after every name the verb
    resolves and before any row is read)."""
    seen: dict[str, str] = {}
    for name, by in outs:
        if name in seen:
            return Err(
                EvalError(
                    TYPE_ERROR,
                    f"{verb} output '{name}' is named twice, by {seen[name]} and by {by}; "
                    "give each output a name of its own",
                )
            )
        seen[name] = by
    return None


def _eval_project(f: Frame, pairs: list[tuple[str, str]]) -> Result[Frame, EvalError]:
    resolved: list[tuple[str, str, int]] = []
    for src, out in pairs:
        i = _col_index(f.cols, src)
        if i is None:
            return _unknown(src, f.cols)
        resolved.append((out, f.cols[i][1], i))
    refused = _repeated_output("project", [(out, f"the projection of '{src}'") for src, out in pairs])
    if refused is not None:
        return refused
    cols: Schema = [(o, ty) for o, ty, _ in resolved]
    rows = [[row[i] for _, _, i in resolved] for row in f.rows]
    # A projection renames a column and states nothing new about it: every metadatum is kept.
    meta = {o: f.meta.get(f.cols[i][0], NO_METADATA) for o, _, i in resolved}
    return Ok(Frame(cols, rows, meta))


def _eval_derive(f: Frame, name: str, written: ColExpr) -> Result[Frame, EvalError]:
    # The column's unit and typing are read off the schema before any row: a derive refused
    # statically is refused on every frame, an empty one included, and a decided one carries its
    # type, and its unit, whatever rows the frame holds. The column's entry is its own.
    checked = _resolve_units(f"derive '{name}'", f, written)
    if not checked.ok:
        return checked  # type: ignore[return-value]
    unit, expr = checked.value
    meta = {**f.meta, name: _stated(unit)}
    typing = _derived_typing(f.cols, expr)
    if typing == REFUSED:
        return _float_beside_decimal(name)
    new_cells: list[Cell] = []
    for row in f.rows:
        r = _eval_expr(f.cols, row, expr)
        if not r.ok:
            return r  # type: ignore[return-value]
        new_cells.append(r.value)
    if typing is None:
        rty = _type_from_cells(name, new_cells)
        if not rty.ok:
            return rty  # type: ignore[return-value]
        typing = rty.value
    ty = typing
    i = _col_index(f.cols, name)
    if i is not None:
        cols = [(n, ty if j == i else t) for j, (n, t) in enumerate(f.cols)]
        rows = [[c if j == i else cell for j, cell in enumerate(row)] for row, c in zip(f.rows, new_cells, strict=True)]
        return Ok(Frame(cols, rows, meta))
    cols = [*f.cols, (name, ty)]
    rows = [[*row, c] for row, c in zip(f.rows, new_cells, strict=True)]
    return Ok(Frame(cols, rows, meta))


def _eval_group_by(f: Frame, keys: list[str], aggs: list[Agg]) -> Result[Frame, EvalError]:
    idxs: list[int] = []
    for k in keys:
        i = _col_index(f.cols, k)
        if i is None:
            return _unknown(k, f.cols)
        idxs.append(i)

    # Groups key on the canonical cell token, in first-appearance order; a group's key cells are
    # its first row's.
    groups: dict[tuple[str, ...], tuple[list[Cell], list[Row]]] = {}
    for row in f.rows:
        key_cells = [row[i] for i in idxs]
        token = _row_token(key_cells)
        if token in groups:
            groups[token][1].append(row)
        else:
            groups[token] = (key_cells, [row])

    resolved: list[tuple[Agg, str, int]] = []
    for a in aggs:
        ty = _col_type(f.cols, a.of)
        if ty is None:
            return _unknown(a.of, f.cols)
        i = _col_index(f.cols, a.of)
        assert i is not None
        resolved.append((a, ty, i))
    refused = _repeated_output(
        "groupBy",
        [(k, f"the key '{k}'") for k in keys] + [(a.name, f"the aggregate over '{a.of}'") for a in aggs],
    )
    if refused is not None:
        return refused  # type: ignore[return-value]

    key_cols: Schema = [(k, _col_type(f.cols, k) or STRING) for k in keys]
    agg_cols: Schema = [(a.name, _agg_type(a.fn, ty)) for a, ty, _ in resolved]
    rows: list[Row] = []
    for key_cells, grp in groups.values():
        out: Row = list(key_cells)
        for a, ty, ci in resolved:
            r = aggregate(a.fn, Column(a.of, ty, [g[ci] for g in grp]))
            if not r.ok:
                return r  # type: ignore[return-value]
            out.append(r.value)
        rows.append(out)
    # A key keeps its entry; an aggregate's column keeps its source's unit, a count none.
    meta = {k: f.meta.get(k, NO_METADATA) for k in keys} | {
        a.name: _stated(_aggregate_unit(a.fn, _col_unit(f, a.of))) for a, _, _ in resolved
    }
    return Ok(Frame(key_cols + agg_cols, rows, meta))


def _eval_sort(f: Frame, by: list[tuple[str, str]]) -> Frame:
    def cmp(a: Row, b: Row) -> int:
        return _row_key_compare(f.cols, by, a, b)

    rows = sorted(f.rows, key=cmp_to_key(cmp))
    return Frame(f.cols, rows, f.meta)


def _eval_distinct(f: Frame) -> Frame:
    seen: set[tuple[str, ...]] = set()
    out: list[Row] = []
    for row in f.rows:
        key = _row_token(row)
        if key not in seen:
            seen.add(key)
            out.append(row)
    return Frame(f.cols, out, f.meta)


def _eval_limit(f: Frame, n: int, offset: int) -> Frame:
    start = min(max(0, offset), len(f.rows))
    return Frame(f.cols, f.rows[start : start + max(0, n)], f.meta)


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
    # A key pair is an equality, so the comparison's rule holds it: two stated units must be one.
    for left_name, right_name in on:
        lu, ru = _col_unit(f, left_name), _col_unit(right, right_name)
        if lu is not None and ru is not None and lu != ru:
            return _unit_mismatch(
                f"join key '{left_name}' = '{right_name}'",
                Binary("eq", Col(left_name), Col(right_name)),
                render_unit(lu),
                render_unit(ru),
            )

    def key_match(lr: Row, rr: Row) -> bool:
        return all(_cell_eq(lr[i], rr[j]) for i, j in zip(li, ri, strict=True))

    left_names = {n for n, _ in f.cols}
    right_cols: Schema = [((n + "_right" if n in left_names else n), ty) for n, ty in right.cols]
    # Each side keeps its entries; a right column renamed for a collision keeps its metadata.
    meta = f.meta | {
        out: right.meta.get(n, NO_METADATA) for (out, _), (n, _) in zip(right_cols, right.cols, strict=True)
    }
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

    return Ok(Frame(out_cols, left_side + right_only, meta))


def _eval_union(f: Frame, other: Frame) -> Result[Frame, EvalError]:
    if _available(f.cols) != _available(other.cols):
        return Err(EvalError(JOIN_ERROR, "union requires matching column names"))
    # The unit is the left's, the right's where only the right states one; two are refused.
    meta: dict[str, FieldMeta] = {}
    for name, _ in f.cols:
        m = _units_agree("union column", name, f.meta.get(name, NO_METADATA), other.meta.get(name, NO_METADATA))
        if not m.ok:
            return m  # type: ignore[return-value]
        meta[name] = m.value
    return Ok(Frame(f.cols, f.rows + other.rows, meta))


def _eval_set_op(verb: str, keep_present: bool, f: Frame, other: Frame) -> Result[Frame, EvalError]:  # noqa: FBT001
    """``Intersect`` (``keep_present``) / ``Except``: the multiset set-ops, keyed on the SAME
    canonical row token ``Distinct`` dedups on, so a null matches a null and an int never matches
    a float or a decimal. The left's order and duplicate multiplicity survive."""
    if _available(f.cols) != _available(other.cols):
        return Err(EvalError(JOIN_ERROR, f"{verb} requires matching column names"))
    # Row identity across two units is the comparison's error; the left schema is what is kept.
    for name, _ in f.cols:
        m = _units_agree(f"{verb} column", name, f.meta.get(name, NO_METADATA), other.meta.get(name, NO_METADATA))
        if not m.ok:
            return m  # type: ignore[return-value]
    present = {_row_token(row) for row in other.rows}
    return Ok(Frame(f.cols, [row for row in f.rows if (_row_token(row) in present) == keep_present], f.meta))


def _eval_window(f: Frame, spec: WindowSpec) -> Result[Frame, EvalError]:  # noqa: C901
    of_idx = _col_index(f.cols, spec.of)
    if of_idx is None and spec.fn not in ("rowNumber", "rank"):
        return _unknown(spec.of, f.cols)
    # A running total over a decimal column stays EXACT and a decimal, as `sum` over one is.
    decimal_source = _col_type(f.cols, spec.of) == DECIMAL

    part_idx = [i for i in (_col_index(f.cols, p) for p in spec.partition_by) if i is not None]

    partitions: dict[tuple[str, ...], list[tuple[int, Row]]] = {}
    for i, row in enumerate(f.rows):
        partitions.setdefault(_row_token([row[j] for j in part_idx]), []).append((i, row))

    def value_at(row: Row) -> Cell:
        return row[of_idx] if of_idx is not None else NULL

    def num_at(c: Cell) -> float | None:
        if c.kind == DECIMAL:
            return decimal_text.to_float(c.value)  # type: ignore[arg-type]
        return _as_num(c)

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
        elif spec.fn == "cumulSum" and decimal_source:
            outs = []
            running = decimal_text.ZERO
            for v in vals:
                text = _decimal_of(v)
                if text is not None:
                    running = decimal_text.add(running, text) or running
                outs.append(cell_decimal(running))
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
                nums = [x for x in (num_at(v) for v in vals[lo : i + 1]) if x is not None]
                outs.append(NULL if not nums else cell_float(_left_sum(nums) / len(nums)))
        for (orig_i, _), out in zip(ordered, outs, strict=True):
            computed.append((orig_i, out))

    computed.sort(key=lambda t: t[0])
    out_cells = [c for _, c in computed]

    if spec.fn in ("rowNumber", "rank"):
        ty = INT
    elif spec.fn == "cumulSum" and decimal_source:
        ty = DECIMAL
    elif spec.fn in ("cumulSum", "rollingMean"):
        ty = FLOAT
    else:
        ty = _col_type(f.cols, spec.of) or STRING

    cols = [*f.cols, (spec.as_, ty)]
    rows = [[*row, out] for row, out in zip(f.rows, out_cells, strict=True)]
    meta = {**f.meta, spec.as_: _stated(_window_unit(spec.fn, _col_unit(f, spec.of)))}
    return Ok(Frame(cols, rows, meta))


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
    # Two on values of one text are the one column they name when the cells are equal (two NaNs);
    # a text shared by cells the comparison keeps apart is refused below, with the index columns.
    on_values: list[Cell] = []
    for c in sorted(seen_on, key=cell_string):
        if not any(cell_string(o) == cell_string(c) and _cell_eq(o, c) for o in on_values):
            on_values.append(c)
    refused = _repeated_output(
        "pivot",
        [(n, f"the index column '{n}'") for n in s.index]
        + [(cell_string(ov), f"the column for the on value '{cell_string(ov)}'") for ov in on_values],
    )
    if refused is not None:
        return refused  # type: ignore[return-value]

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
            r = aggregate(s.agg, Column(s.values, val_type, matching))
            if not r.ok:
                return r  # type: ignore[return-value]
            cells.append(r.value)
        rows.append(cells)
    # An index column keeps its entry; every value column is one aggregate of `values`, so each
    # carries that aggregate's unit.
    value_meta = _stated(_aggregate_unit(s.agg, _col_unit(f, s.values)))
    meta = {n: f.meta.get(n, NO_METADATA) for n in s.index} | {name: value_meta for name, _ in pivot_cols}
    return Ok(Frame(idx_cols + pivot_cols, rows, meta))


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
    refused = _repeated_output(
        "unpivot",
        [(n, f"the id column '{n}'") for n in id_vars]
        + [("variable", "the unpivot's variable column"), ("value", "the unpivot's value column")],
    )
    if refused is not None:
        return refused  # type: ignore[return-value]

    # The value column is typed by the one derived-column rule, the value columns being its arms:
    # their declared types' join where the schema decides it — over an empty frame too — and the
    # melted cells only where it does not.
    typing = _unpivot_typing([f.cols[i][1] for i in val_idx])
    if typing == REFUSED:
        return _float_beside_decimal("value")
    id_cols: Schema = [(n, _col_type(f.cols, n) or STRING) for n in id_vars]
    rows: list[Row] = []
    for row in f.rows:
        id_cells = [row[i] for i in id_idx]
        for name, vi in zip(value_vars, val_idx, strict=True):
            rows.append([*id_cells, cell_str(name), row[vi]])
    if typing is None:
        rty = _type_from_cells("value", [r[-1] for r in rows])
        if not rty.ok:
            return rty  # type: ignore[return-value]
        typing = rty.value
    cols: Schema = [*id_cols, ("variable", STRING), ("value", typing)]
    meta = {n: f.meta.get(n, NO_METADATA) for n in id_vars} | {"value": _stated(_shared_unit(f, value_vars))}
    return Ok(Frame(cols, rows, meta))


# ── pipeline driver ──────────────────────────────────────────────────────────

Resolver = Callable[[str], Result[Table, EvalError]]


def no_resolve(r: str) -> Result[Table, EvalError]:
    return Err(EvalError(UNRESOLVED_SOURCE, f"unresolved source ref: {r}"))


def _eval_source(resolve: Resolver, src: object) -> Result[Table, EvalError]:
    if isinstance(src, Embedded):
        return Ok(src.table)
    assert isinstance(src, Ref)
    return resolve(src.name)


def _eval_step(resolve: Resolver, f: Frame, t: Transform) -> Result[Frame, EvalError]:  # noqa: PLR0911
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
    if isinstance(t, Join | Union | Intersect | Except):
        rsrc = _eval_source(resolve, t.source)
        if not rsrc.ok:
            return rsrc  # type: ignore[return-value]
        other = _to_frame(rsrc.value)
        if isinstance(t, Join):
            return _eval_join(f, other, t.on, t.how)
        if isinstance(t, Union):
            return _eval_union(f, other)
        return _eval_set_op("intersect" if isinstance(t, Intersect) else "except", isinstance(t, Intersect), f, other)
    return Err(EvalError(TYPE_ERROR, f"unknown Transform {type(t)!r}"))


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
