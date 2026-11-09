"""The theme-manifest validation pass — what the decoded values MEAN (Phase 2133).

The decoder is tolerant and total: the cross-host byte law omits a member at its
default, so ``{"kind":"ContrastFloor","role":"x"}`` is the canonical form of a floor
of 0 and a strict decoder would refuse every other host's output. Whether a decoded
invariant can mean what it says is asked here, after decode, with the shared defect
code table of WIRE_FORMAT.md §13 (the style-observer family's ``manifest-validation``
tier), identical on every manifest host:

- ``CONTRAST_FLOOR_OUT_OF_RANGE`` — a :class:`ContrastFloor`'s ``min_ratio`` lies
  outside [1, 21] (an omitted one is 0); ``subject`` is the role.
- ``CONTRAST_FLOOR_ROLE_EMPTY`` — its ``role`` is empty; ``subject`` is ``""``.
- ``CONTRAST_FLOOR_ROLE_UNBOUND`` — no role binding binds its non-empty role.
- ``USAGE_BUDGET_TOKEN_UNDECLARED`` — a :class:`UsageBudget`'s token names no
  declared token; ``subject`` is the token.
- ``INVARIANT_KIND_UNKNOWN`` — the kind is outside this host's vocabulary: kept,
  and enforced by nothing; ``subject`` is the kind.

The pass never refuses and never repairs; a host decides what a defect costs it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from ..canonical import encode_value
from ..model import Obj
from .manifest import (
    ContrastFloor,
    NamedRole,
    ThemeManifest,
    UnknownInvariant,
    UsageBudget,
    try_get_token,
)

type ManifestDefectCode = Literal[
    "CONTRAST_FLOOR_OUT_OF_RANGE",
    "CONTRAST_FLOOR_ROLE_EMPTY",
    "CONTRAST_FLOOR_ROLE_UNBOUND",
    "USAGE_BUDGET_TOKEN_UNDECLARED",
    "INVARIANT_KIND_UNKNOWN",
]


@dataclass(frozen=True)
class ManifestDefect:
    """One validation finding: which rule, which invariant (its position in the
    decoded list), and what it names."""

    code: ManifestDefectCode
    index: int
    subject: str


# The range WCAG's contrast formula produces: below it every pair passes, above it none can.
_MIN_CONTRAST_RATIO: Final = 1.0
_MAX_CONTRAST_RATIO: Final = 21.0


def _binds(role: str, manifest: ThemeManifest) -> bool:
    """Does any role binding bind ``role``? A tone binding binds its tone's wire
    name, a named one its name."""
    return any((b.role.name if isinstance(b.role, NamedRole) else b.role.tone) == role for b in manifest.roles)


def validate(manifest: ThemeManifest) -> list[ManifestDefect]:
    """Every declared invariant that cannot mean what it says, as one shared code.

    Invariant by invariant in list order, and within one in the code table's order.
    """
    out: list[ManifestDefect] = []
    for index, inv in enumerate(manifest.invariants):
        k = inv.kind
        if isinstance(k, ContrastFloor):
            if not (_MIN_CONTRAST_RATIO <= k.min_ratio <= _MAX_CONTRAST_RATIO):
                out.append(ManifestDefect("CONTRAST_FLOOR_OUT_OF_RANGE", index, k.role))
            if k.role == "":
                out.append(ManifestDefect("CONTRAST_FLOOR_ROLE_EMPTY", index, ""))
            elif not _binds(k.role, manifest):
                out.append(ManifestDefect("CONTRAST_FLOOR_ROLE_UNBOUND", index, k.role))
        elif isinstance(k, UsageBudget):
            if try_get_token(k.token, manifest) is None:
                out.append(ManifestDefect("USAGE_BUDGET_TOKEN_UNDECLARED", index, k.token))
        elif isinstance(k, UnknownInvariant):
            out.append(ManifestDefect("INVARIANT_KIND_UNKNOWN", index, k.name))
    return out


def encode_manifest_defect(d: ManifestDefect) -> str:
    """One defect's canonical JSON — ``{"code":…,"index":…,"subject":…}`` — the bytes
    the corpus's ``manifest-validation`` vectors pin on every host."""
    return encode_value(Obj(None, {"code": d.code, "index": d.index, "subject": d.subject}))
