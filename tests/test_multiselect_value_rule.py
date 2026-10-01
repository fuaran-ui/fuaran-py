"""Phase 1962 — a multi-select `Select` carries `values` and no `value`.

The corpus pins the lenient drop (placeholder and a real binding) and the
single-select `MISSING_FIELD`. This suite pins what it does not: a MALFORMED
`value` on a multi-select is still decoded first, so it refuses exactly as any
malformed binding does rather than being silently dropped.
"""

from __future__ import annotations

import json

from fuaran_ui.schema import decode_node, encode_node


def _select(value: object) -> str:
    return json.dumps(
        {
            "id": "s1",
            "kind": {
                "$type": "Select",
                "label": "Tags",
                "multiple": True,
                "source": {"$type": "Static", "value": [{"label": "Red", "value": "red"}]},
                "value": value,
                "values": {"$type": "State", "key": "tags"},
            },
        }
    )


def test_a_well_formed_value_on_a_multi_select_is_dropped() -> None:
    result = decode_node(_select({"$type": "State", "key": "primary"}))
    assert result.ok, result.error
    assert "value" not in json.loads(encode_node(result.value))["kind"]


def test_a_malformed_value_on_a_multi_select_still_refuses() -> None:
    for bad in ({"$type": "Nope"}, 42):
        result = decode_node(_select(bad))
        assert not result.ok, bad
        assert result.error.path.startswith("$.kind.value"), result.error
