"""The structural tree-op diff — the inverse of the apply engine.

``diff(before, after)`` computes a minimal, deterministic ``TreeOp`` sequence that
transforms one decoded ``Node`` tree into another, addressed by ``NodeId``. It is
the sibling of the F# ``TreeOpDiff`` engine, built to the same contract rather than
transpiled.

**The round-trip law** (the correctness contract every host shares):

    fold(apply, before, diff(before, after)) == after

where equality is **canonical-JSON** bytes (:func:`~fuaran_ui.canonical.encode_value`),
the same oracle the corpus commits to — *not* Python object identity.

**Algorithm** (mirrors the F# engine, deterministic op order throughout):

1. **Cross-parent move pre-pass.** Index each tree as ``nodeId -> parentId`` over the
   structural (layout) child lists; a node present in *both* trees whose parent changed,
   whose new parent already exists in ``before``, and whose move introduces no cycle, is
   emitted as ``MoveNode(id, newParent, 0)`` and folded through :func:`~fuaran_ui.ops.apply`
   to form an intermediate tree. The remaining diff runs move-free against it.
2. **Per node** (``before.id == after.id``): if the *kind-own* fields changed (a canonical
   compare of the childless kind, style/state/accessibility neutralised) — try a granular
   field-level refinement (``UpdateProp`` per differing addressable field), **propose-then-verify**
   by applying the candidates and accepting only if they reproduce ``after``'s kind; otherwise
   take the coarse-but-correct floor ``EditNode(after.kind)`` (which carries ``after``'s whole
   subtree). Then emit the style edit and ``UpdateState`` for a drifted facet, and
   ``UpdateAccessibility`` / ``UpdateTooltip`` for a trait that moved (Phase 2527). A style edit
   is field-level (Phase 2314, the F# engine's rule since Phase 2291): ``PatchStyle`` naming
   exactly the members that moved, and the whole-record ``UpdateStyle`` only when all six did.
3. **Children** (layout child list): ``RemoveNode`` for dropped ids (before-order), ``InsertChild``
   for new children appended at the growing tail, a single ``ReorderChildren`` if the post-insert
   order still differs from the target, then recurse into the children present on both sides.

**Minimality is "coarse-but-correct floor + verified refinement":** correctness (the round-trip
law) is unconditional; the granularity of the emitted ops can regress to a wholesale ``EditNode``
without ever breaking round-trip. One edit is inexpressible in the op vocabulary and therefore
not emitted (mirroring the F# engine): a state **removal** — a state present in ``before`` but
absent in ``after`` (``UpdateState`` can set the facet, never delete it). It does not arise from an
op-reachable ``after``, which is the round-trip law's domain. A style removal IS expressible: an
absent style slot is the all-default record (WIRE_FORMAT §3.1), so it diffs to the ``PatchStyle``
returning the moved members to their default tokens, which the apply engine stores as the absent
slot (Phase 2313). So is an accessibility removal, the ``UpdateAccessibility`` carrying the empty
record, and a tooltip removal, the ``UpdateTooltip`` carrying no ``tooltip`` member (Phase 2527).
"""

from __future__ import annotations

from ..canonical import encode_value
from ..model import Arr, Node, Obj, Value
from ..result import Ok
from ..schema.decode import _STYLE_DEFAULTS
from .apply import _FIELDS, _NOT_SUPPORTED, apply
from .enumeration import STRUCTURAL, children, with_ordered_children

# ── facet-isolation helpers (closure-safe canonical probes) ──────────────────
#
# **Traversal never exceeds apply's addressable set**: the diff recurses through the
# STRUCTURAL child lists of :mod:`~fuaran_ui.ops.enumeration`, the surface the apply
# engine's structural ops edit, so every op it emits targets a node apply can locate.
#
# One bound is this producer's own granularity, and it is DECLARED rather than derived:
# a change inside a ``Modal`` or a ``ScrollArea`` takes the ``EditNode`` floor at the
# container, carrying its whole new kind, instead of recursing into its ordered list.
# The cross-host diff goldens pin exactly that script for this host (the
# ``modal-interior-change`` / ``scrollarea-interior-change`` entries of the shared
# golden family's ``divergent`` section), so recursing there is a change to the shared
# golden first. Both scripts satisfy the round-trip law; only the depth differs.
_FLOORED_CONTAINERS = frozenset({"Modal", "ScrollArea"})


def _children(node: Node) -> list[Node]:
    """The structural child list the diff recurses through; ``[]`` for a childless kind
    and for a container the diff floors (:data:`_FLOORED_CONTAINERS`)."""
    if node.kind.tag in _FLOORED_CONTAINERS:
        return []
    return children(node, STRUCTURAL)


def _childless_kind(kind: Obj) -> Obj:
    """The kind with the structural child list the diff recurses through emptied (isolates
    the kind-own fields). On a floored container, or a kind holding no ordered list, the
    ``children`` field is kind-own content and stays part of the kind-change probe."""
    if kind.tag in _FLOORED_CONTAINERS:
        return kind
    return with_ordered_children(Node("", kind, {}), []).kind


def _kind_canonical(node: Node) -> str:
    """Canonical bytes of the node's kind-own fields alone (children emptied,
    style/state/accessibility neutralised) — the kind-change probe."""
    return encode_value(Node(node.id, _childless_kind(node.kind), {}))


def _style_record(node: Node) -> dict[str, Value]:
    """The node's whole style record, every member materialised: an absent slot, or a
    member omitted at its default, reads as that member's default token (§3.1/§3.6)."""
    style = node.extras.get("style")
    present = style.fields if isinstance(style, Obj) else {}
    return {member: present.get(member, default) for member, default in _STYLE_DEFAULTS.items()}


def _state_of(node: Node) -> Value | None:
    return node.extras.get("state")


def _canon(value: Value) -> str:
    return encode_value(value)


# ── move detection (cross-parent) ────────────────────────────────────────────


def _index_parents(node: Node, parent_id: str | None, acc: dict[str, str]) -> None:
    for child in _children(node):
        acc[child.id] = node.id
        _index_parents(child, node.id, acc)


def _all_ids(node: Node) -> set[str]:
    ids = {node.id}
    for child in _children(node):
        ids |= _all_ids(child)
    return ids


def _is_ancestor(ancestor_id: str, descendant_id: str, root: Node) -> bool:
    """``True`` when ``descendant_id`` sits under ``ancestor_id`` in ``root``."""
    if root.id == ancestor_id:
        return descendant_id in (_all_ids(root) - {ancestor_id})
    for child in _children(root):
        if _is_ancestor(ancestor_id, descendant_id, child):
            return True
    return False


def _detect_moves(a: Node, b: Node) -> list[tuple[str, str]]:
    """``(movedId, newParentId)`` for ids in *both* trees whose structural parent
    changed, whose new parent exists in ``a``, and whose move introduces no cycle.
    Deterministically ordered by moved id."""
    pa: dict[str, str] = {}
    _index_parents(a, None, pa)
    pb: dict[str, str] = {}
    _index_parents(b, None, pb)
    a_ids = _all_ids(a)
    moves: list[tuple[str, str]] = []
    for node_id in sorted(pb):
        parent_b = pb[node_id]
        parent_a = pa.get(node_id)
        if (
            parent_a is not None
            and parent_a != parent_b
            and parent_b in a_ids
            and not _is_ancestor(node_id, parent_b, a)
        ):
            moves.append((node_id, parent_b))
    return moves


# ── field-level refinement (propose-then-verify) ─────────────────────────────


def _reverse_fields(tag: str | None) -> dict[str, str] | None:
    """camelCase wire key -> PascalCase ``UpdateProp`` path for a kind's flat field
    surface, or ``None`` when the kind has no addressable field table."""
    table = _FIELDS.get(tag or "")
    if table is None:
        return None
    rev: dict[str, str] = {}
    for pascal, camel in table.items():
        if camel == _NOT_SUPPORTED:
            continue
        rev[camel] = pascal
    return rev


def _extract_field_updates(a: Node, b: Node) -> list[Obj] | None:
    """Candidate ``UpdateProp`` ops for a same-tag kind change, or ``None`` when the
    difference is not expressible field-wise (an unaddressable or removed field)."""
    rev = _reverse_fields(a.kind.tag)
    if rev is None:
        return None
    a_fields = a.kind.fields
    b_fields = b.kind.fields
    keys = (set(a_fields) | set(b_fields)) - {"children"}
    ops: list[Obj] = []
    for camel in sorted(keys):
        av = a_fields.get(camel)
        bv = b_fields.get(camel)
        if _canon(av) == _canon(bv):
            continue
        if camel not in rev or bv is None:
            return None  # unaddressable, or a field removal — fall to the floor
        ops.append(Obj("UpdateProp", {"path": rev[camel], "target": a.id, "value": bv}))
    return ops


def _try_field_level(a: Node, b: Node) -> list[Obj] | None:
    """Granular ``UpdateProp`` ops that reproduce ``b``'s kind, or ``None`` (floor)."""
    if a.kind.tag != b.kind.tag:
        return None
    candidates = _extract_field_updates(a, b)
    if not candidates:
        return None
    probe: Node = a
    for op in candidates:
        result = apply(op, probe)
        if not isinstance(result, Ok):
            return None
        probe = result.value
    return candidates if _kind_canonical(probe) == _kind_canonical(b) else None


def _style_ops(a: Node, b: Node) -> list[Obj]:
    """The op taking ``a``'s style to ``b``'s: nothing when the records are equal;
    ``PatchStyle`` naming exactly the members that moved (a member returning to its
    default is spelled with the default token — in a delta that is an edit, not an
    omission); and the whole-record ``UpdateStyle`` only when every member moved."""
    before = _style_record(a)
    after = _style_record(b)
    moved = {member: value for member, value in after.items() if _canon(before[member]) != _canon(value)}
    if not moved:
        return []
    if len(moved) == len(after):
        record = {member: value for member, value in after.items() if value != _STYLE_DEFAULTS[member]}
        return [Obj("UpdateStyle", {"style": Obj(None, record), "target": b.id})]
    return [Obj("PatchStyle", {"style": Obj(None, moved), "target": b.id})]


def _trait_ops(a: Node, b: Node) -> list[Obj]:
    """The ops taking ``a``'s two traits to ``b``'s, each only when it moved. An absent
    accessibility and an empty one are the same trait, as the apply engine stores it."""
    ops: list[Obj] = []
    empty = Obj(None, {})
    after = b.extras.get("accessibility", empty)
    if _canon(a.extras.get("accessibility", empty)) != _canon(after):
        ops.append(Obj("UpdateAccessibility", {"accessibility": after, "target": b.id}))
    tooltip = b.extras.get("tooltip")
    if _canon(a.extras.get("tooltip")) != _canon(tooltip):
        fields: dict[str, Value] = {"target": b.id}
        if tooltip is not None:
            fields["tooltip"] = tooltip
        ops.append(Obj("UpdateTooltip", fields))
    return ops


def _facet_ops(a: Node, b: Node) -> list[Obj]:
    """The style edit, then ``UpdateState`` for a state that drifted (and is present on
    ``b`` — a state removal is not op-expressible), then the trait edits."""
    ops = _style_ops(a, b)
    b_state = _state_of(b)
    if b_state is not None and _canon(_state_of(a)) != _canon(b_state):
        ops.append(Obj("UpdateState", {"state": b_state, "target": b.id}))
    return ops + _trait_ops(a, b)


# ── children diff (removes -> inserts -> reorders -> recurse) ─────────────────


def _children_diff(a: Node, b: Node) -> list[Obj]:
    a_kids = _children(a)
    b_kids = _children(b)
    a_ids = [c.id for c in a_kids]
    b_ids = [c.id for c in b_kids]
    a_set, b_set = set(a_ids), set(b_ids)
    a_map = {c.id: c for c in a_kids}
    ops: list[Obj] = []

    # removes — before-order, ids absent from after
    for cid in a_ids:
        if cid not in b_set:
            ops.append(Obj("RemoveNode", {"target": cid}))

    # inserts — after-only children appended. This diff already targeted the
    # growing tail, so 0.4.0's append-only InsertChild changes nothing about the
    # script it emits; only the ordinal, which merely restated the tail, goes.
    survivors = [cid for cid in a_ids if cid in b_set]
    b_only = [c for c in b_kids if c.id not in a_set]
    for child in b_only:
        ops.append(Obj("InsertChild", {"child": child, "parentId": a.id}))

    # reorder — single op if the post-insert order still differs from the target
    post_insert = survivors + [c.id for c in b_only]
    if post_insert != b_ids:
        ops.append(Obj("ReorderChildren", {"newOrder": Arr(list(b_ids)), "parentId": a.id}))

    # recurse into children present on both sides
    for child in b_kids:
        if child.id in a_map:
            ops.extend(_diff_node(a_map[child.id], child))
    return ops


# ── per-node diff ────────────────────────────────────────────────────────────


def _diff_node(a: Node, b: Node) -> list[Obj]:
    """Diff two nodes sharing an id (``a.id == b.id``)."""
    if _kind_canonical(a) != _kind_canonical(b):
        refined = _try_field_level(a, b)
        if refined is not None:
            return refined + _facet_ops(a, b) + _children_diff(a, b)
        # floor: EditNode carries after's whole kind (children included) — no child recursion
        return [Obj("EditNode", {"newKind": b.kind, "target": b.id})] + _facet_ops(a, b)
    return _facet_ops(a, b) + _children_diff(a, b)


# ── public entry ─────────────────────────────────────────────────────────────


def diff(before: Node, after: Node) -> list[Obj]:
    """The minimal, deterministic ``TreeOp`` list transforming ``before`` into ``after``.

    Each op is a decoded :class:`~fuaran_ui.model.Obj` (tag + fields), ready for
    :func:`~fuaran_ui.ops.apply` or :func:`~fuaran_ui.ops.encode_op`. Satisfies the
    round-trip law ``fold(apply, before, diff(before, after)) == after`` under
    canonical-JSON equality.
    """
    # A changed root id is expressible only by ReplaceRoot (the one op that may swap it).
    if before.id != after.id:
        return [Obj("ReplaceRoot", {"node": after})]

    ops: list[Obj] = []
    intermediate = before
    for moved_id, new_parent in _detect_moves(before, after):
        move_op = Obj("MoveNode", {"newParentId": new_parent, "target": moved_id})
        result = apply(move_op, intermediate)
        if isinstance(result, Ok):
            ops.append(move_op)
            intermediate = result.value
    ops.extend(_diff_node(intermediate, after))
    return ops


def diff_batched(before: Node, after: Node) -> list[Obj]:
    """As :func:`diff`, but wrap a multi-op result in a single ``Batch`` (atomic apply)."""
    ops = diff(before, after)
    if len(ops) >= 2:
        return [Obj("Batch", {"ops": Arr(list(ops))})]
    return ops
