"""``fuaran_ui.reconcile`` — merge by fold: the reconciliation policy (WIRE_FORMAT §34).

A merge of two branches is a **derivation**: both branches' op deltas are folded
from their common base, and the merge records only what the fold cannot decide on
its own — a script. Core's conflict report (:func:`fuaran_ui.merge.conflicts_in`,
over the base-aware footprint) decides which pairs interfere; this module is the
thin policy over it, the Python port of the reference ``DagReconcile``
(Phase 2293; this host, Phase 2642). Each reported pair is one of:

1. an **agreement** — the two ops encode to the same canonical bytes, so the fold
   already holds the effect when it reaches the second. Admitted;
2. an **interleave** — an ``InsertPositionClash`` at a parent both ops append a
   DIFFERENT node to (an ``InsertChild`` under it, or a ``MoveNode`` to it).
   Admitted, and ordered by the child-order rule;
3. a **rebase** (fuaran#2671) — one op is a ``ReorderChildren(p, order)`` and the
   other changes ``p``'s child list: an ``InsertChild`` under ``p``, a
   ``MoveNode`` to ``p``, or a ``RemoveNode`` or ``MoveNode`` of a child ``order``
   lists (neither a ``Batch``). Admitted: the fold applies the reorder REBASED to
   the children ``p`` holds when it is reached — the listed children still there,
   in the declared order, then the rest in held order;
4. anything else — **refused**, classed ``DeleteModify`` when exactly one op
   removes (a ``RemoveNode``, or a ``Batch`` holding one) and ``Conflict``
   otherwise. Two reorders of one parent that differ stay refused.

**The child-order rule.** Under an interleaved or rebased parent the children
neither delta appended keep the fold's order (the rebased reorder's, where a delta
reordered the parent), and the appended ones follow: each delta's run of appends
in its own order, the two runs merged by comparing heads ordinally by NodeId, the
smaller first, and a node both runs hold (two agreeing moves) listed once, where
the merge first reaches it. An append a later reorder of the same parent on the
same delta lists is settled by that reorder and leaves the run. The merge records
``ReorderChildren(parent, order)`` for EVERY interleaved or rebased parent
(fuaran#2645): a merge node is replayed by Core's closure fold in its own drain
order, so the script fixes the order. The fold runs in BOTH orders, each followed
by the script, and a disagreement is refused as ``OrderSensitive`` — the admitted
classes lie outside Core's independence theorem, so this is checked, not assumed.

Since fuaran#2645 this is the op DAG's ONLY merge; the three-way tree merge
retired with its ``merge-conformance`` keys (fuaran#2646).

The refusal envelope (§34.5) is a JSON array of the refused pairs with
ordinal-sorted keys; its sha256 is the cross-host refusal hash.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .canonical import escape_string, utf16_order_key
from .merge import INSERT_POSITION_CLASH, SLOT_CLASH, OpConflict, conflicts_in
from .model import Arr, Node, Obj, Value
from .ops.apply import ApplyError, _layout_children, apply
from .ops.encode import encode_op
from .result import Ok
from .schema import encode_node

__all__ = [
    "DELETE_MODIFY",
    "CONFLICT",
    "Refusal",
    "DeltaDoesNotApply",
    "Refused",
    "OrderSensitive",
    "FoldFaulted",
    "IntroducedDefects",
    "ReconcileFault",
    "Reconciled",
    "apply_held",
    "reconcile",
    "reconcile_gated",
    "sort_canonical",
    "encode_refusal_envelope",
]

#: The two refusal classes, by their wire names.
DELETE_MODIFY = "DeleteModify"
CONFLICT = "Conflict"


@dataclass(frozen=True)
class Refusal:
    """One refused pair: Core's report entry and the policy's class."""

    conflict: OpConflict
    refusal_class: str


@dataclass(frozen=True)
class DeltaDoesNotApply:
    """A branch's own delta does not apply from the base: ``side`` (``"A"`` / ``"B"``),
    the index of the first failing op, and the engine's error."""

    side: str
    index: int
    error: ApplyError


@dataclass(frozen=True)
class Refused:
    """Core reported pairs the policy does not admit, in canonical order."""

    refusals: tuple[Refusal, ...]


@dataclass(frozen=True)
class OrderSensitive:
    """Every pair was admitted and the two fold orders reached different trees."""

    a_then_b: str
    b_then_a: str


@dataclass(frozen=True)
class FoldFaulted:
    """Every pair was admitted and the fold faulted applying an op."""

    error: ApplyError


@dataclass(frozen=True)
class IntroducedDefects[D]:
    """The derived tree carries defects neither branch head had (§27.8.2's gate)."""

    defects: tuple[D, ...]


type ReconcileFault = DeltaDoesNotApply | Refused | OrderSensitive | FoldFaulted | IntroducedDefects[object]


@dataclass(frozen=True)
class Reconciled:
    """A derived merge: the tree, the script the merge records (one
    ``ReorderChildren`` per interleaved or rebased parent, parents in ordinal
    order), the distinct agreeing ops applied as no change, and how many parents
    were interleaved or rebased."""

    tree: Node
    script: tuple[Obj, ...]
    agreements: int
    interleaves: int


# ─── the fold ────────────────────────────────────────────────────────────────


def _children(node: Node) -> list[Node]:
    return _layout_children(node) or []


def _find(node_id: str, node: Node) -> Node | None:
    if node.id == node_id:
        return node
    for child in _children(node):
        found = _find(node_id, child)
        if found is not None:
            return found
    return None


def _str(op: Obj, name: str) -> str:
    v = op.fields.get(name)
    return v if isinstance(v, str) else ""


def _held(op: Obj, tree: Node) -> bool:
    """Does ``tree`` already hold ``op``'s effect? Only the two cases where
    re-applying an identical op FAILS are named; every other op is idempotent."""
    if op.tag == "RemoveNode":
        return _find(_str(op, "target"), tree) is None
    if op.tag == "InsertChild":
        parent = _find(_str(op, "parentId"), tree)
        child = op.fields.get("child")
        if parent is None or not isinstance(child, Node):
            return False
        wanted = encode_node(child)
        return any(c.id == child.id and encode_node(c) == wanted for c in _children(parent))
    return False


def _members(op: Obj) -> list[Obj]:
    inner = op.fields.get("ops")
    return [o for o in inner.items if isinstance(o, Obj)] if isinstance(inner, Arr) else []


def apply_held(op: Obj, tree: Node) -> Ok[Node] | ApplyError:
    """:func:`~fuaran_ui.ops.apply`, with an op whose effect ``tree`` already holds
    applied as no change. A ``Batch`` is applied member by member under the same
    rule, atomically: the first member that fails and is not held fails it."""
    if op.tag == "Batch":
        current = tree
        for member in _members(op):
            step = apply_held(member, current)
            if not isinstance(step, Ok):
                return step
            current = step.value
        return Ok(current)
    result = apply(op, tree)
    if isinstance(result, Ok):
        return result
    return Ok(tree) if _held(op, tree) else result.error


def _order(op: Obj) -> list[str]:
    order = op.fields.get("newOrder")
    return [c for c in order.items if isinstance(c, str)] if isinstance(order, Arr) else []


def _rebased(tree: Node, op: Obj) -> Obj:
    """A reorder rebased to the children its parent holds in ``tree``: the listed
    children still there, in the declared order, then the others in held order.
    ``op`` itself where the parent is not in the tree."""
    parent = _str(op, "parentId")
    node = _find(parent, tree)
    if node is None:
        return op
    kids = [c.id for c in _children(node)]
    listed = list(dict.fromkeys(c for c in _order(op) if c in set(kids)))
    order: list[Value] = [*listed, *(k for k in kids if k not in set(listed))]
    return Obj("ReorderChildren", {"newOrder": Arr(order), "parentId": parent})


def _fold(ops: list[Obj], start: Node, rebase: frozenset[str] = frozenset()) -> Ok[Node] | ApplyError:
    """:func:`apply_held` op by op, with a reorder of a parent in ``rebase`` applied
    rebased to the children the parent holds when the fold reaches it."""
    current = start
    for op in ops:
        if op.tag == "ReorderChildren" and _str(op, "parentId") in rebase:
            op = _rebased(current, op)
        step = apply_held(op, current)
        if not isinstance(step, Ok):
            return step
        current = step.value
    return Ok(current)


def _first_failure(delta: list[Obj], base: Node) -> tuple[int, ApplyError] | None:
    current = base
    for i, op in enumerate(delta):
        result = apply(op, current)
        if not isinstance(result, Ok):
            return i, result.error
        current = result.value
    return None


# ─── the policy ──────────────────────────────────────────────────────────────


def _removes(op: Obj) -> bool:
    if op.tag == "RemoveNode":
        return True
    return op.tag == "Batch" and any(_removes(o) for o in _members(op))


def _append_into(parent: str, op: Obj) -> str | None:
    """The node ``op`` appends to ``parent``, when it is an append there."""
    if op.tag == "InsertChild" and _str(op, "parentId") == parent:
        child = op.fields.get("child")
        return child.id if isinstance(child, Node) else None
    if op.tag == "MoveNode" and _str(op, "newParentId") == parent:
        return _str(op, "target")
    return None


_AGREEMENT = "agreement"
_INTERLEAVE = "interleave"
_REBASE = "rebase"


def _rebase_of(reorder: Obj, other: Obj) -> str | None:
    """The parent whose child list ``reorder`` and ``other`` both change, when
    ``reorder`` is a ``ReorderChildren`` and ``other`` an append to its parent or a
    removal or move of a child it lists."""
    if reorder.tag != "ReorderChildren":
        return None
    parent, order = _str(reorder, "parentId"), _order(reorder)
    if other.tag == "InsertChild" and _str(other, "parentId") == parent:
        return parent
    if other.tag == "MoveNode" and (_str(other, "newParentId") == parent or _str(other, "target") in order):
        return parent
    if other.tag == "RemoveNode" and _str(other, "target") in order:
        return parent
    return None


def _judge(c: OpConflict) -> tuple[str, str | None]:
    """``agreement``, ``interleave``, ``rebase`` (with its parent) or a refusal class."""
    if encode_op(c.left) == encode_op(c.right):
        return _AGREEMENT, None
    left, right = _append_into(c.address, c.left), _append_into(c.address, c.right)
    if c.shape == INSERT_POSITION_CLASH and left is not None and right is not None and left != right:
        return _INTERLEAVE, c.address
    rebased = _rebase_of(c.left, c.right) or _rebase_of(c.right, c.left)
    if rebased is not None:
        return _REBASE, rebased
    return (DELETE_MODIFY if _removes(c.left) != _removes(c.right) else CONFLICT), None


def _order_key(r: Refusal) -> tuple[bytes, ...]:
    c = r.conflict
    return (
        utf16_order_key(c.address),
        utf16_order_key(c.shape),
        utf16_order_key(c.slot or ""),
        utf16_order_key(encode_op(c.left)),
        utf16_order_key(encode_op(c.right)),
    )


def sort_canonical(refusals: list[Refusal] | tuple[Refusal, ...]) -> list[Refusal]:
    """Refusals in envelope order — address, shape, slot, left bytes, right bytes,
    each ordinal — with exact duplicates removed."""
    unique: dict[tuple[bytes, ...], Refusal] = {}
    for r in refusals:
        unique.setdefault(_order_key(r), r)
    return [unique[k] for k in sorted(unique)]


def _interleave(a: list[str], b: list[str]) -> list[str]:
    """Two runs, each in its own order, merged by comparing heads ordinally. An id
    both runs hold appears twice; :func:`_reorder_for` keeps its first occurrence."""
    out: list[str] = []
    i = j = 0
    while i < len(a) and j < len(b):
        if utf16_order_key(a[i]) <= utf16_order_key(b[j]):
            out.append(a[i])
            i += 1
        else:
            out.append(b[j])
            j += 1
    return out + a[i:] + b[j:]


def _reorder_for(tree: Node, parent: str, run_a: list[str], run_b: list[str]) -> Obj | None:
    """The ``ReorderChildren`` the rule asks of ``parent``; ``None`` when the parent
    is not in the tree. Recorded even where the fold already gives the rule's order
    (fuaran#2645): a merge node's replay applies the branches in Core's drain order,
    so the script has to fix the order."""
    node = _find(parent, tree)
    if node is None:
        return None
    kids = [c.id for c in _children(node)]
    present = set(kids)
    appended = set(run_a) | set(run_b)

    def keep(run: list[str]) -> list[str]:
        return list(dict.fromkeys(i for i in run if i in present))

    # A node both runs hold (two agreeing moves) is listed once, where the merge
    # first reaches it — the same whichever run is named first.
    order = [k for k in kids if k not in appended] + list(dict.fromkeys(_interleave(keep(run_a), keep(run_b))))
    new_order: list[Value] = list(order)
    return Obj("ReorderChildren", {"newOrder": Arr(new_order), "parentId": parent})


def _appends_of(parent: str, delta: list[Obj]) -> list[str]:
    """A delta's run of appends into ``parent``, in delta order. An append a later
    reorder of the same parent on the delta lists is settled by that reorder: the
    delta placed it, so it leaves the run."""
    run: list[str] = []
    for op in delta:
        if op.tag == "ReorderChildren" and _str(op, "parentId") == parent:
            listed = set(_order(op))
            run = [x for x in run if x not in listed]
            continue
        appended = _append_into(parent, op)
        if appended is not None and appended not in run:
            run.append(appended)
    return run


def _script_for(tree: Node, parents: list[str], delta_a: list[Obj], delta_b: list[Obj]) -> list[Obj]:
    script: list[Obj] = []
    for p in parents:
        reorder = _reorder_for(tree, p, _appends_of(p, delta_a), _appends_of(p, delta_b))
        if reorder is not None:
            script.append(reorder)
    return script


def reconcile(base: Node, delta_a: list[Obj], delta_b: list[Obj]) -> Reconciled | ReconcileFault:
    """Reconcile two branch deltas from their common base under the policy: the
    derived tree and the script a merge records, or a typed refusal."""
    for side, delta in (("A", delta_a), ("B", delta_b)):
        failure = _first_failure(delta, base)
        if failure is not None:
            return DeltaDoesNotApply(side, failure[0], failure[1])

    verdicts = [(c, _judge(c)) for c in conflicts_in(base, delta_a, delta_b)]
    refusals = [Refusal(c, v) for c, (v, _) in verdicts if v in (DELETE_MODIFY, CONFLICT)]
    if refusals:
        return Refused(tuple(sort_canonical(refusals)))

    rebase = frozenset(p for _, (v, p) in verdicts if v == _REBASE and p is not None)
    parents = sorted(
        {p for _, (v, p) in verdicts if v in (_INTERLEAVE, _REBASE) and p is not None}, key=utf16_order_key
    )
    agreements = len({encode_op(c.left) for c, (v, _) in verdicts if v == _AGREEMENT})

    def derive(first: list[Obj], second: list[Obj]) -> tuple[Node, list[Obj]] | ApplyError:
        folded = _fold(first + second, base, rebase)
        if not isinstance(folded, Ok):
            return folded
        script = _script_for(folded.value, parents, delta_a, delta_b)
        normalised = _fold(script, folded.value)
        return normalised if not isinstance(normalised, Ok) else (normalised.value, script)

    ab, ba = derive(delta_a, delta_b), derive(delta_b, delta_a)
    if isinstance(ab, ApplyError):
        return FoldFaulted(ab)
    if isinstance(ba, ApplyError):
        return FoldFaulted(ba)
    ab_text, ba_text = encode_node(ab[0]), encode_node(ba[0])
    if ab_text != ba_text:
        return OrderSensitive(ab_text, ba_text)
    return Reconciled(ab[0], tuple(ab[1]), agreements, len(parents))


def reconcile_gated[D](
    introduced_defects: Callable[[Node, Node, Node], list[D]],
    base: Node,
    delta_a: list[Obj],
    delta_b: list[Obj],
) -> Reconciled | ReconcileFault:
    """:func:`reconcile`, then the validator gate: a defect the derived tree carries
    and neither branch head does refuses the merge. ``introduced_defects`` takes the
    two heads and the derived tree (§27.8.2's diff, over the host's validator)."""
    result = reconcile(base, delta_a, delta_b)
    if not isinstance(result, Reconciled):
        return result

    def head(delta: list[Obj]) -> Node:
        folded = _fold(delta, base)
        return folded.value if isinstance(folded, Ok) else base

    defects = introduced_defects(head(delta_a), head(delta_b), result.tree)
    return IntroducedDefects(tuple(defects)) if defects else result


def encode_refusal_envelope(refusals: list[Refusal] | tuple[Refusal, ...]) -> str:
    """The §34.5 refusal envelope: the refused pairs in canonical order, each
    ``{"address","class","left","right","shape"[,"slot"]}`` with the ops embedded
    as their canonical encodings. Byte-stable across hosts."""
    entries = []
    for r in sort_canonical(refusals):
        c = r.conflict
        entry = (
            '{"address":'
            + escape_string(c.address)
            + ',"class":'
            + escape_string(r.refusal_class)
            + ',"left":'
            + encode_op(c.left)
            + ',"right":'
            + encode_op(c.right)
            + ',"shape":'
            + escape_string(c.shape)
        )
        if c.shape == SLOT_CLASH:
            entry += ',"slot":' + escape_string(c.slot or "")
        entries.append(entry + "}")
    return "[" + ",".join(entries) + "]"
