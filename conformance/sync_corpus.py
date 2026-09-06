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

The optional argument names the authority explicitly, for a checkout whose
directory depth is not the canonical side-by-side one (a git worktree, say).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# conformance/sync_corpus.py → conformance → fuaran-py → Fuaran-UI → wire-format-fixtures
_HERE = Path(__file__).resolve()
AUTHORITY = _HERE.parents[2] / "wire-format-fixtures"
SNAPSHOT = _HERE.parent / "corpus"

# The core certification families — the Node/TreeOp/reject/lenient/envelope set
# the schema + cross-host runner certify. Mirrors sync-corpus.mjs exactly.
_FILES = ("manifest.json", "schema.json", "render-fidelity.json")
_DIRS = ("nodes", "ops", "reject", "lenient", "envelope", "elicitation", "markdown")


def sync(authority: Path | None = None) -> Path:
    authority = AUTHORITY if authority is None else Path(authority).resolve()
    if not (authority / "manifest.json").is_file():
        raise SystemExit(
            f"Authoritative corpus not found at {authority}\n"
            "This script requires the canonical workspace layout (the workspace repo's "
            "wire-format-fixtures/ as a sibling of this fuaran-py checkout), or an "
            "explicit path as the first argument."
        )

    shutil.rmtree(SNAPSHOT, ignore_errors=True)
    SNAPSHOT.mkdir(parents=True, exist_ok=True)

    for name in _FILES:
        shutil.copyfile(authority / name, SNAPSHOT / name)
    for name in _DIRS:
        shutil.copytree(authority / name, SNAPSHOT / name)

    return SNAPSHOT


if __name__ == "__main__":
    source = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else AUTHORITY
    dest = sync(source)
    print(f"Corpus snapshot synced: {source} -> {dest}", file=sys.stderr)
