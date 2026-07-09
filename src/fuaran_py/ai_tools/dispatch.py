"""Default-deny-by-shape dispatch gate (FGP 3).

An agent driving the tree proposes ``Action`` effects; the host decides whether
each may run. This is the policy gate the introspection/dispatch path consults
**by shape** — the action's wire discriminator (`$type`) — before any effectful
action is dispatched. It is **default-deny**: an effect shape the host has not
explicitly permitted is refused, so a new or unexpected effect can never fire by
omission (the same posture as the reference tier's runtime dispatch gate and the
capability seam's default-deny-by-shape validation).

The gate is a *policy* only — it never executes an action (a Python host executes
the permitted effect downstream). It classifies an action's shape and returns an
allow/deny decision with a reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model import Obj
from ..schema.decode import ACTION_CASES

# Effectful shapes that reach outside the pure state-update loop — they require
# explicit host permission (mirrors the reference runtime's gated set:
# call/navigate/ai-tool/read-file-body, plus the host-effect notify / clipboard /
# capability-invoke). An effect not in this set is still default-denied unless
# permitted; this set is what a host reasons about when granting.
GATED_EFFECT_SHAPES = frozenset(
    {"Dispatch", "Navigate", "AiTool", "ReadFileBody", "Notify", "WriteToClipboard", "Invoke"}
)

# Structural, side-effect-free composition — safe to permit broadly. ``Chain`` is
# a sequence (its members are gated individually); ``SetState`` mutates only the
# local MVU state.
INERT_SHAPES = frozenset({"Chain", "SetState"})


@dataclass(frozen=True)
class DispatchDecision:
    """The gate's verdict for one action shape."""

    shape: str
    allowed: bool
    reason: str


def is_gated_effect(shape: str) -> bool:
    """Whether a shape is an outward/host effect that must be explicitly permitted."""
    return shape in GATED_EFFECT_SHAPES


@dataclass(frozen=True)
class DispatchGate:
    """A default-deny-by-shape policy gate.

    ``DispatchGate()`` denies every action shape. A host opts shapes in explicitly
    with :meth:`permitting`; :meth:`permissive_inert` grants only the side-effect-free
    structural shapes (``Chain`` / ``SetState``), leaving every outward effect denied.
    """

    allowed: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def deny_all(cls) -> DispatchGate:
        return cls(frozenset())

    @classmethod
    def permitting(cls, *shapes: str) -> DispatchGate:
        return cls(frozenset(shapes))

    @classmethod
    def permissive_inert(cls) -> DispatchGate:
        """Permit only the inert structural shapes; every gated effect stays denied."""
        return cls(frozenset(INERT_SHAPES))

    def with_permitted(self, *shapes: str) -> DispatchGate:
        return DispatchGate(self.allowed | frozenset(shapes))

    def authorize_shape(self, shape: str) -> DispatchDecision:
        """The verdict for a bare action-shape string (default-deny)."""
        if shape not in ACTION_CASES:
            return DispatchDecision(shape, False, f"unknown action shape {shape!r}")
        if shape in self.allowed:
            return DispatchDecision(shape, True, "explicitly permitted")
        return DispatchDecision(shape, False, "default-deny by shape (not permitted)")

    def authorize(self, action: Obj) -> DispatchDecision:
        """The verdict for a decoded ``Action`` object (reads its ``$type`` tag)."""
        if not isinstance(action, Obj) or action.tag is None:
            return DispatchDecision("<malformed>", False, "not a discriminated action object")
        return self.authorize_shape(action.tag)
