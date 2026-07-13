"""fuaran-py — a headless Python host of the Fuaran UI wire format.

A dependency-light, idiomatic-Python reference implementation of the canonical
Fuaran UI wire format (``WIRE_FORMAT.md``): decode / encode for the ``Node`` tree
and the ``TreeOp`` algebra, plus a pre-emit validator. It is a *sibling*
reference implementation built to the language-neutral spec + conformance
corpus, not a transpile of any other host.

Canonical imports::

    from fuaran_py import decode_node, encode_node, decode_op, encode_op
    from fuaran_py import decode_dag_record, encode_dag_record   # branching op-stream
    from fuaran_py import Ok, Err, DecodeError
    from fuaran_py.renderer import render_html      # optional headless renderer
    from fuaran_py.client import FuaranClient, FuaranSession   # generation-endpoint client
"""

from __future__ import annotations

from .dag import DagOpRecord, DagResultEnvelope, decode_dag_record, encode_dag_record
from .merge import (
    MergeAuthor,
    MergeConflict,
    MergeConflicts,
    MergeOk,
    MergeResult,
    Primary,
    Secondary,
    merge3,
    merge3_way_lenient,
    merge3_way_with_author,
    merge_3way,
)
from .model import Arr, Node, Obj, from_json
from .ops import decode_op, diff, encode_op
from .result import (
    CODES,
    DecodeError,
    DecodeResult,
    Err,
    Ok,
)
from .schema import decode_node, encode_node
from .validator import Finding, validate_node

__version__ = "0.0.1"

__all__ = [
    "decode_node",
    "encode_node",
    "decode_op",
    "encode_op",
    "decode_dag_record",
    "encode_dag_record",
    "DagOpRecord",
    "DagResultEnvelope",
    "merge_3way",
    "merge3",
    "merge3_way_with_author",
    "merge3_way_lenient",
    "MergeOk",
    "MergeConflicts",
    "MergeConflict",
    "MergeResult",
    "MergeAuthor",
    "Primary",
    "Secondary",
    "diff",
    "validate_node",
    "Finding",
    "Node",
    "Obj",
    "Arr",
    "from_json",
    "Ok",
    "Err",
    "DecodeError",
    "DecodeResult",
    "CODES",
    "__version__",
]
