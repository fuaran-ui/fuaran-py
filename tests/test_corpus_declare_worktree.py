"""Phase 1802 — ``sync_corpus.py --declare`` names the REPOSITORY, from a worktree too.

The declare step writes this host's records into the corpus's ``copies.json``, addressing
each copy by its workspace-relative path. It used to derive that path from the snapshot's
absolute location, so a run from a git worktree declared the WORKTREE as the estate copy
and exited 0.

These tests build a throwaway estate under ``tmp_path`` — a fake repository in the
canonical side-by-side layout, a corpus beside it, and a worktree cut from the repository —
and run the real script (copied in) from both checkouts, as a subprocess. Hermetic on
purpose: nothing touches this repository's worktree list or the workspace corpus's
``copies.json``. The worktree is removed (``git worktree remove --force``) even when an
assertion fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "conformance" / "sync_corpus.py"
_SCRIPT_IN_REPO = Path("conformance") / "sync_corpus.py"
_EXPECTED_PREFIX = "Fuaran/Fuaran-UI/fuaran-py/conformance/corpus"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not on PATH")


def _env() -> dict[str, str]:
    """The ambient environment minus anything that would steer git elsewhere."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "FUARAN_WIRE_FIXTURES"}


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-c", "user.name=probe", "-c", "user.email=probe@example.invalid", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )
    assert completed.returncode == 0, f"git {' '.join(args)} failed in {cwd}:\n{completed.stderr}"
    return completed.stdout.strip()


def _declare(checkout: Path, corpus: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(checkout / _SCRIPT_IN_REPO), "--declare", str(corpus)],
        cwd=checkout,
        capture_output=True,
        text=True,
        env=_env(),
        check=False,
    )


def _write_corpus(root: Path) -> None:
    """A minimal corpus plus a copies.json carrying another producer's record."""
    (root / "nodes").mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text('{"fixtures": [{"input": "nodes/a.json"}]}\n', encoding="utf-8")
    (root / "schema.json").write_text("{}\n", encoding="utf-8")
    (root / "nodes" / "a.json").write_text('{"kind":"Text"}\n', encoding="utf-8")
    other = {
        "source": "schema.json",
        "consumers": ["Fuaran/Fuaran-UI/other-host/schema.json"],
        "check": "fingerprint",
        "regen": "elsewhere",
    }
    copies = {"kind": "copies", "producer": "wire-format-fixtures", "note": "probe", "records": [other]}
    (root / "copies.json").write_text(json.dumps(copies, indent=2) + "\n", encoding="utf-8", newline="")


def _make_repo(root: Path) -> None:
    """A fresh repository holding only the script, committed."""
    (root / _SCRIPT_IN_REPO).parent.mkdir(parents=True)
    shutil.copyfile(_SCRIPT, root / _SCRIPT_IN_REPO)
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "probe")


@dataclass
class Estate:
    tmp: Path
    repo: Path
    worktree: Path


@pytest.fixture
def estate(tmp_path: Path) -> Iterator[Estate]:
    tmp = tmp_path.resolve()
    ui = tmp / "ws" / "Fuaran" / "Fuaran-UI"
    repo = ui / "fuaran-py"
    worktree = ui / "wt-probe"
    _write_corpus(ui / "wire-format-fixtures")
    _make_repo(repo)
    _git(repo, "worktree", "add", "-q", "--detach", str(worktree), "HEAD")
    try:
        yield Estate(tmp, repo, worktree)
    finally:
        if worktree.exists():
            _git(repo, "worktree", "remove", "--force", str(worktree))


def test_worktree_and_checkout_declare_byte_identical_records(estate: Estate) -> None:
    from_checkout = estate.tmp / "corpus-checkout"
    from_worktree = estate.tmp / "corpus-worktree"
    _write_corpus(from_checkout)
    _write_corpus(from_worktree)

    a = _declare(estate.repo, from_checkout)
    assert a.returncode == 0, a.stderr
    b = _declare(estate.worktree, from_worktree)
    assert b.returncode == 0, b.stderr

    checkout_bytes = (from_checkout / "copies.json").read_bytes()
    worktree_bytes = (from_worktree / "copies.json").read_bytes()
    assert worktree_bytes == checkout_bytes

    consumers = [c for r in json.loads(worktree_bytes)["records"] for c in r["consumers"]]
    assert f"{_EXPECTED_PREFIX}/nodes/a.json" in consumers
    assert "Fuaran/Fuaran-UI/other-host/schema.json" in consumers
    assert [c for c in consumers if "wt-probe" in c] == []


def test_refuses_when_git_cannot_name_the_repository(estate: Estate) -> None:
    loose = estate.tmp / "loose"
    (loose / _SCRIPT_IN_REPO).parent.mkdir(parents=True)
    shutil.copyfile(_SCRIPT, loose / _SCRIPT_IN_REPO)
    corpus = estate.tmp / "corpus-loose"
    _write_corpus(corpus)
    before = (corpus / "copies.json").read_bytes()

    r = _declare(loose, corpus)
    assert r.returncode == 1
    assert "cannot declare: git cannot name the repository" in r.stderr
    assert (corpus / "copies.json").read_bytes() == before


def test_refuses_when_the_primary_checkout_is_outside_the_canonical_layout(estate: Estate) -> None:
    stray = estate.tmp / "stray" / "fuaran-py"
    _make_repo(stray)
    corpus = estate.tmp / "corpus-stray"
    _write_corpus(corpus)
    before = (corpus / "copies.json").read_bytes()

    r = _declare(stray, corpus)
    assert r.returncode == 1
    assert "does not sit in the canonical side-by-side layout" in r.stderr
    assert (corpus / "copies.json").read_bytes() == before


def test_probe_runs_the_script_under_test(estate: Estate) -> None:
    assert (estate.repo / _SCRIPT_IN_REPO).read_bytes() == _SCRIPT.read_bytes()
