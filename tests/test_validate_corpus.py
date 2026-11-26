"""The corpus ``validate`` family (WIRE_FORMAT §27.8.2, Phase 2381): which pre-emit defects a document carries.

Each vector is a canonical document and the defects the reference validator reports on it. This host decodes the
tree, validates it, and asserts that its findings, restricted to the codes its ``validator-coverage.json`` declares
``implemented``, are exactly the vector's defects restricted to the same codes.

A finding of this host carries a ``$``-rooted path and no node id, so each one is compared by CODE, NODE and PATH:
its path must be well formed and extend the stated node's path at a segment boundary (the reject leg's own
``path_matches``), and the deepest node object that path passes through must be the stated node, so a finding below
the node (its ``stateKey``, its ``id``) is still located at that node and never at another one under it.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_ui import decode_node, validate_node
from fuaran_ui.conformance.harness import path_matches

_REPO = Path(__file__).resolve().parents[1]
_IMPLEMENTED = frozenset(json.loads((_REPO / "validator-coverage.json").read_text(encoding="utf-8"))["implemented"])

# The §29.3 path grammar: `.name` members, `['…']` for a key that is not a plain identifier, `[i]` for an element.
_SEGMENT = re.compile(r"\.([A-Za-z_$][A-Za-z0-9_$]*)|\['((?:[^'\\]|\\.)*)'\]|\[([0-9]+)\]")


def _vectors() -> list[dict[str, Any]]:
    manifest = CORPUS_ROOT / "validate" / "manifest.json"
    if not manifest.is_file():
        return []
    vectors: list[dict[str, Any]] = json.loads(manifest.read_text(encoding="utf-8"))["vectors"]
    return vectors


def _is_node(value: object) -> bool:
    return isinstance(value, dict) and isinstance(value.get("id"), str) and isinstance(value.get("kind"), dict)


def _located_node(tree: object, path: str) -> tuple[str, str | None]:
    """The path of the deepest node object ``path`` passes through, and that node's id."""
    assert path.startswith("$"), f"{path!r} is not $-rooted"
    at, node_path, node_id = tree, "$", tree["id"] if _is_node(tree) else None
    consumed, rest = "$", path[1:]
    while rest:
        step = _SEGMENT.match(rest)
        assert step is not None, f"{path!r} is not a section 29.3 path (stuck at {rest!r})"
        member, quoted, index = step.groups()
        if index is not None:
            at = at[int(index)] if isinstance(at, list) and int(index) < len(at) else None
        else:
            key = member if member is not None else quoted
            at = at.get(key) if isinstance(at, dict) else None
        consumed, rest = consumed + step.group(0), rest[step.end() :]
        if at is None:
            break
        if _is_node(at):
            node_path, node_id = consumed, at["id"]
    return node_path, node_id


def test_the_family_travels_with_this_host() -> None:
    # The snapshot carries validate/ (conformance/sync_corpus.py), so the leg never goes green on no vectors.
    assert len(_vectors()) >= 6


@corpus_required
@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: v["id"])
def test_validate_vector(vector: dict[str, Any]) -> None:
    decoded = decode_node(vector["tree"])
    assert decoded.ok, f"{vector['id']}: the tree does not decode: {decoded}"
    tree = json.loads(vector["tree"])

    expected = Counter((d["code"], d["node"], d["path"]) for d in vector["expected"] if d["code"] in _IMPLEMENTED)
    actual: Counter[tuple[str, str | None, str]] = Counter()
    for finding in validate_node(decoded.value):
        if finding.code not in _IMPLEMENTED:
            continue
        node_path, node_id = _located_node(tree, finding.path)
        why = path_matches(finding.path, node_path)
        assert why is None, f"{vector['id']}: {finding.code}: {why}"
        actual[(finding.code, node_id, node_path)] += 1

    assert actual == expected, f"{vector['id']}: findings {sorted(actual.elements())} != {sorted(expected.elements())}"


def test_a_finding_at_a_dotted_index_is_refused() -> None:
    # The leg's own falsifier: the spelling this host wrote before Phase 2381 is not a section 29.3 path.
    tree = json.loads('{"id":"r","kind":{"$type":"Box","children":[{"id":"a","kind":{"$type":"Box"}}]}}')
    with pytest.raises(AssertionError):
        _located_node(tree, "$.kind.children.0.kind.stateKey")
    assert _located_node(tree, "$.kind.children[0].kind.stateKey") == ("$.kind.children[0]", "a")
