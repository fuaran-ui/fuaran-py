"""Merge-conformance: the Python host reproduces the F# host's merge result exactly.

Mirrors the F#/TS ``merge.test.ts`` Leg-B gate over the workspace
``wire-format-fixtures/merge-conformance/`` corpus. Two fixture kinds:

* ``merge-3way`` — ``encode(merge_3way(base, a, b))`` is byte-identical to the
  committed ``expectedFile`` and ``sha256`` of those bytes == the manifest
  ``outcomeHash`` (the SemanticStyle sub-field blend + the NodeId-byte tie-break).
* ``merge-validator-gated`` — a structurally-clean merge that *introduces* a
  domain-validity defect (present in the merged tree but in neither parent) is a
  semantic conflict; the deterministic artifact is the **verdict** (the
  introduced-defect set, canonically encoded). The sample domain validator + the
  introduced-defect diff + the verdict codec are ported **test-side**, exactly as
  the F#/TS hosts port them — the invariant is a documented sample, not a host API.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import pytest

from _corpus import MERGE_CORPUS_ROOT, merge_corpus_required, merge_fixtures
from fuaran_py import decode_node, encode_node
from fuaran_py.canonical import escape_string
from fuaran_py.merge import merge_3way
from fuaran_py.model import Arr, Node, Obj


def _read(rel: str) -> str:
    text = (MERGE_CORPUS_ROOT / rel).read_text(encoding="utf-8")
    return text[:-1] if text.endswith("\n") else text


def _sha256hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _decode_or_raise(rel: str) -> Node:
    result = decode_node(_read(rel))
    assert result.ok, f"decode {rel} failed: {result.error}"
    return result.value


# ─── Phase-184 validator-gated port (test-side sample validator + verdict codec) ──


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
    Inspects the root node only (not recursive), mirroring the F#/TS walker."""
    if tree.kind.tag != "Dashboard":
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


def _introduced_defects(parent_a: Node, parent_b: Node, merged: Node) -> list[MergeDefect]:
    parent_keys = {_identity(d) for d in _gated_validator(parent_a)} | {
        _identity(d) for d in _gated_validator(parent_b)
    }
    introduced = [d for d in _gated_validator(merged) if _identity(d) not in parent_keys]
    return sorted(introduced, key=_order_key)


def _encode_verdict(defects: list[MergeDefect]) -> str:
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


# ─── the gate ─────────────────────────────────────────────────────────────────


@merge_corpus_required
@pytest.mark.parametrize("fixture", merge_fixtures(), ids=lambda fx: fx["id"])
def test_merge_conformance(fixture: dict) -> None:
    base = _decode_or_raise(fixture["baseFile"])
    a = _decode_or_raise(fixture["aFile"])
    b = _decode_or_raise(fixture["bFile"])

    result = merge_3way(base, a, b)
    assert result.ok, f"{fixture['id']}: unexpected conflicts: {result}"

    if fixture["kind"] == "merge-3way":
        merged_bytes = encode_node(result.tree)
        assert merged_bytes == _read(fixture["expectedFile"]), fixture["id"]
        assert _sha256hex(merged_bytes) == fixture["outcomeHash"], fixture["id"]
    else:  # merge-validator-gated
        introduced = _introduced_defects(a, b, result.tree)
        # The fixture exists to introduce a defect — guard against a silent no-op.
        assert len(introduced) > 0, f"{fixture['id']}: expected an introduced defect"
        verdict_bytes = _encode_verdict(introduced)
        assert verdict_bytes == _read(fixture["verdictFile"]), fixture["id"]
        assert _sha256hex(verdict_bytes) == fixture["verdictHash"], fixture["id"]
