"""The ``Binding.State`` SEEDING pass — ``WIRE_FORMAT.md`` §24.4.

§24.1 says what a declared default resolves to *for the reader that carries it*.
§24.4 says what it means for every OTHER reader of the same slot: a
``Binding.State`` carrying a ``defaultValue`` DECLARES the value of its slot, so
a grid bound to ``$state.members`` and carrying the rows, beside a badge whose
``Transform`` derives over the same key and carries nothing, read the same rows.

It is a RENDER-parity obligation, not a codec one (§24.6): the bytes round-trip
identically with or without the rule, which is exactly why no codec family
catches a host that has not adopted it. A seed is *authored data* that travels
in the document — not store state — so resolving it costs this headless host no
session state, on the same reasoning that admitted declared-default resolution
in :func:`fuaran_py.renderer.bindings.resolve_binding`.

The five rules, ported from the SPECIFICATION rather than from either reference
implementation, each answering a question two readers of one key raise that one
does not:

1. **Who declares** — any ``Binding.State`` with a PRESENT ``defaultValue``, in
   any slot. There is no separate declaration form and no new namespace.
2. **Precedence: host value > written value > seed.** A seed is the value of a
   slot before anything else has said anything, never an override. This host
   holds no written values, so it lays the seeds UNDER the caller's own
   ``sources`` and the caller wins every key it names.
3. **Order-independence** — seeding happens over the WHOLE tree before any
   binding resolves, so a badge that appears before the grid declaring the rows
   is not a special case and document order carries no meaning.
4. **Two declarations of one key** — a disagreement is ``FUARAN106`` (a
   validator concern, not this module's), but a renderer must still be
   deterministic and takes the FIRST declaration in tree order. An EMPTY
   declaration declares nothing: it is the value an unseeded slot already has.
5. **A host-reserved key is never seeded** — a seed is a tree-originated write,
   and §12's reserved ``host.`` namespace refuses those on every path.

The walk is STRUCTURAL, over the decoded value graph, rather than a typed
per-slot walk: it finds a ``State`` binding in any slot, including one a later
``Spec`` case adds, so it carries no forward-coupling duty that a new
binding-bearing field could silently break.
"""

from __future__ import annotations

from ..model import Arr, Node, Obj, Value

#: The HOST-OWNED state namespace (§12). A tree-originated write naming one of
#: these keys is refused, so a tree-originated SEED naming one must be too.
HOST_RESERVED_STATE_PREFIX = "host."

type BindingSeeds = dict[str, object]


def collect_state_seeds(node: Node) -> BindingSeeds:
    """The value each ``$state.<key>`` slot carries before anything else has
    said anything — rule 1 filtered by rules 4 and 5.

    Empty when the tree declares nothing.
    """
    seeds: BindingSeeds = {}
    _walk(node, seeds)
    return seeds


def with_state_seeds(node: Node, sources: BindingSeeds | None) -> BindingSeeds | None:
    """Lay a tree's seeds UNDER a caller's own binding sources (rule 2: the
    caller wins every key it names).

    The caller's mapping is never mutated — a host may reuse one across renders,
    and a pass that wrote into it would leak the first tree's declarations into
    the second tree's render. Returns the caller's own object unchanged when the
    tree declares nothing, so an unseeded tree costs one walk and no allocation.
    """
    seeds = collect_state_seeds(node)
    if not seeds:
        return sources
    merged: BindingSeeds = dict(seeds)
    if sources:
        merged.update(sources)
    return merged


def _walk(value: Value, seeds: BindingSeeds) -> None:
    """Descend one decoded value, recording the first declaration of each key.

    Object members are visited in SORTED KEY ORDER — the canonical document's
    own member order, which the encoder reproduces — so "first in tree order" is
    a property of the BYTES rather than of this host's dict construction order.
    Array order is the wire's own and is honoured as it stands.
    """
    if isinstance(value, Node):
        _walk(value.kind, seeds)
        _walk_fields(value.extras, seeds)
    elif isinstance(value, Arr):
        for item in value.items:
            _walk(item, seeds)
    elif isinstance(value, Obj):
        if value.tag == "State":
            _record(value, seeds)
        # Keep descending whatever the tag: a ``Local`` re-sync source, an
        # ``I18n`` argument, or a ``Transform`` param's ``from`` can nest
        # another binding underneath this one.
        _walk_fields(value.fields, seeds)


def _walk_fields(fields: dict[str, Value], seeds: BindingSeeds) -> None:
    for name in sorted(fields):
        _walk(fields[name], seeds)


def _record(obj: Obj, seeds: BindingSeeds) -> None:
    """Apply rules 1, 4 and 5 to one ``Binding.State`` object."""
    key = obj.fields.get("key")
    if not isinstance(key, str):
        return
    if "defaultValue" not in obj.fields:
        return  # rule 1 — a reader that declares nothing declares nothing.
    if key.startswith(HOST_RESERVED_STATE_PREFIX):
        return  # rule 5.
    declared = obj.fields["defaultValue"]
    if _is_empty_declaration(declared):
        return  # rule 4 — an empty declaration declares nothing.
    if key in seeds:
        return  # rule 4 — the FIRST declaration in tree order wins.
    seeds[key] = declared


def _is_empty_declaration(value: Value) -> bool:
    """The EMPTY table, which is what a seed must not be.

    ``"defaultValue": []`` is the identity of the seeding lattice, not a claim
    about content: an unseeded slot already resolves to the empty table, so an
    empty declaration adds nothing an absent one does not already say. Both
    consequences are load-bearing rather than tidy. It must not WIN the
    first-declaration race — ``{"$type":"State","key":k,"defaultValue":[]}`` is
    how a ``Transform`` source slot says "I read this key and carry no data of
    my own", so a badge spelling it before the grid that carries the rows would
    otherwise seed the slot EMPTY and make rule 3 false. And it must not
    CONFLICT, or that same pair would raise ``FUARAN106`` against the grid
    beside it — an Error on the very document the seeding rule exists to make
    work.
    """
    if isinstance(value, Arr):
        return not value.items
    if isinstance(value, Obj):
        # The canonical columnar spelling of the same nothing.
        columns = value.fields.get("columns")
        if isinstance(columns, Obj):
            return not columns.fields
    return False
