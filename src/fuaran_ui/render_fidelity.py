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

    from fuaran_ui.render_fidelity import load_manifest, fidelity_of, fidelity_badge

    manifest = load_manifest(corpus_root)
    row = fidelity_of(manifest, "Math")
    for segment in fidelity_badge(row):
        ...
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Literal

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
class ObligationVocabularyEntry:
    """One token of the CLOSED obligation vocabulary the artefact enumerates.

    Closed, and that is the property doing the work: an open free-form vocabulary
    would let a host accept a claim it has no checker for, whereas a closed one
    means a host can enumerate what exists independently of the rows it happens
    to read — and report an id it does not implement.
    """

    id: str
    meaning: str


@dataclass(frozen=True)
class RenderObligation:
    """One checkable claim a kind owes, bound to the section that states it."""

    id: str
    """The vocabulary token — resolvable against ``obligation_vocabulary``."""
    statement: str
    """The normative sentence FOR THAT KIND.

    The same claim reads differently on a transport (an accessible name is
    mandatory on the wire) and on a decorative image (it is the empty string),
    which is why the statement is per-row rather than per-vocabulary-token.
    """
    section: str
    """The ``WIRE_FORMAT.md`` section that states the claim.

    An obligation with no section is an assertion about a host's habits rather
    than about the specification, and is not admissible.
    """


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
    obligations: tuple[RenderObligation, ...] = ()
    """The checkable claims this kind owes.

    Empty states no checkable claim; it is NOT a statement that the row's
    ``fallback`` prose is optional. Defaulted so a row carrying no
    ``obligations`` key parses rather than failing — obligations are additive
    within a major version, and a reader must survive a row that predates them.
    """


@dataclass(frozen=True)
class TraitObligation:
    """One checkable claim a node-level TRAIT owes (WIRE_FORMAT §13, fuaran#1696).

    The sibling of :class:`RenderObligation` over the other subject population,
    plus ``rule``: the ordinal of the numbered rule in the cited section. It is
    carried rather than left to be matched from the prose — §3.1 states five
    numbered obligations and a conformant host registers five checkers, and
    without the ordinal the correspondence between them is a reader's
    reconstruction rather than a fact in the artefact. ``None`` where the cited
    section does not number its rules.
    """

    id: str
    statement: str
    section: str
    rule: int | None = None


@dataclass(frozen=True)
class TraitRow:
    """One node-level trait: a member that rides the ENVELOPE rather than a kind.

    ``style.direction`` is owed by a ``Badge``, a ``Markdown`` and a ``DataGrid``
    alike and belongs to none of them, so declaring it on kind rows would state
    forty-odd claims where there is one. ``trait`` is the wire PATH of the member
    it governs, which is why a host can key one registry by ``subject/claim``: a
    dotted path can never collide with a ``kind.$type``.

    ``scope`` is the tagged ``appliesTo.scope`` — ``allKinds``, or ``namedKinds``
    with ``scope_kinds`` beside it. Tagged rather than a bare list because "every
    kind" must not be spellable as an empty array, which reads as the opposite
    claim.
    """

    trait: str
    summary: str
    scope: str
    scope_kinds: tuple[str, ...]
    fixtures: tuple[str, ...]
    obligations: tuple[TraitObligation, ...]
    contract: str


@dataclass(frozen=True)
class RenderFidelityManifest:
    version: int
    id: str
    description: str
    tiers: tuple[tuple[str, str], ...]
    kinds: tuple[FidelityRow, ...]
    obligation_vocabulary: tuple[ObligationVocabularyEntry, ...] = field(default_factory=tuple)
    traits: tuple[TraitRow, ...] = field(default_factory=tuple)
    """The node-level traits.

    Defaulted so an artefact predating the section parses rather than failing,
    on exactly the terms ``obligations`` is defaulted on a row: traits are
    additive within a major version.
    """


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


def _parse_obligations(value: Any, kind: str) -> tuple[RenderObligation, ...]:
    """Parse a row's ``obligations``.

    An ABSENT key parses as empty — the rows that declare no checkable claim are
    the majority, and obligations arrived additively — while a present but
    non-array value is a defect and is named as one.
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RenderFidelityError(f"render-fidelity: {kind}.obligations must be an array")
    obligations: list[RenderObligation] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise RenderFidelityError(f"render-fidelity: {kind}.obligations[] must hold objects")
        where = f"{kind}.obligations[]"
        obligations.append(
            RenderObligation(
                id=_require_str(entry, "id", where),
                statement=_require_str(entry, "statement", where),
                section=_require_str(entry, "section", where),
            )
        )
    return tuple(obligations)


def _parse_traits(value: Any) -> tuple[TraitRow, ...]:
    """Parse the top-level ``traits`` array.

    An ABSENT key parses as empty — an artefact predating the section is a legal
    shape — while a present but malformed one is a defect and is named as one.
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        raise RenderFidelityError("render-fidelity: manifest.traits must be an array")
    traits: list[TraitRow] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise RenderFidelityError("render-fidelity: manifest.traits[] must hold objects")
        name = _require_str(entry, "trait", "traits[]")
        applies_to = entry.get("appliesTo")
        if not isinstance(applies_to, dict):
            raise RenderFidelityError(f"render-fidelity: {name}.appliesTo must be an object")
        scope = _require_str(applies_to, "scope", f"{name}.appliesTo")
        if scope not in ("allKinds", "namedKinds"):
            raise RenderFidelityError(f"render-fidelity: {name}.appliesTo.scope is unknown: {scope!r}")
        scope_kinds = applies_to.get("kinds") or []
        if not isinstance(scope_kinds, list) or not all(isinstance(k, str) for k in scope_kinds):
            raise RenderFidelityError(f"render-fidelity: {name}.appliesTo.kinds[] must be strings")
        fixtures = entry.get("fixtures") or []
        if not isinstance(fixtures, list) or not all(isinstance(f, str) for f in fixtures):
            raise RenderFidelityError(f"render-fidelity: {name}.fixtures[] must be strings")
        raw_obligations = entry.get("obligations")
        if not isinstance(raw_obligations, list):
            raise RenderFidelityError(f"render-fidelity: {name}.obligations must be an array")
        obligations: list[TraitObligation] = []
        for o in raw_obligations:
            if not isinstance(o, dict):
                raise RenderFidelityError(f"render-fidelity: {name}.obligations[] must hold objects")
            where = f"{name}.obligations[]"
            rule = o.get("rule")
            obligations.append(
                TraitObligation(
                    id=_require_str(o, "id", where),
                    statement=_require_str(o, "statement", where),
                    section=_require_str(o, "section", where),
                    rule=rule if isinstance(rule, int) else None,
                )
            )
        traits.append(
            TraitRow(
                trait=name,
                summary=_require_str(entry, "summary", name),
                scope=scope,
                scope_kinds=tuple(scope_kinds),
                fixtures=tuple(fixtures),
                obligations=tuple(obligations),
                contract=_require_str(entry, "contract", name),
            )
        )
    return tuple(traits)


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
                obligations=_parse_obligations(entry.get("obligations"), kind),
            )
        )

    tiers: list[tuple[str, str]] = []
    for entry in raw_tiers:
        if not isinstance(entry, dict):
            raise RenderFidelityError("render-fidelity: manifest.tiers[] must hold objects")
        tiers.append((_require_str(entry, "tier", "tiers[]"), _require_str(entry, "meaning", "tiers[]")))

    raw_vocabulary = value.get("obligationVocabulary")
    if raw_vocabulary is not None and not isinstance(raw_vocabulary, list):
        raise RenderFidelityError("render-fidelity: manifest.obligationVocabulary must be an array")
    vocabulary: list[ObligationVocabularyEntry] = []
    for entry in raw_vocabulary or []:
        if not isinstance(entry, dict):
            raise RenderFidelityError("render-fidelity: manifest.obligationVocabulary[] must hold objects")
        vocabulary.append(
            ObligationVocabularyEntry(
                id=_require_str(entry, "id", "obligationVocabulary[]"),
                meaning=_require_str(entry, "meaning", "obligationVocabulary[]"),
            )
        )

    raw_id = value.get("$id")
    raw_description = value.get("description")

    return RenderFidelityManifest(
        version=value["version"],
        id=raw_id if isinstance(raw_id, str) else "",
        description=raw_description if isinstance(raw_description, str) else "",
        tiers=tuple(tiers),
        kinds=tuple(kinds),
        obligation_vocabulary=tuple(vocabulary),
        traits=_parse_traits(value.get("traits")),
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


# ── Obligation coverage (WIRE_FORMAT.md §13) ────────────────────────────────
#
# The reporting shape every adopting host uses, declared here so the hosts answer
# the same question in the same words rather than each inventing a way to say "we
# did not check that". The port of the F# ``Fuaran.UI.RenderFidelity`` coverage
# surface and the TypeScript ``reportObligations`` family.
#
# The `fallback` prose in the artefact is complete and normative, and a machine
# cannot check a paragraph. A host can render a kind, pass every byte-parity
# fixture in the corpus, and still have silently dropped a claim that paragraph
# states — none of them is a missing discriminator arm, so no codec test and no
# type checker reaches them. These functions are how a host says which ones it
# actually checked.


def all_obligations(
    manifest: RenderFidelityManifest,
) -> tuple[tuple[str, RenderObligation | TraitObligation], ...]:
    """Every declared obligation, paired with the SUBJECT that owes it, in table order.

    The kind rows first, then the trait rows. Both, deliberately: the whole
    mechanism is that the ENUMERATION is the artefact's, so a trait declared
    tomorrow must reach a host's report without that host changing anything but
    its answer — and a reader iterating ``kinds`` alone would hold a green gate
    over an unowed claim.
    """
    return tuple(
        [(row.kind, obligation) for row in manifest.kinds for obligation in row.obligations]
        + [(row.trait, obligation) for row in manifest.traits for obligation in row.obligations]
    )


@dataclass(frozen=True)
class Asserted:
    """The host renders the kind and its suite checks the claim in EMITTED OUTPUT."""

    status: ClassVar[Literal["asserted"]] = "asserted"


@dataclass(frozen=True)
class Unchecked:
    """The host renders the kind and has no checker for the claim.

    The case the whole mechanism exists for. NOT CHECKED IS NOT PASSED: a host
    that renders a kind and does not assert one of its claims must say so, WITH a
    reason, and fail its gate unless the exemption is declared in the suite. An
    obligation that quietly falls out of a host's suite is exactly the silent
    failure the closed vocabulary replaces.
    """

    reason: str
    status: ClassVar[Literal["unchecked"]] = "unchecked"


@dataclass(frozen=True)
class NotRendered:
    """The host does not render the kind at all.

    Distinct from :class:`Unchecked`: nothing is owed, rather than owed and
    unpaid. It is still printed — a reader must be able to tell the two apart
    without consulting the host's source.
    """

    reason: str
    status: ClassVar[Literal["notRendered"]] = "notRendered"


type ObligationOutcome = Asserted | Unchecked | NotRendered


@dataclass(frozen=True)
class ObligationReport:
    """One line of a host's obligation report."""

    kind: str
    claim_id: str
    statement: str
    section: str
    outcome: ObligationOutcome


def report_obligations(
    manifest: RenderFidelityManifest,
    status_of: Callable[[str, str], ObligationOutcome],
) -> tuple[ObligationReport, ...]:
    """Project the manifest through a host's own answer, one line per declared obligation.

    The ENUMERATION is the manifest's, never the host's — so a newly declared
    obligation appears in the report the moment it lands rather than when someone
    remembers it. ``status_of`` is called with ``(kind, claim_id)``.
    """
    return tuple(
        ObligationReport(
            kind=kind,
            claim_id=obligation.id,
            statement=obligation.statement,
            section=obligation.section,
            outcome=status_of(kind, obligation.id),
        )
        for kind, obligation in all_obligations(manifest)
    )


def unasserted_obligations(report: Sequence[ObligationReport]) -> tuple[ObligationReport, ...]:
    """The report lines a host must SURFACE: everything it did not assert.

    Empty is the only silent result — anything else is printed, so an unchecked
    obligation is visible in the run rather than inferable from its absence.
    """
    return tuple(line for line in report if line.outcome.status != "asserted")


def describe_obligation_report(line: ObligationReport) -> str:
    """The one-line rendering of a report line.

    Byte-for-byte the sentence the F# and TypeScript hosts print, so the five
    hosts answer the same question in the same words.
    """
    outcome = line.outcome
    if isinstance(outcome, Asserted):
        rendered = "asserted"
    elif isinstance(outcome, Unchecked):
        rendered = f"UNCHECKED ({outcome.reason})"
    else:
        rendered = f"not rendered ({outcome.reason})"
    return f"{line.kind}/{line.claim_id} [{line.section}]: {rendered}"
