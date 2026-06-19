"""Pre-emit validation surface (default-deny by shape).

The decoder already rejects malformed *wire* input; this surface validates a
*constructed* tree before it is emitted, catching the structural defects an
author is most likely to introduce — empty node ids, duplicate ids, and
unrecognised node kinds — and returning structured findings rather than throwing.
It is intentionally small for the bootstrap; the full rule set (the analogue of
the language tier's validator framework) is filled in incrementally.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model import Arr, Node, Obj, Value
from ..schema.decode import KNOWN_KINDS


@dataclass(frozen=True)
class Finding:
    """A structural validation finding at a ``$``-rooted path."""

    code: str
    path: str
    message: str


def validate_node(node: Node) -> list[Finding]:
    """Walk a node tree, returning any structural findings (empty list ⇒ clean)."""
    findings: list[Finding] = []
    seen_ids: set[str] = set()
    _walk(node, "$", findings, seen_ids)
    return findings


def _walk(node: Node, path: str, findings: list[Finding], seen_ids: set[str]) -> None:
    if node.id == "":
        findings.append(Finding("EMPTY_NODE_ID", f"{path}.id", "node id is empty"))
    elif node.id in seen_ids:
        findings.append(Finding("DUPLICATE_NODE_ID", f"{path}.id", f"duplicate node id '{node.id}'"))
    else:
        seen_ids.add(node.id)

    if node.kind.tag not in KNOWN_KINDS:
        findings.append(Finding("UNKNOWN_NODE_KIND", f"{path}.kind.$type", f"unrecognised node kind '{node.kind.tag}'"))

    for child, child_path in _child_nodes(node.kind, f"{path}.kind"):
        _walk(child, child_path, findings, seen_ids)


def _child_nodes(value: Value, path: str) -> list[tuple[Node, str]]:
    """Find directly-nested ``Node`` values (e.g. layout ``children``)."""
    out: list[tuple[Node, str]] = []
    if isinstance(value, Node):
        out.append((value, path))
    elif isinstance(value, Arr):
        for i, item in enumerate(value.items):
            out.extend(_child_nodes(item, f"{path}.{i}"))
    elif isinstance(value, Obj):
        for key, field_value in value.fields.items():
            out.extend(_child_nodes(field_value, f"{path}.{key}"))
    return out
