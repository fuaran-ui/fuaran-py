"""The placement algebra — placed insert / move / nudge, and the clone verbs
(duplicate / paste) built on top of them.

The op vocabulary is deliberately positionless: ``InsertChild`` and ``MoveNode``
append, and an explicit order is stated only by ``ReorderChildren`` naming every
sibling id (an id is checkable; an ordinal is not — see the README's "Retired wire
vocabulary" note). Placing a node anywhere but last is therefore
``Batch [InsertChild|MoveNode, ReorderChildren]`` — correct, but it leaves every
consumer deriving the full sibling permutation itself. This module ships that
derivation once, purely additively: **every helper emits ops built from the existing
vocabulary** (``InsertChild`` / ``MoveNode`` / ``ReorderChildren`` / ``Batch``), so the
wire format, the conformance corpus and the apply engine are untouched — and the
reorder leg is dropped whenever appending already yields the wanted order, keeping the
common case a single bare op.

Pre-checks mirror the apply engine's own rejections (absent parent, childless kind,
absent node, duplicate id, move-into-self, move-into-descendant) so an editor can grey
out an illegal drop without a dry-run apply — with one deliberate tightening: an anchor
that is not among the destination's post-op children is REFUSED
(:data:`UNKNOWN_ANCHOR`) rather than silently appended. The only op that could honour
such an anchor would be a ``ReorderChildren`` naming it, which the apply engine refuses
as ``OrderingMismatch``; saying so before emission is friendlier than a rejection after
it.

The clone verbs lift a subtree and rewrite its ids to a fresh, collision-free set before
the insert. The remap runs over the **whole traversal surface** — every position
:func:`~fuaran_py.ops.apply._child_slots` reaches, not just the structural child lists —
because the id-uniqueness contract is tree-wide, and a clone that kept an old id inside
a ``Switch`` case or a ``State`` slot would smuggle a duplicate past it.

This is a sibling of the F# ``Fuaran.UI.Ops.Placement`` module, built to the same
semantics rather than transpiled: the traversal is this host's own apply-engine
traversal, so a helper verdict and an apply verdict cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..model import Arr, Node, Obj
from ..result import Ok
from .apply import (
    _all_ids,
    _child_slots,
    _find,
    _find_layout_parent,
    _is_ancestor,
    _layout_children,
)

# ── Placement vocabulary ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Last:
    """Append — what ``InsertChild`` / ``MoveNode`` do on their own."""


@dataclass(frozen=True)
class First:
    """Prepend — before every current sibling."""


@dataclass(frozen=True)
class Before:
    """Immediately before the named sibling."""

    anchor: str


@dataclass(frozen=True)
class After:
    """Immediately after the named sibling."""

    anchor: str


#: Where a node should sit among its destination siblings, stated the only way the op
#: vocabulary allows: by naming an existing sibling, or an end.
type Placement = Last | First | Before | After


@dataclass(frozen=True)
class Target:
    """A structural destination: which parent, and where among its children."""

    parent_id: str
    placement: Placement = Last()


# ── Rejections ───────────────────────────────────────────────────────────────
#
# Each code is a pre-statement of the apply-time refusal the emitted op would have
# met, so a helper rejection and an apply rejection agree — no false permit, no false
# refuse. The shape mirrors this package's own `ApplyError` (a code plus the ids the
# code names) rather than an F#-style payload union, so both read alike to a caller.

#: The destination parent is not in the tree (apply: ``ParentNotFound``).
PARENT_NOT_FOUND = "ParentNotFound"
#: The destination parent's kind carries no ``children`` list (apply: ``ChildlessKind``).
CHILDLESS_KIND = "ChildlessKind"
#: The node to move / nudge / duplicate is not structurally addressable — absent, or
#: held in a non-structural position the structural ops cannot reach (apply:
#: ``NodeNotFound``).
NODE_NOT_FOUND = "NodeNotFound"
#: The placement anchor is not among the destination's post-op children. The only op
#: that could honour it — a ``ReorderChildren`` naming it — is refused by the apply
#: engine as ``OrderingMismatch``.
UNKNOWN_ANCHOR = "UnknownAnchor"
#: The subtree being inserted carries an id already present in the tree (apply:
#: ``DuplicateNodeId``).
DUPLICATE_ID = "DuplicateId"
#: The node would become its own parent (apply: ``KindMismatch``).
MOVE_INTO_SELF = "MoveIntoSelf"
#: The destination sits inside the node's own subtree — a cycle (apply:
#: ``KindMismatch``).
MOVE_INTO_DESCENDANT = "MoveIntoDescendant"
#: The root has no siblings to nudge among.
CANNOT_NUDGE_ROOT = "CannotNudgeRoot"
#: The nudge would leave the sibling range (already first / already last).
NUDGE_OUT_OF_RANGE = "NudgeOutOfRange"

CODES = frozenset(
    {
        PARENT_NOT_FOUND,
        CHILDLESS_KIND,
        NODE_NOT_FOUND,
        UNKNOWN_ANCHOR,
        DUPLICATE_ID,
        MOVE_INTO_SELF,
        MOVE_INTO_DESCENDANT,
        CANNOT_NUDGE_ROOT,
        NUDGE_OUT_OF_RANGE,
    }
)


@dataclass(frozen=True)
class PlaceError:
    """Why a placement could not become an op.

    ``subject`` is the id the code names (the parent, the moved node, or the anchor).
    ``parent_id`` carries the second id of :data:`MOVE_INTO_DESCENDANT`; ``delta``
    carries the requested step of :data:`NUDGE_OUT_OF_RANGE`.

    :attr:`message` is a derived property rather than a field, so equality is over the
    code and the ids alone — rewording a sentence can never move a verdict.
    """

    code: str
    subject: str
    parent_id: str | None = None
    delta: int | None = None

    @property
    def message(self) -> str:
        if self.code == PARENT_NOT_FOUND:
            return f"Parent node '{self.subject}' not found in tree."
        if self.code == CHILDLESS_KIND:
            return f"Node '{self.subject}' has no children field — only layout kinds accept structural child ops."
        if self.code == NODE_NOT_FOUND:
            return f"Node '{self.subject}' is not structurally addressable in this tree."
        if self.code == UNKNOWN_ANCHOR:
            return f"Anchor '{self.subject}' is not among the destination's children after the op."
        if self.code == DUPLICATE_ID:
            return f"NodeId '{self.subject}' is already present in the tree; ids must be unique."
        if self.code == MOVE_INTO_SELF:
            return f"Cannot move node '{self.subject}' into itself."
        if self.code == MOVE_INTO_DESCENDANT:
            return (
                f"Cannot move node '{self.subject}' into its own descendant '{self.parent_id}' (would create a cycle)."
            )
        if self.code == CANNOT_NUDGE_ROOT:
            return f"Node '{self.subject}' is the root and has no siblings to nudge among."
        if self.code == NUDGE_OUT_OF_RANGE:
            return f"Nudging '{self.subject}' by {self.delta} would leave the sibling range."
        return f"Placement refused for '{self.subject}': {self.code}."


@dataclass(frozen=True)
class PlaceErr:
    """A refused placement carrying the :class:`PlaceError` (mirrors ``ApplyErr``)."""

    error: PlaceError

    @property
    def ok(self) -> bool:
        return False


#: A helper that emits an op: ``Ok`` carrying the decoded ``TreeOp``, or the refusal.
type PlaceResult = Ok[Obj] | PlaceErr
#: The verdict-only form used by :func:`can_place`.
type PlaceCheck = Ok[None] | PlaceErr


def _refuse(
    code: str,
    subject: str,
    parent_id: str | None = None,
    delta: int | None = None,
) -> PlaceErr:
    return PlaceErr(PlaceError(code, subject, parent_id, delta))


# ── Fresh-id strategy (the clone verbs' id-minting seam) ─────────────────────

#: How the clone verbs mint replacement ids: given the id being replaced and a predicate
#: over every id already claimed (the whole target tree, the whole incoming subtree, and
#: ids minted earlier in the same remap), return an id the predicate refuses. Injectable
#: so a host with its own id discipline can supply one; :func:`derived_ids` is the default.
type FreshIds = Callable[[str, Callable[[str], bool]], str]


def derived_ids(old_id: str, taken: Callable[[str], bool]) -> str:
    """The default: ``<old_id>-copy``, then ``-copy-2``, ``-copy-3``, … — the first
    candidate not already taken. Deterministic (derived from the id it replaces, with no
    ambient state) and collision-free by probing.
    """
    n = 1
    while True:
        candidate = f"{old_id}-copy" if n == 1 else f"{old_id}-copy-{n}"
        if not taken(candidate):
            return candidate
        n += 1


def sequential_ids(prefix: str) -> FreshIds:
    """Sequential ids under a fixed prefix (``<prefix>-1``, ``-2``, …) — the
    deterministic-replay option: the minted sequence depends only on the prefix and the
    order of requests, never on the ids being replaced. Each call to ``sequential_ids``
    starts its own counter.
    """
    counter = 0

    def mint(_old_id: str, taken: Callable[[str], bool]) -> str:
        nonlocal counter
        while True:
            counter += 1
            candidate = f"{prefix}-{counter}"
            if not taken(candidate):
                return candidate

    return mint


# ── Internals ────────────────────────────────────────────────────────────────


def _container_children(root: Node, parent_id: str) -> Ok[list[str]] | PlaceErr:
    """The destination's current child ids, or the mirrored apply-side refusal."""
    parent = _find(parent_id, root)
    if parent is None:
        return _refuse(PARENT_NOT_FOUND, parent_id)
    children = _layout_children(parent)
    if children is None:
        return _refuse(CHILDLESS_KIND, parent_id)
    return Ok([c.id for c in children])


def _reposition(order: list[str], moved: str, placement: Placement) -> Ok[list[str]] | PlaceErr:
    """Place ``moved`` within ``order`` (which already contains it) per ``placement``.

    An anchor that is not in the list is refused — the honest alternative (silently
    appending) would emit an op that does not honour the caller's stated intent.
    """
    rest = [i for i in order if i != moved]

    if isinstance(placement, Last):
        return Ok([*rest, moved])
    if isinstance(placement, First):
        return Ok([moved, *rest])

    offset = 0 if isinstance(placement, Before) else 1
    if placement.anchor not in rest:
        return _refuse(UNKNOWN_ANCHOR, placement.anchor)
    at = rest.index(placement.anchor) + offset
    return Ok([*rest[:at], moved, *rest[at:]])


def _structurally_present(node_id: str, root: Node) -> bool:
    """Whether ``node_id`` is addressable by the structural ops: the root, or a node
    reachable through a layout ``children`` list. A node held in a non-structural
    position (a ``Switch`` case, an ``ErrorBoundary`` slot, a ``State`` placeholder) is
    visible to traversal but not movable, and the apply engine refuses ops against it as
    ``NodeNotFound``.
    """
    return root.id == node_id or _find_layout_parent(node_id, root) is not None


def _batch(ops: list[Obj]) -> Obj:
    return Obj("Batch", {"ops": Arr(list(ops))})


def _reorder(parent_id: str, order: list[str]) -> Obj:
    return Obj("ReorderChildren", {"parentId": parent_id, "newOrder": Arr(list(order))})


def _placed(op: Obj, parent_id: str, appended: list[str], wanted: list[str]) -> Obj:
    """``op`` alone when appending already yields ``wanted``, else the two-op batch that
    states the order by naming every sibling id.
    """
    if wanted == appended:
        return op
    return _batch([op, _reorder(parent_id, wanted)])


def _remap_ids(node: Node, rename: dict[str, str]) -> Node:
    """Rewrite every id in ``rename`` across the whole traversal surface, returning the
    node UNCHANGED (by identity) where nothing beneath it moved.

    The one non-obvious line: ``_child_slots`` hands back rebuild closures that each
    capture the node they were computed from, so applying several rebuilds drawn from a
    single call would discard all but the last. The slot list is therefore recomputed
    against the accumulator on every step. An id-only rewrite never changes the slot
    count or order, so the index walk is stable.
    """
    new_id = rename.get(node.id, node.id)
    current = node if new_id == node.id else Node(new_id, node.kind, node.extras)

    i = 0
    while True:
        slots = _child_slots(current)
        if i >= len(slots):
            return current
        child, rebuild = slots[i]
        replaced = _remap_ids(child, rename)
        if replaced is not child:
            current = rebuild(replaced)
        i += 1


def _remap_for_insert(fresh_ids: FreshIds, target_root: Node, incoming: Node) -> Node:
    """Rewrite every id in ``incoming`` that collides with an id in ``target_root`` to a
    fresh, collision-free one. Ids with no collision are preserved — a pasted subtree
    keeps its identity where it can; a subtree duplicated within its own tree remaps
    every id, since every one collides.
    """
    existing = set(_all_ids(target_root))

    # Fresh ids must also dodge the incoming subtree's own ids (a minted id colliding
    # with a not-yet-visited incoming node would re-introduce the duplicate the remap
    # exists to remove) and each other.
    taken = existing | set(_all_ids(incoming))

    rename: dict[str, str] = {}
    for old_id in _all_ids(incoming):
        if old_id in existing and old_id not in rename:
            fresh = fresh_ids(old_id, lambda candidate: candidate in taken)
            taken.add(fresh)
            rename[old_id] = fresh

    return incoming if not rename else _remap_ids(incoming, rename)


# ── The verbs ────────────────────────────────────────────────────────────────


def can_place(root: Node, moved: str, target: Target) -> PlaceCheck:
    """Whether ``moved`` may legally take up residence at ``target`` — the pre-check an
    editor uses to grey out an illegal drop without a dry-run apply.

    Mirrors the apply engine's rejections: absent node, move into itself, move into its
    own descendant (a cycle), absent or childless destination, unknown anchor.
    """
    if not _structurally_present(moved, root):
        return _refuse(NODE_NOT_FOUND, moved)
    if target.parent_id == moved:
        return _refuse(MOVE_INTO_SELF, moved)
    if _is_ancestor(moved, target.parent_id, root):
        return _refuse(MOVE_INTO_DESCENDANT, moved, target.parent_id)

    siblings = _container_children(root, target.parent_id)
    if not siblings.ok:
        assert isinstance(siblings, PlaceErr)
        return siblings
    assert isinstance(siblings, Ok)

    membership = [i for i in siblings.value if i != moved] + [moved]
    repositioned = _reposition(membership, moved, target.placement)
    if not repositioned.ok:
        assert isinstance(repositioned, PlaceErr)
        return repositioned
    return Ok(None)


def place_op(root: Node, child: Node, target: Target) -> PlaceResult:
    """The op an insertion becomes.

    ``InsertChild`` appends, so the wanted order is computed over the post-insert
    membership and stated by a ``ReorderChildren`` naming every sibling id; the reorder
    leg is dropped when appending already produces that order.
    """
    siblings = _container_children(root, target.parent_id)
    if not siblings.ok:
        assert isinstance(siblings, PlaceErr)
        return siblings
    assert isinstance(siblings, Ok)

    # The apply engine's own duplicate-id check, asked before emission rather than after.
    existing = set(_all_ids(root))
    duplicate = next((cid for cid in _all_ids(child) if cid in existing), None)
    if duplicate is not None:
        return _refuse(DUPLICATE_ID, duplicate)

    appended = [*siblings.value, child.id]
    repositioned = _reposition(appended, child.id, target.placement)
    if not repositioned.ok:
        assert isinstance(repositioned, PlaceErr)
        return repositioned
    assert isinstance(repositioned, Ok)

    insert = Obj("InsertChild", {"parentId": target.parent_id, "child": child})
    return Ok(_placed(insert, target.parent_id, appended, repositioned.value))


def move_op(root: Node, moved: str, target: Target) -> PlaceResult:
    """The op a move becomes.

    ``MoveNode`` appends under the new parent, and the node may already be one of that
    parent's children (a re-placement within one parent), so the post-move membership is
    the siblings WITHOUT it plus it.
    """
    check = can_place(root, moved, target)
    if not check.ok:
        assert isinstance(check, PlaceErr)
        return check

    siblings = _container_children(root, target.parent_id)
    if not siblings.ok:
        assert isinstance(siblings, PlaceErr)
        return siblings
    assert isinstance(siblings, Ok)

    appended = [i for i in siblings.value if i != moved] + [moved]
    repositioned = _reposition(appended, moved, target.placement)
    if not repositioned.ok:
        assert isinstance(repositioned, PlaceErr)
        return repositioned
    assert isinstance(repositioned, Ok)

    move = Obj("MoveNode", {"target": moved, "newParentId": target.parent_id})
    return Ok(_placed(move, target.parent_id, appended, repositioned.value))


def nudge_op(root: Node, node_id: str, delta: int) -> PlaceResult:
    """The op a keyboard move-up (``-1``) / move-down (``+1``) becomes: the node swapped
    with the sibling ``delta`` positions away, stated as the FULL sibling id order —
    which is what ``ReorderChildren`` requires, a partial list being refused by the apply
    engine, and rightly, since a partial order is not one.
    """
    if root.id == node_id:
        return _refuse(CANNOT_NUDGE_ROOT, node_id)

    parent = _find_layout_parent(node_id, root)
    if parent is None:
        return _refuse(NODE_NOT_FOUND, node_id)

    ids = [c.id for c in (_layout_children(parent) or [])]
    index = ids.index(node_id)
    swap_with = index + delta
    if swap_with < 0 or swap_with >= len(ids):
        return _refuse(NUDGE_OUT_OF_RANGE, node_id, delta=delta)

    reordered = list(ids)
    reordered[index], reordered[swap_with] = ids[swap_with], ids[index]
    return Ok(_reorder(parent.id, reordered))


# ── Clone verbs ──────────────────────────────────────────────────────────────


def duplicate_op_with(fresh_ids: FreshIds, root: Node, source: str, target: Target) -> PlaceResult:
    """Duplicate the subtree rooted at ``source`` and place the clone at ``target``,
    minting replacement ids with ``fresh_ids``.

    The emitted op is an ordinary placed insert — the clone is a fresh subtree, so the
    standard apply gate (including the tree-wide duplicate-id check) accepts it unchanged.
    """
    sub = _find(source, root)
    if sub is None:
        return _refuse(NODE_NOT_FOUND, source)
    return place_op(root, _remap_for_insert(fresh_ids, root, sub), target)


def duplicate_op(root: Node, source: str, target: Target) -> PlaceResult:
    """:func:`duplicate_op_with` under the default derived-suffix id strategy."""
    return duplicate_op_with(derived_ids, root, source, target)


def paste_op_with(fresh_ids: FreshIds, target_root: Node, incoming: Node, target: Target) -> PlaceResult:
    """Place a subtree lifted from a DIFFERENT tree into ``target_root``, remapping any
    id that collides with one already present (ids with no collision are preserved).

    The incoming subtree's ids must be unique within itself — a subtree extracted from
    any well-formed tree is.
    """
    return place_op(target_root, _remap_for_insert(fresh_ids, target_root, incoming), target)


def paste_op(target_root: Node, incoming: Node, target: Target) -> PlaceResult:
    """:func:`paste_op_with` under the default derived-suffix id strategy."""
    return paste_op_with(derived_ids, target_root, incoming, target)
