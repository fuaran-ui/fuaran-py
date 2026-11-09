"""``fuaran_ui.footprint`` — the footprint of a ``TreeOp``: what it reads and writes.

The Python port of the reference ``Fuaran.UI.Ops.TreeOpFootprint`` (Phase 2292,
base-aware form Phase 2327) over Fuaran Core's ``Footprint`` record and its
``Ops.footprint`` / ``Ops.footprintIn`` arms (WIRE_FORMAT.md §34.2, Phase 2642).
The projection is part of the merge-by-fold contract: a host whose footprint is
coarser than the reference's refuses pairs the reference admits, and one that is
finer admits pairs it refuses, so this module reproduces it address for address.

Two forms:

* the **pure** form (:func:`footprint_of`) is computed from the op alone. A
  structural op is Core's skeleton footprint; a vertical op (an in-place edit) is
  Core's ``UpdateNode`` footprint — the node read and content-written, every id of
  a subtree it puts in read and content-written, and an unknown-parent write of
  the node, without which the removal of an ancestor would read as independent.
* the **base-aware** form (:func:`footprint_in`) is computed at the tree the op
  applies to. A removal content-writes the whole subtree it destroys and names its
  parent; a move names its source parent and reads the destination's ancestors; a
  vertical op whose node the STRUCTURAL walk locates, and which applies, is
  declared at the slot it changes (``kind``, ``state``, ``style``,
  ``style.<field>`` beside a read of ``style``, ``accessibility``, ``tooltip``) plus
  every id it puts in or takes out. A ``Batch`` is threaded member by member: each
  member at the tree the ones before it reach, and from a member that does not
  apply onwards every member takes the pure arm.

Two walks, as on the reference: the **structural** walk is a layout node's ordered
``children`` alone (it locates nodes and their parents), and the **keyed** walk is
every position the tier holds a node in (:func:`fuaran_ui.ops.apply` 's own id
walk), which counts the ids an op authors or destroys. A node held only in a keyed
position is not located, and takes the pure arm.

Total: no op throws. Pure: no IO, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Arr, Node, Obj, Value
from .ops.apply import _all_ids, _child_slots, _layout_children, apply
from .result import Ok

__all__ = [
    "Footprint",
    "EMPTY",
    "union",
    "slot_edit",
    "reading_slot",
    "slot_clash",
    "slots_against_node",
    "footprint_of",
    "footprint_of_script",
    "footprint_in",
    "footprint_of_script_in",
    "inserted",
]


@dataclass(frozen=True)
class Footprint:
    """Fuaran Core's ``Footprint``: the addresses a script reads and writes.

    ``reads`` — ids whose existence it depends on; ``structure_writes`` — named
    parents whose child list it changes; ``content_writes`` — ids it authors,
    destroys, relocates or rewrites; ``unknown_parent_writes`` — ids standing for a
    parent it cannot name; ``slot_reads`` / ``slot_writes`` — ``(node, slot)`` pairs
    accessed without the whole node.
    """

    reads: frozenset[str] = field(default_factory=frozenset)
    structure_writes: frozenset[str] = field(default_factory=frozenset)
    content_writes: frozenset[str] = field(default_factory=frozenset)
    unknown_parent_writes: frozenset[str] = field(default_factory=frozenset)
    slot_reads: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    slot_writes: frozenset[tuple[str, str]] = field(default_factory=frozenset)


EMPTY = Footprint()


def union(a: Footprint, b: Footprint) -> Footprint:
    """Both footprints' addresses, kind by kind (Core's ``Footprint.union``)."""
    return Footprint(
        a.reads | b.reads,
        a.structure_writes | b.structure_writes,
        a.content_writes | b.content_writes,
        a.unknown_parent_writes | b.unknown_parent_writes,
        a.slot_reads | b.slot_reads,
        a.slot_writes | b.slot_writes,
    )


def slot_edit(node_id: str, slot: str) -> Footprint:
    """A read and a write of one slot of a node, and nothing at the node."""
    pair = frozenset({(node_id, slot)})
    return Footprint(slot_reads=pair, slot_writes=pair)


def reading_slot(node_id: str, slot: str) -> Footprint:
    """A read of one slot of a node and no write."""
    return Footprint(slot_reads=frozenset({(node_id, slot)}))


def slot_clash(a: Footprint, b: Footprint) -> frozenset[tuple[str, str]]:
    """The slots one side writes and the other accesses, either way round."""
    return (a.slot_writes & b.slot_writes) | (a.slot_writes & b.slot_reads) | (a.slot_reads & b.slot_writes)


def slots_against_node(a: Footprint, b: Footprint) -> frozenset[str]:
    """The nodes ``a`` accesses through a slot that ``b`` touches whole."""
    written = frozenset(n for n, _ in a.slot_writes)
    read = frozenset(n for n, _ in a.slot_reads)
    return (written & (b.content_writes | b.reads | b.structure_writes)) | (read & b.content_writes)


# ─── walks ───────────────────────────────────────────────────────────────────


def _structural(node: Node) -> list[Node]:
    return _layout_children(node) or []


def _preorder(root: Node) -> list[Node]:
    out: list[Node] = []
    stack = [root]
    while stack:
        n = stack.pop()
        out.append(n)
        stack.extend(reversed(_structural(n)))
    return out


def _locate(node_id: str, root: Node) -> Node | None:
    """The node carrying ``node_id`` over the structural walk."""
    return next((n for n in _preorder(root) if n.id == node_id), None)


def _structural_parent(node_id: str, root: Node) -> Node | None:
    return next((n for n in _preorder(root) if any(c.id == node_id for c in _structural(n))), None)


def _proper_ancestors(node_id: str, root: Node) -> frozenset[str]:
    """The structural ancestors of ``node_id``, the node excluded; empty when absent."""

    def path(n: Node) -> list[str] | None:
        if n.id == node_id:
            return [n.id]
        for c in _structural(n):
            below = path(c)
            if below is not None:
                return [n.id, *below]
        return None

    found = path(root)
    return frozenset(found[:-1]) if found else frozenset()


def _ids(node: Node) -> frozenset[str]:
    """Every id in ``node``'s subtree over the keyed walk, ``node`` included."""
    return frozenset(_all_ids(node))


def _ids_of(nodes: list[Node]) -> frozenset[str]:
    return frozenset().union(*(_ids(n) for n in nodes))


def _held(node: Node) -> frozenset[str]:
    """Every id ``node`` holds below itself."""
    return _ids(node) - {node.id}


# ─── what an op puts in ─────────────────────────────────────────────────────


def _kind_held(node_id: str, kind: Value) -> list[Node]:
    """The nodes a kind holds: the keyed walk of a carrier that has only that kind."""
    if not isinstance(kind, Obj):
        return []
    return [child for child, _ in _child_slots(Node(node_id, kind))]


def _state_alternatives(state: Value) -> list[Node]:
    if not isinstance(state, Obj):
        return []
    return [n for n in (state.fields.get("onLoading"), state.fields.get("onEmpty")) if isinstance(n, Node)]


def _ops_of(op: Obj) -> list[Obj]:
    inner = op.fields.get("ops")
    return [o for o in inner.items if isinstance(o, Obj)] if isinstance(inner, Arr) else []


def _str(op: Obj, name: str) -> str:
    v = op.fields.get(name)
    return v if isinstance(v, str) else ""


def inserted(op: Obj) -> list[Node]:
    """The subtree roots ``op`` puts into the tree, in the order it names them."""
    tag = op.tag
    if tag == "EditNode":
        return _kind_held(_str(op, "target"), op.fields.get("newKind"))
    if tag == "UpdateState":
        return _state_alternatives(op.fields.get("state"))
    if tag == "InsertChild":
        child = op.fields.get("child")
        return [child] if isinstance(child, Node) else []
    if tag == "ReplaceRoot":
        node = op.fields.get("node")
        return [node] if isinstance(node, Node) else []
    if tag == "Batch":
        return [n for o in _ops_of(op) for n in inserted(o)]
    return []


# ─── the pure form ───────────────────────────────────────────────────────────

_VERTICAL = frozenset(
    {
        "EditNode",
        "UpdateProp",
        "ReplaceBinding",
        "UpdateStyle",
        "PatchStyle",
        "UpdateState",
        "UpdateAccessibility",
        "UpdateTooltip",
    }
)


def _vertical(target: str, incoming: list[Node]) -> Footprint:
    introduced = _ids_of(incoming)
    return Footprint(
        reads=introduced | {target},
        content_writes=introduced | {target},
        unknown_parent_writes=frozenset({target}),
    )


def footprint_of(op: Obj) -> Footprint:
    """The pure footprint of one op. Total over the op cases."""
    tag = op.tag
    if tag == "InsertChild":
        parent = _str(op, "parentId")
        child = op.fields.get("child")
        ids = _ids(child) if isinstance(child, Node) else frozenset()
        return Footprint(reads=ids | {parent}, structure_writes=frozenset({parent}), content_writes=ids)
    if tag == "RemoveNode":
        target = _str(op, "target")
        one = frozenset({target})
        return Footprint(reads=one, content_writes=one, unknown_parent_writes=one)
    if tag == "MoveNode":
        target, new_parent = _str(op, "target"), _str(op, "newParentId")
        return Footprint(
            reads=frozenset({target, new_parent}),
            structure_writes=frozenset({new_parent}),
            content_writes=frozenset({target}),
            unknown_parent_writes=frozenset({target}),
        )
    if tag == "ReorderChildren":
        parent = _str(op, "parentId")
        order = op.fields.get("newOrder")
        named = frozenset(i for i in order.items if isinstance(i, str)) if isinstance(order, Arr) else frozenset()
        return Footprint(reads=named | {parent}, structure_writes=frozenset({parent}))
    if tag in _VERTICAL:
        return _vertical(_str(op, "target"), inserted(op))
    if tag == "ReplaceRoot":
        node = op.fields.get("node")
        if not isinstance(node, Node):
            return EMPTY
        ids = _ids(node)
        root = frozenset({node.id})
        return Footprint(reads=ids, content_writes=ids, structure_writes=root, unknown_parent_writes=root)
    if tag == "Batch":
        return footprint_of_script(_ops_of(op))
    return EMPTY


def footprint_of_script(ops: list[Obj]) -> Footprint:
    """The pure footprint of a script: its ops' footprints, unioned."""
    acc = EMPTY
    for op in ops:
        acc = union(acc, footprint_of(op))
    return acc


# ─── the base-aware form ─────────────────────────────────────────────────────


def _slotted(writes: list[str], reads: list[str], target: str, touched: frozenset[str]) -> Footprint:
    acc = EMPTY
    for slot in writes:
        acc = union(acc, slot_edit(target, slot))
    for slot in reads:
        acc = union(acc, reading_slot(target, slot))
    return Footprint(
        reads=acc.reads | touched,
        structure_writes=acc.structure_writes,
        content_writes=acc.content_writes | touched,
        unknown_parent_writes=acc.unknown_parent_writes,
        slot_reads=acc.slot_reads,
        slot_writes=acc.slot_writes,
    )


def _held_change(node_id: str, node: Node, after: Node | None) -> frozenset[str]:
    """The ids an op whose contract puts no node in moved anyway — a guard."""
    before = _held(node)
    now = _locate(node_id, after) if after is not None else None
    if now is None:
        return before
    held_now = _held(now)
    return frozenset() if held_now == before else before | held_now


def _structural_in(state: Node, op: Obj) -> Footprint:
    """Core's ``Ops.footprintIn`` over one skeleton op: the resolved arm, else the pure one."""
    tag = op.tag
    if tag == "RemoveNode":
        target = _str(op, "target")
        located = _locate(target, state)
        parent = _structural_parent(target, state)
        if located is not None and parent is not None:
            return Footprint(
                reads=frozenset({target, parent.id}),
                structure_writes=frozenset({parent.id}),
                content_writes=_ids(located),
            )
    elif tag == "MoveNode":
        target, new_parent = _str(op, "target"), _str(op, "newParentId")
        located = _locate(target, state)
        parent = _structural_parent(target, state)
        destination = _locate(new_parent, state)
        if located is not None and parent is not None and destination is not None:
            return Footprint(
                reads=frozenset({target, new_parent, parent.id}) | _proper_ancestors(new_parent, state),
                structure_writes=frozenset({parent.id, new_parent}),
                content_writes=frozenset({target}),
            )
    return footprint_of(op)


def _style_fields(delta: Value) -> list[str]:
    return sorted(delta.fields) if isinstance(delta, Obj) else []


def _resolved_at(state: Node, after: Node | None, op: Obj) -> Footprint:
    """The base-aware arm of one non-batch op at ``state``, given the tree it reaches."""
    tag = op.tag
    if tag in ("InsertChild", "RemoveNode", "MoveNode", "ReorderChildren"):
        return _structural_in(state, op)
    if tag == "ReplaceRoot":
        plain = footprint_of(op)
        replaced = _ids(state)
        return Footprint(
            reads=plain.reads | replaced,
            structure_writes=plain.structure_writes | {state.id},
            content_writes=plain.content_writes | replaced,
            unknown_parent_writes=plain.unknown_parent_writes,
        )
    if tag not in _VERTICAL:
        return footprint_of(op)

    target = _str(op, "target")
    node = _locate(target, state)
    if node is None or after is None:
        return footprint_of(op)
    if tag == "EditNode":
        old = _kind_held(target, node.kind)
        return _slotted(["kind"], [], target, _ids_of(old) | _ids_of(inserted(op)))
    if tag == "UpdateState":
        old = _state_alternatives(node.extras.get("state"))
        return _slotted(["state"], [], target, _ids_of(old) | _ids_of(inserted(op)))
    if tag == "PatchStyle":
        fields = [f"style.{f}" for f in _style_fields(op.fields.get("style"))]
        return _slotted(fields, ["style"], target, _held_change(target, node, after))
    slot = {
        "UpdateProp": "kind",
        "ReplaceBinding": "kind",
        "UpdateStyle": "style",
        "UpdateAccessibility": "accessibility",
        "UpdateTooltip": "tooltip",
    }[tag]
    return _slotted([slot], [], target, _held_change(target, node, after))


def _threaded(state: Node | None, op: Obj) -> tuple[Footprint, Node | None]:
    if state is None:
        return footprint_of(op), None
    if op.tag == "Batch":
        acc = EMPTY
        current: Node | None = state
        for member in _ops_of(op):
            fp, current = _threaded(current, member)
            acc = union(acc, fp)
        return acc, current
    result = apply(op, state)
    after = result.value if isinstance(result, Ok) else None
    return _resolved_at(state, after, op), after


def footprint_in(state: Node, op: Obj) -> Footprint:
    """The footprint of one op at the tree it applies to (the base-aware form).

    Sound where :func:`footprint_of` is and never less precise. The shape the
    conflict report (:func:`fuaran_ui.merge.conflicts_in`) takes.
    """
    return _threaded(state, op)[0]


def footprint_of_script_in(base: Node, ops: list[Obj]) -> Footprint:
    """The footprint of a script at the tree it applies to: each op at the tree the
    ops before it reach, unioned."""
    return footprint_in(base, Obj("Batch", {"ops": Arr(list(ops))}))
