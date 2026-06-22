"""The Python capability host-registration seam (the §3 hard-stuff entry point).

The Compute layer splits notebook work into the **liftable** majority — the
declarative :mod:`fuaran_py.ui.compute` algebra that travels as data — and the
**genuinely-arbitrary** remainder: a scoped, host-registered *capability* (model
inference, scipy, custom numpy) the wire references by id, never by code.

This module ships the **unblocked** half of that seam for the Python host: a registry
where a Python capability *body* is declared (id + signature) and registered, so a
Python host (server / Pyodide island) can resolve an invocation to a body. It is the
Python analogue of the reference invocable-capability registry.

.. note::
   **The capability/invoke *wire* shape is gated on the F# Capability/Invoke phase.**
   Until that ships and lands its corpus fixtures, this host-side registry has **no
   canonical encode/decode** — there is no fixed wire to match yet, and guessing it
   would risk a parity break. ``decode``/``encode`` for ``Capability`` / ``Binding.Invoke``
   The **``Invoke`` wire** (``Binding.Invoke`` / ``Action.Invoke``) ships here now that its
   canonical shape is fixed: :func:`invoke` authors it and :func:`parse_invocation` decodes
   it. The wider ``Placement`` / ``Deferred`` surface follows as those fixtures are exercised.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..canonical import encode_value
from ..model import Arr, Obj
from ..model import Value as WireValue

# A capability body: typed args (by name) → a realized value.
CapabilityBody = Callable[[Mapping[str, Any]], Any]

# An invocation argument's value — a bare JSON scalar (the wire carries it untyped).
InvokeArgValue = str | int | float | bool


@dataclass(frozen=True)
class HoleSpace:
    """A declared argument's value space — the validation envelope for an invocation arg.

    A closed contract by shape (default-deny): an arg outside its space is rejected before
    the body runs. Mirrors the reference hole-space vocabulary (int/float range, string
    length, enum, any-string)."""

    kind: str  # "intRange" | "floatRange" | "stringLen" | "enum" | "anyString"
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] = ()

    def accepts(self, value: object) -> bool:
        if self.kind == "intRange":
            return isinstance(value, int) and not isinstance(value, bool) and self._in_range(value)
        if self.kind == "floatRange":
            return isinstance(value, (int, float)) and not isinstance(value, bool) and self._in_range(float(value))
        if self.kind == "stringLen":
            return isinstance(value, str) and self._in_range(len(value))
        if self.kind == "enum":
            return isinstance(value, str) and value in self.choices
        if self.kind == "anyString":
            return isinstance(value, str)
        return False

    def _in_range(self, n: float) -> bool:
        if self.min is not None and n < self.min:
            return False
        return not (self.max is not None and n > self.max)


@dataclass(frozen=True)
class Signature:
    """A capability's typed argument contract — ordered ``(name, space)`` holes."""

    holes: tuple[tuple[str, HoleSpace], ...] = ()

    def validate(self, args: Mapping[str, Any]) -> str | None:
        """``None`` if every declared hole is present + in-space and no extra arg appears,
        else a human reason (default-deny by shape)."""
        declared = {name for name, _ in self.holes}
        for name in args:
            if name not in declared:
                return f"argument '{name}' addresses no declared hole"
        for name, space in self.holes:
            if name not in args:
                return f"missing argument '{name}'"
            if not space.accepts(args[name]):
                return f"argument '{name}' is outside its declared space"
        return None


@dataclass(frozen=True)
class Capability:
    """A declared, host-registered unit of arbitrary compute (id + signature + body).

    Placement (where it runs) is a typed contract that lands with the capability wire
    phase; this declaration is the Python-host body registration."""

    id: str
    signature: Signature
    body: CapabilityBody


def capability(
    id: str,  # noqa: A002 — the wire/registry field name
    body: CapabilityBody,
    holes: Sequence[tuple[str, HoleSpace]] = (),
) -> Capability:
    """Declare a capability: an id, a Python body, and its typed argument holes."""
    return Capability(id, Signature(tuple(holes)), body)


class InvokeError(Exception):
    """A capability invocation that failed validation or dispatch (a named, recoverable failure)."""


@dataclass
class CapabilityRegistry:
    """A host's capability table. ``register`` adds a body; ``invoke`` validates the args
    against the signature (default-deny) before dispatching to the Python body."""

    _by_id: dict[str, Capability] = field(default_factory=dict)

    def register(self, cap: Capability) -> CapabilityRegistry:
        if cap.id in self._by_id:
            raise InvokeError(f"capability '{cap.id}' is already registered")
        self._by_id[cap.id] = cap
        return self

    def ids(self) -> list[str]:
        return sorted(self._by_id)

    def get(self, capability_id: str) -> Capability | None:
        return self._by_id.get(capability_id)

    def invoke(self, capability_id: str, args: Mapping[str, Any]) -> Any:
        cap = self._by_id.get(capability_id)
        if cap is None:
            raise InvokeError(f"no capability registered for id '{capability_id}'")
        reason = cap.signature.validate(args)
        if reason is not None:
            raise InvokeError(f"invalid invocation of '{capability_id}': {reason}")
        return cap.body(args)

    def invoke_wire(self, parsed: object) -> Any:
        """Decode a parsed ``Invoke`` wire object and dispatch it (validate → body)."""
        inv = parse_invocation(parsed)
        return self.invoke(inv.capability_id, inv.args)


# Hole-space constructors (the polars-author-facing vocabulary).


def int_range(lo: int, hi: int) -> HoleSpace:
    return HoleSpace("intRange", float(lo), float(hi))


def float_range(lo: float, hi: float) -> HoleSpace:
    return HoleSpace("floatRange", lo, hi)


def string_len(lo: int, hi: int) -> HoleSpace:
    return HoleSpace("stringLen", float(lo), float(hi))


def enum(*choices: str) -> HoleSpace:
    return HoleSpace("enum", choices=tuple(choices))


def any_string() -> HoleSpace:
    return HoleSpace("anyString")


# ── The Invoke wire (Binding.Invoke / Action.Invoke) ─────────────────────────
#
# A capability reference by id + ordered ``(addr, value)`` args. The wire shape is
# identical in a binding position (a value source) and an action position (an effect):
# ``{"$type":"Invoke","args":[{"addr":…,"value":…}],"capabilityId":…}``.


@dataclass(frozen=True)
class Invoke:
    """An ``Invoke`` authoring value — usable as a node ``source`` (binding) or an
    ``onClick`` / action (effect); both lower to the same canonical wire."""

    capability_id: str
    args: tuple[tuple[str, InvokeArgValue], ...] = ()

    def to_wire(self) -> WireValue:
        return Obj(
            "Invoke",
            {
                "args": Arr([Obj(None, {"addr": addr, "value": value}) for addr, value in self.args]),
                "capabilityId": self.capability_id,
            },
        )

    def to_json(self) -> str:
        return encode_value(self.to_wire())


def invoke(capability_id: str, **args: InvokeArgValue) -> Invoke:
    """Author an ``Invoke`` — ``invoke("forecast.revenue", horizon="12", scenario="base")``.

    Argument order is preserved (kwargs are ordered); it is the wire ``args`` order."""
    return Invoke(capability_id, tuple(args.items()))


@dataclass(frozen=True)
class Invocation:
    """A decoded invocation — the capability id + its args as a name→value mapping."""

    capability_id: str
    args: dict[str, Any]


def parse_invocation(parsed: object) -> Invocation:
    """Decode a parsed ``Invoke`` wire object into a typed :class:`Invocation`.

    Raises :class:`InvokeError` on a malformed shape (a default-deny decode)."""
    if not isinstance(parsed, dict) or parsed.get("$type") != "Invoke":
        raise InvokeError("not an Invoke object")
    cap = parsed.get("capabilityId")
    if not isinstance(cap, str):
        raise InvokeError("Invoke.capabilityId must be a string")
    raw_args = parsed.get("args")
    if not isinstance(raw_args, list):
        raise InvokeError("Invoke.args must be an array")
    args: dict[str, Any] = {}
    for entry in raw_args:
        if not isinstance(entry, dict) or "addr" not in entry or "value" not in entry:
            raise InvokeError("each Invoke arg needs 'addr' and 'value'")
        addr = entry["addr"]
        if not isinstance(addr, str):
            raise InvokeError("Invoke arg 'addr' must be a string")
        args[addr] = entry["value"]
    return Invocation(cap, args)
