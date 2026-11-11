"""``fuaran_ui.merge`` — Fuaran Core's conflict report over two op deltas.

Merge by fold (WIRE_FORMAT.md §34) is the op DAG's only merge since fuaran#2645:
the policy in :mod:`fuaran_ui.reconcile` judges exactly the op pairs this module
reports. The three-way tree merge that used to live here (``merge_3way``, the
human-primacy layer and its refusal envelope) retired with the
``merge-conformance`` triad families (fuaran#2646); a precedence merge is a script
the reference host offers, not a tree merge.
"""

from __future__ import annotations

from dataclasses import dataclass

from .canonical import utf16_order_key
from .footprint import Footprint, footprint_in, slot_clash, slots_against_node, structural
from .model import Node, Obj
from .ops.apply import apply
from .result import Ok

__all__ = [
    "CONCURRENT_UPDATE",
    "INSERT_POSITION_CLASH",
    "MOVE_VS_REMOVE",
    "SLOT_CLASH",
    "OpConflict",
    "conflicts",
    "conflicts_in",
]

# ─── the conflict report over op deltas (merge by fold, WIRE_FORMAT §34.2) ────
#
# Fuaran Core's ``Dag.conflicts`` / ``Dag.conflictsIn``, ported for the fold's
# policy (:mod:`fuaran_ui.reconcile`). Detection only: every pair ``(a, c)`` of the
# two deltas whose footprints are not independent, once per address it collides
# on, tagged with the most specific shape — content first, then a shared named
# parent, then a remove/move racing a structural write — plus one entry per slot
# both sides access and at least one writes.

#: The four conflict shapes, by their wire names.
CONCURRENT_UPDATE = "ConcurrentUpdate"
INSERT_POSITION_CLASH = "InsertPositionClash"
MOVE_VS_REMOVE = "MoveVsRemove"
SLOT_CLASH = "SlotClash"


@dataclass(frozen=True)
class OpConflict:
    """One interfering op pair: ``left`` from the first delta, ``right`` from the
    second, the ``address`` they collide on and its ``shape``; ``slot`` is set
    exactly when ``shape`` is ``SlotClash`` (Core's ``MergeConflict``)."""

    left: Obj
    right: Obj
    address: str
    shape: str
    slot: str | None = None


def _writes_structure(f: Footprint) -> bool:
    """Does ``f`` touch structure? Core 0.42.0's ``Footprint.structural`` non-empty —
    the set the unknown-parent clause meets, child-list slot accesses included."""
    return bool(structural(f))


def conflicts(footprints_a: list[tuple[Obj, Footprint]], footprints_b: list[tuple[Obj, Footprint]]) -> list[OpConflict]:
    """Every interfering pair across two footprinted deltas (Core's ``Dag.conflicts``)."""
    out: list[OpConflict] = []
    for a, fa in footprints_a:
        for b, fb in footprints_b:
            concurrent = (
                (fa.content_writes & fb.content_writes)
                | (fa.content_writes & fb.reads)
                | (fb.content_writes & fa.reads)
                | slots_against_node(fa, fb)
                | slots_against_node(fb, fa)
            )
            insert_clash = (fa.structure_writes & fb.structure_writes) - concurrent
            move_remove: frozenset[str] = frozenset()
            if fa.unknown_parent_writes and _writes_structure(fb):
                move_remove |= fa.unknown_parent_writes
            if fb.unknown_parent_writes and _writes_structure(fa):
                move_remove |= fb.unknown_parent_writes
            move_remove = move_remove - concurrent - insert_clash
            for shape, addresses in (
                (CONCURRENT_UPDATE, concurrent),
                (INSERT_POSITION_CLASH, insert_clash),
                (MOVE_VS_REMOVE, move_remove),
            ):
                out.extend(OpConflict(a, b, addr, shape) for addr in sorted(addresses, key=utf16_order_key))
            for node, slot in sorted(slot_clash(fa, fb), key=lambda p: (utf16_order_key(p[0]), utf16_order_key(p[1]))):
                out.append(OpConflict(a, b, node, SLOT_CLASH, slot))
    return out


def _threaded(base: Node, delta: list[Obj]) -> list[tuple[Obj, Footprint]]:
    out: list[tuple[Obj, Footprint]] = []
    state = base
    for op in delta:
        out.append((op, footprint_in(state, op)))
        result = apply(op, state)
        if isinstance(result, Ok):
            state = result.value
    return out


def conflicts_in(base: Node, delta_a: list[Obj], delta_b: list[Obj]) -> list[OpConflict]:
    """:func:`conflicts` over the base-aware footprint (Core's ``Dag.conflictsIn``).

    Each delta is threaded through :func:`~fuaran_ui.ops.apply` from ``base``, so
    every op is footprinted at the tree the ops before it reach; an op that does
    not apply leaves the state where it was. ``[]`` is the promise that the two
    deltas commute from ``base``.
    """
    return conflicts(_threaded(base, delta_a), _threaded(base, delta_b))
