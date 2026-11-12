"""Phase 2591 - a cited figure inside a sentence (WIRE_FORMAT 3.6.32).

The render bytes are pinned by the shared markdown corpus; these tests reach the node path:
the wire member, the renderer's use of it, the email projection, and the UpdateProp table.
"""

import json

from fuaran_ui import decode_node
from fuaran_ui.ops import apply, decode_op
from fuaran_ui.renderer import render_html
from fuaran_ui.renderer.email import render_email
from fuaran_ui.result import Ok
from fuaran_ui.schema import encode_node
from fuaran_ui.ui import encode, fuaran

_CITED = (
    '{"id":"m","kind":{"$type":"Markdown","citations":['
    '{"label":"Online share","name":"share","reference":"fact:share"},'
    '{"name":"spend","reference":"fact:spend"}],'
    '"text":"It took [23%](cite:share) of [£412k](cite:spend)."}}'
)


def _node(wire: str):
    decoded = decode_node(wire)
    assert isinstance(decoded, Ok), decoded
    return decoded.value


def test_a_cited_markdown_round_trips_byte_identically() -> None:
    assert encode_node(_node(_CITED)) == _CITED


def test_a_markdown_citing_nothing_has_no_member() -> None:
    node = fuaran.markdown("m", "x")
    assert encode(node) == '{"id":"m","kind":{"$type":"Markdown","text":"x"}}'


def test_a_sentence_is_one_paragraph_with_each_figure_in_line() -> None:
    html = render_html(_node(_CITED))
    assert (
        "<p>It took "
        '<span class="fuaran-cited" data-fact="fact:share">'
        '<span class="fuaran-cited-label">Online share </span>23%</span> of '
        '<span class="fuaran-cited" data-fact="fact:spend">£412k</span>.</p>'
    ) in html
    assert "fuaran-kind-fact" not in html
    assert "<a " not in html


def test_an_email_keeps_the_figure_and_its_reference_and_prints_no_label() -> None:
    html = render_email(_node(_CITED))
    assert 'data-fact="fact:share"' in html
    assert "Online share" not in html
    assert "about:blank" not in html


def test_the_authoring_surface_writes_the_member() -> None:
    from fuaran_ui.schema import types as t

    node = fuaran.markdown("m", "It took [23%](cite:a).", citations=[t.MarkdownCitation("a", "fact:a")])
    assert json.loads(encode(node))["kind"]["citations"] == [{"name": "a", "reference": "fact:a"}]


def test_a_fact_has_an_update_prop_surface() -> None:
    """The row 2535 left out: UpdateProp on a Fact reaches its reference and its icon."""
    tree = _node('{"id":"f","kind":{"$type":"Fact","label":"Revenue","value":"1"}}')
    for field, wire_key, value in (("Reference", "reference", "fact:a"), ("Icon", "icon", "star")):
        op = decode_op(json.dumps({"$type": "UpdateProp", "target": "f", "path": field, "value": value}))
        assert isinstance(op, Ok), op
        applied = apply(op.value, tree)
        assert isinstance(applied, Ok), applied
        assert json.loads(encode_node(applied.value))["kind"][wire_key] == value
