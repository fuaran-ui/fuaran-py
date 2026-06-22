"""The polars-like Compute authoring surface — the data-science authoring win.

A data scientist writes a transform pipeline in **idiomatic, polars-shaped Python**;
it serialises to the canonical ``Transform`` wire form that every host's evaluator runs
identically (no Python in the browser — the JS evaluator runs it at native speed)::

    from fuaran_py.ui import frame, col

    fr = (
        frame({"dept": ["eng", "eng", "sales"], "amount": [100, 120, None]},
              schema={"dept": "string", "amount": "int"})
        .filter(col("amount") > 0)
        .group_by("dept").agg(col("amount").sum().alias("total"))
        .sort("total", descending=True)
    )
    wire = fr.to_transform_json()   # canonical {"$type":"Transform","pipeline":[…],"source":{…}}

The expression DSL (:class:`Expr`) overloads Python operators (``>`` ``+`` ``&`` ``~`` …)
into the serializable ``ColExpr`` algebra; the :class:`Frame` builder accumulates an
ordered pipeline of ``Transform`` steps. ``frame(...).collect()`` runs the **same**
reference evaluator locally for a preview — but the artifact a Fuaran app ships is the
*pipeline*, not the result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from ..canonical import encode_value
from ..dataframe import (
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
    DataSource,
    Derive,
    Distinct,
    Embedded,
    Filter,
    GroupBy,
    Join,
    Limit,
    Lit,
    Not,
    Pivot,
    PivotSpec,
    Project,
    Ref,
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
    eval_pipeline,
)
from ..dataframe import codec as _codec
from ..dataframe.model import NULL
from ..model import Arr, Obj
from ..model import Value as WireValue

# ── Expression DSL ───────────────────────────────────────────────────────────


def _to_cell(value: object) -> Cell:
    """Coerce a bare Python scalar into a literal cell (``None`` ⇒ null)."""
    if value is None:
        return NULL
    if isinstance(value, bool):
        return cell_bool(value)
    if isinstance(value, int):
        return cell_int(value)
    if isinstance(value, float):
        return cell_float(value)
    if isinstance(value, str):
        return cell_str(value)
    raise TypeError(f"cannot lift {type(value)!r} to a literal — use lit(...) with an explicit type")


def _as_expr(value: object) -> Expr:
    if isinstance(value, Expr):
        return value
    return Expr(Lit(_to_cell(value)))


@dataclass(frozen=True)
class Expr:
    """A scalar expression — a thin, operator-overloaded wrapper over ``ColExpr``."""

    colexpr: ColExpr

    # arithmetic ----------------------------------------------------------------
    def __add__(self, o: object) -> Expr:
        return Expr(Binary("add", self.colexpr, _as_expr(o).colexpr))

    def __radd__(self, o: object) -> Expr:
        return _as_expr(o).__add__(self)

    def __sub__(self, o: object) -> Expr:
        return Expr(Binary("sub", self.colexpr, _as_expr(o).colexpr))

    def __rsub__(self, o: object) -> Expr:
        return _as_expr(o).__sub__(self)

    def __mul__(self, o: object) -> Expr:
        return Expr(Binary("mul", self.colexpr, _as_expr(o).colexpr))

    def __rmul__(self, o: object) -> Expr:
        return _as_expr(o).__mul__(self)

    def __truediv__(self, o: object) -> Expr:
        return Expr(Binary("div", self.colexpr, _as_expr(o).colexpr))

    def __rtruediv__(self, o: object) -> Expr:
        return _as_expr(o).__truediv__(self)

    def __mod__(self, o: object) -> Expr:
        return Expr(Binary("mod", self.colexpr, _as_expr(o).colexpr))

    def __rmod__(self, o: object) -> Expr:
        return _as_expr(o).__mod__(self)

    # comparison ----------------------------------------------------------------
    def __gt__(self, o: object) -> Expr:
        return Expr(Binary("gt", self.colexpr, _as_expr(o).colexpr))

    def __ge__(self, o: object) -> Expr:
        return Expr(Binary("ge", self.colexpr, _as_expr(o).colexpr))

    def __lt__(self, o: object) -> Expr:
        return Expr(Binary("lt", self.colexpr, _as_expr(o).colexpr))

    def __le__(self, o: object) -> Expr:
        return Expr(Binary("le", self.colexpr, _as_expr(o).colexpr))

    def eq(self, o: object) -> Expr:
        """Wire equality (``==`` is reserved for Python identity in dict keys / sets)."""
        return Expr(Binary("eq", self.colexpr, _as_expr(o).colexpr))

    def ne(self, o: object) -> Expr:
        return Expr(Binary("ne", self.colexpr, _as_expr(o).colexpr))

    # logical -------------------------------------------------------------------
    def __and__(self, o: object) -> Expr:
        return Expr(Binary("and", self.colexpr, _as_expr(o).colexpr))

    def __or__(self, o: object) -> Expr:
        return Expr(Binary("or", self.colexpr, _as_expr(o).colexpr))

    def __invert__(self) -> Expr:
        return Expr(Not(self.colexpr))

    # transforms ----------------------------------------------------------------
    def cast(self, type_: str) -> Expr:
        return Expr(Cast(type_, self.colexpr))

    def coalesce(self, *others: object) -> Expr:
        return Expr(Coalesce([self.colexpr, *(_as_expr(o).colexpr for o in others)]))

    def abs(self) -> Expr:
        return Expr(ApplyFn("abs", [self.colexpr]))

    def round(self) -> Expr:
        return Expr(ApplyFn("round", [self.colexpr]))

    def floor(self) -> Expr:
        return Expr(ApplyFn("floor", [self.colexpr]))

    def ceil(self) -> Expr:
        return Expr(ApplyFn("ceil", [self.colexpr]))

    def length(self) -> Expr:
        return Expr(ApplyFn("length", [self.colexpr]))

    def lower(self) -> Expr:
        return Expr(ApplyFn("lower", [self.colexpr]))

    def upper(self) -> Expr:
        return Expr(ApplyFn("upper", [self.colexpr]))

    def substr(self, start: int, length: int) -> Expr:
        return Expr(ApplyFn("substr", [self.colexpr, Lit(cell_int(start)), Lit(cell_int(length))]))

    def date_part(self, part: str) -> Expr:
        return Expr(ApplyFn("datePart", [Lit(cell_str(part)), self.colexpr]))

    # aggregation ---------------------------------------------------------------
    def _agg(self, fn: str) -> AggExpr:
        if not isinstance(self.colexpr, Col):
            raise TypeError("aggregations apply to a column expression, e.g. col('x').sum()")
        return AggExpr(fn=fn, of=self.colexpr.name, name=self.colexpr.name)

    def sum(self) -> AggExpr:
        return self._agg("sum")

    def mean(self) -> AggExpr:
        return self._agg("mean")

    def min(self) -> AggExpr:
        return self._agg("min")

    def max(self) -> AggExpr:
        return self._agg("max")

    def count(self) -> AggExpr:
        return self._agg("count")

    def median(self) -> AggExpr:
        return self._agg("median")

    def std(self) -> AggExpr:
        return self._agg("stddev")

    def first(self) -> AggExpr:
        return self._agg("first")

    def last(self) -> AggExpr:
        return self._agg("last")

    # naming --------------------------------------------------------------------
    def alias(self, name: str) -> _Named:
        """Name a derived column (for :meth:`Frame.select` rename or a single derive)."""
        return _Named(name, self)


@dataclass(frozen=True)
class AggExpr:
    """An aggregate over a single column — name it with :meth:`alias` (defaults to the of-column)."""

    fn: str
    of: str
    name: str

    def alias(self, name: str) -> AggExpr:
        return replace(self, name=name)

    def to_agg(self) -> Agg:
        return Agg(self.name, self.fn, self.of)


@dataclass(frozen=True)
class _Named:
    name: str
    expr: Expr


def col(name: str) -> Expr:
    """A column reference — the root of the expression DSL."""
    return Expr(Col(name))


def lit(value: object, type_: str | None = None) -> Expr:
    """A literal. ``type_`` pins a ``date`` / ``timestamp`` (otherwise inferred from the Python type)."""
    if type_ == "date" and isinstance(value, str):
        return Expr(Lit(cell_date(value)))
    if type_ == "timestamp" and isinstance(value, str):
        return Expr(Lit(cell_timestamp(value)))
    return Expr(Lit(_to_cell(value)))


# ── when / then / otherwise (Case) ───────────────────────────────────────────


class _WhenBuilder:
    def __init__(self, branches: list[tuple[ColExpr, ColExpr]], pending: Expr) -> None:
        self._branches = branches
        self._pending = pending

    def then(self, value: object) -> _ThenBuilder:
        return _ThenBuilder([*self._branches, (self._pending.colexpr, _as_expr(value).colexpr)])


class _ThenBuilder:
    def __init__(self, branches: list[tuple[ColExpr, ColExpr]]) -> None:
        self._branches = branches

    def when(self, cond: Expr) -> _WhenBuilder:
        return _WhenBuilder(self._branches, cond)

    def otherwise(self, value: object) -> Expr:
        return Expr(Case(self._branches, _as_expr(value).colexpr))


def when(cond: Expr) -> _WhenBuilder:
    """Start a ``when(cond).then(a).when(cond2).then(b).otherwise(c)`` case chain."""
    return _WhenBuilder([], cond)


# ── Source authoring (embedded columns from Python data, or a named ref) ─────


def _infer_type(values: Sequence[object]) -> str:
    present = [v for v in values if v is not None]
    if present and all(isinstance(v, bool) for v in present):
        return "bool"
    if present and all(isinstance(v, int) and not isinstance(v, bool) for v in present):
        return "int"
    if present and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in present):
        return "float"
    return "string"


def _cell_of(ty: str, v: object) -> Cell:
    if v is None:
        return NULL
    if ty == "int":
        assert isinstance(v, (int, float))
        return cell_int(int(v))
    if ty == "float":
        assert isinstance(v, (int, float))
        return cell_float(float(v))
    if ty == "bool":
        return cell_bool(bool(v))
    if ty == "date":
        return cell_date(str(v))
    if ty == "timestamp":
        return cell_timestamp(str(v))
    return cell_str(str(v))


def _normalise_schema(
    data: Mapping[str, Sequence[object]], schema: Mapping[str, str] | Sequence[tuple[str, str]] | None
) -> list[tuple[str, str]]:
    if schema is None:
        return [(name, _infer_type(values)) for name, values in data.items()]
    if isinstance(schema, Mapping):
        return [(name, schema[name]) for name in data]
    return list(schema)


def _build_source(
    data: Mapping[str, Sequence[object]], schema: Mapping[str, str] | Sequence[tuple[str, str]] | None
) -> DataSource:
    resolved = _normalise_schema(data, schema)
    columns = [Column(name, ty, [_cell_of(ty, v) for v in data[name]]) for name, ty in resolved]
    return Embedded(Table(resolved, columns))


# ── Frame builder ─────────────────────────────────────────────────────────────


def _resolve_pairs(on: str | Sequence[str] | Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
    if isinstance(on, str):
        return [(on, on)]
    out: list[tuple[str, str]] = []
    for entry in on:
        if isinstance(entry, str):
            out.append((entry, entry))
        else:
            out.append((entry[0], entry[1]))
    return out


def _resolve_order(by: str | Sequence[str], descending: bool | Sequence[bool]) -> list[tuple[str, str]]:
    cols = [by] if isinstance(by, str) else list(by)
    if isinstance(descending, bool):
        dirs = [descending] * len(cols)
    else:
        dirs = list(descending)
    return [(c, "desc" if d else "asc") for c, d in zip(cols, dirs, strict=True)]


@dataclass(frozen=True)
class Frame:
    """An immutable, chainable transform builder over a :class:`DataSource`.

    Each verb returns a new ``Frame`` with one more pipeline step; nothing runs until
    :meth:`collect` (local preview) or the artifact is emitted (:meth:`to_transform_json`)."""

    source: DataSource
    pipeline: tuple[Transform, ...] = ()

    def _step(self, t: Transform) -> Frame:
        return Frame(self.source, (*self.pipeline, t))

    # row / column verbs --------------------------------------------------------
    def filter(self, predicate: Expr) -> Frame:
        return self._step(Filter(predicate.colexpr))

    def select(self, *columns: str | _Named) -> Frame:
        pairs: list[tuple[str, str]] = []
        for c in columns:
            if isinstance(c, str):
                pairs.append((c, c))
            else:
                if not isinstance(c.expr.colexpr, Col):
                    raise TypeError("select renames a column: col('a').alias('b')")
                pairs.append((c.expr.colexpr.name, c.name))
        return self._step(Project(pairs))

    def derive(self, name: str, expr: Expr) -> Frame:
        return self._step(Derive(name, expr.colexpr))

    def with_column(self, name: str, expr: Expr) -> Frame:
        return self.derive(name, expr)

    def with_columns(self, **named: Expr) -> Frame:
        f = self
        for name, expr in named.items():
            f = f.derive(name, expr)
        return f

    # grouping ------------------------------------------------------------------
    def group_by(self, *keys: str) -> _GroupBy:
        return _GroupBy(self, list(keys))

    # ordering / dedup / slice --------------------------------------------------
    def sort(self, by: str | Sequence[str], descending: bool | Sequence[bool] = False) -> Frame:
        return self._step(Sort(_resolve_order(by, descending)))

    def distinct(self) -> Frame:
        return self._step(Distinct())

    def limit(self, n: int, offset: int = 0) -> Frame:
        return self._step(Limit(n, offset))

    def head(self, n: int) -> Frame:
        return self.limit(n, 0)

    # joins / set ops -----------------------------------------------------------
    def join(
        self,
        other: Frame | DataSource,
        on: str | Sequence[str] | Sequence[tuple[str, str]],
        how: str = "inner",
    ) -> Frame:
        right = other.source if isinstance(other, Frame) else other
        return self._step(Join(right, _resolve_pairs(on), how))

    def union(self, other: Frame | DataSource) -> Frame:
        right = other.source if isinstance(other, Frame) else other
        return self._step(Union(right))

    # window / reshape ----------------------------------------------------------
    def window(
        self,
        fn: str,
        of: str,
        as_: str,
        partition_by: Sequence[str] = (),
        order_by: str | Sequence[str] = (),
        descending: bool | Sequence[bool] = False,
    ) -> Frame:
        ob = _resolve_order(order_by, descending) if order_by else []
        return self._step(Window(WindowSpec(list(partition_by), ob, fn, of, as_)))

    def pivot(self, index: str | Sequence[str], on: str, values: str, agg: str = "sum") -> Frame:
        idx = [index] if isinstance(index, str) else list(index)
        return self._step(Pivot(PivotSpec(idx, on, values, agg)))

    def unpivot(self, id_vars: Sequence[str], value_vars: Sequence[str]) -> Frame:
        return self._step(Unpivot(list(id_vars), list(value_vars)))

    # emission ------------------------------------------------------------------
    def to_pipeline(self) -> list[Transform]:
        return list(self.pipeline)

    def to_pipeline_json(self) -> str:
        """The canonical wire string for the ordered pipeline (a ``Transform`` array)."""
        return _codec.encode_pipeline(list(self.pipeline))

    def to_transform_binding(self) -> TransformBinding:
        """The ``Binding.Transform`` authoring value — usable as a data-bound node source."""
        return TransformBinding(self.source, tuple(self.pipeline))

    def to_transform_json(self) -> str:
        """The canonical ``{"$type":"Transform","pipeline":[…],"source":{…}}`` wire string."""
        return encode_value(self.to_transform_binding().to_wire())

    # local preview -------------------------------------------------------------
    def collect(self) -> Table:
        """Run the **same** reference evaluator locally (a preview; the artifact is the pipeline)."""
        if not isinstance(self.source, Embedded):
            raise ValueError("collect() needs an embedded source; a Ref source resolves host-side")
        result = eval_pipeline(list(self.pipeline), self.source.table)
        if not result.ok:
            raise ValueError(f"pipeline evaluation failed: {result.error.detail}")
        return result.value


@dataclass(frozen=True)
class _GroupBy:
    frame: Frame
    keys: list[str]

    def agg(self, *aggs: AggExpr) -> Frame:
        return self.frame._step(GroupBy(self.keys, [a.to_agg() for a in aggs]))


@dataclass(frozen=True)
class TransformBinding:
    """The ``Binding.Transform`` authoring value (a source + a pipeline).

    Lowers to ``{"$type":"Transform","pipeline":[…],"source":{…}}`` — pass it as a node
    ``source`` (e.g. ``fuaran.grid(..., source=fr.to_transform_binding())``)."""

    source: DataSource
    pipeline: tuple[Transform, ...]

    def to_wire(self) -> WireValue:
        return Obj(
            "Transform",
            {
                "pipeline": Arr([_codec.encode_transform_value(t) for t in self.pipeline]),
                "source": _codec.encode_source_value(self.source),
            },
        )


def frame(
    data: Mapping[str, Sequence[object]] | DataSource,
    schema: Mapping[str, str] | Sequence[tuple[str, str]] | None = None,
) -> Frame:
    """Open a :class:`Frame` over embedded columns (``{"col": [...]}``) or a ``DataSource``.

    ``schema`` (``{name: type}``) pins column types; omit it to infer from the data
    (``date`` / ``timestamp`` need an explicit schema)."""
    if isinstance(data, (Embedded, Ref)):
        return Frame(data)
    return Frame(_build_source(data, schema))


def source_ref(name: str, schema: Mapping[str, str] | Sequence[tuple[str, str]]) -> DataSource:
    """A named source the host resolves (the wire carries the name + schema, never rows)."""
    # The Ref wire carries only the name + an (empty) schema; `schema` documents the
    # column contract the host must satisfy when it resolves the source.
    return Ref(name)


def transform(fr: Frame) -> TransformBinding:
    """The ``Binding.Transform`` for a frame — a node-source-shaped authoring value."""
    return fr.to_transform_binding()
