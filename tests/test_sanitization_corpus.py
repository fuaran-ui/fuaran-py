"""The shared ``sanitization/`` corpus family, run against this host's URL floor.

Unlike every other corpus family this one is **not** byte-parity: the markup a host
wraps around a URL differs legitimately between hosts, so comparing those bytes would
pin accidents rather than the contract. Each case states an **invariant** instead —
``reject`` (refuse it) or ``accept`` (take it, and emit the normalised form) — plus
the reason the URL parser gives, which is what makes the case meaningful.

The corpus verifies its own ``reason`` claims against a real WHATWG parser
(``sanitization/verify-against-url-parser.mjs``); this module verifies that *this*
host agrees with the resulting invariants.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from fuaran_py.renderer.sanitize import sanitize_url, sanitize_url_or_blank


def _manifest_path() -> Path | None:
    for parent in [Path(__file__).resolve(), *Path(__file__).resolve().parents]:
        candidate = parent / "wire-format-fixtures" / "sanitization" / "manifest.json"
        if candidate.is_file():
            return candidate
    return None


def _cases() -> list[dict[str, Any]]:
    path = _manifest_path()
    if path is None:
        return []
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return [c for group in manifest["groups"] for c in group["cases"]]


_CASES = _cases()

corpus_required = pytest.mark.skipif(not _CASES, reason="wire-format-fixtures/sanitization not found")


@corpus_required
@pytest.mark.parametrize("case", _CASES, ids=[c["id"] for c in _CASES])
def test_url_floor_invariant_holds(case: dict[str, Any]) -> None:
    got = sanitize_url(case["input"])
    if case["invariant"] == "reject":
        assert got is None, f"{case['id']}: expected REJECT, got {got!r}"
        # §19 rule 6 — the or-blank variant substitutes about:blank.
        assert sanitize_url_or_blank(case["input"]) == "about:blank"
    else:
        assert got == case["expected"], f"{case['id']}: expected {case['expected']!r}, got {got!r}"
