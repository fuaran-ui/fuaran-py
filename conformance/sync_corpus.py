"""Sync the bundled corpus snapshot from the authoritative workspace corpus.

The Python port of ``fuaran-ts/packages/conformance/scripts/sync-corpus.mjs``.

The authoritative corpus lives in the workspace repo at ``../wire-format-fixtures``
(relative to this repo's root — the canonical side-by-side workspace layout). F#
is the sole generator (``--emit-corpus``); this script clean-copies the
certification payload set (``manifest.json``, ``schema.json``,
``render-fidelity.json``, ``render-text.json``, ``nodes/``, ``ops/``, ``reject/``, ``lenient/``,
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
    python conformance/sync_corpus.py --declare
    python conformance/sync_corpus.py --declare <path-to-wire-format-fixtures>

The optional path argument names the authority explicitly, for a checkout whose
directory depth is not the canonical side-by-side one (a git worktree, say).

The estate declaration and ``--declare``
----------------------------------------

``--declare`` (re)writes this snapshot's per-file records into the authority's
own ``copies.json`` — the estate's generated-cross-repo-copy registry that
``roadmapctl copies <workspace-root>`` projects on every sweep as
``RM-COPY-STALE``, quoting the regenerating command verbatim. The record set is
derived from the same payload list ``sync`` copies, so the declaration cannot
drift from what is actually bundled; a hand-kept list would be exactly the
hand-kept copy the registry exists to remove.

``fuaran-ts`` declares into the SAME file, so this step MERGES: it replaces only
the records whose copy lives under this snapshot and leaves every other record
untouched. Both writers emit identical byte formatting, so whichever runs second
does not re-churn the other's rows.

What this buys over the two guards above: both of them live on ONE side of the
copy and can only see their own checkout, so a corpus commit nobody re-synced is
invisible until someone runs that host's suite. The sweep holds every repo at
once and names the stale files with the command that fixes them, before anyone
inherits a red gate they did not cause.

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

**The comparison is by COMMIT, never by payload, and that has a cost worth
knowing before you cut a tag.** A corpus commit touching none of the copied
families — a README edit, a doc pointer, anything under a path ``sync`` does not
copy — still moves the authority's HEAD, so the recorded commit stops equalling
it and the release path refuses while the snapshot's bytes are correct. The
remedy is a **no-op re-sync**: run ``python conformance/sync_corpus.py`` and
commit what it writes, often ``corpus/snapshot.json`` alone. That commit is the
snapshot recording which revision it was last checked against, and it is an
ordinary part of the release recipe rather than a workaround for one.

The alternative — diffing the payload instead — was considered and is refused.
The authority is a REPOSITORY, so "these bytes happen to match today's" is a
weaker claim than "this was taken from that revision", and it reads a snapshot
as current whenever the authority's change happened to miss the copied set. That
is the one state a release must not be able to enter without knowing.
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

# The `regen` clause the estate sweep quotes verbatim on a stale finding. Rooted at
# the workspace, because that is where the reader of a `roadmapctl copies` finding is.
_REGEN_COMMAND = f"cd Fuaran/Fuaran-UI/fuaran-py; {_RESYNC_COMMAND}"

# The estate copy registry the `--declare` step writes into, at the authority's root.
_COPIES_MANIFEST = "copies.json"

# JSON carries no comment syntax, so the header note the registry's readers need is a
# field. `roadmapctl copies` reads `kind`, `producer` and `records` and ignores anything
# else, so this rides along unmolested. Seeded once and then preserved verbatim by both
# writers, so a hand edit to it survives the next declare.
_COPIES_NOTE = (
    "GENERATED — do not hand-edit the records. Each bundled-corpus record is (re)written by "
    "the host that bundles the snapshot: fuaran-ts via `pnpm --filter @fuaran-ui/conformance "
    "declare-corpus`, fuaran-py via `python conformance/sync_corpus.py --declare`. Each writer "
    "replaces only the records whose copy lives under its own snapshot, so the two co-own this "
    "file without clobbering each other. fuaran-go and fuaran-rs are DELIBERATELY UNDECLARED: "
    "they bundle no snapshot and read this corpus directly from the workspace (their CI checks "
    "it out to ../wire-format-fixtures), so there is no copy of it that can go stale."
)

# The core certification families — the Node/TreeOp/reject/lenient/envelope set
# the schema + cross-host runner certify. Mirrors sync-corpus.mjs exactly.
_FILES = ("manifest.json", "schema.json", "render-fidelity.json", "render-text.json")
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


def payload_files(authority: Path) -> list[str]:
    """Every bundled file, authority-relative and sorted — the declaration's record set.

    Derived from the same ``_FILES`` / ``_DIRS`` the sync copies, never from a second
    list: a declaration that enumerated the payload independently could go out of step
    with what is actually bundled, which is precisely the drift this exists to report.
    """
    files = [name for name in _FILES if (authority / name).is_file()]
    for name in _DIRS:
        root = authority / name
        if not root.is_dir():
            continue
        files.extend(sorted(p.relative_to(authority).as_posix() for p in root.rglob("*") if p.is_file()))
    return sorted(files)


def declare(authority: Path | None = None, snapshot: Path | None = None) -> int:
    """(Re)write this snapshot's per-file records into the authority's ``copies.json``.

    One record per COPY, not per source. A source bundled by two hosts is two records,
    because a record carries exactly one ``regen`` clause and the two hosts are re-synced
    by different commands — the sweep must be able to quote the right one at whoever is
    reading the finding.

    ``check: "fingerprint"`` rather than ``bytes``: the corpus generator writes the
    platform newline, so a freshly regenerated authority working tree can be CRLF while a
    committed snapshot is the LF its ``.gitattributes`` pins. A registry that reported all
    ~600 files as drifted on that is a registry the estate learns to scroll past.
    """
    authority = AUTHORITY if authority is None else Path(authority).resolve()
    snapshot = SNAPSHOT if snapshot is None else Path(snapshot).resolve()

    if not (authority / "manifest.json").is_file():
        print(
            f"cannot declare: no authoritative corpus at {authority}",
            file=sys.stderr,
        )
        return 1

    # The workspace root: the directory the estates sit under, three levels above the
    # authority in the canonical layout. `roadmapctl copies` resolves every consumer path
    # against it, and there is no other root that can address a file in a different repo.
    workspace_root = authority.parents[2]
    try:
        prefix = snapshot.relative_to(workspace_root).as_posix()
    except ValueError:
        print(
            f"cannot declare: the bundled snapshot at {snapshot} does not sit under the "
            f"workspace root inferred from the authority ({workspace_root}). The estate copy "
            "registry addresses consumers by workspace-relative path, so a checkout outside "
            "the canonical side-by-side layout cannot declare — re-run from one that is.",
            file=sys.stderr,
        )
        return 1

    mine = [
        {
            "source": rel,
            "consumers": [f"{prefix}/{rel}"],
            "check": "fingerprint",
            "regen": _REGEN_COMMAND,
        }
        for rel in payload_files(authority)
    ]

    # Merge: keep every record that is not about a copy under THIS snapshot.
    manifest_path = authority / _COPIES_MANIFEST
    existing: list[dict] = []
    note = ""
    if manifest_path.is_file():
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
        if parsed.get("kind") != "copies":
            print(
                f'cannot declare: {manifest_path} exists but is not a "kind": "copies" manifest',
                file=sys.stderr,
            )
            return 1
        note = parsed["note"] if isinstance(parsed.get("note"), str) else ""
        existing = [
            r for r in parsed.get("records", []) if not any(c.startswith(f"{prefix}/") for c in r.get("consumers", []))
        ]

    records = sorted(existing + mine, key=lambda r: (r["source"], (r["consumers"] or [""])[0]))

    payload = {
        "kind": "copies",
        "producer": "wire-format-fixtures",
        "note": note or _COPIES_NOTE,
        "records": records,
    }
    # ensure_ascii=False and the explicit newline="" so these bytes match the ones
    # fuaran-ts's sync-corpus.mjs writes for the same content — the two hosts co-own this
    # file, and a formatting difference would make each rewrite churn the other's rows.
    manifest_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="",
    )

    print(
        f"Declared {len(mine)} bundled snapshot file(s) as estate copies in {manifest_path}\n"
        f"  consumer prefix: {prefix}\n"
        f"  total records now: {len(records)}",
        file=sys.stderr,
    )
    return 0


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
    # The commonest instance, said out loud so it is not read as a fixture change
    # that this host has failed to adopt: the comparison is by COMMIT, so an
    # authority commit touching none of the copied families lands here with the
    # payload already correct, and the re-sync writes only the sentinel.
    print(
        "  (this compares the authority COMMIT, not the payload — an authority commit touching no bundled "
        "fixture lands here too, and the re-sync then rewrites only corpus/snapshot.json)",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str]) -> int:
    args = list(argv)
    checking = "--check" in args
    declaring = "--declare" in args
    args = [a for a in args if a not in ("--check", "--declare")]
    if len(args) > 1 or (checking and declaring):
        print(
            f"usage: {_RESYNC_COMMAND} [--check | --declare] [<path-to-wire-format-fixtures>]",
            file=sys.stderr,
        )
        return 1

    source = Path(args[0]).resolve() if args else AUTHORITY
    if checking:
        return check(source)
    if declaring:
        return declare(source)

    dest = sync(source)
    recorded = recorded_commit()
    print(
        f"Corpus snapshot synced: {source} -> {dest} (authority commit {recorded or 'unknown'})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
