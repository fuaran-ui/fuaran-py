""":class:`ThemeManifest` → canonical JSON — the inverse of :mod:`.decode`.

Port of the Rust ``fuaran_rs::theme::manifest::{to_json, encode}`` (Phase 1725),
the portable byte ORACLE every manifest host encodes to; the TypeScript and Go
encoders are ports of the same function. Read a disagreement with them as a
wire-format question for every host at once, never as a fixture refresh.

Canonical in the codec's one sense — :func:`fuaran_ui.canonical.encode_value`:
object keys sorted in UTF-16 code-unit order at every depth, no whitespace, the
WIRE_FORMAT.md §2 rule-5 number layout and rule-6 escapes. This module carries no
canonical renderer of its own; a second one in this package would be drift by
construction.

Every member the decoder tolerates the absence of is OMITTED at its default, so
``encode(decode(bytes)) == bytes`` for canonical bytes already in this shape, and
an invariant kind this host does not know (:class:`UnknownInvariant`) is
re-emitted with its members as parsed (Phase 2133).
"""

from __future__ import annotations

from ..canonical import encode_value
from ..model import Arr, Obj, Value, from_json
from .manifest import (
    DEFAULT_WEIGHT,
    ContrastFloor,
    Invariant,
    ManifestMeta,
    ManifestRole,
    ManifestToken,
    MotionVoice,
    NamedRole,
    RoleBinding,
    ThemeManifest,
    UnknownInvariant,
    UsageBudget,
    invariant_kind_name,
)


def _obj(fields: dict[str, Value]) -> Obj:
    return Obj(None, fields)


def _put_str(fields: dict[str, Value], key: str, value: str) -> None:
    """Set a string member only when it is non-empty — the decoder reads an absent one as ``""``."""
    if value != "":
        fields[key] = value


def _put_num(fields: dict[str, Value], key: str, value: float, fallback: float) -> None:
    """Set a numeric member only when it differs from what the decoder supplies in its absence."""
    if value != fallback:
        fields[key] = value


# ── Tokens — the flat dotted names back into a DTCG group tree ────────────────


def _token_leaf(t: ManifestToken) -> Obj:
    """One DTCG token node. ``$value`` is the leaf discriminator the decoder stops on,
    so it is emitted unconditionally — an empty value included."""
    fields: dict[str, Value] = {}
    _put_str(fields, "$type", t.type)
    fields["$value"] = t.value
    if t.description is not None:
        fields["$description"] = t.description
    if t.role is not None:
        fields["$extensions"] = _obj({"fuaran": _obj({"role": t.role})})
    return _obj(fields)


# A node of the tree under construction: a placed token LEAF (an ``Obj``) or a GROUP
# (a ``dict``) whose children are still open.
type _Tree = dict[str, "Obj | _Tree"]


def _insert_at(tree: _Tree, segments: list[str], leaf: Obj) -> None:
    """Place one token at its dotted path, replacing whatever occupies that path.

    A DTCG path addresses a group OR a token, never both, so two tokens whose names
    collide (equal, or one a strict prefix of the other) are not jointly
    representable: the later write wins, which is why descending past a leaf
    CLEARS it — as on every other host.
    """
    head, rest = segments[0], segments[1:]
    if not rest:
        tree[head] = leaf
        return
    existing = tree.get(head)
    group: _Tree
    if isinstance(existing, dict):
        group = existing
    else:
        group = {}
        tree[head] = group
    _insert_at(group, rest, leaf)


def _tree_to_obj(tree: _Tree) -> Obj:
    return _obj({k: (n if isinstance(n, Obj) else _tree_to_obj(n)) for k, n in tree.items()})


def _tokens_tree(tokens: list[ManifestToken]) -> Obj:
    root: _Tree = {}
    for t in tokens:
        _insert_at(root, t.name.split("."), _token_leaf(t))
    return _tree_to_obj(root)


# ── Roles + invariants ────────────────────────────────────────────────────────


def _role_json(r: ManifestRole) -> Obj:
    return _obj({"named": r.name}) if isinstance(r, NamedRole) else _obj({"tone": r.tone})


def _role_binding_json(b: RoleBinding) -> Obj:
    # `token` is required — the decoder drops a binding without it. An absent `role`
    # decodes to NamedRole(""), so that one value is omitted.
    fields: dict[str, Value] = {"token": b.token_name}
    if b.role != NamedRole(""):
        fields["role"] = _role_json(b.role)
    return _obj(fields)


def _invariant_json(inv: Invariant) -> Obj:
    # `kind` is the discriminator — an absent one drops the invariant at decode, so it
    # is never omitted.
    fields: dict[str, Value] = {"kind": invariant_kind_name(inv)}
    k = inv.kind
    if isinstance(k, ContrastFloor):
        _put_str(fields, "role", k.role)
        _put_num(fields, "minRatio", k.min_ratio, 0.0)
    elif isinstance(k, UsageBudget):
        _put_str(fields, "token", k.token)
        _put_num(fields, "targetPct", k.target_pct, 0.0)
        _put_num(fields, "tolerancePct", k.tolerance_pct, 0.0)
    elif isinstance(k, MotionVoice):
        _put_num(fields, "maxDurationMs", k.budget.max_duration_ms, 0)
        if k.budget.easing is not None:
            fields["easing"] = k.budget.easing
    elif isinstance(k, UnknownInvariant):
        for name, value in k.members.items():
            fields[name] = from_json(value)
    _put_num(fields, "weight", inv.weight, DEFAULT_WEIGHT)
    return _obj(fields)


def _meta_json(meta: ManifestMeta) -> Obj | None:
    if meta.name == "" and meta.version == "" and meta.description is None:
        return None
    fields: dict[str, Value] = {}
    _put_str(fields, "name", meta.name)
    _put_str(fields, "version", meta.version)
    if meta.description is not None:
        fields["description"] = meta.description
    return _obj(fields)


# ── Top-level ─────────────────────────────────────────────────────────────────


def to_json(manifest: ThemeManifest) -> Obj:
    """The JSON value for a manifest, for a host embedding it in a larger document.

    Always the Fuaran wrapper shape, never a bare DTCG tree: a top-level ``tokens``
    key is what selects the wrapper branch in the decoder, so omitting it at empty
    would decode the document as vanilla DTCG and silently discard meta, roles and
    invariants.
    """
    fields: dict[str, Value] = {}
    meta = _meta_json(manifest.meta)
    if meta is not None:
        fields["meta"] = meta
    fields["tokens"] = _tokens_tree(manifest.tokens)
    if manifest.roles:
        fields["roles"] = Arr([_role_binding_json(b) for b in manifest.roles])
    if manifest.invariants:
        fields["invariants"] = Arr([_invariant_json(i) for i in manifest.invariants])
    return _obj(fields)


def encode(manifest: ThemeManifest) -> str:
    """Encode a manifest as canonical JSON, byte-identical with every other manifest host.

    ``encode(decode(bytes)) == bytes`` for canonical bytes already in this shape, an
    unknown invariant kind included; for any manifest, ``encode`` is a fixpoint
    through the decoder — ``encode(decode(encode(m))) == encode(m)`` — since the
    wire carries tokens in sorted order where a hand-built manifest need not.
    """
    return encode_value(to_json(manifest))
