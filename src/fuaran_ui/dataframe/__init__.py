"""``fuaran_ui.dataframe`` — the Compute-layer columnar strand.

A self-contained, dependency-light implementation of the Compute-layer wire surface
and its reference evaluator:

* :mod:`~fuaran_ui.dataframe.model` — the typed columnar model (``Cell`` / ``Column`` /
  ``Table`` / ``DataSource``) + the serializable ``Transform`` / ``ColExpr`` algebra.
* :mod:`~fuaran_ui.dataframe.codec` — the byte-exact canonical codec (encode reuses the
  proven node encoder; decode walks against the schema), the Python leg of cross-host
  conformance.
* :mod:`~fuaran_ui.dataframe.evaluate` — the pure columnar reference evaluator with the
  pinned null/coercion/ordering/float semantics, certified byte-identical to the
  reference over the F#-generated parity fixtures.
* :mod:`~fuaran_ui.dataframe.decimal_text` — the exact decimal a ``decimal`` cell carries
  as canonical text: its grammar, order, arithmetic and the one rounding kernel.

The ergonomic, polars-shaped *authoring* surface that emits a ``Transform`` lives in
:mod:`fuaran_ui.ui` (``frame`` / ``col``); this package is the data + engine.
"""

from __future__ import annotations

from . import codec, decimal_text, evaluate, model
from .codec import (
    decode_pipeline,
    decode_pipeline_json,
    decode_source,
    decode_source_json,
    encode_pipeline,
    encode_source,
    try_encode_source,
)
from .evaluate import aggregate, cell_token, eval_pipeline, eval_pipeline_with, no_resolve
from .model import (
    NULL,
    ROUNDING_MODES,
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
    EvalError,
    Except,
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
)

__all__ = [
    # submodules
    "model",
    "codec",
    "decimal_text",
    "evaluate",
    # codec
    "encode_source",
    "try_encode_source",
    "decode_source",
    "decode_source_json",
    "encode_pipeline",
    "decode_pipeline",
    "decode_pipeline_json",
    # evaluator
    "eval_pipeline",
    "eval_pipeline_with",
    "no_resolve",
    "aggregate",
    "cell_token",
    # model — sources / tables
    "Cell",
    "Column",
    "Table",
    "DataSource",
    "Embedded",
    "Ref",
    "Schema",
    "NULL",
    "cell_int",
    "cell_float",
    "cell_bool",
    "cell_str",
    "cell_date",
    "cell_timestamp",
    "cell_decimal",
    # model — algebra
    "ColExpr",
    "Col",
    "Lit",
    "Binary",
    "Not",
    "Coalesce",
    "Case",
    "Cast",
    "ApplyFn",
    "Param",
    "InList",
    "InParam",
    "IsNull",
    "Quotient",
    "Rounded",
    "Rounding",
    "ROUNDING_MODES",
    "Transform",
    "Filter",
    "Project",
    "Derive",
    "GroupBy",
    "Join",
    "Window",
    "WindowSpec",
    "Pivot",
    "PivotSpec",
    "Unpivot",
    "Sort",
    "Distinct",
    "Limit",
    "Union",
    "Intersect",
    "Except",
    "Agg",
    # results / errors
    "Ok",
    "Err",
    "Result",
    "ColumnError",
    "EvalError",
]
