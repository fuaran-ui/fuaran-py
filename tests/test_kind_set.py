"""Phase 548 — cross-host kind-set attestation (the Python leg).

The Python host's emittable NodeKind vocabulary (``KNOWN_KINDS`` minus the legacy
decode-upgrade tags) must equal the generated ``wire-format-fixtures`` manifest
``kinds`` enumeration. A vocabulary commit that skips this host fails here with a
*named* missing kind ("Python decoder lacks Drawing"), so the drift class dies at
the host's next test run rather than at a later audit. Skips cleanly on a
standalone checkout (the corpus is absent).
"""

from __future__ import annotations

import json

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py.schema.decode import KNOWN_KINDS

# The legacy decode-upgrade tags: the four retired container kinds (→ Box) and
# ``Table`` (→ DataGrid). They are recognised on decode but never appear as a
# canonical node's ``kind.$type``, so they are absent from the manifest ``kinds``
# enumeration (which is generated from the canonical node round-trip fixtures).
LEGACY_KINDS = frozenset({"Dashboard", "Stack", "GridLayout", "Card", "Table"})


@corpus_required
def test_node_kind_set_matches_manifest() -> None:
    manifest = json.loads((CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    manifest_kinds = set(manifest.get("kinds", []))
    assert manifest_kinds, "manifest.json declares no 'kinds' array — regenerate the corpus with --emit-corpus"

    # The emittable vocabulary: recognised kinds minus the legacy decode-upgrade tags.
    canonical = set(KNOWN_KINDS) - LEGACY_KINDS

    missing = sorted(manifest_kinds - canonical)
    extra = sorted(canonical - manifest_kinds)
    assert not missing, f"manifest kinds the Python decoder lacks (add the decode arm): {missing}"
    assert not extra, f"Python decoder kinds the manifest omits (regenerate with --emit-corpus): {extra}"
