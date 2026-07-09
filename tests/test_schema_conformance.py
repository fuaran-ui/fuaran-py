"""Schema-validity leg: this host's re-encoded output validates against the
canonical Draft 2020-12 JSON Schema (WIRE_FORMAT.md §13).

The cross-host runner's Leg B does two things per accept-fixture: byte-compare
the re-encoded output against the F# canonical form (proved here by
``test_roundtrip``), *and* validate that output against ``schema.json`` with an
off-the-shelf Draft 2020-12 validator. This module is the native ``pytest``
mirror of that second check — it proves the Python host emits **schema-valid**
wire, not merely F#-byte-identical wire (belt-and-suspenders: an encoder bug
that happened to match the corpus bytes but violated the schema would still be
caught, and vice-versa).

The validator (``jsonschema``) is a **dev-only** dependency — the runtime codec
stays standard-library-only per ``CLAUDE.md``. The schema is scoped to the
canonical ``Node`` / ``TreeOp`` shapes (its top level is ``oneOf: [Node,
TreeOp]``), so this leg covers the ``node-round-trip`` / ``op-round-trip`` /
``lenient-accept`` families — the ones that re-encode to a pure canonical
Node/TreeOp form. Envelope / DAG / merge / markdown fixtures are separate
top-level shapes and are certified by their own suites.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT, corpus_required, fixtures_of
from fuaran_py.ops import decode_op, encode_op
from fuaran_py.schema import decode_node, encode_node

jsonschema = pytest.importorskip(
    "jsonschema",
    reason="jsonschema (dev extra) is required for the schema-validity leg",
)


def _schema_validator() -> object:
    schema = json.loads((CORPUS_ROOT / "schema.json").read_text(encoding="utf-8"))
    # Draft 2020-12 is pinned by the schema's own $schema keyword; construct the
    # matching validator explicitly so the leg fails loud rather than silently
    # falling back to an older meta-schema.
    return jsonschema.Draft202012Validator(schema)


@corpus_required
@pytest.mark.parametrize(
    "fixture",
    fixtures_of("node-round-trip", "op-round-trip", "lenient-accept"),
    ids=lambda fx: fx["id"],
)
def test_reencoded_output_is_schema_valid(fixture: dict) -> None:
    validator = _schema_validator()
    input_text = (CORPUS_ROOT / fixture["inputFile"]).read_text(encoding="utf-8")

    if fixture.get("decoder", "node") == "op":
        decoded = decode_op(input_text)
        assert decoded.ok, f"{fixture['id']}: decode failed: {decoded.error}"
        reencoded = encode_op(decoded.value)
    else:
        decoded = decode_node(input_text)
        assert decoded.ok, f"{fixture['id']}: decode failed: {decoded.error}"
        reencoded = encode_node(decoded.value)

    instance = json.loads(reencoded)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.absolute_path))
    assert not errors, f"{fixture['id']}: schema violations:\n" + "\n".join(
        f"  {list(e.absolute_path) or '$'}: {e.message}" for e in errors
    )


@corpus_required
def test_schema_pins_draft_2020_12() -> None:
    schema = json.loads((CORPUS_ROOT / "schema.json").read_text(encoding="utf-8"))
    assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
