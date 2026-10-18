"""WIRE_FORMAT.md §5 "The two encode refusals" — the ``row-encode/`` corpus family (Phase 1960).

Each vector is a row feed in host-neutral cell descriptors and either its canonical bytes
or its refusal. This host maps an ``int64`` descriptor onto an ``int`` cell, a ``utc``
dateTime onto an aware ``datetime`` and an ``unspecified`` one onto a naive ``datetime``,
and runs them through :func:`~fuaran_ui.schema.types.encode_rows`, the lowering a DataGrid /
Chart ``source`` takes. Every vector is representable here, so none is skipped.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui.canonical import encode_value
from fuaran_ui.schema.types import Chart, RowCellRefused, Static, encode_rows

_MANIFEST = json.loads((CORPUS_ROOT / "row-encode" / "manifest.json").read_text(encoding="utf-8"))
_VECTORS = _MANIFEST["vectors"]


def _cell(d: dict) -> object:
    if "int64" in d:
        return int(d["int64"])
    naive = dt.datetime.fromisoformat(d["dateTime"])
    if d["kind"] == "utc":
        return naive.replace(tzinfo=dt.UTC)
    assert d["kind"] == "unspecified", d
    return naive


def _feed(v: dict) -> list[dict[str, object]]:
    return [{k: _cell(d) for k, d in row.items()} for row in v["rows"]]


def test_the_family_has_its_vectors() -> None:
    assert len(_VECTORS) >= 6
    assert {"expected" in v or "refused" in v for v in _VECTORS} == {True}


@pytest.mark.parametrize("vector", _VECTORS, ids=[v["id"] for v in _VECTORS])
def test_row_encode_vector(vector: dict) -> None:
    rows = _feed(vector)
    if "refused" in vector:
        with pytest.raises(RowCellRefused) as caught:
            encode_rows(rows)
        got = caught.value
        assert {"code": got.code, "row": got.row, "key": got.key} == vector["refused"]
    else:
        assert encode_rows(rows) == vector["expected"]


def test_a_chart_source_refuses_through_the_row_feed() -> None:
    chart = Chart(kind="Line", source=Static([{"at": dt.datetime(2026, 1, 1)}]), x_field="x", y_fields=["y"])
    with pytest.raises(RowCellRefused, match="row 0, cell 'at'"):
        chart.to_wire()
    bound = Chart(kind="Line", source=Static([{"n": 2**53}]), x_field="x", y_fields=["y"])
    assert '"value":[{"n":9007199254740992}]' in encode_value(bound.to_wire())
