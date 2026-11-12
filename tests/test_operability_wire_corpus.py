"""Phase 2517 - the corpus's ``operability/`` family, wire section (WIRE_FORMAT 31.1, 31.2).

This host's codec carries the ``affordance`` member on ``Select``, ``Button`` and every form field,
and refuses a pattern the carrying kind's slot rule forbids. The family's ``wire.roundTrip`` vectors
must re-encode byte for byte and its ``wire.reject`` vectors must refuse with the recorded code at
the recorded path. The derivation and matcher sections (31.3-31.5) are not ported, which is why the
capability manifest declares the family ``pending`` rather than covered.

The family is read from the corpus root; it is not part of the bundled snapshot's payload, so the
tests skip when the authority is absent.
"""

from __future__ import annotations

import json

import pytest

from _corpus import AUTHORITY_ROOT
from fuaran_ui import decode_node, encode_node

_VECTORS = AUTHORITY_ROOT / "operability" / "operability-vectors.json"

pytestmark = pytest.mark.skipif(not _VECTORS.is_file(), reason=f"operability family not found at {_VECTORS}")


def _wire() -> dict:
    return json.loads(_VECTORS.read_text(encoding="utf-8"))["wire"] if _VECTORS.is_file() else {}


_ROUND_TRIP = _wire().get("roundTrip", [])
_REJECT = _wire().get("reject", [])


def test_the_family_carries_both_wire_sections() -> None:
    assert _ROUND_TRIP
    assert _REJECT


@pytest.mark.parametrize("vector", _ROUND_TRIP, ids=lambda v: v["id"])
def test_round_trip_is_byte_identical(vector: dict) -> None:
    decoded = decode_node(vector["canonical"])
    assert decoded.ok, getattr(decoded, "error", decoded)
    assert encode_node(decoded.value) == vector["canonical"]


@pytest.mark.parametrize("vector", _REJECT, ids=lambda v: v["id"])
def test_reject_refuses_with_the_recorded_code_and_path(vector: dict) -> None:
    decoded = decode_node(vector["input"])
    assert not decoded.ok
    assert decoded.error.code == vector["expectedErrorCode"]
    assert decoded.error.path.startswith(vector["expectedPath"])
