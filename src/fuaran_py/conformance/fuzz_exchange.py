"""Cross-host fuzz-sample exchange — the Python half (Leg F).

The fixed corpus pins each host to a curated set of named traps. This exchange
pins two hosts *to each other* over the **generated** tree-space the curated
fixtures cannot reach, and it does so in a way no single-host suite can: one
host's canonical bytes are checked by a *different* host's codec.

The counterpart on the F# side is
``fuaran-dotnet/src/Fuaran.UI.JsonDecode.Tests/FuzzSamples.fs``; the counterpart
on the TypeScript side is ``wire-format-fixtures/conformance/property-cross-host.mjs``.
This module is the exact mirror of the latter, for this host.

Two legs, run in this order::

    # Leg E' — emit the F#-canonical samples (writes <dir>/fsharp/)
    dotnet run --project ../fuaran-dotnet/src/Fuaran.UI.JsonDecode.Tests -c Release \\
        -- --emit-fuzz-samples <dir> 300

    # Leg F — F# → Python (this module): decode + re-encode each F#-canonical
    #         sample through the Python codec, assert byte-identity, and write
    #         the Python-canonical output to <dir>/python/
    python -m fuaran_py.conformance.fuzz_exchange <dir>

    # Leg G — Python → F# (the converse): the F# codec re-encodes this host's
    #         genuine encoder output byte-for-byte
    dotnet run --project ../fuaran-dotnet/src/Fuaran.UI.JsonDecode.Tests -c Release \\
        -- --check-fuzz-samples <dir> python

``F`` ⟹ Python canonical == F# canonical and ``G`` ⟹ F# canonical == Python
canonical over the generated space, both directions. A one-byte divergence in
either host's codec turns the gate red and names the sample plus the first
differing byte.

**Not a substitute for the within-host floor.** ``tests/test_generative_parity.py``
asserts this host's own ``encode ∘ decode`` is a fixed point over ≥1000 generated
trees. That proves Python is self-consistent — never that it *agrees with another
host*. The two are complementary and neither subsumes the other.

``<dir>`` defaults to ``../wire-format-fixtures/conformance/fuzz-samples`` (the
canonical side-by-side workspace layout; the directory is git-ignored in the
corpus repo — the samples are generated, never committed).

**Known open divergence — the `UpdateState` canonicalisation gap (F# side).**
The exchange found one on its first run, and it reproduces on every fresh draw
(1–4 samples in 600, always an ``op-`` sample, never a ``node-`` one). The shape:
a ``Filters`` chip whose control carries a `value` binding that is *exactly* the
chip's own auto-binding — ``{"$type":"Filter","name":<the chip's own name>}``.
``WIRE_FORMAT.md`` §"Filter chips are FormFieldKind controls" makes the encoder
side normative — it "symmetrically **omits** a `value` that is exactly that auto
binding" — and this host does. The F# encoder does too, on every path *except*
one: its op codec routes `Node` and `NodeKind` payloads through the canonical-form
projection but not the `StateBehaviour` payload of a ``TreeOp.UpdateState``, whose
`onLoading` / `onEmpty` slots are themselves nodes. So an `UpdateState` carrying a
`Filters` chip emits the pre-canonical bytes, and this host normalises them away.

Two facts locate the defect on the producing side rather than here: no ``node-``
sample has ever carried the explicit shape (that path *is* canonicalised), and
the converse leg is **600/600 green** — the F# codec accepts this host's collapsed
output and reproduces it byte-for-byte. Fixing it is a one-line change in the F#
op encoder's state appender; until then this leg reports the divergence rather
than suppressing it, because a sample allowlist is how a gate stops being one.

Exit codes: ``0`` all samples agree · ``1`` at least one divergence ·
``2`` the ``<dir>/fsharp/`` input set is missing (emit it first).

Standard-library only, like the rest of the runtime package.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..ops import decode_op, encode_op
from ..schema import decode_node, encode_node

# fuzz_exchange.py → conformance → fuaran_py → src → fuaran-py → Fuaran-UI
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SAMPLES_DIR = _REPO_ROOT.parent / "wire-format-fixtures" / "conformance" / "fuzz-samples"

#: The sub-directory naming this host's canonical output — the ``<host>`` the F#
#: converse leg is pointed at (``--check-fuzz-samples <dir> python``).
HOST = "python"

#: The sub-directory holding the producing host's canonical input samples.
SOURCE_HOST = "fsharp"


@dataclass(frozen=True)
class ExchangeResult:
    """The outcome of one Leg-F run."""

    total: int
    failures: list[str]
    output_dir: Path

    @property
    def ok(self) -> bool:
        return not self.failures


def _first_diff(source: str, mine: str) -> str:
    """Locate the first byte at which this host's canonical form diverges.

    Rendered through :func:`ascii` because the generated samples span non-BMP
    text and the report is routinely piped — a raw repr would raise
    ``UnicodeEncodeError`` on a legacy-codepage stdout and lose the finding.
    """
    n = min(len(source), len(mine))
    for i in range(n):
        if source[i] != mine[i]:
            window = 24
            return (
                f"first diff at byte {i}: "
                f"{SOURCE_HOST}={ascii(source[i : i + window])} {HOST}={ascii(mine[i : i + window])}"
            )
    return f"lengths differ: {SOURCE_HOST}={len(source)} {HOST}={len(mine)}"


def run(
    samples_dir: Path, *, report: Callable[[str], None] = lambda msg: print(msg, file=sys.stderr)
) -> ExchangeResult:
    """Round-trip every ``<samples_dir>/fsharp/`` sample through this host's codec.

    Asserts byte-identity against the input and writes this host's canonical
    output to ``<samples_dir>/python/`` for the converse leg. Raises
    :class:`FileNotFoundError` when the input set is absent.
    """
    source_dir = samples_dir / SOURCE_HOST
    output_dir = samples_dir / HOST

    if not source_dir.is_dir():
        raise FileNotFoundError(source_dir)

    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(p for p in source_dir.iterdir() if p.suffix == ".json")
    failures: list[str] = []

    for path in files:
        name = path.name
        wire = path.read_text(encoding="utf-8")
        is_op = name.startswith("op-")
        decode: Callable[[str], Any] = decode_op if is_op else decode_node
        encode: Callable[[Any], str] = encode_op if is_op else encode_node

        result = decode(wire)
        if not result.ok:
            err = result.error
            failures.append(
                f"DECODE FAILED {name}: the {HOST} decoder rejected an {SOURCE_HOST}-canonical sample - "
                f"{err.code} at {err.path} - {ascii(err.message)}"
            )
            report(failures[-1])
            continue

        mine = encode(result.value)
        if mine != wire:
            failures.append(
                f"MISMATCH {name} ({SOURCE_HOST}->{HOST}): {HOST} canonical diverges from "
                f"{SOURCE_HOST} - {_first_diff(wire, mine)}"
            )
            report(failures[-1])

        # Written unconditionally — the converse leg must check this host's
        # genuine encoder output, including where it already diverged here.
        (output_dir / name).write_text(mine, encoding="utf-8", newline="")

    return ExchangeResult(total=len(files), failures=failures, output_dir=output_dir)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    samples_dir = Path(args[0]).resolve() if args else DEFAULT_SAMPLES_DIR

    try:
        result = run(samples_dir)
    except FileNotFoundError as missing:
        print(
            f"\n  FATAL: {SOURCE_HOST} fuzz samples not found at\n    {missing}\n"
            f"  Emit them first: dotnet run --project "
            f"../fuaran-dotnet/src/Fuaran.UI.JsonDecode.Tests -c Release "
            f"-- --emit-fuzz-samples {samples_dir} 300",
            file=sys.stderr,
        )
        return 2

    if result.ok:
        print(
            f"Cross-host {SOURCE_HOST}->{HOST} leg: {result.total} generated samples re-encoded "
            f"byte-identically; {HOST}-canonical samples written to {result.output_dir} for the "
            f"converse (--check-fuzz-samples <dir> {HOST})."
        )
        return 0

    print(
        f"\nCross-host {SOURCE_HOST}->{HOST} leg: {len(result.failures)}/{result.total} generated samples diverged.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
