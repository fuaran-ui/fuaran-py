"""Deterministic GFM markdown renderer — cross-host conformance gate (Phase 292).

Loads the workspace-root corpus ``../wire-format-fixtures/markdown/corpus.json``
and asserts the Python renderer (``fuaran_py.renderer.markdown.to_html``)
reproduces every ``source -> html`` pair byte-for-byte. The F# reference
renderer emits the corpus; this is the Python leg of the §11.1-style cross-host
gate (``Py == corpus``), which together with the F# and TS legs proves
``F# == TS == Py``. Skipped when the corpus is absent (standalone checkout).
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py.renderer import markdown

_MARKDOWN_CORPUS = CORPUS_ROOT / "markdown" / "corpus.json"


def _markdown_fixtures() -> list[dict]:
    if not _MARKDOWN_CORPUS.is_file():
        return []
    return json.loads(_MARKDOWN_CORPUS.read_text(encoding="utf-8"))["fixtures"]


@corpus_required
def test_markdown_corpus_non_empty() -> None:
    assert len(_markdown_fixtures()) > 0


@corpus_required
@pytest.mark.parametrize("fixture", _markdown_fixtures(), ids=lambda f: f["id"])
def test_markdown_render_matches_corpus(fixture: dict) -> None:
    assert markdown.to_html(fixture["source"]) == fixture["html"], fixture["id"]
