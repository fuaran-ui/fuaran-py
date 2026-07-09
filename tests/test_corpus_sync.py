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

from pathlib import Path

import pytest

from _corpus import AUTHORITY_ROOT, SNAPSHOT_ROOT

# The families the snapshot pins — must match conformance/sync_corpus.py.
_FILES = ("manifest.json", "schema.json")
_DIRS = ("nodes", "ops", "reject", "lenient", "envelope")

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
