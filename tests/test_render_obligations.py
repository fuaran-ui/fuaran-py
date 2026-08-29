"""Executable render-obligation conformance (WIRE_FORMAT.md §13) — this host's adoption.

The sibling of the F# and TypeScript server-renderer suites.

Codec conformance is byte-parity and strong. Render obligations were prose: §3.6.5
and §3.6.6 state, in sentences, that an accessible name is always emitted, that
``autoplay`` never appears without ``muted``, that an audio transport has no
autoplay pathway at all, that a refused source emits no affordance. A host can
pass every fixture in the corpus and silently fail every one of those — none is a
missing discriminator arm, so no codec test and no type checker reaches them.

So the manifest carries them now, and this suite asserts FROM the manifest rather
than from a hand list beside it. Three consequences, which are the whole point:

* The ENUMERATION is the corpus artefact's. A newly declared obligation on a kind
  this host renders arrives here as a claim with no checker and turns the suite
  RED — not as a paragraph a future reader may re-read.

* NOT CHECKED IS NOT PASSED. Every claim this host does not assert is printed by
  name with the section that states it, and fails the gate unless it carries a
  declared exemption. Silence is never an answer.

* The go-red property is PROVEN. ``status_of`` is exercised against a claim no
  checker covers and must report it unchecked — the shape a new obligation takes
  on the day it lands.

Every checker asserts in EMITTED HTML through this host's render path. A checker
that inspected the typed tree would be re-stating the type system; the obligations
are claims about output. The trees are authored through ``fuaran_py.ui`` and taken
through the canonical wire (``encode`` → ``decode_node``) before rendering, which
is the only path this host's renderer accepts — and means each checker asserts
against a tree that has survived the codec, not one built beside it.

Skipped when the artefact is absent (the standalone-checkout posture the sibling
corpus tests use). Note the artefact is a corpus-root file the offline snapshot
sync does not copy, so a standalone checkout skips rather than certifying against
a stale copy.
"""

from __future__ import annotations

import json
import os
import warnings
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from _corpus import CORPUS_ROOT
from fuaran_py import decode_node
from fuaran_py.render_fidelity import (
    Asserted,
    ObligationOutcome,
    ObligationReport,
    RenderFidelityManifest,
    Unchecked,
    describe_obligation_report,
    parse_manifest,
    report_obligations,
    unasserted_obligations,
)
from fuaran_py.renderer import render_html
from fuaran_py.ui import encode, fuaran

# ── Locating the artefact ────────────────────────────────────────────────────
#
# ``FUARAN_RENDER_FIDELITY`` names the artefact FILE and overrides the corpus-root
# resolution. It exists so the go-red property of this suite can be PROVEN rather
# than asserted: a perturbed scratch copy — an obligation declared on a kind whose
# row has none — is pointed at with the variable, the suite is observed to fail
# naming that claim, and the shared corpus is never written to. A conformance
# gate whose falsifiability is only claimed is the shape this whole file exists to
# replace, so the mechanism that proves it must not require perturbing the oracle.
_OVERRIDE = os.environ.get("FUARAN_RENDER_FIDELITY")
ARTIFACT = Path(_OVERRIDE) if _OVERRIDE else CORPUS_ROOT / "render-fidelity.json"

artifact_required = pytest.mark.skipif(
    not ARTIFACT.is_file(),
    reason=f"render-fidelity.json not found at {ARTIFACT}",
)


def load() -> RenderFidelityManifest:
    return parse_manifest(json.loads(ARTIFACT.read_text(encoding="utf-8")))


def render(node: object) -> str:
    """Author → canonical wire → decode → HTML.

    The renderer consumes a decoded structural tree, so this is the host's real
    render path rather than a shortcut around it.
    """
    decoded = decode_node(encode(node))  # type: ignore[arg-type]
    assert decoded.ok, getattr(decoded, "error", decoded)
    return render_html(decoded.value)


#: A destination that is safe by the scheme floor and entirely undeclared, so the
#: ambient egress policy (deny-by-default on this host) refuses it. This is the
#: input the three "refused" obligations are about.
REFUSED = "https://collector.example/asset.jpg"
#: The marked refusal a refused destination renders as.
REFUSAL_URL = "about:blank#fuaran-egress-refused"


# ── The checkers ────────────────────────────────────────────────────────────
#
# One per (kind, claim). Each pins BOTH directions where the obligation has two:
# an emission test alone cannot tell a renderer that honours a conditional from
# one that emits unconditionally.


def check_accessible_name_always() -> None:
    # Both variants, because the label is mandatory for the KIND and not for one
    # arm of it. A renderer emitting it only on `<video>` passes a video-only test.
    video = render(fuaran.video("mv", src="/walkthrough.mp4", label="Studio walkthrough"))
    audio = render(fuaran.audio("ma", src="/commentary.mp3", label="Curator commentary"))

    assert 'aria-label="Studio walkthrough"' in video, "a video emits the resolved label as aria-label"
    assert 'aria-label="Curator commentary"' in audio, "an audio emits the resolved label as aria-label"


def check_autoplay_muted_pairing() -> None:
    autoplaying = render(fuaran.video("mva", src="/ambient.mp4", label="Ambient loop", autoplay=True))

    assert "autoplay" in autoplaying, "a declared autoplay is emitted"
    assert "muted" in autoplaying, "and never without muted — an unmuted autoplay is blocked and means nothing"

    # The pairing runs one way, and this is the half a one-sided assertion misses:
    # `muted` unasked silences a video the reader started themselves.
    plain = render(fuaran.video("mv", src="/walkthrough.mp4", label="Studio walkthrough"))

    assert "autoplay" not in plain, "autoplay is not declared, so it must not be emitted"
    assert "muted" not in plain, "muted rides autoplay; unasked it is a behaviour change"


def check_no_autoplay_pathway() -> None:
    audio = render(fuaran.audio("ma", src="/commentary.mp3", label="Curator commentary"))

    assert "autoplay" not in audio, "an <audio> must never carry an autoplay attribute"
    assert "muted" not in audio, "an <audio> has no autoplay, so it has nothing to mute"


def check_refused_source_dropped() -> None:
    refused = render(fuaran.video("mvp", src="/walkthrough.mp4", label="Studio walkthrough", poster=REFUSED))

    assert "collector.example" not in refused, "a refused poster's destination is never emitted"
    assert "poster=" not in refused, (
        "a refused poster is DROPPED, not emitted at the refusal URL — a poster at the refusal URL is a "
        "broken image over the player, where no poster shows the first frame"
    )

    # The allow twin. Without it a renderer that dropped EVERY poster would pass
    # the refusal assertion and this obligation would guard nothing.
    allowed = render(
        fuaran.video("mvp2", src="/walkthrough.mp4", label="Studio walkthrough", poster="/walkthrough-poster.jpg")
    )

    assert 'poster="/walkthrough-poster.jpg"' in allowed, "a local poster still renders"


def check_alt_always_emitted() -> None:
    named = render(fuaran.image("img", src="/harbour.jpg", alt="Fishing boats moored at first light"))
    assert 'alt="Fishing boats moored at first light"' in named, "the alt text is emitted"

    # The decorative case is the one that matters. An omitted `alt` and an empty
    # one are different claims to assistive technology: omitted means "nobody
    # said", empty means "this is decorative, skip it".
    decorative = render(fuaran.image("imgd", src="/rule.png", alt=""))
    assert 'alt=""' in decorative, "a decorative image emits an EMPTY alt, never no alt at all"


def check_anchor_affordance_on_expandable() -> None:
    html = render(fuaran.image("imge", src="/harbour.jpg", alt="Harbour", expandable=True))

    # The ELEMENT is pinned, not only the class: the whole no-JS claim is that
    # this is an `<a href>`, and a `<span class="fuaran-image-expand">` carrying
    # the data attribute would pass a class-only assertion while giving a
    # scriptless reader nothing.
    assert '<a class="fuaran-image-expand" href="/harbour.jpg" data-fuaran-expandable="">' in html, (
        "expandable emits a real anchor to the asset the image already names"
    )

    not_expandable = render(fuaran.image("imgp", src="/harbour.jpg", alt="Harbour"))
    assert "fuaran-image-expand" not in not_expandable, "an undeclared expansion emits no anchor"


def check_refused_src_no_affordance() -> None:
    html = render(fuaran.image("imgr", src=REFUSED, alt="Harbour", expandable=True))

    assert "fuaran-image-expand" not in html, (
        "a src the egress floor refused emits NO expand anchor — an affordance that cannot be honoured is "
        "worse than none"
    )

    # The image itself still renders, at the refusal URL. Without this leg a
    # renderer that dropped the whole node would pass the assertion above, and
    # this obligation would be satisfied by a worse bug than the one it guards.
    assert REFUSAL_URL in html, "the img is still emitted, with the marked refusal URL as its src"
    assert 'href="https://collector.example' not in html, "and the refused destination never becomes a navigable href"


def check_figure_caption_outside_link() -> None:
    html = render(
        fuaran.image(
            "imgef",
            src="/harbour.jpg",
            alt="Harbour",
            expandable=True,
            caption="The harbour at dawn",
        )
    )

    # Asserting the two opening tags IN ORDER is what catches the inversion
    # (anchor outside figure), which would carry every one of the same classes.
    assert (
        '<figure class="fuaran-image-figure"><a class="fuaran-image-expand" href="/harbour.jpg" '
        'data-fuaran-expandable="">' in html
    ), "the figure wraps the anchor, not the other way round"
    assert '</a><figcaption class="fuaran-image-figure-caption">The harbour at dawn</figcaption></figure>' in html, (
        "the figcaption is the anchor's SIBLING — the caption is prose a reader quotes, not a second click surface"
    )


def check_srcset_ascending_by_width() -> None:
    # Authored DESCENDING, so the assertion pins the renderer's SORT and not
    # merely its spelling: the wire preserves authored array order (§3.6.4), so a
    # renderer emitting authored order would produce a srcset containing all the
    # same URLs and fail here.
    html = render(
        fuaran.image(
            "imgs",
            src="/harbour.jpg",
            alt="Harbour",
            src_set=[("/harbour-1600.jpg", 1600), ("/harbour-800.jpg", 800), ("/harbour-400.jpg", 400)],
        )
    )

    assert 'srcset="/harbour-400.jpg 400w, /harbour-800.jpg 800w, /harbour-1600.jpg 1600w"' in html, (
        "candidates are emitted ascending by width"
    )

    # The second half of the same obligation: a refused candidate is DROPPED, so
    # the primary src remains the fallback rather than the list carrying a
    # destination the floor refused.
    with_refused = render(
        fuaran.image(
            "imgs2",
            src="/harbour.jpg",
            alt="Harbour",
            src_set=[("/harbour-400.jpg", 400), (REFUSED, 1600)],
        )
    )

    assert "collector.example" not in with_refused, "a refused candidate's destination is never emitted"
    assert "/harbour-400.jpg 400w" in with_refused, "…while the candidates that pass the floor still are"


# ── The unregistered-degradation obligation (§25.4) ─────────────────────────


def check_unregistered_custom_labelled() -> None:
    """The claim is CONDITIONAL on a contract card being available, and this host
    holds no card reader — so no card is available for any identity, and the
    identity-only placeholder is the conformant answer rather than a shortfall.

    What is asserted here is therefore the UNCARDED path alone, which is the only
    path this host has: the placeholder names the component, emits no prop VALUE,
    and invents no description it does not have. The carded branches of §25.4 (a
    card's summary, the machine-readable verdict marker, the withheld description
    on a contradicted content hash) are OUT OF SCOPE for this host because it has
    nothing to read a card from.

    This host does NOT thereby claim §25 adoption. That is a separate bar with its
    own §11.0 table, and asserting the uncarded leg of one §25.4 obligation is not
    it.
    """
    html = render(
        fuaran.custom(
            "cust",
            module_id="analytics",
            component_id="sparkline",
            props={"series": '{"points":[1,2,3]}'},
        )
    )

    assert "[fuaran:custom analytics.sparkline]" in html, (
        "the identity-only placeholder names the component — a reader is never left with a blank"
    )
    assert 'data-fuaran-custom-module="analytics"' in html, "the module identity is machine-readable"
    assert 'data-fuaran-custom-component="sparkline"' in html, "and so is the component identity"

    # No card, so no claim about a card. A verdict marker here would assert a
    # verification this host never performed.
    assert "data-fuaran-custom-card" not in html, "a host with no card claims nothing about a card"

    # Never a prop VALUE: this host was not asked to interpret the node's props,
    # and a placeholder that leaked one would be disclosing payload it does not
    # understand into the document.
    assert "points" not in html, "no prop value reaches the placeholder"
    assert "series" not in html, "not even the declared prop names, absent a card that declares them"


#: The registry: which (kind, claim) pairs this host asserts, and how. Keyed by
#: the claim's WIRE token, because the enumeration it is matched against comes
#: from the artefact.
CHECKERS: Mapping[str, Callable[[], None]] = {
    "Media/accessible-name-always": check_accessible_name_always,
    "Media/autoplay-muted-pairing": check_autoplay_muted_pairing,
    "Media/no-autoplay-pathway": check_no_autoplay_pathway,
    "Media/refused-source-dropped": check_refused_source_dropped,
    "Image/alt-always-emitted": check_alt_always_emitted,
    "Image/anchor-affordance-on-expandable": check_anchor_affordance_on_expandable,
    "Image/refused-src-no-affordance": check_refused_src_no_affordance,
    "Image/figure-caption-outside-link": check_figure_caption_outside_link,
    "Image/srcset-ascending-by-width": check_srcset_ascending_by_width,
    "Custom/unregistered-custom-labelled": check_unregistered_custom_labelled,
}

#: Obligations this host declares it does NOT check, each with a reason.
#:
#: EMPTY is the correct state for this host: it renders every canonical kind, so
#: every declared obligation is one it owes. The map exists because the
#: alternative — an unchecked obligation silently absent from the registry — is
#: precisely the failure the manifest replaces. A host that genuinely cannot check
#: a claim (no player, no network loader, a decode-only surface) records it here
#: and its report says so out loud.
DECLARED_EXEMPTIONS: Mapping[str, str] = {}

#: The reason an unregistered claim reports, worded so a reader can act on it
#: without reading this file first.
NO_CHECKER_REASON = (
    "no checker registered in test_render_obligations.py and no declared exemption — "
    "add one, or declare why this host cannot check it"
)


def status_of(kind: str, claim_id: str) -> ObligationOutcome:
    key = f"{kind}/{claim_id}"
    if key in CHECKERS:
        return Asserted()
    exemption = DECLARED_EXEMPTIONS.get(key)
    if exemption is not None:
        return Unchecked(exemption)
    return Unchecked(NO_CHECKER_REASON)


def _surface(line: ObligationReport) -> None:
    """Print one unasserted line, and warn as well when it is EXEMPTED.

    ``print`` is the shape the sibling hosts use and is what a failing gate shows.
    But pytest captures stdout on a PASSING test, so a declared exemption — the
    one unasserted outcome that does not fail — would be invisible in exactly the
    run where nobody is already looking. The warning puts it in the summary of
    every run instead. "Not checked is not passed" is worth nothing if the reader
    never sees which claims were not checked.
    """
    described = f"  render obligation not asserted: {describe_obligation_report(line)}"
    print(described)
    if f"{line.kind}/{line.claim_id}" in DECLARED_EXEMPTIONS:
        warnings.warn(described.strip(), UserWarning, stacklevel=2)


# ── The gate ────────────────────────────────────────────────────────────────


@artifact_required
def test_asserts_every_obligation_the_manifest_declares() -> None:
    manifest = load()
    report = report_obligations(manifest, status_of)

    assert report, (
        "the manifest declares no obligations at all — either the artefact is stale or this suite is "
        "reading the wrong file, and either way it is asserting nothing"
    )

    # NOT CHECKED IS NOT PASSED. Everything this host did not assert is surfaced
    # by name and section before the gate decides, so an exempted claim is visible
    # in the run rather than inferable from its absence.
    unmet = unasserted_obligations(report)
    for line in unmet:
        _surface(line)

    undeclared = [
        f"{line.kind}/{line.claim_id} [{line.section}]"
        for line in unmet
        if f"{line.kind}/{line.claim_id}" not in DECLARED_EXEMPTIONS
    ]

    assert not undeclared, (
        "a render obligation this host owes has no checker: assert it, or add a declared exemption "
        f"saying why this host cannot — {undeclared}"
    )


# ── The go-red proof ────────────────────────────────────────────────────────


@artifact_required
def test_an_obligation_with_no_checker_reports_unchecked() -> None:
    # The shape a NEWLY-DECLARED obligation takes on the day it lands: a
    # kind/claim pair the registry does not cover. Without this probe the gate
    # above could be green because the classification never reports anything,
    # which is the completeness check that cannot fail.
    outcome = status_of("Markdown", "accessible-name-always")
    assert outcome.status == "unchecked", "an unregistered (kind, claim) must be reported UNCHECKED"
    assert isinstance(outcome, Unchecked)
    assert "no checker registered" in outcome.reason, "in words a reader can act on"

    # …and the gate's own filter must classify it as unasserted, which is what
    # turns the suite red.
    probe = ObligationReport(
        kind="Markdown",
        claim_id="accessible-name-always",
        statement="",
        section="probe",
        outcome=outcome,
    )
    assert len(unasserted_obligations([probe])) == 1


# ── The vocabulary seam ─────────────────────────────────────────────────────


@artifact_required
def test_every_declared_claim_resolves_against_the_closed_vocabulary() -> None:
    # A row naming a claim the vocabulary omits is unresolvable: a host keying its
    # registry off the vocabulary could never report it, and a host must never
    # accept a claim it cannot name.
    manifest = load()
    vocabulary = {entry.id for entry in manifest.obligation_vocabulary}

    assert vocabulary, "the artefact carries no obligation vocabulary"

    unresolvable = [
        f"{row.kind}/{obligation.id}"
        for row in manifest.kinds
        for obligation in row.obligations
        if obligation.id not in vocabulary
    ]
    assert not unresolvable, f"a kind declares an obligation the closed vocabulary does not carry: {unresolvable}"

    # Every claim carries a section. An obligation with no section is an assertion
    # about a host's habits, not about the specification.
    for row in manifest.kinds:
        for obligation in row.obligations:
            assert "WIRE_FORMAT.md" in obligation.section, f"{row.kind}/{obligation.id}: no spec section"
            assert obligation.statement, f"{row.kind}/{obligation.id}: no normative statement"


# ── The registry is not itself a second source of truth ─────────────────────


@artifact_required
def test_registers_no_checker_for_an_obligation_the_manifest_does_not_declare() -> None:
    # A checker for a claim no row declares is a stale assertion: it passes
    # forever and guards a contract that has moved, which is exactly the drift the
    # generated artefact exists to remove.
    manifest = load()
    declared = {f"{row.kind}/{obligation.id}" for row in manifest.kinds for obligation in row.obligations}
    orphans = sorted(key for key in CHECKERS if key not in declared)

    assert not orphans, (
        "a checker asserts an obligation no manifest row declares — either the row was removed or the "
        f"checker was never declared: {orphans}"
    )


# ── The checkers themselves ─────────────────────────────────────────────────
#
# Run by name, so a failing obligation names the claim it broke rather than
# surfacing as one opaque red test.


@artifact_required
@pytest.mark.parametrize("claim", sorted(CHECKERS))
def test_owes(claim: str) -> None:
    CHECKERS[claim]()
