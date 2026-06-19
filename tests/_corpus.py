"""Locate the workspace conformance corpus for the test suite.

The corpus authority lives alongside this sibling at ``../wire-format-fixtures``.
Tests that consume it are skipped when it is absent (e.g. ``fuaran-py`` checked
out standalone), mirroring the TypeScript host's drift-guard ``skipIf``. A synced
offline snapshot + drift guard is a follow-up.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# tests/_corpus.py → tests → fuaran-py → Fuaran-UI → wire-format-fixtures
CORPUS_ROOT = Path(__file__).resolve().parents[2] / "wire-format-fixtures"


def corpus_available() -> bool:
    return (CORPUS_ROOT / "manifest.json").is_file()


corpus_required = pytest.mark.skipif(
    not corpus_available(),
    reason=f"conformance corpus not found at {CORPUS_ROOT}",
)


def fixtures_of(*kinds: str) -> list[dict]:
    if not corpus_available():
        return []
    manifest = json.loads((CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    return [fx for fx in manifest["fixtures"] if fx["kind"] in kinds]
