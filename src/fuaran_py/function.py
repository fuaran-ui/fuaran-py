"""A signature-searchable function registry — deterministic, no-LLM composition.

The sibling of the F# ``Fuaran.Core.Function`` ``FunctionRegistry.findBySignature``
API (Phase 512): register functions by the node-kind they *produce* and the typed
*holes* they require, then search by signature — "what can I run to produce ``X`` with
the context I have?" — and compose a result by chaining matched functions rather than
prompting a model. It is the demo-parity engine behind Pattern-Bank.

**Reuse.** A hole's value envelope is the same closed :class:`~fuaran_py.ui.capability.HoleSpace`
vocabulary (``intRange`` / ``floatRange`` / ``stringLen`` / ``enum`` / ``anyString``) the
capability registry already ships — it *is* the F# ``ValueSpace``. The function
*signature* itself is richer than the capability arg-contract (it carries hole
addresses, a hole kind, a slot constraint, and a produced result type), so it is a
distinct shape here.

**Matching semantics** (pinned to the F# engine):

* A query is ``(result_type, available)`` — the node-kind to produce (``None`` = any) and
  the context holes on offer. Only a function's **required** holes gate a match; matching
  is **by address**.
* **Subsumes** — the "everything I can run with this context" query: the result type matches
  (or the query is a wildcard) and every required hole is satisfiable from context —
  ``available ⊆ required`` for value spaces, a slot-kind match for slots (``availableSpace``
  fits inside the function's declared envelope). This is assignable/subtype matching.
* **Exact** — the required-hole address set equals the context address set and each pair is
  shape-equal (kind + space + slot).
* **No ranking score** — candidates return in deterministic lexicographic id order (the
  ``ByResult`` index is a pre-filter, not a ranker).
* **No path is typed** — an empty candidate list, and a compose that cannot reach the target
  returns a :class:`NoPath` with a reason, never a guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from .ui.capability import HoleSpace

# ── signature shapes (the F# Signature / SigEntry / FunctionEntry twins) ──────

# A hole kind — the role a hole plays in a function's signature.
VALUE = "value"
SLOT = "slot"
REPEAT = "repeat"
ACTION = "action"


@dataclass(frozen=True)
class SigEntry:
    """One hole in a function signature (F# ``SigEntry``): matched by absolute ``addr``.

    A ``value`` / ``repeat`` hole carries a :class:`HoleSpace`; a ``slot`` hole carries a
    node-kind constraint (``slot``); an ``action`` hole carries an opaque effect tag."""

    addr: str
    name: str = ""
    kind: str = VALUE  # VALUE | SLOT | REPEAT | ACTION
    space: HoleSpace | None = None
    slot: str | None = None
    action: str | None = None
    required: bool = True


@dataclass(frozen=True)
class Signature:
    """A function's typed contract (F# ``Signature``) — its holes + an opaque effect summary."""

    name: str
    holes: tuple[SigEntry, ...] = ()
    effect: str | None = None


@dataclass(frozen=True)
class FunctionEntry:
    """A registered function: an id, the node-kind it *produces*, and its signature."""

    id: str
    result_type: str
    signature: Signature


@dataclass(frozen=True)
class SignatureQuery:
    """A search: the node-kind to produce (``None`` = any) + the context holes on offer."""

    result_type: str | None
    available: tuple[SigEntry, ...] = ()


# Match modes (F# ``MatchMode``).
SUBSUMES = "Subsumes"
EXACT = "Exact"


# ── value-space + slot subsumption (available ⊆ required) ────────────────────


def _bounds(space: HoleSpace) -> tuple[float, float]:
    lo = space.min if space.min is not None else -math.inf
    hi = space.max if space.max is not None else math.inf
    return lo, hi


def _space_subsumes(required: HoleSpace | None, available: HoleSpace | None) -> bool:
    """``True`` when the context's ``available`` space fits inside the function's
    ``required`` envelope (the F# ``spaceSubsumes`` — ``available ⊆ required``)."""
    if required is None:
        return True
    if available is None:
        return False
    if required.kind == "anyString":
        return available.kind in ("anyString", "stringLen")
    if required.kind != available.kind:
        return False
    if required.kind in ("intRange", "floatRange", "stringLen"):
        rlo, rhi = _bounds(required)
        alo, ahi = _bounds(available)
        return rlo <= alo and ahi <= rhi
    if required.kind == "enum":
        return set(available.choices) <= set(required.choices)
    return True  # anyString == anyString


def _slot_subsumes(required_slot: str | None, available_slot: str | None) -> bool:
    """``None`` required accepts any; otherwise the slot kinds must match (F# ``slotSubsumes``)."""
    return required_slot is None or required_slot == available_slot


def _hole_satisfied(required: SigEntry, available: dict[str, SigEntry]) -> bool:
    """A required hole is satisfiable from the context (matched by address)."""
    provided = available.get(required.addr)
    if provided is None:
        return False
    if required.kind in (VALUE, REPEAT):
        return _space_subsumes(required.space, provided.space)
    if required.kind == SLOT:
        return _slot_subsumes(required.slot, provided.slot)
    if required.kind == ACTION:
        return required.action is None or required.action == provided.action
    return True


def _matches_query(mode: str, query: SignatureQuery, entry: FunctionEntry) -> bool:
    if query.result_type is not None and entry.result_type != query.result_type:
        return False
    required = [h for h in entry.signature.holes if h.required]
    available = {a.addr: a for a in query.available}
    if mode == SUBSUMES:
        return all(_hole_satisfied(h, available) for h in required)
    # EXACT — the required-hole address set equals the context set, shape-equal per pair.
    if {h.addr for h in required} != {a.addr for a in query.available}:
        return False
    return all(
        available[h.addr].kind == h.kind and available[h.addr].space == h.space and available[h.addr].slot == h.slot
        for h in required
    )


# ── the registry ──────────────────────────────────────────────────────────────


@dataclass
class FunctionRegistry:
    """A signature-searchable table of functions, indexed by produced result type."""

    _entries: dict[str, FunctionEntry] = field(default_factory=dict)
    _by_result: dict[str, set[str]] = field(default_factory=dict)

    def register(self, entry: FunctionEntry) -> FunctionRegistry:
        if entry.id in self._entries:
            raise ValueError(f"function '{entry.id}' is already registered")
        self._entries[entry.id] = entry
        self._by_result.setdefault(entry.result_type, set()).add(entry.id)
        return self

    def get(self, function_id: str) -> FunctionEntry | None:
        return self._entries.get(function_id)

    def ids(self) -> list[str]:
        return sorted(self._entries)

    def find_by_signature(
        self, inputs: list[SigEntry] | tuple[SigEntry, ...], output: str | None, mode: str = SUBSUMES
    ) -> list[FunctionEntry]:
        """The registered functions matching ``(output, inputs)`` under ``mode``, returned in
        deterministic lexicographic id order (no relevance score). ``output=None`` is a
        produce-axis wildcard. Empty when nothing matches — the typed no-match."""
        query = SignatureQuery(output, tuple(inputs))
        candidate_ids = self._by_result.get(output, set()) if output is not None else set(self._entries)
        return [self._entries[i] for i in sorted(candidate_ids) if _matches_query(mode, query, self._entries[i])]

    # ── deterministic composition (the Pattern-Bank fast path) ────────────────

    def compose(
        self, output: str, inputs: list[SigEntry] | tuple[SigEntry, ...], mode: str = SUBSUMES, max_depth: int = 4
    ) -> ComposeResult:
        """Chain functions to produce ``output`` from the ``inputs`` context deterministically,
        or return a typed :class:`NoPath`. A direct signature match is a single step; an
        unfilled **slot** hole is recursively composed from the same context. No LLM, no guess."""
        available = tuple(inputs)
        seen: set[str] = set()
        steps = _compose(self, output, available, mode, max_depth, seen)
        if steps is None:
            return NoPath(f"no deterministic function chain reaches '{output}' from the given context")
        return ComposePath(tuple(steps))


@dataclass(frozen=True)
class ComposeStep:
    """One function applied in a composition — the function id + the slot it fills (``None`` at the root)."""

    function_id: str
    fills_slot: str | None = None


@dataclass(frozen=True)
class ComposePath:
    """A deterministic composition reaching the target — the ordered function steps (root last)."""

    steps: tuple[ComposeStep, ...]
    ok: Literal[True] = True


@dataclass(frozen=True)
class NoPath:
    """No deterministic chain reaches the target (typed, not a guess)."""

    reason: str
    ok: Literal[False] = False


type ComposeResult = ComposePath | NoPath


def _compose(
    registry: FunctionRegistry,
    output: str,
    available: tuple[SigEntry, ...],
    mode: str,
    depth: int,
    seen: set[str],
) -> list[ComposeStep] | None:
    """Return the ordered steps producing ``output`` (root last), or ``None``."""
    if depth <= 0 or output in seen:
        return None
    # Direct match: a function producing `output` whose every required hole is in context.
    direct = registry.find_by_signature(list(available), output, mode)
    if direct:
        return [ComposeStep(direct[0].id)]

    seen = seen | {output}
    available_by_addr = {a.addr: a for a in available}
    # Otherwise: a producer whose only unmet required holes are slots we can compose.
    for function_id in sorted(registry._by_result.get(output, set())):  # noqa: SLF001 — same-module access
        entry = registry._entries[function_id]  # noqa: SLF001
        required = [h for h in entry.signature.holes if h.required]
        sub_steps: list[ComposeStep] = []
        satisfiable = True
        for hole in required:
            if _hole_satisfied(hole, available_by_addr):
                continue
            if hole.kind == SLOT and hole.slot is not None:
                child = _compose(registry, hole.slot, available, mode, depth - 1, seen)
                if child is None:
                    satisfiable = False
                    break
                # tag the child's root with the slot it fills
                child[-1] = ComposeStep(child[-1].function_id, fills_slot=hole.addr)
                sub_steps.extend(child)
            else:
                satisfiable = False
                break
        if satisfiable:
            return [*sub_steps, ComposeStep(function_id)]
    return None


# ── SigEntry convenience constructors ────────────────────────────────────────


def value_hole(addr: str, space: HoleSpace | None = None, *, required: bool = True, name: str = "") -> SigEntry:
    return SigEntry(addr=addr, name=name or addr, kind=VALUE, space=space, required=required)


def slot_hole(addr: str, kind: str | None = None, *, required: bool = True, name: str = "") -> SigEntry:
    return SigEntry(addr=addr, name=name or addr, kind=SLOT, slot=kind, required=required)


def function_entry(
    function_id: str, result_type: str, holes: tuple[SigEntry, ...] = (), effect: str | None = None
) -> FunctionEntry:
    return FunctionEntry(function_id, result_type, Signature(function_id, holes, effect))
