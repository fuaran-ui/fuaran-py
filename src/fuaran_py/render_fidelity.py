"""The render-fidelity manifest reader (WIRE_FORMAT.md §13).

The canonical wire format ships a generated per-``NodeKind`` declaration at
``wire-format-fixtures/render-fidelity.json``: for each ``kind.$type``, what the
wire carries (*source*), what the parity-checked render pins (*fallback*), and
what — if anything — is declared client-only *rich*.

This module is the reader and the badge derivation, **not** a copy of the data.
That distinction is the point of the phase that introduced the artefact: the
tiers lived in prose, so any surface that wanted to *state* which tier it was
delivering had to hand-annotate, and a hand annotation here would be a second
source of truth drifting silently from the declaration the artefact is generated
from. Nothing below enumerates a kind or asserts a posture; the manifest is
loaded, parsed and read.

Loading is explicit — pass the corpus directory or the parsed JSON. This host
does not guess where the corpus is, for the same reason its conformance suite
resolves it in one documented place.

Canonical use::

    from fuaran_py.render_fidelity import load_manifest, fidelity_of, fidelity_badge

    manifest = load_manifest(corpus_root)
    row = fidelity_of(manifest, "Math")
    for segment in fidelity_badge(row):
        ...
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

#: The artefact's file name at the corpus root.
ARTIFACT_NAME = "render-fidelity.json"

#: How a kind's declared client-only tier relates to the parity-checked DOM.
#:
#: ``clientOnly`` replaces or upgrades the fallback's DOM after hydration and is
#: excluded from every parity comparison by contract. ``behavioural`` attaches
#: behaviour at hydration and must NOT alter the hydrated DOM, which is why the
#: overlay contract admits a focus trap and refuses a portal. ``none`` is a
#: positive statement: the fallback is the whole render.
RichTierClass = Literal["none", "behavioural", "clientOnly"]

TierName = Literal["source", "fallback", "rich"]


@dataclass(frozen=True)
class RichTier:
    """The declared third tier of a kind, if any."""

    cls: RichTierClass
    meaning: str
    technique: str | None = None
    enhancement: str | None = None
    seam: str | None = None


@dataclass(frozen=True)
class FidelityRow:
    """One kind's declared render-fidelity posture."""

    kind: str
    """The wire discriminator (``kind.$type``)."""
    sensitive: bool
    """Whether the kind carries an explicit, phase-pinned fidelity contract."""
    source: str
    fallback: str
    rich: RichTier
    fixtures: tuple[str, ...]
    """Corpus-relative fixture paths pinning the fallback."""
    contract: str


@dataclass(frozen=True)
class RenderFidelityManifest:
    version: int
    id: str
    description: str
    tiers: tuple[tuple[str, str], ...]
    kinds: tuple[FidelityRow, ...]


class RenderFidelityError(ValueError):
    """The artefact is absent or malformed.

    Raised rather than degraded-to-empty on purpose: a fidelity surface that
    silently reads as "no data" is how a badge starts lying.
    """


def _require_str(obj: dict[str, Any], key: str, where: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str):
        raise RenderFidelityError(f"render-fidelity: {where}.{key} must be a string")
    return value


def _parse_rich(value: Any, kind: str) -> RichTier:
    if not isinstance(value, dict):
        raise RenderFidelityError(f"render-fidelity: {kind}.rich must be an object")
    cls = _require_str(value, "class", f"{kind}.rich")
    if cls not in ("none", "behavioural", "clientOnly"):
        raise RenderFidelityError(f"render-fidelity: {kind}.rich.class is an unknown tier class {cls!r}")
    return RichTier(
        cls=cls,  # type: ignore[arg-type]
        meaning=_require_str(value, "meaning", f"{kind}.rich"),
        technique=value.get("technique") if isinstance(value.get("technique"), str) else None,
        enhancement=value.get("enhancement") if isinstance(value.get("enhancement"), str) else None,
        seam=value.get("seam") if isinstance(value.get("seam"), str) else None,
    )


def parse_manifest(value: Any) -> RenderFidelityManifest:
    """Parse the generated artefact, naming the offending path on any defect."""
    if not isinstance(value, dict):
        raise RenderFidelityError("render-fidelity: the manifest must be a JSON object")
    if not isinstance(value.get("version"), int):
        raise RenderFidelityError("render-fidelity: manifest.version must be a number")
    raw_kinds = value.get("kinds")
    raw_tiers = value.get("tiers")
    if not isinstance(raw_kinds, list):
        raise RenderFidelityError("render-fidelity: manifest.kinds must be an array")
    if not isinstance(raw_tiers, list):
        raise RenderFidelityError("render-fidelity: manifest.tiers must be an array")

    kinds: list[FidelityRow] = []
    for entry in raw_kinds:
        if not isinstance(entry, dict):
            raise RenderFidelityError("render-fidelity: manifest.kinds[] must hold objects")
        kind = _require_str(entry, "kind", "kinds[]")
        fixtures = entry.get("fixtures") or []
        if not isinstance(fixtures, list) or not all(isinstance(f, str) for f in fixtures):
            raise RenderFidelityError(f"render-fidelity: {kind}.fixtures[] must be strings")
        kinds.append(
            FidelityRow(
                kind=kind,
                sensitive=entry.get("sensitive") is True,
                source=_require_str(entry, "source", kind),
                fallback=_require_str(entry, "fallback", kind),
                rich=_parse_rich(entry.get("rich"), kind),
                fixtures=tuple(fixtures),
                contract=_require_str(entry, "contract", kind),
            )
        )

    tiers: list[tuple[str, str]] = []
    for entry in raw_tiers:
        if not isinstance(entry, dict):
            raise RenderFidelityError("render-fidelity: manifest.tiers[] must hold objects")
        tiers.append((_require_str(entry, "tier", "tiers[]"), _require_str(entry, "meaning", "tiers[]")))

    raw_id = value.get("$id")
    raw_description = value.get("description")

    return RenderFidelityManifest(
        version=value["version"],
        id=raw_id if isinstance(raw_id, str) else "",
        description=raw_description if isinstance(raw_description, str) else "",
        tiers=tuple(tiers),
        kinds=tuple(kinds),
    )


def load_manifest(corpus_root: Path | str) -> RenderFidelityManifest:
    """Read and parse ``render-fidelity.json`` from a corpus directory."""
    path = Path(corpus_root) / ARTIFACT_NAME
    if not path.is_file():
        raise RenderFidelityError(
            f"render-fidelity: {path} not found. Regenerate it from the F# reference with "
            "`dotnet run --project src/Fuaran.UI.JsonDecode.Tests -- --emit-fidelity <corpus>`."
        )
    return parse_manifest(json.loads(path.read_text(encoding="utf-8")))


def fidelity_of(manifest: RenderFidelityManifest, wire_kind: str) -> FidelityRow | None:
    """The declared posture of a wire kind, or ``None`` when the manifest has no row.

    ``None`` is the honest answer for a kind arriving over the §15.3 tolerance
    path — it must be reported as unknown, never assumed single-tier.
    """
    for row in manifest.kinds:
        if row.kind == wire_kind:
            return row
    return None


@dataclass(frozen=True)
class BadgeSegment:
    tier: TierName
    present: bool
    """Whether the kind HAS this tier.

    ``False`` on ``rich`` is a positive statement ("the fallback is the whole
    render"), not missing information.
    """
    detail: str


def fidelity_badge(row: FidelityRow) -> tuple[BadgeSegment, BadgeSegment, BadgeSegment]:
    """The three-segment fidelity badge for a row: source / fallback / rich.

    The port of the F# ``Fuaran.UI.RenderFidelity.badge`` and the TypeScript
    ``fidelityBadge``. Same artefact, same three segments, same order, so a badge
    reads identically whichever host produced the page.
    """
    if row.rich.cls == "none":
        rich_detail = row.rich.meaning
    elif row.rich.cls == "behavioural":
        rich_detail = f"behaviour only, no DOM change: {row.rich.enhancement or ''} ({row.rich.seam or ''})"
    else:
        rich_detail = (
            f"client-only, outside every parity comparison: {row.rich.technique or ''} ({row.rich.seam or ''})"
        )

    return (
        BadgeSegment(tier="source", present=True, detail=row.source),
        BadgeSegment(tier="fallback", present=True, detail=row.fallback),
        BadgeSegment(tier="rich", present=row.rich.cls != "none", detail=rich_detail),
    )


def delivered_tier(row: FidelityRow, target: Literal["no_script", "hydrated"]) -> Literal["fallback", "rich"]:
    """Which tier a given target actually delivers for a kind.

    ``no_script`` — the scripts-disabled reader, a crawler, or a non-browser host
    such as this one — always receives the fallback, by contract. A hydrated
    browser receives the rich tier only where one is declared ``clientOnly``; a
    ``behavioural`` tier changes no DOM, so the delivered RENDER is still the
    fallback even after hydration.
    """
    return "rich" if target == "hydrated" and row.rich.cls == "clientOnly" else "fallback"
