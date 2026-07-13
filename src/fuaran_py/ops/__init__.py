"""``fuaran_py.ops`` — the ``TreeOp`` codec.

Public surface:

* :func:`~fuaran_py.ops.decode.decode_op` — wire JSON → ``Result[Obj, DecodeError]``
* :func:`~fuaran_py.ops.encode.encode_op` — decoded op → canonical wire JSON
"""

from __future__ import annotations

from .apply import ApplyErr, ApplyError, ApplyResult, apply
from .decode import OP_CASES, decode_op
from .diff import diff, diff_batched
from .encode import encode_op

__all__ = [
    "decode_op",
    "encode_op",
    "OP_CASES",
    "apply",
    "ApplyError",
    "ApplyErr",
    "ApplyResult",
    "diff",
    "diff_batched",
]
