"""The render-TEXT conformance family — this host's leg (Phase 1663).

Loads the corpus's ``render-text.json`` (the family the manifest's ``renderText``
pointer names) and, for every vector, decodes the named node fixture, finds the
named node, resolves the named text slot under the vector's **PINNED** sources,
and asserts the produced string equals ``expectedText`` byte-for-byte.

**The enumeration is the ARTEFACT'S, never a list beside these checkers.** A
vector added upstream arrives here as a claim this host must meet, and a vector
naming a slot this host has no reader for is REPORTED BY NAME and fails — not
checked is not passed, the posture WIRE_FORMAT.md §13 already takes for a render
obligation. That is the whole mechanism: without it a host could pass by reading
fewer vectors than the corpus declares.

**Why the sources must be pinned, restated because it is the point.** A
``Binding.Now`` slot rendered against the machine's own clock has no expected
text at all, and a ``Format.Since`` rendered against it has one that changes
every second. The instant and the locale are data in the vector, so the
comparison is a function of the corpus and this host alone.

Skipped when the corpus is absent (a standalone checkout), like every other
corpus leg here.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_ui import decode_node
from fuaran_ui.model import Arr, Node, Obj, Value
from fuaran_ui.renderer.bindings import BindingSources, render_text, resolve_locale_tag

#: The family's CLOSED slot vocabulary, as THIS host reads it: the wire kind's
#: tag paired with the field holding the TextSource. A vector naming a slot
#: absent from this map fails the run with the slot named — the artefact's
#: ``slotVocabulary`` is what a reader compares against, and being behind it is a
#: fact this host must report rather than skip.
_SLOT_READERS: dict[str, tuple[str, str]] = {
    "Fact.value": ("Fact", "value"),
    "Markdown.text": ("Markdown", "text"),
}

_ARTEFACT = CORPUS_ROOT / "render-text.json"


def _family() -> dict:
    return json.loads(_ARTEFACT.read_text(encoding="utf-8"))


def _vectors() -> list[dict]:
    if not _ARTEFACT.is_file():
        return []
    return list(_family()["vectors"])


def _find_node(node: Value, node_id: str) -> Node | None:
    """The node with ``node_id`` anywhere in a decoded tree.

    A generic structural walk: this host's decoded tree is ``Obj`` / ``Arr`` all
    the way down, so it descends every container without naming one — which is
    what keeps the family extensible here without an edit.
    """
    if isinstance(node, Node):
        if node.id == node_id:
            return node
        found = _find_node(node.kind, node_id)
        if found is not None:
            return found
        for value in node.extras.values():
            found = _find_node(value, node_id)
            if found is not None:
                return found
        return None
    if isinstance(node, Obj):
        for value in node.fields.values():
            found = _find_node(value, node_id)
            if found is not None:
                return found
        return None
    if isinstance(node, Arr):
        for value in node.items:
            found = _find_node(value, node_id)
            if found is not None:
                return found
    return None


def _text_slot(node: Node, slot: str) -> Value:
    reader = _SLOT_READERS.get(slot)
    assert reader is not None, (
        f"slot '{slot}' is declared by the corpus's render-text family and this host has no reader "
        f"for it — the host is BEHIND the artefact and must add one (not checked is not passed)"
    )
    kind, field = reader
    assert node.kind.tag == kind, f"vector names slot '{slot}' on node '{node.id}', whose kind is {node.kind.tag!r}"
    return node.kind.fields.get(field)


def _render(vector: dict) -> str:
    fixture = (CORPUS_ROOT / vector["fixture"]).read_text(encoding="utf-8")
    decoded = decode_node(fixture)
    assert decoded.ok, f"fixture {vector['fixture']} did not decode: {decoded}"
    target = _find_node(decoded.value, vector["nodeId"])
    assert target is not None, f"fixture {vector['fixture']} carries no node with id {vector['nodeId']!r}"
    pinned = vector["sources"]
    sources = BindingSources(values=dict(pinned["values"]), now=pinned["now"], locale=pinned["locale"])
    return render_text(_text_slot(target, vector["slot"]), sources)


@corpus_required
@pytest.mark.skipif(not _ARTEFACT.is_file(), reason="render-text.json not in the corpus")
@pytest.mark.parametrize("vector", _vectors(), ids=lambda v: v["id"])
def test_render_text_vector(vector: dict) -> None:
    assert _render(vector) == vector["expectedText"], f"render-text vector {vector['id']}: {vector['description']}"


@corpus_required
@pytest.mark.skipif(not _ARTEFACT.is_file(), reason="render-text.json not in the corpus")
def test_the_family_is_not_empty_and_pins_both_arms() -> None:
    """Non-vacuity, derived from the artefact rather than asserted about it.

    The parametrised test above passes trivially over an empty vector list, and
    an artefact that lost the two arms this phase exists for would still pass it.
    """
    vectors = _vectors()
    assert vectors, "render-text.json declares no vectors"
    assert any(v["id"].startswith("now-") for v in vectors), "no Binding.Now vector"
    assert any(v["id"].startswith("since-") for v in vectors), "no Format.Since vector"
    assert any(v["sources"]["now"] == "" and v["expectedText"] == "" for v in vectors), (
        "no no-host-clock vector — the family does not pin what an unresolvable instant renders"
    )


@corpus_required
@pytest.mark.skipif(not _ARTEFACT.is_file(), reason="render-text.json not in the corpus")
def test_a_perturbed_expectation_fails() -> None:
    """The go-red property, proven against the same comparison the gate uses.

    The shape a divergence takes on the day it lands. Without it, a reader cannot
    tell a host that renders every vector correctly from one whose renderer
    returns the corpus's own strings.
    """
    vector = _vectors()[0]
    assert _render(vector) != vector["expectedText"] + "!"


@corpus_required
@pytest.mark.skipif(not _ARTEFACT.is_file(), reason="render-text.json not in the corpus")
def test_this_host_reads_every_declared_slot() -> None:
    """A slot the artefact declares and this host cannot read is a REPORTED gap.

    Measured against ``slotVocabulary`` rather than against the vectors, because
    the vocabulary is what a future vector will draw from: a slot declared today
    and vectored tomorrow should redden this host today, while it is cheap.
    """
    declared = {entry["slot"] for entry in _family()["slotVocabulary"]}
    missing = sorted(declared - set(_SLOT_READERS))
    assert not missing, f"the corpus declares text slots this host has no reader for: {missing}"


@corpus_required
@pytest.mark.skipif(not _ARTEFACT.is_file(), reason="render-text.json not in the corpus")
def test_excluded_slots_are_not_rendered_by_this_host() -> None:
    """The exclusions are a claim about THIS host too, so it is checked.

    Every slot the family excludes is one whose text comes from a locale
    database; this host renders none of them, and a future change that started
    rendering one — inventing a locale answer rather than declaring absence —
    would pass every vector above while breaking the family's premise.
    """
    for entry in _family()["excluded"]:
        fixture = (CORPUS_ROOT / entry["fixture"]).read_text(encoding="utf-8")
        decoded = decode_node(fixture)
        assert decoded.ok
        target = _find_node(decoded.value, entry["nodeId"])
        assert target is not None, f"excluded slot {entry['slot']} names a node that is not in its fixture"
        text = target.kind.fields.get("text")
        assert render_text(text, BindingSources(now="2026-08-02T06:59:24Z")) == "", (
            f"this host rendered the excluded slot {entry['slot']} — {entry['reason']}"
        )


@corpus_required
def test_ambient_locale_resolves_through_the_seam() -> None:
    """``LocaleSource`` precedence is the seam's rule, not each caller's.

    ``Explicit`` pins its own tag whatever the host furnishes; ``Ambient`` reads
    the host's ``locale``, whose ``""`` default means the runtime default. This
    is the member of the widened record the locale-independent renderings above
    deliberately do not consult, so it is exercised here or nowhere.
    """
    fixture = (CORPUS_ROOT / "nodes" / "format-since.json").read_text(encoding="utf-8")
    decoded = decode_node(fixture)
    assert decoded.ok
    ambient = _find_node(decoded.value, "since-auto")
    explicit = _find_node(decoded.value, "since-declared-hour")
    assert ambient is not None and explicit is not None
    host = BindingSources(locale="fr-FR")
    assert resolve_locale_tag(ambient.kind.fields["text"].fields["binding"], host) == "fr-FR"
    assert resolve_locale_tag(explicit.kind.fields["text"].fields["binding"], host) == "en-GB"
    assert resolve_locale_tag(ambient.kind.fields["text"].fields["binding"], None) == ""
