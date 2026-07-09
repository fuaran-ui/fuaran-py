"""``fuaran_py.ai_tools`` — the AI-tools introspection surface.

The runtime introspection an orchestrator/agent needs to drive the Fuaran tree
natively from Python (the language AI agents are written in), over the Phase 234
headless codec and typed surface. Three parts, all provider-agnostic and
standard-library-only:

* :mod:`~fuaran_py.ai_tools.surface` — the **emittable-surface catalog**: what an
  agent may emit (recognised kinds / bindings / actions / text-sources), the
  bounded **value-space** projections, provider-agnostic **tool/function schemas**
  it registers, and :func:`~fuaran_py.ai_tools.surface.validate_emission` to check
  an emission against the typed surface.
* :mod:`~fuaran_py.ai_tools.introspect` — read-only **tree introspection**: kind,
  bound binding slots (with their canonical wire-form expression), and structure.
* :mod:`~fuaran_py.ai_tools.dispatch` — the **default-deny-by-shape dispatch gate**
  (FGP 3): an effect shape the host has not permitted is refused.
"""

from __future__ import annotations

from .dispatch import (
    GATED_EFFECT_SHAPES,
    INERT_SHAPES,
    DispatchDecision,
    DispatchGate,
    is_gated_effect,
)
from .introspect import (
    BindingSlot,
    NodeIntrospection,
    TreeIntrospection,
    binding_expression,
    binding_slots,
    child_ids,
    child_nodes,
    find_node,
    inspect_tree,
    kind_name,
    node_state,
    walk_nodes,
)
from .surface import (
    ValidationResult,
    action_cases,
    binding_cases,
    describe_surface,
    emit_tool_schema,
    emittable_kinds,
    text_source_cases,
    tool_schemas,
    validate_emission,
    value_space,
)

__all__ = [
    # surface
    "emittable_kinds",
    "binding_cases",
    "action_cases",
    "text_source_cases",
    "value_space",
    "describe_surface",
    "validate_emission",
    "ValidationResult",
    "tool_schemas",
    "emit_tool_schema",
    # introspect
    "kind_name",
    "binding_expression",
    "binding_slots",
    "child_nodes",
    "child_ids",
    "walk_nodes",
    "find_node",
    "node_state",
    "inspect_tree",
    "BindingSlot",
    "NodeIntrospection",
    "TreeIntrospection",
    # dispatch
    "DispatchGate",
    "DispatchDecision",
    "is_gated_effect",
    "GATED_EFFECT_SHAPES",
    "INERT_SHAPES",
]
