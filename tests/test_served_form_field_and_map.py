"""The served form field and Map, in the reference host's ruled shape (Phase 2391).

The reference host settled both under its 0.98.0 rulings: a form field is
``div.fuaran-form-field > label.fuaran-form-label[for] + control[id, data-fuaran-field]
+ help`` (R4), and a Map is the served placeholder carrying, when its markers
resolve, one list item per marker (R12). The old ``span.fuaran-form-field-label``
inside a wrapping ``<label>`` is retired, so a host that still emits it serves a
class the reference vocabulary no longer spells.

This host keeps its UNIFORM control floor (one ``<input>`` per field, see
``_form_field``); what moves is the wrapper, the label and the control's ``id``.
"""

from __future__ import annotations

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_ui import decode_node
from fuaran_ui.renderer import render_html


def _form(field: str) -> str:
    return (
        f'{{"id":"f1","kind":{{"$type":"Form","fields":[{field}],'
        '"onSubmit":{"$type":"Dispatch"},"submitLabel":"Save"}}'
    )


def _render(wire: str) -> str:
    result = decode_node(wire)
    assert result.ok, getattr(result, "error", result)
    return render_html(result.value)


def test_a_required_field_is_the_ruled_wrapper_label_and_control() -> None:
    html = _render(_form('{"id":"x","kind":{"$type":"Text","onChange":"<closure>"},"label":"Name","required":true}'))
    assert (
        '<div class="fuaran-form-field"><label class="fuaran-form-label" for="x">Name *</label>'
        '<input class="fuaran-form-field-control" id="x" data-fuaran-field="x" /></div>'
    ) in html


def test_an_optional_field_has_no_asterisk() -> None:
    html = _render(_form('{"id":"x","kind":{"$type":"Text","onChange":"<closure>"},"label":"Name"}'))
    assert '<label class="fuaran-form-label" for="x">Name</label>' in html


def test_the_help_text_follows_the_control() -> None:
    html = _render(
        _form('{"id":"x","kind":{"$type":"Text","onChange":"<closure>"},"label":"Name","help":"As on your passport"}')
    )
    assert (
        '<input class="fuaran-form-field-control" id="x" data-fuaran-field="x" />'
        '<div class="fuaran-form-help">As on your passport</div></div>'
    ) in html


def test_the_retired_field_label_span_is_not_served() -> None:
    html = _render(_form('{"id":"x","kind":{"$type":"Text","onChange":"<closure>"},"label":"Name"}'))
    assert "fuaran-form-field-label" not in html
    assert '<label class="fuaran-form-field"' not in html


@corpus_required
def test_a_map_whose_markers_resolve_serves_the_marker_list() -> None:
    html = _render((CORPUS_ROOT / "nodes/map-1.json").read_text(encoding="utf-8"))
    assert (
        '<div class="fuaran-map fuaran-map-ssr-placeholder" data-fuaran-ssr-placeholder="Map" '
        'data-fuaran-marker-count="1">[Map: 1 markers — hydrates client-side]'
        '<ul class="fuaran-map-marker-list"><li class="fuaran-map-marker">London @ (51.5000, -0.1200)</li></ul>'
        "</div>"
    ) in html
    assert "fuaran-map-placeholder" not in html


def test_a_map_with_no_markers_is_the_caption_alone() -> None:
    html = _render(
        '{"id":"m","kind":{"$type":"Map","centreLatitude":0,"centreLongitude":0,'
        '"source":{"$type":"Static","value":[]},"zoom":3}}'
    )
    assert ('data-fuaran-marker-count="0">[Map: 0 markers — hydrates client-side]</div>') in html
    assert "fuaran-map-marker-list" not in html
