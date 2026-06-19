"""Structural typed model for the Fuaran UI wire format.

A decoded wire document is represented as a small typed tree:

* ``Node``       — a UI node envelope (``id`` + ``kind`` [+ optional ``state`` /
                   ``style`` / ``accessibility``]).
* ``Obj``        — a discriminated ``$type`` object (a DU case such as
                   ``Static`` / ``Literal`` / ``Metric``) or a plain record
                   object (``tag is None``).
* ``Arr``        — an ordered array.
* scalars        — ``str`` / ``int`` / ``float`` / ``bool`` / ``None`` are kept
                   as native Python values so the ``int`` vs ``float`` wire
                   distinction survives a round-trip.

This v0 model is deliberately generic — per-kind dataclasses (a richer
``NodeKind`` union, typed specs) are a follow-up enrichment. What matters for a
conformant host is that the model preserves exactly the structure the canonical
encoder needs to reproduce byte-identical output (see :mod:`fuaran_py.canonical`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

type Value = str | int | float | bool | None | Node | Obj | Arr


@dataclass(frozen=True)
class Obj:
    """A ``$type``-discriminated object (``tag`` set) or a plain record (``tag is None``)."""

    tag: str | None
    fields: dict[str, Value]


@dataclass(frozen=True)
class Arr:
    """An ordered array of wire values."""

    items: list[Value]


@dataclass(frozen=True)
class Node:
    """A UI node envelope: ``id`` + ``kind`` plus any validated optional sections."""

    id: str
    kind: Obj
    extras: dict[str, Value] = field(default_factory=dict)


def from_json(value: object) -> Value:
    """Convert an already-parsed JSON value into the structural model.

    Used for wire positions whose content the codec does not decompose (opaque
    ``JsonValue`` payloads, structural pass-through of not-yet-typed cases). The
    ``int`` / ``float`` distinction that ``json.loads`` produces is preserved.
    """
    if isinstance(value, bool):
        return value
    if value is None or isinstance(value, (int, float, str)):
        return value
    if isinstance(value, list):
        return Arr([from_json(item) for item in value])
    if isinstance(value, dict):
        tag = value.get("$type")
        if isinstance(tag, str):
            return Obj(tag, {k: from_json(v) for k, v in value.items() if k != "$type"})
        return Obj(None, {k: from_json(v) for k, v in value.items()})
    raise TypeError(f"value is not a JSON-shaped object: {type(value)!r}")
