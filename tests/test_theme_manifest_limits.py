"""A theme manifest is read under WIRE_FORMAT §21.1's syntactic limits (Phase 2653).

The same manifest string is decoded on every host (the Phase 2133 byte law), so each
edge below is stated as the format's literal value, at it and one past it. A host that
drifts to figures of its own goes red here rather than in another host's suite.
"""

from __future__ import annotations

import pytest

from fuaran_ui.theme_manifest import decode


def _nested_groups(levels: int) -> str:
    """A vanilla DTCG tree ``levels`` groups deep over one leaf: ``levels + 1`` containers."""
    return '{"g":' * levels + '{"$value":"#000"}' + "}" * levels


def _token_value(value: str) -> str:
    return '{"tokens":{"a":{"$value":"' + value + '"}}}'


def _invariant_items(n: int) -> str:
    return '{"tokens":{},"invariants":[' + ",".join(["0"] * n) + "]}"


def _token_members(n: int) -> str:
    return '{"tokens":{' + ",".join(f'"k{i}":0' for i in range(n)) + "}}"


def _pad_to(n_bytes: int) -> str:
    base = '{"tokens":{}}'
    return base + " " * (n_bytes - len(base))


def _refusal(json_str: str) -> str:
    with pytest.raises(ValueError) as caught:
        decode(json_str)
    return str(caught.value)


def test_a_manifest_256_containers_deep_decodes_and_257_is_refused_by_name() -> None:
    # The scalar leaf sits inside the 256th container and adds no level: §21.1
    # counts every `{` and `[`.
    assert len(decode(_nested_groups(255)).tokens) == 1
    error = _refusal(_nested_groups(256))
    assert error.startswith("LIMIT_EXCEEDED")
    assert "MAX_JSON_DEPTH = 256" in error


def test_a_depth_bomb_is_refused_before_recursion() -> None:
    error = _refusal(_nested_groups(100000))
    assert error.startswith("LIMIT_EXCEEDED")
    assert "MAX_JSON_DEPTH = 256" in error


def test_a_string_is_bounded_at_1048576_code_points() -> None:
    decode(_token_value("a" * 1048576))
    assert "MAX_STRING_LENGTH = 1048576" in _refusal(_token_value("a" * 1048577))
    # Code points, not UTF-16 units: an astral character costs one.
    decode(_token_value("\U0001d11e" * 1048576))


def test_an_array_is_bounded_at_100000_items() -> None:
    decode(_invariant_items(100000))
    assert "MAX_ARRAY_LENGTH = 100000" in _refusal(_invariant_items(100001))


def test_an_object_is_bounded_at_100000_members() -> None:
    decode(_token_members(100000))
    error = _refusal(_token_members(100001))
    assert error.startswith("LIMIT_EXCEEDED")
    assert "members" in error


def test_the_document_is_bounded_at_33554432_utf8_bytes() -> None:
    decode(_pad_to(33554432))
    assert "MAX_DOCUMENT_BYTES = 33554432" in _refusal(_pad_to(33554433))


def test_malformed_text_is_refused_as_malformed_never_as_a_breach() -> None:
    assert _refusal('{"tokens":{').startswith("INVALID_JSON")
