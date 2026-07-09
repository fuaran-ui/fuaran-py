"""Sync the bundled corpus snapshot from the authoritative workspace corpus.

The Python port of ``fuaran-ts/packages/conformance/scripts/sync-corpus.mjs``.

The authoritative corpus lives in the workspace repo at ``../wire-format-fixtures``
(relative to this repo's root — the canonical side-by-side workspace layout). F#
is the sole generator (``--emit-corpus``); this script clean-copies the
certification payload set (``manifest.json``, ``schema.json``, ``nodes/``,
``ops/``, ``reject/``, ``lenient/``, ``envelope/``) into this repo's
``conformance/corpus/`` snapshot. The ``conformance/`` tooling subdirectory of
the authority (its in-house cross-host gate) is intentionally NOT copied, and
neither are the ``dag/`` / ``merge-conformance/`` / ``chain/`` / ``markdown/``
sub-corpora — each carries its own manifest and its own suite.

Run after any corpus regeneration (fuaran's ``--emit-corpus``), then commit the
snapshot with the repo. ``tests/test_corpus_sync.py`` fails the suite if the
committed snapshot drifts from the authority. The snapshot also makes
``fuaran-py`` standalone-testable: when the authority is absent (a lone
``fuaran-py`` checkout) the test suite falls back to reading it (see
``tests/_corpus.py``).

Usage::

    python conformance/sync_corpus.py
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
_FILES = ("manifest.json", "schema.json")
_DIRS = ("nodes", "ops", "reject", "lenient", "envelope")


def sync() -> Path:
    if not (AUTHORITY / "manifest.json").is_file():
        raise SystemExit(
            f"Authoritative corpus not found at {AUTHORITY}\n"
            "This script requires the canonical workspace layout (the workspace repo's "
            "wire-format-fixtures/ as a sibling of this fuaran-py checkout)."
        )

    shutil.rmtree(SNAPSHOT, ignore_errors=True)
    SNAPSHOT.mkdir(parents=True, exist_ok=True)

    for name in _FILES:
        shutil.copyfile(AUTHORITY / name, SNAPSHOT / name)
    for name in _DIRS:
        shutil.copytree(AUTHORITY / name, SNAPSHOT / name)

    return SNAPSHOT


if __name__ == "__main__":
    dest = sync()
    print(f"Corpus snapshot synced: {AUTHORITY} -> {dest}", file=sys.stderr)
