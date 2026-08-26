"""fuaran#687 — the CLOSE of the migration window fuaran#681 opened.

0.4.0 removed the ordinal from ``InsertChild`` and ``MoveNode``: both append, and
``ReorderChildren`` states order by naming child ids. Through the window every
decoder ACCEPTED AND IGNORED a legacy ``position`` / ``newPosition`` so the hosts
could adopt independently. Every host is now positionless and no emitter produces
the field, so the tolerance is withdrawn: it is a decode error, named at its own
path.

The corpus fixtures (``reject-op-insertchild-retired-position``,
``reject-op-movenode-retired-newposition``) certify code + path. These add the two
things the corpus deliberately cannot: the didactic text — op-side reject fixtures
assert code and path only — and the cross-host ORDERING guarantee, which no single
fixture can express because its payload is well-formed apart from the retired
field by design.
"""

from __future__ import annotations

import pytest

from fuaran_py.ops import decode_op
from fuaran_py.result import WRONG_TYPE, Err


@pytest.mark.parametrize(
    ("raw", "want_path"),
    [
        (
            '{"$type":"InsertChild","child":{"id":"n","kind":{"$type":"Markdown","text":"x"}},'
            '"parentId":"p","position":3}',
            "$.position",
        ),
        (
            '{"$type":"MoveNode","newParentId":"q","newPosition":2,"target":"n"}',
            "$.newPosition",
        ),
    ],
)
def test_retired_position_refused_by_name(raw: str, want_path: str) -> None:
    result = decode_op(raw)
    assert isinstance(result, Err), "the retired field was accepted — the migration window is closed"
    assert result.error.code == WRONG_TYPE
    assert result.error.path == want_path, "the error must name the retired field"
    # The didactic names what to reach for instead. A refusal that only says "no"
    # sends the author looking for a spelling.
    assert "ReorderChildren" in result.error.message


def test_retired_position_outranks_a_missing_required_field() -> None:
    """The retired field is named AHEAD of any other defect in the same op.

    Without this ordering an author who also omitted a required field would fix
    that and meet this one only on the next run. Fixed identically across all five
    hosts, so which defect surfaces first is deterministic.
    """
    result = decode_op('{"$type":"InsertChild","position":0}')
    assert isinstance(result, Err), "an op missing parentId AND carrying position decoded"
    assert result.error.path == "$.position", "the retired field wins over the missing required field"


def test_positionless_form_round_trips() -> None:
    from fuaran_py.ops import encode_op

    current = '{"$type":"MoveNode","newParentId":"q","target":"n"}'
    result = decode_op(current)
    assert not isinstance(result, Err), result
    assert encode_op(result.value) == current
