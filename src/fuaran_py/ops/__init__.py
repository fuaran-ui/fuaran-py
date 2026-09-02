"""``fuaran_py.ops`` — the ``TreeOp`` codec.

Public surface:

* :func:`~fuaran_py.ops.decode.decode_op` — wire JSON → ``Result[Obj, DecodeError]``
* :func:`~fuaran_py.ops.encode.encode_op` — decoded op → canonical wire JSON
* :mod:`fuaran_py.ops.placement` — the placement algebra (``place_op`` / ``move_op`` /
  ``nudge_op`` / ``can_place``) and the clone verbs (``duplicate_op`` / ``paste_op``),
  which emit only the ops above — no new wire vocabulary.
"""

from __future__ import annotations

from .apply import ApplyErr, ApplyError, ApplyResult, apply
from .decode import OP_CASES, decode_op
from .diff import diff, diff_batched
from .encode import encode_op
from .placement import (
    After,
    Before,
    First,
    Last,
    PlaceCheck,
    PlaceErr,
    PlaceError,
    Placement,
    PlaceResult,
    Target,
    can_place,
    derived_ids,
    duplicate_op,
    duplicate_op_with,
    move_op,
    nudge_op,
    paste_op,
    paste_op_with,
    place_op,
    sequential_ids,
)

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
    # Placement algebra + clone verbs (Phase 831).
    "Placement",
    "Last",
    "First",
    "Before",
    "After",
    "Target",
    "PlaceError",
    "PlaceErr",
    "PlaceResult",
    "PlaceCheck",
    "can_place",
    "place_op",
    "move_op",
    "nudge_op",
    "duplicate_op",
    "duplicate_op_with",
    "paste_op",
    "paste_op_with",
    "derived_ids",
    "sequential_ids",
]
