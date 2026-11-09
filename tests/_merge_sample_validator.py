"""The sample domain validator + introduced-defect diff + verdict codec (Phase 184).

Ported TEST-SIDE, exactly as the F#/TS hosts port them — the invariant is a
documented sample, not a host API. Shared by the tree-merge gate
(``merge-validator-gated``, ``test_merge_conformance``) and the fold gate
(``merge-fold-gated``, ``test_merge_fold_conformance``), so the two certify
against one validator.
"""

from __future__ import annotations

from dataclasses import dataclass

from fuaran_ui.canonical import escape_string
from fuaran_ui.model import Arr, Node, Obj


@dataclass(frozen=True)
class MergeDefect:
    code: str
    node_id: str
    facet: str
    message: str


def _children(tree: Node) -> list[Node]:
    children = tree.kind.fields.get("children")
    if isinstance(children, Arr):
        return [c for c in children.items if isinstance(c, Node)]
    return []


def _tone(node: Node) -> str:
    style = node.extras.get("style")
    if isinstance(style, Obj):
        v = style.fields.get("tone")
        if isinstance(v, str):
            return v
    return "Default"


def _gated_validator(tree: Node) -> list[MergeDefect]:
    """Sample domain validator: "at most one Brand-toned pane per dashboard".
    Inspects the root node only (not recursive), mirroring the F#/TS walker.
    Post-Phase-390 the dashboard is a ``Box`` with ``role == "Dashboard"``."""
    if tree.kind.tag != "Box" or tree.kind.fields.get("role") != "Dashboard":
        return []
    brand_kids = [c for c in _children(tree) if _tone(c) == "Brand"]
    if len(brand_kids) <= 1:
        return []
    return [
        MergeDefect(
            "TESTBRAND001",
            c.id,
            "style.tone",
            f"Pane '{c.id}' shares Brand tone with a sibling — at most one Brand pane per dashboard.",
        )
        for c in brand_kids
    ]


def _identity(d: MergeDefect) -> str:
    return f"{d.code} {d.node_id} {d.facet}"


def _order_key(d: MergeDefect) -> str:
    return f"{d.node_id} {d.facet} {d.code}"


def introduced_defects(parent_a: Node, parent_b: Node, merged: Node) -> list[MergeDefect]:
    parent_keys = {_identity(d) for d in _gated_validator(parent_a)} | {
        _identity(d) for d in _gated_validator(parent_b)
    }
    introduced = [d for d in _gated_validator(merged) if _identity(d) not in parent_keys]
    return sorted(introduced, key=_order_key)


def encode_verdict(defects: list[MergeDefect]) -> str:
    entries = [
        "{"
        + '"code":'
        + escape_string(d.code)
        + ',"facet":'
        + escape_string(d.facet)
        + ',"message":'
        + escape_string(d.message)
        + ',"nodeId":'
        + escape_string(d.node_id)
        + "}"
        for d in sorted(defects, key=_order_key)
    ]
    return "[" + ",".join(entries) + "]"
