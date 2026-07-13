"""Byte-exact canonical codec for the Compute-layer wire surface.

Encode lowers the columnar / algebra trees into the generic structural model
(:class:`~fuaran_py.model.Obj` / :class:`~fuaran_py.model.Arr` / scalars) and hands
them to the **proven** canonical encoder (:func:`fuaran_py.canonical.encode_value`),
so the Ordinal key-sort, the cross-host float layout, and the escape rules are shared
with the node codec — the Compute wire is byte-identical to the F# canonical output
*by construction*, with no second encoder to drift.

Decode parses with the standard library and walks the parsed JSON against the schema
(so a float column's integer-looking token is read as a float, exactly as the
reference does), surfacing the six-code :class:`~fuaran_py.dataframe.model.ColumnError`
envelope on any wire-shape violation.
"""

from __future__ import annotations

import json
from typing import Any

from ..canonical import encode_value
from ..model import Arr, Obj, Value
from .model import (
    AGG_FNS,
    BIN_OPS,
    COLUMN_TYPES,
    JOIN_KINDS,
    MALFORMED_SHAPE,
    MISSING_FIELD,
    NOT_JSON,
    NULL,
    SCALAR_FNS,
    SORT_DIRS,
    TYPE_MISMATCH,
    UNKNOWN_TYPE,
    WINDOW_FNS,
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
    ColumnError,
    DataSource,
    Derive,
    Distinct,
    Embedded,
    Err,
    Filter,
    GroupBy,
    Join,
    Limit,
    Lit,
    Not,
    Ok,
    Param,
    Pivot,
    PivotSpec,
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
    default_for,
    is_null,
)

# ════════════════════════════════════════════════════════════════════════════
#  Encode — lower to the structural model, then the canonical encoder
# ════════════════════════════════════════════════════════════════════════════


def _typed(tag: str, fields: dict[str, Value]) -> Obj:
    """A ``$type``-discriminated object (the DU-position convention)."""
    return Obj(tag, fields)


# ── cells / columns / sources ────────────────────────────────────────────────


def _cell_value(ty: str, c: Cell) -> Value:
    """The present-value JSON payload for a cell in a column of type ``ty`` (a null
    cell becomes the type-default placeholder; an ``int`` in a ``float`` column widens)."""
    present = default_for(ty) if is_null(c) else c
    if present.kind == "int" and ty == "float":
        return float(present.value)  # type: ignore[arg-type]
    return present.value


def _column_obj(col: Column) -> Obj:
    values: list[Value] = [_cell_value(col.type, c) for c in col.cells]
    validity: list[Value] = [not is_null(c) for c in col.cells]
    return Obj(None, {"values": Arr(values), "validity": Arr(validity)})


def _schema_arr(schema: Schema) -> Arr:
    return Arr([Obj(None, {"name": name, "type": ty}) for name, ty in schema])


def encode_source_value(src: DataSource) -> Value:
    """Lower a :class:`DataSource` to the structural model."""
    if isinstance(src, Embedded):
        t = src.table
        columns: dict[str, Value] = {}
        for name, _ in t.schema:
            col = next((c for c in t.columns if c.name == name), Column(name, "string", []))
            columns[name] = _column_obj(col)
        return Obj(None, {"schema": _schema_arr(t.schema), "columns": Obj(None, columns)})
    return Obj(None, {"schema": Arr([]), "ref": src.name})


def encode_source(src: DataSource) -> str:
    """Canonical wire string for a :class:`DataSource`."""
    return encode_value(encode_source_value(src))


# ── literals ──────────────────────────────────────────────────────────────────


def _cell_literal(c: Cell) -> Obj:
    if c.kind == "null":
        return _typed("Null", {})
    tag = {
        "int": "Int",
        "float": "Float",
        "bool": "Bool",
        "string": "Str",
        "date": "Date",
        "timestamp": "Timestamp",
    }[c.kind]
    return _typed(tag, {"value": c.value})


# ── ColExpr ───────────────────────────────────────────────────────────────────


def encode_expr_value(e: ColExpr) -> Value:
    if isinstance(e, Col):
        return _typed("col", {"name": e.name})
    if isinstance(e, Lit):
        return _typed("lit", {"cell": _cell_literal(e.cell)})
    if isinstance(e, Binary):
        return _typed("binary", {"op": e.op, "left": encode_expr_value(e.left), "right": encode_expr_value(e.right)})
    if isinstance(e, Not):
        return _typed("not", {"expr": encode_expr_value(e.expr)})
    if isinstance(e, Coalesce):
        return _typed("coalesce", {"exprs": Arr([encode_expr_value(x) for x in e.exprs])})
    if isinstance(e, Case):
        return _typed(
            "case",
            {
                "cases": Arr(
                    [Obj(None, {"when": encode_expr_value(w), "then": encode_expr_value(t)}) for w, t in e.cases]
                ),
                "else": encode_expr_value(e.else_expr),
            },
        )
    if isinstance(e, Cast):
        return _typed("cast", {"type": e.type, "expr": encode_expr_value(e.expr)})
    if isinstance(e, ApplyFn):
        return _typed("apply", {"fn": e.fn, "args": Arr([encode_expr_value(x) for x in e.args])})
    if isinstance(e, Param):
        return _typed("param", {"name": e.name})
    raise TypeError(f"cannot encode ColExpr {type(e)!r}")


# ── Transform ─────────────────────────────────────────────────────────────────


def _pair_obj(p: tuple[str, str]) -> Obj:
    return Obj(None, {"a": p[0], "b": p[1]})


def _order_obj(o: tuple[str, str]) -> Obj:
    return Obj(None, {"col": o[0], "dir": o[1]})


def _agg_obj(a: Agg) -> Obj:
    return Obj(None, {"name": a.name, "fn": a.fn, "of": a.of})


def _str_arr(xs: list[str]) -> Arr:
    return Arr(list(xs))


def encode_transform_value(t: Transform) -> Value:
    if isinstance(t, Filter):
        return _typed("filter", {"pred": encode_expr_value(t.pred)})
    if isinstance(t, Project):
        return _typed("project", {"cols": Arr([_pair_obj(p) for p in t.cols])})
    if isinstance(t, Derive):
        return _typed("derive", {"name": t.name, "expr": encode_expr_value(t.expr)})
    if isinstance(t, GroupBy):
        return _typed("groupBy", {"keys": _str_arr(t.keys), "aggs": Arr([_agg_obj(a) for a in t.aggs])})
    if isinstance(t, Join):
        return _typed(
            "join",
            {"source": encode_source_value(t.source), "on": Arr([_pair_obj(p) for p in t.on]), "how": t.how},
        )
    if isinstance(t, Window):
        s = t.spec
        return _typed(
            "window",
            {
                "partitionBy": _str_arr(s.partition_by),
                "orderBy": Arr([_order_obj(o) for o in s.order_by]),
                "fn": s.fn,
                "of": s.of,
                "as": s.as_,
            },
        )
    if isinstance(t, Pivot):
        s2 = t.spec
        return _typed("pivot", {"index": _str_arr(s2.index), "on": s2.on, "values": s2.values, "agg": s2.agg})
    if isinstance(t, Unpivot):
        return _typed("unpivot", {"idVars": _str_arr(t.id_vars), "valueVars": _str_arr(t.value_vars)})
    if isinstance(t, Sort):
        return _typed("sort", {"by": Arr([_order_obj(o) for o in t.by])})
    if isinstance(t, Distinct):
        return _typed("distinct", {})
    if isinstance(t, Limit):
        return _typed("limit", {"n": t.n, "offset": t.offset})
    if isinstance(t, Union):
        return _typed("union", {"source": encode_source_value(t.source)})
    raise TypeError(f"cannot encode Transform {type(t)!r}")


def encode_pipeline(pipeline: list[Transform]) -> str:
    """Canonical wire string for an ordered pipeline."""
    return encode_value(Arr([encode_transform_value(t) for t in pipeline]))


# ════════════════════════════════════════════════════════════════════════════
#  Decode — parse, then walk against the schema (six-code ColumnError envelope)
# ════════════════════════════════════════════════════════════════════════════


def _err(code: str, detail: str) -> Err[ColumnError]:
    return Err(ColumnError(code, detail))


def _kind_name(v: object) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "null"


def _field(obj: object, key: str) -> Result[Any, ColumnError]:
    if not isinstance(obj, dict):
        return _err(MALFORMED_SHAPE, f"expected object, got {_kind_name(obj)}")
    if key not in obj:
        return _err(MISSING_FIELD, key)
    return Ok(obj[key])


def _kind_of(obj: object) -> Result[str, ColumnError]:
    r = _field(obj, "$type")
    if not r.ok:
        return r  # type: ignore[return-value]
    v = r.value
    if not isinstance(v, str):
        return _err(MALFORMED_SHAPE, "$type must be a string")
    return Ok(v)


# ── cells / sources ──────────────────────────────────────────────────────────


def _decode_cell_value(col: str, ty: str, v: object) -> Result[Cell, ColumnError]:
    """Decode a present value into a cell of the declared column type. A float column
    accepts an integer token (lossless widening); every other type requires its kind."""
    if ty == "int" and isinstance(v, int) and not isinstance(v, bool):
        return Ok(cell_int(v))
    if ty == "float" and isinstance(v, bool) is False and isinstance(v, (int, float)):
        return Ok(cell_float(float(v)))
    if ty == "bool" and isinstance(v, bool):
        return Ok(cell_bool(v))
    if ty == "string" and isinstance(v, str):
        return Ok(cell_str(v))
    if ty == "date" and isinstance(v, str):
        return Ok(cell_date(v))
    if ty == "timestamp" and isinstance(v, str):
        return Ok(cell_timestamp(v))
    return _err(TYPE_MISMATCH, f"column '{col}': expected {ty} value, got {_kind_name(v)}")


def _decode_schema(el: object) -> Result[Schema, ColumnError]:
    if not isinstance(el, list):
        return _err(MALFORMED_SHAPE, f"schema: expected array, got {_kind_name(el)}")
    out: Schema = []
    for entry in el:
        rn = _field(entry, "name")
        if not rn.ok:
            return rn  # type: ignore[return-value]
        rt = _field(entry, "type")
        if not rt.ok:
            return rt  # type: ignore[return-value]
        name, ty = rn.value, rt.value
        if not isinstance(name, str):
            return _err(MALFORMED_SHAPE, "schema.name: expected string")
        if not isinstance(ty, str) or ty not in COLUMN_TYPES:
            return _err(UNKNOWN_TYPE, f"unknown column type '{ty}'; expected one of: {', '.join(COLUMN_TYPES)}")
        out.append((name, ty))
    return Ok(out)


def _decode_column(columns_obj: object, name: str, ty: str) -> Result[Column, ColumnError]:
    if not isinstance(columns_obj, dict) or name not in columns_obj:
        return _err(MISSING_FIELD, f"columns.{name}")
    col_el = columns_obj[name]
    rv = _field(col_el, "values")
    if not rv.ok:
        return rv  # type: ignore[return-value]
    rva = _field(col_el, "validity")
    if not rva.ok:
        return rva  # type: ignore[return-value]
    values, validity = rv.value, rva.value
    if not isinstance(values, list):
        return _err(MALFORMED_SHAPE, f"{name}.values: expected array")
    if not isinstance(validity, list):
        return _err(MALFORMED_SHAPE, f"{name}.validity: expected array")
    if len(values) != len(validity):
        return _err(
            "LENGTH_MISMATCH", f"column '{name}': values/validity length mismatch ({len(values)} vs {len(validity)})"
        )
    cells: list[Cell] = []
    for raw, present in zip(values, validity, strict=True):
        if not isinstance(present, bool):
            return _err(MALFORMED_SHAPE, f"{name}.validity: expected bool, got {_kind_name(present)}")
        if not present:
            cells.append(_null())  # validity false ⇒ the cell is null (the placeholder is ignored)
        else:
            rc = _decode_cell_value(name, ty, raw)
            if not rc.ok:
                return rc  # type: ignore[return-value]
            cells.append(rc.value)
    return Ok(Column(name, ty, cells))


def _null() -> Cell:
    return NULL


def decode_source_json(el: object) -> Result[DataSource, ColumnError]:
    rs = _field(el, "schema")
    if not rs.ok:
        return rs  # type: ignore[return-value]
    rschema = _decode_schema(rs.value)
    if not rschema.ok:
        return rschema  # type: ignore[return-value]
    schema = rschema.value
    if isinstance(el, dict) and "ref" in el:
        ref = el["ref"]
        if not isinstance(ref, str):
            return _err(MALFORMED_SHAPE, "ref: expected string")
        return Ok(Ref(ref))
    rc = _field(el, "columns")
    if not rc.ok:
        return rc  # type: ignore[return-value]
    columns_obj = rc.value
    cols: list[Column] = []
    for name, ty in schema:
        rcol = _decode_column(columns_obj, name, ty)
        if not rcol.ok:
            return rcol  # type: ignore[return-value]
        cols.append(rcol.value)
    return Ok(Embedded(Table(schema, cols)))


def decode_source(text: str) -> Result[DataSource, ColumnError]:
    try:
        parsed = json.loads(text)
    except ValueError as ex:
        return _err(NOT_JSON, str(ex))
    return decode_source_json(parsed)


# ── ColExpr ───────────────────────────────────────────────────────────────────


def _decode_cell_literal(el: object) -> Result[Cell, ColumnError]:
    rk = _kind_of(el)
    if not rk.ok:
        return rk  # type: ignore[return-value]
    tag = rk.value
    assert isinstance(el, dict)
    if tag == "Null":
        return Ok(_null())
    v = el.get("value")
    if tag == "Int" and isinstance(v, int) and not isinstance(v, bool):
        return Ok(cell_int(v))
    if tag == "Float" and not isinstance(v, bool) and isinstance(v, (int, float)):
        return Ok(cell_float(float(v)))
    if tag == "Bool" and isinstance(v, bool):
        return Ok(cell_bool(v))
    if tag == "Str" and isinstance(v, str):
        return Ok(cell_str(v))
    if tag == "Date" and isinstance(v, str):
        return Ok(cell_date(v))
    if tag == "Timestamp" and isinstance(v, str):
        return Ok(cell_timestamp(v))
    return _err(TYPE_MISMATCH, f"lit: bad value for {tag}")


def decode_expr(el: object) -> Result[ColExpr, ColumnError]:
    rk = _kind_of(el)
    if not rk.ok:
        return rk  # type: ignore[return-value]
    k = rk.value
    assert isinstance(el, dict)
    if k == "col":
        r = _field(el, "name")
        if not r.ok:
            return r  # type: ignore[return-value]
        if not isinstance(r.value, str):
            return _err(MALFORMED_SHAPE, "col.name: expected string")
        return Ok(Col(r.value))
    if k == "lit":
        r = _field(el, "cell")
        if not r.ok:
            return r  # type: ignore[return-value]
        rc = _decode_cell_literal(r.value)
        return rc if not rc.ok else Ok(Lit(rc.value))  # type: ignore[return-value]
    if k == "binary":
        rop = _field(el, "op")
        if not rop.ok:
            return rop  # type: ignore[return-value]
        if rop.value not in BIN_OPS:
            return _err(UNKNOWN_TYPE, f"unknown binary op '{rop.value}'")
        ra = _field(el, "left")
        if not ra.ok:
            return ra  # type: ignore[return-value]
        la = decode_expr(ra.value)
        if not la.ok:
            return la  # type: ignore[return-value]
        rb = _field(el, "right")
        if not rb.ok:
            return rb  # type: ignore[return-value]
        lb = decode_expr(rb.value)
        if not lb.ok:
            return lb  # type: ignore[return-value]
        return Ok(Binary(rop.value, la.value, lb.value))
    if k == "not":
        r = _field(el, "expr")
        if not r.ok:
            return r  # type: ignore[return-value]
        inner = decode_expr(r.value)
        return inner if not inner.ok else Ok(Not(inner.value))  # type: ignore[return-value]
    if k == "coalesce":
        r = _field(el, "exprs")
        if not r.ok:
            return r  # type: ignore[return-value]
        xs = _decode_expr_list(r.value)
        return xs if not xs.ok else Ok(Coalesce(xs.value))  # type: ignore[return-value]
    if k == "case":
        rcases = _field(el, "cases")
        if not rcases.ok:
            return rcases  # type: ignore[return-value]
        if not isinstance(rcases.value, list):
            return _err(MALFORMED_SHAPE, "case.cases: expected array")
        pairs: list[tuple[ColExpr, ColExpr]] = []
        for c in rcases.value:
            rw = _field(c, "when")
            if not rw.ok:
                return rw  # type: ignore[return-value]
            lw = decode_expr(rw.value)
            if not lw.ok:
                return lw  # type: ignore[return-value]
            rt = _field(c, "then")
            if not rt.ok:
                return rt  # type: ignore[return-value]
            lt = decode_expr(rt.value)
            if not lt.ok:
                return lt  # type: ignore[return-value]
            pairs.append((lw.value, lt.value))
        re = _field(el, "else")
        if not re.ok:
            return re  # type: ignore[return-value]
        le = decode_expr(re.value)
        return le if not le.ok else Ok(Case(pairs, le.value))  # type: ignore[return-value]
    if k == "cast":
        rt = _field(el, "type")
        if not rt.ok:
            return rt  # type: ignore[return-value]
        if rt.value not in COLUMN_TYPES:
            return _err(UNKNOWN_TYPE, f"unknown cast type '{rt.value}'")
        re = _field(el, "expr")
        if not re.ok:
            return re  # type: ignore[return-value]
        le = decode_expr(re.value)
        return le if not le.ok else Ok(Cast(rt.value, le.value))  # type: ignore[return-value]
    if k == "apply":
        rfn = _field(el, "fn")
        if not rfn.ok:
            return rfn  # type: ignore[return-value]
        if rfn.value not in SCALAR_FNS:
            return _err(UNKNOWN_TYPE, f"unknown scalar fn '{rfn.value}'")
        ra = _field(el, "args")
        if not ra.ok:
            return ra  # type: ignore[return-value]
        xs = _decode_expr_list(ra.value)
        return xs if not xs.ok else Ok(ApplyFn(rfn.value, xs.value))  # type: ignore[return-value]
    if k == "param":
        r = _field(el, "name")
        if not r.ok:
            return r  # type: ignore[return-value]
        if not isinstance(r.value, str):
            return _err(MALFORMED_SHAPE, "param.name: expected string")
        return Ok(Param(r.value))
    return _err(UNKNOWN_TYPE, f"unknown ColExpr '{k}'")


def _decode_expr_list(el: object) -> Result[list[ColExpr], ColumnError]:
    if not isinstance(el, list):
        return _err(MALFORMED_SHAPE, "expected array of expressions")
    out: list[ColExpr] = []
    for x in el:
        r = decode_expr(x)
        if not r.ok:
            return r  # type: ignore[return-value]
        out.append(r.value)
    return Ok(out)


# ── Transform ─────────────────────────────────────────────────────────────────


def _str_list(el: object, ctx: str) -> Result[list[str], ColumnError]:
    if not isinstance(el, list) or not all(isinstance(x, str) for x in el):
        return _err(MALFORMED_SHAPE, f"{ctx}: expected array of strings")
    return Ok(list(el))


def _pair_of(el: object) -> Result[tuple[str, str], ColumnError]:
    ra = _field(el, "a")
    if not ra.ok:
        return ra  # type: ignore[return-value]
    rb = _field(el, "b")
    if not rb.ok:
        return rb  # type: ignore[return-value]
    if not isinstance(ra.value, str) or not isinstance(rb.value, str):
        return _err(MALFORMED_SHAPE, "pair: expected strings")
    return Ok((ra.value, rb.value))


def _order_of(el: object) -> Result[tuple[str, str], ColumnError]:
    rc = _field(el, "col")
    if not rc.ok:
        return rc  # type: ignore[return-value]
    rd = _field(el, "dir")
    if not rd.ok:
        return rd  # type: ignore[return-value]
    direction = rd.value if rd.value in SORT_DIRS else "asc"
    if not isinstance(rc.value, str):
        return _err(MALFORMED_SHAPE, "order.col: expected string")
    return Ok((rc.value, direction))


def _list_of(el: object, ctx: str, f: Any) -> Result[list[Any], ColumnError]:
    if not isinstance(el, list):
        return _err(MALFORMED_SHAPE, f"{ctx}: expected array")
    out: list[Any] = []
    for x in el:
        r = f(x)
        if not r.ok:
            return r
        out.append(r.value)
    return Ok(out)


def _agg_of(el: object) -> Result[Agg, ColumnError]:
    rn = _field(el, "name")
    if not rn.ok:
        return rn  # type: ignore[return-value]
    rf = _field(el, "fn")
    if not rf.ok:
        return rf  # type: ignore[return-value]
    ro = _field(el, "of")
    if not ro.ok:
        return ro  # type: ignore[return-value]
    if rf.value not in AGG_FNS:
        return _err(UNKNOWN_TYPE, f"unknown agg fn '{rf.value}'")
    if not isinstance(rn.value, str) or not isinstance(ro.value, str):
        return _err(MALFORMED_SHAPE, "agg: expected strings")
    return Ok(Agg(rn.value, rf.value, ro.value))


def decode_transform(el: object) -> Result[Transform, ColumnError]:  # noqa: C901, PLR0911, PLR0912
    rk = _kind_of(el)
    if not rk.ok:
        return rk  # type: ignore[return-value]
    k = rk.value
    assert isinstance(el, dict)
    if k == "filter":
        r = _field(el, "pred")
        if not r.ok:
            return r  # type: ignore[return-value]
        e = decode_expr(r.value)
        return e if not e.ok else Ok(Filter(e.value))  # type: ignore[return-value]
    if k == "project":
        r = _field(el, "cols")
        if not r.ok:
            return r  # type: ignore[return-value]
        ps = _list_of(r.value, "project.cols", _pair_of)
        return ps if not ps.ok else Ok(Project(ps.value))  # type: ignore[return-value]
    if k == "derive":
        rn = _field(el, "name")
        if not rn.ok:
            return rn  # type: ignore[return-value]
        re = _field(el, "expr")
        if not re.ok:
            return re  # type: ignore[return-value]
        e = decode_expr(re.value)
        if not e.ok:
            return e  # type: ignore[return-value]
        if not isinstance(rn.value, str):
            return _err(MALFORMED_SHAPE, "derive.name: expected string")
        return Ok(Derive(rn.value, e.value))
    if k == "groupBy":
        rkeys = _field(el, "keys")
        if not rkeys.ok:
            return rkeys  # type: ignore[return-value]
        keys = _str_list(rkeys.value, "groupBy.keys")
        if not keys.ok:
            return keys  # type: ignore[return-value]
        raggs = _field(el, "aggs")
        if not raggs.ok:
            return raggs  # type: ignore[return-value]
        aggs = _list_of(raggs.value, "groupBy.aggs", _agg_of)
        return aggs if not aggs.ok else Ok(GroupBy(keys.value, aggs.value))  # type: ignore[return-value]
    if k == "join":
        rsrc = _field(el, "source")
        if not rsrc.ok:
            return rsrc  # type: ignore[return-value]
        src = decode_source_json(rsrc.value)
        if not src.ok:
            return src  # type: ignore[return-value]
        ron = _field(el, "on")
        if not ron.ok:
            return ron  # type: ignore[return-value]
        on = _list_of(ron.value, "join.on", _pair_of)
        if not on.ok:
            return on  # type: ignore[return-value]
        rhow = _field(el, "how")
        if not rhow.ok:
            return rhow  # type: ignore[return-value]
        if rhow.value not in JOIN_KINDS:
            return _err(UNKNOWN_TYPE, f"unknown join kind '{rhow.value}'")
        return Ok(Join(src.value, on.value, rhow.value))
    if k == "window":
        rpb = _field(el, "partitionBy")
        if not rpb.ok:
            return rpb  # type: ignore[return-value]
        pb = _str_list(rpb.value, "window.partitionBy")
        if not pb.ok:
            return pb  # type: ignore[return-value]
        rob = _field(el, "orderBy")
        if not rob.ok:
            return rob  # type: ignore[return-value]
        ob = _list_of(rob.value, "window.orderBy", _order_of)
        if not ob.ok:
            return ob  # type: ignore[return-value]
        rfn = _field(el, "fn")
        if not rfn.ok:
            return rfn  # type: ignore[return-value]
        if rfn.value not in WINDOW_FNS:
            return _err(UNKNOWN_TYPE, f"unknown window fn '{rfn.value}'")
        rof = _field(el, "of")
        if not rof.ok:
            return rof  # type: ignore[return-value]
        ras = _field(el, "as")
        if not ras.ok:
            return ras  # type: ignore[return-value]
        return Ok(Window(WindowSpec(pb.value, ob.value, rfn.value, rof.value, ras.value)))
    if k == "pivot":
        rindex = _field(el, "index")
        if not rindex.ok:
            return rindex  # type: ignore[return-value]
        index = _str_list(rindex.value, "pivot.index")
        if not index.ok:
            return index  # type: ignore[return-value]
        ron = _field(el, "on")
        if not ron.ok:
            return ron  # type: ignore[return-value]
        rvals = _field(el, "values")
        if not rvals.ok:
            return rvals  # type: ignore[return-value]
        ragg = _field(el, "agg")
        if not ragg.ok:
            return ragg  # type: ignore[return-value]
        if ragg.value not in AGG_FNS:
            return _err(UNKNOWN_TYPE, f"unknown agg fn '{ragg.value}'")
        return Ok(Pivot(PivotSpec(index.value, ron.value, rvals.value, ragg.value)))
    if k == "unpivot":
        rid = _field(el, "idVars")
        if not rid.ok:
            return rid  # type: ignore[return-value]
        idv = _str_list(rid.value, "unpivot.idVars")
        if not idv.ok:
            return idv  # type: ignore[return-value]
        rvv = _field(el, "valueVars")
        if not rvv.ok:
            return rvv  # type: ignore[return-value]
        vv = _str_list(rvv.value, "unpivot.valueVars")
        return vv if not vv.ok else Ok(Unpivot(idv.value, vv.value))  # type: ignore[return-value]
    if k == "sort":
        r = _field(el, "by")
        if not r.ok:
            return r  # type: ignore[return-value]
        by = _list_of(r.value, "sort.by", _order_of)
        return by if not by.ok else Ok(Sort(by.value))  # type: ignore[return-value]
    if k == "distinct":
        return Ok(Distinct())
    if k == "limit":
        rn = _field(el, "n")
        if not rn.ok:
            return rn  # type: ignore[return-value]
        roff = _field(el, "offset")
        if not roff.ok:
            return roff  # type: ignore[return-value]
        if not isinstance(rn.value, int) or not isinstance(roff.value, int):
            return _err(MALFORMED_SHAPE, "limit: expected ints")
        return Ok(Limit(rn.value, roff.value))
    if k == "union":
        r = _field(el, "source")
        if not r.ok:
            return r  # type: ignore[return-value]
        src = decode_source_json(r.value)
        return src if not src.ok else Ok(Union(src.value))  # type: ignore[return-value]
    return _err(UNKNOWN_TYPE, f"unknown Transform '{k}'")


def decode_pipeline(text: str) -> Result[list[Transform], ColumnError]:
    try:
        parsed = json.loads(text)
    except ValueError as ex:
        return _err(NOT_JSON, str(ex))
    return decode_pipeline_json(parsed)


def decode_pipeline_json(el: object) -> Result[list[Transform], ColumnError]:
    if not isinstance(el, list):
        return _err(MALFORMED_SHAPE, "pipeline: expected a JSON array of transform steps")
    out: list[Transform] = []
    for step in el:
        r = decode_transform(step)
        if not r.ok:
            return r  # type: ignore[return-value]
        out.append(r.value)
    return Ok(out)
