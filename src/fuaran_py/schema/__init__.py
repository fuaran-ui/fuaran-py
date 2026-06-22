"""``fuaran_py.schema`` — the typed tree + canonical Node codec.

Public surface:

* :func:`~fuaran_py.schema.decode.decode_node` — wire JSON → ``Result[Node, DecodeError]``
* :func:`~fuaran_py.schema.encode.encode_node` — ``Node`` → canonical wire JSON
"""

from __future__ import annotations

from . import types
from .decode import KIND_SCHEMAS, KNOWN_KINDS, decode_node
from .encode import encode_node

__all__ = ["decode_node", "encode_node", "KNOWN_KINDS", "KIND_SCHEMAS", "types"]
