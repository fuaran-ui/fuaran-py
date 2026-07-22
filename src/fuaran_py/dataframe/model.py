"""The columnar/dataframe model — the Python leg of the Compute-layer wire surface.

A faithful, dependency-light port of the canonical columnar strand and its
dataframe-transform algebra: the typed null-aware ``Cell`` / ``Column`` / ``Table``
columnar model, the ``DataSource`` it serialises to, and the serializable
``Transform`` + ``ColExpr`` expression trees. The algebra is *data*, not code — a
pipeline authored in Python serialises to a canonical wire form every host's
evaluator runs identically (see :mod:`fuaran_py.dataframe.codec` for the byte-exact
codec and :mod:`fuaran_py.dataframe.evaluate` for the reference-parity evaluator).

This module is the **types**; it carries no platform primitives and no third-party
dependency. ``Cell`` is a single tagged value so the ``int`` / ``float`` / ``date``
distinctions survive a round-trip exactly, the same way the reference model keeps a
closed scalar set with a first-class null case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ── Column scalar types (the closed, Arrow-compatible set) — wire tags ───────

INT = "int"
FLOAT = "float"
BOOL = "bool"
STRING = "string"
DATE = "date"
TIMESTAMP = "timestamp"

COLUMN_TYPES: tuple[str, ...] = (INT, FLOAT, BOOL, STRING, DATE, TIMESTAMP)


# ── Cell — one realized scalar, or the first-class null/NA marker ────────────


@dataclass(frozen=True)
class Cell:
    """A single scalar cell. ``kind`` is one of the column-type tags or ``"null"``;
    ``value`` is the native Python payload (``None`` for null). The explicit ``kind``
    keeps ``Date`` distinct from ``Str`` and ``bool`` distinct from ``int`` under the
    structural equality the evaluator relies on for grouping / distinct."""

    kind: str
    value: int | float | bool | str | None


NULL = Cell("null", None)


def cell_int(v: int) -> Cell:
    return Cell(INT, v)


def cell_float(v: float) -> Cell:
    return Cell(FLOAT, v)


def cell_bool(v: bool) -> Cell:  # noqa: FBT001
    return Cell(BOOL, v)


def cell_str(v: str) -> Cell:
    return Cell(STRING, v)


def cell_date(v: str) -> Cell:
    return Cell(DATE, v)


def cell_timestamp(v: str) -> Cell:
    return Cell(TIMESTAMP, v)


def is_null(c: Cell) -> bool:
    return c.kind == "null"


def type_of(c: Cell) -> str | None:
    """The column type a present cell carries (``None`` for the type-agnostic null)."""
    return None if c.kind == "null" else c.kind


def default_for(ty: str) -> Cell:
    """The type-default placeholder a ``Null`` cell encodes as (the validity mask, not
    this placeholder, carries the nullity — values stay null-free, as the wire requires)."""
    return {
        INT: Cell(INT, 0),
        FLOAT: Cell(FLOAT, 0.0),
        BOOL: Cell(BOOL, False),
        STRING: Cell(STRING, ""),
        DATE: Cell(DATE, ""),
        TIMESTAMP: Cell(TIMESTAMP, ""),
    }[ty]


# ── Column / Table / DataSource ──────────────────────────────────────────────

# A schema is an ordered list of (name, column-type) pairs; column order follows it.
Schema = list[tuple[str, str]]


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    cells: list[Cell]


@dataclass(frozen=True)
class Table:
    schema: Schema
    columns: list[Column]


@dataclass(frozen=True)
class Embedded:
    """An embedded columnar table — the rows travel on the wire."""

    table: Table


@dataclass(frozen=True)
class Ref:
    """A named source the host resolves; the wire carries the name, never the rows."""

    name: str


DataSource = Embedded | Ref


# ── ColExpr — a scalar expression over a row's columns + literals ────────────


@dataclass(frozen=True)
class Col:
    name: str


@dataclass(frozen=True)
class Lit:
    cell: Cell


@dataclass(frozen=True)
class Binary:
    op: str  # one of BIN_OPS
    left: ColExpr
    right: ColExpr


@dataclass(frozen=True)
class Not:
    expr: ColExpr


@dataclass(frozen=True)
class Coalesce:
    exprs: list[ColExpr]


@dataclass(frozen=True)
class Case:
    cases: list[tuple[ColExpr, ColExpr]]  # (when, then) pairs
    else_expr: ColExpr


@dataclass(frozen=True)
class Cast:
    type: str  # a column type tag
    expr: ColExpr


@dataclass(frozen=True)
class ApplyFn:
    fn: str  # one of SCALAR_FNS
    args: list[ColExpr]


@dataclass(frozen=True)
class Param:
    """A named hole bound at evaluation time from a host parameter env (Phase 424).

    A compute graph declares ``parameters`` on its ``Transform`` binding; each binds a
    ``Param`` name to a scalar source (e.g. a filter-store value or state). The evaluator
    substitutes a bound param with its literal cell; an unbound one prunes its filter (the
    host leniency) or, if it survives, is a named ``UNBOUND_PARAM`` failure — never a guess."""

    name: str


@dataclass(frozen=True)
class InList:
    """Membership against a literal list (fuaran-core#91): ``expr IN items``."""

    expr: ColExpr
    items: list[ColExpr]


@dataclass(frozen=True)
class InParam:
    """Membership against a bound multi-select list param (fuaran-core#91)."""

    expr: ColExpr
    param: str


@dataclass(frozen=True)
class IsNull:
    """Null test (fuaran-core#90): true where the inner expression is null."""

    expr: ColExpr


ColExpr = Col | Lit | Binary | Not | Coalesce | Case | Cast | ApplyFn | Param | InList | InParam | IsNull

# Closed vocabularies (wire tags) — additive only.
BIN_OPS: frozenset[str] = frozenset(
    {
        "add",
        "sub",
        "mul",
        "div",
        "mod",
        "eq",
        "ne",
        "lt",
        "le",
        "gt",
        "ge",
        "and",
        "or",
        # fuaran-core#90 — the string predicates.
        "contains",
        "startsWith",
        "endsWith",
    }
)
SCALAR_FNS: frozenset[str] = frozenset(
    {
        "abs",
        "round",
        "floor",
        "ceil",
        "length",
        "lower",
        "upper",
        "substr",
        "datePart",
        # fuaran-core#90 — the string/date builders.
        "concat",
        "trim",
        "replace",
        "dateDiffDays",
    }
)
AGG_FNS: frozenset[str] = frozenset({"sum", "mean", "min", "max", "count", "median", "stddev", "first", "last"})
JOIN_KINDS: frozenset[str] = frozenset({"inner", "left", "right", "outer"})
# fuaran-core#92 — `cumSum` renamed `cumulSum` (the legacy tag stays a decode alias).
WINDOW_FNS: frozenset[str] = frozenset({"rowNumber", "rank", "lag", "lead", "cumulSum", "rollingMean"})
SORT_DIRS: frozenset[str] = frozenset({"asc", "desc"})


# ── Aggregates / window / pivot specs ────────────────────────────────────────


@dataclass(frozen=True)
class Agg:
    name: str
    fn: str  # one of AGG_FNS
    of: str


@dataclass(frozen=True)
class WindowSpec:
    partition_by: list[str]
    order_by: list[tuple[str, str]]  # (col, dir)
    fn: str  # one of WINDOW_FNS
    of: str
    as_: str


@dataclass(frozen=True)
class PivotSpec:
    index: list[str]
    on: str
    values: str
    agg: str  # one of AGG_FNS


# ── Transform — the v1 verb set ──────────────────────────────────────────────


@dataclass(frozen=True)
class Filter:
    pred: ColExpr


@dataclass(frozen=True)
class Project:
    cols: list[tuple[str, str]]  # (source, output)


@dataclass(frozen=True)
class Derive:
    name: str
    expr: ColExpr


@dataclass(frozen=True)
class GroupBy:
    keys: list[str]
    aggs: list[Agg]


@dataclass(frozen=True)
class Join:
    source: DataSource
    on: list[tuple[str, str]]  # (leftCol, rightCol)
    how: str  # one of JOIN_KINDS


@dataclass(frozen=True)
class Window:
    spec: WindowSpec


@dataclass(frozen=True)
class Pivot:
    spec: PivotSpec


@dataclass(frozen=True)
class Unpivot:
    id_vars: list[str]
    value_vars: list[str]


@dataclass(frozen=True)
class Sort:
    by: list[tuple[str, str]]  # (col, dir)


@dataclass(frozen=True)
class Distinct:
    pass


@dataclass(frozen=True)
class Limit:
    n: int
    offset: int = 0


@dataclass(frozen=True)
class Union:
    source: DataSource


Transform = Filter | Project | Derive | GroupBy | Join | Window | Pivot | Unpivot | Sort | Distinct | Limit | Union


# ── Error envelopes ──────────────────────────────────────────────────────────
#
# Two closed, additive envelopes — the codec's six-code ``ColumnError`` (a wire-shape
# violation) and the evaluator's ``EvalError`` (a pipeline-semantic failure). Both
# *name* the failure; a closed-set failure enumerates the alternatives.

# Codec codes
NOT_JSON = "NOT_JSON"
MISSING_FIELD = "MISSING_FIELD"
MALFORMED_SHAPE = "MALFORMED_SHAPE"
UNKNOWN_TYPE = "UNKNOWN_TYPE"
TYPE_MISMATCH = "TYPE_MISMATCH"
LENGTH_MISMATCH = "LENGTH_MISMATCH"


@dataclass(frozen=True)
class ColumnError:
    code: str
    detail: str


# Evaluator codes
UNKNOWN_COLUMN = "UNKNOWN_COLUMN"
TYPE_ERROR = "TYPE_ERROR"
AGG_ERROR = "AGG_ERROR"
JOIN_ERROR = "JOIN_ERROR"
ARITY_ERROR = "ARITY_ERROR"
UNRESOLVED_SOURCE = "UNRESOLVED_SOURCE"
UNBOUND_PARAM = "UNBOUND_PARAM"


@dataclass(frozen=True)
class EvalError:
    code: str
    detail: str


# ── A tiny self-contained result (the compute strand's recoverable discipline) ──
#
# ``ok`` is a ``Literal`` discriminator field (not a property), so ``Ok[T] | Err[E]``
# is a *tagged* union: ``if not r.ok: ...`` narrows the fall-through to ``Ok[T]`` for
# the type-checker — the recoverable-result pattern without a forest of casts.


@dataclass(frozen=True)
class Ok[T]:
    value: T
    ok: Literal[True] = True


@dataclass(frozen=True)
class Err[E]:
    error: E
    ok: Literal[False] = False


type Result[T, E] = Ok[T] | Err[E]


# A convenience for an empty table.
EMPTY_TABLE = Table(schema=[], columns=[])
