"""Cross-host CLI parity: ``fuaran-ui validate`` against ``@fuaran-ui/cli validate``.

Two legs over one pinned contract (``fixtures/cli_parity/contract.json``), and
they answer different questions:

*Leg A — always runs.* This host must satisfy the contract on every machine,
including a lone ``fuaran-py`` clone and this repo's CI, neither of which has a
Node toolchain or a sibling checkout. That is the whole reason the contract is a
committed file rather than something derived by spawning the other CLI: a parity
check that can only run in a cross-host workspace is a parity check that does not
run where the package is released from.

*Leg B — runs in a cross-host checkout with a built reference CLI.* The
TypeScript CLI is spawned over the same fixture files and must satisfy the same
contract. Without it the pin decays into a description of this host: it would go
on passing after the reference front-end changed, which is the failure mode a
pinned oracle has and a live one does not. With it, the pin is checked from both
ends and a divergence surfaces on the first machine that holds both repos.

What is compared, and what is not
---------------------------------

Exit code, and a NORMALISED report: the verdict, the payload kind, and each
diagnostic's ``code`` and ``$``-rooted ``path``. Diagnostic MESSAGES are excluded
deliberately — the wire contract fixes the code vocabulary and the path, and every
conformant host must agree on those, while the message is host-worded by design
(the reference front-end's ``INVALID_JSON`` message quotes its own parser's
expectation, which no other host could reproduce without copying that parser). A
diff over messages would fail on a difference the specification licenses. Where
the two hosts DO agree on the exact stdout bytes, the contract pins those too, and
this test checks them.

Leg B compares exit codes and, where pinned, stdout bytes — not the JSON report.
The reference front-end has no ``--json`` form to compare against; the report's
parity is a claim about its members matching the TypeScript tool's own
``ValidateResult`` (``valid`` / ``kind`` / ``diagnostics``), pinned in the
contract and checked against this host in leg A.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from _reference_host import sibling_host_root
from fuaran_ui.cli import dispatch

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cli_parity"
CONTRACT: dict[str, Any] = json.loads((FIXTURES / "contract.json").read_text(encoding="utf-8"))
CASES: list[dict[str, Any]] = CONTRACT["cases"]

#: The reference host and the path of its built CLI entry point within it.
REFERENCE_HOST = "fuaran-ts"
REFERENCE_CLI_PACKAGE = Path("packages") / "cli"
REFERENCE_CLI_ENTRY = Path("dist") / "cli.js"

#: Names the reference host's root explicitly, for a checkout whose directory
#: depth is not the canonical side-by-side one. A **git worktree** is the case
#: that matters: it sits outside the estate tree, so the upward walk cannot reach
#: a sibling and leg B would skip on a machine that plainly holds both repos.
#: Same escape hatch, and same reason, as the corpus sync's optional path argument.
REFERENCE_HOST_ENV = "FUARAN_TS_ROOT"


def _argv(case: dict[str, Any]) -> list[str]:
    """The case's argv with the ``<fixture>`` placeholder resolved to a real path."""
    fixture = case["fixture"]
    if fixture is None:
        assert "<fixture>" not in case["argv"], f"{case['name']}: argv names a fixture the case declares none of"
        return list(case["argv"])
    resolved = str(FIXTURES / fixture)
    return [resolved if arg == "<fixture>" else arg for arg in case["argv"]]


def _normalise(report: dict[str, Any]) -> dict[str, Any]:
    """Keep the members the wire contract fixes; drop the host-worded message."""
    return {
        "valid": report["valid"],
        "kind": report["kind"],
        "diagnostics": [{"code": d["code"], "path": d["path"]} for d in report["diagnostics"]],
    }


def test_the_contract_is_not_vacuous() -> None:
    """A contract of zero cases, or of no cases carrying a report, proves nothing."""
    assert len(CASES) >= 4
    assert sum(1 for c in CASES if c["report"] is not None) >= 3
    assert {c["exit"] for c in CASES} == {0, 1, 2}


# --- Leg A: this host satisfies the pinned contract ---------------------------


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_this_host_satisfies_the_pinned_contract(case: dict[str, Any]) -> None:
    result = dispatch(_argv(case))
    assert result.code == case["exit"], f"{case['name']}: exit code"
    if case["out"] is not None:
        assert result.out == case["out"], f"{case['name']}: stdout bytes"
    if case["report"] is not None:
        json_result = dispatch([*_argv(case), "--json"])
        assert json_result.code == case["exit"], f"{case['name']}: --json exit code"
        assert _normalise(json.loads(json_result.out)) == case["report"], f"{case['name']}: report"


# --- Leg B: the reference front-end satisfies it too --------------------------


def _reference_cli() -> Path | None:
    """The built ``@fuaran-ui/cli`` entry point, or ``None`` with the reason skipped.

    Raises when the sibling host is checked out but its CLI package has MOVED —
    the silent-vacuous state ``_reference_host.py`` exists to prevent, in the
    shape it takes here. A missing ``dist/`` is not that: it is an uncommitted
    build output, legitimately absent in a checkout nobody has built.
    """
    declared = os.environ.get(REFERENCE_HOST_ENV)
    if declared:
        host: Path | None = Path(declared).resolve()
        if host is not None and not host.is_dir():
            raise AssertionError(
                f"{REFERENCE_HOST_ENV}={declared} names no directory. A declared reference host that "
                "does not exist must fail rather than skip — declaring it is how a machine says it "
                "expects this leg to run."
            )
    else:
        host = sibling_host_root(REFERENCE_HOST)
    if host is None:
        return None  # genuinely standalone — leg A is the whole check here.
    package = host / REFERENCE_CLI_PACKAGE
    if not package.is_dir():
        raise AssertionError(
            f"{host} is checked out but {package} does not exist. The reference CLI package has "
            "moved or been renamed, and this parity leg would silently stop running. Update "
            "REFERENCE_CLI_PACKAGE in tests/test_cli_parity.py rather than letting it skip."
        )
    entry = package / REFERENCE_CLI_ENTRY
    return entry if entry.is_file() else None


def _run_reference(argv: list[str], entry: Path) -> tuple[int, str]:
    completed = subprocess.run(  # noqa: S603 — a fixed node invocation over committed fixtures
        ["node", str(entry), *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(FIXTURES),
        env={**os.environ, "NO_COLOR": "1"},
        check=False,
    )
    return completed.returncode, completed.stdout


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_reference_front_end_satisfies_the_same_contract(case: dict[str, Any]) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH — the pinned contract (leg A) still binds")
    entry = _reference_cli()
    if entry is None:
        pytest.skip(
            f"no built {REFERENCE_HOST} CLI beside this host (dist/ is a build output) — "
            "the pinned contract (leg A) still binds"
        )

    code, out = _run_reference(_argv(case), entry)
    assert code == case["exit"], (
        f"{case['name']}: the reference CLI exits {code} where the pinned contract says "
        f"{case['exit']}. The pin is stale, or the two front-ends have diverged — read the "
        "reference source named in contract.json before moving either."
    )
    if case["out"] is not None:
        assert out == case["out"], f"{case['name']}: the reference CLI's stdout has moved off the pin"
