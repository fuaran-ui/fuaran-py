"""What a *static projection* does with each canonical wire kind.

The renderer emits one thing today: an HTML body fragment for a browser, where every
kind either paints or hydrates. Two further projections of the same tree — a crawlable
markdown **document** (:mod:`fuaran_ui.renderer.document`) and an email-safe **digest**
(:mod:`fuaran_ui.renderer.email`) — render into targets with no runtime at all, and both
therefore have to answer a question the browser renderer never asks: *what does this kind
become when nothing can execute?*

This module is that question's vocabulary, and nothing else. It holds no scope table of
its own — each projection declares its own, one row per kind — but it holds the four
dispositions they classify with, the lookup, and the two completeness checks that make a
scope table falsifiable against the canonical kind list rather than merely plausible.

**Why the vocabulary is shared and the tables are not.** The two projections disagree
constantly about individual kinds — a ``Chart`` is inline SVG in a document and a link in
a digest, because Outlook's Word engine does not draw SVG — so a shared table would be
wrong on its first row. What they cannot afford to disagree about is what the four words
MEAN, and what it means for a table to be complete: those are the halves that a second
copy would let drift silently, which is the only kind of drift worth a module.

The vocabulary is the reference host's, transliterated: ``Fuaran.UI.Renderer.Server.Email``
declares the same four dispositions for the same reason, and the distinction between the
last two is the one that carries weight — ``OpenLive`` says "this exists and you have to
leave the page to use it", ``Omitted`` says "this carries nothing a static projection can
convey". A reader can act on the first.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

#: The four dispositions, as the discriminator string a :class:`Disposition` carries.
#:
#: A ``Literal`` rather than an ``Enum``: these are read in table literals hundreds of
#: rows long, and the enum spelling would triple the width of every row without making a
#: single one of them clearer.
DispositionKind = Literal["rendered", "structural", "openLive", "omitted"]


@dataclass(frozen=True)
class Disposition:
    """What a projection does with one wire kind, and — mandatorily — why.

    ``note`` is not documentation of the code; it is the decision. A scope row that
    records only "openLive" says nothing a reader can argue with, and the arguments are
    the content here: several rows in both tables exist to record an alternative that was
    considered and declined, which is exactly the reasoning a later maintainer would
    otherwise have to reconstruct before daring to change the row.
    """

    kind: DispositionKind
    note: str


def rendered(note: str) -> Disposition:
    """Painted by the projection in full — the display subset proper."""
    return Disposition("rendered", note)


def structural(note: str) -> Disposition:
    """A carrier: the node paints nothing beyond layout; its children render."""
    return Disposition("structural", note)


def open_live(note: str) -> Disposition:
    """Projected to a labelled "open live" affordance. Never a half-working control."""
    return Disposition("openLive", note)


def omitted(note: str) -> Disposition:
    """Zero-paint in this projection, deliberately."""
    return Disposition("omitted", note)


#: A projection's declared scope: one row per canonical wire kind, ordered by wire name.
#:
#: A tuple rather than a dict, so the authored ORDER is the file's order and a new kind
#: lands as one clean insert a reviewer can find. :func:`duplicate_kinds` is what keeps
#: the sequence honest about being keyed.
Scope = Sequence[tuple[str, Disposition]]


def disposition_of(scope: Scope, wire_kind: str) -> Disposition | None:
    """The declared posture of ``wire_kind``, or ``None`` for a kind with no row.

    ``None`` is reachable only for a kind outside the canonical set — the completeness
    check below makes a missing canonical row a test failure rather than a silent
    fallthrough at render time.
    """
    for kind, disposition in scope:
        if kind == wire_kind:
            return disposition
    return None


def duplicate_kinds(scope: Scope) -> list[str]:
    """Kinds appearing more than once in ``scope``, in first-duplicate order.

    A sequence can hold two rows for one kind where a mapping cannot, and
    :func:`disposition_of` would then silently serve the first. Cheap to check, and the
    failure it catches is invisible by construction.
    """
    seen: set[str] = set()
    dupes: list[str] = []
    for kind, _ in scope:
        if kind in seen and kind not in dupes:
            dupes.append(kind)
        seen.add(kind)
    return sorted(dupes)


def missing_kinds(scope: Scope, canonical: Iterable[str]) -> list[str]:
    """Canonical kinds the scope table does not declare a posture for.

    This is the check that makes a projection's scope line a CONTRACT rather than a
    snapshot: a new ``NodeKind`` arriving in the wire format cannot reach a release with
    no declared posture, because the test that calls this reddens the moment the manifest
    grows a row the table has not answered.
    """
    declared = {kind for kind, _ in scope}
    return sorted(k for k in canonical if k not in declared)


def unknown_kinds(scope: Scope, canonical: Iterable[str]) -> list[str]:
    """Scope rows naming something the canonical kind list does not contain.

    The converse check, and it catches the opposite mistake: a row kept for a kind that
    was renamed or withdrawn is dead weight that reads as coverage.
    """
    known = set(canonical)
    return sorted({kind for kind, _ in scope if kind not in known})


def behavioural_wire_kinds(manifest: Mapping[str, object]) -> list[str]:
    """The kinds the render-fidelity manifest declares ``behavioural`` (WIRE_FORMAT §13).

    DERIVED, never restated. ``behavioural`` means precisely "inert server-side, gains its
    behaviour at hydration", which is the same set that must not render as a control in a
    target that hydrates nothing — so both projections take their interactive set from
    here and the corpus tests assert every member has an ``openLive`` row. A new
    interactive kind therefore reddens a test rather than silently shipping a dead button.

    Takes the parsed manifest rather than a path: this host does not guess where the
    corpus is, for the same reason :mod:`fuaran_ui.render_fidelity` does not.
    """
    kinds = manifest.get("kinds")
    if not isinstance(kinds, list):
        return []
    out: list[str] = []
    for row in kinds:
        if not isinstance(row, dict):
            continue
        rich = row.get("rich")
        name = row.get("kind")
        if isinstance(rich, dict) and rich.get("class") == "behavioural" and isinstance(name, str):
            out.append(name)
    return sorted(out)


__all__ = [
    "Disposition",
    "DispositionKind",
    "Scope",
    "behavioural_wire_kinds",
    "disposition_of",
    "duplicate_kinds",
    "missing_kinds",
    "open_live",
    "omitted",
    "rendered",
    "structural",
    "unknown_kinds",
]
