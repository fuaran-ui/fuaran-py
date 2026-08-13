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
  Resolution is three-valued (:data:`ParamResolution`): a scalar cell, *unbound*, or a
  **non-scalar** source — a structured value in a scalar slot, e.g. a whole row written
  into a ``field``-less ``Selection``. The last is a named failure, matching the reference
  hosts' ``Transform param '<name>' resolved to a non-scalar value``; boxing it to a NULL
  cell would prune or match nothing and read as empty data rather than a broken binding.
* **Declared defaults seed the param.** An unwritten ``Selection`` (0.2.9) / ``Filter``
  (0.2.0) / ``State`` binding falls back to its own ``defaultValue`` — so the param is
  *bound* and its filter is evaluated, not pruned. This is the pre-selected-row /
  pre-selected-filter mechanism: resolution-time defaulting, no store seeding. A
  ``Selection`` reads ``nodeId`` (its identity key) and a declared ``field`` projects
  that column off the written row.
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

import json
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
#
# Param resolution is THREE-valued, not two: the reference hosts split it into a
# ``Resolution`` (Resolved / NotResolved) and a scalar projection
# (``value_to_cell`` / ``objToCell``) whose failure is a loud error. Collapsing
# the two — as a ``Cell | None`` return must — boxes a structured value to the
# NULL cell, which then prunes or matches nothing and reads as *empty data*
# rather than a *broken binding*. Hence the tagged union below.


@dataclass(frozen=True)
class ParamResolved:
    """The param's source resolved to a scalar cell (JSON null included — the NULL cell)."""

    cell: Cell
    kind: Literal["resolved"] = "resolved"


@dataclass(frozen=True)
class ParamUnbound:
    """The param's source is unwritten and declares no default — the filter-pruning trigger."""

    kind: Literal["unbound"] = "unbound"


@dataclass(frozen=True)
class ParamNonScalar:
    """The param's source resolved to a STRUCTURED value, which has no scalar form.

    The loud twin of the reference hosts' ``value_to_cell`` / ``objToCell``
    returning nothing — e.g. a row ``Obj`` written by a grid row-click into a
    ``field``-less ``Selection`` slot. ``detail`` carries the cross-host message
    verbatim."""

    detail: str
    kind: Literal["nonScalar"] = "nonScalar"


type ParamResolution = ParamResolved | ParamUnbound | ParamNonScalar


def _non_scalar(name: str) -> ParamNonScalar:
    return ParamNonScalar(f"Transform param '{name}' resolved to a non-scalar value")


def _scalar_to_cell(name: str, value: object) -> ParamResolution:
    """A resolved param source as a cell. JSON null / an absent value IS a scalar
    (the NULL cell, per the reference ``JVal::Null → Cell::Null``); a structured
    ``Obj`` / ``Arr`` / host record has no scalar form and resolves loudly."""
    if value is None:
        return ParamResolved(NULL)
    if isinstance(value, bool):
        return ParamResolved(cell_bool(value))
    if isinstance(value, int):
        return ParamResolved(cell_int(value))
    if isinstance(value, float):
        return ParamResolved(cell_float(value))
    if isinstance(value, str):
        return ParamResolved(cell_str(value))
    return _non_scalar(name)


# A host state store: parameter name → its current scalar value.
type ComputeState = dict[str, object]


def _declared_default(name: str, binding: Obj) -> ParamResolution:
    """The binding's declared ``defaultValue`` as a cell, or :class:`ParamUnbound`
    when it declares none. Resolution-time defaulting IS the pre-selection
    mechanism — ``Selection`` (0.2.9), ``Filter`` (0.2.0), ``State`` — so a param
    sourced from an unwritten-but-defaulted binding is **bound**, never pruned.
    A declared default that is itself structured is a non-scalar error, exactly
    as a written one would be."""
    if "defaultValue" not in binding.fields:
        return ParamUnbound()
    return _scalar_to_cell(name, binding.fields["defaultValue"])


def _project_selection(name: str, raw: object, field: Value) -> ParamResolution:
    """Project a written selection to its scalar. A declared ``field`` (0.2.10)
    names the column to read off the stored ROW — a grid's row-click writes the
    whole row, and a param slot is scalar. A field-less selection is already the
    scalar value; when it is NOT (the row itself landed in the slot) that is the
    non-scalar error, not a silent NULL."""
    if isinstance(field, str):
        if isinstance(raw, Obj):
            return _scalar_to_cell(name, raw.fields.get(field))
        if isinstance(raw, dict):
            return _scalar_to_cell(name, raw.get(field))
    return _scalar_to_cell(name, raw)


def resolve_param_binding(name: str, binding: Value, state: ComputeState) -> ParamResolution:
    """Resolve parameter ``name``'s ``from`` binding against the host ``state`` store.

    Mirrors the reference hosts' ``resolve`` + scalar projection: the written host
    value wins, and an unwritten binding falls back to its own declared
    ``defaultValue`` before it is called :class:`ParamUnbound` (the filter-pruning
    trigger). ``Selection`` keys on ``nodeId`` (the binding's identity key — the
    accessor sentinel is off the wire since 0.2.0), ``Filter`` / ``Query`` on
    ``name``, ``State`` on ``key``. A source that resolves to a structured value
    is :class:`ParamNonScalar`, never a silently-boxed NULL cell."""
    if not isinstance(binding, Obj):
        return ParamUnbound()
    if binding.tag == "Static":
        return _scalar_to_cell(name, binding.fields.get("value"))
    if binding.tag == "Filter":
        filter_name = binding.fields.get("name")
        if isinstance(filter_name, str) and filter_name in state:
            return _scalar_to_cell(name, state[filter_name])
        return _declared_default(name, binding)
    if binding.tag == "Selection":
        node_id = binding.fields.get("nodeId")
        if isinstance(node_id, str) and node_id in state:
            return _project_selection(name, state[node_id], binding.fields.get("field"))
        return _declared_default(name, binding)
    if binding.tag == "State":
        key = binding.fields.get("key")
        if isinstance(key, str):
            if key in state:
                return _scalar_to_cell(name, state[key])
            return _declared_default(name, binding)
    return ParamUnbound()


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


# fuaran#818 — the preserved LIVE Transform-source tags, each with its store
# identity key. A live source resolves against the flat state store first
# (subscription semantics' evaluation-time analogue), then falls back to the
# binding's carried defaultValue (the decode-time initial snapshot), then the
# empty table (a Selection / Query with nothing yet — zero rows).
_LIVE_SOURCE_KEYS = {"State": "key", "Selection": "nodeId", "Query": "name"}


def _transpose_row_major(data: object) -> object:
    """Row-major rows (a list of row dicts) transpose to the canonical columnar
    ``{"columns": …}`` shape — FIRST-row key set (sorted ordinal), absent cells
    ``None`` — the same fuaran#815 normalisation the decode-time snapshot used.
    Anything else passes through untouched."""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        rows = data
        columns = {k: [row.get(k) if isinstance(row, dict) else None for row in rows] for k in sorted(data[0])}
        return {"columns": columns}
    return data


def _live_source(source_value: Obj, state: ComputeState) -> object:
    """Materialise a live source's current data as raw columnar JSON: the
    host-seeded store value when present, else the carried defaultValue, else
    the empty table."""
    key = source_value.fields.get(_LIVE_SOURCE_KEYS[source_value.tag or ""])
    raw: object | None = state.get(key) if isinstance(key, str) else None
    if raw is None:
        dv = source_value.fields.get("defaultValue")
        raw = json.loads(encode_value(dv)) if dv is not None else None
    if raw is None:
        return {"columns": {}}
    return _transpose_row_major(raw)


def evaluate_transform(transform: Obj, state: ComputeState) -> ComputeResult:
    """Evaluate one decoded ``Binding.Transform`` against the host ``state`` store."""
    source_value = transform.fields.get("source")
    pipeline_value = transform.fields.get("pipeline")
    if source_value is None or pipeline_value is None:
        return ComputeErr(EvalError("TYPE_ERROR", "Transform binding missing 'source' or 'pipeline'"))

    if isinstance(source_value, Obj) and source_value.tag in _LIVE_SOURCE_KEYS:
        # fuaran#818 — a preserved LIVE source: evaluate over the CURRENT data
        # (host-seeded store value, else the initial snapshot). A non-tabular
        # live value surfaces the columnar codec's own didactic — loud, never a
        # silent wrong value.
        src = decode_source(json.dumps(_live_source(source_value, state), separators=(",", ":")))
    else:
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
                    resolution = resolve_param_binding(name, entry.fields.get("from"), state)
                    if resolution.kind == "nonScalar":
                        return ComputeErr(EvalError("TYPE_ERROR", resolution.detail))
                    if resolution.kind == "resolved":
                        env[name] = resolution.cell

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
