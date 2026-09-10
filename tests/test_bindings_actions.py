"""Phase 1580 — ``Binding.Query`` / ``Invoke`` / ``Action.Call`` / ``AiTool`` /
``TextSource.I18n`` become authorable from Python.

The decoder has carried all five for a long time; what the typed model did not
have was a MEMBERSHIP. ``fuaran_ui.ui.encode`` needs a ``.to_wire()`` root, so a
binding or action case absent from the typed unions has no spelling — and one of
them shows exactly why membership rather than lowering is the thing being fixed:
``Invoke`` already lowered to the right bytes from ``ui.capability``, and both its
fixtures already encoded byte-identically here, while every cross-host consumer
that reasons over ``t.Binding`` / ``t.Action`` measured it as unmodelled. A record
outside the union is invisible to everything but the call site that names it.

The per-fixture byte-identity assertions live where this host already keeps them,
in ``tests/test_ui_authoring.py``'s single parity table, because a second table is
how two of them end up disagreeing about what the corpus says. What lives here is
everything a fixture set cannot pin on its own:

* the CENSUS — the phase's set is computed from the corpus, and the fixtures
  it does NOT author are named with the construct that still blocks them, so a
  new fixture carrying one of these constructs reddens this file rather
  than sitting silently unauthored;
* the UNION MEMBERSHIP, resolved the way a cross-host probe resolves it
  (``typing.get_args`` over the alias), which is the one property a byte test
  structurally cannot see;
* the OMISSION rule each record obeys — ``dependsOn`` omits at empty while
  ``args`` is WRITTEN at empty, and those are opposite answers to what looks like
  one question;
* the ROUND TRIP through this host's own codec, because the acceptance asks for
  decode and not only encode;
* the DISPATCH GATE over the three new effect paths, including the acceptance's
  own claim: an authored ``Invoke`` naming an unregistered capability is refused
  exactly as a decoded one is.
"""

from __future__ import annotations

import json
import typing

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_ui import decode_node, encode_node
from fuaran_ui.ai_tools import DispatchGate, is_gated_effect
from fuaran_ui.canonical import encode_value
from fuaran_ui.model import Arr, Node, Obj
from fuaran_ui.schema import types as t
from fuaran_ui.ui import action, binding, encode
from fuaran_ui.ui.capability import (
    CapabilityRegistry,
    InvokeError,
    any_string,
    capability,
    parse_invocation,
)
from test_ui_authoring import _authored

#: The wire tags this phase makes authorable, and the fixtures each is expected
#: to appear in. Both halves are ASSERTED against the corpus below rather than
#: trusted: a count in a docstring is wrong out loud the first time the corpus
#: moves and nobody re-reads the paragraph.
PHASE_TAGS = ("Query", "Invoke", "I18n", "Call", "AiTool")

#: Fixtures that carry one of :data:`PHASE_TAGS` and that this phase deliberately
#: does NOT author, each with the construct that still blocks it.
#:
#: Both are blocked on ``Binding.Expr`` — the WIRE binding, which is a different
#: thing from the compute-layer ``Expr`` expression DSL this host already ships.
#: The DSL builds a ``ColExpr`` tree for a ``Transform`` pipeline; the wire
#: binding is a ``{"$type":"Expr","expr":…,"params":…}`` case of the ``Binding``
#: union, which the decoder recognises and the typed model does not spell. It is
#: excluded here on the phase's own terms rather than overlooked: it couples to
#: the whole ``ColExpr`` lowering and its ``params`` binding list, which is a
#: surface of its own and not one of the five cases this phase adds.
BLOCKED = {
    "expr-params-state-selection": "Binding.Expr",
    "node-visible": "Binding.Expr",
}


def _fixtures_carrying(tag: str) -> list[str]:
    """Every node fixture whose canonical bytes carry ``$type: tag`` ANYWHERE.

    A substring match on the canonical form, which is exact here because the
    corpus files are canonical JSON: ``"$type":"Query"`` has one spelling, no
    whitespace, and the token cannot occur inside a string value without its own
    escaping. Matching the whole tree rather than the root is what pulls in the
    composite fixtures — a ``Call`` inside a ``Confirm`` inside a button is as
    unauthorable as one at the root.
    """
    return [
        path.stem
        for path in sorted((CORPUS_ROOT / "nodes").glob("*.json"))
        if f'"$type":"{tag}"' in path.read_text(encoding="utf-8")
    ]


def _phase_fixtures() -> set[str]:
    ids: set[str] = set()
    for tag in PHASE_TAGS:
        ids.update(_fixtures_carrying(tag))
    return ids


# ── The census ───────────────────────────────────────────────────────────────


@corpus_required
def test_every_fixture_carrying_a_phase_construct_is_authored_or_named() -> None:
    """The set is COMPUTED, so it cannot silently fall behind the corpus.

    A new fixture carrying ``Query`` / ``Invoke`` / ``I18n`` / ``Call`` /
    ``AiTool`` either joins the parity table or joins :data:`BLOCKED` with the
    construct that stops it. What it cannot do is arrive unnoticed, which is the
    state the quarantine on the other side of this boundary exists to detect and
    the state this test exists to prevent.
    """
    carried = _phase_fixtures()
    authored = set(_authored())
    unaccounted = sorted(carried - authored - set(BLOCKED))
    assert not unaccounted, (
        f"corpus fixtures carrying a Phase 1580 construct that are neither authored "
        f"in test_ui_authoring's parity table nor named in BLOCKED: {unaccounted}"
    )


@corpus_required
def test_the_blocked_set_still_blocks() -> None:
    """A ``BLOCKED`` entry naming a construct the host has since grown is a stale
    claim, and a stale claim reads exactly like a measured one.

    The falsifier is cheap and total: every name in ``BLOCKED`` must still be
    absent from the ``Binding`` union. When ``Binding.Expr`` lands, this reddens
    and the entries come out — which is the same self-clearing shape the
    cross-host quarantine uses, applied on this side of the boundary.
    """
    absent = {c.split(".", 1)[1] for c in BLOCKED.values()}
    modelled = {getattr(case, "__name__", "") for case in typing.get_args(t.Binding)}
    assert not (absent & modelled), (
        f"BLOCKED names {sorted(absent & modelled)} as unmodelled, but the Binding union "
        "now carries it — author the fixtures and remove the entries"
    )


@corpus_required
def test_every_blocked_id_names_a_real_fixture() -> None:
    ids = {path.stem for path in (CORPUS_ROOT / "nodes").glob("*.json")}
    missing = sorted(set(BLOCKED) - ids)
    assert not missing, f"BLOCKED names fixtures the corpus does not carry: {missing}"


# ── Union membership — the property a byte test cannot see ───────────────────


@pytest.mark.parametrize(
    "alias,case",
    [
        (t.Binding, "Query"),
        (t.Binding, "Invoke"),
        (t.TextSource, "I18n"),
        (t.Action, "Call"),
        (t.Action, "AiTool"),
        (t.Action, "Invoke"),
    ],
)
def test_the_case_is_a_member_of_its_union(alias: object, case: str) -> None:
    """Resolved through ``typing.get_args``, which is how a consumer OUTSIDE this
    package asks what the host models — and how the estate's cross-host projection
    probe asks it.

    ``Invoke`` appears twice on purpose. One record is a member of both unions,
    because the wire shape is identical in a value position and an effect
    position; the reference tier spells that as two DU cases carrying the same
    fields, and one class in two aliases is the same statement with nothing to
    keep in step.
    """
    names = {getattr(member, "__name__", "") for member in typing.get_args(alias)}
    assert case in names, f"{case} is not a member of the union (members: {sorted(names)})"


def test_the_invoke_record_has_one_definition() -> None:
    """``ui.capability.Invoke`` is the SAME class as ``schema.types.Invoke``.

    Before this phase there were two records that could have lowered to the same
    wire: the one the capability seam shipped, and the one the union needed. Two
    records emitting one wire shape is the drift class the corpus cannot catch,
    because both would pass every byte test right up until one of them changed.
    """
    from fuaran_ui.ui import capability as cap

    assert cap.Invoke is t.Invoke
    assert cap.InvokeArg is t.InvokeArg


# ── Omission rules ───────────────────────────────────────────────────────────


def encode_value_of(record: object) -> str:
    """The canonical JSON of one lowered record, on its own — the unit these
    omission rules are actually about."""
    return encode_value(record.to_wire())  # type: ignore[attr-defined]


def test_query_omits_an_empty_depends_on() -> None:
    assert encode_value_of(t.Query("orders")) == '{"$type":"Query","name":"orders"}'


def test_query_writes_depends_on_in_the_authored_order() -> None:
    """The wire slot is a LIST, so two orders are two documents; nothing here
    sorts it."""
    assert (
        encode_value_of(t.Query("orders", ("status", "region")))
        == '{"$type":"Query","dependsOn":["status","region"],"name":"orders"}'
    )
    assert (
        encode_value_of(t.Query("orders", ("region", "status")))
        == '{"$type":"Query","dependsOn":["region","status"],"name":"orders"}'
    )


def test_i18n_writes_an_empty_args_bag_rather_than_omitting_it() -> None:
    """The opposite answer to ``Query.dependsOn``'s, and deliberately so: the
    reference IDL declares ``args`` REQUIRED, so a key with no placeholders is
    ``"args":{}`` on every host. ``tooltip-metric-1`` is the corpus's witness."""
    assert encode_value_of(t.I18n("metric.latency.hint")) == '{"$type":"I18n","args":{},"key":"metric.latency.hint"}'


def test_invoke_writes_an_empty_args_list_rather_than_omitting_it() -> None:
    """Same rule as ``I18n``, same reason: ``args`` is required, and an
    argument-free capability is a real thing whose emptiness is a statement."""
    assert encode_value_of(t.Invoke("model.refresh")) == '{"$type":"Invoke","args":[],"capabilityId":"model.refresh"}'


def test_call_omits_both_result_slots_when_neither_is_declared() -> None:
    """The third real shape — ``composite-tabs-panels``'s form submits and reads
    nothing back — so this is not an under-specified call to be defaulted away."""
    assert encode_value_of(t.Call("/api/preferences")) == '{"$type":"Call","endpoint":"/api/preferences"}'


def test_call_writes_the_closure_sentinel_only_when_a_handler_is_declared() -> None:
    assert (
        encode_value_of(t.Call("/api/refresh", on_result=True))
        == '{"$type":"Call","endpoint":"/api/refresh","onResult":"<closure>"}'
    )


def test_call_result_targets_carry_their_wire_tags_not_their_python_names() -> None:
    """``IntoState`` / ``IntoQuery`` are Python names for wire tags ``State`` /
    ``Query``; the two ``Binding`` cases that READ these slots already hold those
    names in this flat namespace. The rename is what stops an author passing a
    binding where a result target belongs — and the wire must not see it."""
    assert (
        encode_value_of(t.Call("/api/total", into=t.IntoState("total")))
        == '{"$type":"Call","endpoint":"/api/total","into":{"$type":"State","key":"total"}}'
    )
    assert (
        encode_value_of(t.Call("/api/orders", into=t.IntoQuery("orders")))
        == '{"$type":"Call","endpoint":"/api/orders","into":{"$type":"Query","name":"orders"}}'
    )


# ── The round trip through this host's own codec ─────────────────────────────


@corpus_required
@pytest.mark.parametrize("fixture_id", sorted(_phase_fixtures() - set(BLOCKED)))
def test_the_authored_tree_decodes_and_re_encodes_byte_stably(fixture_id: str) -> None:
    """The acceptance asks for the fixtures to ENCODE byte-identically, which the
    parity table asserts. This asserts the other direction on the same trees: the
    bytes this host authors are bytes this host reads back to the same value."""
    authored = encode(_authored()[fixture_id])
    decoded = decode_node(authored)
    assert decoded.ok, decoded
    assert encode_node(decoded.value) == authored


# ── The dispatch gate over the three new effect paths ────────────────────────


@pytest.mark.parametrize("shape", ["Invoke", "Call", "AiTool"])
def test_each_new_dispatch_path_is_a_gated_effect(shape: str) -> None:
    """FGP 3 — every one of the three reaches OUTSIDE the pure state-update loop
    and so needs explicit host permission.

    ``Call`` is the one that was wrong: it was named first in the module's own
    description of the gated set ("call/navigate/ai-tool/read-file-body") and
    absent from the set itself, so ``is_gated_effect("Call")`` answered False. The
    VERDICT was never affected — an unclassified shape is default-denied anyway —
    but a host asking which shapes it must decide about was told an HTTP call to a
    host endpoint was not one of them.
    """
    assert is_gated_effect(shape)


@pytest.mark.parametrize("shape", ["Invoke", "Call", "AiTool"])
def test_each_new_dispatch_path_is_denied_by_default(shape: str) -> None:
    """Neither an empty gate nor the inert-only gate lets one through, and the
    permitting gate does — the second half matters as much as the first, because a
    gate that refused everything would pass the refusal test and be useless."""
    assert not DispatchGate.deny_all().authorize_shape(shape).allowed
    assert not DispatchGate.permissive_inert().authorize_shape(shape).allowed
    assert DispatchGate.permitting(shape).authorize_shape(shape).allowed


def _find_obj(value: object, tag: str) -> Obj | None:
    """The first ``Obj`` carrying ``tag``, anywhere in a structural wire value."""
    if isinstance(value, Node):
        return _find_obj(value.kind, tag) or _find_obj(Arr(list(value.extras.values())), tag)
    if isinstance(value, Obj):
        if value.tag == tag:
            return value
        return _find_obj(Arr(list(value.fields.values())), tag)
    if isinstance(value, Arr):
        for item in value.items:
            found = _find_obj(item, tag)
            if found is not None:
                return found
    return None


@corpus_required
@pytest.mark.parametrize(
    "fixture_id,shape",
    [("btn-invoke", "Invoke"), ("call-into", "Call"), ("btn-json-payloads", "AiTool")],
)
def test_the_gate_reads_an_authored_action_exactly_as_it_reads_a_decoded_one(fixture_id: str, shape: str) -> None:
    """The gate takes a decoded ``Obj``; an authored action's ``to_wire()`` IS one.

    Asserted against the corpus fixture rather than against a hand-built object,
    so the two inputs are provably the same document — which is the whole of what
    "the authoring constructors produce exactly the shapes the gate recognises"
    can mean. The gate is then run over BOTH, because equality of inputs is only
    half the claim if the verdict is never taken.
    """
    authored = _find_obj(_authored()[fixture_id].to_wire(), shape)
    decoded = decode_node((CORPUS_ROOT / "nodes" / f"{fixture_id}.json").read_text(encoding="utf-8"))
    assert decoded.ok, decoded
    from_wire = _find_obj(decoded.value, shape)

    assert authored is not None, f"no authored {shape} in {fixture_id}"
    assert from_wire is not None, f"no decoded {shape} in {fixture_id}"
    assert authored == from_wire

    gate = DispatchGate.permitting(shape)
    assert gate.authorize(authored).allowed
    assert gate.authorize(from_wire).allowed
    assert not DispatchGate.deny_all().authorize(authored).allowed


# ── The acceptance's registry claim ──────────────────────────────────────────


def _as_json(obj: Obj) -> object:
    return json.loads(encode_value(obj))


@corpus_required
def test_an_authored_invoke_naming_an_unregistered_capability_is_refused_as_a_decoded_one_is() -> None:
    """Two paths into the same registry, and the refusal must not depend on which.

    The authored path goes record → canonical JSON → ``parse_invocation``; the
    decoded path goes corpus bytes → this host's decoder → the same parse. A
    registry that answered differently for the two would mean an authored tree
    could reach a body an equivalent decoded tree could not, which is a hole in a
    default-deny seam rather than an inconvenience.
    """
    registry = CapabilityRegistry()
    registry.register(capability("something.else", lambda _a: 1, holes=[("x", any_string())]))

    authored = binding.invoke("forecast.revenue", horizon="12", scenario="base")
    assert isinstance(authored, t.Invoke)
    from_authored = parse_invocation(json.loads(authored.to_json()))

    decoded = decode_node((CORPUS_ROOT / "nodes" / "metric-invoke.json").read_text(encoding="utf-8"))
    assert decoded.ok, decoded
    invoke_obj = _find_obj(decoded.value, "Invoke")
    assert invoke_obj is not None
    from_decoded = parse_invocation(_as_json(invoke_obj))

    assert from_authored == from_decoded

    with pytest.raises(InvokeError) as authored_err:
        registry.invoke(from_authored.capability_id, from_authored.args)
    with pytest.raises(InvokeError) as decoded_err:
        registry.invoke(from_decoded.capability_id, from_decoded.args)
    assert str(authored_err.value) == str(decoded_err.value)
    assert "forecast.revenue" in str(authored_err.value)


def test_an_authored_invoke_reaching_a_registered_capability_still_runs() -> None:
    """The other half of default-deny: the seam must let a registered id THROUGH,
    and the args must arrive under the names the holes declare."""
    registry = CapabilityRegistry()
    registry.register(
        capability(
            "forecast.revenue",
            lambda a: f"{a['scenario']}@{a['horizon']}",
            holes=[("horizon", any_string()), ("scenario", any_string())],
        )
    )
    authored = action.invoke("forecast.revenue", horizon="12", scenario="base")
    assert registry.invoke_wire(json.loads(authored.to_json())) == "base@12"  # type: ignore[union-attr]
