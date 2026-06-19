"""The make-or-break: canonical number formatting (WIRE_FORMAT.md §2 rule 5).

The encoder must reproduce the canonical float layout exactly — it does NOT
delegate to ``json.dumps`` / ``repr``. These cases pin the cross-host divergence
zone (exponent threshold + sign-padding + case) where a naive serializer fails.
"""

from __future__ import annotations

import pytest

from fuaran_py.canonical import format_finite_double

CASES = [
    # divergence zone (these are exactly the metric-float-* corpus values)
    (1e21, "1E+21"),
    (1e-7, "1E-07"),
    (0.30000000000000004, "0.30000000000000004"),
    (1.2345678901234568e17, "1.2345678901234568E+17"),
    # plain decimals (agree across hosts)
    (1234.5, "1234.5"),
    (0.07, "0.07"),
    (0.42, "0.42"),
    (99.5, "99.5"),
    # integers-as-float collapse to the integer layout
    (42.0, "42"),
    (1.0, "1"),
    (0.0, "0"),
    (-0.0, "0"),  # negative zero collapses to positive zero
    # signs + spec examples
    (-3.5, "-3.5"),
    (1.602e-19, "1.602E-19"),
    (5e-324, "5E-324"),  # smallest positive subnormal
    (-1e21, "-1E+21"),
]


@pytest.mark.parametrize(("value", "expected"), CASES, ids=[repr(v) for v, _ in CASES])
def test_format_finite_double(value: float, expected: str) -> None:
    assert format_finite_double(value) == expected


def test_specials_encode_as_quoted_sentinels() -> None:
    from fuaran_py.canonical import encode_value

    assert encode_value(float("nan")) == '"NaN"'
    assert encode_value(float("inf")) == '"Infinity"'
    assert encode_value(float("-inf")) == '"-Infinity"'
