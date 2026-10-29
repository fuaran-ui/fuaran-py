"""Byte-exact canonical codec for the Compute-layer wire surface.

Encode lowers the columnar / algebra trees into the generic structural model
(:class:`~fuaran_ui.model.Obj` / :class:`~fuaran_ui.model.Arr` / scalars) and hands
them to the **proven** canonical encoder (:func:`fuaran_ui.canonical.encode_value`),
so the Ordinal key-sort, the cross-host float layout, and the escape rules are shared
with the node codec — the Compute wire is byte-identical to the F# canonical output
*by construction*, with no second encoder to drift.

Decode parses with the standard library and walks the parsed JSON against the schema
(so a float column's integer-looking token is read as a float, exactly as the
reference does), surfacing the six-code :class:`~fuaran_ui.dataframe.model.ColumnError`
envelope on any wire-shape violation.
"""

from __future__ import annotations

import math
from typing import Any

from ...canonical import encode_value
from ...model import Arr, Obj, Value
from ...result import INVALID_JSON
from ...shapeguard import check_shape, load_bounded
from . import decimal_text
from .model import (
    AGG_FNS,
    BIN_OPS,
    COLUMN_TYPES,
    DECIMAL,
    JOIN_KINDS,
    LIMIT_EXCEEDED,
    MALFORMED_SHAPE,
    MISSING_FIELD,
    NO_METADATA,
    NOT_JSON,
    NULL,
    ROUNDING_MODES,
    SCALAR_FNS,
    TIMESTAMP_UNIT_DIGITS,
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
    Convert,
    DataSource,
    Derive,
    Distinct,
    Embedded,
    Err,
    Except,
    Field,
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
    PivotSpec,
    Project,
    Quotient,
    Ref,
    Result,
    Rounded,
    Rounding,
    Sort,
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
    default_for,
    is_null,
    table_fields,
    table_of_fields,
)
from .unit import UnitRefusal, parse_unit, refusal_text, render_unit

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
    if present.kind == "int" and ty == DECIMAL:
        return str(present.value)  # an int widens into a decimal column as its digits
    return present.value


def _column_obj(col: Column) -> Obj:
    values: list[Value] = [_cell_value(col.type, c) for c in col.cells]
    validity: list[Value] = [not is_null(c) for c in col.cells]
    return Obj(None, {"values": Arr(values), "validity": Arr(validity)})


def field_value(f: Field) -> Obj:
    """One schema entry as the wire carries it (WIRE_FORMAT §3.6.26): ``name`` and ``type``
    always, then the metadata members the field states and no other — ``unit`` (its canonical
    text), ``label``, ``description``, and ``ext`` when it holds a member — so a field without
    metadata writes exactly the ``{name, type}`` entry it always did."""
    members: dict[str, Value] = {"name": f.name, "type": f.type}
    m = f.meta
    if m.unit is not None:
        members["unit"] = render_unit(m.unit)
    if m.label is not None:
        members["label"] = m.label
    if m.description is not None:
        members["description"] = m.description
    if m.ext:
        members["ext"] = Obj(None, dict(m.ext))
    return Obj(None, members)


def encode_field(f: Field) -> str:
    """The canonical bytes of one schema entry."""
    return encode_value(field_value(f))


def encode_source_value(src: DataSource) -> Value:
    """Lower a :class:`DataSource` to the structural model."""
    if isinstance(src, Embedded):
        t = src.table
        columns: dict[str, Value] = {}
        for name, _ in t.schema:
            col = next((c for c in t.columns if c.name == name), Column(name, "string", []))
            columns[name] = _column_obj(col)
        schema = Arr([field_value(f) for f in table_fields(t)])
        return Obj(None, {"schema": schema, "columns": Obj(None, columns)})
    return Obj(None, {"schema": Arr([]), "ref": src.name})


def encode_source(src: DataSource) -> str:
    """Canonical wire string for a :class:`DataSource`. Assumes a table whose cells belong to their
    columns — :func:`try_encode_source` is the guarded entry point for derived or untrusted data."""
    return encode_value(encode_source_value(src))


def _cell_fault(name: str, ty: str, c: Cell) -> ColumnError | None:
    """Why a present cell cannot be written into column ``name`` of type ``ty``, if it cannot: a
    cell of another type (an ``int`` widens into ``float`` and ``decimal``), a non-finite float, or
    decimal text that is not canonical."""
    if is_null(c):
        return None
    if c.kind != ty and not (c.kind == "int" and ty in ("float", DECIMAL)):
        return ColumnError(TYPE_MISMATCH, f"column '{name}': a {c.kind} cell in a {ty} column")
    if c.kind == "float" and not math.isfinite(c.value):  # type: ignore[arg-type]
        return ColumnError(MALFORMED_SHAPE, f"column '{name}': a non-finite float has no wire form")
    if c.kind == DECIMAL and not decimal_text.is_canonical(c.value):
        return ColumnError(MALFORMED_SHAPE, f"column '{name}': decimal text '{c.value}' is not canonical")
    return None


def try_encode_source(src: DataSource) -> Result[str, ColumnError]:
    """Total, guarded encode: every present cell is checked against its column first, so over a
    table whose cells belong to their columns it is exactly ``Ok(encode_source(src))``, and a cell
    that does not — decimal text that is not canonical included — is refused by name."""
    if isinstance(src, Embedded):
        for col in src.table.columns:
            for c in col.cells:
                fault = _cell_fault(col.name, col.type, c)
                if fault is not None:
                    return Err(fault)
    return Ok(encode_source(src))


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
        # Phase 2001: the decimal TEXT, a JSON string — a number token would have been a float.
        "decimal": "Decimal",
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
    if isinstance(e, InList):
        return _typed("in", {"expr": encode_expr_value(e.expr), "items": Arr([encode_expr_value(x) for x in e.items])})
    if isinstance(e, InParam):
        return _typed("in", {"expr": encode_expr_value(e.expr), "param": e.param})
    if isinstance(e, IsNull):
        return _typed("isNull", {"expr": encode_expr_value(e.expr)})
    if isinstance(e, Quotient):
        return _typed(
            "quotient",
            {
                "dividend": encode_expr_value(e.dividend),
                "divisor": encode_expr_value(e.divisor),
                "rounding": _rounding_obj(e.rounding),
            },
        )
    if isinstance(e, Rounded):
        return _typed("rounded", {"expr": encode_expr_value(e.expr), "rounding": _rounding_obj(e.rounding)})
    if isinstance(e, Convert):
        return _typed("convert", {"expr": encode_expr_value(e.expr), "unit": render_unit(e.unit)})
    raise TypeError(f"cannot encode ColExpr {type(e)!r}")


def _rounding_obj(r: Rounding) -> Obj:
    # The scale is an integer slot: the literal, or `{"$param": name}` where a parameter binds it.
    scale: Value = Obj(None, {"$param": r.scale.name}) if isinstance(r.scale, Param) else r.scale
    return Obj(None, {"mode": r.mode, "scale": scale})


# ── Transform ─────────────────────────────────────────────────────────────────


def _pair_obj(p: tuple[str, str]) -> Obj:
    return Obj(None, {"a": p[0], "b": p[1]})


def _order_obj(o: tuple[str, str]) -> Obj:
    # 0.28.0 — the member is `column`, not `col`: a member whose only honest name is
    # "the column" is spelled out in full. `col` remains a decode alias, never emitted.
    return Obj(None, {"column": o[0], "dir": o[1]})


def _agg_obj(a: Agg) -> Obj:
    return Obj(None, {"name": a.name, "fn": a.fn, "of": a.of})


def _str_arr(xs: list[str]) -> Arr:
    return Arr(list(xs))


def encode_transform_value(t: Transform) -> Value:
    if isinstance(t, Filter):
        return _typed("filter", {"pred": encode_expr_value(t.pred)})
    if isinstance(t, Project):
        # 0.28.0 — `columns`, not `cols`, for the reason `_order_obj` above states.
        return _typed("project", {"columns": Arr([_pair_obj(p) for p in t.cols])})
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
    if isinstance(t, Intersect):
        return _typed("intersect", {"source": encode_source_value(t.source)})
    if isinstance(t, Except):
        return _typed("except", {"source": encode_source_value(t.source)})
    raise TypeError(f"cannot encode Transform {type(t)!r}")


def encode_pipeline(pipeline: list[Transform]) -> str:
    """Canonical wire string for an ordered pipeline."""
    return encode_value(Arr([encode_transform_value(t) for t in pipeline]))


# ════════════════════════════════════════════════════════════════════════════
#  Decode — parse, then walk against the schema (six-code ColumnError envelope)
# ════════════════════════════════════════════════════════════════════════════


def _guarded_parse(text: str) -> tuple[object, Err[ColumnError] | None]:
    """The one guarded parse for this module's two text readers (§20.1).

    A §20 row binds an entry point, so a dataframe payload must answer a
    repeated member or an unpaired surrogate exactly as ``decode_node`` does;
    routing it through ``shapeguard`` also makes it total on hostile input,
    where a bare ``json.loads`` let a deep document escape as a throw. The
    guard's ``INVALID_JSON`` maps onto this module's own ``NOT_JSON`` spelling;
    a limit breach keeps ``LIMIT_EXCEEDED``, because §21.2 rule 2 forbids
    relabelling it as a syntax error.
    """
    parsed, error = load_bounded(text)
    if error is None:
        error = check_shape(parsed)
    if error is not None:
        code = NOT_JSON if error.code == INVALID_JSON else LIMIT_EXCEEDED
        return None, _err(code, error.message)
    return parsed, None


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


def _try_field(obj: object, key: str) -> object | None:
    if isinstance(obj, dict) and key in obj:
        return obj[key]
    return None


def _field_aliased(obj: object, canonical: str, alias: str) -> Result[Any, ColumnError]:
    """fuaran-core#92 (lenient-ingest) — accept exactly one of the canonical
    field or its observed alias; both present is ambiguous (didactic), neither
    reports the canonical name."""
    c = _try_field(obj, canonical)
    a = _try_field(obj, alias)
    if c is not None and a is not None:
        return _err(MALFORMED_SHAPE, f'give "{canonical}" (canonical) or "{alias}" (alias), not both')
    if c is not None:
        return Ok(c)
    if a is not None:
        return Ok(a)
    return _err(MISSING_FIELD, canonical)


# fuaran-core#94 (lenient-ingest) — render an epoch-seconds instant as the
# canonical ISO-8601 UTC timestamp string. Pure integer arithmetic
# (civil-from-days), clock-free; negative epochs (pre-1970) are handled.
def _iso_of_epoch_seconds(secs: int) -> str:
    days, sod = divmod(secs, 86400)
    z = days + 719468
    # Python's // already floors; the C-style negative bias would count one era too many
    # (every instant before 0000-03-01 landed a day late — found by Phase 2206's 0000 vector).
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    day = doy - (153 * mp + 2) // 5 + 1
    month = mp + 3 if mp < 10 else mp - 9
    year = yoe + era * 400 + (1 if month <= 2 else 0)
    return f"{year:04d}-{month:02d}-{day:02d}T{sod // 3600:02d}:{sod % 3600 // 60:02d}:{sod % 60:02d}Z"


# ── cells / sources ──────────────────────────────────────────────────────────


# Phase 2206 (fuaran-core#422) — the timestamp unit. The epoch seconds the canonical
# instant text spells: 0000-01-01T00:00:00Z and 9999-12-31T23:59:59Z.
_MIN_EPOCH_SECOND = -62_167_219_200
_MAX_EPOCH_SECOND = 253_402_300_799
_TIMESTAMP_TAG_OF_DIGITS = ("timestamp", "timestamp_ms", "timestamp_us", "timestamp_ns")


def _days_in_month(y: int, m: int) -> int:
    if m == 2:
        return 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28
    return 30 if m in (4, 6, 9, 11) else 31


def _instant_fraction_digits(s: str) -> int | None:
    """The fraction digits of a CANONICAL instant text (``YYYY-MM-DDThh:mm:ssZ``, or
    ``YYYY-MM-DDThh:mm:ss.FZ`` with one to nine digits and no trailing zero, naming a
    moment that exists), or ``None`` for any other text."""
    n = len(s)
    if n < 20 or s[4] != "-" or s[7] != "-" or s[10] != "T" or s[13] != ":" or s[16] != ":":
        return None
    parts = (s[0:4], s[5:7], s[8:10], s[11:13], s[14:16], s[17:19])
    if not all(len(p) == len(p.encode()) and p.isdigit() for p in parts):
        return None
    y, mo, d, h, mi, se = (int(p) for p in parts)
    if not (1 <= mo <= 12 and 1 <= d <= _days_in_month(y, mo) and h <= 23 and mi <= 59 and se <= 59):
        return None
    if n == 20:
        return 0 if s[19] == "Z" else None
    frac = s[20:-1]
    if n > 30 or s[19] != "." or s[-1] != "Z" or frac.endswith("0"):
        return None
    if not (len(frac) == len(frac.encode()) and frac.isdigit()):
        return None
    return len(frac)


def _decode_instant(col: str, ty: str, v: object) -> Result[Cell, ColumnError] | None:
    """A timestamp column's present value at its unit (WIRE_FORMAT §3.6.25). TEXT is
    read only in its canonical form, with a fraction no finer than the unit (a coarser
    instant widens in unchanged — one instant has one text). An EPOCH NUMBER (whole-
    valued, ``|x| < 9e15``) is read in a ``timestamp`` column by magnitude, as
    fuaran-core#94 pinned it (≥ 1e11 ⇒ milliseconds, TRUNCATED to the second), and in a
    sub-second column in its own unit with no guess; either way it must name an instant
    in the years 0000–9999. ``None`` for a value of another JSON kind."""
    digits = TIMESTAMP_UNIT_DIGITS[ty]
    if isinstance(v, str):
        n = _instant_fraction_digits(v)
        if n is None:
            return _err(
                MALFORMED_SHAPE,
                f"{col}: a {ty} value must be canonical ISO-8601 text, YYYY-MM-DDThh:mm:ssZ "
                "or YYYY-MM-DDThh:mm:ss.FZ, naming a moment that exists",
            )
        if n > digits:
            need = _TIMESTAMP_TAG_OF_DIGITS[(n + 2) // 3]
            return _err(
                MALFORMED_SHAPE,
                f"{col}: a {ty} value's fraction is finer than the column's unit holds: {need} or finer is needed",
            )
        return Ok(cell_timestamp(v))
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not (math.isfinite(v) and v == math.floor(v)):
        return None
    if abs(v) >= 9e15:
        return None
    i = int(v)
    if digits == 0:
        # By magnitude; milliseconds TRUNCATE toward zero, as the reference's int64
        # division does (a floor would move a negative instant a second earlier).
        second, fraction = i, 0
        if abs(i) >= 100_000_000_000:
            second = -((-i) // 1000) if i < 0 else i // 1000
    else:
        second, fraction = divmod(i, 10**digits)
    if not _MIN_EPOCH_SECOND <= second <= _MAX_EPOCH_SECOND:
        return _err(
            MALFORMED_SHAPE,
            f"{col}: the epoch {i} names an instant outside the years 0000-9999 the canonical timestamp spells",
        )
    text = _iso_of_epoch_seconds(second)
    if fraction:
        text = f"{text[:19]}.{str(fraction).rjust(digits, '0').rstrip('0')}Z"
    return Ok(cell_timestamp(text))


_INT32_MIN = -2147483648
_INT32_MAX = 2147483647


def _int32_value(v: object) -> int | None:
    """The value of a JSON number that is whole-valued and inside the signed 32-bit range —
    an ``int`` column's value under WIRE_FORMAT §3.6.28 (§7.1's integer-slot rule, read by
    value rather than spelling) — or ``None``."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and not (math.isfinite(v) and v.is_integer()):
        return None
    i = int(v)
    return i if _INT32_MIN <= i <= _INT32_MAX else None


def _decode_cell_value(col: str, ty: str, v: object) -> Result[Cell, ColumnError]:
    """Decode a present value into a cell of the declared column type. A float column
    accepts an integer token (lossless widening); a timestamp column of any unit reads
    through ``_decode_instant``; every other type requires its kind."""
    if ty == "int" and isinstance(v, (int, float)) and not isinstance(v, bool):
        # An int column holds signed 32-bit integers, read by value (WIRE_FORMAT §3.6.28):
        # 2.0 is 2; a fraction, or a value past either end, is refused — never truncated or widened.
        i = _int32_value(v)
        if i is None:
            return _err(
                TYPE_MISMATCH,
                f"column '{col}': an int column holds signed 32-bit integers, and {v!r} is not one",
            )
        return Ok(cell_int(i))
    if ty == "float" and isinstance(v, bool) is False and isinstance(v, (int, float)):
        return Ok(cell_float(float(v)))
    if ty == "bool" and isinstance(v, bool):
        return Ok(cell_bool(v))
    if ty == "string" and isinstance(v, str):
        return Ok(cell_str(v))
    if ty == "date" and isinstance(v, str):
        return Ok(cell_date(v))
    if ty in TIMESTAMP_UNIT_DIGITS:
        r = _decode_instant(col, ty, v)
        if r is not None:
            return r
    if ty == DECIMAL:
        return _decode_decimal_value(col, v)
    return _err(TYPE_MISMATCH, f"column '{col}': expected {ty} value, got {_kind_name(v)}")


#: The largest magnitude a whole-valued number token carries exactly: 2^53.
_INT53_MAX = 9007199254740992


def _decode_decimal_value(col: str, v: object) -> Result[Cell, ColumnError]:
    """A decimal column reads a STRING of decimal text and canonicalises it (``12.50`` decodes to
    ``12.5``), and a whole number token within 2^53, which is exact. A fractional token, or a whole
    one past 2^53, has been through a float by the time it arrives, so it is refused: a type whose
    purpose is exactness cannot vouch for it. An emitter writes a decimal as a string."""
    if isinstance(v, str):
        canonical = decimal_text.try_canonical(v)
        if canonical is None:
            return _err(
                MALFORMED_SHAPE,
                f"column '{col}': a decimal value must be decimal text — an optional '-', digits, and an "
                "optional '.' followed by digits, with no exponent, sign '+', separator or white space",
            )
        return Ok(cell_decimal(canonical))
    if isinstance(v, int) and not isinstance(v, bool) and abs(v) <= _INT53_MAX:
        return Ok(cell_decimal(str(v)))
    if isinstance(v, float) and math.isfinite(v) and v == math.floor(v) and abs(v) <= _INT53_MAX:
        return Ok(cell_decimal(str(int(v))))
    return _err(TYPE_MISMATCH, f"column '{col}': expected decimal value, got {_kind_name(v)}")


def _optional_text(column: str, member: str, entry: dict) -> Result[str | None, ColumnError]:
    """An optional string member of a schema entry: absent is ``None``; present and not a string
    is refused naming the column and the member."""
    if member not in entry:
        return Ok(None)
    v = entry[member]
    if not isinstance(v, str):
        return _err(MALFORMED_SHAPE, f"{column}: schema entry member '{member}' must be a string, got {_kind_name(v)}")
    return Ok(v)


def _decode_meta(name: str, entry: dict) -> Result[FieldMeta, ColumnError]:
    """A schema entry's metadata members (WIRE_FORMAT §3.6.26): ``unit`` parsed by the unit
    algebra and refused, naming the column and the reason, when it does not parse; ``label`` and
    ``description`` strings; ``ext`` an object of strings. Absent members state nothing."""
    ru = _optional_text(name, "unit", entry)
    if not ru.ok:
        return ru  # type: ignore[return-value]
    unit = None
    if ru.value is not None:
        parsed = parse_unit(ru.value)
        if isinstance(parsed, UnitRefusal):
            return _err(
                MALFORMED_SHAPE,
                f"{name}: unit '{ru.value}' is not a unit the kit parses: {refusal_text(parsed)}",
            )
        unit = parsed
    rl = _optional_text(name, "label", entry)
    if not rl.ok:
        return rl  # type: ignore[return-value]
    rd = _optional_text(name, "description", entry)
    if not rd.ok:
        return rd  # type: ignore[return-value]
    ext: dict[str, str] = {}
    if "ext" in entry:
        raw = entry["ext"]
        if not isinstance(raw, dict):
            return _err(MALFORMED_SHAPE, f"{name}: schema entry member 'ext' must be an object, got {_kind_name(raw)}")
        for k, v in raw.items():
            if not isinstance(v, str):
                return _err(
                    MALFORMED_SHAPE,
                    f"{name}: schema entry extension member '{k}' must be a string, got {_kind_name(v)}",
                )
            ext[k] = v
    meta = FieldMeta(unit, rl.value, rd.value, ext)
    return Ok(NO_METADATA if meta.is_empty else meta)


def decode_field(el: object) -> Result[Field, ColumnError]:
    """One schema entry from parsed JSON: ``name`` and ``type``, then its metadata
    (WIRE_FORMAT §3.6.26). A member the entry does not define is read past."""
    if not isinstance(el, dict):
        return _err(MALFORMED_SHAPE, f"expected object, got {_kind_name(el)}")
    rn = _field(el, "name")
    if not rn.ok:
        return rn  # type: ignore[return-value]
    rt = _field(el, "type")
    if not rt.ok:
        return rt  # type: ignore[return-value]
    name, ty = rn.value, rt.value
    if not isinstance(name, str):
        return _err(MALFORMED_SHAPE, "schema.name: expected string")
    if not isinstance(ty, str) or ty not in COLUMN_TYPES:
        return _err(UNKNOWN_TYPE, f"unknown column type '{ty}'; expected one of: {', '.join(COLUMN_TYPES)}")
    rm = _decode_meta(name, el)
    if not rm.ok:
        return rm  # type: ignore[return-value]
    return Ok(Field(name, ty, rm.value))


def decode_schema(el: object) -> Result[list[Field], ColumnError]:
    """A schema array from parsed JSON, each entry through :func:`decode_field`."""
    if not isinstance(el, list):
        return _err(MALFORMED_SHAPE, f"schema: expected array, got {_kind_name(el)}")
    out: list[Field] = []
    for entry in el:
        rf = decode_field(entry)
        if not rf.ok:
            return rf  # type: ignore[return-value]
        out.append(rf.value)
    return Ok(out)


def _column_parts(name: str, col_el: object) -> Result[tuple[list, list], ColumnError]:
    """fuaran-core#88 (lenient-ingest) — a column riding as a BARE JSON array is
    the "just the data" shorthand (`values` with an all-present mask; the wire
    has no JSON null, so a bare array can only mean every cell present).
    fuaran-core#94 — a wrapped object carrying `values` but NO `validity` mask
    is the same all-present statement. Absent cells still require the full
    wrapped form, which stays canonical."""
    if isinstance(col_el, list):
        return Ok((list(col_el), [True] * len(col_el)))
    rv = _field(col_el, "values")
    if not rv.ok:
        return rv  # type: ignore[return-value]
    values = rv.value
    if not isinstance(values, list):
        return _err(MALFORMED_SHAPE, f"{name}.values: expected array")
    validity_el = _try_field(col_el, "validity")
    if validity_el is None:
        return Ok((values, [True] * len(values)))
    if not isinstance(validity_el, list):
        return _err(MALFORMED_SHAPE, f"{name}.validity: expected array")
    return Ok((values, validity_el))


def _decode_column(columns_obj: object, name: str, ty: str) -> Result[Column, ColumnError]:
    if not isinstance(columns_obj, dict) or name not in columns_obj:
        return _err(MISSING_FIELD, f"columns.{name}")
    rparts = _column_parts(name, columns_obj[name])
    if not rparts.ok:
        return rparts  # type: ignore[return-value]
    values, validity = rparts.value
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


def _infer_column_type(name: str, values: list) -> Result[str, ColumnError]:
    """fuaran-core#88 (lenient-ingest) — infer one column's type from its cells.
    PINNED deterministic rules: all-int numerics ⇒ int (a whole value inside int32,
    WIRE_FORMAT §3.6.28), any other number ⇒ float,
    all-bool ⇒ bool, all-string ⇒ string — NEVER date/timestamp (temporal types
    require a declared schema). Empty or mixed is a didactic reject."""
    if not values:
        return _err(
            MALFORMED_SHAPE,
            f"{name}: cannot infer a column type from an empty / all-null column — declare it in an "
            'explicit "schema" array',
        )
    tags: list[str] = []
    for v in values:
        # By value (WIRE_FORMAT §3.6.28): a whole number inside int32 is an int, any other
        # number a float — so 2.0 infers int and a whole value past the range infers float.
        tag = _kind_name(v)
        if tag in ("int", "float"):
            tag = "int" if _int32_value(v) is not None else "float"
        if tag not in tags:
            tags.append(tag)
    tag_set = set(tags)
    if tag_set == {"int"}:
        return Ok("int")
    if "float" in tag_set and tag_set <= {"int", "float"}:
        return Ok("float")
    if tag_set == {"bool"}:
        return Ok("bool")
    if tag_set == {"string"}:
        return Ok("string")
    return _err(
        MALFORMED_SHAPE,
        f"{name}: cannot infer a single column type from mixed cell kinds ({', '.join(tags)}) — "
        'declare it in an explicit "schema" array',
    )


def decode_source_json(el: object) -> Result[DataSource, ColumnError]:
    # fuaran-core#88 — `schema` may be OMITTED on an EMBEDDED source (inferred
    # per `_infer_column_type`, columns in Ordinal key order); a `ref` source
    # still requires it (no cells to infer from). The canonical encoder always
    # emits the explicit schema, so the shorthand normalises on re-encode.
    fields: list[Field] | None = None
    if isinstance(el, dict) and "schema" in el:
        rschema = decode_schema(el["schema"])
        if not rschema.ok:
            return rschema  # type: ignore[return-value]
        fields = rschema.value
    if isinstance(el, dict) and "ref" in el:
        if fields is None:
            return _err(
                MALFORMED_SHAPE,
                'a ref source requires an explicit "schema" array — there are no cells to infer column types from',
            )
        ref = el["ref"]
        if not isinstance(ref, str):
            return _err(MALFORMED_SHAPE, "ref: expected string")
        return Ok(Ref(ref))
    rc = _field(el, "columns")
    if not rc.ok:
        return rc  # type: ignore[return-value]
    columns_obj = rc.value
    if fields is None:
        if not isinstance(columns_obj, dict):
            return _err(MALFORMED_SHAPE, "columns: expected object")
        fields = []
        for name in sorted(columns_obj):
            rparts = _column_parts(name, columns_obj[name])
            if not rparts.ok:
                return rparts  # type: ignore[return-value]
            rty = _infer_column_type(name, rparts.value[0])
            if not rty.ok:
                return rty  # type: ignore[return-value]
            fields.append(Field(name, rty.value))
    cols: list[Column] = []
    for f in fields:
        rcol = _decode_column(columns_obj, f.name, f.type)
        if not rcol.ok:
            return rcol  # type: ignore[return-value]
        cols.append(rcol.value)
    return Ok(Embedded(table_of_fields(fields, cols)))


def decode_source(text: str) -> Result[DataSource, ColumnError]:
    parsed, guard_error = _guarded_parse(text)
    if guard_error is not None:
        return guard_error
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
    # Decimal text, canonicalised on read; an integer token is exact and read too, and anything
    # else — a fractional number token included — is refused.
    if tag == "Decimal" and isinstance(v, str):
        canonical = decimal_text.try_canonical(v)
        if canonical is not None:
            return Ok(cell_decimal(canonical))
    if tag == "Decimal" and isinstance(v, int) and not isinstance(v, bool):
        return Ok(cell_decimal(str(v)))
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
    if k in ("apply", "call", "fn"):
        # fuaran-core#93 — `call` aliases `apply` (same fn/args fields);
        # fuaran-core#94 adds the third observed spelling `fn`.
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
    if k == "in":
        # fuaran-core#91 — membership: exactly one of `items` (literal list) /
        # `param` (a bound multi-select list param).
        rsub = _field(el, "expr")
        if not rsub.ok:
            return rsub  # type: ignore[return-value]
        subject = decode_expr(rsub.value)
        if not subject.ok:
            return subject  # type: ignore[return-value]
        items_el = _try_field(el, "items")
        param_el = _try_field(el, "param")
        if items_el is not None and param_el is not None:
            return _err(
                MALFORMED_SHAPE,
                'in: give exactly ONE of "items" (a literal list) or "param" (a multi-select list param), not both',
            )
        if items_el is not None:
            if not isinstance(items_el, list):
                return _err(MALFORMED_SHAPE, "expected array")
            xs = _decode_expr_list(items_el)
            return xs if not xs.ok else Ok(InList(subject.value, xs.value))  # type: ignore[return-value]
        if param_el is not None:
            if not isinstance(param_el, str):
                return _err(MALFORMED_SHAPE, "expected string")
            return Ok(InParam(subject.value, param_el))
        return _err(MISSING_FIELD, "items")
    if k == "isNull":
        r = _field(el, "expr")
        if not r.ok:
            return r  # type: ignore[return-value]
        inner = decode_expr(r.value)
        return inner if not inner.ok else Ok(IsNull(inner.value))  # type: ignore[return-value]
    if k == "quotient":
        rdd = _field(el, "dividend")
        if not rdd.ok:
            return rdd  # type: ignore[return-value]
        dividend = decode_expr(rdd.value)
        if not dividend.ok:
            return dividend
        rdv = _field(el, "divisor")
        if not rdv.ok:
            return rdv  # type: ignore[return-value]
        divisor = decode_expr(rdv.value)
        if not divisor.ok:
            return divisor
        rr = _field(el, "rounding")
        if not rr.ok:
            return rr  # type: ignore[return-value]
        rounding = _decode_rounding(rr.value)
        if not rounding.ok:
            return rounding  # type: ignore[return-value]
        return Ok(Quotient(dividend.value, divisor.value, rounding.value))
    if k == "rounded":
        r = _field(el, "expr")
        if not r.ok:
            return r  # type: ignore[return-value]
        inner = decode_expr(r.value)
        if not inner.ok:
            return inner
        rr = _field(el, "rounding")
        if not rr.ok:
            return rr  # type: ignore[return-value]
        rounding = _decode_rounding(rr.value)
        if not rounding.ok:
            return rounding  # type: ignore[return-value]
        return Ok(Rounded(inner.value, rounding.value))
    if k == "convert":
        ru = _field(el, "unit")
        if not ru.ok:
            return ru  # type: ignore[return-value]
        if not isinstance(ru.value, str):
            return _err(MALFORMED_SHAPE, "convert: unit must be a string")
        unit = parse_unit(ru.value)
        if isinstance(unit, UnitRefusal):
            return _err(MALFORMED_SHAPE, f"convert: '{ru.value}' is not a unit")
        r = _field(el, "expr")
        if not r.ok:
            return r  # type: ignore[return-value]
        inner = decode_expr(r.value)
        if not inner.ok:
            return inner
        return Ok(Convert(inner.value, unit))
    if k in ("contains", "startsWith", "endsWith"):
        # fuaran-core#93 — expression-level string-predicate spellings:
        # {"$type":"contains","expr":X,"other":Y} (also left/right) denotes
        # exactly Binary(contains, X, Y). Canonical stays the "binary" form.
        return _decode_flat_binary(el, k)
    if k in ("and", "or"):
        # fuaran-core#94 — flat logical spellings: variadic `exprs` left-folds
        # into the nested binary form (and/or are associative), or left/right.
        exprs_el = _try_field(el, "exprs")
        if exprs_el is not None:
            if not isinstance(exprs_el, list):
                return _err(MALFORMED_SHAPE, "expected array")
            xs = _decode_expr_list(exprs_el)
            if not xs.ok:
                return xs  # type: ignore[return-value]
            if not xs.value:
                return _err(MALFORMED_SHAPE, f"{k}.exprs: expected a non-empty array")
            acc = xs.value[0]
            for e in xs.value[1:]:
                acc = Binary(k, acc, e)
            return Ok(acc)
        return _decode_flat_binary(el, k)
    if k in ("eq", "ne", "lt", "le", "gt", "ge"):
        # fuaran-core#94 — flat comparison spellings.
        return _decode_flat_binary(el, k)
    if k in SCALAR_FNS:
        # fuaran-core#94 — flat scalar-fn spellings: {"$type":"lower","expr":X}
        # / {"$type":"concat","args":[…]} denote ApplyFn(fn, args).
        args_el = _try_field(el, "args")
        if args_el is not None:
            if not isinstance(args_el, list):
                return _err(MALFORMED_SHAPE, "expected array")
            xs = _decode_expr_list(args_el)
            return xs if not xs.ok else Ok(ApplyFn(k, xs.value))  # type: ignore[return-value]
        r = _field(el, "expr")
        if not r.ok:
            return r  # type: ignore[return-value]
        inner = decode_expr(r.value)
        return inner if not inner.ok else Ok(ApplyFn(k, [inner.value]))  # type: ignore[return-value]
    return _err(UNKNOWN_TYPE, f"unknown ColExpr '{k}'")


def _decode_rounding(el: object) -> Result[Rounding, ColumnError]:
    """A rounding policy: ``mode`` one of the seven spellings (anything else is ``UNKNOWN_TYPE``
    naming them), ``scale`` an integer slot — the literal, or ``{"$param": name}``."""
    rm = _field(el, "mode")
    if not rm.ok:
        return rm  # type: ignore[return-value]
    if not isinstance(rm.value, str):
        return _err(MALFORMED_SHAPE, "rounding mode: expected string")
    if rm.value not in ROUNDING_MODES:
        return _err(UNKNOWN_TYPE, f"unknown rounding mode '{rm.value}'; expected one of: {', '.join(ROUNDING_MODES)}")
    rs = _field(el, "scale")
    if not rs.ok:
        return rs  # type: ignore[return-value]
    scale = rs.value
    if isinstance(scale, dict):
        if "$param" not in scale:
            return _err(MALFORMED_SHAPE, 'rounding scale: an object here is a parameter slot and must carry "$param"')
        if not isinstance(scale["$param"], str):
            return _err(MALFORMED_SHAPE, '"$param" must be a JSON string (rounding scale)')
        return Ok(Rounding(Param(scale["$param"]), rm.value))
    if isinstance(scale, bool) or not isinstance(scale, int):
        return _err(MALFORMED_SHAPE, "rounding scale: expected int")
    return Ok(Rounding(scale, rm.value))


def _decode_flat_binary(el: object, op: str) -> Result[ColExpr, ColumnError]:
    """A flat binary spelling: `left`/`right` canonical, `expr`/`other` aliases."""
    ra = _field_aliased(el, "left", "expr")
    if not ra.ok:
        return ra  # type: ignore[return-value]
    la = decode_expr(ra.value)
    if not la.ok:
        return la  # type: ignore[return-value]
    rb = _field_aliased(el, "right", "other")
    if not rb.ok:
        return rb  # type: ignore[return-value]
    lb = decode_expr(rb.value)
    if not lb.ok:
        return lb  # type: ignore[return-value]
    return Ok(Binary(op, la.value, lb.value))


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
    # fuaran-core#92 admitted `column` as an alias of `col`; 0.28.0 SWAPPED which of the
    # two is canonical, so both still decode, `column` re-encodes, and giving both is
    # refused as ambiguous. Boolean `descending` remains an alias for `dir`; #93 —
    # `direction` is a third spelling, and a directionless entry is the SQL default (asc).
    rc = _field_aliased(el, "column", "col")
    if not rc.ok:
        return rc  # type: ignore[return-value]
    if not isinstance(rc.value, str):
        return _err(MALFORMED_SHAPE, "order.column: expected string")
    name = rc.value
    dir_el = _try_field(el, "dir")
    desc_el = _try_field(el, "descending")
    direction_el = _try_field(el, "direction")
    present = [x for x in (dir_el, desc_el, direction_el) if x is not None]
    if len(present) > 1:
        return _err(
            MALFORMED_SHAPE,
            'give ONE of "dir" (canonical: asc|desc), "descending" (alias boolean), or "direction" (alias: asc|desc)',
        )
    if desc_el is not None:
        if not isinstance(desc_el, bool):
            return _err(MALFORMED_SHAPE, '"descending" must be a JSON boolean')
        return Ok((name, "desc" if desc_el else "asc"))
    chosen = dir_el if dir_el is not None else direction_el
    if chosen is None:
        return Ok((name, "asc"))
    if not isinstance(chosen, str):
        return _err(MALFORMED_SHAPE, "expected string")
    return Ok((name, "desc" if chosen == "desc" else "asc"))


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


def _agg_fn_of(tag: object) -> str | None:
    """The agg-fn vocabulary + the `avg` → `mean` alias (the SQL prior)."""
    if tag == "avg":
        return "mean"
    if isinstance(tag, str) and tag in AGG_FNS:
        return tag
    return None


def _agg_of(el: object) -> Result[Agg, ColumnError]:
    # fuaran-core#92 — aggregate-entry aliases: `as` for `name`, `op` for `fn`,
    # `column` for `of`; `avg` aliases `mean`.
    rn = _field_aliased(el, "name", "as")
    if not rn.ok:
        return rn  # type: ignore[return-value]
    rf = _field_aliased(el, "fn", "op")
    if not rf.ok:
        return rf  # type: ignore[return-value]
    ro = _field_aliased(el, "of", "column")
    if not ro.ok:
        return ro  # type: ignore[return-value]
    fn = _agg_fn_of(rf.value)
    if fn is None:
        return _err(UNKNOWN_TYPE, f"unknown agg fn '{rf.value}'")
    if not isinstance(rn.value, str) or not isinstance(ro.value, str):
        return _err(MALFORMED_SHAPE, "agg: expected strings")
    return Ok(Agg(rn.value, fn, ro.value))


def decode_transform(el: object) -> Result[Transform, ColumnError]:  # noqa: C901, PLR0911, PLR0912
    rk = _kind_of(el)
    if not rk.ok:
        return rk  # type: ignore[return-value]
    k = rk.value
    assert isinstance(el, dict)
    if k == "filter":
        # fuaran-core#93 — `predicate` aliases `pred`; fuaran-core#89 — the flat
        # filter-step prior {"$type":"filter","column":C,"op":O,"param":P|"value":V}
        # coerces to the canonical nested predicate.
        pred_el = _try_field(el, "pred")
        predicate_el = _try_field(el, "predicate")
        if pred_el is not None and predicate_el is not None:
            return _err(MALFORMED_SHAPE, 'give "pred" (canonical) or "predicate" (alias), not both')
        chosen = pred_el if pred_el is not None else predicate_el
        if chosen is not None:
            e = decode_expr(chosen)
            return e if not e.ok else Ok(Filter(e.value))  # type: ignore[return-value]
        col_el = _try_field(el, "column")
        op_el = _try_field(el, "op")
        if col_el is not None and op_el is not None:
            if not isinstance(col_el, str) or not isinstance(op_el, str):
                return _err(MALFORMED_SHAPE, "expected string")
            if op_el not in BIN_OPS:
                return _err(UNKNOWN_TYPE, f"unknown binary op '{op_el}'")
            param_el = _try_field(el, "param")
            value_el = "value" in el if isinstance(el, dict) else False
            if param_el is not None and value_el:
                return _err(
                    MALFORMED_SHAPE,
                    'flat filter step: give exactly ONE of "param" (a pipeline param name) or "value" '
                    "(a scalar literal), not both",
                )
            if param_el is not None:
                if not isinstance(param_el, str):
                    return _err(MALFORMED_SHAPE, "expected string")
                return Ok(Filter(Binary(op_el, Col(col_el), Param(param_el))))
            if value_el:
                assert isinstance(el, dict)
                v = el["value"]
                if isinstance(v, bool):
                    cell = cell_bool(v)
                elif isinstance(v, int):
                    cell = cell_int(v)
                elif isinstance(v, float):
                    cell = cell_float(v)
                elif isinstance(v, str):
                    cell = cell_str(v)
                else:
                    return _err(MALFORMED_SHAPE, 'flat filter step: "value" must be a scalar (string/int/float/bool)')
                return Ok(Filter(Binary(op_el, Col(col_el), Lit(cell))))
            return _err(
                MALFORMED_SHAPE,
                'flat filter step: {column, op} needs "param" (a pipeline param name) or "value" '
                "(a scalar literal) as the right-hand side",
            )
        return _err(
            MALFORMED_SHAPE,
            'a filter step carries "pred" (a $type-discriminated expression: binary/col/param/lit/apply) — '
            'or the flat short form {"column":…,"op":…,"param":…|"value":…}',
        )
    if k == "project":
        # 0.28.0 — `cols` is the pre-rename spelling, kept as a decode alias; giving both
        # is the same ambiguity refusal every other aliased member of this algebra makes.
        r = _field_aliased(el, "columns", "cols")
        if not r.ok:
            return r  # type: ignore[return-value]
        ps = _list_of(r.value, "project.columns", _pair_of)
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
        # fuaran-core#92 — `by` (pandas prior) aliases `keys`; `aggregations` aliases `aggs`.
        rkeys = _field_aliased(el, "keys", "by")
        if not rkeys.ok:
            return rkeys  # type: ignore[return-value]
        keys = _str_list(rkeys.value, "groupBy.keys")
        if not keys.ok:
            return keys  # type: ignore[return-value]
        raggs = _field_aliased(el, "aggs", "aggregations")
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
        # fuaran-core#92 — `cumSum` is the legacy pre-rename tag; normalises to
        # `cumulSum` on re-encode.
        if rfn.value == "cumSum":
            rfn = Ok("cumulSum")
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
        agg_fn = _agg_fn_of(ragg.value)
        if agg_fn is None:
            return _err(UNKNOWN_TYPE, f"unknown agg fn '{ragg.value}'")
        return Ok(Pivot(PivotSpec(index.value, ron.value, rvals.value, agg_fn)))
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
        # fuaran-core#92 — `keys` (SQL ORDER-BY-list prior) aliases `by`.
        r = _field_aliased(el, "by", "keys")
        if not r.ok:
            return r  # type: ignore[return-value]
        by = _list_of(r.value, "sort.by", _order_of)
        return by if not by.ok else Ok(Sort(by.value))  # type: ignore[return-value]
    if k == "distinct":
        return Ok(Distinct())
    if k == "limit":
        # fuaran-core#92 — `count` aliases `n`; an absent `offset` is unambiguously 0.
        rn = _field_aliased(el, "n", "count")
        if not rn.ok:
            return rn  # type: ignore[return-value]
        off_el = _try_field(el, "offset")
        offset = 0 if off_el is None else off_el
        if (
            isinstance(rn.value, bool)
            or not isinstance(rn.value, int)
            or isinstance(offset, bool)
            or not isinstance(offset, int)
        ):
            return _err(MALFORMED_SHAPE, "limit: expected ints")
        return Ok(Limit(rn.value, offset))
    if k == "union":
        r = _field(el, "source")
        if not r.ok:
            return r  # type: ignore[return-value]
        src = decode_source_json(r.value)
        return src if not src.ok else Ok(Union(src.value))  # type: ignore[return-value]
    if k in ("intersect", "except"):
        r = _field(el, "source")
        if not r.ok:
            return r  # type: ignore[return-value]
        src = decode_source_json(r.value)
        if not src.ok:
            return src  # type: ignore[return-value]
        return Ok(Intersect(src.value) if k == "intersect" else Except(src.value))
    return _err(UNKNOWN_TYPE, f"unknown Transform '{k}'")


def decode_pipeline(text: str) -> Result[list[Transform], ColumnError]:
    parsed, guard_error = _guarded_parse(text)
    if guard_error is not None:
        return guard_error
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
