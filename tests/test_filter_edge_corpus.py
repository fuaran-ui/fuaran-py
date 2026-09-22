"""FUARAN075 — the dangling-filter-reference rule, over the corpus's two negative pairs.

Four fixtures, two pairs, one variable each: whether the document's ``Filters``
node declares the second chip its consumer's declared edge reads.

* ``filters-param-source-{declared,undeclared}`` (fuaran#1784) — the arm where
  the edge is a ``Transform`` param whose ``from`` is a chip.
* ``filters-dependson-{declared,undeclared}`` (fuaran#1800) — the arm where the
  edge is a ``Query``'s ``dependsOn`` entry.

All four are legal wire and round-trip byte-identically, so the node-round-trip
family certifies the codec and says nothing about any of this. The divergence is
in what a host DOES with a document, and here that is ``validate_node``.

Until fuaran#1800 this host had no such rule, and the consequence was not a
missing warning but a silent wrong answer: an undeclared chip resolves to
nothing exactly as an UNSET chip does, the lenient "unset ⇒ no constraint" prune
drops the dependent step, and the consumer renders the UNFILTERED set for a
document whose author asked for a filter.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_ui import decode_node, validate_node
from fuaran_ui.model import Arr, Node, Obj

#: ``(stem, reader node id)`` — the two arms, asserted identically.
_ARMS = [
    ("filters-param-source", "scoped-grid"),
    ("filters-dependson", "scoped-metric"),
]


def _decode(name: str) -> Node:
    """Decode a corpus fixture, FAILING rather than skipping on a broken decode.

    An assertion suite that silently drops the document it is about certifies
    nothing.
    """
    raw = (CORPUS_ROOT / "nodes" / f"{name}.json").read_text(encoding="utf-8")
    result = decode_node(raw)
    assert result.ok, f"{name} failed to decode: {result}"
    return result.value


def _dangling(name: str) -> list[tuple[str, str]]:
    """``FUARAN075`` findings as ``(reader node id, filter name)``."""
    out: list[tuple[str, str]] = []
    for finding in validate_node(_decode(name)):
        if finding.code == "FUARAN075":
            # The reader and the name are what the message states; recover them
            # from it rather than re-deriving, so a message that stopped naming
            # either would fail here too.
            parts = finding.message.split("'")
            out.append((parts[1], parts[3]))
    return sorted(out)


@corpus_required
@pytest.mark.parametrize(("stem", "reader"), _ARMS)
def test_control_is_clean_outright(stem: str, reader: str) -> None:
    """A control carrying some other defect would make the negative's one finding hard to attribute."""
    assert validate_node(_decode(f"{stem}-declared")) == []


@corpus_required
@pytest.mark.parametrize(("stem", "reader"), _ARMS)
def test_negative_names_the_undeclared_chip_and_nothing_else(stem: str, reader: str) -> None:
    findings = validate_node(_decode(f"{stem}-undeclared"))
    assert [f.code for f in findings] == ["FUARAN075"], findings
    assert _dangling(f"{stem}-undeclared") == [(reader, "genre")]


@corpus_required
@pytest.mark.parametrize(("stem", "reader"), _ARMS)
def test_the_pair_isolates_one_variable(stem: str, reader: str) -> None:
    """The vacuity guard.

    If the two fixtures had drifted apart in some second respect the assertions
    above would still pass while the pair had stopped measuring what it claims
    to. The control declares strictly more chips than the negative, and both
    carry the same reader.
    """

    def chip_names(name: str) -> list[str]:
        tree = _decode(name)
        children = tree.kind.fields["children"]
        assert isinstance(children, Arr)
        chips = next(c for c in children.items if isinstance(c, Node) and c.id == "edge-chips")
        items = chips.kind.fields["items"]
        assert isinstance(items, Arr)
        return [i.fields["name"] for i in items.items if isinstance(i, Obj)]

    def reader_ids(name: str) -> list[str]:
        tree = _decode(name)
        children = tree.kind.fields["children"]
        assert isinstance(children, Arr)
        return [c.id for c in children.items if isinstance(c, Node)]

    assert chip_names(f"{stem}-declared") == ["region", "genre"]
    assert chip_names(f"{stem}-undeclared") == ["region"]
    assert reader_ids(f"{stem}-declared") == reader_ids(f"{stem}-undeclared") == ["edge-chips", reader]


@corpus_required
def test_the_manifest_carries_both_pairs() -> None:
    """The fixtures are the corpus's, not this suite's — a rename here must go red."""
    manifest = json.loads((CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    ids = {fx["id"] for fx in manifest["fixtures"]}
    for stem, _ in _ARMS:
        assert f"{stem}-declared" in ids
        assert f"{stem}-undeclared" in ids


def test_a_chip_self_read_is_a_declaration_not_a_dangling_edge() -> None:
    """Go-red, hand-built and corpus-free.

    Every chip reads its own ``$filters.<name>`` slot. If a plain ``Filter``
    binding counted as a declared EDGE, every control above would report its own
    chips and the assertions would be passing for the wrong reason.
    """
    chip = Obj(
        None,
        {
            "name": "region",
            "label": "Region",
            "kind": Obj("Text", {"value": Obj("Filter", {"name": "region"})}),
        },
    )
    tree = Node("root", Obj("Filters", {"items": Arr([chip])}))
    assert [f.code for f in validate_node(tree)] == []


def test_a_query_dependson_with_no_filters_node_at_all_is_still_the_finding() -> None:
    """The rule quantifies over the TREE's declarations, not over the presence of a chip node.

    A consumer in a document that declares no chips anywhere is the degenerate
    case, and it is a dangling edge for the same reason: nothing can ever write
    the slot it waits on.
    """
    tree = Node(
        "lonely-metric",
        Obj("Metric", {"label": "Revenue", "value": Obj("Query", {"name": "orders", "dependsOn": Arr(["region"])})}),
    )
    assert [f.code for f in validate_node(tree)] == ["FUARAN075"]
