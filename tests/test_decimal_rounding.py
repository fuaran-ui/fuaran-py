"""The seven rounding modes, pinned mode by mode (Phase 2001).

The drawn ``transformLaws`` sample reaches only some modes, and none of its quotients lands on a
tie, so a mode that rounded a tie the wrong way would pass it. This file pins every mode against the
well-known table the seven modes are defined by (``java.math.RoundingMode`` names the same seven),
through the one rounding kernel ``Quotient`` and ``Rounded`` share, through the evaluator, and
through the wire codec's seven spellings.
"""

from __future__ import annotations

import pytest

from fuaran_ui.dataframe import (
    ROUNDING_MODES,
    Col,
    Column,
    Derive,
    Lit,
    Quotient,
    Rounded,
    Rounding,
    Table,
    cell_decimal,
    cell_int,
    decimal_text,
    decode_pipeline,
    encode_pipeline,
    eval_pipeline,
)
from fuaran_ui.dataframe.model import DECIMAL, UNKNOWN_TYPE

# Input → the answer at scale 0 under each mode, in the order of the header.
HEADER = ("up", "down", "ceiling", "floor", "half-up", "half-down", "half-even")
TABLE = {
    "5.5": ("6", "5", "6", "5", "6", "5", "6"),
    "2.5": ("3", "2", "3", "2", "3", "2", "2"),
    "1.6": ("2", "1", "2", "1", "2", "2", "2"),
    "1.1": ("2", "1", "2", "1", "1", "1", "1"),
    "1": ("1", "1", "1", "1", "1", "1", "1"),
    "-1": ("-1", "-1", "-1", "-1", "-1", "-1", "-1"),
    "-1.1": ("-2", "-1", "-1", "-2", "-1", "-1", "-1"),
    "-1.6": ("-2", "-1", "-1", "-2", "-2", "-2", "-2"),
    "-2.5": ("-3", "-2", "-2", "-3", "-3", "-2", "-2"),
    "-5.5": ("-6", "-5", "-5", "-6", "-6", "-5", "-6"),
}
CASES = [(text, mode, row[HEADER.index(mode)]) for text, row in TABLE.items() for mode in HEADER]


def test_the_vocabulary_is_the_seven_modes() -> None:
    assert set(ROUNDING_MODES) == set(HEADER)
    assert len(ROUNDING_MODES) == 7


@pytest.mark.parametrize(("text", "mode", "expected"), CASES)
def test_quantize_to_scale_zero(text: str, mode: str, expected: str) -> None:
    assert decimal_text.quantize(mode, 0, text) == expected


@pytest.mark.parametrize(("text", "mode", "expected"), CASES)
def test_divide_rounds_the_exact_quotient(text: str, mode: str, expected: str) -> None:
    """``x * 7 / 7`` at scale 0 is ``x`` rounded once — the divide path through the same kernel."""
    times_seven = decimal_text.mul(text, "7")
    assert times_seven is not None
    assert decimal_text.divide(mode, 0, times_seven, "7") == expected


def test_one_rounding_never_two() -> None:
    # 0.445 to one place is 0.4; rounding to two places first (0.45) and then to one would give 0.5.
    assert decimal_text.quantize("half-up", 1, "0.445") == "0.4"
    assert decimal_text.divide("half-up", 1, "445", "1000") == "0.4"
    # A third to four places, and its negation, under the directed modes.
    assert decimal_text.divide("ceiling", 4, "1", "3") == "0.3334"
    assert decimal_text.divide("ceiling", 4, "-1", "3") == "-0.3333"
    assert decimal_text.divide("floor", 4, "-1", "3") == "-0.3334"


@pytest.mark.parametrize("mode", HEADER)
def test_quotient_and_rounded_through_the_evaluator(mode: str) -> None:
    table = Table(
        [("m", DECIMAL), ("k", "int")],
        [Column("m", DECIMAL, [cell_decimal("-2.5")]), Column("k", "int", [cell_int(1)])],
    )
    pipeline = [
        Derive("q", Quotient(Col("m"), Col("k"), Rounding(0, mode))),
        Derive("r", Rounded(Col("m"), Rounding(0, mode))),
    ]
    result = eval_pipeline(pipeline, table)
    assert result.ok
    expected = TABLE["-2.5"][HEADER.index(mode)]
    by_name = {c.name: c for c in result.value.columns}
    assert by_name["q"].cells == [cell_decimal(expected)]
    assert by_name["r"].cells == [cell_decimal(expected)]


@pytest.mark.parametrize("mode", HEADER)
def test_every_mode_spelling_round_trips_on_the_wire(mode: str) -> None:
    wire = encode_pipeline([Derive("r", Rounded(Lit(cell_decimal("1.25")), Rounding(1, mode)))])
    decoded = decode_pipeline(wire)
    assert decoded.ok
    assert encode_pipeline(decoded.value) == wire


def test_an_unknown_mode_is_refused_by_name() -> None:
    wire = (
        '[{"$type":"derive","expr":{"$type":"rounded","expr":{"$type":"col","name":"m"},'
        '"rounding":{"mode":"half-odd","scale":2}},"name":"r"}]'
    )
    decoded = decode_pipeline(wire)
    assert not decoded.ok
    assert decoded.error.code == UNKNOWN_TYPE
    assert "half-odd" in decoded.error.detail


def test_a_scale_outside_the_bound_is_refused_at_evaluation() -> None:
    table = Table([("m", DECIMAL)], [Column("m", DECIMAL, [cell_decimal("1")])])
    for scale in (-1, decimal_text.MAX_SCALE + 1):
        assert not eval_pipeline([Derive("r", Rounded(Col("m"), Rounding(scale, "down")))], table).ok
    assert eval_pipeline([Derive("r", Rounded(Col("m"), Rounding(decimal_text.MAX_SCALE, "down")))], table).ok
