"""The certification-kit stdio bridge (fuaran_py.conformance.bridge).

Proves the bridge the JS adapter (``wire-format-fixtures/conformance/
fuaran-py.adapter.mjs``) shells is correct: over the corpus, its ``decodeNode`` /
``encodeNode`` / ``decodeOp`` / ``encodeOp`` ops reproduce the canonical form and
the canonical reject code/path — so the kit report (Leg via the JS bridge) and
the native ``pytest`` harness certify the *same* codec and cannot disagree.

The full-corpus legs drive ``handle()`` in-process (fast); a subprocess smoke
test exercises the actual ``python -m fuaran_py.conformance.bridge`` stdio path
the adapter uses.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from _corpus import CORPUS_ROOT, corpus_required, fixtures_of
from fuaran_py.conformance.bridge import handle


def _canon(text: str) -> str:
    return text[:-1] if text.endswith("\n") else text


@corpus_required
@pytest.mark.parametrize(
    "fixture",
    fixtures_of("node-round-trip", "op-round-trip", "lenient-accept"),
    ids=lambda fx: fx["id"],
)
def test_bridge_roundtrip(fixture: dict) -> None:
    decoder = fixture.get("decoder", "node")
    decode_op_name = "decodeOp" if decoder == "op" else "decodeNode"
    encode_op_name = "encodeOp" if decoder == "op" else "encodeNode"
    input_text = (CORPUS_ROOT / fixture["inputFile"]).read_text(encoding="utf-8")

    decoded = handle({"op": decode_op_name, "input": input_text})
    assert decoded["ok"], f"{fixture['id']}: bridge {decode_op_name} rejected an accept fixture: {decoded}"

    encoded = handle({"op": encode_op_name, "input": input_text})
    assert encoded["ok"], f"{fixture['id']}: bridge {encode_op_name} failed: {encoded}"
    expected = _canon((CORPUS_ROOT / fixture["expectedFile"]).read_text(encoding="utf-8"))
    assert encoded["value"] == expected, f"{fixture['id']}: bridge output diverges from the canonical form"


@corpus_required
@pytest.mark.parametrize("fixture", fixtures_of("reject"), ids=lambda fx: fx["id"])
def test_bridge_reject(fixture: dict) -> None:
    decode_op_name = "decodeOp" if fixture.get("decoder") == "op" else "decodeNode"
    input_text = (CORPUS_ROOT / fixture["inputFile"]).read_text(encoding="utf-8")

    decoded = handle({"op": decode_op_name, "input": input_text})
    assert not decoded["ok"], f"{fixture['id']}: bridge accepted a reject fixture"
    assert decoded["error"]["code"] == fixture["expectedErrorCode"], (
        f"{fixture['id']}: code {decoded['error']['code']} != {fixture['expectedErrorCode']}"
    )
    assert decoded["error"]["path"].startswith(fixture.get("expectedPath", "$")), (
        f"{fixture['id']}: path {decoded['error']['path']!r} !startswith {fixture.get('expectedPath')!r}"
    )


def test_bridge_unknown_op_is_reported() -> None:
    res = handle({"op": "nope", "input": "{}"})
    assert res["ok"] is False
    assert res["error"]["code"] == "INVALID_JSON"


@corpus_required
def test_bridge_subprocess_stdio_path() -> None:
    """The actual `python -m fuaran_py.conformance.bridge` stdio path the .mjs adapter shells."""
    fixtures = fixtures_of("node-round-trip")
    assert fixtures, "expected at least one node-round-trip fixture"
    fixture = fixtures[0]
    input_text = (CORPUS_ROOT / fixture["inputFile"]).read_text(encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "fuaran_py.conformance.bridge"],
        input=json.dumps({"op": "encodeNode", "input": input_text}),
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads(proc.stdout)
    assert result["ok"], f"subprocess bridge failed: {result}"
    expected = _canon((CORPUS_ROOT / fixture["expectedFile"]).read_text(encoding="utf-8"))
    assert result["value"] == expected
