"""Schema-level operations over :class:`~fuaran_ui.dataframe.model.Field` lists (Phase 2207).

The twin of the reference's schema operations, reading a field's metadata exactly as it does
(WIRE_FORMAT §3.6.26): a structural :func:`diff` and its replay :func:`patch`, the delta's
canonical wire (:func:`encode_delta`), a depended-on-column compatibility verdict
(:func:`classify`), and the cross-host content :func:`fingerprint`. The ``laws/fieldLaws`` corpus
family pins each one.

The law: ``patch(old, diff(old, target)) == Ok(target)`` for every pair of schemas that each name
no column twice, and ``diff(a, a)`` is :data:`IDENTITY_DELTA`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

from ...canonical import encode_value, utf16_order_key
from ...model import Arr, Obj, Value
from .codec import field_value
from .model import (
    INT,
    NO_METADATA,
    TIMESTAMP_UNIT_DIGITS,
    Err,
    Field,
    FieldMeta,
    Ok,
    Result,
)
from .unit import UnitOfMeasure, render_unit

__all__ = [
    "BREAKING",
    "COMPATIBLE",
    "IDENTITY_DELTA",
    "UNKNOWN",
    "FieldChange",
    "SchemaCompat",
    "SchemaDelta",
    "SchemaError",
    "canonical_fields",
    "classify",
    "diff",
    "encode_delta",
    "fingerprint",
    "fnv1a",
    "patch",
    "widens",
]

# ── The text kernel: FNV-1a and the injective field pre-image ────────────────

_FOLD_SEP: Final = "\u0001"
_FIELD_ESC: Final = "\u0010"


def fnv1a(s: str) -> str:
    """32-bit FNV-1a, as eight lower-case hex digits, folded over the string's UTF-16 CODE UNITS —
    not its UTF-8 bytes and not its code points: an astral character is two steps (its high
    surrogate, then its low one), exactly as the reference folds a .NET ``char``."""
    data = s.encode("utf-16-le", errors="surrogatepass")
    h = 2166136261
    for i in range(0, len(data), 2):
        h ^= data[i] | (data[i + 1] << 8)
        h = (h * 16777619) & 0xFFFFFFFF
    return f"{h:08x}"


def canonical_fields(fields: list[str]) -> str:
    """The injective pre-image of a field sequence: each field with every U+0010 and U+0001 it
    carries escaped by a preceding U+0010, then terminated by U+0001, concatenated."""
    return "".join(
        f.replace(_FIELD_ESC, _FIELD_ESC + _FIELD_ESC).replace(_FOLD_SEP, _FIELD_ESC + _FOLD_SEP) + _FOLD_SEP
        for f in fields
    )


# ── The delta ────────────────────────────────────────────────────────────────

type Member = Literal["unit", "label", "description", "ext"]
type MemberValue = UnitOfMeasure | str | None


@dataclass(frozen=True)
class FieldChange:
    """One moved metadata member of a column common to both schemas: the member, its value
    ``before`` and ``after`` (``None`` where the field stated none; a unit as a
    :class:`UnitOfMeasure`), and an ``ext`` change's ``key``."""

    member: Member
    before: MemberValue
    after: MemberValue
    key: str | None = None


@dataclass(frozen=True)
class SchemaDelta:
    """What changed from an old schema to a target one: columns ``added`` (target order) and
    ``removed`` (old order) with their metadata, ``retyped`` ``(name, from, to)`` and ``amended``
    ``(name, change)`` in old order, whether the common columns were ``reordered``, and ``order`` —
    the target's column order, empty when it is the order :func:`patch` derives without it."""

    added: list[Field] = field(default_factory=list)
    removed: list[Field] = field(default_factory=list)
    retyped: list[tuple[str, str, str]] = field(default_factory=list)
    amended: list[tuple[str, FieldChange]] = field(default_factory=list)
    reordered: bool = False
    order: list[str] = field(default_factory=list)


#: The delta that changes nothing — what ``diff(a, a)`` returns.
IDENTITY_DELTA: Final = SchemaDelta()


@dataclass(frozen=True)
class SchemaError:
    """Why :func:`patch` refused a delta — the reference's ``SchemaError`` case as ``code``
    (``DuplicateColumn``, ``AbsentColumn``, ``TypeDisagrees``, ``MetadataDisagrees``,
    ``AlreadyPresent``, ``OrderMismatch``) and a ``detail``."""

    code: str
    detail: str


def _changes(before: FieldMeta, after: FieldMeta) -> list[FieldChange]:
    """The metadata changes between two fields of one name, in member order: the unit, the label,
    the description, then each extension key the two carry between them, ordinally."""
    out: list[FieldChange] = []
    if before.unit != after.unit:
        out.append(FieldChange("unit", before.unit, after.unit))
    if before.label != after.label:
        out.append(FieldChange("label", before.label, after.label))
    if before.description != after.description:
        out.append(FieldChange("description", before.description, after.description))
    for k in sorted(set(before.ext) | set(after.ext), key=utf16_order_key):
        b, a = before.ext.get(k), after.ext.get(k)
        if b != a:
            out.append(FieldChange("ext", b, a, k))
    return out


def diff(old: list[Field], target: list[Field]) -> SchemaDelta:
    """The structured delta from ``old`` to ``target`` (total)."""
    old_map = {f.name: f for f in old}
    new_map = {f.name: f for f in target}
    added = [f for f in target if f.name not in old_map]
    removed = [f for f in old if f.name not in new_map]
    retyped = [
        (f.name, f.type, new_map[f.name].type) for f in old if f.name in new_map and new_map[f.name].type != f.type
    ]
    amended = [(f.name, c) for f in old if f.name in new_map for c in _changes(f.meta, new_map[f.name].meta)]
    common_old = [f.name for f in old if f.name in new_map]
    common_new = [f.name for f in target if f.name in old_map]
    derived = common_old + [f.name for f in added]
    target_order = [f.name for f in target]
    return SchemaDelta(
        added,
        removed,
        retyped,
        amended,
        common_old != common_new,
        [] if target_order == derived else target_order,
    )


def _member(meta: FieldMeta, change: FieldChange) -> MemberValue:
    if change.member == "unit":
        return meta.unit
    if change.member == "label":
        return meta.label
    if change.member == "description":
        return meta.description
    return meta.ext.get(change.key or "")


def _apply_change(change: FieldChange, f: Field) -> Field | None:
    """The field ``change`` leaves when applied to ``f``, or ``None`` when ``f`` does not carry the
    ``before`` the change records."""
    m = f.meta
    if _member(m, change) != change.before:
        return None
    after = change.after
    if change.member == "unit":
        meta = FieldMeta(after if isinstance(after, UnitOfMeasure) else None, m.label, m.description, m.ext)
    elif change.member == "label":
        meta = FieldMeta(m.unit, after if isinstance(after, str) else None, m.description, m.ext)
    elif change.member == "description":
        meta = FieldMeta(m.unit, m.label, after if isinstance(after, str) else None, m.ext)
    else:
        ext = {k: v for k, v in m.ext.items() if k != change.key}
        if isinstance(after, str):
            ext[change.key or ""] = after
        meta = FieldMeta(m.unit, m.label, m.description, ext)
    return Field(f.name, f.type, NO_METADATA if meta.is_empty else meta)


def patch(old: list[Field], delta: SchemaDelta) -> Result[list[Field], SchemaError]:  # noqa: C901
    """Apply a delta: remove, retype, amend, add, then reorder — each step checking the delta was
    computed against this schema, and refusing with a named :class:`SchemaError` otherwise."""
    names = [f.name for f in old]
    dup = next((n for i, n in enumerate(names) if n in names[:i]), None)
    if dup is not None:
        return Err(SchemaError("DuplicateColumn", dup))
    s = list(old)

    def find(name: str) -> int | None:
        return next((i for i, f in enumerate(s) if f.name == name), None)

    for r in delta.removed:
        i = find(r.name)
        if i is None:
            return Err(SchemaError("AbsentColumn", r.name))
        if s[i].type != r.type:
            return Err(SchemaError("TypeDisagrees", f"{r.name}: expected {r.type}, got {s[i].type}"))
        del s[i]
    for name, from_ty, to_ty in delta.retyped:
        i = find(name)
        if i is None:
            return Err(SchemaError("AbsentColumn", name))
        if s[i].type != from_ty:
            return Err(SchemaError("TypeDisagrees", f"{name}: expected {from_ty}, got {s[i].type}"))
        s[i] = Field(name, to_ty, s[i].meta)
    for name, change in delta.amended:
        i = find(name)
        if i is None:
            return Err(SchemaError("AbsentColumn", name))
        applied = _apply_change(change, s[i])
        if applied is None:
            return Err(SchemaError("MetadataDisagrees", f"{name}: {change.member}"))
        s[i] = applied
    for a in delta.added:
        if find(a.name) is not None:
            return Err(SchemaError("AlreadyPresent", a.name))
        s.append(a)
    if delta.order:
        have = [f.name for f in s]
        if sorted(delta.order, key=utf16_order_key) != sorted(have, key=utf16_order_key):
            return Err(SchemaError("OrderMismatch", f"expected a permutation of {have}, got {delta.order}"))
        by_name = {f.name: f for f in s}
        s = [by_name[n] for n in delta.order]
    return Ok(s)


# ── The delta's canonical wire ───────────────────────────────────────────────


def _member_text(v: MemberValue) -> str:
    return render_unit(v) if isinstance(v, UnitOfMeasure) else str(v)


def _amended_value(name: str, c: FieldChange) -> Obj:
    members: dict[str, Value] = {"name": name, "member": c.member}
    if c.member == "ext":
        members["key"] = c.key
    if c.before is not None:
        members["from"] = _member_text(c.before)
    if c.after is not None:
        members["to"] = _member_text(c.after)
    return Obj(None, members)


def encode_delta(d: SchemaDelta) -> str:
    """The delta's canonical wire: ``added``, ``removed`` (as schema entries), ``retyped``
    (``{name, from, to}``), ``reordered`` and ``order`` always, and ``amended`` only when a member
    moved — so a delta between schemas without metadata keeps the bytes it always had."""
    members: dict[str, Value] = {
        "added": Arr([field_value(f) for f in d.added]),
        "removed": Arr([field_value(f) for f in d.removed]),
        "retyped": Arr([Obj(None, {"name": n, "from": a, "to": b}) for n, a, b in d.retyped]),
        "reordered": d.reordered,
        "order": Arr(list(d.order)),
    }
    if d.amended:
        members["amended"] = Arr([_amended_value(n, c) for n, c in d.amended])
    return encode_value(Obj(None, members))


# ── Compatibility ────────────────────────────────────────────────────────────

COMPATIBLE: Final = "compatible"
BREAKING: Final = "breaking"
UNKNOWN: Final = "unknown"


@dataclass(frozen=True)
class SchemaCompat:
    """A compatibility ``verdict`` — :data:`COMPATIBLE`, :data:`BREAKING` or :data:`UNKNOWN` — and
    the ``reasons`` a non-compatible one names."""

    verdict: str
    reasons: list[str] = field(default_factory=list)


def widens(source: str, target: str) -> bool:
    """Whether a retype from ``source`` to ``target`` is a safe widening: ``int`` into ``float`` or
    ``decimal``, a timestamp into an equal-or-finer unit, otherwise the same type."""
    if source == INT and target in ("float", "decimal"):
        return True
    if source in TIMESTAMP_UNIT_DIGITS and target in TIMESTAMP_UNIT_DIGITS:
        return TIMESTAMP_UNIT_DIGITS[target] >= TIMESTAMP_UNIT_DIGITS[source]
    return source == target


def classify(depends_on: list[str], delta: SchemaDelta) -> SchemaCompat:
    """Classify a delta against the columns a consumer depends on: a removed depended-on column, a
    retype that is not a widening, or a stated unit replaced or withdrawn is breaking; a moved
    extension member is unknown; anything else is compatible. Breaking outranks unknown."""
    deps = set(depends_on)
    breaking = [f"depended-on column removed: {f.name} ({f.type})" for f in delta.removed if f.name in deps]
    breaking += [
        f"depended-on column {n} retyped {a} → {b} (not a safe widening)"
        for n, a, b in delta.retyped
        if n in deps and not widens(a, b)
    ]
    breaking += [
        f"depended-on column {n} unit changed {_member_text(c.before)} → "
        f"{'none' if c.after is None else _member_text(c.after)} (its values no longer mean what was read)"
        for n, c in delta.amended
        if c.member == "unit" and c.before is not None and n in deps
    ]
    unknown = [
        f"depended-on column {n} extension member {c.key} changed (not interpreted by the kit)"
        for n, c in delta.amended
        if c.member == "ext" and n in deps
    ]
    if breaking:
        return SchemaCompat(BREAKING, breaking)
    if unknown:
        return SchemaCompat(UNKNOWN, unknown)
    return SchemaCompat(COMPATIBLE)


# ── The fingerprint ──────────────────────────────────────────────────────────


def _metadata_preimage(m: FieldMeta) -> str:
    """The pre-image element a field's metadata contributes: the canonical unit text prefixed
    ``u``, the label ``l``, the description ``d`` (each only when stated), then each extension key
    and its value in ordinal key order — all through :func:`canonical_fields`."""
    parts = [
        "" if m.unit is None else "u" + render_unit(m.unit),
        "" if m.label is None else "l" + m.label,
        "" if m.description is None else "d" + m.description,
    ]
    for k in sorted(m.ext, key=utf16_order_key):
        parts += [k, m.ext[k]]
    return canonical_fields(parts)


def fingerprint(schema: list[Field]) -> str:
    """The schema's stable cross-host content fingerprint: FNV-1a over the canonical field
    pre-image of its ``name:type`` list, each field that states metadata contributing a second
    element after its own — so a schema without metadata keeps the fingerprint it always had."""
    parts: list[str] = []
    for f in schema:
        parts.append(f"{f.name}:{f.type}")
        if not f.meta.is_empty:
            parts.append(_metadata_preimage(f.meta))
    return fnv1a(canonical_fields(parts))
