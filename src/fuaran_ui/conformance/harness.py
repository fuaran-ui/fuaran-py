"""A minimal corpus round-trip harness (smoke-level).

Loads the workspace conformance corpus ``manifest.json`` and runs each fixture's
assertion against this host:

* ``node-round-trip`` / ``op-round-trip`` → ``encode(decode(input))`` is
  byte-identical to ``expectedFile`` (the canonical form);
* ``reject`` → ``decode(input)`` fails with the manifest's ``expectedErrorCode``
  and a path that starts with ``expectedPath``.

This is the inner-loop smoke harness for the bootstrap. The full certification
harness (corpus snapshot + drift guard, schema validation, the JS-bridge kit,
CI leg, and generative parity) is a follow-up.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..dag import decode_dag_record, encode_dag_record
from ..ops import decode_op, encode_op
from ..schema import decode_node, encode_node


@dataclass(frozen=True)
class FixtureResult:
    fixture_id: str
    kind: str
    passed: bool
    detail: str


def _canon(text: str) -> str:
    # Strip at most one trailing newline (mirrors the cross-host runner's canon).
    return text[:-1] if text.endswith("\n") else text


def _first_diff(a: str, b: str) -> str:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            lo = max(0, i - 12)
            return f"first diff at byte {i}: expected …{b[lo : i + 12]!r}… got …{a[lo : i + 12]!r}…"
    if len(a) != len(b):
        return f"length differs: got {len(a)}, expected {len(b)}"
    return "identical"


def run_fixture(fixture: dict, corpus_root: Path) -> FixtureResult:
    fid = fixture["id"]
    kind = fixture["kind"]
    decoder = fixture.get("decoder", "node")
    decode: Callable[[str], Any] = decode_node if decoder == "node" else decode_op
    encode: Callable[[Any], str] = encode_node if decoder == "node" else encode_op

    input_text = (corpus_root / fixture["inputFile"]).read_text(encoding="utf-8")

    if kind == "dag-record-round-trip":
        dag_result: Any = decode_dag_record(input_text)
        if not dag_result.ok:
            return FixtureResult(fid, kind, False, f"expected decode to succeed, got {dag_result.error}")
        reencoded = encode_dag_record(dag_result.value)
        expected = _canon((corpus_root / fixture["expectedFile"]).read_text(encoding="utf-8"))
        if reencoded == expected:
            return FixtureResult(fid, kind, True, "byte-identical round-trip")
        return FixtureResult(fid, kind, False, _first_diff(reencoded, expected))

    # lenient-accept (WIRE_FORMAT §16, normative): the shorthand inputFile MUST
    # decode, and MUST re-encode to the verbose canonical expectedFile — the
    # same decode → encode → byte-compare leg as a round-trip fixture, with
    # inputFile ≠ expectedFile.
    if kind in ("node-round-trip", "op-round-trip", "lenient-accept"):
        result = decode(input_text)
        if not result.ok:
            return FixtureResult(fid, kind, False, f"expected decode to succeed, got {result.error}")
        reencoded = encode(result.value)
        expected = _canon((corpus_root / fixture["expectedFile"]).read_text(encoding="utf-8"))
        if reencoded == expected:
            return FixtureResult(fid, kind, True, "byte-identical round-trip")
        return FixtureResult(fid, kind, False, _first_diff(reencoded, expected))

    if kind == "reject":
        result = decode(input_text)
        if result.ok:
            return FixtureResult(fid, kind, False, "expected decode to fail, but it succeeded")
        want_code = fixture["expectedErrorCode"]
        want_path = fixture.get("expectedPath", "$")
        err = result.error
        if err.code != want_code:
            return FixtureResult(fid, kind, False, f"code {err.code} != expected {want_code}")
        if not err.path.startswith(want_path):
            return FixtureResult(fid, kind, False, f"path {err.path!r} does not start with {want_path!r}")
        return FixtureResult(fid, kind, True, f"{err.code} at {err.path}")

    return FixtureResult(fid, kind, False, f"unknown fixture kind {kind!r}")


def run_corpus(corpus_root: Path) -> list[FixtureResult]:
    manifest = json.loads((corpus_root / "manifest.json").read_text(encoding="utf-8"))
    return [run_fixture(fx, corpus_root) for fx in manifest["fixtures"]]


def run_dag_corpus(dag_root: Path) -> list[FixtureResult]:
    """Run the additive ``dag/`` sub-corpus (its own ``manifest.json``), whose
    fixtures are ``dag-record-round-trip`` and resolve relative to ``dag_root``."""
    manifest = json.loads((dag_root / "manifest.json").read_text(encoding="utf-8"))
    return [run_fixture(fx, dag_root) for fx in manifest["fixtures"]]
