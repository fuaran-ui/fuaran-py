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
are claims about output. The trees are authored through ``fuaran_ui.ui`` and taken
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
import re
import warnings
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui import decode_node
from fuaran_ui.render_fidelity import (
    Asserted,
    ObligationOutcome,
    ObligationReport,
    RenderFidelityManifest,
    Unchecked,
    all_obligations,
    describe_obligation_report,
    parse_manifest,
    report_obligations,
    unasserted_obligations,
)
from fuaran_ui.renderer import render_html
from fuaran_ui.renderer.egress import DENY_NON_LOCAL_EGRESS, PERMISSIVE_EGRESS, EgressPolicy
from fuaran_ui.schema import types as t
from fuaran_ui.ui import encode, fuaran, track

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


def render(node: object, egress_policy: EgressPolicy = DENY_NON_LOCAL_EGRESS) -> str:
    """Author → canonical wire → decode → HTML.

    The renderer consumes a decoded structural tree, so this is the host's real
    render path rather than a shortcut around it.

    ``egress_policy`` defaults to the host's own deny-by-default, which is what
    the three "refused" obligations are asserted under. A checker that needs the
    ALLOWED twin — the half without which a renderer that dropped everything
    would pass — names the permissive policy explicitly.
    """
    decoded = decode_node(encode(node))  # type: ignore[arg-type]
    assert decoded.ok, getattr(decoded, "error", decoded)
    return render_html(decoded.value, egress_policy=egress_policy)


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

    # fuaran#1110 widened this claim to the TRACK source, which takes the
    # poster's disposition rather than the element source's: an element must have
    # a source, but it need not have this track, and a <track> at the refusal URL
    # is a menu entry that opens onto nothing.
    refused_track = render(
        fuaran.video(
            "mvt0",
            src="/walkthrough.mp4",
            label="Studio walkthrough",
            tracks=[
                track.captions(src=REFUSED, src_lang="en", label="English captions"),
                track.subtitles(src="/walkthrough.gd.vtt", src_lang="gd", label="Gàidhlig"),
            ],
        )
    )

    assert "collector.example" not in refused_track, "a refused track source's destination is never emitted"
    assert REFUSAL_URL not in refused_track, "the track is DROPPED, not emitted at the refusal URL"
    assert 'src="/walkthrough.gd.vtt"' in refused_track, (
        "…while the tracks that pass the floor still are — a refusal drops ONE entry, never the menu"
    )


# ── The Phase 1110 track / transcript obligations ───────────────────────────


def _tracked(node_id: str, tracks: list) -> str:
    return render(fuaran.video(node_id, src="/walkthrough.mp4", label="Studio walkthrough", tracks=tracks))


def check_authored_child_order() -> None:
    # Authored in an order no sort produces: a `gd` subtitles track before an
    # `en` captions one before an `en` descriptions one. Alphabetical by srclang,
    # by label, or by kind would all move at least one entry, so any re-sort
    # shows up here.
    html = _tracked(
        "mvt",
        [
            track.subtitles(src="/restoration-2.gd.vtt", src_lang="gd", label="Gàidhlig"),
            track.captions(src="/restoration-2.en.vtt", src_lang="en", label="English captions", default=True),
            track.descriptions(src="/restoration-2.ad.vtt", src_lang="en", label="Audio description"),
        ],
    )

    gd = html.find("/restoration-2.gd.vtt")
    en = html.find("/restoration-2.en.vtt")
    ad = html.find("/restoration-2.ad.vtt")

    assert gd > -1, "the first authored track is emitted"
    assert gd < en, "the authored order is preserved — the gd track precedes the en one"
    assert en < ad, "…and the whole list, not merely its head"


def check_single_default_per_kind() -> None:
    # Two captions tracks both electing themselves default, plus a subtitles one
    # that also does. First-wins is PER KIND, so the subtitles election survives
    # beside the captions one and only the SECOND captions election is dropped.
    html = _tracked(
        "mvd",
        [
            track.captions(src="/a.en.vtt", src_lang="en", label="English captions", default=True),
            track.captions(src="/b.en.vtt", src_lang="en", label="English captions (verbose)", default=True),
            track.subtitles(src="/c.gd.vtt", src_lang="gd", label="Gàidhlig", default=True),
        ],
    )

    assert html.count("default=") == 2, (
        "one default survives per KIND — the captions duplicate loses, the subtitles election does not"
    )
    assert "/b.en.vtt" in html, "the losing track is still EMITTED; only its claim on the menu is dropped"

    # The twin without which a renderer that dropped EVERY default would pass.
    single = _tracked("mvd1", [track.captions(src="/a.en.vtt", src_lang="en", label="English captions", default=True)])

    assert "default=" in single, "a lone election is honoured"


def check_transcript_disclosure_named() -> None:
    with_transcript = render(
        fuaran.audio(
            "mat",
            src="/commentary.mp3",
            label="Curator commentary",
            transcript="The harbour was rebuilt twice.",
        )
    )

    assert "<details" in with_transcript, "a declared transcript renders as a disclosure"
    assert "fuaran-media-transcript" in with_transcript, "…carrying the media-scoped class, not the Disclosure kind's"
    assert "The harbour was rebuilt twice." in with_transcript, (
        "…and the transcript text itself, which is the document's content"
    )
    assert 'aria-label="Curator commentary"' in with_transcript, (
        "the disclosure carries the media's resolved label as its accessible name"
    )

    # BESIDE, not inside: a <details> within a media element is fallback content
    # a browser never shows, so the ordering here is the whole obligation.
    assert with_transcript.index("<audio") < with_transcript.index("<details"), (
        "the disclosure sits beside the transport, after it"
    )
    assert "</audio>" in with_transcript
    assert with_transcript.index("</audio>") < with_transcript.index("<details"), (
        "…and OUTSIDE it — inside, a browser treats it as fallback content and never shows it"
    )

    # The absent twin: no transcript, no disclosure and no wrapper. Without it a
    # renderer that always emitted the group would pass everything above.
    without = render(fuaran.audio("mat0", src="/commentary.mp3", label="Curator commentary"))

    assert "<details" not in without, "no transcript, no disclosure"
    assert "fuaran-media-group" not in without, "…and no group wrapper either"


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
# ── The Phase 1111 embed obligations ────────────────────────────────────────


def check_embed_accessible_name_always() -> None:
    named = render(fuaran.embed("em", src="https://example.com/x", title="Quarterly figures"))

    assert 'title="Quarterly figures"' in named, "the frame carries its accessible name"

    # The EMPTY case is the one that matters. A frame is a focus container a
    # reader tabs INTO, so it is never decorative — unlike an image, which can
    # honestly say so with an empty `alt`. The attribute is emitted whatever it
    # resolves to, and a stated empty is not the same claim as an absent one.
    empty = render(fuaran.embed("em2", src="https://example.com/x", title=""))

    assert 'title=""' in empty, "an empty title is still EMITTED, never dropped"


def check_embed_sandbox_always_exactly_declared() -> None:
    none_granted = render(fuaran.embed("es", src="https://example.com/x", title="Figures"))

    # EMPTY and PRESENT. Omitting it on a permissionless embed produces the same
    # markup as an UNSANDBOXED frame — the one mistake here that grants
    # everything while looking like it grants nothing.
    assert 'sandbox=""' in none_granted, "the sandbox attribute is emitted even when nothing is granted"

    # Authored OUT of declaration order and with a duplicate, so this pins the
    # ORDER and the DE-DUPLICATION rather than the token set: two documents
    # naming the same set must produce identical markup whatever order they
    # authored.
    some = render(
        fuaran.embed(
            "es2",
            src="https://example.com/x",
            title="Figures",
            permissions=["AllowForms", "AllowScripts", "AllowScripts"],
        )
    )

    assert 'sandbox="allow-scripts allow-forms"' in some, "tokens ride in DECLARATION order, de-duplicated"
    assert "allow-same-origin" not in some, (
        "…and never a token the document did not name — AllowSameOrigin plus AllowScripts "
        "is how a framed page removes its own sandbox"
    )

    # Fullscreen is a PERMISSIONS-POLICY directive and rides `allow`, not
    # `sandbox`. Emitting it as a sandbox token would grant nothing while looking
    # like it granted something.
    fullscreen = render(
        fuaran.embed("es3", src="https://example.com/x", title="Figures", permissions=["AllowFullscreen"])
    )

    assert 'allow="fullscreen"' in fullscreen, "fullscreen is a permissions-policy directive"
    assert 'sandbox=""' in fullscreen, "…and is NOT a sandbox token"


def check_refused_embed_source_omitted() -> None:
    # `http` — the embed class admits `https` and nothing else, because a
    # same-origin frame is where a sandbox can be removed from the inside.
    refused = render(fuaran.embed("er", src="http://example.com/x", title="Figures"))

    assert "src=" not in refused, (
        "the source attribute is OMITTED ENTIRELY rather than pointed at the refusal URL — "
        "an <img> at that URL shows a broken image, but an <iframe> at it RENDERS THAT PAGE"
    )
    assert "data-fuaran-egress-refused" in refused, "…while the refusal is still RECORDED"
    assert 'title="Figures"' in refused, "and the frame is still emitted, named and sandboxed"

    # The twin. Without it a renderer that dropped the whole node — or refused
    # every scheme — would satisfy the assertions above by a worse bug.
    allowed = render(
        fuaran.embed("ea", src="https://example.com/x", title="Figures"),
        egress_policy=PERMISSIVE_EGRESS,
    )

    assert 'src="https://example.com/x"' in allowed, "a source the floor admits still rides"


# ── The Phase 1115 / 1116 / 1117 upload obligation ──────────────────────────


def check_picker_always_present() -> None:
    # A DECLARED INGRESS GESTURE IS ADDITIONAL. Whatever the document declares —
    # a drop zone, a paste route, a capture device, a streamed destination — the
    # file picker and its label are emitted, so the keyboard-accessible route
    # survives and a no-script host renders a working upload. There is no
    # keyboard equivalent of a drag, so a control that swapped the picker for a
    # drop zone would have removed the only route some readers have.
    variants: dict[str, dict[str, object]] = {
        "up-plain": {},
        "up-drop": {"drop_target": True},
        "up-paste": {"accept_paste": True},
        "up-both": {"drop_target": True, "accept_paste": True},
        "up-capture": {"capture": "Camera"},
        "up-dest": {"destination": "session-recordings"},
    }
    for node_id, extra in variants.items():
        html = render(fuaran.file_upload(node_id, label="Upload", **extra))  # type: ignore[arg-type]
        assert 'type="file"' in html, f"{node_id}: the file input is emitted"
        assert "fuaran-file-upload-label" in html, f"{node_id}: and its label with it"

    # fuaran#1116's own half: the keyword names a camera FACING, and the DEVICE
    # NAME is not a keyword and is non-conforming markup.
    camera = render(fuaran.file_upload("upc", label="Photograph the receipt", capture="Camera"))

    assert 'capture="environment"' in camera, "Camera projects to the rear-facing keyword"
    assert "Camera" not in camera.split("capture=")[1][:20], "…never the wire spelling, which is not a keyword"


# ── The fuaran#1548 ceiling obligation ──────────────────────────────────────


def check_ceiling_recorded_never_enforced() -> None:
    # TWO claims in one, and the second is what a marker-emission test alone
    # would miss: the marker records only THAT a ceiling was declared, so a host
    # that put the NUMBER in the markup would pass an emission assertion while
    # telling a reader this tier enforces a bound it cannot enforce at all.
    # Nothing on this path can act on the number — HTML has no attribute for a
    # byte ceiling, and `multiple` is a boolean rather than a count.
    ceiling_bytes = render(fuaran.file_upload("ucb", label="Attach a scan", max_bytes=5_242_880))

    assert 'data-fuaran-upload-max-bytes="declared"' in ceiling_bytes, (
        "a declared byte ceiling is recorded, so the declaration is visibly read rather than dropped"
    )
    assert "5242880" not in ceiling_bytes, (
        "...and its VALUE is nowhere in the markup - carrying it would claim an enforcement that is not there"
    )
    assert "data-fuaran-upload-max-files" not in ceiling_bytes, (
        "...and the count marker is absent when the count member is, so the two are recorded independently"
    )

    ceiling_files = render(fuaran.file_upload("ucf", label="Attach up to three", multiple=True, max_files=3))

    assert 'data-fuaran-upload-max-files="declared"' in ceiling_files, (
        "a declared count ceiling is recorded on the same terms"
    )
    assert "data-fuaran-upload-max-bytes" not in ceiling_files, (
        "...and the byte marker is absent when the byte member is"
    )

    # The polarity, which is what makes the members additive: an upload
    # declaring neither is byte-identical in render to what it always was.
    plain = render(fuaran.file_upload("ucp", label="Upload"))

    assert "data-fuaran-upload-max-" not in plain, "an upload declaring no ceiling carries no ceiling marker at all"
    assert 'type="file"' in plain and 'type="file"' in ceiling_bytes, (
        "and a declared ceiling changes nothing about the control itself"
    )


# ── The Phase 1119 modality obligation ──────────────────────────────────────


def check_aria_modal_only_when_blocking() -> None:
    blocking = render(fuaran.modal("md", heading="Confirm", open=True))

    assert 'role="dialog"' in blocking, "the blocking surface is a dialog"
    assert 'aria-modal="true"' in blocking, "…and claims the page behind it is INERT"

    popover = render(fuaran.modal("mp", heading="Details", open=True, modality="Popover"))

    # The claim is about INERTNESS, and a non-blocking anchored surface leaves
    # the page genuinely available. A host emitting `aria-modal` here tells
    # assistive technology the rest of the page is unreachable when it is not.
    assert 'role="dialog"' in popover, "the anchored surface still carries the dialog role"
    assert "aria-modal" not in popover, "…but never the inertness claim"


# ── The Phase 1120 tree obligation ──────────────────────────────────────────


def check_tree_accessible_name_always() -> None:
    html = render(
        fuaran.tree(
            "tr",
            items=[
                t.TreeItem("goods", t.LiteralText("Goods"), (t.TreeItem("cocoa", t.LiteralText("Cocoa")),)),
                t.TreeItem("ledger", t.LiteralText("Ledger")),
            ],
        )
    )

    # STATED rather than computed. A `treeitem` OWNS its child group, so a name
    # derived from contents reads the whole branch out as the row's own name —
    # "Goods Cocoa" for the parent here. Asserting the parent's name is exactly
    # its own visible label is what catches that.
    assert 'aria-label="Goods"' in html, "the parent row is named by its OWN label, not its subtree"
    assert 'aria-label="Ledger"' in html, "a leaf row is named too — every instance, never only some"
    assert '<span class="fuaran-tree-label">Goods</span>' in html, (
        "…and the stated name is byte-identical to the visible label, so 'label in name' holds"
    )


# ── fuaran#1696 — the `style.direction` TRAIT (§3.1) ────────────────────────
#
# A trait rides the node ENVELOPE, so these five checkers are written against a
# kind chosen for being uninteresting: the claims are about the wrapper, and a
# checker leaning on some kind's own markup would be asserting that kind.
#
# They decode RAW canonical JSON rather than authoring through ``fuaran``, and
# that is load-bearing for rule 4. This host's encoder omits ``direction`` at its
# ``auto`` identity, so an authored round trip would compare an emission against
# itself and the claim would be vacuous — the CODEC would be under test, not the
# renderer. A document that carries the member explicitly is the only input that
# asks the render path the question.
#
# Two of the five are COMPARISONS rather than emission assertions, and that is
# what makes them checkable at all. Rule 4 says ``auto`` is the absence of a
# declaration, so the honest test is that the two emissions are byte-identical:
# the reference host emits ``dir="auto"`` for a bidi-isolated display leaf under
# a heuristic this host has deliberately not adopted, so "emits nothing" would be
# a claim that means different things on different hosts. Rule 5 says nothing
# else is derived, and the test is that a declared emission differs from the
# undeclared one by the direction and its isolation ALONE — a subtraction no
# single-node assertion can express.


def render_json(canonical: str) -> str:
    """Decode canonical wire JSON and render it, with no authoring surface between."""
    decoded = decode_node(canonical)
    assert decoded.ok, getattr(decoded, "error", decoded)
    return render_html(decoded.value)


def direction_leaf(direction: str | None, text: str) -> str:
    """One leaf whose ``style.direction`` is as given; ``None`` omits the member."""
    style = "" if direction is None else f',"style":{{"direction":"{direction}"}}'
    return render_json(f'{{"id":"d","kind":{{"$type":"Badge","label":"{text}","variant":"Neutral"}}{style}}}')


def direction_block(child_direction: str | None) -> str:
    """An ``rtl`` container holding one child.

    The two claims a single leaf cannot carry — inheritance and descendant
    emission — need a tree to act on.
    """
    child_style = "" if child_direction is None else f',"style":{{"direction":"{child_direction}"}}'
    return render_json(
        '{"id":"block","kind":{"$type":"Box","children":['
        '{"id":"child","kind":{"$type":"Badge","label":"RR123456789IL","variant":"Neutral"}'
        f"{child_style}"
        '}],"layout":{"$type":"Flex","direction":"Vertical","wrap":false},"role":"Group"},'
        '"style":{"direction":"rtl"}}'
    )


def check_declared_direction_emitted() -> None:
    ltr = direction_leaf("ltr", "RR123456789IL")
    rtl = direction_leaf("rtl", "\u05e9\u05dc\u05d5\u05dd")
    assert ' dir="ltr"' in ltr, f"a declared ltr direction is emitted on the node's own wrapper: {ltr}"
    assert ' dir="rtl"' in rtl, f"…and so is a declared rtl one: {rtl}"

    # The twin. Without it a renderer emitting `dir="ltr"` on every node would
    # pass both assertions above while saying nothing true.
    undeclared = direction_leaf(None, "plain")
    assert " dir=" not in undeclared, f"an undeclared node must not carry a direction it never declared: {undeclared}"


def check_declared_run_isolated() -> None:
    # The ISOLATION is the class, whose reference-stylesheet rule is
    # `unicode-bidi: isolate`. `dir` alone states a direction and leaves the text
    # AROUND the run reordered, which is the half that is invisible when you look
    # only at the value itself.
    ltr = direction_leaf("ltr", "RR123456789IL")
    rtl = direction_leaf("rtl", "\u05e9\u05dc\u05d5\u05dd")
    assert "fuaran-dir-ltr" in ltr, f"a declared ltr run carries the isolating class: {ltr}"
    assert "fuaran-dir-rtl" in rtl, f"…and so does a declared rtl one: {rtl}"

    undeclared = direction_leaf(None, "plain")
    assert "fuaran-dir-" not in undeclared, (
        f"an undeclared node is isolated by nothing, because it declared nothing: {undeclared}"
    )


def check_declaration_wins_over_inference() -> None:
    # An `ltr` reference INSIDE an `rtl` block — the case the member exists for.
    html = direction_block("ltr")
    assert ' dir="rtl"' in html, f"the declaring container keeps its own direction: {html}"
    assert ' dir="ltr"' in html, (
        f"the nested declaration did not win over the inherited direction — the inference exists for "
        f"values whose direction is unknown, the declaration for the ones it gets wrong: {html}"
    )


def check_auto_is_no_declaration() -> None:
    explicit = direction_leaf("auto", "plain")
    omitted = direction_leaf(None, "plain")
    assert explicit == omitted, (
        "a node declaring `auto` must render identically to the same node omitting the member — "
        f"`auto` IS the absence of a declaration\nwith auto: {explicit}\nomitted:   {omitted}"
    )


def check_no_derived_direction_behaviour() -> None:
    # The SUBTRACTION: a renderer that also flipped an alignment, swapped a
    # layout side or pushed a direction onto descendants fails here and passes
    # every assertion above.
    declared = direction_leaf("rtl", "RR123456789IL")
    undeclared = direction_leaf(None, "RR123456789IL")
    stripped = declared.replace(' dir="rtl"', "", 1).replace(" fuaran-dir-rtl", "", 1)
    assert stripped == undeclared, (
        "a declared direction changed something other than the direction and its isolation — no layout "
        f"side, locale, alignment or descendant direction may be derived from it\nstripped:   {stripped}"
        f"\nundeclared: {undeclared}"
    )

    # …and the descendant half, stated separately because a single leaf cannot
    # carry it: an undeclared child inside a declaring parent emits no direction
    # of its own. Inheritance is the receiving surface's, not a second emission.
    html = direction_block(None)
    assert html.count(" dir=") == 1, (
        "exactly one element declared a direction, so exactly one may carry it — a direction pushed "
        f"onto descendants is a derived behaviour rule 5 forbids: {html}"
    )


# ── fuaran#1701 — the interactive-row class (§3.6.24) ───────────────────────
#
# Built from RAW canonical JSON, for the reason the direction checkers give:
# `onRowClick` is a closure-bearing slot whose whole wire content is its
# PRESENCE, so authoring it through a surface would put the surface under test
# rather than the renderer.

MARKER = "fuaran-grid-row-interactive"


def bound_grid(row_action: bool) -> str:
    """A data-bound grid over two static rows, with the row action declared or omitted."""
    action = '"onRowClick":"<closure>",' if row_action else ""
    return render_json(
        '{"id":"g","kind":{"$type":"DataGrid","columns":[{"field":"reference",'
        '"kind":{"$type":"Text"},"label":"Reference"}],'
        f"{action}"
        '"source":{"$type":"Static","value":[{"reference":"S-1"},{"reference":"S-2"}]}}}'
    )


def static_rows_grid(row_action: bool) -> str:
    """The same grid in ``staticRows`` mode, which honours no row action in any tier."""
    action = '"onRowClick":"<closure>",' if row_action else ""
    return render_json(
        '{"id":"g","kind":{"$type":"DataGrid","columns":[],'
        f"{action}"
        '"source":{"$type":"Static","value":[]},'
        '"staticRows":{"headers":["Reference"],"rows":[["S-1"]]}}}'
    )


def check_interactive_row_only_with_action() -> None:
    # Rule 1, both directions. An emission test alone cannot tell a renderer that
    # honours the declaration from one that marks every row.
    declared = bound_grid(True)
    assert MARKER in declared, (
        "a grid declaring a row action must mark its rows, so the pointer affordance keyed on the "
        f"marker promises a click the document declared: {declared}"
    )
    undeclared = bound_grid(False)
    assert MARKER not in undeclared, (
        "a grid declaring no row action must mark no row - a pointer over inert content is a "
        f"promise the markup does not keep: {undeclared}"
    )
    # ...and the rows are there either way, so the negative above is about the
    # DECLARATION rather than about an empty render.
    assert undeclared.count('<tr class="fuaran-grid-row"') == 2, undeclared

    # Rule 2 - the static leg renders real rows AND can read the declaration, and
    # must still mark none: the mode honours no row action in any tier, so a
    # marked row there would promise a click nothing can deliver.
    static_declared = static_rows_grid(True)
    assert "fuaran-table-row" in static_declared, static_declared
    assert MARKER not in static_declared, (
        "a `staticRows` grid honours no row action in any tier, so its rows carry no interactive-row "
        f"marker whatever the grid declares: {static_declared}"
    )
    assert MARKER not in static_rows_grid(False)


# ── fuaran#1704 — Sparkline float-sequence resolution (§24.7) ────────────────
#
# The claims are about a HOST-FED series, so these two checkers are the only ones
# in this file that render against a non-empty store. That is structural rather
# than convenient: a float-sequence slot TYPES its elements at decode, so
# ``[1,"3.5",3]`` is a WRONG_TYPE and no document can carry the case. The store
# is the only place a foreign element exists, which is why §24.7 is a render
# obligation and not a codec family.
#
# The observable is the emitted ``<polyline points="…">``: the lowering yields
# one point per series element, so counting points counts readings. An assertion
# on the em-dash alone could not tell a host that read every element from one
# that read the first two and gave up.
#
# The document is the corpus's own bound-source sparkline —
# ``nodes/state-absent-default.json``'s ``absent-default-sparkline``, reproduced
# here as one node so the checker renders the subject rather than digging it out
# of a six-node composite.
BOUND_SPARKLINE = (
    '{"id":"absent-default-sparkline","kind":{"$type":"Sparkline","source":{"$type":"State","key":"series"}}}'
)


def render_series(series: object) -> str:
    """Render the bound sparkline with ``series`` fed from the store."""
    decoded = decode_node(BOUND_SPARKLINE)
    assert decoded.ok, getattr(decoded, "error", decoded)
    return render_html(decoded.value, {"series": series})


def point_count(html: str) -> int:
    """How many readings the emission shows: one ``x,y`` pair per element."""
    match = re.search(r'points="([^"]*)"', html)
    return len(match.group(1).split()) if match else 0


def check_float_seq_reads_element_wise() -> None:
    finite = render_series([1.0, 2.0, 3.0, 4.0])
    assert point_count(finite) == 4, f"a four-element series must draw four readings: {finite}"

    # The element the rule is about: one the host cannot read as a number, among
    # readable neighbours. Several spellings, because a host special-casing
    # strings and one special-casing foreign types are different defects.
    for foreign in ("banana", True, None, {}, [1]):
        html = render_series([1.0, foreign, 3.0, 4.0])
        assert "fuaran-sparkline-empty" not in html, (
            f"one unreadable element ({foreign!r}) suppressed the whole series — the em-dash is the "
            f"UNRESOLVED case, not the partly-readable one; discarding the readable points tells the "
            f"reader nothing at all: {html}"
        )
        assert point_count(html) == 4, (
            f"an unreadable element ({foreign!r}) changed the series LENGTH — a series index is a "
            f"position, so a dropped reading slides every later one one place left: {html}"
        )


def check_float_seq_accept_set_closed() -> None:
    # The twin FIRST, so the comparison below is against a real render rather
    # than two em-dashes agreeing about nothing.
    genuine = render_series([0.0, 3.5, 7.0])
    assert point_count(genuine) == 3, f"the genuine number must be read — the closed set admits JSON numbers: {genuine}"

    # The comparison IS the claim, and it is the one formulation that reads the
    # same on every host: a host that coerced "3.5" emits byte-identical markup
    # for the two, whatever its geometry. Asserting the characters ``3.5`` are
    # absent would pass on a host that coerced and then scaled the coordinate.
    for spelling in ("3.5", "+3.5", " 3.5 ", "1_0", "infinity", "nan"):
        coerced = render_series([0.0, spelling, 7.0])
        assert coerced != genuine, (
            f"the string {spelling!r} resolved to the number it spells — the accept set at this slot "
            f"is §7's and closed, and this host's own decoder refuses exactly this spelling, so "
            f"accepting it here makes the two halves of one slot disagree"
        )

    # …and the three the set DOES admit, in the same shape. Without them the
    # claim above would be satisfied by a host that read no string at all,
    # including the sentinels the format exists to spell.
    for sentinel in ("NaN", "Infinity", "-Infinity"):
        html = render_series([1.0, sentinel, 3.0])
        assert point_count(html) == 3, (
            f"the sentinel {sentinel!r} is IN the accept set and must read as its non-finite value: {html}"
        )


CHECKERS: Mapping[str, Callable[[], None]] = {
    "Media/accessible-name-always": check_accessible_name_always,
    "Media/autoplay-muted-pairing": check_autoplay_muted_pairing,
    "Media/no-autoplay-pathway": check_no_autoplay_pathway,
    "Media/refused-source-dropped": check_refused_source_dropped,
    # fuaran#1110 — the timed-text tracks and the transcript.
    "Media/authored-child-order": check_authored_child_order,
    "Media/single-default-per-kind": check_single_default_per_kind,
    "Media/transcript-disclosure-named": check_transcript_disclosure_named,
    "Image/alt-always-emitted": check_alt_always_emitted,
    "Image/anchor-affordance-on-expandable": check_anchor_affordance_on_expandable,
    "Image/refused-src-no-affordance": check_refused_src_no_affordance,
    "Image/figure-caption-outside-link": check_figure_caption_outside_link,
    "Image/srcset-ascending-by-width": check_srcset_ascending_by_width,
    "Custom/unregistered-custom-labelled": check_unregistered_custom_labelled,
    # fuaran#1128 — the platform-baseline wave's own obligations. Each arrived
    # here as a claim with no checker on the day the manifest declared it, which
    # is the fuaran#1109 mechanism working as designed: a newly declared
    # obligation turns this suite RED rather than sitting in a paragraph.
    "Embed/accessible-name-always": check_embed_accessible_name_always,
    "Embed/sandbox-always-exactly-declared": check_embed_sandbox_always_exactly_declared,
    "Embed/refused-embed-source-omitted": check_refused_embed_source_omitted,
    "FileUpload/picker-always-present": check_picker_always_present,
    "FileUpload/ceiling-recorded-never-enforced": check_ceiling_recorded_never_enforced,
    "Modal/aria-modal-only-when-blocking": check_aria_modal_only_when_blocking,
    "Tree/accessible-name-always": check_tree_accessible_name_always,
    # fuaran#1696 — the node-level TRAIT, keyed by its id rather than a kind.
    # The dot is what keeps the two subject populations distinguishable in one
    # registry: a trait id is the wire path of the member it governs, and a
    # `kind.$type` is a bare identifier.
    "style.direction/declared-direction-emitted": check_declared_direction_emitted,
    "style.direction/declared-run-isolated": check_declared_run_isolated,
    "style.direction/declaration-wins-over-inference": check_declaration_wins_over_inference,
    "style.direction/auto-is-no-declaration": check_auto_is_no_declaration,
    "style.direction/no-derived-direction-behaviour": check_no_derived_direction_behaviour,
    # fuaran#1701 - the row-action affordance.
    "DataGrid/interactive-row-only-with-action": check_interactive_row_only_with_action,
    # fuaran#1704 — the two float-sequence resolution claims (§24.7).
    "Sparkline/float-seq-reads-element-wise": check_float_seq_reads_element_wise,
    "Sparkline/float-seq-accept-set-closed": check_float_seq_accept_set_closed,
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
def test_every_declared_trait_scope_is_actionable() -> None:
    """``appliesTo`` decides whether a trait claim is OWED on this host at all.

    One riding only kinds this host does not render owes nothing; one riding the
    envelope is owed by everything. A scope this host cannot interpret is
    therefore not a cosmetic defect — it is an unanswerable question about
    whether the gate should be red.

    Both arms are asserted in BOTH directions, because the tagged shape exists
    precisely so that "every kind" is not spellable as an empty array: an
    ``allKinds`` carrying a list, or a ``namedKinds`` carrying none, would each
    read as the opposite of what it says.
    """
    manifest = load()
    kind_names = {row.kind for row in manifest.kinds}

    for row in manifest.traits:
        assert "." in row.trait, (
            f"a trait id is the wire path of the member it governs, never a bare kind name: {row.trait}"
        )
        assert row.trait not in kind_names, f"{row.trait} collides with a kind name; one registry keys both populations"
        if row.scope == "allKinds":
            assert not row.scope_kinds, (
                f"{row.trait}: an allKinds scope names no kinds - a list would be a narrower claim "
                f"than the scope itself: {row.scope_kinds}"
            )
        elif row.scope == "namedKinds":
            assert row.scope_kinds, (
                f"{row.trait}: a namedKinds scope with an empty list rides NOTHING, which is "
                "satisfiable by rendering nothing at all"
            )
        else:  # pragma: no cover - the parser refuses any other token
            raise AssertionError(
                f"{row.trait}: this host cannot interpret the scope {row.scope!r}, so it cannot say "
                "whether the trait's claims are owed here"
            )


@artifact_required
def test_registers_no_checker_for_an_obligation_the_manifest_does_not_declare() -> None:
    # A checker for a claim no row declares is a stale assertion: it passes
    # forever and guards a contract that has moved, which is exactly the drift the
    # generated artefact exists to remove.
    manifest = load()
    # BOTH subject populations: a checker keyed by a trait id is exactly as
    # orphanable as one keyed by a kind name, and quantifying over `kinds` alone
    # would report every trait checker as stale.
    declared = {f"{subject}/{obligation.id}" for subject, obligation in all_obligations(manifest)}
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
