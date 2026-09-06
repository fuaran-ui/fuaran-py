"""Every node-valued position in the wire decodes as a NODE.

A container kind whose children reach the structural pass-through decodes them as
untagged generic objects. They round-trip byte-exactly, so the codec looks correct
— and every consumer of the decoded tree is blind to them: ``walk_nodes`` /
``find_node`` / ``inspect_tree`` do not see a node inside one, and ``validate_node``
does not descend, so a duplicate id inside such a container produces no
``FUARAN-DUP-ID``.

**The gate is DERIVED from the corpus, not a list of kinds.** A node envelope is
recognisable in raw JSON — an ``id`` beside a ``kind`` whose ``$type`` is a known
node kind — so the test can count the node positions a fixture actually contains
and require the decoded tree to expose exactly those. A hand-maintained list of
container kinds is what let this class hide in the first place: three kinds were
routed, five were not, and nothing measured the difference.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT, corpus_required, fixtures_of
from fuaran_py import decode_node
from fuaran_py.ai_tools import walk_nodes
from fuaran_py.schema.decode import KNOWN_KINDS
from fuaran_py.validator import validate_node


def _node_ids(value: object) -> list[str]:
    """Every node envelope's id in a raw decoded-JSON value, depth-first.

    A node envelope is ``{"id": <str>, "kind": {"$type": <known kind>, …}}``. That
    shape is the wire's own definition of a node, so reading it back out of the raw
    JSON is independent of anything the decoder believes.
    """
    found: list[str] = []
    if isinstance(value, dict):
        kind = value.get("kind")
        if isinstance(value.get("id"), str) and isinstance(kind, dict) and kind.get("$type") in KNOWN_KINDS:
            found.append(value["id"])
        for item in value.values():
            found.extend(_node_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_node_ids(item))
    return found


def _node_fixtures() -> list[dict]:
    return [fx for fx in fixtures_of("node-round-trip") if fx.get("decoder", "node") == "node"]


@corpus_required
@pytest.mark.parametrize("fixture", _node_fixtures(), ids=lambda fx: fx["id"])
def test_every_node_position_in_the_corpus_is_reachable(fixture: dict) -> None:
    text = (CORPUS_ROOT / fixture["inputFile"]).read_text(encoding="utf-8")
    result = decode_node(text)
    assert result.ok, getattr(result, "error", result)

    expected = sorted(_node_ids(json.loads(text)))
    reachable = sorted(node.id for node in walk_nodes(result.value))
    assert reachable == expected, (
        f"{fixture['id']}: node positions the decoded tree does not expose as nodes — "
        f"missing {sorted(set(expected) - set(reachable))}"
    )


# ── The `Disclosure` instance the finding was measured on ────────────────────

_DISCLOSURE_WITH_A_DUPLICATE = json.dumps(
    {
        "id": "root",
        "kind": {
            "$type": "Disclosure",
            "heading": "Details",
            "open": {"$type": "Static", "value": False},
            "defaultOpen": False,
            "children": [
                {"id": "dup", "kind": {"$type": "Markdown", "text": "first"}},
                {"id": "dup", "kind": {"$type": "Markdown", "text": "second"}},
            ],
        },
    }
)


def test_disclosure_children_are_reachable_by_introspection() -> None:
    result = decode_node(_DISCLOSURE_WITH_A_DUPLICATE)
    assert result.ok, getattr(result, "error", result)
    assert [node.id for node in walk_nodes(result.value)] == ["root", "dup", "dup"]


def test_a_duplicate_id_inside_a_disclosure_is_reported() -> None:
    """The consequence the introspection gap actually has.

    The validator descends the tree the decoder built, so a child that decoded as
    a generic object is a child the duplicate-id check never sees — and the
    finding it exists to raise is silently absent.
    """
    result = decode_node(_DISCLOSURE_WITH_A_DUPLICATE)
    assert result.ok, getattr(result, "error", result)
    codes = [finding.code for finding in validate_node(result.value)]
    assert "FUARAN-DUP-ID" in codes, codes
