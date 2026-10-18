"""The ``decimal`` law family — the exact decimal's text, order, sum, float reading, codec and
aggregates, over AUTHORED inputs (Phase 2001).

``laws/decimal-laws.json`` is read THROUGH ``laws/manifest.json``: the file must agree with its
manifest row (family id, kit version, vector count). Every vector of every case runs; an ``accept``
vector compares this host's answer with the reference's, and a ``reject`` vector requires this host
to refuse. The refusal CLASS is compared only where this host has a matching one — the column
codec's ``TypeMismatch`` and ``MalformedShape`` — and otherwise refusal alone is asserted. The set of
case kinds is pinned, so a new kind fails here rather than going unrun.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui.canonical import format_finite_double
from fuaran_ui.dataframe import (
    NULL,
    Cell,
    Column,
    Embedded,
    Table,
    aggregate,
    cell_decimal,
    cell_int,
    cell_token,
    decimal_text,
    decode_source,
    encode_source,
    try_encode_source,
)
from fuaran_ui.dataframe.model import DECIMAL, MALFORMED_SHAPE, TYPE_MISMATCH

FAMILY = "decimal"

#: The reference's refusal classes this host spells too (the column codec's envelope). A class not
#: here is a refusal this host makes under another name, so only the refusal itself is asserted.
MATCHING_CLASSES = {"TypeMismatch": TYPE_MISMATCH, "MalformedShape": MALFORMED_SHAPE}


def _family() -> tuple[dict, dict]:
    laws = CORPUS_ROOT / "laws"
    manifest = json.loads((laws / "manifest.json").read_text(encoding="utf-8"))
    rows = [f for f in manifest["families"] if f["id"] == FAMILY]
    assert len(rows) == 1, f"laws/manifest.json declares {len(rows)} {FAMILY} families"
    return rows[0], json.loads((laws / rows[0]["file"]).read_text(encoding="utf-8"))


ROW, FILE = _family()
VECTORS: list[dict] = FILE["vectors"]


def _cells(spec: list[dict]) -> list[Cell]:
    """A vector's ``cells`` — ``{kind, text}``, a null cell carrying no text — as this host's cells,
    the decimal text kept AS GIVEN so a non-canonical one reaches the guard under test."""
    out: list[Cell] = []
    for c in spec:
        if c["kind"] == "null":
            out.append(NULL)
        elif c["kind"] == "decimal":
            out.append(cell_decimal(c["text"]))
        elif c["kind"] == "int":
            out.append(cell_int(int(c["text"])))
        else:
            raise AssertionError(f"unknown cell kind {c['kind']!r}")
    return out


def _decimal_column(spec: list[dict]) -> Column:
    return Column("c", DECIMAL, _cells(spec))


def _answer_canonical(i: dict) -> tuple[object, str | None]:
    text = decimal_text.try_canonical(i["text"])
    return text, None


def _answer_compare(i: dict) -> tuple[object, str | None]:
    return decimal_text.compare(i["a"], i["b"]), None


def _answer_add(i: dict) -> tuple[object, str | None]:
    return decimal_text.add(i["a"], i["b"]), None


def _answer_to_float(i: dict) -> tuple[object, str | None]:
    f = decimal_text.to_float(i["text"])
    return (None if f is None else format_finite_double(f)), None


def _answer_codec_decode(i: dict) -> tuple[object, str | None]:
    r = decode_source(i["document"])
    return (encode_source(r.value), None) if r.ok else (None, r.error.code)


def _answer_codec_encode(i: dict) -> tuple[object, str | None]:
    table = Table([("c", DECIMAL)], [_decimal_column(i["cells"])])
    r = try_encode_source(Embedded(table))
    return (r.value, None) if r.ok else (None, r.error.code)


def _answer_aggregate(i: dict) -> tuple[object, str | None]:
    r = aggregate(i["fn"], _decimal_column(i["cells"]))
    return (cell_token(r.value), None) if r.ok else (None, r.error.code)


#: Each case kind: how this host answers it, and the member of ``expected`` holding the answer.
CASES: dict[str, tuple[Callable[[dict], tuple[object, str | None]], str]] = {
    "canonical": (_answer_canonical, "canonical"),
    "compare": (_answer_compare, "order"),
    "add": (_answer_add, "sum"),
    "toFloat": (_answer_to_float, "float"),
    "codecDecode": (_answer_codec_decode, "canonical"),
    "codecEncode": (_answer_codec_encode, "canonical"),
    "aggregate": (_answer_aggregate, "token"),
}


def test_the_file_agrees_with_its_manifest_row() -> None:
    assert FILE["family"] == ROW["id"]
    assert FILE["kitVersion"] == ROW["kitVersion"]
    assert len(VECTORS) == ROW["vectors"]


def test_the_case_kinds_are_exactly_the_known_set() -> None:
    assert {v["case"] for v in VECTORS} == set(CASES)


@pytest.mark.parametrize("vector", VECTORS, ids=[v["id"] for v in VECTORS])
def test_decimal_vector(vector: dict) -> None:
    answer, member = CASES[vector["case"]]
    got, refusal = answer(vector["input"])
    expected = vector["expected"]
    if expected["verdict"] == "reject":
        assert got is None, f"{vector['id']}: this host answered {got!r}, the reference refused"
        mine = MATCHING_CLASSES.get(expected["error"])
        if mine is not None:
            assert refusal == mine, f"{vector['id']}: refused as {refusal}, the reference as {expected['error']}"
        return
    assert expected["verdict"] == "accept", f"{vector['id']}: unknown verdict {expected['verdict']!r}"
    assert got == expected[member], f"{vector['id']}: this host ≠ the reference"
