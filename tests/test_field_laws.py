"""The ``fieldLaws`` law family — a schema entry's metadata (unit, label, description, ext), its
codec, and the schema operations that read it, over AUTHORED inputs (Phase 2207, WIRE_FORMAT
§3.6.26).

``laws/field-laws.json`` is read THROUGH ``laws/manifest.json``: the file must agree with its
manifest row (family id, kit version, vector count). Every vector of every case runs; an ``accept``
vector compares this host's answer with the reference's, member by member, and a ``reject`` vector
requires this host to refuse. The refusal CLASS is compared only where this host has a matching one
— the column codec's ``TypeMismatch`` and ``MalformedShape`` — and otherwise refusal alone is
asserted. The set of case kinds is pinned, so a new kind fails here rather than going unrun. The
``fingerprint``, ``delta`` and ``classify`` inputs are schemas in wire form, read through this
host's own schema-entry decoder.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui.dataframe import (
    Field,
    FieldMeta,
    UnitRefusal,
    classify,
    decode_schema,
    decode_source,
    diff,
    encode_delta,
    encode_field,
    encode_source,
    fingerprint,
    parse_unit,
    patch,
)
from fuaran_ui.dataframe.model import MALFORMED_SHAPE, TYPE_MISMATCH

FAMILY = "fieldLaws"

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

#: An answer: the members of ``expected`` it computed, or the refusal's class.
type Answer = tuple[dict[str, object] | None, str | None]


def _schema(wire: list[dict]) -> list[Field]:
    """A wire-form schema through this host's own schema-entry decoder; a vector's input schema is
    one the reference accepts, so a refusal here is this host's defect."""
    r = decode_schema(wire)
    assert r.ok, f"this host refused an input schema the reference reads: {r}"
    return r.value


def _answer_field_encode(i: dict) -> Answer:
    unit = None
    if "unit" in i:
        parsed = parse_unit(i["unit"])
        assert not isinstance(parsed, UnitRefusal), f"unit {i['unit']!r} refused: {parsed}"
        unit = parsed
    meta = FieldMeta(unit, i.get("label"), i.get("description"), dict(i.get("ext", {})))
    return {"canonical": encode_field(Field(i["name"], i["type"], meta))}, None


def _answer_codec_decode(i: dict) -> Answer:
    r = decode_source(i["document"])
    return ({"canonical": encode_source(r.value)}, None) if r.ok else (None, r.error.code)


def _answer_fingerprint(i: dict) -> Answer:
    return {"fingerprint": fingerprint(_schema(i["schema"]))}, None


def _answer_delta(i: dict) -> Answer:
    old, target = _schema(i["old"]), _schema(i["target"])
    delta = diff(old, target)
    patched = patch(old, delta)
    assert patched.ok, f"patch refused its own diff: {patched}"
    assert patched.value == target, "patch(old, diff(old, target)) is not target"
    return {
        "canonical": encode_delta(delta),
        "patched": [json.loads(encode_field(f)) for f in patched.value],
    }, None


def _answer_classify(i: dict) -> Answer:
    delta = diff(_schema(i["old"]), _schema(i["target"]))
    return {"compat": classify(i["dependsOn"], delta).verdict}, None


#: Each case kind: how this host answers it.
CASES: dict[str, Callable[[dict], Answer]] = {
    "fieldEncode": _answer_field_encode,
    "codecDecode": _answer_codec_decode,
    "fingerprint": _answer_fingerprint,
    "delta": _answer_delta,
    "classify": _answer_classify,
}


def test_the_file_agrees_with_its_manifest_row() -> None:
    assert FILE["family"] == ROW["id"]
    assert FILE["kitVersion"] == ROW["kitVersion"]
    assert len(VECTORS) == ROW["vectors"]


def test_the_case_kinds_are_exactly_the_known_set() -> None:
    assert {v["case"] for v in VECTORS} == set(CASES)


@pytest.mark.parametrize("vector", VECTORS, ids=[v["id"] for v in VECTORS])
def test_field_vector(vector: dict) -> None:
    got, refusal = CASES[vector["case"]](vector["input"])
    expected = vector["expected"]
    if expected["verdict"] == "reject":
        assert got is None, f"{vector['id']}: this host answered {got!r}, the reference refused"
        mine = MATCHING_CLASSES.get(expected["error"])
        if mine is not None:
            assert refusal == mine, f"{vector['id']}: refused as {refusal}, the reference as {expected['error']}"
        return
    assert expected["verdict"] == "accept", f"{vector['id']}: unknown verdict {expected['verdict']!r}"
    assert got is not None, f"{vector['id']}: this host refused ({refusal}), the reference accepted"
    assert set(got) == set(expected) - {"verdict"}, f"{vector['id']}: the answered members are not the expected ones"
    for member, answer in got.items():
        assert answer == expected[member], f"{vector['id']}: {member}: this host ≠ the reference"
