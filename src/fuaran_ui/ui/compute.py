"""The polars-like Compute authoring surface — the data-science authoring win.

A data scientist writes a transform pipeline in **idiomatic, polars-shaped Python**;
it serialises to the canonical ``Transform`` wire form that every host's evaluator runs
identically (no Python in the browser — the JS evaluator runs it at native speed)::

    from fuaran_ui.ui import frame, col

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
from typing import Protocol, runtime_checkable

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
    InList,
    InParam,
    IsNull,
    Join,
    Limit,
    Lit,
    Not,
    Param,
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

    # membership ----------------------------------------------------------------
    def is_in(self, items: Expr | Sequence[object]) -> Expr:
        """Membership — ``col('region').is_in(param('regions'))`` or a literal list.

        The two spellings are two wire cases and the difference is load-bearing: a
        param names a slot a multi-select control writes, and an EMPTY selection is
        *unbound* rather than an empty set, so the dependent filter prunes and the
        unfiltered table shows. A literal list is a constraint the document carries.
        """
        if isinstance(items, Expr):
            if not isinstance(items.colexpr, Param):
                raise TypeError("is_in over an expression takes a param: col('x').is_in(param('xs'))")
            return Expr(InParam(self.colexpr, items.colexpr.name))
        return Expr(InList(self.colexpr, [_as_expr(item).colexpr for item in items]))

    # option derivation ---------------------------------------------------------
    def unique(self) -> OptionSource:
        """This column's distinct values, as a control's option list.

        Not an expression: it is a *declaration* that the option list is derived from
        the data rather than enumerated in the document, and it lowers to a
        ``Transform`` the host evaluates. Passing it where a predicate belongs fails
        by type, which is the point of it not being an :class:`Expr`.
        """
        if not isinstance(self.colexpr, Col):
            raise TypeError("unique() applies to a column expression, e.g. col('region').unique()")
        return OptionSource(self.colexpr.name)

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


def param(name: str) -> Expr:
    """A named hole a declared control fills — ``col('region').eq(param('region'))``.

    The name is the parameter's, and a control declares it: the ``Transform`` binding
    carries a ``params`` entry pairing this name with the control's own slot, and the
    host substitutes the slot's current value before it evaluates. Nothing here reads a
    value; the artifact is still the pipeline.

    A param the frame has not been told about is refused when the binding is lowered
    (:meth:`Frame.to_transform_binding`), by name — the evaluator's ``UNBOUND_PARAM`` is
    the backstop for a pipeline that reached it some other way, never the first thing an
    author sees.
    """
    return Expr(Param(name))


def lit(value: object, type_: str | None = None) -> Expr:
    """A literal. ``type_`` pins a ``date`` / ``timestamp`` (otherwise inferred from the Python type)."""
    if type_ == "date" and isinstance(value, str):
        return Expr(Lit(cell_date(value)))
    if type_ == "timestamp" and isinstance(value, str):
        return Expr(Lit(cell_timestamp(value)))
    return Expr(Lit(_to_cell(value)))


# ── Parameter declarations (fuaran#1170) ─────────────────────────────────────


class UnboundParamError(ValueError):
    """A pipeline reads a parameter no declared control fills.

    Raised where the ``Transform`` binding is built, which is the last moment the
    author's own names are still in hand. The alternative is a tree that renders and
    silently drops the filter that mentions the name, in a browser, on someone else's
    machine.
    """


@dataclass(frozen=True)
class ParamDecl:
    """One ``params`` entry: a parameter name, and the slot its value is read from.

    ``source`` is an ordinary :class:`~fuaran_ui.schema.types.Binding` — a control's
    ``State`` slot, a filter, or a literal — so a parameter is bound to the same
    vocabulary everything else in the tree reads from, and nothing new is minted for it.
    """

    name: str
    source: object  # a `schema.types` Binding — anything that lowers via `to_wire()`

    def to_wire(self) -> WireValue:
        lowered = self.source
        to_wire = getattr(lowered, "to_wire", None)
        if not callable(to_wire):
            raise TypeError(f"param {self.name!r}: source must be a Binding, got {type(self.source).__name__}")
        return Obj(None, {"from": to_wire(), "name": self.name})


@dataclass(frozen=True)
class OptionSource:
    """``col(name).unique()`` — a control's option list, derived from the data.

    Lowered by a control constructor against whichever source the control was given, to
    a pipeline projecting the column to ``value``, de-duplicating, ordering, and copying
    it to ``label``. The two column names are the shape a host reads an option list in,
    so the derived table IS the option list rather than something a second step has to
    convert.
    """

    column: str

    def pipeline(self) -> tuple[Transform, ...]:
        """The steps that turn a table into ``(value, label)`` option rows."""
        return (
            Project([(self.column, "value")]),
            Distinct(),
            Sort([("value", "asc")]),
            Derive("label", Col("value")),
        )


def _expr_param_names(expr: ColExpr) -> set[str]:
    """Every parameter name an expression reads, scalar and list alike.

    The twin of the host resolver's own walk (:mod:`fuaran_ui.compute.evaluate`), and
    deliberately total over the closed ``ColExpr`` union: a case this misses is a name
    the author-time check would not refuse and the browser would.
    """
    if isinstance(expr, Param):
        return {expr.name}
    if isinstance(expr, InParam):
        return _expr_param_names(expr.expr) | {expr.param}
    if isinstance(expr, Binary):
        return _expr_param_names(expr.left) | _expr_param_names(expr.right)
    if isinstance(expr, (Not, Cast, IsNull)):
        return _expr_param_names(expr.expr)
    if isinstance(expr, Coalesce):
        return {n for x in expr.exprs for n in _expr_param_names(x)}
    if isinstance(expr, ApplyFn):
        return {n for x in expr.args for n in _expr_param_names(x)}
    if isinstance(expr, InList):
        return _expr_param_names(expr.expr) | {n for x in expr.items for n in _expr_param_names(x)}
    if isinstance(expr, Case):
        names = _expr_param_names(expr.else_expr)
        for when_e, then_e in expr.cases:
            names |= _expr_param_names(when_e) | _expr_param_names(then_e)
        return names
    return set()  # Col, Lit


def _pipeline_param_names(pipeline: Sequence[Transform]) -> set[str]:
    """Every parameter name a pipeline reads. ``filter`` and ``derive`` are the only
    steps carrying an expression, which is the same pair the host substitutes over."""
    names: set[str] = set()
    for step in pipeline:
        if isinstance(step, Filter):
            names |= _expr_param_names(step.pred)
        elif isinstance(step, Derive):
            names |= _expr_param_names(step.expr)
    return names


@runtime_checkable
class _ParamSource(Protocol):
    """Anything that declares parameters — a control, or a bare :class:`ParamDecl`."""

    @property
    def params(self) -> tuple[ParamDecl, ...]: ...


def _declarations_of(source: ParamDecl | _ParamSource) -> tuple[ParamDecl, ...]:
    if isinstance(source, ParamDecl):
        return (source,)
    declared = getattr(source, "params", None)
    if declared is None:
        raise TypeError(
            f"bind() takes controls or ParamDecls; {type(source).__name__} declares no parameters. "
            "A control built by `fuaran_ui.ui.control` carries its own."
        )
    return tuple(declared)


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
    #: fuaran#1170 — the parameters declared for this frame, in declaration order. A
    #: frame that declares none emits no ``params`` key, so every pipeline authored
    #: before controls existed encodes byte-identically.
    params: tuple[ParamDecl, ...] = ()

    def _step(self, t: Transform) -> Frame:
        return replace(self, pipeline=(*self.pipeline, t))

    # parameter declaration ------------------------------------------------------
    def bind(self, *sources: ParamDecl | _ParamSource) -> Frame:
        """Declare the controls (or bare :class:`ParamDecl`\\ s) this pipeline reads.

        Binding is what makes :func:`param` resolvable, and declaring the same name
        twice with two different slots is refused here rather than left to a host: one
        slot cannot hold two values, and a renderer that had to pick would pick
        differently from the next one. Re-declaring the SAME pairing is a no-op, so
        binding one control to two frames costs nothing.
        """
        declared = {d.name: d for d in self.params}
        added: list[ParamDecl] = []
        for source in sources:
            for decl in _declarations_of(source):
                existing = declared.get(decl.name)
                if existing is None:
                    declared[decl.name] = decl
                    added.append(decl)
                elif existing.source != decl.source:
                    raise UnboundParamError(
                        f"parameter {decl.name!r} is already declared from a different slot; "
                        "one parameter names one slot, so rename one of the controls."
                    )
        return replace(self, params=(*self.params, *added))

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
        """The ``Binding.Transform`` authoring value — usable as a data-bound node source.

        This is where an **unbound parameter is refused**: every name the pipeline reads
        must have been declared by :meth:`bind`. The refusal names the missing parameters
        and what IS declared, because the commonest cause is a spelling — a range control
        declares ``<name>_min`` / ``<name>_max``, not ``<name>``.
        """
        declared = {d.name for d in self.params}
        missing = sorted(_pipeline_param_names(self.pipeline) - declared)
        if missing:
            known = ", ".join(sorted(declared)) if declared else "nothing"
            raise UnboundParamError(
                f"the pipeline reads parameter(s) {', '.join(repr(m) for m in missing)} that no "
                f"declared control fills; declared: {known}. Pass the control(s) to .bind(...) "
                "before lowering the binding."
            )
        return TransformBinding(self.source, tuple(self.pipeline), tuple(self.params))

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
    #: fuaran#1170 — the declared parameters. OMITTED from the wire when empty, so a
    #: parameter-free binding is byte-identical to what this class emitted before.
    params: tuple[ParamDecl, ...] = ()

    def to_wire(self) -> WireValue:
        fields: dict[str, WireValue] = {
            "pipeline": Arr([_codec.encode_transform_value(t) for t in self.pipeline]),
            "source": _codec.encode_source_value(self.source),
        }
        if self.params:
            fields["params"] = Arr([p.to_wire() for p in self.params])
        return Obj("Transform", fields)


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
