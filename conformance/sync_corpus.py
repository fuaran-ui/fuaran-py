"""Sync the bundled corpus snapshot from the authoritative workspace corpus.

The Python port of ``fuaran-ts/packages/conformance/scripts/sync-corpus.mjs``.

The authoritative corpus lives in the workspace repo at ``../wire-format-fixtures``
(relative to this repo's root — the canonical side-by-side workspace layout). F#
is the sole generator (``--emit-corpus``); this script clean-copies the
certification payload set (``manifest.json``, ``schema.json``,
``render-fidelity.json``, ``nodes/``, ``ops/``, ``reject/``, ``lenient/``,
``envelope/``, ``elicitation/``, ``markdown/``) into this repo's
``conformance/corpus/`` snapshot. The ``conformance/`` tooling subdirectory of
the authority (its in-house cross-host gate) is intentionally NOT copied, and
neither are the ``dag/`` / ``merge-conformance/`` / ``chain/`` sub-corpora —
each carries its own manifest, its own suite, and its own skip guard keyed to
that manifest, so a checkout without them skips cleanly.

``markdown/`` and ``render-fidelity.json`` ARE copied, and were not before. The
suites that read them guard on the CORE manifest rather than on the file they
actually open (``tests/test_markdown_corpus.py``'s two non-vacuity assertions),
so a checkout carrying only the snapshot ran them against an empty fixture list
and went RED rather than skipping. Copying them makes the snapshot the whole of
what those suites need, which is the durable form of the fix.

Run after any corpus regeneration (fuaran's ``--emit-corpus``), then commit the
snapshot with the repo. ``tests/test_corpus_sync.py`` fails the suite if the
committed snapshot drifts from the authority. The snapshot also makes
``fuaran-py`` standalone-testable: when the authority is absent (a lone
``fuaran-py`` checkout) the test suite falls back to reading it (see
``tests/_corpus.py``).

Usage::

    python conformance/sync_corpus.py
    python conformance/sync_corpus.py <path-to-wire-format-fixtures>
    python conformance/sync_corpus.py --check
    python conformance/sync_corpus.py --check <path-to-wire-format-fixtures>

The optional path argument names the authority explicitly, for a checkout whose
directory depth is not the canonical side-by-side one (a git worktree, say).

The provenance sentinel and ``--check``
---------------------------------------

Every sync writes ``conformance/corpus/snapshot.json`` recording the authority
commit the snapshot was taken from::

    {"authorityCommit": "<40 lowercase hex>", "kind": "corpusSnapshot"}

``--check`` measures that record against the authority clone's HEAD and reports
the distance between them, without copying anything:

* exit ``0`` — the snapshot records the authority's current HEAD;
* exit ``2`` — the snapshot is BEHIND (or otherwise differs from) the authority;
* exit ``1`` — the check could not be made (no authority beside this checkout,
  the authority is not a git clone, or the snapshot carries no sentinel).

The ``1`` and ``2`` cases are kept apart deliberately. A check that reported
drift it had not measured would be the same vacuous green this guard exists to
prevent, read backwards. Both are non-zero, so a release gate that refuses on
any non-zero refuses both; only the wording distinguishes them.

Why this exists at all: a green CI run is not evidence that the release gesture
will pass. CI and the publish workflow each check the corpus out at HEAD, so a
snapshot that matched the authority when CI ran can be behind it minutes later
when the tag is cut — which is exactly what happened to the first ``v0.1.0``
tag, whose publish run failed the gated ``test_corpus_sync`` after the authority
moved twice. The distance is recomputed on the tagged tree, before that run.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

# conformance/sync_corpus.py → conformance → fuaran-py → Fuaran-UI → wire-format-fixtures
_HERE = Path(__file__).resolve()
AUTHORITY = _HERE.parents[2] / "wire-format-fixtures"
SNAPSHOT = _HERE.parent / "corpus"

# The provenance sentinel: which authority commit this snapshot was taken from.
# It lives at the snapshot ROOT, outside every family directory, so the
# byte-identity guard in tests/test_corpus_sync.py (which compares the families
# and the three named top-level files) never sees it and needs no exclusion.
SENTINEL = SNAPSHOT / "snapshot.json"
SENTINEL_KIND = "corpusSnapshot"

_RESYNC_COMMAND = "python conformance/sync_corpus.py"

# The core certification families — the Node/TreeOp/reject/lenient/envelope set
# the schema + cross-host runner certify. Mirrors sync-corpus.mjs exactly.
_FILES = ("manifest.json", "schema.json", "render-fidelity.json")
_DIRS = ("nodes", "ops", "reject", "lenient", "envelope", "elicitation", "markdown")

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _git(repo: Path, *args: str) -> str | None:
    """Run a read-only git command in ``repo``; ``None`` when it cannot answer.

    Every failure mode is folded into ``None`` on purpose — no git on PATH, the
    directory not being a clone, a shallow checkout that cannot resolve a commit.
    The callers turn ``None`` into "I could not measure this" rather than into a
    number, because a fabricated distance is worse than an absent one.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def authority_head(authority: Path) -> str | None:
    """The authority clone's HEAD commit, or ``None`` when it cannot be read."""
    sha = _git(authority, "rev-parse", "HEAD")
    return sha if sha is not None and _SHA_RE.match(sha) else None


def recorded_commit(sentinel: Path | None = None) -> str | None:
    """The authority commit the committed snapshot records, if it records one."""
    sentinel = SENTINEL if sentinel is None else sentinel
    try:
        payload = json.loads(sentinel.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    sha = payload.get("authorityCommit")
    return sha if isinstance(sha, str) and _SHA_RE.match(sha) else None


def commits_behind(authority: Path, snapshot_sha: str) -> int | None:
    """How many authority commits land after ``snapshot_sha``.

    ``None`` when the clone cannot answer — most commonly a shallow checkout
    (``actions/checkout`` defaults to depth 1), which holds HEAD but not the
    commit the snapshot names. Both workflows here therefore check the corpus
    out at full depth; where a caller has not, the check says the distance is
    unknown rather than guessing at it.
    """
    count = _git(authority, "rev-list", "--count", f"{snapshot_sha}..HEAD")
    if count is None or not count.isdigit():
        return None
    return int(count)


def write_sentinel(authority: Path, sentinel: Path | None = None) -> str | None:
    """Record the authority commit this snapshot was taken from.

    A clone whose HEAD cannot be read records ``null`` rather than nothing: the
    snapshot is still a valid corpus, and ``--check`` reports honestly that it
    cannot tell how old it is. Silently omitting the field would make an
    unmeasurable snapshot indistinguishable from a fresh one.
    """
    sentinel = SENTINEL if sentinel is None else sentinel
    head = authority_head(authority)
    payload = {"authorityCommit": head, "kind": SENTINEL_KIND}
    # newline="" — no platform translation. The repo pins LF (.gitattributes),
    # and a data artefact whose bytes depend on which host last re-synced is a
    # diff waiting to happen.
    sentinel.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="",
    )
    return head


def sync(authority: Path | None = None, snapshot: Path | None = None) -> Path:
    authority = AUTHORITY if authority is None else Path(authority).resolve()
    snapshot = SNAPSHOT if snapshot is None else snapshot
    if not (authority / "manifest.json").is_file():
        raise SystemExit(
            f"Authoritative corpus not found at {authority}\n"
            "This script requires the canonical workspace layout (the workspace repo's "
            "wire-format-fixtures/ as a sibling of this fuaran-py checkout), or an "
            "explicit path as the first argument."
        )

    shutil.rmtree(snapshot, ignore_errors=True)
    snapshot.mkdir(parents=True, exist_ok=True)

    for name in _FILES:
        shutil.copyfile(authority / name, snapshot / name)
    for name in _DIRS:
        shutil.copytree(authority / name, snapshot / name)

    write_sentinel(authority, snapshot / SENTINEL.name)

    return snapshot


def check(authority: Path | None = None, sentinel: Path | None = None) -> int:
    """Report the snapshot's distance from the authority. See the module docstring."""
    authority = AUTHORITY if authority is None else Path(authority).resolve()
    sentinel = SENTINEL if sentinel is None else sentinel

    if not (authority / "manifest.json").is_file():
        print(
            f"corpus drift UNMEASURED: no authoritative corpus at {authority} — "
            "nothing to measure the snapshot against",
            file=sys.stderr,
        )
        return 1

    snapshot_sha = recorded_commit(sentinel)
    if snapshot_sha is None:
        print(
            f"corpus drift UNMEASURED: the snapshot at {sentinel.parent} records no authority commit "
            f"(expected {sentinel.name} to carry a 40-character authorityCommit) — "
            f"re-sync to record one: {_RESYNC_COMMAND}",
            file=sys.stderr,
        )
        return 1

    head = authority_head(authority)
    if head is None:
        print(
            f"corpus drift UNMEASURED: cannot read HEAD of the authority at {authority} "
            "(not a git clone, or git is unavailable)",
            file=sys.stderr,
        )
        return 1

    if snapshot_sha == head:
        print(f"corpus snapshot in sync with the authority ({head})", file=sys.stderr)
        return 0

    behind = commits_behind(authority, snapshot_sha)
    if behind is None:
        headline = "snapshot behind authority by an unknown number of commits"
    elif behind == 0:
        # HEAD is not a descendant of the recorded commit: the authority's
        # history was rewritten, or the snapshot came off another branch. Not
        # "behind", and calling it that would be a guess.
        headline = "snapshot does not match the authority and is not behind it (rewritten history, or another branch)"
    else:
        headline = f"snapshot behind authority by {behind} commit{'' if behind == 1 else 's'}"

    print(f"{headline} (authority {head}, snapshot {snapshot_sha})", file=sys.stderr)
    print(f"re-sync and commit the snapshot: {_RESYNC_COMMAND}", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    args = list(argv)
    checking = "--check" in args
    if checking:
        args = [a for a in args if a != "--check"]
    if len(args) > 1:
        print(f"usage: {_RESYNC_COMMAND} [--check] [<path-to-wire-format-fixtures>]", file=sys.stderr)
        return 1

    source = Path(args[0]).resolve() if args else AUTHORITY
    if checking:
        return check(source)

    dest = sync(source)
    recorded = recorded_commit()
    print(
        f"Corpus snapshot synced: {source} -> {dest} (authority commit {recorded or 'unknown'})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
