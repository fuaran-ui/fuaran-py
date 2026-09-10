"""WIRE_FORMAT.md Section 3.3.3 — ``Binding.Local``'s declarative half, and a
decoded ``Binding.Computed``.

The corpus fixture ``nodes/form-local-debounce.json`` carries three
``"<closure>"`` sentinels. On the reference host they used to restore to a
``format`` returning ``""``, a ``parse`` that always failed and an ``onCommit``
returning a sentinel object, so a wire-authored debounced input rendered empty
and could never commit a keystroke. This host is a CODEC and a server renderer,
so its half of that finding is two things:

* the two refusals the declarative members bring — a codec case with no total
  inverse, and two commit destinations at once — each of which structure alone
  cannot make;
* the guarantee that a decoded ``Computed`` resolves to an ERROR naming its
  replacements (Phase 1667 gave this host's seam the channel; before it, only the
  negative half — never a value — could be asserted).

``test_reject.py`` already runs both refusal fixtures out of the shared corpus,
which is the cross-host parity leg. What is asserted HERE is the pairing that a
manifest-driven sweep cannot express: each refusal beside the accepting
neighbour that would otherwise let a refuse-everything decoder pass.
"""

from __future__ import annotations

import pytest

from fuaran_ui.model import Obj
from fuaran_ui.renderer import DECODED_COMPUTED_MESSAGE, WireSurvivabilityError, render_html
from fuaran_ui.renderer.bindings import resolve_binding
from fuaran_ui.schema.decode import decode_node
from fuaran_ui.schema.encode import encode_node

_DEBOUNCE = (
    '{"id":"form-local-debounce","kind":{"$type":"Form","fields":[{"id":"email-input",'
    '"kind":{"$type":"Text","onChange":"<closure>","value":{"$type":"Local","flushOn":'
    '{"$type":"OnDebounce","milliseconds":250},"format":"<closure>","initialFrom":'
    '{"$type":"Static","value":"draft@example.com"},"onCommit":"<closure>","parse":"<closure>"}},'
    '"label":"Email","required":true}],"onSubmit":{"$type":"Chain","ops":[]},"submitLabel":"Save"}}'
)

_DECLARED = (
    '{"id":"form-local-declared","kind":{"$type":"Form","fields":[{"id":"unit-price",'
    '"kind":{"$type":"Number","value":{"$type":"Local","codec":{"$type":"Number","decimals":2},'
    '"commitTo":"order.unitPrice","flushOn":{"$type":"OnBlur"},"format":"<closure>","initialFrom":'
    '{"$type":"State","defaultValue":0,"key":"order.unitPrice"},"parse":"<closure>"}},'
    '"label":"Unit price","required":false}],"onSubmit":{"$type":"Chain","ops":[]},"submitLabel":"Save"}}'
)

_CURRENCY_CODEC = (
    '{"id":"f","kind":{"$type":"Form","fields":[{"id":"amount","kind":{"$type":"Number","value":'
    '{"$type":"Local","codec":{"$type":"Currency","isoCode":"GBP"},"commitTo":"order.amount",'
    '"flushOn":{"$type":"OnBlur"},"format":"<closure>","initialFrom":{"$type":"State",'
    '"defaultValue":0,"key":"order.amount"},"parse":"<closure>"}},"label":"Amount","required":false}],'
    '"onSubmit":{"$type":"Chain","ops":[]},"submitLabel":"Save"}}'
)

_BOTH_DESTINATIONS = (
    '{"id":"f","kind":{"$type":"Form","fields":[{"id":"email","kind":{"$type":"Text","value":'
    '{"$type":"Local","commitTo":"form.email","flushOn":{"$type":"OnBlur"},"format":"<closure>",'
    '"initialFrom":{"$type":"Static","value":"a@b.c"},"onCommit":"<closure>","parse":"<closure>"}},'
    '"label":"Email","required":false}],"onSubmit":{"$type":"Chain","ops":[]},"submitLabel":"Save"}}'
)


def test_a_declared_local_round_trips_both_members() -> None:
    """The accepting neighbour of both refusals below.

    The assertion is on the BYTES, because that is this host's obligation: a
    codec that dropped either member would decode perfectly and re-encode a
    different document.
    """
    result = decode_node(_DECLARED)
    assert result.ok, getattr(result, "error", None)
    assert encode_node(result.value) == _DECLARED


def test_codec_without_a_total_inverse_is_refused_at_its_own_path() -> None:
    result = decode_node(_CURRENCY_CODEC)
    assert not result.ok
    assert result.error.code == "WRONG_TYPE"
    assert result.error.path == "$.kind.fields[0].kind.value.codec"


def test_two_commit_destinations_are_refused_at_the_commit_to_path() -> None:
    result = decode_node(_BOTH_DESTINATIONS)
    assert not result.ok
    assert result.error.code == "WRONG_TYPE"
    assert result.error.path == "$.kind.fields[0].kind.value.commitTo"


def test_either_destination_alone_decodes() -> None:
    """The refusal above is the PAIR, not either member — the go-red half.

    Without this, a decoder that refused every `Local` carrying `commitTo`, or
    every `Local` at all, would satisfy both refusal tests exactly.
    """
    assert decode_node(_DEBOUNCE).ok  # onCommit alone
    assert decode_node(_DECLARED).ok  # commitTo alone


def test_a_decoded_computed_resolves_to_an_error_naming_its_replacements() -> None:
    """The finding, from this host's end — Phase 1667.

    A decoded ``Computed`` has nothing to compute with — its whole payload is a
    closure that crosses the wire as ``"<closure>"`` — so WIRE_FORMAT §5 says it
    resolves to an ERROR naming its replacements, never to a value. Until 1667
    this seam had no error channel and answered ``None``, the slot's empty state:
    the negative half held (no ``0`` / ``""`` / ``False`` ever) while the
    positive half — the reader is TOLD, and told the remedy — did not.

    Go-red: against the pre-1667 seam nothing is raised and this fails on the
    first line.
    """
    computed = Obj("Computed", {})

    with pytest.raises(WireSurvivabilityError) as raised:
        resolve_binding(computed)

    assert str(raised.value) == DECODED_COMPUTED_MESSAGE
    assert "Binding.Expr" in str(raised.value), "and it names the case that replaced it"

    # ... and it still errors when the host furnishes a bag of sources whose keys
    # a fall-through lookup might otherwise have matched, which is the negative
    # half restated: no value of any kind, ever.
    with pytest.raises(WireSurvivabilityError):
        resolve_binding(computed, {"fn": "anything", "key": 0, "name": "", "nodeId": False})


def test_a_decoded_computed_in_a_metric_slot_errors_the_render() -> None:
    """The rendering end of the same rule.

    The fixture that used to render the empty state: a ``Metric`` whose value is
    a decoded ``Computed`` put an em-dash on the page, indistinguishable from a
    metric whose query has not answered yet. The error now reaches the caller of
    :func:`render_html` instead — Python's channel for "this document asked a
    question no decoded tree can answer" — so nothing plausible is printed.
    """
    metric = (
        '{"id":"m","kind":{"$type":"Metric","emphasis":"Normal","format":{"$type":"None"},'
        '"label":{"$type":"Literal","text":"Revenue"},"value":{"$type":"Computed","fn":"<closure>"},'
        '"tone":"Default","weight":"Standard"}}'
    )
    decoded = decode_node(metric)
    assert decoded.ok, "the document is well-formed and must decode"

    with pytest.raises(WireSurvivabilityError) as raised:
        render_html(decoded.value)

    assert str(raised.value) == DECODED_COMPUTED_MESSAGE


def test_every_other_binding_still_resolves() -> None:
    """The go-red half of the two above.

    A change that made EVERY binding raise would satisfy both of them and break
    the host. Two shapes that must keep answering: a value, and absence.
    """
    assert resolve_binding(Obj("Static", {"value": 41.0})) == 41.0
    assert resolve_binding(Obj("Query", {"name": "sales"})) is None
