"""The packaging contract: what the package says about itself must be true.

``fuaran_py.__version__`` is what a consumer reads at runtime to answer "which
host is this?" — in a notebook, in a bug report, in a compatibility check. It is a
hand-written literal, so nothing but a test stops it drifting from the version
actually shipped, and nothing did: it sat at ``0.0.1`` through four tagged
releases. The companion distribution pins the pair this way; the host now does
too.

A literal plus this test, rather than reading ``importlib.metadata`` at import
time: this host is importable from a plain ``sys.path`` entry (Pyodide, a vendored
copy) where no distribution metadata exists at all, and a package that raises on
import in that arrangement would be a worse defect than the one being fixed.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import fuaran_py


def _pyproject() -> dict[str, Any]:
    root = Path(__file__).resolve().parent.parent
    with (root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_the_declared_version_matches_the_package() -> None:
    declared = _pyproject()["project"]["version"]
    assert declared == fuaran_py.__version__, (
        f"pyproject.toml declares {declared!r} but fuaran_py.__version__ is "
        f"{fuaran_py.__version__!r} — a consumer asking this package which version it is "
        "would be told the wrong answer. Move both in the same commit."
    )
