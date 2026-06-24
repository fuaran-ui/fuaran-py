"""Action-discriminator coverage at a validated action position.

A `Modal.onDismiss` is the one wire position whose `Action` discriminator is
validated (the rest pass through unvalidated). All 11 canonical `Action` cases
(WIRE_FORMAT.md §3.3) must therefore be accepted there — previously the Python
host listed only 7, so a valid F# Modal whose `onDismiss` was `Call` / `AiTool`
/ `CommitLocal` / `Invoke` was wrongly rejected (parity audit gap P1). These
tests pin the four formerly-missing cases (and the genuine-reject floor).
"""

from __future__ import annotations

import json

import pytest

from fuaran_py import decode_node, encode_node

# The four cases the Python `ACTION_CASES` set previously omitted, in
# representative canonical-ish wire shapes (decoded structurally after the
# discriminator is validated, so the exact fields round-trip verbatim).
FORMERLY_MISSING = {
    "Call": {"$type": "Call", "endpoint": "/api/x", "onResult": "<closure>"},
    "AiTool": {"$type": "AiTool", "args": {"q": "x"}, "tool": "search"},
    "CommitLocal": {"$type": "CommitLocal", "key": "draft"},
    "Invoke": {"$type": "Invoke", "args": [{"addr": "rows", "value": "all"}], "capabilityId": "model.score"},
}


def _modal_with(action: dict) -> str:
    node = {
        "id": "m",
        "kind": {
            "$type": "Modal",
            "children": [],
            "dismissable": True,
            "onDismiss": action,
            "open": {"$type": "Static", "value": False},
        },
    }
    return json.dumps(node)


@pytest.mark.parametrize("tag", list(FORMERLY_MISSING), ids=list(FORMERLY_MISSING))
def test_modal_ondismiss_action_accepted_and_byte_stable(tag: str) -> None:
    decoded = decode_node(_modal_with(FORMERLY_MISSING[tag]))
    assert decoded.ok, f"{tag} onDismiss was rejected"

    once = encode_node(decoded.value)
    # The action survives, not silently dropped.
    assert f'"$type":"{tag}"' in once

    # Byte-stable round-trip: encode(decode(encode(decode x))) == encode(decode x).
    twice = decode_node(once)
    assert twice.ok
    assert encode_node(twice.value) == once


def test_unknown_action_still_rejected() -> None:
    decoded = decode_node(_modal_with({"$type": "Bogus"}))
    assert not decoded.ok
    assert decoded.error.code == "UNKNOWN_DU_CASE"
