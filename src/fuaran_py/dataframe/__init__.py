"""``fuaran_py.dataframe`` — the Compute-layer columnar strand.

A self-contained, dependency-light implementation of the Compute-layer wire surface
and its reference evaluator:

* :mod:`~fuaran_py.dataframe.model` — the typed columnar model (``Cell`` / ``Column`` /
  ``Table`` / ``DataSource``) + the serializable ``Transform`` / ``ColExpr`` algebra.
* :mod:`~fuaran_py.dataframe.codec` — the byte-exact canonical codec (encode reuses the
  proven node encoder; decode walks against the schema), the Python leg of cross-host
  conformance.
* :mod:`~fuaran_py.dataframe.evaluate` — the pure columnar reference evaluator with the
  pinned null/coercion/ordering/float semantics, certified byte-identical to the
  reference over the F#-generated parity fixtures.

The ergonomic, polars-shaped *authoring* surface that emits a ``Transform`` lives in
:mod:`fuaran_py.ui` (``frame`` / ``col``); this package is the data + engine.
"""

from __future__ import annotations

from . import codec, evaluate, model
from .codec import (
    decode_pipeline,
    decode_pipeline_json,
    decode_source,
    decode_source_json,
    encode_pipeline,
    encode_source,
)
from .evaluate import eval_pipeline, eval_pipeline_with, no_resolve
from .model import (
    NULL,
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
    Filter,
    GroupBy,
    Join,
    Limit,
    Lit,
    Not,
    Ok,
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
)

__all__ = [
    # submodules
    "model",
    "codec",
    "evaluate",
    # codec
    "encode_source",
    "decode_source",
    "decode_source_json",
    "encode_pipeline",
    "decode_pipeline",
    "decode_pipeline_json",
    # evaluator
    "eval_pipeline",
    "eval_pipeline_with",
    "no_resolve",
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
    "Agg",
    # results / errors
    "Ok",
    "Err",
    "Result",
    "ColumnError",
    "EvalError",
]
