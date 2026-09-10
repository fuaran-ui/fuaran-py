"""``fuaran_ui.cli`` — the ``fuaran-ui`` console script.

Every verb is a thin wrapper over a library call this host already ships, which
is the whole design: the CLI adds an entry point, never a second implementation.
``validate`` decodes through :func:`~fuaran_ui.schema.decode.decode_node` /
:func:`~fuaran_ui.ops.decode.decode_op` and walks
:func:`~fuaran_ui.validator.validate_node`; ``render`` calls
:func:`~fuaran_ui.renderer.render_html`; ``export`` calls the static projections
(:func:`~fuaran_ui.renderer.render_markdown` /
:func:`~fuaran_ui.renderer.render_email`); ``corpus-sync`` delegates to the
checkout's own ``conformance/sync_corpus.py``.

::

    fuaran-ui validate tree.json          # -> valid (node)          exit 0
    fuaran-ui validate tree.json --json   # -> the machine report
    fuaran-ui render tree.json > body.html
    fuaran-ui export tree.json --format markdown > page.md

``validate`` is at parity with ``@fuaran-ui/cli validate`` — same exit codes,
same stdout — so the two hosts' get-started tracks read the same. See
:mod:`fuaran_ui.cli.core` for what parity means precisely, why the structural
findings ride stderr rather than the exit code, and why there is no
``spec-hash`` verb.
"""

from __future__ import annotations

from .core import POSTURE, POSTURE_NOTE, USAGE, CliResult, dispatch, main

__all__ = ["POSTURE", "POSTURE_NOTE", "USAGE", "CliResult", "dispatch", "main"]
