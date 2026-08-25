"""Locate the F# reference host — the parity oracles' shared seam.

Several tests here are **oracles against the reference host's own sources**: the
class-name vocabulary parity lock (``test_render_parity.py``) and the reference
stylesheet byte-copy check (``test_renderer.py``). Each needs the F# sibling
checked out alongside, and each is correct to stand down when it genuinely is
not — a standalone ``fuaran-py`` clone has no reference host to compare against.

What is **not** correct is standing down in a cross-host checkout, where a
missing reference host means the oracle has been silently disabled. That is not
hypothetical: the reference host was renamed once (``fuaran`` →
``fuaran-dotnet``) and the hard-coded paths were not updated, so every oracle
depending on it reported success while checking nothing for as long as the
rename was old. Two properties of this module make that unrepeatable.

*Accept every spelling.* :data:`REFERENCE_HOST_NAMES` lists both, and resolution
walks up from this host's own root rather than deriving the estate root from
some other artefact's location — so neither a rename nor a corpus fallback can
re-silence the oracles.

*Distinguish "absent" from "moved".* :func:`vacuous_gate_diagnosis` returns a
message exactly when a sibling host proves this is a cross-host checkout **and**
no reference host can be found; ``test_render_parity.py`` asserts it is ``None``,
so that case arrives as a red test naming everything tried, not as a quiet skip.
This mirrors the Rust host's ``REFERENCE_HOST_NAMES`` + deliberate panic.
"""

from __future__ import annotations

from pathlib import Path

#: Every directory name the F# reference host has shipped under, newest first.
#: Renamed ``fuaran`` → ``fuaran-dotnet``; accepting both means a rename in
#: either direction cannot disable the parity oracles again. **If it is renamed
#: a third time, add the spelling here** rather than letting the oracles skip.
REFERENCE_HOST_NAMES: tuple[str, ...] = ("fuaran-dotnet", "fuaran")

#: Sibling hosts whose presence proves this is a cross-host checkout (the shape
#: the conformance work builds) rather than a standalone clone. Excludes this
#: host and the reference host.
OTHER_HOST_NAMES: tuple[str, ...] = ("fuaran-ts", "fuaran-rs", "fuaran-go", "fuaran-kt", "fuaran-swift")

#: This repo's root: ``tests/_reference_host.py`` → ``tests`` → ``fuaran-py``.
HOST_ROOT = Path(__file__).resolve().parents[1]


def _search_roots() -> list[Path]:
    return [HOST_ROOT, *HOST_ROOT.parents]


def reference_host_root() -> Path | None:
    """The F# reference host's repo root, or ``None`` when it is not checked out.

    Probes for a ``src/`` directory under each accepted spelling, walking up from
    this repo's own root, so a side-by-side workspace resolves at the first step
    and a nested layout still resolves.
    """
    for directory in _search_roots():
        for name in REFERENCE_HOST_NAMES:
            candidate = directory / name
            if (candidate / "src").is_dir():
                return candidate
    return None


def sibling_host_dir() -> Path | None:
    """A sibling host proving this is a cross-host checkout, if one is present."""
    for directory in _search_roots():
        for sibling in OTHER_HOST_NAMES:
            candidate = directory / sibling
            if candidate.is_dir():
                return candidate
    return None


def vacuous_gate_diagnosis() -> str | None:
    """Non-``None`` when the reference-host oracles are silently disabled.

    ``None`` means either "the reference host resolved" or "this is genuinely a
    standalone clone" — both states in which skipping is the honest answer. A
    message means a sibling host is present but the reference host is not, which
    is the silent-vacuous-green state this whole module exists to surface.
    """
    if reference_host_root() is not None:
        return None
    sibling = sibling_host_dir()
    if sibling is None:
        return None  # genuinely standalone — skipping is correct.
    return (
        f"cross-host checkout detected ({sibling} is present) but the F# reference host is at none of "
        f"{list(REFERENCE_HOST_NAMES)} anywhere at or above {HOST_ROOT}. The render-parity oracles cannot "
        "run, and without this check they would skip silently — a gate reporting success while checking "
        "nothing. If the reference host was renamed again, add the new spelling to REFERENCE_HOST_NAMES in "
        "tests/_reference_host.py; do not let the oracles skip."
    )
