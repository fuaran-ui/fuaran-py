"""Canonical encode for a decoded ``Node`` (WIRE_FORMAT.md §2)."""

from __future__ import annotations

from ..canonical import encode_value
from ..model import Node


def encode_node(node: Node) -> str:
    """Encode a :class:`~fuaran_py.model.Node` to canonical wire JSON."""
    return encode_value(node)
