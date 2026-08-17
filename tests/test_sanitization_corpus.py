"""The shared ``sanitization/`` corpus family, run against this host's render-time floor.

``WIRE_FORMAT.md`` §22 (and §19 for the URL group). Unlike every other corpus family
this one is **not** byte-parity: the markup a host wraps around a payload differs
legitimately between hosts, so comparing those bytes would pin accidents rather than
the contract. Each case states an **invariant** instead — ``reject``, ``accept`` or
``inert`` — and this module asserts that *this* host satisfies it.

The url-floor group's claims are verified by the corpus itself against a real WHATWG
parser (``sanitization/verify-against-url-parser.mjs``), so what is checked here is
agreement with an invariant established independently, rather than agreement between
two of our own assertions.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from fuaran_py.renderer.markdown import to_html
from fuaran_py.renderer.sanitize import (
    is_allowed_extra_attribute_key,
    is_safe_extra_attribute_value,
    sanitize_markdown_html,
    sanitize_url,
    sanitize_url_or_blank,
)

# Groups whose seam does not exist on this host. Declared rather than omitted: a
# group this host silently skipped would read as covered in the family, which is
# the shape §22.2 refuses.
NOT_APPLICABLE: dict[str, str] = {}


def _manifest_path() -> Path | None:
    for parent in [Path(__file__).resolve(), *Path(__file__).resolve().parents]:
        candidate = parent / "wire-format-fixtures" / "sanitization" / "manifest.json"
        if candidate.is_file():
            return candidate
    return None


def _groups() -> list[dict[str, Any]]:
    path = _manifest_path()
    if path is None:
        return []
    return json.loads(path.read_text(encoding="utf-8"))["groups"]


_GROUPS = _groups()


def _cases(group_id: str) -> list[dict[str, Any]]:
    for g in _GROUPS:
        if g["id"] == group_id:
            return g["cases"]
    return []


def _ids(cases: list[dict[str, Any]]) -> list[str]:
    return [c["id"] for c in cases]


corpus_required = pytest.mark.skipif(not _GROUPS, reason="wire-format-fixtures/sanitization not found")


def _assert_inert(rendered: str, case: dict[str, Any]) -> None:
    """The ``inert`` check.

    A **pattern**, not a substring, deliberately: an escaped payload still contains
    the text ``onclick=``, harmlessly, so a substring check would fail a *correct*
    host. What must not exist is a live tag carrying the handler.

    ``required`` is the other half, catching a host that satisfies every forbidden
    pattern by discarding the content entirely.
    """
    for pattern in case.get("forbiddenPattern", []):
        assert not re.search(pattern, rendered, re.IGNORECASE), (
            f"{case['id']}: output matches forbidden pattern {pattern!r} — "
            f"payload {case['input']!r} survived as live markup"
        )
    for required in case.get("required", []):
        assert required in rendered, (
            f"{case['id']}: output is missing required {required!r} — the payload was stripped rather than escaped"
        )


@corpus_required
def test_every_group_is_claimed() -> None:
    """A group no leg runs would be silently untested while reading as covered."""
    known = {"url-floor", "markdown-body", "text-source", "extra-attributes"} | set(NOT_APPLICABLE)
    unclaimed = [g["id"] for g in _GROUPS if g["id"] not in known]
    assert not unclaimed, f"the corpus carries group(s) this host neither runs nor declares not-applicable: {unclaimed}"


@corpus_required
@pytest.mark.parametrize("case", _cases("url-floor"), ids=_ids(_cases("url-floor")))
def test_url_floor(case: dict[str, Any]) -> None:
    got = sanitize_url(case["input"])
    if case["invariant"] == "reject":
        assert got is None, f"{case['id']}: expected REJECT, got {got!r}"
        # §19 rule 6 — the or-blank variant substitutes about:blank.
        assert sanitize_url_or_blank(case["input"]) == "about:blank"
    else:
        assert got == case["expected"], f"{case['id']}: expected {case['expected']!r}, got {got!r}"


@corpus_required
@pytest.mark.parametrize("case", _cases("markdown-body"), ids=_ids(_cases("markdown-body")))
def test_markdown_body(case: dict[str, Any]) -> None:
    # The render path in order: the deterministic GFM renderer, which escapes by
    # construction, then the defence-in-depth sweep. The obligation is on the pair.
    _assert_inert(sanitize_markdown_html(to_html(case["input"])), case)


@corpus_required
@pytest.mark.parametrize("case", _cases("text-source"), ids=_ids(_cases("text-source")))
def test_text_source(case: dict[str, Any]) -> None:
    # The markdown renderer is the seam a text-bearing string reaches on this host,
    # and it escapes by construction — which is what makes the legitimate
    # ``a < b && c > d`` case survive intact rather than stripped.
    _assert_inert(to_html(case["input"]), case)


@corpus_required
@pytest.mark.parametrize("case", _cases("extra-attributes"), ids=_ids(_cases("extra-attributes")))
def test_extra_attributes(case: dict[str, Any]) -> None:
    admitted = (
        is_allowed_extra_attribute_key(case["input"])
        if case["target"] == "key"
        else is_safe_extra_attribute_value(case["input"])
    )
    assert admitted == (case["invariant"] == "accept"), f"{case['id']}: payload {case['input']!r}"
