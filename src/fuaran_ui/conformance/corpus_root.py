"""The ONE corpus-root resolver for this repository (Phase 2204).

Every suite, harness and script here that reads the shared wire-format corpus
finds it through :func:`resolve_corpus_root`, in one order:

1. an explicit argument, when the caller has one;
2. ``FUARAN_WIRE_FIXTURES``, the variable every host in the estate reads;
3. the canonical sibling clone, ``../wire-format-fixtures`` beside this repo.

A NAMED root (1 or 2) must hold a ``manifest.json`` or it is REFUSED with
:class:`CorpusRootRefused`, naming the variable and the path — never ignored.
Falling through to the sibling would reach, from a git worktree, the SHARED
primary clone: the very corpus the override exists to leave alone, so a run
would certify against an oracle nobody named and report it as green. An empty
or whitespace-only value counts as unset, as it does in the other hosts.

The fallback is returned whether or not it exists: whether an absent sibling is
a skip or a failure stays each caller's decision, as it was before.

Standard library only, on purpose: ``conformance/sync_corpus.py`` loads this
file by path so the sync script still runs without the package installed.
``tests/test_corpus_root.py`` fails the build when any other file spells the
sibling path for itself.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

#: The variable that names the corpus root — the estate's own spelling.
CORPUS_ROOT_ENV = "FUARAN_WIRE_FIXTURES"

#: The corpus clone's directory name.
CORPUS_DIR_NAME = "wire-format-fixtures"

#: The file whose presence marks a directory as a checkout of this repository: the sync script
#: is the one file every repository tool here is run beside, and no other project spells it.
REPO_MARKER = Path("conformance") / "sync_corpus.py"


class CorpusRootRefused(ValueError):
    """A named corpus root holds no ``manifest.json``."""


class RepoRootRefused(ValueError):
    """An explicitly named repository root holds no ``conformance/sync_corpus.py``."""


def sibling_corpus_root(repo: Path | None = None) -> Path:
    """The canonical sibling clone, ``../wire-format-fixtures`` beside a repository.

    ``repo`` names the checkout the corpus sits beside; omitted, it is the checkout this file
    sits in. A caller that works on a DIFFERENT checkout than the one holding this module (a
    worktree run with another checkout's install) passes its own.
    """
    if repo is not None:
        return Path(repo).resolve().parent / CORPUS_DIR_NAME
    # parents: [0] conformance, [1] fuaran_ui, [2] src, [3] fuaran-py, [4] the
    # directory the corpus sits beside.
    return Path(__file__).resolve().parents[4] / CORPUS_DIR_NAME


def find_repo_root(explicit: str | os.PathLike[str] | None = None, *, cwd: Path | None = None) -> Path | None:
    """The checkout to operate on: ``explicit``, else the one the invoking directory sits in.

    Never the location of the installed module. An editable install made in one checkout is
    importable from every other, so a generator that located its target from ``__file__``
    wrote into whichever checkout the interpreter's install pointed at - from a git worktree
    that is the shared primary clone, and the run reported success. The checkout is instead
    what the caller is standing in (the nearest ancestor of ``cwd`` holding
    :data:`REPO_MARKER`) or what it names.

    Raises :class:`RepoRootRefused` when a named root is not a checkout; returns ``None`` when
    nothing is named and ``cwd`` is in no checkout - each caller decides what that means.
    """
    named = _named(explicit)
    if named is not None:
        root = Path(named).expanduser().resolve()
        if not (root / REPO_MARKER).is_file():
            raise RepoRootRefused(
                f"the repository given explicitly ({named!r}) is not a fuaran-py checkout "
                f"(no {REPO_MARKER.as_posix()} under {root})."
            )
        return root
    here = (Path.cwd() if cwd is None else Path(cwd)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / REPO_MARKER).is_file():
            return candidate
    return None


def _named(value: str | os.PathLike[str] | None) -> str | None:
    if value is None:
        return None
    text = os.fspath(value).strip()
    return text or None


def resolve_corpus_root(
    explicit: str | os.PathLike[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    sibling: Path | None = None,
) -> Path:
    """Explicit argument, then ``FUARAN_WIRE_FIXTURES``, then the sibling clone.

    ``sibling`` overrides the fallback for a caller that knows its own checkout
    (the sync script passes the sibling of the checkout it operates on). Raises
    :class:`CorpusRootRefused` when a named root holds no ``manifest.json``.
    """
    named = _named(explicit)
    if named is not None:
        root = Path(named).expanduser().resolve()
        if not (root / "manifest.json").is_file():
            raise CorpusRootRefused(
                f"the corpus root given explicitly ({named!r}) does not name a conformance corpus "
                f"(no manifest.json under {root}). It is refused rather than ignored: point it at "
                "the corpus root itself."
            )
        return root
    env = os.environ if environ is None else environ
    declared = _named(env.get(CORPUS_ROOT_ENV))
    if declared is not None:
        root = Path(declared).expanduser().resolve()
        if not (root / "manifest.json").is_file():
            raise CorpusRootRefused(
                f"{CORPUS_ROOT_ENV}={declared!r} does not name a conformance corpus (no manifest.json "
                f"under {root}). Point it at the corpus root, or unset it. It is refused rather than "
                f"ignored: falling back would reach ../{CORPUS_DIR_NAME}, which from a worktree is the "
                "shared primary clone the override exists to leave alone."
            )
        return root
    return sibling_corpus_root() if sibling is None else sibling


def present_corpus_root(explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """:func:`resolve_corpus_root`, or ``None`` when nothing is named and the sibling is absent."""
    root = resolve_corpus_root(explicit)
    return root if (root / "manifest.json").is_file() else None
