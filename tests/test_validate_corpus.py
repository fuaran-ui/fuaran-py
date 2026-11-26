"""The corpus ``validate`` family (WIRE_FORMAT §27.8.2, Phase 2381): which pre-emit defects a document carries.

Each vector is a canonical document and the defects the reference validator reports on it. This host decodes the
tree, validates it, and asserts that its findings, restricted to the codes its ``validator-coverage.json`` declares
``implemented``, are exactly the vector's defects restricted to the same codes.

A finding of this host carries a code and a ``$``-rooted path, and no node id and no severity, so each one is compared
by CODE, NODE and PATH: its path must address the document in the §29.3 grammar and extend the stated node's path at a
segment boundary (the reject leg's own ``path_matches``), and the deepest node object that path passes through must be
the stated node, so a finding below the node (its ``stateKey``, its ``id``) is still located at that node and never at
another one under it. What the leg does NOT compare — the severity among it — is stated in ``STABILITY.md``
("What the ``validate`` leg compares").
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

# The §29.3 path grammar: `[digits]` is an index; anything else is a name — `.` and then everything up to the next `.`
# or `[`, however the key is spelled (`.aria-hidden`, `.On time`, `.404`). There is no quoted form: a bracketed run
# that is not all digits reads as a name, bracket included, and so names no member of the document.
_SEGMENT = re.compile(r"\[([0-9]+)\]|\.([^.\[]*)|(\[[^.\[]*)")


def _vectors() -> list[dict[str, Any]]:
    manifest = CORPUS_ROOT / "validate" / "manifest.json"
    if not manifest.is_file():
        return []
    vectors: list[dict[str, Any]] = json.loads(manifest.read_text(encoding="utf-8"))["vectors"]
    return vectors


def _is_node(value: object) -> bool:
    return isinstance(value, dict) and isinstance(value.get("id"), str) and isinstance(value.get("kind"), dict)


def _located_node(tree: object, path: str) -> tuple[str, str | None]:
    """The path of the deepest node object ``path`` passes through, and that node's id.

    Refused (``AssertionError``) when a segment other than the last names nothing in the document: a name on an array,
    an index on an object, a member the object does not hold. The last may name an absent member (a missing field).
    """
    assert path.startswith("$"), f"{path!r} is not $-rooted"
    at, node_path, node_id = tree, "$", tree["id"] if _is_node(tree) else None
    consumed, rest = "$", path[1:]
    while rest:
        step = _SEGMENT.match(rest)
        assert step is not None, f"{path!r} is not a section 29.3 path (stuck at {rest!r})"
        index, member, _bracketed = step.groups()
        if index is not None:
            at = at[int(index)] if isinstance(at, list) and int(index) < len(at) else None
        elif member is not None:
            at = at.get(member) if isinstance(at, dict) else None
        else:
            at = None
        consumed, rest = consumed + step.group(0), rest[step.end() :]
        if at is None:
            assert not rest, f"{path!r} names nothing in the document at {consumed!r}"
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


def test_the_leg_reads_the_section_29_grammar() -> None:
    # The leg's own falsifiers. The spelling this host wrote before Phase 2381 (`children.0`) names a member an array
    # does not have, and the quoted segment §29.3 withdrew names a member no object has; the bracketed index names the
    # node it sits under, and a member is written as its key stands, digits and punctuation included (§6, §29.3).
    tree = json.loads(
        '{"id":"r","accessibility":{"aria-hidden":true},"kind":{"$type":"Box","children":['
        '{"id":"a","kind":{"$type":"Box"}},'
        '{"id":"f","kind":{"$type":"FragmentRef","args":{"my-slot":{"tree":{"id":"s","kind":{"$type":"Box"}}},'
        '"404":{"tree":{"id":"n","kind":{"$type":"Box"}}}}}},'
        '{"id":"m","kind":{"$type":"TonedPill","map":{"On time":"Good"}}}]}}'
    )
    for refused in (
        "$.kind.children.0.kind.stateKey",
        "$.kind.children[1].kind.args['my-slot'].tree.kind",
        "$.kind[0].id",
        "kind",
    ):
        with pytest.raises(AssertionError):
            _located_node(tree, refused)
    assert _located_node(tree, "$.kind.children[0].kind.stateKey") == ("$.kind.children[0]", "a")
    assert _located_node(tree, "$.accessibility.aria-hidden") == ("$", "r")
    assert _located_node(tree, "$.kind.children[1].kind.args.my-slot.tree.kind") == (
        "$.kind.children[1].kind.args.my-slot.tree",
        "s",
    )
    assert _located_node(tree, "$.kind.children[1].kind.args.404.tree.kind.$type") == (
        "$.kind.children[1].kind.args.404.tree",
        "n",
    )
    assert _located_node(tree, "$.kind.children[2].kind.map.On time") == ("$.kind.children[2]", "m")
    assert _located_node(tree, "$.kind.children[2].kind.absentField") == ("$.kind.children[2]", "m")
    assert path_matches("$.kind.columns[2].kind.map.404", "$.kind.columns[2].kind.map") is None
