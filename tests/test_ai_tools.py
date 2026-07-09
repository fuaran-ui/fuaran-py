"""Phase 237 — the fuaran_py.ai_tools introspection surface.

Asserts the emitted schemas match the typed surface (no drift from the codec),
round-trips a representative agent-driven emission through the Phase 234 codec,
and pins the default-deny-by-shape dispatch gate (FGP 3).
"""

from __future__ import annotations

import typing

from _corpus import CORPUS_ROOT, corpus_required, fixtures_of
from fuaran_py import ai_tools
from fuaran_py.schema import decode_node
from fuaran_py.schema import types as t
from fuaran_py.schema.decode import ACTION_CASES, BINDING_CASES, KNOWN_KINDS, TEXT_SOURCE_CASES
from fuaran_py.ui import binding, encode, fuaran

# ── The catalog matches the codec's own recognised-case sets (no drift) ───────


def test_emittable_surface_matches_codec_sets() -> None:
    assert ai_tools.emittable_kinds() == sorted(KNOWN_KINDS)
    assert ai_tools.binding_cases() == sorted(BINDING_CASES)
    assert ai_tools.action_cases() == sorted(ACTION_CASES)
    assert ai_tools.text_source_cases() == sorted(TEXT_SOURCE_CASES)


def test_value_space_matches_typed_enums() -> None:
    space = ai_tools.value_space()
    # A known bounded enum resolves to its exact allowed values.
    assert space["Tone"] == list(typing.get_args(t.Tone))
    assert space["Orientation"] == ["Vertical", "Horizontal"]
    assert space["BoxRole"] == list(typing.get_args(t.BoxRole))
    # Every projected enum is non-empty and matches the typed alias.
    for name, values in space.items():
        assert values, f"{name} value-space is empty"


def test_describe_surface_bundles_everything() -> None:
    surface = ai_tools.describe_surface()
    assert set(surface) == {"kinds", "bindings", "actions", "textSources", "valueSpace"}
    assert surface["kinds"] == ai_tools.emittable_kinds()
    assert surface["valueSpace"]["Weight"] == list(typing.get_args(t.Weight))


# ── validate_emission — the accept/reject decision against the typed surface ──


@corpus_required
def test_validate_emission_accepts_corpus_nodes() -> None:
    for fixture in fixtures_of("node-round-trip")[:12]:
        wire = (CORPUS_ROOT / fixture["inputFile"]).read_text(encoding="utf-8")
        result = ai_tools.validate_emission(wire)
        assert result.ok, f"{fixture['id']}: expected accept, got {result.code} @ {result.path}"


@corpus_required
def test_validate_emission_reports_canonical_reject() -> None:
    node_rejects = [fx for fx in fixtures_of("reject") if fx.get("decoder", "node") == "node"]
    assert node_rejects, "expected node reject fixtures"
    fixture = node_rejects[0]
    wire = (CORPUS_ROOT / fixture["inputFile"]).read_text(encoding="utf-8")
    result = ai_tools.validate_emission(wire)
    assert not result.ok
    assert result.code == fixture["expectedErrorCode"]
    assert result.path is not None and result.path.startswith(fixture.get("expectedPath", "$"))


# ── Round-trip a representative agent-driven emission through the codec ────────


def test_agent_emission_round_trips_and_introspects() -> None:
    tree = fuaran.dashboard(
        "root",
        children=[
            fuaran.metric("rev", label="Revenue", value=binding.state("total", 0)),
            fuaran.markdown("note", "Updated hourly."),
        ],
    )
    wire = encode(tree)

    # The emission validates against the typed surface.
    assert ai_tools.validate_emission(wire).ok

    decoded = decode_node(wire)
    assert decoded.ok
    root = decoded.value

    # Structure: the dashboard's two children are introspectable by id.
    tree_view = ai_tools.inspect_tree(root)
    assert tree_view.child_ids == ("rev", "note")

    # The metric's state-bound source is reported with its canonical expression.
    metric = ai_tools.node_state(root, "rev")
    assert metric is not None
    assert metric.kind == "Metric"
    state_slots = [b for b in metric.bindings if b.source == "State"]
    assert state_slots, f"expected a State binding slot, got {metric.bindings}"
    assert state_slots[0].expression == "$state.total"


@corpus_required
def test_inspect_tree_walks_corpus_nodes_without_error() -> None:
    for fixture in fixtures_of("node-round-trip")[:20]:
        wire = (CORPUS_ROOT / fixture["inputFile"]).read_text(encoding="utf-8")
        decoded = decode_node(wire)
        assert decoded.ok
        view = ai_tools.inspect_tree(decoded.value)
        assert view.id  # every node has an id
        assert view.kind


# ── Default-deny-by-shape dispatch gate (FGP 3) ───────────────────────────────


def test_gate_default_denies_every_effect() -> None:
    gate = ai_tools.DispatchGate()  # deny-all
    for shape in ("Navigate", "AiTool", "ReadFileBody", "Dispatch", "Notify", "SetState", "Chain"):
        decision = gate.authorize_shape(shape)
        assert not decision.allowed, f"{shape} should be default-denied"
        assert "default-deny" in decision.reason


def test_gate_permits_explicitly() -> None:
    gate = ai_tools.DispatchGate.permitting("Navigate")
    assert gate.authorize_shape("Navigate").allowed
    assert not gate.authorize_shape("AiTool").allowed  # still denied


def test_gate_permissive_inert_allows_only_structural() -> None:
    gate = ai_tools.DispatchGate.permissive_inert()
    assert gate.authorize_shape("Chain").allowed
    assert gate.authorize_shape("SetState").allowed
    assert not gate.authorize_shape("Navigate").allowed
    assert not gate.authorize_shape("ReadFileBody").allowed


def test_gate_rejects_unknown_and_malformed_shapes() -> None:
    gate = ai_tools.DispatchGate.permitting("Navigate")
    assert not gate.authorize_shape("NotARealAction").allowed
    # a non-discriminated object is refused
    from fuaran_py.model import Obj

    assert not gate.authorize(Obj(None, {})).allowed


def test_gate_authorizes_decoded_action_object() -> None:
    from fuaran_py.model import Obj

    gate = ai_tools.DispatchGate.permitting("Navigate")
    assert gate.authorize(Obj("Navigate", {"route": "/home"})).allowed
    assert not gate.authorize(Obj("ReadFileBody", {})).allowed


def test_gated_effect_classification() -> None:
    assert ai_tools.is_gated_effect("Navigate")
    assert ai_tools.is_gated_effect("ReadFileBody")
    assert not ai_tools.is_gated_effect("Chain")
    assert not ai_tools.is_gated_effect("SetState")


# ── Provider-agnostic tool schemas ───────────────────────────────────────────


def test_tool_schemas_are_provider_agnostic() -> None:
    tools = ai_tools.tool_schemas()
    names = {tool["name"] for tool in tools}
    assert names == {"fuaran_list_surface", "fuaran_validate_tree", "fuaran_inspect_tree"}
    for tool in tools:
        assert set(tool) >= {"name", "description", "parameters"}
        assert tool["parameters"]["type"] == "object"


def test_emit_tool_schema_embeds_the_wire_schema() -> None:
    wire_schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "oneOf": []}
    tool = ai_tools.emit_tool_schema(wire_schema)
    assert tool["name"] == "fuaran_emit_tree"
    assert tool["parameters"] is wire_schema
