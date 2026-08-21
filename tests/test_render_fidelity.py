"""The Python host reads the GENERATED render-fidelity manifest (WIRE_FORMAT.md §13).

It never carries a copy of it, and that is what these tests exist to hold. Hard-coding
the tier postures here would be easy and would stay green forever while drifting from
the F# declaration the artefact is generated from — the exact second-source-of-truth
defect the artefact was introduced to remove. So every assertion reads the artefact,
and the vocabulary leg measures this host's own ``KNOWN_KINDS`` against it in BOTH
directions, the same shape as the Phase 548 kind-set attestation in ``test_kind_set``.

Skipped when the corpus is absent. Note the artefact is a corpus-root file that the
snapshot sync does not copy (that script mirrors the certification payload set), so on
a standalone checkout these are skipped rather than run against a stale copy.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT
from fuaran_py.render_fidelity import (
    RenderFidelityError,
    delivered_tier,
    fidelity_badge,
    fidelity_of,
    load_manifest,
    parse_manifest,
)
from fuaran_py.schema.decode import KNOWN_KINDS

# The legacy decode-upgrade tags: recognised on decode, never emitted as a canonical
# ``kind.$type``, so they are absent from the fidelity manifest for the same reason
# they are absent from the corpus ``kinds`` enumeration. Mirrors ``test_kind_set``.
LEGACY_KINDS = frozenset({"Dashboard", "Stack", "GridLayout", "Card", "Table"})

ARTIFACT = CORPUS_ROOT / "render-fidelity.json"

artifact_required = pytest.mark.skipif(
    not ARTIFACT.is_file(),
    reason=f"render-fidelity.json not found at {ARTIFACT}",
)


@artifact_required
def test_manifest_parses_and_pins_the_v1_identity() -> None:
    manifest = load_manifest(CORPUS_ROOT)
    assert manifest.version == 1
    assert manifest.id == "https://fuaran.dev/wire-format/v1/render-fidelity.json"
    assert [tier for tier, _ in manifest.tiers] == ["source", "fallback", "rich"]
    assert manifest.kinds


@artifact_required
def test_every_kind_this_host_emits_has_a_posture() -> None:
    manifest = load_manifest(CORPUS_ROOT)
    declared = {row.kind for row in manifest.kinds}
    canonical = set(KNOWN_KINDS) - LEGACY_KINDS

    missing = sorted(canonical - declared)
    extra = sorted(declared - canonical)
    assert not missing, f"kinds this host emits with no render-fidelity row: {missing}"
    assert not extra, f"fidelity rows for kinds this host does not emit: {extra}"


@artifact_required
def test_badge_derives_three_segments_for_every_kind() -> None:
    manifest = load_manifest(CORPUS_ROOT)
    for row in manifest.kinds:
        badge = fidelity_badge(row)
        assert [segment.tier for segment in badge] == ["source", "fallback", "rich"]
        assert all(segment.detail for segment in badge)
        assert badge[2].present is (row.rich.cls != "none")


@artifact_required
def test_the_shipped_fidelity_contracts_are_represented() -> None:
    manifest = load_manifest(CORPUS_ROOT)

    for kind in ("Modal", "Toast", "ScrollArea", "CodeBlock", "Markdown", "Math"):
        row = fidelity_of(manifest, kind)
        assert row is not None, f"{kind} has no fidelity row"
        assert row.sensitive, f"{kind} carries a shipped fidelity contract"
        assert row.fixtures, f"{kind} names no pinning fixture"

    # The rich tiers that change DOM after hydration.
    for kind in ("CodeBlock", "Markdown", "Math"):
        row = fidelity_of(manifest, kind)
        assert row is not None and row.rich.cls == "clientOnly"

    # The overlay contract's enhancement is behaviour, never DOM: a portal would be a
    # DOM change and the contract refuses one.
    modal = fidelity_of(manifest, "Modal")
    assert modal is not None and modal.rich.cls == "behavioural"

    for kind in ("ScrollArea", "Toast"):
        row = fidelity_of(manifest, kind)
        assert row is not None and row.rich.cls == "none"


@artifact_required
def test_every_named_fixture_resolves_in_the_corpus() -> None:
    manifest = load_manifest(CORPUS_ROOT)
    dangling = [
        f"{row.kind}: {fixture}"
        for row in manifest.kinds
        for fixture in row.fixtures
        if not (CORPUS_ROOT / fixture).is_file()
    ]
    assert not dangling


@artifact_required
def test_a_headless_host_always_delivers_the_fallback_tier() -> None:
    # This host renders server HTML with no hydration step at all, so the fallback is
    # what it can honestly claim for every kind — which is the useful thing for it to
    # be able to SAY, rather than leaving a reader to infer it.
    manifest = load_manifest(CORPUS_ROOT)
    for row in manifest.kinds:
        assert delivered_tier(row, "no_script") == "fallback"
        expected = "rich" if row.rich.cls == "clientOnly" else "fallback"
        assert delivered_tier(row, "hydrated") == expected


@artifact_required
def test_an_unknown_kind_is_reported_as_unknown() -> None:
    # The §15.3 tolerance path preserves kinds this host does not model. A badge
    # surface must report that rather than assume the fallback is the whole render.
    assert fidelity_of(load_manifest(CORPUS_ROOT), "KindFromANewerProfile") is None


@artifact_required
def test_a_malformed_manifest_is_refused_by_path() -> None:
    # The reader must be able to go red. A row missing its fallback declaration is the
    # realistic defect — a half-written row — and it must name the kind, not degrade to
    # an empty manifest that a badge surface would render as "no fidelity data".
    good = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    broken = dict(good)
    broken["kinds"] = [{k: v for k, v in good["kinds"][0].items() if k != "fallback"}]

    with pytest.raises(RenderFidelityError) as excinfo:
        parse_manifest(broken)
    assert good["kinds"][0]["kind"] in str(excinfo.value)
