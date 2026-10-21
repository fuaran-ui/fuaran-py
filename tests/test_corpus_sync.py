"""Corpus snapshot drift guard (WIRE_FORMAT.md §11 forward-coupling, D4), and the corpus-family gate.

The Python mirror of ``fuaran-ts``'s ``corpus-sync.test.ts``. The committed
snapshot under ``conformance/corpus/`` (written by ``conformance/sync_corpus.py``)
must be byte-identical to the authoritative workspace corpus. When the corpus
regenerates under §11, this fails until ``sync_corpus.py`` is re-run and the
snapshot re-committed — so a corpus advance can never silently leave the Python
host pinned to a stale copy. The payload is read from ``sync_corpus.py`` itself
(Phase 1938): a second list here had already drifted from it, leaving two bundled
families copied and never checked.

The family gate (WIRE_FORMAT §27.7, Phase 1938) holds the capability manifest's
``corpusFamilies`` to the family set derived from the corpus tree: a family the
corpus has and the manifest does not name, or one the manifest names and the
corpus no longer has, fails. It replaced a printer that logged the uncovered
root kinds and asserted nothing.

Skipped when the authority is absent (a standalone ``fuaran-py`` checkout): there
is nothing to drift-check against, and the snapshot is then the corpus the rest
of the suite reads.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from _corpus import AUTHORITY_ROOT, SNAPSHOT_ROOT
from fuaran_ui.conformance.host_capability import CORPUS_FAMILIES, corpus_families

_SYNC = Path(__file__).resolve().parents[1] / "conformance" / "sync_corpus.py"
_MANIFEST = Path(__file__).resolve().parents[1] / "conformance" / "host-capability-manifest.json"


def _sync_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("_sync_corpus", _SYNC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_sync = _sync_module()

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
    for name in _sync._DIRS:
        authority = _rel_files(AUTHORITY_ROOT / name)
        snapshot = _rel_files(SNAPSHOT_ROOT / name)
        assert snapshot == authority, (
            f"{name}/ snapshot file set drifted from the authority "
            f"(missing: {sorted(authority - snapshot)}, extra: {sorted(snapshot - authority)}); "
            "run: python conformance/sync_corpus.py"
        )


@authority_present
def test_snapshot_payload_bytes_identical() -> None:
    def same(rel: str) -> bool:
        snap = SNAPSHOT_ROOT / rel
        return snap.is_file() and snap.read_bytes() == (AUTHORITY_ROOT / rel).read_bytes()

    drifted = [rel for rel in _sync.payload_files(AUTHORITY_ROOT) if not same(rel)]
    assert not drifted, f"snapshot drifted from the authority: {drifted}; run: python conformance/sync_corpus.py"


# ── the corpus families this host certifies, held to the corpus tree (§27.7) ─


@authority_present
def test_the_manifest_names_every_corpus_family_and_no_other() -> None:
    corpus = corpus_families(AUTHORITY_ROOT)
    declared = set(CORPUS_FAMILIES)
    missing, stale = sorted(corpus - declared), sorted(declared - corpus)
    assert not missing, f"corpus families this host's manifest does not name: {missing}"
    assert not stale, f"families the manifest names that the corpus no longer has: {stale}"


def test_the_published_manifest_carries_the_declaration() -> None:
    published = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    assert published["corpusFamilies"] == dict(sorted(CORPUS_FAMILIES.items()))
    for family, answer in CORPUS_FAMILIES.items():
        assert answer == "covered" or (
            isinstance(answer, dict) and len(answer) == 1 and next(iter(answer)) in ("notApplicable", "pending")
        ), f'{family}: an answer is "covered", notApplicable or pending'


@authority_present
def test_the_family_derivation_sees_a_self_enumerated_family(tmp_path: Path) -> None:
    # The falsifier: a family that exists only as a directory with its own manifest — the shape
    # the printer could not see — must join the derived set, and so must a `families` entry.
    (tmp_path / "manifest.json").write_text('{"fixtures":[{"kind":"node-round-trip"}]}', encoding="utf-8")
    (tmp_path / "newfamily").mkdir()
    (tmp_path / "newfamily" / "manifest.json").write_text('{"cases":[]}', encoding="utf-8")
    (tmp_path / "split").mkdir()
    (tmp_path / "split" / "manifest.json").write_text('{"families":[{"id":"a"},{"id":"b"}]}', encoding="utf-8")
    (tmp_path / "goldens").mkdir()
    assert corpus_families(tmp_path) == {"node-round-trip", "newfamily", "split/a", "split/b"}


def test_a_resync_restamps_only_the_manifest_authority(tmp_path: Path) -> None:
    """Phase 2301: the re-sync carries the new authority commit into the capability manifest."""
    (tmp_path / "conformance").mkdir()
    manifest = tmp_path / "conformance" / "host-capability-manifest.json"
    before = '{\n  "host": "x",\n  "corpusAuthority": "' + "a" * 40 + '",\n  "tokens": []\n}\n'
    manifest.write_text(before, encoding="utf-8")
    assert _sync.restamp_manifest_authority(tmp_path, "b" * 40) == manifest
    assert manifest.read_text(encoding="utf-8") == before.replace("a" * 40, "b" * 40)
    _sync.restamp_manifest_authority(tmp_path, None)
    assert '"corpusAuthority": null,' in manifest.read_text(encoding="utf-8")
    assert _sync.restamp_manifest_authority(tmp_path / "absent", "c" * 40) is None
