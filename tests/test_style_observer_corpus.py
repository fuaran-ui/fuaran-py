"""This host's certification against the shared ``style-observer/`` family (Phase 1752).

Until this file existed, the byte-identical ``StyleFlag`` / ``StyleObservation``
encode was a cross-host law only because four hosts had each written the same
literals into their own test files — ``test_style_observer.py`` here, the Rust
port beside it, the Go tier measured by eye, and the reference tier's own suite.
Four suites that agree are not an oracle: a fifth host has nothing to certify
against but the other four's tests, and a regression introduced in all four at
once, by the port that created them, is invisible from every one of them.

So the cases moved into the corpus, and this reads them. The literals in
``test_style_observer.py`` stay exactly where they are and are now the go-red
partner: they are written by hand, this is written by the reference emitter, and
a divergence between the implementation and the family reds one of the two.

**Not checked is not passed.** A tier or a flag kind this host does not
understand is REPORTED by name with the vector id — never skipped — and the
vacuity guard below refuses a run in which the family turned out to be empty,
because "every vector matched" and "no vector was loaded" are the same green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _corpus import CORPUS_ROOT, corpus_required, fixtures_of
from fuaran_ui.style_observer import (
    FontRole,
    StyleInput,
    StyleObservation,
    StyleObserverOptions,
    derive_style_flags,
    encode_style_flag,
    encode_style_observation,
    per_node_flags,
    rgba,
    to_style_observation,
    verify_usage_budgets,
)
from fuaran_ui.theme_manifest import decode as decode_manifest

KNOWN_TIERS = {"observation", "per-node-manifest", "usage-budget"}


def _load(fixture: dict) -> dict:
    return json.loads((CORPUS_ROOT / fixture["inputFile"]).read_text(encoding="utf-8"))


def _rgba(raw: dict) -> object:
    return rgba(raw["r"], raw["g"], raw["b"], raw["a"])


def _options(raw: dict) -> StyleObserverOptions:
    return StyleObserverOptions(
        contrast_aa_threshold=raw["contrastAaThreshold"],
        invisible_text_threshold=raw["invisibleTextThreshold"],
        accent_indistinct_threshold=raw["accentIndistinctThreshold"],
    )


def _observation(raw: dict) -> StyleObservation:
    """A snapshot spelled out by the fixture — the manifest-aware tiers take an
    already-derived observation, so its flag list is deliberately empty."""
    return StyleObservation(
        node_id=raw["nodeId"],
        foreground=_rgba(raw["foreground"]),
        effective_background=_rgba(raw["effectiveBackground"]),
        font_role=FontRole(raw["fontRole"]),
        emitted_tone=raw["emittedTone"],
        contrast_ratio=raw["contrastRatio"],
        flags=[],
    )


def _style_input(raw: dict) -> StyleInput:
    return StyleInput(
        foreground=_rgba(raw["foreground"]),
        background_layers=[_rgba(layer) for layer in raw["backgroundLayers"]],
        font_family=raw["fontFamily"],
        emitted_tone=raw["emittedTone"],
    )


_FIXTURES = fixtures_of("style-observer")


@corpus_required
def test_the_family_is_not_empty() -> None:
    """The guard that stops every assertion below from being vacuously green.

    A checker driven by a parametrised list over an empty corpus reports total
    success having compared nothing, which is indistinguishable from a passing
    run right up until somebody looks.
    """
    assert _FIXTURES, (
        f"manifest.json at {CORPUS_ROOT} lists no style-observer fixtures — this suite would have "
        "certified nothing while reporting success"
    )
    for fixture in _FIXTURES:
        assert (CORPUS_ROOT / fixture["inputFile"]).is_file(), (
            f"{fixture['id']}: manifest.json lists {fixture['inputFile']}, which is not on disk"
        )


@corpus_required
@pytest.mark.parametrize("fixture", _FIXTURES, ids=lambda fx: fx["id"])
def test_style_observer_vector(fixture: dict) -> None:
    case = _load(fixture)
    tier = case["tier"]
    assert tier in KNOWN_TIERS, (
        f"{fixture['id']}: tier {tier!r} is outside this host's vocabulary. Reported by name rather "
        "than skipped — not checked is not passed"
    )

    if tier == "observation":
        options = _options(case["options"])
        style_input = _style_input(case["input"])

        flags = derive_style_flags(options, style_input)
        assert [encode_style_flag(f) for f in flags] == case["expectedFlags"], (
            f"{fixture['id']}: the derived flags do not encode to the family's bytes"
        )

        observation = to_style_observation(options, case["nodeId"], style_input)
        assert encode_style_observation(observation) == case["expectedObservation"], (
            f"{fixture['id']}: the encoded observation is not byte-identical to the family's"
        )

    elif tier == "per-node-manifest":
        manifest = decode_manifest(case["manifest"])
        flags = per_node_flags(manifest, _observation(case["observation"]))
        assert [encode_style_flag(f) for f in flags] == case["expectedManifestFlags"], (
            f"{fixture['id']}: the manifest-aware per-node flags do not encode to the family's bytes"
        )

    else:
        manifest = decode_manifest(case["manifest"])
        nodes = [(_observation(entry["observation"]), entry["area"]) for entry in case["nodeAreas"]]
        flags = verify_usage_budgets(manifest, nodes)
        assert [encode_style_flag(f) for f in flags] == case["expectedBudgetFlags"], (
            f"{fixture['id']}: the usage-budget flags do not encode to the family's bytes"
        )


@corpus_required
def test_go_red_a_single_flipped_expected_byte_is_caught(tmp_path: Path) -> None:
    """The proof that the comparison above can fail.

    A byte comparison falls silently into certifying nothing — an absent corpus,
    an empty enumeration, a list that was skipped — and every one of those looks
    like a pass. So one fixture is perturbed by a single digit and fed through
    the same code path, which must reject it.
    """
    observation_cases = [fx for fx in _FIXTURES if _load(fx)["tier"] == "observation"]
    assert observation_cases, "no observation-tier vector to perturb — the probe measured nothing"

    case = _load(observation_cases[0])
    genuine = case["expectedObservation"]
    perturbed = genuine.replace('"contrastRatio":21.00', '"contrastRatio":21.01')
    assert perturbed != genuine, (
        "the perturbation changed nothing, so this test proves nothing — the probe, not the subject, failed"
    )

    options = _options(case["options"])
    produced = encode_style_observation(to_style_observation(options, case["nodeId"], _style_input(case["input"])))
    assert produced == genuine, "the unperturbed vector should still pass"
    assert produced != perturbed, "a flipped expectation byte compared equal to what this host produces"
