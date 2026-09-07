"""The corpus snapshot's provenance sentinel, and the drift check built on it.

``conformance/sync_corpus.py`` records the authority commit each snapshot was
taken from in ``conformance/corpus/snapshot.json``, and ``--check`` measures that
record against the authority clone's HEAD. Both workflows run the check: CI as a
non-blocking WARN on every push, and the publish workflow as a refusal on the
tagged tree, before any test runs.

The check is what stands between a green CI run and a failed release gesture. CI
and the publish workflow each check the corpus out at HEAD, so a snapshot that
matched when CI ran can be behind it by the time a tag is cut — which is how the
first ``v0.1.0`` tag failed its gated ``test_corpus_sync`` and had to be
withdrawn. A guard for that has to be exercised rather than asserted, so every
test here drives the real code against a real throwaway git repository:

* the committed sentinel is well formed;
* ``sync`` writes one, so a re-sync can never quietly drop it;
* ``--check`` returns ``0`` in sync, ``2`` behind (naming the distance and both
  commits), and ``1`` when it cannot measure — the three exits the workflows
  branch on. The ``1`` cases matter as much as the ``2``: a check that reported
  drift it had not measured would be the same vacuous green read backwards.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_sync_corpus():
    """Import ``conformance/sync_corpus.py`` — a script, not a package module."""
    path = _REPO_ROOT / "conformance" / "sync_corpus.py"
    spec = importlib.util.spec_from_file_location("_sync_corpus_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync_corpus = _load_sync_corpus()

git_available = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _commit(repo: Path, name: str, body: str) -> str:
    (repo / name).write_text(body, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", f"add {name}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def authority(tmp_path: Path) -> Path:
    """A throwaway two-commit stand-in for the corpus authority."""
    repo = tmp_path / "wire-format-fixtures"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "fuaran-py tests")
    _commit(repo, "manifest.json", '{"fixtures": []}\n')
    return repo


@pytest.fixture
def sentinel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point the module's sentinel at a scratch path, never the committed one."""
    path = tmp_path / "snapshot.json"
    monkeypatch.setattr(sync_corpus, "SENTINEL", path)
    yield path


# ── the committed artefact ───────────────────────────────────────────────────


def test_committed_sentinel_is_well_formed() -> None:
    payload = json.loads(sync_corpus.SENTINEL.read_text(encoding="utf-8"))
    assert payload["kind"] == sync_corpus.SENTINEL_KIND
    assert re.fullmatch(r"[0-9a-f]{40}", payload["authorityCommit"]), (
        "the committed snapshot must record the 40-character authority commit it was "
        f"synced from; re-sync to record one: {sync_corpus._RESYNC_COMMAND}"
    )


# ── sync writes it ───────────────────────────────────────────────────────────


@git_available
def test_sync_records_the_authority_commit(authority: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A re-sync cannot quietly drop the sentinel — the whole check rests on it."""
    for name in sync_corpus._FILES:
        (authority / name).write_text("{}\n", encoding="utf-8")
    for name in sync_corpus._DIRS:
        (authority / name).mkdir()
        (authority / name / "fixture.json").write_text("{}\n", encoding="utf-8")
    head = _commit(authority, "marker.txt", "payload\n")

    snapshot = tmp_path / "snapshot-root"
    monkeypatch.setattr(sync_corpus, "SNAPSHOT", snapshot)
    monkeypatch.setattr(sync_corpus, "SENTINEL", snapshot / "snapshot.json")

    sync_corpus.sync(authority)

    payload = json.loads((snapshot / "snapshot.json").read_text(encoding="utf-8"))
    assert payload == {"authorityCommit": head, "kind": sync_corpus.SENTINEL_KIND}


# ── --check, one test per exit ───────────────────────────────────────────────


@git_available
def test_check_is_green_when_the_snapshot_records_head(authority: Path, sentinel: Path) -> None:
    head = sync_corpus.write_sentinel(authority, sentinel)
    assert head == _git(authority, "rev-parse", "HEAD")
    assert sync_corpus.check(authority) == 0


@git_available
def test_check_reports_the_distance_when_behind(
    authority: Path, sentinel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = sync_corpus.write_sentinel(authority, sentinel)
    _commit(authority, "second.json", "{}\n")
    head = _commit(authority, "third.json", "{}\n")

    assert sync_corpus.check(authority) == 2

    reported = capsys.readouterr().err
    assert "snapshot behind authority by 2 commits" in reported
    assert f"authority {head}" in reported
    assert f"snapshot {old}" in reported
    assert sync_corpus._RESYNC_COMMAND in reported


@git_available
def test_check_says_one_commit_in_the_singular(
    authority: Path, sentinel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sync_corpus.write_sentinel(authority, sentinel)
    _commit(authority, "second.json", "{}\n")

    assert sync_corpus.check(authority) == 2
    assert "behind authority by 1 commit " in capsys.readouterr().err


@git_available
def test_check_does_not_call_a_rewritten_history_behind(
    authority: Path, sentinel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A commit HEAD does not descend from is not "behind", and calling it that
    would be a guess. It is still a refusal — the two differ, and that difference
    is the fact the release gate acts on."""
    ahead = _commit(authority, "second.json", "{}\n")
    sync_corpus.write_sentinel(authority, sentinel)
    _git(authority, "reset", "-q", "--hard", "HEAD~1")

    assert sync_corpus.check(authority) == 2

    reported = capsys.readouterr().err
    assert "is not behind it" in reported
    assert f"snapshot {ahead}" in reported


@git_available
def test_check_admits_when_the_distance_is_unknown(
    authority: Path, sentinel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A shallow clone holds HEAD but not the recorded commit — the standard
    ``actions/checkout`` default. The distance is then unmeasurable, and the
    check says so rather than inventing a number. Still a refusal: the recorded
    commit is not HEAD, which is all the release gate needs."""
    sentinel.write_text(json.dumps({"authorityCommit": "0" * 40, "kind": sync_corpus.SENTINEL_KIND}), encoding="utf-8")

    assert sync_corpus.check(authority) == 2
    assert "an unknown number of commits" in capsys.readouterr().err


def test_check_cannot_measure_without_an_authority(tmp_path: Path, sentinel: Path) -> None:
    assert sync_corpus.check(tmp_path / "absent") == 1


@git_available
def test_check_cannot_measure_without_a_sentinel(authority: Path, sentinel: Path) -> None:
    assert not sentinel.exists()
    assert sync_corpus.check(authority) == 1


@git_available
def test_check_cannot_measure_a_malformed_sentinel(authority: Path, sentinel: Path) -> None:
    sentinel.write_text('{"authorityCommit": "not-a-sha"}\n', encoding="utf-8")
    assert sync_corpus.check(authority) == 1


def test_check_cannot_measure_a_non_clone(tmp_path: Path, sentinel: Path) -> None:
    """An authority that is a directory of fixtures but not a git clone."""
    plain = tmp_path / "plain-corpus"
    plain.mkdir()
    (plain / "manifest.json").write_text('{"fixtures": []}\n', encoding="utf-8")
    sentinel.write_text(json.dumps({"authorityCommit": "a" * 40, "kind": sync_corpus.SENTINEL_KIND}), encoding="utf-8")
    assert sync_corpus.check(plain) == 1
