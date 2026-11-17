"""Tree introspection — walk a decoded ``Node`` tree and report each node's kind,
its bound binding slots (with the canonical wire-form expression), and its
structural children.

The Python analogue of the reference tier's read-only introspection surface: the
*static* view an agent uses to inspect a tree it (or the model) emitted — kind
discriminator, which slots are data-bound and how (`$state.<key>` / `$queries.<name>`
/ …), and the structural shape. Value resolution against a live host + geometry
probing are a renderer-side concern and are deliberately out of scope here (this
is the source-side static surface, matching the reference tier's split).

It walks the generic decoded model (``Node`` / ``Obj`` / ``Arr``) rather than a
per-kind table, so a new ``NodeKind`` needs no change here — a bound slot is any
field whose value is a ``Binding``-tagged object, discovered structurally.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..model import Arr, Node, Obj
from ..model import Value as WireValue
from ..ops.enumeration import KEYED, children, walk
from ..ops.enumeration import find_node as _find_node
from ..schema.decode import BINDING_CASES


@dataclass(frozen=True)
class BindingSlot:
    """One data-bound slot on a node: its dotted field path within the kind, the
    ``Binding`` case that produced it, and the canonical wire-form expression."""

    slot: str
    source: str
    expression: str


@dataclass(frozen=True)
class NodeIntrospection:
    """The per-node introspection envelope."""

    id: str
    kind: str
    bindings: tuple[BindingSlot, ...]
    child_ids: tuple[str, ...]


@dataclass(frozen=True)
class TreeIntrospection:
    """A recursive structural snapshot of a whole tree."""

    id: str
    kind: str
    bindings: tuple[BindingSlot, ...]
    child_ids: tuple[str, ...]
    children: tuple[TreeIntrospection, ...]


def kind_name(node: Node) -> str:
    """The wire discriminator for a node's kind (the ``$type`` tag) — e.g. ``Box``,
    ``Metric``, ``DataGrid``."""
    return node.kind.tag or "<untagged>"


def binding_expression(binding: Obj) -> tuple[str, str]:
    """Classify a ``Binding``-tagged object into ``(source, expression)`` — the
    canonical wire-form accessor the reference tier reports (`$state.<key>`, …)."""
    tag = binding.tag or ""
    fields = binding.fields
    if tag == "Static":
        return ("Static", "$static")
    if tag == "Query":
        return ("Query", f"$queries.{fields.get('name', '')}")
    if tag == "Filter":
        return ("Filter", f"$filters.{fields.get('name', '')}")
    if tag == "Selection":
        return ("Selection", f"$selection.{fields.get('nodeId', fields.get('sourceId', ''))}")
    if tag == "State":
        return ("State", f"$state.{fields.get('key', '')}")
    if tag == "I18n":
        return ("I18n", f"$i18n.{fields.get('key', '')}")
    if tag == "Local":
        return ("Computed", "$local")
    if tag == "Format":
        return ("Computed", "$format")
    if tag == "Transform":
        return ("Computed", "$transform")
    if tag == "Invoke":
        return ("Computed", "$invoke")
    # Computed + any future value-computing case.
    return ("Computed", "$computed")


def _is_binding(value: WireValue) -> bool:
    return isinstance(value, Obj) and value.tag in BINDING_CASES


def binding_slots(node: Node) -> tuple[BindingSlot, ...]:
    """Every data-bound slot in the node's kind, in document order, with a dotted
    field path (e.g. ``source``, ``spec.activeIndex``). Recurses through record
    objects + arrays but **not** into a nested ``Node`` (those belong to the child)."""
    slots: list[BindingSlot] = []

    def walk(value: WireValue, path: str) -> None:
        if isinstance(value, Node):
            return  # a child node's bindings are the child's, not this node's
        if isinstance(value, Obj):
            if _is_binding(value):
                source, expr = binding_expression(value)
                slots.append(BindingSlot(slot=path, source=source, expression=expr))
                return
            for name, field_value in value.fields.items():
                walk(field_value, f"{path}.{name}" if path else name)
        elif isinstance(value, Arr):
            for i, item in enumerate(value.items):
                walk(item, f"{path}[{i}]")

    for name, field_value in node.kind.fields.items():
        walk(field_value, name)
    return tuple(slots)


def child_nodes(node: Node) -> tuple[Node, ...]:
    """The immediate child nodes over the keyed walk — every position the node holds
    another node in: the kind's children and arms, its slot arguments, the
    ``state.onLoading`` / ``state.onEmpty`` alternatives and the author-declared
    ``fallback`` (fuaran#1812). Every one is a node position of the document, sharing
    the one id space (8.1), so every consumer of the decoded tree sees it."""
    return tuple(children(node, KEYED))


def child_ids(node: Node) -> tuple[str, ...]:
    return tuple(c.id for c in child_nodes(node))


def walk_nodes(tree: Node) -> list[Node]:
    """Depth-first walk of every node in the tree, root first (the keyed walk)."""
    return walk(tree, KEYED)


def find_node(tree: Node, node_id: str) -> Node | None:
    """The first node with ``node_id`` (depth-first, the keyed walk), or ``None`` —
    the same lookup the apply engine locates an op's target by."""
    return _find_node(tree, node_id)


def _introspect(node: Node) -> NodeIntrospection:
    return NodeIntrospection(
        id=node.id,
        kind=kind_name(node),
        bindings=binding_slots(node),
        child_ids=child_ids(node),
    )


def node_state(tree: Node, node_id: str) -> NodeIntrospection | None:
    """The introspection envelope for a single node by id, or ``None``."""
    node = find_node(tree, node_id)
    return None if node is None else _introspect(node)


def inspect_tree(tree: Node) -> TreeIntrospection:
    """A recursive structural snapshot of the whole tree."""
    base = _introspect(tree)
    return TreeIntrospection(
        id=base.id,
        kind=base.kind,
        bindings=base.bindings,
        child_ids=base.child_ids,
        children=tuple(inspect_tree(c) for c in child_nodes(tree)),
    )
