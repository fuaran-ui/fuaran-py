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

# The additive DAG-record sub-corpus carries its own manifest under dag/.
DAG_CORPUS_ROOT = CORPUS_ROOT / "dag"

# The additive merge-conformance sub-corpus carries its own manifest.
MERGE_CORPUS_ROOT = CORPUS_ROOT / "merge-conformance"

# The op-stream hash-chain sub-corpus is a single golden file (no manifest dir).
CHAIN_CORPUS_FILE = CORPUS_ROOT / "chain" / "chain-corpus.json"


def corpus_available() -> bool:
    return (CORPUS_ROOT / "manifest.json").is_file()


def dag_corpus_available() -> bool:
    return (DAG_CORPUS_ROOT / "manifest.json").is_file()


def merge_corpus_available() -> bool:
    return (MERGE_CORPUS_ROOT / "manifest.json").is_file()


corpus_required = pytest.mark.skipif(
    not corpus_available(),
    reason=f"conformance corpus not found at {CORPUS_ROOT}",
)


def fixtures_of(*kinds: str) -> list[dict]:
    if not corpus_available():
        return []
    manifest = json.loads((CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    return [fx for fx in manifest["fixtures"] if fx["kind"] in kinds]


dag_corpus_required = pytest.mark.skipif(
    not dag_corpus_available(),
    reason=f"DAG sub-corpus not found at {DAG_CORPUS_ROOT}",
)


def dag_fixtures() -> list[dict]:
    if not dag_corpus_available():
        return []
    manifest = json.loads((DAG_CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    return list(manifest["fixtures"])


merge_corpus_required = pytest.mark.skipif(
    not merge_corpus_available(),
    reason=f"merge-conformance sub-corpus not found at {MERGE_CORPUS_ROOT}",
)


def merge_fixtures() -> list[dict]:
    if not merge_corpus_available():
        return []
    manifest = json.loads((MERGE_CORPUS_ROOT / "manifest.json").read_text(encoding="utf-8"))
    return list(manifest["fixtures"])


def chain_corpus_available() -> bool:
    return CHAIN_CORPUS_FILE.is_file()


chain_corpus_required = pytest.mark.skipif(
    not chain_corpus_available(),
    reason=f"op-stream chain corpus not found at {CHAIN_CORPUS_FILE}",
)


def chain_corpus() -> dict:
    if not chain_corpus_available():
        return {"records": []}
    return json.loads(CHAIN_CORPUS_FILE.read_text(encoding="utf-8"))


def chain_records() -> list[dict]:
    return list(chain_corpus().get("records", []))
