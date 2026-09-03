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
* **LIST params (Phase 610 / fuaran-core#91).** A ``params`` entry whose ``from`` binding
  resolves to an ARRAY of scalars is a **list param** — a multi-select chip's selection —
  and the pipeline reads it through the membership test's ``param`` form
  (``InParam``, wire ``{"$type":"in","expr":…,"param":…}``). Three rules, and each is
  observable:

  1. It resolves by **SUBSTITUTION, not through the evaluation env**:
     :func:`substitute_list_params` rewrites every bound ``InParam`` to the literal
     ``InList`` form *before* the prune and *before* evaluation, which is why the
     evaluator's ``InParam`` arm is a strict ``UNBOUND_PARAM`` error rather than a
     lookup. A pipeline reaching the evaluator with an ``InParam`` still in it names an
     unbound param and fails loudly rather than passing silently.
  2. An **EMPTY selection is UNBOUND**, never ``InList(x, [])``: the dependent ``filter``
     step prunes under the same lenient "unset ⇒ no constraint" rule an unset scalar chip
     already gets, so deselecting everything shows the **unfiltered** table rather than an
     empty one. "Nothing selected" is the absence of a constraint, not a constraint no row
     satisfies.
  3. A **kind mismatch reaches the strict refusal**: a list bound to a name the pipeline
     reads as a scalar ``Param``, or a scalar bound to one it reads as an ``InParam``,
     substitutes nothing — and because the name IS bound (just to the other kind) the
     prune does not fire either, so the surviving hole reaches the evaluator's
     ``UNBOUND_PARAM``. Never a silent wrong scoping.

  One ``_expr_param_names``-driven prune covers both param kinds with no second rule: a
  substituted step names no param at all, while an unsubstituted one still names its own —
  and the reactivity edge is derived from the same walk.
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
    InList,
    InParam,
    IsNull,
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
class ParamResolvedList:
    """The param's source resolved to a NON-EMPTY list of scalar cells — a LIST param
    (Phase 610), the multi-select chip's selection. Resolved by substitution into the
    pipeline's ``InParam`` occurrences, never placed in the scalar evaluation env.

    An EMPTY list never lands here: it is :class:`ParamUnbound`, so its filter prunes
    and the unfiltered table shows."""

    cells: list[Cell]
    kind: Literal["resolvedList"] = "resolvedList"


@dataclass(frozen=True)
class ParamNonScalar:
    """The param's source resolved to a STRUCTURED value, which has no scalar form.

    The loud twin of the reference hosts' ``value_to_cell`` / ``objToCell``
    returning nothing — e.g. a row ``Obj`` written by a grid row-click into a
    ``field``-less ``Selection`` slot. ``detail`` carries the cross-host message
    verbatim."""

    detail: str
    kind: Literal["nonScalar"] = "nonScalar"


type ParamResolution = ParamResolved | ParamResolvedList | ParamUnbound | ParamNonScalar


def _non_scalar(name: str) -> ParamNonScalar:
    return ParamNonScalar(f"Transform param '{name}' resolved to a non-scalar value")


def _scalar_cell(value: object) -> Cell | None:
    """The scalar cell for a resolved value, or ``None`` when it has no scalar form.
    JSON null / an absent value IS a scalar (the NULL cell, per the reference
    ``JVal::Null → Cell::Null``); a structured ``Obj`` / ``Arr`` / host record is not."""
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
    return None


def _list_cells(value: object) -> list[Cell] | None:
    """Coerce a resolved LIST to cells for a Transform LIST param (Phase 610).

    Accepts BOTH representations the store legitimately holds — a raw Python ``list``
    (a host that hands the renderer parsed JSON) and a structural ``Arr`` (a value read
    off the tree, e.g. a binding's carried ``defaultValue``) — for the same reason
    :func:`_lift_store_value` does. Element coercion is :func:`_scalar_cell`'s, so a list
    element and a scalar param cannot disagree about what a value means; a non-list, or a
    list holding a non-scalar item, is ``None`` and stays the loud non-scalar error."""
    if isinstance(value, Arr):
        items: list[object] = list(value.items)
    elif isinstance(value, list):
        items = list(value)
    else:
        return None
    cells: list[Cell] = []
    for item in items:
        cell = _scalar_cell(item)
        if cell is None:
            return None
        cells.append(cell)
    return cells


def _scalar_to_cell(name: str, value: object) -> ParamResolution:
    """A resolved param source as a param resolution: a scalar cell, a LIST of scalar
    cells (Phase 610), *unbound*, or the loud non-scalar failure.

    The scalar reading is tried first, exactly as the reference hosts do, so nothing
    about an existing scalar param changes. An EMPTY list is :class:`ParamUnbound`, not
    an empty membership set — deselecting everything is the absence of a constraint."""
    cell = _scalar_cell(value)
    if cell is not None:
        return ParamResolved(cell)
    cells = _list_cells(value)
    if cells is None:
        return _non_scalar(name)
    if not cells:
        return ParamUnbound()
    return ParamResolvedList(cells)


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
    if isinstance(expr, InList):
        return (
            _expr_param_names(expr.expr).union(*(_expr_param_names(x) for x in expr.items))
            if expr.items
            else _expr_param_names(expr.expr)
        )
    if isinstance(expr, InParam):
        # A LIST param shares the scalar params' namespace: the prune and the reactivity
        # edge are both derived from this one walk.
        return _expr_param_names(expr.expr) | {expr.param}
    if isinstance(expr, IsNull):
        return _expr_param_names(expr.expr)
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
    if isinstance(expr, InList):
        return InList(_substitute_expr(expr.expr, env), [_substitute_expr(x, env) for x in expr.items])
    if isinstance(expr, InParam):
        # A scalar env never binds a LIST param — that is `_substitute_list_params_expr`'s
        # job, and conflating them is what would silently scope a grid by the wrong kind.
        return InParam(_substitute_expr(expr.expr, env), expr.param)
    if isinstance(expr, IsNull):
        return IsNull(_substitute_expr(expr.expr, env))
    return expr  # Col, Lit


def _substitute_list_params_expr(expr: ColExpr, list_env: dict[str, list[Cell]]) -> ColExpr:
    """Rewrite every ``InParam`` bound in ``list_env`` to the literal ``InList`` form —
    the Python mirror of the reference ``Transform.substituteListParams`` (Phase 610).

    An UNBOUND ``InParam`` is left intact on purpose: the caller's prune then sees it
    still naming its own param, which is why one prune covers both param kinds."""
    if isinstance(expr, InParam):
        inner = _substitute_list_params_expr(expr.expr, list_env)
        cells = list_env.get(expr.param)
        if cells is None:
            return InParam(inner, expr.param)
        return InList(inner, [Lit(c) for c in cells])
    if isinstance(expr, Binary):
        return Binary(
            expr.op,
            _substitute_list_params_expr(expr.left, list_env),
            _substitute_list_params_expr(expr.right, list_env),
        )
    if isinstance(expr, Not):
        return Not(_substitute_list_params_expr(expr.expr, list_env))
    if isinstance(expr, Coalesce):
        return Coalesce([_substitute_list_params_expr(x, list_env) for x in expr.exprs])
    if isinstance(expr, Case):
        return Case(
            [
                (_substitute_list_params_expr(w, list_env), _substitute_list_params_expr(t, list_env))
                for w, t in expr.cases
            ],
            _substitute_list_params_expr(expr.else_expr, list_env),
        )
    if isinstance(expr, Cast):
        return Cast(expr.type, _substitute_list_params_expr(expr.expr, list_env))
    if isinstance(expr, ApplyFn):
        return ApplyFn(expr.fn, [_substitute_list_params_expr(x, list_env) for x in expr.args])
    if isinstance(expr, InList):
        return InList(
            _substitute_list_params_expr(expr.expr, list_env),
            [_substitute_list_params_expr(x, list_env) for x in expr.items],
        )
    if isinstance(expr, IsNull):
        return IsNull(_substitute_list_params_expr(expr.expr, list_env))
    return expr  # Col, Lit, Param


def substitute_list_params(pipeline: list[Transform], list_env: dict[str, list[Cell]]) -> list[Transform]:
    """Substitute every bound LIST param through a whole pipeline (Phase 610).

    Only ``filter`` / ``derive`` carry a ``ColExpr``; every other step is returned
    unchanged. Public because the substitution IS the resolution rule — a host driving
    a pipeline itself owes the same rewrite before it evaluates."""
    out: list[Transform] = []
    for step in pipeline:
        if isinstance(step, Filter):
            out.append(Filter(_substitute_list_params_expr(step.pred, list_env)))
        elif isinstance(step, Derive):
            out.append(Derive(step.name, _substitute_list_params_expr(step.expr, list_env)))
        else:
            out.append(step)
    return out


def _prune_and_substitute(
    pipeline: list[Transform],
    env: dict[str, Cell],
    list_env: dict[str, list[Cell]] | None = None,
) -> list[Transform]:
    """Substitute bound LIST params, drop filters referencing an unbound param, then
    substitute bound scalar params.

    The ORDER is the rule (Phase 610): a substituted ``InParam`` becomes an ``InList``
    and so names no param at all, while an unbound one survives naming its own and is
    caught by the prune — one rule, both param kinds. A name bound to the *other* kind
    counts as bound here, so its surviving hole is NOT pruned and reaches the evaluator's
    strict ``UNBOUND_PARAM`` rather than being silently dropped."""
    list_env = list_env or {}
    if list_env:
        pipeline = substitute_list_params(pipeline, list_env)
    bound = set(env) | set(list_env)
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


def _lift_store_value(raw: object) -> object:
    """Lift a store value to the raw JSON shape the columnar codec reads.

    The store legitimately holds values in EITHER representation, and this
    function is the one place that has to know it. A host that hands the
    renderer parsed JSON puts ``list``/``dict`` in the store; the tree's own
    values — a ``defaultValue`` returned by ``resolve_binding``, and since
    §24.4 a SEED laid under the host's sources — are structural ``Arr`` / ``Obj``
    model values. Before seeding, a structural value could only reach here if a
    host deliberately put one there; seeding makes it the PRIMARY path for the
    charter's own pair, so it stops being a corner.

    ``encode_value`` is the same conversion the ``defaultValue`` fallback below
    already performs, applied one step earlier so both entry points agree.
    """
    if isinstance(raw, (Arr, Obj)):
        return json.loads(encode_value(raw))
    return raw


def _live_source(source_value: Obj, state: ComputeState) -> object:
    """Materialise a live source's current data as raw columnar JSON: the
    host-seeded store value when present, else the carried defaultValue, else
    the empty table."""
    key = source_value.fields.get(_LIVE_SOURCE_KEYS[source_value.tag or ""])
    raw: object | None = _lift_store_value(state.get(key)) if isinstance(key, str) else None
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
    list_env: dict[str, list[Cell]] = {}
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
                    elif resolution.kind == "resolvedList":
                        # Phase 610 — a LIST param resolves by SUBSTITUTION, so it never
                        # enters the scalar env.
                        list_env[name] = resolution.cells

    effective = _prune_and_substitute(pipe.value, env, list_env)
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
