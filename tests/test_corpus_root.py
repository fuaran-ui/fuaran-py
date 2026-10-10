"""Phase 2204 — every corpus reader resolves the corpus through ONE resolver.

``fuaran_ui.conformance.corpus_root`` resolves the shared wire-format corpus as:
explicit argument, then ``FUARAN_WIRE_FIXTURES``, then ``../wire-format-fixtures``;
a named root holding no ``manifest.json`` is refused, never ignored. These tests
pin the order, prove the sync script and the refusal report honour the variable
end to end (a scratch corpus with one fixture changed), and guard the repository
against a reader that spells the sibling path for itself.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fuaran_ui.conformance import refusal_report
from fuaran_ui.conformance.corpus_root import (
    CORPUS_ROOT_ENV,
    CorpusRootRefused,
    resolve_corpus_root,
    sibling_corpus_root,
)

_REPO = Path(__file__).resolve().parents[1]
_SNAPSHOT = _REPO / "conformance" / "corpus"
_SCRIPT_IN_REPO = Path("conformance") / "sync_corpus.py"
_RESOLVER_IN_REPO = Path("src") / "fuaran_ui" / "conformance" / "corpus_root.py"


def _corpus(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "manifest.json").write_text("{}", encoding="utf-8")
    return path


# ─── the order ───────────────────────────────────────────────────────────────


def test_nothing_named_falls_back_to_the_sibling() -> None:
    assert resolve_corpus_root(environ={}) == sibling_corpus_root()


def test_an_empty_variable_counts_as_unset() -> None:
    assert resolve_corpus_root(environ={CORPUS_ROOT_ENV: "  "}) == sibling_corpus_root()


def test_the_variable_is_honoured(tmp_path: Path) -> None:
    named = _corpus(tmp_path / "named")
    assert resolve_corpus_root(environ={CORPUS_ROOT_ENV: str(named)}) == named.resolve()


def test_an_explicit_argument_beats_the_variable(tmp_path: Path) -> None:
    explicit = _corpus(tmp_path / "explicit")
    got = resolve_corpus_root(explicit, environ={CORPUS_ROOT_ENV: str(tmp_path / "nowhere")})
    assert got == explicit.resolve()


def test_a_variable_naming_no_corpus_is_refused_naming_variable_and_path(tmp_path: Path) -> None:
    with pytest.raises(CorpusRootRefused, match=rf"{CORPUS_ROOT_ENV}=.*not-a-corpus.*does not name a conformance"):
        resolve_corpus_root(environ={CORPUS_ROOT_ENV: str(tmp_path / "not-a-corpus")})


def test_an_explicit_argument_naming_no_corpus_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CorpusRootRefused, match="does not name a conformance corpus"):
        resolve_corpus_root(tmp_path, environ={})


# ─── the readers, end to end ─────────────────────────────────────────────────


def _altered_corpus(tmp_path: Path) -> tuple[Path, str]:
    """A copy of the bundled snapshot with one node fixture's bytes changed."""
    root = tmp_path / "altered"
    shutil.copytree(_SNAPSHOT, root)
    name = sorted(p.name for p in (root / "nodes").glob("*.json"))[0]
    path = root / "nodes" / name
    path.write_text(path.read_text(encoding="utf-8").replace("{", "{ ", 1), encoding="utf-8", newline="")
    return root, name


def _probe_repo(tmp_path: Path) -> Path:
    """A throwaway checkout holding the sync script and the resolver it loads."""
    probe = tmp_path / "estate" / "fuaran-py"
    for rel in (_SCRIPT_IN_REPO, _RESOLVER_IN_REPO):
        (probe / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_REPO / rel, probe / rel)
    return probe


def _sync(probe: Path, corpus: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env[CORPUS_ROOT_ENV] = str(corpus)
    return subprocess.run(
        [sys.executable, str(probe / _SCRIPT_IN_REPO)],
        cwd=probe,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_sync_corpus_syncs_from_the_corpus_the_variable_names(tmp_path: Path) -> None:
    altered, name = _altered_corpus(tmp_path)
    probe = _probe_repo(tmp_path)
    result = _sync(probe, altered)
    assert result.returncode == 0, result.stderr
    synced = probe / "conformance" / "corpus" / "nodes" / name
    assert synced.read_bytes() == (altered / "nodes" / name).read_bytes()


def test_sync_corpus_refuses_a_variable_naming_no_corpus(tmp_path: Path) -> None:
    probe = _probe_repo(tmp_path)
    result = _sync(probe, tmp_path / "not-a-corpus")
    assert result.returncode == 1
    assert re.search(rf"{CORPUS_ROOT_ENV}=.*does not name a conformance corpus", result.stderr), result.stderr
    assert not (probe / "conformance" / "corpus").exists()


def test_the_refusal_report_reads_the_corpus_the_variable_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    altered, _ = _altered_corpus(tmp_path)
    monkeypatch.setenv(CORPUS_ROOT_ENV, str(altered))
    out = tmp_path / "report.json"
    assert refusal_report.main(["--out", str(out)]) == 0
    assert Path(json.loads(out.read_text(encoding="utf-8"))["corpus"]) == altered.resolve()

    monkeypatch.setenv(CORPUS_ROOT_ENV, str(tmp_path / "not-a-corpus"))
    assert refusal_report.main(["--out", str(out)]) == 2


# ─── the guard ───────────────────────────────────────────────────────────────

#: A path built from the corpus directory name: a ``/ "wire-format-fixtures"``
#: join, a ``"../wire-format-fixtures..."`` literal, or ``"wire-format-fixtures/..."``.
_SPELLING = re.compile(
    r"""/\s*["']wire-format-fixtures["']|["'](?:\.\./)+wire-format-fixtures|["']wire-format-fixtures/[^"'\s]*["']"""
)

#: Throwaway estates the declare and sentinel tests build: they name the directory
#: to CREATE it, not to read a corpus from it. File -> exact stripped line.
_ALLOWED = {
    ("tests/test_corpus_declare_worktree.py", '_write_corpus(ui / "wire-format-fixtures")'),
    ("tests/test_corpus_sentinel.py", 'repo = tmp_path / "wire-format-fixtures"'),
}


def _tracked_python() -> list[str]:
    listed = subprocess.run(["git", "-C", str(_REPO), "ls-files", "*.py"], capture_output=True, text=True, check=False)
    assert listed.returncode == 0, f"git cannot list this checkout: {listed.stderr.strip()}"
    return listed.stdout.split()


def test_no_reader_spells_the_sibling_corpus_path_for_itself() -> None:
    files = _tracked_python()
    assert len(files) > 50, "the guard is not looking at the repository"
    offenders = []
    for rel in files:
        if rel == _RESOLVER_IN_REPO.as_posix():
            continue
        for number, line in enumerate((_REPO / rel).read_text(encoding="utf-8").splitlines(), start=1):
            text = line.strip()
            if text.startswith("#") or not _SPELLING.search(line) or (rel, text) in _ALLOWED:
                continue
            offenders.append(f"{rel}:{number}: {text}")
    assert offenders == [], "resolve the corpus with fuaran_ui.conformance.corpus_root instead:\n" + "\n".join(
        offenders
    )


def test_the_guard_is_not_vacuous() -> None:
    assert _SPELLING.search('Path(__file__).resolve().parents[2] / "wire-format-fixtures"')
    assert _SPELLING.search('"../wire-format-fixtures/manifest.json"')
    assert not _SPELLING.search('pytest.skip("wire-format-fixtures/validator not found")')
