# Decoder robustness: the long-run record

`decoder-robustness.json` beside this file is **generated** by this host's own fuzz leg and
rewritten in full by the run that produces it; do not hand-edit it. It records one long run:
the seed, the iteration count, how many generated inputs each decode entry point saw, the
refusal codes they answered with, the largest single decode time, the worst canonical
amplification, and the number of counterexamples (an escaping exception, a hang, a budget
breach, or an accepted input whose canonical form does not re-decode). Zero counterexamples
is the claim under test: decoding is total over hostile input.

Regenerate it with the command below from the repository root, replacing `<seed>` (the
committed record names the seed it was produced with, so the same stream can be replayed):

```
python -m fuaran_ui.conformance.decoder_fuzz --long --iterations 250000 --seed <seed> --evidence docs/decoder-robustness.json
```

The bounded form of the same leg runs on every pull request with a fixed seed. The five
input families and the four invariants are those of the reference host's harness; the
generator is a sibling per host rather than one shared byte stream, so two hosts' records
are comparable by classification and not by identical inputs.
