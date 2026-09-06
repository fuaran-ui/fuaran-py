"""Corpus snapshot drift guard (WIRE_FORMAT.md §11 forward-coupling, D4).

The Python mirror of ``fuaran-ts``'s ``corpus-sync.test.ts``. The committed
snapshot under ``conformance/corpus/`` (written by ``conformance/sync_corpus.py``)
must be byte-identical to the authoritative workspace corpus. When the corpus
regenerates under §11, this fails until ``sync_corpus.py`` is re-run and the
snapshot re-committed — so a corpus advance can never silently leave the Python
host pinned to a stale copy.

Skipped when the authority is absent (a standalone ``fuaran-py`` checkout): there
is nothing to drift-check against, and the snapshot is then the corpus the rest
of the suite reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _corpus import AUTHORITY_ROOT, SNAPSHOT_ROOT

# The families the snapshot pins — must match conformance/sync_corpus.py.
_FILES = ("manifest.json", "schema.json", "render-fidelity.json")
_DIRS = ("nodes", "ops", "reject", "lenient", "envelope", "elicitation", "markdown")

authority_present = pytest.mark.skipif(
    not (AUTHORITY_ROOT / "manifest.json").is_file(),
    reason=f"authoritative corpus not found at {AUTHORITY_ROOT} — snapshot is the corpus",
)


def _rel_files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


@authority_present
def test_snapshot_exists() -> None:
    assert (SNAPSHOT_ROOT / "manifest.json").is_file(), (
        f"corpus snapshot missing at {SNAPSHOT_ROOT}; run: python conformance/sync_corpus.py"
    )


@authority_present
def test_snapshot_file_set_matches_authority() -> None:
    for name in _DIRS:
        authority = _rel_files(AUTHORITY_ROOT / name)
        snapshot = _rel_files(SNAPSHOT_ROOT / name)
        assert snapshot == authority, (
            f"{name}/ snapshot file set drifted from the authority "
            f"(missing: {sorted(authority - snapshot)}, extra: {sorted(snapshot - authority)}); "
            "run: python conformance/sync_corpus.py"
        )


@authority_present
@pytest.mark.parametrize("name", _FILES)
def test_snapshot_top_files_byte_identical(name: str) -> None:
    authority = (AUTHORITY_ROOT / name).read_bytes()
    snapshot = (SNAPSHOT_ROOT / name).read_bytes()
    assert snapshot == authority, f"{name} snapshot drifted from the authority; run: python conformance/sync_corpus.py"


@authority_present
@pytest.mark.parametrize("name", _DIRS)
def test_snapshot_family_bytes_identical(name: str) -> None:
    root_a = AUTHORITY_ROOT / name
    for path in sorted(root_a.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root_a)
        snap = SNAPSHOT_ROOT / name / rel
        assert snap.read_bytes() == path.read_bytes(), (
            f"{name}/{rel.as_posix()} snapshot drifted from the authority; run: python conformance/sync_corpus.py"
        )


# ── the families this host does not run, named rather than silently absent ───


_COVERED_FAMILIES = (
    "node-round-trip",
    "op-round-trip",
    "reject",
    "lenient-accept",
    "envelope-round-trip",
    "envelope-reject",
    "elicitation-round-trip",
    "elicitation-reject",
    "elicitation-answer-accept",
    "elicitation-answer-reject",
)


@authority_present
def test_corpus_families_beyond_the_floor_are_named(capsys: pytest.CaptureFixture[str]) -> None:
    """Print, by name and count, every corpus family this host does not run.

    A declared lag and a silent omission are indistinguishable from a green
    suite, and only one of them is honest. The contract-card family is a
    deliberate lag on this host, as it is on the Go and Rust hosts — but a
    reader has no way to tell that from a suite that simply never mentions it.
    This test always passes; its output is the declaration, and the assertion
    below only guards against the list of covered families going stale in the
    other direction (a family named as covered that the corpus no longer holds).
    """
    manifest = json.loads((AUTHORITY_ROOT / "manifest.json").read_text(encoding="utf-8"))
    present: dict[str, int] = {}
    for fixture in manifest["fixtures"]:
        present[fixture["kind"]] = present.get(fixture["kind"], 0) + 1

    with capsys.disabled():
        for kind in sorted(present):
            if kind not in _COVERED_FAMILIES:
                print(f"skipped family (declared lag, not covered by this host): {kind} x {present[kind]}")

    stale = [k for k in _COVERED_FAMILIES if k not in present]
    assert not stale, f"named as covered but absent from the corpus: {stale}"
