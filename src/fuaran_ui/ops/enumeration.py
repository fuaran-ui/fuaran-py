"""``fuaran_ui.ops.enumeration`` — what a node's children are.

The ONE enumeration of the parent/child relation in this host, with the reach a caller
wants stated rather than re-derived. The relation used to be written by hand in every
walk that needed it, and the copies disagreed about reach: the apply engine addressed
six container kinds while the speech projection and the IDL hold eight (``Modal`` and
``ScrollArea`` carry an ordered ``children`` list too); the validator, the AI-tools
introspection and the runtime's event binding did not see ``state.onLoading`` /
``state.onEmpty``; the runtime did not see ``Switch`` arms or slot arguments, and the
transform evaluator did not see slot arguments; the renderers' fragment registry did
not descend into ``ErrorBoundary.fallback``, the state alternatives or a fragment body.
Nothing in the byte corpus can see two walks that disagree, so the rule is structural:
this module is the only place a node is taken apart to find the nodes it holds.

**The slot list is read from the generated IDL tables**, not written by hand.
:data:`~fuaran_ui.schema.generated.KIND_FIELDS` names every member a kind carries and its
type, so the nodes a kind holds are derived from it at import, and a vocabulary change
that adds a node-holding member is enumerated with no edit here. A member that holds a
node in a SHAPE this module does not classify fails the import, rather than every walk
silently missing a subtree.

Every position a node holds another node in has a CLASS (:class:`Position`), and a caller
asks for the classes it means (a reach). The named reaches mirror the reference host's:

* :data:`STRUCTURAL` — the ordered child lists alone; what the structural ops edit.
* :data:`KIND_HELD` — every node the node's own KIND holds: the ordered lists and arms.
* :data:`FRAGMENT_SCOPE` — the kind-held positions plus the two ``state`` alternatives;
  what the fragment registry walk descends through.
* :data:`KEYED` — every position: the surface node lookup, id uniqueness, the clone
  remap and the footprint quantify over.

One canonical order: the kind's positions in its IDL member order (an ordered list in
list order, a record list item by item, an argument bag in key order), then
``state.onLoading``, ``state.onEmpty``, then the envelope ``fallback``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from ..model import Arr, Node, Obj, Value
from ..schema.generated import KIND_FIELDS, NODE_FIELDS, RECORD_FIELDS, UNION_CASE_FIELDS, F, T

__all__ = [
    "FRAGMENT_SCOPE",
    "KEYED",
    "KIND_HELD",
    "STRUCTURAL",
    "Position",
    "Reach",
    "Slot",
    "children",
    "find_node",
    "map_children",
    "ordered_children",
    "slots",
    "state_arms",
    "walk",
    "with_ordered_children",
]


class Position(StrEnum):
    """The kind of place one node holds another in."""

    #: A container's ordered ``children`` list — the surface the structural ops edit.
    ORDERED = "Ordered"
    #: A node the KIND holds in a named arm: ``ErrorBoundary.child`` / ``.fallback``,
    #: every ``Switch`` case child and its default, a ``FragmentDecl`` body.
    ARM = "Arm"
    #: The envelope's ``state.onLoading`` / ``state.onEmpty``, which render INSTEAD of
    #: the node.
    STATE_ARM = "StateArm"
    #: The envelope's author-declared ``fallback``, which a reader that cannot render
    #: the node renders instead.
    FALLBACK = "Fallback"
    #: A subtree passed as an argument: a ``FragmentRef``'s ``args`` and a ``Mount``'s
    #: ``inputs`` slot arguments.
    ARGUMENT = "Argument"


#: The position classes a walk descends through. A walk names one; there is no default.
type Reach = frozenset[Position]

STRUCTURAL: Reach = frozenset({Position.ORDERED})
KIND_HELD: Reach = frozenset({Position.ORDERED, Position.ARM})
FRAGMENT_SCOPE: Reach = frozenset({Position.ORDERED, Position.ARM, Position.STATE_ARM})
KEYED: Reach = frozenset(Position)


@dataclass(frozen=True, slots=True)
class Slot:
    """One position a node holds another node in.

    ``address`` is the path from the holding node to the held one, in the decoded
    model's own member names (``("kind", "children", 0)``, ``("kind", "cases", 1,
    "child")``, ``("kind", "args", "header", "tree")``, ``("state", "onLoading")``,
    ``("fallback",)``).
    """

    position: Position
    node: Node
    address: tuple[str | int, ...]


# ─── the slot list, derived from the IDL at import ───────────────────────────

type _Step = Callable[[Node], Node]


@dataclass(frozen=True, slots=True)
class _List:
    """A ``list<node>`` member: the kind's ordered child list."""

    field: str

    def read(self, value: Value) -> Iterator[tuple[Node, tuple[str | int, ...]]]:
        if isinstance(value, Arr):
            for i, item in enumerate(value.items):
                if isinstance(item, Node):
                    yield item, (self.field, i)

    def map(self, value: Value, f: _Step) -> Value:
        if not isinstance(value, Arr):
            return value
        items = [f(item) if isinstance(item, Node) else item for item in value.items]
        return value if all(a is b for a, b in zip(items, value.items, strict=True)) else Arr(items)


@dataclass(frozen=True, slots=True)
class _One:
    """A ``node`` member: one named arm."""

    field: str

    def read(self, value: Value) -> Iterator[tuple[Node, tuple[str | int, ...]]]:
        if isinstance(value, Node):
            yield value, (self.field,)

    def map(self, value: Value, f: _Step) -> Value:
        return f(value) if isinstance(value, Node) else value


@dataclass(frozen=True, slots=True)
class _Records:
    """A ``list<record>`` member whose record holds nodes (``Switch.cases[].child``)."""

    field: str
    members: tuple[str, ...]

    def read(self, value: Value) -> Iterator[tuple[Node, tuple[str | int, ...]]]:
        if isinstance(value, Arr):
            for i, item in enumerate(value.items):
                if isinstance(item, Obj):
                    for member in self.members:
                        held = item.fields.get(member)
                        if isinstance(held, Node):
                            yield held, (self.field, i, member)

    def map(self, value: Value, f: _Step) -> Value:
        if not isinstance(value, Arr):
            return value
        items = [_map_members(item, self.members, f) if isinstance(item, Obj) else item for item in value.items]
        return value if all(a is b for a, b in zip(items, value.items, strict=True)) else Arr(items)


@dataclass(frozen=True, slots=True)
class _Bag:
    """A ``map<union>`` member whose union cases hold nodes (a slot-argument bag), read
    in key order — the wire's order, whatever order a hand-built bag was written in."""

    field: str
    cases: dict[str, tuple[str, ...]]

    def read(self, value: Value) -> Iterator[tuple[Node, tuple[str | int, ...]]]:
        if isinstance(value, Obj):
            for key in sorted(value.fields):
                arg = value.fields[key]
                if isinstance(arg, Obj):
                    for member in self.cases.get(arg.tag or "", ()):
                        held = arg.fields.get(member)
                        if isinstance(held, Node):
                            yield held, (self.field, key, member)

    def map(self, value: Value, f: _Step) -> Value:
        if not isinstance(value, Obj):
            return value
        replaced: dict[str, Value] = {}
        for key in sorted(value.fields):
            arg = value.fields[key]
            if isinstance(arg, Obj):
                members = self.cases.get(arg.tag or "", ())
                new_arg = _map_members(arg, members, f) if members else arg
                if new_arg is not arg:
                    replaced[key] = new_arg
        return Obj(value.tag, {**value.fields, **replaced}) if replaced else value


def _map_members(obj: Obj, members: tuple[str, ...], f: _Step) -> Obj:
    replaced = {m: f(held) for m in members if isinstance(held := obj.fields.get(m), Node)}
    if all(replaced[m] is obj.fields[m] for m in replaced):
        return obj
    return Obj(obj.tag, {**obj.fields, **replaced})


type _Accessor = _List | _One | _Records | _Bag


def _holds_node(t: T, seen: frozenset[str] = frozenset()) -> bool:
    """Does a value of type ``t`` hold a node anywhere beneath it?"""
    if t.kind == "node":
        return True
    if t.of is not None and _holds_node(t.of, seen):
        return True
    if any(_holds_node(a, seen) for a in t.args):
        return True
    if t.name is None or t.name in seen:
        return False
    inner = seen | {t.name}
    if t.kind == "record":
        return any(_holds_node(f.type, inner) for f in RECORD_FIELDS.get(t.name, ()))
    if t.kind == "union":
        cases = UNION_CASE_FIELDS.get(t.name, {})
        return any(_holds_node(f.type, inner) for fields in cases.values() for f in fields)
    return False


def _direct_nodes(fields: tuple[F, ...], where: str) -> tuple[str, ...]:
    """The members of a record or union case that ARE a node; any member holding one
    deeper is a shape this module does not classify, and fails the import."""
    out: list[str] = []
    for f in fields:
        if f.type.kind == "node":
            out.append(f.name)
        elif _holds_node(f.type):
            raise TypeError(f"{where}.{f.name} holds a node in a shape the child enumeration does not classify")
    return tuple(out)


def _classify(kind: str, name: str, t: T) -> tuple[Position, _Accessor]:
    where = f"{kind}.{name}"
    if t.kind == "node":
        return Position.ARM, _One(name)
    if t.kind == "list" and t.of is not None and t.of.kind == "node":
        return Position.ORDERED, _List(name)
    if t.kind == "list" and t.of is not None and t.of.kind == "record" and t.of.name is not None:
        members = _direct_nodes(RECORD_FIELDS.get(t.of.name, ()), f"{where}[]")
        return Position.ARM, _Records(name, members)
    if t.kind == "map" and t.of is not None and t.of.kind == "union" and t.of.name is not None:
        cases = {
            case: members
            for case, fields in UNION_CASE_FIELDS.get(t.of.name, {}).items()
            if (members := _direct_nodes(fields, f"{where}{{}}|{case}"))
        }
        return Position.ARGUMENT, _Bag(name, cases)
    raise TypeError(f"{where} holds a node in a shape the child enumeration does not classify")


def _kind_slots() -> dict[str, tuple[tuple[Position, _Accessor], ...]]:
    table: dict[str, tuple[tuple[Position, _Accessor], ...]] = {}
    for kind, fields in KIND_FIELDS.items():
        held = tuple(_classify(kind, f.name, f.type) for f in fields if f.on_wire and _holds_node(f.type))
        if sum(1 for position, _ in held if position is Position.ORDERED) > 1:
            raise TypeError(f"{kind} holds two ordered child lists; the structural ops address one")
        if held:
            table[kind] = held
    return table


#: Each kind's node-holding members, in IDL member order, with their position class.
_KIND_SLOTS = _kind_slots()

#: Each container kind's ordered child-list member.
_ORDERED_FIELD: dict[str, str] = {
    kind: accessor.field
    for kind, held in _KIND_SLOTS.items()
    for position, accessor in held
    if position is Position.ORDERED
}

#: The envelope's state alternatives, in the reference host's order (onLoading first).
_STATE_ARMS: tuple[str, ...] = ("onLoading", "onEmpty")


def _check_envelope() -> None:
    for f in NODE_FIELDS:
        if not _holds_node(f.type):
            continue
        if f.name == "fallback" and f.type.kind == "node":
            continue
        if f.name == "state" and f.type.kind == "record" and f.type.name is not None:
            declared = _direct_nodes(RECORD_FIELDS.get(f.type.name, ()), "state")
            if set(declared) != set(_STATE_ARMS):
                raise TypeError(f"state holds {declared}; the child enumeration orders {_STATE_ARMS}")
            continue
        raise TypeError(f"the node envelope's {f.name} holds a node the child enumeration does not classify")


_check_envelope()


# ─── reading ─────────────────────────────────────────────────────────────────


def state_arms(state: Value) -> list[Node]:
    """The nodes a ``state`` envelope value holds: ``onLoading``, then ``onEmpty``."""
    if not isinstance(state, Obj):
        return []
    return [n for key in _STATE_ARMS if isinstance(n := state.fields.get(key), Node)]


def slots(node: Node) -> list[Slot]:
    """Every position ``node`` holds another node in, in the canonical order."""
    out: list[Slot] = []
    fields = node.kind.fields
    for position, accessor in _KIND_SLOTS.get(node.kind.tag or "", ()):
        out.extend(
            Slot(position, held, ("kind", *address)) for held, address in accessor.read(fields.get(accessor.field))
        )
    state = node.extras.get("state")
    if isinstance(state, Obj):
        for key in _STATE_ARMS:
            held = state.fields.get(key)
            if isinstance(held, Node):
                out.append(Slot(Position.STATE_ARM, held, ("state", key)))
    fallback = node.extras.get("fallback")
    if isinstance(fallback, Node):
        out.append(Slot(Position.FALLBACK, fallback, ("fallback",)))
    return out


def children(node: Node, reach: Reach) -> list[Node]:
    """The immediate children of ``node`` within ``reach``, in the canonical order."""
    return [s.node for s in slots(node) if s.position in reach]


def walk(tree: Node, reach: Reach) -> list[Node]:
    """Every node of ``tree`` within ``reach``, depth-first, root first."""
    out: list[Node] = []
    stack = [tree]
    while stack:
        node = stack.pop()
        out.append(node)
        stack.extend(reversed(children(node, reach)))
    return out


def find_node(tree: Node, node_id: str) -> Node | None:
    """The first node with ``node_id`` in ``tree`` (depth-first, root first), searching
    every position (:data:`KEYED`), or ``None``."""
    stack = [tree]
    while stack:
        node = stack.pop()
        if node.id == node_id:
            return node
        stack.extend(reversed(children(node, KEYED)))
    return None


def ordered_children(node: Node) -> list[Node] | None:
    """The ordered child list of a container kind, or ``None`` for a kind that holds
    none — the distinction the structural ops' ``ChildlessKind`` refusal rests on."""
    field = _ORDERED_FIELD.get(node.kind.tag or "")
    if field is None:
        return None
    value = node.kind.fields.get(field)
    if not isinstance(value, Arr):
        return None
    return [c for c in value.items if isinstance(c, Node)]


def with_ordered_children(node: Node, new_children: list[Node]) -> Node:
    """``node`` with its ordered child list replaced; a kind that holds none is returned
    unchanged."""
    field = _ORDERED_FIELD.get(node.kind.tag or "")
    if field is None:
        return node
    return Node(node.id, Obj(node.kind.tag, {**node.kind.fields, field: Arr(list(new_children))}), node.extras)


# ─── rebuilding ──────────────────────────────────────────────────────────────


def map_children(node: Node, reach: Reach, f: Callable[[Node], Node]) -> Node:
    """``node`` with every immediate child within ``reach`` replaced by ``f`` of it.

    The positions are visited once, in the canonical order; where ``f`` returns every
    child unchanged (by identity) the input node itself is returned.
    """
    fields = node.kind.fields
    new_fields: dict[str, Value] | None = None
    for position, accessor in _KIND_SLOTS.get(node.kind.tag or "", ()):
        if position not in reach:
            continue
        value = fields.get(accessor.field)
        mapped = accessor.map(value, f)
        if mapped is not value:
            new_fields = new_fields if new_fields is not None else dict(fields)
            new_fields[accessor.field] = mapped

    extras: dict[str, Value] | None = None
    state = node.extras.get("state")
    if Position.STATE_ARM in reach and isinstance(state, Obj):
        new_state = _map_members(state, _STATE_ARMS, f)
        if new_state is not state:
            extras = dict(node.extras)
            extras["state"] = new_state
    fallback = node.extras.get("fallback")
    if Position.FALLBACK in reach and isinstance(fallback, Node):
        new_fallback = f(fallback)
        if new_fallback is not fallback:
            extras = extras if extras is not None else dict(node.extras)
            extras["fallback"] = new_fallback

    if new_fields is None and extras is None:
        return node
    kind = node.kind if new_fields is None else Obj(node.kind.tag, new_fields)
    return Node(node.id, kind, node.extras if extras is None else extras)
