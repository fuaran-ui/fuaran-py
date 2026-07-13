"""The compute-layer host resolver — a wire-declared compute graph → derived values.

A data-bearing node (``DataGrid`` / ``Chart`` / ``Table`` / ``Metric``) may carry a
``Binding.Transform`` ``source``: an embedded ``DataSource`` + a ``Transform`` pipeline
+ optional ``parameters`` bound to the host state store. This module is the Python leg
of the F# ``BindingResolver`` compute path (Phases 282/283, TS parity 284): it resolves
the params from a host ``state`` map, evaluates the pipeline via the corpus-certified
:func:`~fuaran_py.dataframe.eval_pipeline`, and yields the derived rows.

Semantics pinned to the reference host:

* **Parameter resolution.** Each ``parameters`` entry binds a ``Param`` name to a scalar
  source (``Filter`` / ``Selection`` / ``State`` / ``Static``) resolved against ``state``.
* **Lenient filter pruning.** A ``Filter`` step referencing an *unbound* param is dropped
  ("an unset filter is no constraint") — the one host-side leniency; the core evaluator
  stays strict, so a bound param substitutes to its literal and an unbound param that
  reaches a non-filter step is a named ``UNBOUND_PARAM`` failure, never a guess.
* **Embedded only.** A ``Ref`` source is ``UNRESOLVED_SOURCE`` (Phase 282 evaluates
  embedded sources), matching the reference.
* **Output shape.** Rows are ``list[dict[str, object]]`` keyed by column name, a null cell
  boxed to ``None`` — the F# ``cellToObj`` row projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..canonical import encode_value
from ..dataframe import (
    ApplyFn,
    Binary,
    Case,
    Cast,
    Cell,
    Coalesce,
    ColExpr,
    Derive,
    Embedded,
    EvalError,
    Filter,
    Lit,
    Not,
    Param,
    Table,
    Transform,
    cell_bool,
    cell_float,
    cell_int,
    cell_str,
    decode_pipeline,
    decode_source,
    eval_pipeline,
)
from ..dataframe.model import NULL, UNRESOLVED_SOURCE, is_null
from ..model import Arr, Node, Obj, Value

# ── result ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ComputeOk:
    """Derived rows from a compute graph — column-name-keyed dicts (null cell → ``None``)."""

    rows: list[dict[str, object]]
    ok: Literal[True] = True


@dataclass(frozen=True)
class ComputeErr:
    """A named compute failure (decode, unresolved source, unbound param, eval error)."""

    error: EvalError
    ok: Literal[False] = False


type ComputeResult = ComputeOk | ComputeErr


# ── scalars + param resolution ───────────────────────────────────────────────


def _scalar_to_cell(value: Value) -> Cell:
    if isinstance(value, bool):
        return cell_bool(value)
    if isinstance(value, int):
        return cell_int(value)
    if isinstance(value, float):
        return cell_float(value)
    if isinstance(value, str):
        return cell_str(value)
    return NULL


# A host state store: parameter name → its current scalar value.
type ComputeState = dict[str, object]


def resolve_param_binding(binding: Value, state: ComputeState) -> Cell | None:
    """Resolve a parameter's ``from`` binding against the host ``state`` store, or
    ``None`` when it is unbound (the filter-pruning trigger)."""
    if not isinstance(binding, Obj):
        return None
    if binding.tag == "Static":
        return _scalar_to_cell(binding.fields.get("value"))
    if binding.tag in ("Filter", "Selection"):
        name = binding.fields.get("name")
        if isinstance(name, str) and name in state:
            return _scalar_to_cell(state[name])  # type: ignore[arg-type]
        return None
    if binding.tag == "State":
        key = binding.fields.get("key")
        if isinstance(key, str):
            if key in state:
                return _scalar_to_cell(state[key])  # type: ignore[arg-type]
            default = binding.fields.get("defaultValue")
            return _scalar_to_cell(default) if default is not None else None
    return None


# ── param names / substitution / pruning ─────────────────────────────────────


def _expr_param_names(expr: ColExpr) -> set[str]:
    if isinstance(expr, Param):
        return {expr.name}
    if isinstance(expr, Binary):
        return _expr_param_names(expr.left) | _expr_param_names(expr.right)
    if isinstance(expr, Not):
        return _expr_param_names(expr.expr)
    if isinstance(expr, Coalesce):
        return set().union(*(_expr_param_names(x) for x in expr.exprs)) if expr.exprs else set()
    if isinstance(expr, Case):
        names: set[str] = _expr_param_names(expr.else_expr)
        for when_e, then_e in expr.cases:
            names |= _expr_param_names(when_e) | _expr_param_names(then_e)
        return names
    if isinstance(expr, Cast):
        return _expr_param_names(expr.expr)
    if isinstance(expr, ApplyFn):
        return set().union(*(_expr_param_names(x) for x in expr.args)) if expr.args else set()
    return set()  # Col, Lit


def _substitute_expr(expr: ColExpr, env: dict[str, Cell]) -> ColExpr:
    if isinstance(expr, Param):
        return Lit(env[expr.name]) if expr.name in env else expr
    if isinstance(expr, Binary):
        return Binary(expr.op, _substitute_expr(expr.left, env), _substitute_expr(expr.right, env))
    if isinstance(expr, Not):
        return Not(_substitute_expr(expr.expr, env))
    if isinstance(expr, Coalesce):
        return Coalesce([_substitute_expr(x, env) for x in expr.exprs])
    if isinstance(expr, Case):
        return Case(
            [(_substitute_expr(w, env), _substitute_expr(t, env)) for w, t in expr.cases],
            _substitute_expr(expr.else_expr, env),
        )
    if isinstance(expr, Cast):
        return Cast(expr.type, _substitute_expr(expr.expr, env))
    if isinstance(expr, ApplyFn):
        return ApplyFn(expr.fn, [_substitute_expr(x, env) for x in expr.args])
    return expr  # Col, Lit


def _prune_and_substitute(pipeline: list[Transform], env: dict[str, Cell]) -> list[Transform]:
    """Drop filters referencing an unbound param, then substitute bound params."""
    bound = set(env)
    out: list[Transform] = []
    for step in pipeline:
        if isinstance(step, Filter):
            if _expr_param_names(step.pred) - bound:
                continue  # unbound-param filter → no constraint (host leniency)
            out.append(Filter(_substitute_expr(step.pred, env)))
        elif isinstance(step, Derive):
            out.append(Derive(step.name, _substitute_expr(step.expr, env)))
        else:
            out.append(step)
    return out


# ── row projection ────────────────────────────────────────────────────────────


def rows_of(table: Table) -> list[dict[str, object]]:
    """Project a :class:`Table` to column-name-keyed rows (null cell → ``None``)."""
    by_name = {c.name: c for c in table.columns}
    count = len(table.columns[0].cells) if table.columns else 0
    rows: list[dict[str, object]] = []
    for i in range(count):
        row: dict[str, object] = {}
        for name, _ in table.schema:
            col = by_name.get(name)
            cell = col.cells[i] if col is not None and i < len(col.cells) else NULL
            row[name] = None if is_null(cell) else cell.value
        rows.append(row)
    return rows


# ── the resolver ──────────────────────────────────────────────────────────────


def evaluate_transform(transform: Obj, state: ComputeState) -> ComputeResult:
    """Evaluate one decoded ``Binding.Transform`` against the host ``state`` store."""
    source_value = transform.fields.get("source")
    pipeline_value = transform.fields.get("pipeline")
    if source_value is None or pipeline_value is None:
        return ComputeErr(EvalError("TYPE_ERROR", "Transform binding missing 'source' or 'pipeline'"))

    src = decode_source(encode_value(source_value))
    if not src.ok:
        return ComputeErr(EvalError(src.error.code, src.error.detail))
    if not isinstance(src.value, Embedded):
        return ComputeErr(EvalError(UNRESOLVED_SOURCE, "compute evaluates embedded sources only"))

    pipe = decode_pipeline(encode_value(pipeline_value))
    if not pipe.ok:
        return ComputeErr(EvalError(pipe.error.code, pipe.error.detail))

    env: dict[str, Cell] = {}
    params = transform.fields.get("params")
    if isinstance(params, Arr):
        for entry in params.items:
            if isinstance(entry, Obj):
                name = entry.fields.get("name")
                if isinstance(name, str):
                    cell = resolve_param_binding(entry.fields.get("from"), state)
                    if cell is not None:
                        env[name] = cell

    effective = _prune_and_substitute(pipe.value, env)
    result = eval_pipeline(effective, src.value.table)
    if not result.ok:
        return ComputeErr(result.error)
    return ComputeOk(rows_of(result.value))


# ── tree walk ─────────────────────────────────────────────────────────────────


def _child_nodes(node: Node) -> list[Node]:
    out: list[Node] = []
    fields = node.kind.fields
    children = fields.get("children")
    if isinstance(children, Arr):
        out.extend(c for c in children.items if isinstance(c, Node))
    for key in ("child", "fallback", "body", "default"):
        value = fields.get(key)
        if isinstance(value, Node):
            out.append(value)
    cases = fields.get("cases")
    if isinstance(cases, Arr):
        for case in cases.items:
            if isinstance(case, Obj):
                case_child = case.fields.get("child")
                if isinstance(case_child, Node):
                    out.append(case_child)
    state_extra = node.extras.get("state")
    if isinstance(state_extra, Obj):
        for key in ("onLoading", "onEmpty"):
            value = state_extra.fields.get(key)
            if isinstance(value, Node):
                out.append(value)
    return out


def evaluate_tree(tree: Node, state: ComputeState) -> dict[str, ComputeResult]:
    """Evaluate every ``Binding.Transform`` ``source`` in the tree against ``state``,
    keyed by the owning node id."""
    derived: dict[str, ComputeResult] = {}
    stack = [tree]
    while stack:
        node = stack.pop()
        source = node.kind.fields.get("source")
        if isinstance(source, Obj) and source.tag == "Transform":
            derived[node.id] = evaluate_transform(source, state)
        stack.extend(reversed(_child_nodes(node)))
    return derived
