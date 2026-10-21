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
import os
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


# ── the pre-tag check: --check --remote (Phase 2225) ─────────────────────────
#
# `--check` measures the snapshot against the authority clone's HEAD, and a clone's HEAD is
# whatever it last fetched. The 0.9.0 tag was cut on a snapshot that matched the maintainer's
# clone and was behind the authority's `main` by one commit: the local check was green, and the
# publish job - which checks the authority out fresh - refused. `--remote` asks the authority's
# own remote what `main` is NOW (`git ls-remote`, which writes nothing, so a read-only clone is
# fine), so the question a tag must answer is the one that gets asked.


@pytest.fixture
def published(authority: Path, tmp_path: Path) -> tuple[Path, Path]:
    """``authority`` with an ``origin`` it has pushed to, and a second clone that can move it."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(authority, "remote", "add", "origin", str(origin))
    _git(authority, "push", "-q", "origin", "main")
    other = tmp_path / "other"
    _git(tmp_path, "clone", "-q", str(origin), str(other))
    _git(other, "config", "user.email", "tests@example.invalid")
    _git(other, "config", "user.name", "fuaran-py tests")
    return origin, other


@git_available
def test_remote_check_is_green_when_the_snapshot_records_the_remote_main(
    authority: Path, sentinel: Path, published: tuple[Path, Path]
) -> None:
    sync_corpus.write_sentinel(authority, sentinel)
    assert sync_corpus.check(authority, remote=True) == 0


@git_available
def test_remote_check_refuses_a_snapshot_the_local_clone_cannot_see_is_behind(
    authority: Path, sentinel: Path, published: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The 0.9.0 case: the snapshot equals the clone's HEAD, and the remote has moved on."""
    _, other = published
    snapshot_sha = sync_corpus.write_sentinel(authority, sentinel)
    remote_tip = _commit(other, "second.json", "{}\n")
    _git(other, "push", "-q", "origin", "main")

    assert sync_corpus.check(authority) == 0, "the local check is blind to the moved remote - that is the trap"
    capsys.readouterr()

    assert sync_corpus.check(authority, remote=True) == 2
    reported = capsys.readouterr().err
    assert f"authority {remote_tip}" in reported
    assert f"snapshot {snapshot_sha}" in reported
    assert "origin/main" in reported
    # The clone never fetched the tip, so the distance is not invented - and the way to get it is named.
    assert "an unknown number of commits" in reported
    assert "git -C" in reported and "fetch" in reported


@git_available
def test_remote_check_reports_the_distance_once_the_clone_has_fetched(
    authority: Path, sentinel: Path, published: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    _, other = published
    sync_corpus.write_sentinel(authority, sentinel)
    _commit(other, "second.json", "{}\n")
    _git(other, "push", "-q", "origin", "main")
    _git(authority, "fetch", "-q", "origin")

    assert sync_corpus.check(authority, remote=True) == 2
    assert "snapshot behind authority by 1 commit " in capsys.readouterr().err


@git_available
def test_remote_check_cannot_measure_an_unreachable_remote(
    authority: Path, sentinel: Path, published: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Offline is UNMEASURED (1), never a pass: a pre-tag check that could not ask must not let a tag through."""
    sync_corpus.write_sentinel(authority, sentinel)
    _git(authority, "remote", "set-url", "origin", str(tmp_path / "no-such-remote.git"))

    assert sync_corpus.check(authority, remote=True) == 1
    assert "UNMEASURED" in capsys.readouterr().err


@git_available
def test_remote_check_cannot_measure_a_clone_with_no_origin(
    authority: Path, sentinel: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sync_corpus.write_sentinel(authority, sentinel)
    assert sync_corpus.check(authority, remote=True) == 1
    assert "UNMEASURED" in capsys.readouterr().err


# ── the script operates on the checkout it is run in (Phase 2225) ────────────

_SCRIPT_IN_REPO = Path("conformance") / "sync_corpus.py"
_RESOLVER_IN_REPO = Path("src") / "fuaran_ui" / "conformance" / "corpus_root.py"


def _probe_checkout(root: Path) -> Path:
    """A throwaway checkout holding the sync script and the resolver it loads."""
    for rel in (_SCRIPT_IN_REPO, _RESOLVER_IN_REPO):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_REPO_ROOT / rel, root / rel)
    return root


def _script_env(corpus: Path | None) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "FUARAN_WIRE_FIXTURES"}
    if corpus is not None:
        env["FUARAN_WIRE_FIXTURES"] = str(corpus)
    return env


def _run_script(script: Path, cwd: Path, corpus: Path | None, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=_script_env(corpus),
        check=False,
    )


def _bundled(root: Path) -> Path:
    return root / "conformance" / "corpus" / "manifest.json"


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """A complete corpus to sync from: the bundled snapshot, minus its own sentinel."""
    root = tmp_path / "authority-corpus"
    shutil.copytree(_REPO_ROOT / "conformance" / "corpus", root)
    (root / "snapshot.json").unlink()
    return root


def test_sync_writes_the_checkout_the_caller_stands_in_not_the_one_holding_the_script(
    tmp_path: Path, corpus: Path
) -> None:
    holding = _probe_checkout(tmp_path / "holding")
    standing_in = _probe_checkout(tmp_path / "standing-in")

    done = _run_script(holding / _SCRIPT_IN_REPO, standing_in, corpus)

    assert done.returncode == 0, done.stderr
    assert _bundled(standing_in).is_file()
    assert not _bundled(holding).exists(), "the checkout holding the script was written to"


def test_an_explicit_repo_wins_over_the_directory_the_caller_stands_in(tmp_path: Path, corpus: Path) -> None:
    holding = _probe_checkout(tmp_path / "holding")
    named = _probe_checkout(tmp_path / "named")
    standing_in = _probe_checkout(tmp_path / "standing-in")

    done = _run_script(holding / _SCRIPT_IN_REPO, standing_in, corpus, "--repo", str(named))

    assert done.returncode == 0, done.stderr
    assert _bundled(named).is_file()
    assert not _bundled(standing_in).exists()
    assert not _bundled(holding).exists()


def test_run_from_outside_any_checkout_falls_back_to_the_checkout_holding_the_script(
    tmp_path: Path, corpus: Path
) -> None:
    holding = _probe_checkout(tmp_path / "holding")
    outside = tmp_path / "outside"
    outside.mkdir()

    done = _run_script(holding / _SCRIPT_IN_REPO, outside, corpus)

    assert done.returncode == 0, done.stderr
    assert _bundled(holding).is_file()


def test_an_explicit_repo_that_is_not_a_checkout_is_refused(tmp_path: Path, corpus: Path) -> None:
    holding = _probe_checkout(tmp_path / "holding")

    done = _run_script(holding / _SCRIPT_IN_REPO, tmp_path, corpus, "--repo", str(tmp_path))

    assert done.returncode == 1
    assert "not a fuaran-py checkout" in done.stderr
    assert not _bundled(holding).exists()


def test_the_sibling_authority_is_looked_for_beside_the_checkout_being_operated_on(tmp_path: Path) -> None:
    """With no corpus named, the fallback is the clone beside the CHECKOUT, not beside the script."""
    holding = _probe_checkout(tmp_path / "ws-a" / "holding")
    standing_in = _probe_checkout(tmp_path / "ws-b" / "standing-in")

    done = _run_script(holding / _SCRIPT_IN_REPO, standing_in, None, "--check")

    assert done.returncode == 1
    assert str(tmp_path / "ws-b") in done.stderr and str(tmp_path / "ws-a") not in done.stderr
