"""The Phase 766/768 authoring vocabulary — Toggle, Now, Selection, Switch ``on``.

The codec adopted the ``Toggle`` form-field kind, the ``Now`` binding, and the
``Switch`` ``on`` binding selector; these tests pin the *authoring* surface
(:mod:`fuaran_py.schema.types` + the ``fuaran_py.ui`` namespaces) to the same
vocabulary: every construct lowers to canonical bytes, survives a decode →
re-encode round-trip byte-stably, and the ``Switch`` selector honours the
Phase 768 collapse rule (a default-free ``State`` keeps the compact ``stateKey``
spelling, exactly as the F#/TS encoders spell it).
"""

from __future__ import annotations

import pytest

from fuaran_py import decode_node, encode_node
from fuaran_py.canonical import encode_value
from fuaran_py.schema import types as t
from fuaran_py.ui import binding, encode, fuaran, node


def _roundtrips(wire: str) -> None:
    """The conformance invariant: authored bytes decode and re-encode byte-stably."""
    decoded = decode_node(wire)
    assert decoded.ok, decoded.error
    assert encode_node(decoded.value) == wire


def _bytes_of(v: object) -> str:
    """Canonical bytes of a lowered authoring value (a sub-tree, not a node)."""
    return encode_value(t._lower(v))


# ── FormFieldKind.Toggle (Phase 766) ─────────────────────────────────────────


def test_bare_toggle_is_the_canonical_minimal_control() -> None:
    """An absent value auto-binds at run time; an absent handler arms the
    write-back default — the wire form is the tag-only ``{"$type":"Toggle"}``."""
    form = node.bare(
        fuaran.form(
            "form-toggle-min",
            submit_label="Save",
            fields=[t.FormField("running", t.LiteralText("Running"), t.ToggleField(), False)],
        )
    )
    wire = encode(form)
    assert '"kind":{"$type":"Toggle"}' in wire
    _roundtrips(wire)


def test_toggle_with_value_and_handler() -> None:
    form = node.bare(
        fuaran.form(
            "form-toggle-full",
            submit_label="Save",
            fields=[
                t.FormField(
                    "running",
                    t.LiteralText("Running"),
                    t.ToggleField(t.Static(True), on_toggle=True),
                    True,
                )
            ],
        )
    )
    wire = encode(form)
    assert '"kind":{"$type":"Toggle","onToggle":"<closure>","value":{"$type":"Static","value":true}}' in wire
    _roundtrips(wire)


def test_toggle_declarative_value_only() -> None:
    """A value with no handler — the declarative write-back form."""
    kind = t.ToggleField(t.State("irrigation", False))
    assert _bytes_of(kind) == '{"$type":"Toggle","value":{"$type":"State","defaultValue":false,"key":"irrigation"}}'


# ── Binding.Now ──────────────────────────────────────────────────────────────


def test_now_binding_is_tag_only() -> None:
    md = fuaran.markdown("today", t.Bound(binding.now()))
    wire = encode(md)
    assert wire == '{"id":"today","kind":{"$type":"Markdown","text":{"$type":"Bound","binding":{"$type":"Now"}}}}'
    _roundtrips(wire)


# ── Binding.Selection ────────────────────────────────────────────────────────


def test_selection_full_form_matches_the_corpus_spelling() -> None:
    """defaultValue < field < nodeId — the exact selector bytes the corpus's
    switch-on-selection fixture carries."""
    sel = binding.selection("ward-grid", field="status", default_value="steady")
    assert _bytes_of(sel) == '{"$type":"Selection","defaultValue":"steady","field":"status","nodeId":"ward-grid"}'


def test_selection_optionals_are_omitted_never_null() -> None:
    assert _bytes_of(t.Selection("grid-1")) == '{"$type":"Selection","nodeId":"grid-1"}'
    assert _bytes_of(t.Selection("grid-1", field="id")) == '{"$type":"Selection","field":"id","nodeId":"grid-1"}'


# ── Switch — the Phase 768 selector ──────────────────────────────────────────


def _cases() -> list[tuple[str, t.UiNode]]:
    return [("critical", fuaran.markdown("sw-critical", "Escalate."))]


def _default() -> t.UiNode:
    return fuaran.markdown("sw-steady", "Within range.")


def test_switch_state_key_spelling_is_unchanged() -> None:
    sw = fuaran.switch("sw", cases=_cases(), default=_default(), state_key="mode")
    wire = encode(sw)
    assert '"stateKey":"mode"' in wire
    assert '"on":' not in wire
    _roundtrips(wire)


def test_switch_on_default_free_state_collapses_to_state_key() -> None:
    """The Phase 768 collapse rule: ``on=State(key)`` is byte-identical to the
    compact ``state_key`` spelling — canonical bytes carry ``on`` only for a
    selector the compact form cannot spell."""
    compact = encode(fuaran.switch("sw", cases=_cases(), default=_default(), state_key="mode"))
    via_on = encode(fuaran.switch("sw", cases=_cases(), default=_default(), on=t.State("mode", None)))
    assert via_on == compact


def test_switch_on_state_with_default_emits_on() -> None:
    sw = fuaran.switch("sw", cases=_cases(), default=_default(), on=t.State("mode", "steady"))
    wire = encode(sw)
    assert '"on":{"$type":"State","defaultValue":"steady","key":"mode"}' in wire
    assert '"stateKey"' not in wire
    _roundtrips(wire)


def test_switch_driven_by_selection_is_now_expressible() -> None:
    """The gap this vocabulary closes: a Switch whose branch follows the clicked
    row, with no writer — the corpus's switch-on-selection selector shape."""
    sw = fuaran.switch(
        "ward-status-panel",
        cases=_cases(),
        default=_default(),
        on=binding.selection("ward-grid", field="status", default_value="steady"),
    )
    wire = encode(sw)
    assert '"on":{"$type":"Selection","defaultValue":"steady","field":"status","nodeId":"ward-grid"}' in wire
    _roundtrips(wire)


def test_switch_requires_exactly_one_selector() -> None:
    cases = tuple(t.SwitchCase(m, c) for m, c in _cases())
    with pytest.raises(ValueError):
        t.Switch(None, cases, _default())
    with pytest.raises(ValueError):
        t.Switch("mode", cases, _default(), on=t.Selection("grid-1"))
