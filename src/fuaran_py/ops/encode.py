"""Canonical encode for a decoded ``TreeOp`` (WIRE_FORMAT.md §2, §3.4)."""

from __future__ import annotations

from ..canonical import encode_value
from ..model import Obj


def encode_op(op: Obj) -> str:
    """Encode a decoded ``TreeOp`` (a tagged :class:`~fuaran_py.model.Obj`) to canonical wire JSON."""
    return encode_value(op)
