"""Phase 2592 - a table cell cites its figure (WIRE_FORMAT 3.6.33)."""

import pytest

from fuaran_ui import decode_node
from fuaran_ui.renderer import render_html
from fuaran_ui.renderer.email import render_email
from fuaran_ui.result import Ok
from fuaran_ui.schema import encode_node
from fuaran_ui.schema.types import LiteralText, TableCitation
from fuaran_ui.ui import encode, fuaran


def _table(citations: str = "") -> str:
    member = f'"citations":[{citations}],' if citations else ""
    return (
        '{"id":"t","kind":{"$type":"DataGrid","columns":[],"source":{"$type":"Static","value":[]},'
        f'"staticRows":{{{member}"headers":["A","B"],"rows":[["x","1"],["y"]]}}}}}}'
    )


_CITED = _table('{"column":1,"label":"One","reference":"fact:one","row":0}')


def _node(wire: str):
    decoded = decode_node(wire)
    assert isinstance(decoded, Ok), decoded
    return decoded.value


def test_a_cited_table_round_trips_byte_identically() -> None:
    assert encode_node(_node(_CITED)) == _CITED


def test_a_table_citing_nothing_has_no_member() -> None:
    node = fuaran.table("t", headers=["A"], rows=[["x"]])
    assert "citations" not in encode(node)


def test_the_author_form_carries_the_citation() -> None:
    node = fuaran.table(
        "t",
        headers=["A"],
        rows=[["x"]],
        citations=[TableCitation(0, 0, "fact:one", LiteralText("One"))],
    )
    assert '"citations":[{"column":0,"label":"One","reference":"fact:one","row":0}]' in encode(node)


def test_a_cited_cell_stamps_the_td_with_the_label_first() -> None:
    html = render_html(_node(_CITED))
    assert (
        '<td class="fuaran-table-cell" data-fact="fact:one"><span class="fuaran-cited-label">One </span>1</td>' in html
    )
    assert '<td class="fuaran-table-cell">x</td>' in html
    assert "fuaran-kind-fact" not in html


def test_a_citation_adds_only_the_attribute_and_the_span() -> None:
    plain = render_html(_node(_table()))
    cited = render_html(_node(_CITED))
    assert (
        cited.replace(' data-fact="fact:one"', "").replace('<span class="fuaran-cited-label">One </span>', "") == plain
    )


def test_the_email_cell_keeps_the_reference_and_prints_no_label() -> None:
    html = render_email(_node(_CITED))
    assert 'data-fact="fact:one"' in html
    assert ">One " not in html


@pytest.mark.parametrize(
    ("citations", "path"),
    [
        ('{"column":0,"reference":"r","row":2}', "row"),
        ('{"column":1,"reference":"r","row":1}', "column"),
    ],
)
def test_a_coordinate_naming_no_cell_is_refused(citations: str, path: str) -> None:
    decoded = decode_node(_table(citations))
    assert not isinstance(decoded, Ok)
    assert decoded.error.path.endswith(f".citations[0].{path}")
