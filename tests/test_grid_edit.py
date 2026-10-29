"""The corpus's ``grid-edit/`` family (WIRE_FORMAT §3.2, "Edits as column ops — GridEdit").

This host decodes every vector's ``GridEdit`` with its own codec, applies the op, derives the
row delta, inverts it and restores the table, and runs the three scripted histories through its
own edit history. Every answer is compared after decoding, through this host's canonical
encoders — the family pins behaviour, not file framing.

The family is self-enumerated and not part of the bundled snapshot, so it is read from the
AUTHORITATIVE corpus only: absent that corpus the corpus half skips, and with it present a missing
family file FAILS — a behaviour family cannot be certified by reading nothing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from _corpus import AUTHORITY_ROOT
from fuaran_ui.canonical import encode_value
from fuaran_ui.dataframe import Embedded, Ok, Table, decode_source_json, encode_source
from fuaran_ui.dataframe.grid_edit import (
    FullRefresh,
    GridEdit,
    GridEditHistory,
    apply_op,
    decode_grid_edit,
    decode_grid_edit_json,
    decode_op_json,
    delta_of,
    encode_delta,
    encode_grid_edit,
    encode_op,
    invert_op,
)
from fuaran_ui.model import Arr, Obj, Value

FAMILY = AUTHORITY_ROOT / "grid-edit"

authority_present = pytest.mark.skipif(
    not (AUTHORITY_ROOT / "manifest.json").is_file(),
    reason=f"authoritative corpus not found at {AUTHORITY_ROOT}",
)


def _family() -> dict[str, Any]:
    return json.loads((FAMILY / "grid-edit-vectors.json").read_text(encoding="utf-8"))


def _table(el: object) -> Table:
    r = decode_source_json(el)
    assert isinstance(r, Ok) and isinstance(r.value, Embedded), f"not an embedded table: {el}"
    return r.value.table


def _table_text(t: Table) -> str:
    return encode_source(Embedded(t))


def _edit(el: object) -> GridEdit:
    r = decode_grid_edit_json(el)
    assert isinstance(r, Ok), r
    return r.value


def _op_text(el: object) -> str:
    r = decode_op_json(el)
    assert isinstance(r, Ok), r
    return encode_op(r.value)


def _canonical(el: object) -> str:
    """Any parsed JSON value in its canonical form (sorted members, the pinned number layout)."""

    def lower(v: object) -> Value:
        if isinstance(v, dict):
            tag = v.get("$type")
            return Obj(tag if isinstance(tag, str) else None, {k: lower(x) for k, x in v.items() if k != "$type"})
        if isinstance(v, list):
            return Arr([lower(x) for x in v])
        return v  # type: ignore[return-value]

    return encode_value(lower(el))


def _answer(table: Table, edit: GridEdit) -> dict[str, object]:
    applied = apply_op(edit.op, table)
    if not isinstance(applied, Ok):
        return {"refused": applied.error.token}
    out: dict[str, object] = {"after": _table_text(applied.value)}
    if edit.row_key_field is None:
        out["delta"] = encode_delta(FullRefresh())
        out["referencePath"] = "noRowIdentityDeclared"
    else:
        out["delta"] = encode_delta(delta_of(edit.row_key_field, table, edit.op))
    inverse = invert_op(edit.op, table)
    if isinstance(inverse, Ok):
        undone = apply_op(inverse.value, applied.value)
        assert isinstance(undone, Ok), undone
        out["inverse"] = encode_op(inverse.value)
        out["undone"] = _table_text(undone.value)
    else:
        out["notInvertible"] = True
    return out


def _expected(e: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    for k, v in e.items():
        if k in ("after", "undone"):
            out[k] = _table_text(_table(v))
        elif k == "inverse":
            out[k] = _op_text(v)
        elif k == "delta":
            out[k] = _canonical(v)
        else:
            out[k] = v
    return out


@authority_present
def test_the_family_is_enumerated_and_read() -> None:
    manifest = json.loads((FAMILY / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["files"] == ["grid-edit-vectors.json"]
    family = _family()
    assert len(family["vectors"]) >= 15  # noqa: PLR2004
    assert len(family["histories"]) >= 3  # noqa: PLR2004


@authority_present
def test_every_vector() -> None:
    for v in _family()["vectors"]:
        edit = _edit(v["input"]["edit"])
        # The codec: what this host decodes, it re-encodes to the canonical bytes the corpus carries.
        assert encode_grid_edit(edit) == _canonical(v["input"]["edit"]), v["id"]
        assert _answer(_table(v["input"]["table"]), edit) == _expected(v["expected"]), v["id"]


@authority_present
def test_every_history() -> None:
    for h in _family()["histories"]:
        history = GridEditHistory(_table(h["input"]["table"]))
        steps: list[dict[str, object]] = []
        for g in h["input"]["gestures"]:
            if "edit" in g:
                r = history.apply(_edit(g["edit"]).op)
            elif "undo" in g:
                r = history.undo()
            else:
                r = history.redo()
            if isinstance(r, Ok):
                history = r.value
                steps.append(
                    {"canRedo": history.can_redo, "canUndo": history.can_undo, "table": _table_text(history.table)}
                )
            else:
                steps.append({"refused": r.error.token})
        expected = [
            s if "refused" in s else {**s, "table": _table_text(_table(s["table"]))} for s in h["expected"]["steps"]
        ]
        assert steps == expected, h["id"]


@pytest.mark.parametrize(
    "text",
    [
        '{"$type":"GridEdit","key":"k","op":{"$type":"removeColumn","name":"x"},"extra":1}',
        '{"$type":"GridEdit","key":"","op":{"$type":"removeColumn","name":"x"}}',
        '{"$type":"GridEdit","key":"k","op":{"$type":"removeColumn","name":"x"},"rowKeyField":""}',
        '{"$type":"GridEdit","key":"k","op":{"$type":"removeColumn","name":"x"},"rowKeyField":3}',
        '{"$type":"GridEdit","key":"k"}',
        '{"$type":"GridEdit","key":"k","op":{"$type":"dropTable"}}',
        '{"$type":"Edit","key":"k","op":{"$type":"removeColumn","name":"x"}}',
        "[1]",
        "{",
    ],
)
def test_the_codec_refuses_what_it_could_not_apply_faithfully(text: str) -> None:
    assert not isinstance(decode_grid_edit(text), Ok)


def test_absence_spells_no_identity_declared() -> None:
    text = '{"$type":"GridEdit","key":"orders","op":{"$type":"removeColumn","name":"note"}}'
    r = decode_grid_edit(text)
    assert isinstance(r, Ok)
    assert encode_grid_edit(r.value) == text
