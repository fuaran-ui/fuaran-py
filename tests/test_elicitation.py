"""Conformance + unit tests for the §18 elicitation artefact.

Certifies :mod:`fuaran_py.elicitation` against the shared ``elicitation-*`` corpus
families (envelope round-trip, outcome round-trip, structured rejects, answer
accept/reject) and mirrors the Go ``elicitation`` package's unit behaviour.
"""

from __future__ import annotations

import pytest

from _corpus import CORPUS_ROOT, corpus_required, fixtures_of
from fuaran_py.elicitation import (
    ANSWER_OUT_OF_SPACE,
    ANSWER_TYPE_MISMATCH,
    UNDECLARED_FIELD,
    decode_answer_doc,
    decode_elicitation,
    decode_outcome,
    encode_elicitation,
    encode_outcome,
)


def _read(rel: str) -> str:
    return (CORPUS_ROOT / rel).read_text(encoding="utf-8")


def _decode(decoder: str, text: str):
    if decoder == "elicitation":
        return decode_elicitation(text)
    if decoder == "elicitation-outcome":
        return decode_outcome(text)
    if decoder == "elicitation-answer":
        return decode_answer_doc(text)
    raise AssertionError(f"unknown decoder {decoder}")


def _encode(decoder: str, value) -> str:
    if decoder == "elicitation":
        return encode_elicitation(value)
    if decoder == "elicitation-outcome":
        return encode_outcome(value)
    raise AssertionError(f"no encoder for {decoder}")


@corpus_required
@pytest.mark.parametrize("fx", fixtures_of("elicitation-round-trip"), ids=lambda fx: fx["id"])
def test_elicitation_round_trip_byte_identical(fx: dict) -> None:
    result = _decode(fx["decoder"], _read(fx["inputFile"]))
    assert result.ok, f"{fx['id']} should decode: {getattr(result, 'error', None)}"
    assert _encode(fx["decoder"], result.value) == _read(fx["expectedFile"]), f"{fx['id']} not byte-identical"


@corpus_required
@pytest.mark.parametrize("fx", fixtures_of("elicitation-reject"), ids=lambda fx: fx["id"])
def test_elicitation_reject(fx: dict) -> None:
    result = _decode(fx["decoder"], _read(fx["inputFile"]))
    assert not result.ok, f"{fx['id']} should refuse"
    assert result.error.code == fx["expectedErrorCode"], f"{fx['id']}: {result.error.code}"
    assert result.error.path.startswith(fx["expectedPath"]), f"{fx['id']}: {result.error.path}"


@corpus_required
@pytest.mark.parametrize("fx", fixtures_of("elicitation-answer-accept"), ids=lambda fx: fx["id"])
def test_elicitation_answer_accept(fx: dict) -> None:
    result = decode_answer_doc(_read(fx["inputFile"]))
    assert result.ok, f"{fx['id']} should accept: {getattr(result, 'error', None)}"


@corpus_required
@pytest.mark.parametrize("fx", fixtures_of("elicitation-answer-reject"), ids=lambda fx: fx["id"])
def test_elicitation_answer_reject(fx: dict) -> None:
    result = decode_answer_doc(_read(fx["inputFile"]))
    assert not result.ok, f"{fx['id']} should refuse"
    assert result.error.code == fx["expectedErrorCode"], f"{fx['id']}: {result.error.code}"
    assert result.error.path.startswith(fx["expectedPath"]), f"{fx['id']}: {result.error.path}"


# ── Unit tests mirroring the Go elicitation_test.go ─────────────────────────


def test_outcome_round_trip() -> None:
    src = '{"$type":"Answered","answer":{"grade":"a","salary":52000},"elicitationId":"elc-full"}'
    result = decode_outcome(src)
    assert result.ok
    assert result.value.kind == "Answered" and result.value.elicitation_id == "elc-full"
    assert encode_outcome(result.value) == src


def test_declined_rejects_smuggled_answer() -> None:
    result = decode_outcome('{"$type":"Declined","answer":{"x":1},"elicitationId":"e"}')
    assert not result.ok and result.error.code == UNDECLARED_FIELD


def test_answer_doc_accept_and_reject() -> None:
    contract = (
        '"contract":{"fields":[{"name":"rating","nodeId":"n","required":true,'
        '"space":{"$type":"intRange","max":5,"min":1},"stateKey":"r"}]}'
    )
    assert decode_answer_doc('{"answer":{"rating":4},' + contract + "}").ok
    out_of_range = decode_answer_doc('{"answer":{"rating":9},' + contract + "}")
    assert not out_of_range.ok
    assert out_of_range.error.code == ANSWER_OUT_OF_SPACE and out_of_range.error.path == "$.answer.rating"
    type_mismatch = decode_answer_doc('{"answer":{"rating":"4"},' + contract + "}")
    assert not type_mismatch.ok and type_mismatch.error.code == ANSWER_TYPE_MISMATCH


def test_int_satisfies_float_range() -> None:
    # JSON has one number type — a whole-valued number satisfies a floatRange (§18.4).
    contract = (
        '"contract":{"fields":[{"name":"score","nodeId":"n","required":true,'
        '"space":{"$type":"floatRange","max":1,"min":0},"stateKey":"s"}]}'
    )
    assert decode_answer_doc('{"answer":{"score":1},' + contract + "}").ok
