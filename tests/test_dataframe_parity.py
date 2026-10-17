"""The Compute-layer Python leg, certified against the reference evaluator's own answers.

The oracle is the corpus ``laws/`` family ``dataframeParity`` (Phase 1938): one named
(source, pipeline) pair per evaluator feature, each answer computed by the reference
evaluator at emit time and the file carried with a recorded producer commit in
``laws/manifest.json``. (Until Phase 1938 this host carried the same cases as its own
``tests/fixtures/dataframe_parity.json``, with hand-recorded answers and no producer.)
Three legs pin the Python host to it:

* **codec round-trip** — ``encode(decode(source))`` and ``encode(decode(pipeline))`` are
  byte-identical to the reference's canonical wire;
* **evaluator parity** — decode the source + pipeline, evaluate with the Python
  evaluator, encode the result, and assert it is **byte-identical** to the reference's
  ``expected.table`` (or that both sides refuse);
* **totality** — the evaluator returns a structured result, never throws.

The family travels in the bundled snapshot (``conformance/sync_corpus.py``), so the leg
reads ``CORPUS_ROOT`` and never skips.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui.dataframe import (
    Embedded,
    decode_pipeline,
    decode_source,
    encode_pipeline,
    encode_source,
    eval_pipeline,
)

FAMILY = "dataframeParity"


def _vectors() -> list[dict]:
    laws = CORPUS_ROOT / "laws"
    manifest = json.loads((laws / "manifest.json").read_text(encoding="utf-8"))
    rows = [f for f in manifest["families"] if f["id"] == FAMILY]
    assert len(rows) == 1, f"laws/manifest.json declares {len(rows)} {FAMILY} families"
    vectors = json.loads((laws / rows[0]["file"]).read_text(encoding="utf-8"))["vectors"]
    assert vectors, f"the {FAMILY} family carries no vectors"
    return vectors


VECTORS = _vectors()
IDS = [v["id"] for v in VECTORS]


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_source_codec_round_trip(vector: dict) -> None:
    source = vector["input"]["source"]
    decoded = decode_source(source)
    assert decoded.ok, f"{vector['id']}: source decode failed: {getattr(decoded, 'error', None)}"
    assert encode_source(decoded.value) == source


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_pipeline_codec_round_trip(vector: dict) -> None:
    pipeline = vector["input"]["pipeline"]
    decoded = decode_pipeline(pipeline)
    assert decoded.ok, f"{vector['id']}: pipeline decode failed: {getattr(decoded, 'error', None)}"
    assert encode_pipeline(decoded.value) == pipeline


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_evaluator_byte_identical_to_reference(vector: dict) -> None:
    src = decode_source(vector["input"]["source"])
    pipe = decode_pipeline(vector["input"]["pipeline"])
    assert src.ok and pipe.ok
    assert isinstance(src.value, Embedded)

    result = eval_pipeline(pipe.value, src.value.table)
    expected = vector["expected"]

    if expected["verdict"] == "ok":
        assert result.ok, f"{vector['id']}: evaluator errored, reference succeeded: {getattr(result, 'error', None)}"
        wire = encode_source(Embedded(result.value))
        assert wire == expected["table"], f"{vector['id']}: evaluator ≠ reference"
    else:
        assert not result.ok, f"{vector['id']}: evaluator succeeded, reference refused"
