"""Phase 2484 — an ``int`` column value is a signed 32-bit integer (WIRE_FORMAT §3.6.28).

The corpus certifies the wire path; these pin the column codec's own reader, its inference,
and the authoring surface (which writes the same columns) to the same rule."""

from __future__ import annotations

import json

import pytest

from fuaran_ui.dataframe import Embedded, decode_source
from fuaran_ui.ui import frame


def _source(values: list[object], ty: str | None = "int") -> str:
    src: dict[str, object] = {"columns": {"n": {"validity": [True] * len(values), "values": values}}}
    if ty is not None:
        src["schema"] = [{"name": "n", "type": ty}]
    return json.dumps(src)


@pytest.mark.parametrize("text", ["2147483647", "-2147483648", "2.0", "-3.0", "0"])
def test_an_int_column_accepts_a_whole_value_inside_int32(text: str) -> None:
    r = decode_source(_source([json.loads(text)]))
    assert r.ok and isinstance(r.value, Embedded)


@pytest.mark.parametrize("text", ["2147483648", "-2147483649", "2.5", "1e10"])
def test_an_int_column_refuses_anything_else(text: str) -> None:
    r = decode_source(_source([json.loads(text)]))
    assert not r.ok and r.error.code == "TYPE_MISMATCH"


@pytest.mark.parametrize(
    ("values", "expected"),
    [([1, 2147483648], "float"), ([2.0, -2147483648], "int"), ([1, 2.5], "float")],
)
def test_inference_reads_a_number_by_value(values: list[object], expected: str) -> None:
    r = decode_source(json.dumps({"columns": {"n": values}}))
    assert r.ok and isinstance(r.value, Embedded)
    assert r.value.table.schema[0][1] == expected


def test_authoring_infers_float_past_int32_and_refuses_a_declared_int_past_it() -> None:
    assert frame({"n": [1, 3_000_000_000]}).collect().schema[0][1] == "float"
    with pytest.raises(ValueError, match="signed 32-bit"):
        frame({"n": [3_000_000_000]}, schema={"n": "int"})
