"""Cross-host vocabulary attestation (the Python leg) — WIRE_FORMAT.md §11.2.

The host's NodeKind and FormFieldKind sets are GENERATED from ``idl.json`` (Phase 2296), so the two
manifest-mirror pins that lived here — the decoder's kind set against ``manifest.json``'s ``kinds``,
and its control set against ``formFieldKinds`` — were deleted with the hand lists they guarded:
a case the IDL declares cannot be absent from the host any more, and the regeneration drift checks
(``tests/test_generated_structural.py``) are what goes red instead. What remains is the corpus
SWEEP below, which asks a different question — whether the fixtures exercise the vocabulary.
"""

from __future__ import annotations

import json

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_ui.schema.decode import FORM_FIELD_KIND_CASES

# ── Phase 746 — the FormFieldKind (control) vocabulary ────────────────────────
#
# The second discriminator family to gain an executable attestation. ``NodeKind``
# has had one since Phase 548; ``FormFieldKind`` had none in ANY host, which is how
# ``DateRange`` landed in the shared corpus and sat unadopted in four hosts while
# every gate stayed green. WIRE_FORMAT.md §11.2.


def _control_kinds_in(value: object) -> list[str]:
    """Every ``FormFieldKind`` discriminator a payload carries.

    ONE control vocabulary, two wire carriers: a ``Form`` spec's ``fields[]`` and a
    ``Filters`` spec's ``items[]``. Carriers are matched by their PARENT
    discriminator, never by property name — ``DataGrid.columns[].kind.$type`` is a
    ``CellKindErased`` and shares the token ``Text`` with this family, so a
    property-name sweep silently attests the wrong family and reports green.
    """
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        carrier = {"Form": "fields", "Filters": "items"}.get(node.get("$type"))  # type: ignore[arg-type]
        if carrier is not None and isinstance(node.get(carrier), list):
            for entry in node[carrier]:
                if isinstance(entry, dict) and isinstance(entry.get("kind"), dict):
                    tag = entry["kind"].get("$type")
                    if isinstance(tag, str):
                        found.append(tag)
        for child in node.values():
            walk(child)

    walk(value)
    return found


@corpus_required
def test_corpus_control_kinds_are_declared() -> None:
    """The sweep: every control discriminator the corpus actually carries is one
    this host declares, and one the manifest enumeration lists."""
    manifest = json.loads((CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    manifest_kinds = set(manifest.get("formFieldKinds", []))

    seen: set[str] = set()
    for fixture in manifest["fixtures"]:
        if fixture["kind"] != "node-round-trip":
            continue
        payload = json.loads((CORPUS_ROOT / fixture["inputFile"]).read_text(encoding="utf-8"))
        seen.update(_control_kinds_in(payload))

    assert seen, "the node corpus carries no Form/Filters control at all — the sweep is blind"

    undeclared = sorted(seen - FORM_FIELD_KIND_CASES)
    assert not undeclared, f"corpus control kinds the Python decoder does not declare: {undeclared}"

    unlisted = sorted(seen - manifest_kinds)
    assert not unlisted, f"control kinds the corpus carries but manifest.formFieldKinds omits: {unlisted}"

    # The derivable direction, expressed over the sweep rather than the manifest:
    # a declared kind no fixture exercises is a vocabulary entry nothing certifies.
    # (Python's ``_dispatch`` gates on ``FORM_FIELD_KIND_CASES`` itself, so an
    # "is it accepted?" probe would be tautological here — this is the honest
    # substitute for the F#/TS acceptance leg.)
    unexercised = sorted(FORM_FIELD_KIND_CASES - seen)
    assert not unexercised, f"declared control kinds no corpus fixture exercises: {unexercised}"
