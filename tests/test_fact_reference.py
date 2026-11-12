"""Phase 2535 - a cited figure (WIRE_FORMAT 3.6.31).

`data-fact` on the tile's root, through the ordinary attribute encoder; an uncited tile carries none.
"""

from fuaran_ui import decode_node
from fuaran_ui.renderer import render_html
from fuaran_ui.result import Ok
from fuaran_ui.schema import encode_node

_CITED = '{"id":"f","kind":{"$type":"Fact","label":"Revenue","reference":"fact:a\\"b","value":"1"}}'
_UNCITED = '{"id":"f","kind":{"$type":"Fact","label":"Revenue","value":"1"}}'


def _node(wire: str):
    decoded = decode_node(wire)
    assert isinstance(decoded, Ok), decoded
    return decoded.value


def test_a_reference_round_trips_byte_identically() -> None:
    assert encode_node(_node(_CITED)) == _CITED


def test_a_cited_fact_stamps_data_fact_on_its_tile() -> None:
    assert '<div class="fuaran-fact fuaran-fact-default" data-fact="fact:a&quot;b">' in render_html(_node(_CITED))


def test_an_uncited_fact_carries_no_attribute() -> None:
    assert "data-fact" not in render_html(_node(_UNCITED))
