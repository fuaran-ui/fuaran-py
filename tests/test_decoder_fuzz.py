"""Decoder robustness fuzz — the gate leg + the go-red self-test.

The bounded run below is what joins this repo's PR gate; the long form is
``python -m fuaran_py.conformance.decoder_fuzz --long --iterations 250000
--evidence <file>``, which a scheduled job runs so the published totality figures
cannot age quietly.

Three things this file asserts that the bounded run alone would not:

* **The harness goes red on a broken decoder.** Five mutants, one per invariant.
  Permanent, not a one-off demonstration at authoring time — a fuzz harness
  nobody has ever seen fail is decoration.
* **The mutants are PARTIAL.** A mutant that broke every input would satisfy
  every go-red test while proving only that the harness reports what it is
  handed.
* **Allocation, measured for real.** ``check`` substitutes a canonical-output
  amplification bound for the reference host's per-thread allocated-bytes
  counter, because this runtime has no free equivalent. The dedicated
  ``tracemalloc`` leg here covers the question that substitution leaves open —
  over the pathological family, where an adversarial shape would amplify — at a
  cost the whole run could not carry.
"""

from __future__ import annotations

import tracemalloc

import pytest

from _corpus import CORPUS_ROOT, corpus_available
from fuaran_py.conformance.decoder_fuzz import (
    BOUNDED_CONFIG,
    DEFAULT_BUDGETS,
    REAL_SUBJECTS,
    Budgets,
    Config,
    Rng,
    Subject,
    SubjectResult,
    check,
    generate,
    load_seeds,
    load_vocabulary,
    run,
    summarise,
)

CORPUS = CORPUS_ROOT if corpus_available() else None
SEEDS = load_seeds(CORPUS)
VOCAB = load_vocabulary(CORPUS)

#: A FIXED seed: the gate must be reproducible, so a red build is the same red
#: build on the next run.
SEED = 1023
ITERATIONS = 4000


def test_refusal_contract_holds_over_generated_hostile_input(capsys: pytest.CaptureFixture[str]) -> None:
    stats = run(REAL_SUBJECTS, DEFAULT_BUDGETS, BOUNDED_CONFIG, SEED, ITERATIONS, SEEDS, VOCAB, minimise_finds=True)

    with capsys.disabled():
        # Printed on every run, pass or fail: a harness whose output is only
        # visible when it fails cannot be checked for having quietly stopped
        # generating anything.
        print(f"\n  [decoder-fuzz] {summarise(stats)}")

    if stats.counterexamples:
        detail = "\n\n".join(c.describe() for c in stats.counterexamples[:5])
        pytest.fail(
            f"{len(stats.counterexamples)} counterexample(s) — the decoder's refusal contract does not "
            f"hold over generated hostile input.\n\n{detail}"
        )

    # A run that generated nothing would report zero counterexamples and look
    # identical to a clean one. Pin the work actually done.
    assert stats.iterations == ITERATIONS
    assert stats.inputs == ITERATIONS * len(REAL_SUBJECTS)
    # Both outcomes must occur. A stream that only ever refuses never reaches the
    # fixed-point invariant; one that only ever accepts is not hostile.
    assert stats.accepted > 0
    assert len(stats.reject_codes) > 0


# ── Go-red: the harness fails when the decoder is broken ────────────────────

_OK_RESULT = SubjectResult(refused_code="INVALID_JSON")

#: The slow mutant is measured against a DELIBERATELY TIGHT budget rather than
#: the shipped three-second one. Sleeping past the real budget would cost three
#: seconds per firing — the sort of cost that gets a go-red test deleted rather
#: than fixed. What is under test is the harness's ability to see a decode that
#: returned past ITS budget, and that is exactly as true at 5 ms as at 3 s.
_TIGHT_TIME = Budgets(soft_time_ms=5.0)


def _every_nth(n: int, name: str, broken) -> Subject:  # noqa: ANN001 — a local test factory
    """Fires only on inputs whose length is divisible by ``n`` — partial by design."""
    return Subject(name=name, run=lambda text: broken(text) if len(text) % n == 0 else _OK_RESULT)


def _throws(_text: str) -> SubjectResult:
    raise TypeError("deliberate: the decoder let an exception escape")


def _slow(_text: str) -> SubjectResult:
    import time

    time.sleep((_TIGHT_TIME.soft_time_ms + 20) / 1000.0)
    return _OK_RESULT


def _amplifies(_text: str) -> SubjectResult:
    big = "x" * (DEFAULT_BUDGETS.amplification_floor_chars + 1)
    return SubjectResult(refused_code=None, canonical=big, re_decoded=big)


def _canonical_refused(_text: str) -> SubjectResult:
    return SubjectResult(refused_code=None, canonical="{}", re_decoded=None, re_decoded_code="INVALID_JSON")


def _fixed_point_broken(_text: str) -> SubjectResult:
    return SubjectResult(refused_code=None, canonical='{"a":1}', re_decoded='{"a":2}')


MUTANTS: tuple[tuple[Subject, Budgets], ...] = (
    (_every_nth(3, "mutant:throws", _throws), DEFAULT_BUDGETS),
    (_every_nth(5, "mutant:slow", _slow), _TIGHT_TIME),
    (_every_nth(7, "mutant:amplifies", _amplifies), DEFAULT_BUDGETS),
    (_every_nth(11, "mutant:canonical-refused", _canonical_refused), DEFAULT_BUDGETS),
    (_every_nth(13, "mutant:fixed-point-broken", _fixed_point_broken), DEFAULT_BUDGETS),
)


@pytest.mark.parametrize(("mutant", "budgets"), MUTANTS, ids=lambda x: x.name if isinstance(x, Subject) else "")
def test_the_harness_goes_red_on_a_broken_decoder(mutant: Subject, budgets: Budgets) -> None:
    stats = run([mutant], budgets, BOUNDED_CONFIG, SEED, 200, SEEDS, VOCAB, minimise_finds=False)
    assert stats.counterexamples, f"{mutant.name} produced no counterexample — the harness cannot see this defect class"


@pytest.mark.parametrize(("mutant", "budgets"), MUTANTS, ids=lambda x: x.name if isinstance(x, Subject) else "")
def test_the_mutants_are_partial(mutant: Subject, budgets: Budgets) -> None:
    # The inverse pin. Without it, a mutant that failed EVERYTHING would satisfy
    # every go-red test above while proving nothing about discrimination.
    stats = run([mutant], budgets, BOUNDED_CONFIG, SEED, 200, SEEDS, VOCAB, minimise_finds=False)
    assert len(stats.counterexamples) < stats.inputs, (
        f"{mutant.name} broke EVERY input — it proves nothing about the harness's discrimination"
    )


def test_a_well_formed_node_is_neither_a_refusal_nor_a_counterexample() -> None:
    # The floor under everything above: the machinery must call a GOOD input
    # good. A harness that reported every input as a counterexample would pass
    # every go-red test in this file.
    good = '{"id":"a","kind":{"$type":"Heading","level":1,"text":"x","variant":"Standard"}}'
    measured = check(REAL_SUBJECTS[0], DEFAULT_BUDGETS, good)
    assert measured.verdict.kind == "clean", f"a well-formed node decoded as {measured.verdict.kind!r}"


# ── The allocation invariant, measured rather than approximated ─────────────


def test_pathological_inputs_stay_inside_an_allocation_budget() -> None:
    """The reference host's third invariant, on the family where it bites.

    ``tracemalloc`` measures allocation faithfully and roughly doubles the run,
    so it is spent here rather than across every iteration: the pathological
    family is precisely where an adversarially-shaped small document could
    amplify into a large heap, and it is the only family whose cost is worth
    paying to see.

    The budget is a floor plus a per-character rate, and the two bind in
    different places. Below the floor the fixed cost of a decode dominates and
    per-character ratios are meaningless (a 100-character input legitimately
    allocates a thousand times its own length); above it the rate binds, and it
    is the rate that catches super-linear work on large inputs.
    """
    floor_bytes = 16 * 1024 * 1024
    per_char = 512
    cfg = Config(name="pathological-probe", max_payload_chars=64 * 1024, heavy_every_n=1)
    rng = Rng(SEED)

    worst_ratio = 0.0
    for i in range(1, 41):
        payload = generate(rng, SEEDS, VOCAB, cfg, i).payload
        if not payload:
            continue
        tracemalloc.start()
        try:
            for subject in REAL_SUBJECTS:
                check(subject, DEFAULT_BUDGETS, payload)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        budget = max(floor_bytes, per_char * len(payload))
        worst_ratio = max(worst_ratio, peak / len(payload))
        assert peak <= budget, (
            f"a {len(payload)}-character pathological input peaked at {peak} bytes, budget {budget} "
            f"({peak / len(payload):.0f} bytes per input character)"
        )

    # A probe that measured nothing would pass this test silently.
    assert worst_ratio > 0.0
