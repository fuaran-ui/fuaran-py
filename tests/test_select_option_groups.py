"""Phase 2571 — a Select carries optional option groups (WIRE_FORMAT §3.6.30).

The corpus certifies the codec (``nodes/select-option-groups*.json``, ``reject/reject-2571-*``);
this file pins what it does not: the authoring surface, the omit-at-default rule for an explicit
``[]``, a bound source the decoder cannot judge, and this host's server rendering.
"""

from __future__ import annotations

from fuaran_ui import decode_node
from fuaran_ui.renderer import render_html
from fuaran_ui.result import Ok
from fuaran_ui.schema import encode_node
from fuaran_ui.schema.types import Select, SelectOption, SelectOptionGroup, Static

_STATIC = '{"$type":"Static","value":[{"label":"A","value":"a"},{"label":"B","value":"b"},{"label":"C","value":"c"}]}'


def _select(groups: str, source: str = _STATIC) -> str:
    return (
        '{"id":"s","kind":{"$type":"Select","groups":' + groups + ',"label":"Pick","source":' + source + ","
        '"value":{"$type":"Static","value":"a"}}}'
    )


def _code_path(wire: str) -> tuple[str, str]:
    result = decode_node(wire)
    assert not isinstance(result, Ok), f"accepted {wire}"
    return result.error.code, result.error.path


def test_groups_round_trip_byte_identically() -> None:
    wire = _select('[{"label":"Two","values":["b"]},{"label":"One","values":["a"]}]')
    result = decode_node(wire)
    assert isinstance(result, Ok)
    assert encode_node(result.value) == wire


def test_an_explicit_empty_list_is_the_absent_member() -> None:
    result = decode_node(_select("[]"))
    assert isinstance(result, Ok)
    assert '"groups"' not in encode_node(result.value)


def test_the_two_refusals() -> None:
    assert _code_path(_select('[{"label":"One","values":["a"]},{"label":"Two","values":["a"]}]')) == (
        "WRONG_TYPE",
        "$.kind.groups[1].values[0]",
    )
    assert _code_path(_select('[{"label":"One","values":["a","a"]}]')) == ("WRONG_TYPE", "$.kind.groups[0].values[1]")
    assert _code_path(_select('[{"label":"One","values":["zz"]}]')) == ("WRONG_TYPE", "$.kind.groups[0].values[0]")


def test_a_bound_source_is_not_judged_at_decode() -> None:
    assert isinstance(decode_node(_select('[{"label":"One","values":["zz"]}]', '{"$type":"State","key":"k"}')), Ok)


def test_the_authoring_surface_writes_groups_and_omits_none() -> None:
    options = Static((SelectOption("A", "a"), SelectOption("B", "b")))
    grouped = Select(label="Pick", source=options, groups=(SelectOptionGroup("One", ("b",)),)).to_wire()
    assert "groups" in grouped.fields
    assert "groups" not in Select(label="Pick", source=options).to_wire().fields


def test_the_server_renderer_draws_optgroups_with_the_remainder_last() -> None:
    result = decode_node(_select('[{"label":"Two","values":["c","b"]}]'))
    assert isinstance(result, Ok)
    html = render_html(result.value)
    assert (
        '<optgroup label="Two"><option value="b">B</option><option value="c">C</option></optgroup>'
        '<option value="a">A</option></select>'
    ) in html
