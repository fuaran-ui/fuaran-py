"""The columnar/dataframe model — the Python leg of the Compute-layer wire surface.

A faithful, dependency-light port of the canonical columnar strand and its
dataframe-transform algebra: the typed null-aware ``Cell`` / ``Column`` / ``Table``
columnar model, the ``DataSource`` it serialises to, and the serializable
``Transform`` + ``ColExpr`` expression trees. The algebra is *data*, not code — a
pipeline authored in Python serialises to a canonical wire form every host's
evaluator runs identically (see :mod:`fuaran_ui.dataframe.codec` for the byte-exact
codec and :mod:`fuaran_ui.dataframe.evaluate` for the reference-parity evaluator).

This module is the **types**; it carries no platform primitives and no third-party
dependency. ``Cell`` is a single tagged value so the ``int`` / ``float`` / ``date``
distinctions survive a round-trip exactly, the same way the reference model keeps a
closed scalar set with a first-class null case.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

# The rounding vocabulary is the decimal module's; re-exported so the algebra's closed sets read
# from one place.
from .decimal_text import MAX_SCALE as MAX_SCALE
from .decimal_text import ROUNDING_MODES as ROUNDING_MODES

if TYPE_CHECKING:
    from .unit import UnitOfMeasure

# ── Column scalar types (the closed, Arrow-compatible set) — wire tags ───────

INT = "int"
FLOAT = "float"
BOOL = "bool"
STRING = "string"
DATE = "date"
TIMESTAMP = "timestamp"
#: The exact decimal (Phase 2001): a cell carries its value as CANONICAL decimal TEXT, never a
#: host decimal or float (see :mod:`fuaran_ui.dataframe.decimal_text`).
DECIMAL = "decimal"
# The three sub-second timestamp units (Phase 2206, fuaran-core#422).
TIMESTAMP_MS = "timestamp_ms"
TIMESTAMP_US = "timestamp_us"
TIMESTAMP_NS = "timestamp_ns"

COLUMN_TYPES: tuple[str, ...] = (
    INT,
    FLOAT,
    BOOL,
    STRING,
    DATE,
    TIMESTAMP,
    DECIMAL,
    TIMESTAMP_MS,
    TIMESTAMP_US,
    TIMESTAMP_NS,
)

# The timestamp family (WIRE_FORMAT §3.6.25): each tag's unit as the fraction digits
# it holds. Every unit carries one canonical instant text per instant, so a timestamp
# cell's kind is ``TIMESTAMP`` whichever unit its column declares; a coarser unit
# widens into a finer one.
TIMESTAMP_UNIT_DIGITS: dict[str, int] = {
    TIMESTAMP: 0,
    TIMESTAMP_MS: 3,
    TIMESTAMP_US: 6,
    TIMESTAMP_NS: 9,
}


# ── Cell — one realized scalar, or the first-class null/NA marker ────────────


@dataclass(frozen=True)
class Cell:
    """A single scalar cell. ``kind`` is one of the column-type tags or ``"null"``;
    ``value`` is the native Python payload (``None`` for null). The explicit ``kind``
    keeps ``Date`` distinct from ``Str`` and ``bool`` distinct from ``int`` under the
    structural equality the evaluator relies on for grouping / distinct. A ``decimal`` cell's
    ``value`` is its decimal TEXT (canonical wherever the codec or the evaluator produced it)."""

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


def cell_decimal(v: str) -> Cell:
    """A decimal cell carrying ``v`` as its text, as given — the codec and the evaluator only ever
    build one from canonical text (``decimal_text.try_canonical``)."""
    return Cell(DECIMAL, v)


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
        DECIMAL: Cell(DECIMAL, "0"),
        TIMESTAMP_MS: Cell(TIMESTAMP, ""),
        TIMESTAMP_US: Cell(TIMESTAMP, ""),
        TIMESTAMP_NS: Cell(TIMESTAMP, ""),
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
class FieldMeta:
    """What a column MEANS, beside what it is (WIRE_FORMAT §3.6.26, Phase 2207): an optional
    ``unit`` (a parsed :class:`~fuaran_ui.dataframe.unit.UnitOfMeasure`), ``label`` and
    ``description``, and ``ext``, namespaced extension members preserved verbatim. ``None`` is
    "not stated" and differs from a stated empty string; an empty ``ext`` states nothing."""

    unit: UnitOfMeasure | None = None
    label: str | None = None
    description: str | None = None
    ext: dict[str, str] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        """Whether the metadata states nothing — the entry is exactly ``{name, type}``."""
        return self.unit is None and self.label is None and self.description is None and not self.ext


#: The metadata that states nothing.
NO_METADATA = FieldMeta()


@dataclass(frozen=True)
class Field:
    """One schema entry: a column's ``name`` and ``type``, and its metadata (Phase 2207) — the
    reference's ``Field``. A :class:`Table` keeps the ``(name, type)`` pairs in ``schema`` and the
    metadata by column name in ``metadata``; :func:`table_fields` reads the two together."""

    name: str
    type: str
    meta: FieldMeta = NO_METADATA


@dataclass(frozen=True)
class Table:
    schema: Schema
    columns: list[Column]
    #: The metadata of the columns that state any (Phase 2207), by column name. A column absent
    #: here states none, so every table built before this member keeps its bytes.
    metadata: dict[str, FieldMeta] = field(default_factory=dict)


def table_fields(t: Table) -> list[Field]:
    """The table's schema as :class:`Field` entries, each with the metadata its column states."""
    return [Field(name, ty, t.metadata.get(name, NO_METADATA)) for name, ty in t.schema]


def table_of_fields(fields: list[Field], columns: list[Column]) -> Table:
    """A table whose schema is ``fields`` — the metadata of each field that states any kept."""
    return Table(
        [(f.name, f.type) for f in fields],
        columns,
        {f.name: f.meta for f in fields if not f.meta.is_empty},
    )


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


@dataclass(frozen=True)
class Rounding:
    """A rounding policy (Phase 2001): a SCALE — decimal places, a literal ``int`` or a named
    :class:`Param` — and a MODE, one of :data:`ROUNDING_MODES`. A scale outside ``0 .. 1000`` is
    refused by name at evaluation.

    Wire: ``{"mode":"half-even","scale":4}``; a param scale is ``{"$param":"<name>"}``."""

    scale: int | Param
    mode: str  # one of ROUNDING_MODES


@dataclass(frozen=True)
class Quotient:
    """The quotient of two EXACT numbers (``decimal`` or ``int``; an int promotes), correctly
    rounded to ``rounding`` — one rounding of the exact value, never two. A ``float`` operand is
    refused by name; a zero divisor answers null; a null operand propagates. The answer is a
    ``decimal``."""

    dividend: ColExpr
    divisor: ColExpr
    rounding: Rounding


@dataclass(frozen=True)
class Rounded:
    """An exact number (``decimal`` or ``int``) brought to ``rounding`` — the one general place a
    decimal loses digits. A ``float`` operand is refused by name; the answer is a ``decimal``."""

    expr: ColExpr
    rounding: Rounding


ColExpr = (
    Col | Lit | Binary | Not | Coalesce | Case | Cast | ApplyFn | Param | InList | InParam | IsNull | Quotient | Rounded
)

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
        # Phase 2400 — the power functions (the reference's D43).
        "pow",
        "exp",
        "ln",
        "log10",
    }
)
AGG_FNS: frozenset[str] = frozenset(
    {"sum", "mean", "min", "max", "count", "median", "stddev", "first", "last", "countDistinct"}
)
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


@dataclass(frozen=True)
class Intersect:
    """Keep the left rows whose FULL ROW also appears in ``source`` (SQL ``INTERSECT ALL``): the
    left's order and duplicate multiplicity survive, and row identity is the canonical cell token
    ``Distinct`` dedups on, so a null matches a null."""

    source: DataSource


@dataclass(frozen=True)
class Except:
    """``Intersect``'s complement: keep the left rows whose full row does NOT appear in ``source``
    (SQL ``EXCEPT ALL``)."""

    source: DataSource


Transform = (
    Filter
    | Project
    | Derive
    | GroupBy
    | Join
    | Window
    | Pivot
    | Unpivot
    | Sort
    | Distinct
    | Limit
    | Union
    | Intersect
    | Except
)


# ── Error envelopes ──────────────────────────────────────────────────────────
#
# Two closed, additive envelopes — the codec's seven-code ``ColumnError`` (a wire-shape
# violation) and the evaluator's ``EvalError`` (a pipeline-semantic failure). Both
# *name* the failure; a closed-set failure enumerates the alternatives.

# Codec codes
NOT_JSON = "NOT_JSON"
MISSING_FIELD = "MISSING_FIELD"
MALFORMED_SHAPE = "MALFORMED_SHAPE"
UNKNOWN_TYPE = "UNKNOWN_TYPE"
TYPE_MISMATCH = "TYPE_MISMATCH"
LENGTH_MISMATCH = "LENGTH_MISMATCH"
#: A §21 resource bound, kept distinct from ``NOT_JSON`` because §21.2 rule 2
#: forbids reporting a limit breach as a syntax error: the two send the author
#: to repair different things.
LIMIT_EXCEEDED = "LIMIT_EXCEEDED"


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
