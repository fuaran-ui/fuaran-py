"""``fuaran_py.merge`` — deterministic author-agnostic 3-way tree merge.

The branching op-stream (:mod:`fuaran_py.dag`) lets an AI branch and a human
branch fork from a common base; this module merges them back. It is the Python
conformant host of the deterministic 3-way merge — the sibling of the F#
``TreeMerge.merge3Way`` and the TypeScript ``@fuaran-ui/ops`` ``merge3Way`` — and
is certified byte-for-byte against the workspace
``wire-format-fixtures/merge-conformance/`` corpus.

A node decomposes into independent **facets**, each merged on its own:

* ``kind`` — the node's own kind-fields (children + style + state + accessibility
  neutralised in the canonical probe).
* ``style.{tone,weight,emphasis,role,voice}`` — the ``SemanticStyle`` sub-fields,
  merged *independently* (A's tone + B's voice auto-blend on the same node).
* ``state`` — the ``StateBehaviour`` block.
* ``accessibility`` — the ``Accessibility`` block.
* ``children`` — the ordered child-id list (structural).

When a facet changed on at most one side, that side's value is taken; when both
changed it differently, it is a **conflict** (returned, not silently picked). The
one structural case auto-merged across both sides is disjoint pure inserts into
the same parent, ordered by NodeId code-point (Ordinal) — the deterministic,
wall-clock-free tie-break. Facet equality is canonical-JSON bytes
(:func:`~fuaran_py.canonical.encode_value`), the same oracle the corpus commits
to, except the closure-free style sub-fields, compared directly.

Two layers share the one facet engine:

* :func:`merge_3way` — the **author-agnostic** wire-host merge (both sides symmetric;
  a genuine conflict keeps ``base`` and is surfaced). This is the corpus-certified
  contract, byte-identical to the F# and TypeScript hosts.
* :func:`merge3_way_with_author` / :func:`merge3` — the **human-primacy** DAG layer
  (the sibling of the F# ``merge3WayWithAuthor``): a branch tagged :class:`Primary`
  (the human) wins a conflicted cell over a :class:`Secondary` (an agent), and the
  conflict is recorded as *resolved* rather than blocking. The authorship tag is
  host-supplied and opaque — the merge mechanism is unchanged, only conflict
  resolution gains precedence.
"""

from __future__ import annotations

from dataclasses import dataclass

from .canonical import encode_value
from .model import Arr, Node, Obj, Value

# SemanticStyle sub-field defaults (an absent style ⟺ all of these; §3.1).
_STYLE_DEFAULTS: dict[str, str] = {"emphasis": "Normal", "tone": "Default", "weight": "Standard"}
_FACET_EXTRAS = ("style", "state", "accessibility")


# ─── merge authorship (the human-primacy layer) ──────────────────────────────
#
# The plain 3-way merge (:func:`merge_3way`) is **author-agnostic** — both branches
# are symmetric and a genuine conflict keeps ``base`` and is surfaced (the wire-host
# contract, certified by the corpus). The **DAG layer** adds precedence: a branch
# tagged :class:`Primary` (the human) wins a conflicted cell over a :class:`Secondary`
# (an agent), and the conflict is recorded as *resolved* (``primacy_held``) rather
# than blocking. This mirrors the F# ``MergeAuthor`` / ``resolveAuthor`` surface —
# the authorship tag is host-supplied and opaque; the merge decodes nothing from it.


@dataclass(frozen=True)
class Primary:
    """The precedence-holding branch (the human) — wins conflicted cells."""


@dataclass(frozen=True)
class Secondary:
    """A non-precedence branch (an agent), with an opaque host tag."""

    tag: str | None = None


type MergeAuthor = Primary | Secondary


# Conflict classes + resolution choices (parity with the F# ``MergeConflictClass`` /
# ``MergeChoice`` vocabularies — the typed report shape the DAG surfaces).
CONCURRENT_EDIT = "ConcurrentEdit"
CONCURRENT_MOVE = "ConcurrentMove"
DELETE_MODIFY = "DeleteModify"
KIND_SWAP_ORPHANS_PIN = "KindSwapOrphansPin"
REORDER_VS_STRUCTURAL = "ReorderVsStructural"
COMBINED_CYCLE = "CombinedCycle"

KEEP_PRIMARY = "KeepPrimary"
KEEP_SECONDARY = "KeepSecondary"
KEEP_BASE = "KeepBase"
REASSERT_PIN_ONTO_NEW_KIND = "ReassertPinOntoNewKind"
KEEP_OLD_KIND = "KeepOldKind"


@dataclass(frozen=True)
class MergeConflict:
    """A conflicting ``(node_id, facet)`` cell.

    ``node_id`` + ``facet`` are the minimal identity (the author-agnostic surface, the
    twin of the TypeScript host's ``{nodeId, facet}``). The remaining fields carry the
    DAG-layer resolution detail (the F# ``Dag.conflicts`` shape): the conflict ``class``,
    the canonical ``base`` / ``primary`` / ``secondary`` cell values (``primary`` set only
    when a pin was held), the opaque ``secondary_tag``, whether human-primacy resolved it
    (``primacy_held``), and the ordered ``choices`` a host may pick from.
    """

    node_id: str
    facet: str
    conflict_class: str = CONCURRENT_EDIT
    base: str | None = None
    primary: str | None = None
    secondary: str | None = None
    secondary_tag: str | None = None
    primacy_held: bool = False
    choices: tuple[str, ...] = ()


@dataclass(frozen=True)
class MergeOk:
    """A usable merged result. ``resolved`` carries conflicts that human-primacy
    auto-resolved (pin held) — empty for a fully clean author-agnostic merge."""

    tree: Node
    resolved: tuple[MergeConflict, ...] = ()

    @property
    def ok(self) -> bool:
        return True


@dataclass(frozen=True)
class MergeConflicts:
    """A merge that surfaced one or more *blocking* (non-pin-held) conflicts (trunk unchanged)."""

    conflicts: tuple[MergeConflict, ...]

    @property
    def ok(self) -> bool:
        return False


type MergeResult = MergeOk | MergeConflicts


@dataclass(frozen=True)
class _Resolution:
    """The per-merge precedence resolution derived once from the two branch authors."""

    a_is_primary: bool
    pin_held: bool
    choices: tuple[str, ...]
    secondary_tag: str | None


def _resolve_author(author_a: MergeAuthor, author_b: MergeAuthor) -> _Resolution:
    """Mirror the F# ``resolveAuthor`` precedence table."""
    if isinstance(author_a, Primary) and isinstance(author_b, Secondary):
        return _Resolution(True, True, (KEEP_PRIMARY, KEEP_SECONDARY, KEEP_BASE), author_b.tag)
    if isinstance(author_a, Secondary) and isinstance(author_b, Primary):
        return _Resolution(False, True, (KEEP_PRIMARY, KEEP_SECONDARY, KEEP_BASE), author_a.tag)
    if isinstance(author_a, Secondary) and isinstance(author_b, Secondary):
        return _Resolution(False, False, (KEEP_BASE, KEEP_SECONDARY), author_a.tag)
    # two primaries — no precedence, host decides
    return _Resolution(False, False, (KEEP_BASE,), None)


_AGNOSTIC = _resolve_author(Secondary(), Secondary())  # the author-agnostic default


# ─── structural helpers ──────────────────────────────────────────────────────


def _non_facet_extras(n: Node) -> dict[str, Value]:
    return {k: v for k, v in n.extras.items() if k not in _FACET_EXTRAS}


def _children_of(n: Node) -> list[Node]:
    """The Layout children (the structural facet); ``[]`` for a childless kind."""
    children = n.kind.fields.get("children")
    if isinstance(children, Arr):
        return [c for c in children.items if isinstance(c, Node)]
    return []


def _childless_kind(kind: Obj) -> Obj:
    """The kind with its children emptied (for the childless-kind probe + rebuild);
    non-layout kinds carry no children and are returned unchanged."""
    if isinstance(kind.fields.get("children"), Arr):
        return Obj(kind.tag, {**kind.fields, "children": Arr([])})
    return kind


def _with_kind_children(kind: Obj, children: list[Node]) -> Obj:
    if isinstance(kind.fields.get("children"), Arr):
        return Obj(kind.tag, {**kind.fields, "children": Arr(list(children))})
    return kind


def _mk_node(src: Node, kind: Obj, style: Value | None, state: Value | None, acc: Value | None) -> Node:
    """Rebuild a node with controlled facets, omitting an absent style/state/
    accessibility (the wire's absent ⟺ default). Non-facet extras (motion,
    extraAttributes) carry over from ``src`` — they are not merge facets."""
    extras = _non_facet_extras(src)
    if style is not None:
        extras["style"] = style
    if state is not None:
        extras["state"] = state
    if acc is not None:
        extras["accessibility"] = acc
    return Node(src.id, kind, extras)


# ─── facet-isolation canonical probes (closure-safe bytes) ───────────────────


def _kind_canonical(n: Node) -> str:
    return encode_value(_mk_node(n, _childless_kind(n.kind), None, None, None))


def _state_canonical(shell: Obj, n: Node) -> str:
    return encode_value(_mk_node(n, shell, None, n.extras.get("state"), None))


def _accessibility_canonical(shell: Obj, n: Node) -> str:
    return encode_value(_mk_node(n, shell, None, None, n.extras.get("accessibility")))


def _style_field(n: Node, name: str) -> str | None:
    style = n.extras.get("style")
    if isinstance(style, Obj):
        v = style.fields.get(name)
        if isinstance(v, str):
            return v
    return _STYLE_DEFAULTS.get(name)


# ─── facet pickers ───────────────────────────────────────────────────────────


def _record_conflict(
    conflicts: list[MergeConflict],
    res: _Resolution,
    node_id: str,
    facet: str,
    base_v: str | None,
    a_v: str | None,
    b_v: str | None,
) -> int:
    """Record a conflicted cell (resolved by primacy or surfaced) and return the pick
    index: ``0`` = base, ``1`` = a, ``2`` = b."""
    primary_v = (a_v if res.a_is_primary else b_v) if res.pin_held else None
    secondary_v = (b_v if res.a_is_primary else a_v) if res.pin_held else a_v
    conflict_class = REORDER_VS_STRUCTURAL if facet == "children" else CONCURRENT_EDIT
    conflicts.append(
        MergeConflict(
            node_id=node_id,
            facet=facet,
            conflict_class=conflict_class,
            base=base_v,
            primary=primary_v,
            secondary=secondary_v,
            secondary_tag=res.secondary_tag,
            primacy_held=res.pin_held,
            choices=res.choices,
        )
    )
    if res.pin_held:
        return 1 if res.a_is_primary else 2
    return 0


def _pick_field(
    conflicts: list[MergeConflict],
    res: _Resolution,
    node_id: str,
    facet: str,
    base_v: str | None,
    a_v: str | None,
    b_v: str | None,
) -> str | None:
    a_ch = a_v != base_v
    b_ch = b_v != base_v
    if a_ch and b_ch and a_v != b_v:
        pick = _record_conflict(conflicts, res, node_id, facet, base_v, a_v, b_v)
        return (base_v, a_v, b_v)[pick]
    if a_ch:
        return a_v
    if b_ch:
        return b_v
    return base_v


def _pick_canonical(
    conflicts: list[MergeConflict],
    res: _Resolution,
    node_id: str,
    facet: str,
    base_c: str,
    a_c: str,
    b_c: str,
) -> int:
    """Returns 0 = base, 1 = a, 2 = b."""
    a_ch = a_c != base_c
    b_ch = b_c != base_c
    if a_ch and b_ch and a_c != b_c:
        return _record_conflict(conflicts, res, node_id, facet, base_c, a_c, b_c)
    if a_ch:
        return 1
    if b_ch:
        return 2
    return 0


def _merge_style(
    conflicts: list[MergeConflict], res: _Resolution, node_id: str, base: Node, a: Node, b: Node
) -> Obj | None:
    tone = _pick_field(
        conflicts,
        res,
        node_id,
        "style.tone",
        _style_field(base, "tone"),
        _style_field(a, "tone"),
        _style_field(b, "tone"),
    )
    weight = _pick_field(
        conflicts,
        res,
        node_id,
        "style.weight",
        _style_field(base, "weight"),
        _style_field(a, "weight"),
        _style_field(b, "weight"),
    )
    emphasis = _pick_field(
        conflicts,
        res,
        node_id,
        "style.emphasis",
        _style_field(base, "emphasis"),
        _style_field(a, "emphasis"),
        _style_field(b, "emphasis"),
    )
    role = _pick_field(
        conflicts,
        res,
        node_id,
        "style.role",
        _style_field(base, "role"),
        _style_field(a, "role"),
        _style_field(b, "role"),
    )
    voice = _pick_field(
        conflicts,
        res,
        node_id,
        "style.voice",
        _style_field(base, "voice"),
        _style_field(a, "voice"),
        _style_field(b, "voice"),
    )
    # Absent ⟺ all-default: omit the whole style facet so it encodes byte-identically.
    if tone == "Default" and weight == "Standard" and emphasis == "Normal" and role is None and voice is None:
        return None
    # Phase 460 — each sub-field is omitted-when-default (WIRE_FORMAT §3.6), matching
    # the decoder/encoder so the blended style re-encodes to its byte-minimal form.
    fields: dict[str, Value] = {}
    if emphasis != "Normal":
        fields["emphasis"] = emphasis
    if tone != "Default":
        fields["tone"] = tone
    if weight != "Standard":
        fields["weight"] = weight
    if role is not None:
        fields["role"] = role
    if voice is not None:
        fields["voice"] = voice
    return Obj(None, fields)


def _is_pure_addition(base_ids: list[str], head_ids: list[str]) -> bool:
    """``True`` when ``head`` is ``base`` with zero removals and zero reorders."""
    head_set = set(head_ids)
    base_set = set(base_ids)
    survive = [i for i in base_ids if i in head_set]
    head_kept = [i for i in head_ids if i in base_set]
    return survive == base_ids and head_kept == base_ids


def _merge3(
    conflicts: list[MergeConflict], res: _Resolution, base: Node, a_opt: Node | None, b_opt: Node | None
) -> Node:
    a = a_opt if a_opt is not None else base
    b = b_opt if b_opt is not None else base
    node_id = base.id
    shell = _childless_kind(base.kind)

    # kind facet
    kind_pick = _pick_canonical(
        conflicts, res, node_id, "kind", _kind_canonical(base), _kind_canonical(a), _kind_canonical(b)
    )
    kind_source = a if kind_pick == 1 else b if kind_pick == 2 else base

    # style sub-fields (independent)
    merged_style = _merge_style(conflicts, res, node_id, base, a, b)

    # state facet
    state_pick = _pick_canonical(
        conflicts,
        res,
        node_id,
        "state",
        _state_canonical(shell, base),
        _state_canonical(shell, a),
        _state_canonical(shell, b),
    )
    merged_state = (a if state_pick == 1 else b if state_pick == 2 else base).extras.get("state")

    # accessibility facet
    acc_pick = _pick_canonical(
        conflicts,
        res,
        node_id,
        "accessibility",
        _accessibility_canonical(shell, base),
        _accessibility_canonical(shell, a),
        _accessibility_canonical(shell, b),
    )
    merged_acc = (a if acc_pick == 1 else b if acc_pick == 2 else base).extras.get("accessibility")

    # children facet (structural)
    base_kids = _children_of(base)
    a_kids = _children_of(a)
    b_kids = _children_of(b)
    base_ids = [c.id for c in base_kids]
    a_ids = [c.id for c in a_kids]
    b_ids = [c.id for c in b_kids]
    a_struct = a_ids != base_ids
    b_struct = b_ids != base_ids
    base_map = {c.id: c for c in base_kids}
    a_map = {c.id: c for c in a_kids}
    b_map = {c.id: c for c in b_kids}

    def recurse_child(cid: str) -> Node:
        bc = base_map.get(cid)
        if bc is not None:
            return _merge3(conflicts, res, bc, a_map.get(cid), b_map.get(cid))
        ac = a_map.get(cid)
        if ac is not None:
            return ac
        bb = b_map.get(cid)
        if bb is not None:
            return bb
        raise RuntimeError(f"merge3: child id {cid} vanished")

    merged_children: list[Node]
    if not a_struct and not b_struct:
        merged_children = [recurse_child(i) for i in base_ids]
    elif a_struct and not b_struct:
        merged_children = [recurse_child(i) for i in a_ids]
    elif not a_struct and b_struct:
        merged_children = [recurse_child(i) for i in b_ids]
    else:
        base_set = set(base_ids)
        a_new = [i for i in a_ids if i not in base_set]
        b_new = [i for i in b_ids if i not in base_set]
        a_new_set = set(a_new)
        disjoint = (
            _is_pure_addition(base_ids, a_ids)
            and _is_pure_addition(base_ids, b_ids)
            and not any(i in a_new_set for i in b_new)
        )
        if disjoint:
            survivors = [recurse_child(i) for i in base_ids]
            new_ids = sorted(set(a_new) | set(b_new))  # Ordinal (code-point) tie-break
            merged_children = survivors + [recurse_child(i) for i in new_ids]
        else:
            # Structural conflict: on a held pin take the primary side's child order,
            # otherwise keep base (the author-agnostic surface).
            pick = _record_conflict(
                conflicts, res, node_id, "children", ",".join(base_ids), ",".join(a_ids), ",".join(b_ids)
            )
            chosen_ids = base_ids if pick == 0 else a_ids if pick == 1 else b_ids
            merged_children = [recurse_child(i) for i in chosen_ids]

    merged_kind = _with_kind_children(_childless_kind(kind_source.kind), merged_children)
    return _mk_node(base, merged_kind, merged_style, merged_state, merged_acc)


def merge_3way(base: Node, a: Node, b: Node) -> MergeResult:
    """Author-agnostic facet 3-way merge of ``a`` and ``b`` over their common
    ``base`` (all three share the root id). Returns :class:`MergeOk` with the
    merged tree on full auto-merge, or :class:`MergeConflicts` with the
    conflicting cells. Deterministic + host-reproducible (NodeId-byte tie-break,
    no wall-clock) — byte-identical to the F# and TypeScript hosts.
    """
    conflicts: list[MergeConflict] = []
    merged = _merge3(conflicts, _AGNOSTIC, base, a, b)
    return MergeOk(merged) if not conflicts else MergeConflicts(tuple(conflicts))


def merge3_way_with_author(author_a: MergeAuthor, author_b: MergeAuthor, base: Node, a: Node, b: Node) -> MergeResult:
    """The human-primacy 3-way merge — the DAG-layer reconciler (F#
    ``merge3WayWithAuthor``). A conflicted cell where one branch is :class:`Primary`
    (the human) and the other :class:`Secondary` (an agent) is **resolved** in the
    primary's favour and recorded with ``primacy_held=True`` (returned in
    :attr:`MergeOk.resolved`, not blocking). A conflict with no precedence (two
    secondaries, or two primaries) keeps ``base`` and **blocks** as
    :class:`MergeConflicts`. Deterministic (NodeId-byte tie-break, no wall-clock).
    """
    res = _resolve_author(author_a, author_b)
    conflicts: list[MergeConflict] = []
    merged = _merge3(conflicts, res, base, a, b)
    blocking = tuple(c for c in conflicts if not c.primacy_held)
    if blocking:
        return MergeConflicts(blocking)
    return MergeOk(merged, tuple(conflicts))


def merge3_way_lenient(base: Node, a: Node, b: Node) -> Node:
    """The never-blocking merge (F# ``merge3WayLenient``): conflicts resolve to
    ``base`` and the merged tree is always returned. Used to build a synthetic
    ancestor when reconciling across an ambiguous common base."""
    conflicts: list[MergeConflict] = []
    return _merge3(conflicts, _AGNOSTIC, base, a, b)


def merge3(base: Node, a: Node, b: Node, *, human: str | None = None) -> MergeResult:
    """The 3-way merge entry point. ``human=None`` (default) is the author-agnostic
    merge (:func:`merge_3way`); ``human="a"`` / ``human="b"`` designates that branch as
    the precedence-holding human and runs the human-primacy merge
    (:func:`merge3_way_with_author`)."""
    if human is None:
        return merge_3way(base, a, b)
    if human not in ("a", "b"):
        raise ValueError("human must be 'a', 'b', or None")
    author_a: MergeAuthor = Primary() if human == "a" else Secondary()
    author_b: MergeAuthor = Primary() if human == "b" else Secondary()
    return merge3_way_with_author(author_a, author_b, base, a, b)
