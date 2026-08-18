"""The declaration in ``validator-coverage.json`` against what the validator raises.

Phase 669's gate compares each host's declaration to the canonical vocabulary, but
it cannot compare a declaration to an IMPLEMENTATION — it reads JSON, not code. So a
host could declare a rule it does not implement, or implement one it never declared,
and the cross-host gate would pass.

This module closes that for this host, which is what ``machineChecked: true`` in the
declaration asserts. It is only possible because the FUARAN code is now a first-class
field on ``Finding`` rather than prose inside the message: when the code and the
message are the same string there is no pair to check.

The implemented set is recovered by SOURCE SCAN rather than by asking the validator,
because asking it would require a tree that triggers every rule — and the rules this
would then miss are exactly the ones no fixture exercises, which is the wrong failure
direction for a coverage check.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "src" / "fuaran_py" / "validator" / "validate.py"
_DECL = Path(__file__).resolve().parents[1] / "validator-coverage.json"

# A code in a `Finding(...)` constructor position — the codes this host can RAISE.
# Deliberately not "any FUARAN code appearing in the file": a code named in a
# docstring is discussion, not implementation, and counting it would let prose
# satisfy the gate.
_RAISED = re.compile(r'Finding\(\s*"(FUARAN[0-9A-Z-]+)"')


def _raised_codes() -> set[str]:
    return set(_RAISED.findall(_SRC.read_text(encoding="utf-8")))


def _declaration() -> dict:
    return json.loads(_DECL.read_text(encoding="utf-8"))


def test_declaration_claims_machine_checked() -> None:
    """If this drops to false, the two tests below are no longer load-bearing."""
    assert _declaration()["machineChecked"] is True


def test_every_raised_code_is_declared() -> None:
    """Implemented-but-undeclared: the drift that makes the matrix understate."""
    declared = set(_declaration()["implemented"])
    undeclared = sorted(_raised_codes() - declared)
    assert not undeclared, (
        f"the validator raises {undeclared} but the declaration does not list them — "
        "add them to `implemented`, or stop raising them"
    )


def test_every_declared_code_is_raised() -> None:
    """Declared-but-unimplemented: the drift that makes the matrix overstate, and
    the more dangerous direction — it claims coverage this host does not have."""
    declared = set(_declaration()["implemented"])
    unimplemented = sorted(declared - _raised_codes())
    assert not unimplemented, (
        f"the declaration lists {unimplemented} but no `Finding(...)` raises them — "
        "implement them, or move them to `abstained` with a reason"
    )


@pytest.mark.parametrize("code", sorted(_raised_codes()))
def test_raised_code_is_in_the_canonical_vocabulary(code: str) -> None:
    """A code this host invented would otherwise look like coverage."""
    vocab_path = None
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "wire-format-fixtures" / "validator" / "defect-vocabulary.json"
        if candidate.is_file():
            vocab_path = candidate
            break
    if vocab_path is None:
        pytest.skip("wire-format-fixtures/validator not found")
    vocab = {e["code"] for e in json.loads(vocab_path.read_text(encoding="utf-8"))["codes"]}
    assert code in vocab, f"{code} is not in the canonical vocabulary — this host invented it"
