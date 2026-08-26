"""Refusal-class report — this host's half of the cross-host identical-rejection check.

The shared corpus declares, per ``reject`` fixture, the code every conformant host
must answer with. Each host's own suite asserts its answer against that
declaration, which makes cross-host agreement true *transitively* — provided every
host's leg actually ran. That proviso is the gap: each of those legs SKIPS when the
corpus is absent, and the estate has already been bitten once by a conformance leg
that silently asserted nothing while its build stayed green.

This module emits this host's answer for every reject fixture as a machine-readable
report. A cross-host runner collects one report per host and asserts the answers
agree with each other AND with the corpus declaration — one artefact, one place, and
a hard failure when a host is missing rather than a quiet omission.

**Per-host error TEXT stays free; the refusal CLASS must agree.** The report carries
the message so a reader can see what this host said, but the runner compares only
the code and the path prefix — the message is diagnostic prose and pinning it across
five languages would be pinning translation, not conformance.

Usage::

    python -m fuaran_py.conformance.refusal_report [--corpus <dir>] [--out <file>]

Writes JSON to stdout by default. Exits non-zero only when the corpus cannot be
read: judging the answers is the runner's job, not this emitter's.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..ops import decode_op
from ..result import Ok
from ..schema import decode_node

HOST = "fuaran-py"


def _default_corpus_root() -> Path | None:
    # parents: [0] conformance, [1] fuaran_py, [2] src, [3] fuaran-py, [4] the
    # workspace root the corpus sits beside.
    root = Path(__file__).resolve().parents[4] / "wire-format-fixtures"
    return root if (root / "manifest.json").is_file() else None


def _decode(decoder: str, text: str) -> tuple[bool, str, str, str]:
    """``(refused, code, path, message)`` for one payload under one decoder.

    Every exception is caught here: a host that raised where the contract says it
    returns has failed the totality claim, and the report says so in a way the
    runner can read rather than dying mid-collection.
    """
    try:
        result = decode_op(text) if decoder == "op" else decode_node(text)
    except Exception as exc:  # noqa: BLE001 — an escaping exception IS a reportable answer
        return True, "ESCAPED-" + type(exc).__name__, "$", str(exc)[:400]
    if isinstance(result, Ok):
        return False, "", "", ""
    err = result.error
    return True, err.code, err.path, err.message


def build_report(corpus_root: Path) -> dict[str, Any]:
    manifest = json.loads((corpus_root / "manifest.json").read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = []
    for fixture in manifest["fixtures"]:
        if fixture.get("kind") != "reject":
            continue
        decoder = fixture.get("decoder", "node")
        if decoder not in ("node", "op"):
            # Envelope / elicitation rejects run through their own decoders and are
            # NOT in scope here. Reported as skipped rather than silently dropped:
            # a runner that could not tell "not applicable" from "not present"
            # would read a shrinking corpus as agreement.
            cases.append({"id": fixture["id"], "decoder": decoder, "skipped": "decoder not in scope"})
            continue
        text = (corpus_root / fixture["inputFile"]).read_text(encoding="utf-8")
        refused, code, path, message = _decode(decoder, text)
        cases.append(
            {
                "id": fixture["id"],
                "decoder": decoder,
                "refused": refused,
                "code": code,
                "path": path,
                "message": message,
            }
        )
    return {"host": HOST, "corpus": str(corpus_root), "cases": cases}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit this host's refusal class for every reject fixture.")
    parser.add_argument("--corpus", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    corpus_root = args.corpus if args.corpus is not None else _default_corpus_root()
    if corpus_root is None or not (corpus_root / "manifest.json").is_file():
        print(
            f"{HOST}: the wire-format corpus was not found"
            + (f" at {corpus_root}" if corpus_root is not None else "")
            + ". Pass --corpus, or check the repo out beside the corpus.",
            file=sys.stderr,
        )
        return 2

    payload = json.dumps(build_report(corpus_root), indent=2) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
