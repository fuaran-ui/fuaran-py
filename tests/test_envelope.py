"""Conformance + unit tests for the §15 wire versioning envelope.

Certifies :mod:`fuaran_py.envelope` against the shared ``envelope-*`` corpus
family (round-trip byte-identical; Foreign hard-refuse) and mirrors the Go
``wire/envelope_test`` negotiation behaviour case-for-case.
"""

from __future__ import annotations

import pytest

from _corpus import CORPUS_ROOT, corpus_required, fixtures_of
from fuaran_py.envelope import (
    BEHIND,
    CURRENT,
    FOREIGN,
    FOREIGN_PROFILE,
    Envelope,
    decode_envelope,
    encode_envelope,
    negotiate,
)


def _read(rel: str) -> str:
    return (CORPUS_ROOT / rel).read_text(encoding="utf-8")


@corpus_required
@pytest.mark.parametrize("fx", fixtures_of("envelope-round-trip"), ids=lambda fx: fx["id"])
def test_envelope_round_trip_byte_identical(fx: dict) -> None:
    result = decode_envelope(_read(fx["inputFile"]))
    assert result.ok, f"{fx['id']} should decode: {getattr(result, 'error', None)}"
    reencoded = encode_envelope(result.value)
    assert reencoded == _read(fx["expectedFile"]), f"{fx['id']} not byte-identical"


@corpus_required
@pytest.mark.parametrize("fx", fixtures_of("envelope-reject"), ids=lambda fx: fx["id"])
def test_envelope_reject(fx: dict) -> None:
    result = decode_envelope(_read(fx["inputFile"]))
    assert not result.ok, f"{fx['id']} should refuse"
    assert result.error.code == fx["expectedErrorCode"]
    assert result.error.path.startswith(fx["expectedPath"])


# ── Negotiation unit tests (mirror the Go wire/envelope behaviour) ──────────


def test_negotiate_current_behind_foreign() -> None:
    assert negotiate("core@1.0") == CURRENT
    assert negotiate("core@1.1") == BEHIND  # authored minor ahead — tolerate
    assert negotiate("core@2.0") == FOREIGN  # major ahead — refuse
    assert negotiate("music@1.0") == FOREIGN  # different namespace — refuse
    assert negotiate("core@1") == FOREIGN  # malformed (no minor)
    assert negotiate("garbage") == FOREIGN


def test_behind_unknown_kind_preserved_verbatim() -> None:
    # A Behind consumer meets an unknown kind → must-ignore-but-preserve.
    src = '{"$payload":{"id":"h1","kind":{"$type":"hologram"},"shimmer":true},"$profile":"core@1.1"}'
    result = decode_envelope(src)
    assert result.ok
    assert result.value.negotiation == BEHIND
    assert encode_envelope(result.value) == src  # verbatim re-encode


def test_foreign_refuses_at_profile_path() -> None:
    result = decode_envelope('{"$payload":{"id":"a","kind":{"$type":"Skeleton","rows":1}},"$profile":"core@2.0"}')
    assert not result.ok
    assert result.error.code == FOREIGN_PROFILE
    assert result.error.path == "$.$profile"


def test_missing_profile_and_payload() -> None:
    r1 = decode_envelope('{"$payload":{"id":"a","kind":{"$type":"Skeleton","rows":1}}}')
    assert not r1.ok and r1.error.path == "$.$profile"
    r2 = decode_envelope('{"$profile":"core@1.0"}')
    assert not r2.ok and r2.error.path == "$.$payload"


def test_current_round_trip_via_constructed_envelope() -> None:
    # 0.2.0 — the Literal envelope stays decode-accepted and normalises to the
    # bare-string canonical form on re-encode.
    src = (
        '{"$payload":{"id":"m1","kind":{"$type":"Markdown","text":{"$type":"Literal","text":"hi"}}},'
        '"$profile":"core@1.0"}'
    )
    canonical = '{"$payload":{"id":"m1","kind":{"$type":"Markdown","text":"hi"}},"$profile":"core@1.0"}'
    result = decode_envelope(src)
    assert result.ok and result.value.negotiation == CURRENT
    assert isinstance(result.value, Envelope)
    assert encode_envelope(result.value) == canonical
