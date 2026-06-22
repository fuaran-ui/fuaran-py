"""Phase 285 — the Compute-layer Python leg, certified against the F# reference.

The ``tests/fixtures/dataframe_parity.json`` corpus is a serialized conformance report
from the F# reference dataframe evaluator: each case carries a ``source`` and a
``pipeline`` in the canonical wire form the F# encoder produced, plus the ``expected``
result the F# *reference evaluator* produced (its ``DataSource`` wire). Three legs pin
the Python host to it:

* **codec round-trip** — ``encode(decode(source))`` and ``encode(decode(pipeline))`` are
  byte-identical to the F# canonical wire (the Python leg of cross-host conformance);
* **evaluator parity** — decode the source + pipeline, evaluate with the Python
  reference evaluator, encode the result, and assert it is **byte-identical** to the F#
  reference's ``expected`` output (or that both sides error);
* **totality** — the evaluator returns a structured result, never throws.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fuaran_py.dataframe import (
    Embedded,
    decode_pipeline,
    decode_source,
    encode_pipeline,
    encode_source,
    eval_pipeline,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dataframe_parity.json"


def _cases() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


CASES = _cases()
IDS = [c["id"] for c in CASES]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_source_codec_round_trip(case: dict) -> None:
    decoded = decode_source(case["source"])
    assert decoded.ok, f"{case['id']}: source decode failed: {getattr(decoded, 'error', None)}"
    assert encode_source(decoded.value) == case["source"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_pipeline_codec_round_trip(case: dict) -> None:
    decoded = decode_pipeline(case["pipeline"])
    assert decoded.ok, f"{case['id']}: pipeline decode failed: {getattr(decoded, 'error', None)}"
    assert encode_pipeline(decoded.value) == case["pipeline"]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_evaluator_byte_identical_to_reference(case: dict) -> None:
    src = decode_source(case["source"])
    pipe = decode_pipeline(case["pipeline"])
    assert src.ok and pipe.ok
    assert isinstance(src.value, Embedded)

    result = eval_pipeline(pipe.value, src.value.table)

    if case["ok"]:
        assert result.ok, f"{case['id']}: evaluator errored, reference succeeded: {getattr(result, 'error', None)}"
        wire = encode_source(Embedded(result.value))
        assert wire == case["expected"], f"{case['id']}: evaluator ≠ reference"
    else:
        assert not result.ok, f"{case['id']}: evaluator succeeded, reference errored"
