"""The command core — argv in, (exit code, stdout, stderr) out.

Structured the way the estate's other two front-ends are: a pure ``dispatch``
that returns what it would have printed, and a bin shim (:func:`main`) that
writes it to the real streams and exits. Nothing here calls ``sys.exit`` and
nothing here writes to a stream, so every verb is testable without spawning a
process — which is what makes the cross-host parity test possible at all.

Parity, precisely
-----------------

``validate`` is the one verb this host shares with ``@fuaran-ui/cli`` (the npm
``fuaran`` bin) and ``Fuaran.UI.Cli`` (the dotnet tool). For it, **the exit code
and the stdout bytes match those front-ends exactly**:

* ``0`` — the document decoded; stdout is ``valid (node)``.
* ``1`` — the document did not decode, or the file could not be read.
* ``2`` — a usage error (no file, unknown verb, unknown flag).

``tests/test_cli_parity.py`` holds the pinned contract and, in a cross-host
checkout, re-derives it by running the TypeScript CLI over the same fixtures.

The other verbs (``render`` / ``export`` / ``corpus-sync``) have no counterpart
in those front-ends; each is a thin wrapper over a library call this host
already ships, and no parity is claimed for them.

Why there is no ``spec-hash`` verb
----------------------------------

The ``canonical-json-sha256-v1`` minting rule is specified by the Fuaran
model-execution wire specification, and its reference implementation is a
workspace-internal, deliberately unpublished package. The corresponding minting
helpers were declined on the F# and TypeScript tiers for a reason that applies
here word for word: the specification's corpus — the only thing that decides
whether a minter is correct — is not shipped with this package and is not
checked out by its CI, so a minter here could not be gated where it runs. A
``spec-hash`` verb whose algorithm nothing can certify is worth less than its
absence, so the absence is recorded rather than filled.

Streams
-------

stdout carries the **artefact** — the verdict line, the JSON report, the
rendered HTML, the exported document — so every verb pipes. stderr carries the
commentary: the validator's posture line, its structural findings, the render
summary. That split is why ``validate``'s stdout can be byte-identical to the
TypeScript CLI's while this host still says more than it does.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..ops import decode_op
from ..renderer import render_email, render_email_document, render_html, render_markdown
from ..result import DecodeError, Err
from ..schema import decode_node
from ..validator import Finding, validate_node

#: What the structural validator claims about itself, in every report it writes.
#: The rule set here is a documented subset of the reference tier's, exactly as
#: the TypeScript validator's is, and a report that did not say so would read as
#: a certificate it is not.
POSTURE = "subset"

POSTURE_NOTE = (
    "the structural rule set is a documented SUBSET of the reference (F#) tier's; a clean "
    "report means no rule in this subset matched, never that the tree is free of every defect "
    "the reference validator would name"
)

USAGE = """fuaran-ui — the Fuaran Python host CLI

Usage:
  fuaran-ui validate <file> [--kind node|op] [--json]   Wire JSON -> pass/fail + diagnostics
  fuaran-ui render <file>                               Wire JSON -> server-HTML body fragment
  fuaran-ui export <file> --format markdown|email|email-document [--title T] [--subject S]
  fuaran-ui corpus-sync [--check | --declare] [<path>]  Bundled corpus snapshot vs the authority
  fuaran-ui help                                        This text

Exit codes: 0 the document is good; 1 it is not (or cannot be read); 2 a usage error.
`validate` matches `@fuaran-ui/cli validate` in exit code and stdout, verb for verb.

Rendering applies the wire format's ambient destination policy at its default,
which denies non-local destinations. A host that wants a wider posture declares
one through the library; the CLI does not take one as a flag."""


@dataclass(frozen=True)
class CliResult:
    """One invocation's outcome: a process exit code and what each stream carries."""

    code: int
    out: str = ""
    err: str = ""


# --- argv helpers (the shape @fuaran-ui/cli and Fuaran.UI.Cli both use) -------


def _flag(argv: list[str], name: str) -> str | None:
    """The value of ``--name value``, or ``None`` when the flag is absent."""
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def _split(
    argv: list[str], flags_with_value: tuple[str, ...], bare_flags: tuple[str, ...]
) -> tuple[list[str], str | None]:
    """Positionals, plus the first unrecognised flag (``None`` when all are known).

    Unlike the TypeScript front-end, an unrecognised flag is REFUSED rather than
    skipped. A silently-ignored ``--json`` is a report the caller believes it
    asked for and did not get; the estate's other validator CLI refuses on the
    same reasoning.
    """
    positionals: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("--"):
            if arg in flags_with_value:
                i += 2
                continue
            if arg in bare_flags:
                i += 1
                continue
            return positionals, arg
        positionals.append(arg)
        i += 1
    return positionals, None


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


# --- report projections ------------------------------------------------------


def _diagnostic_json(error: DecodeError) -> dict[str, Any]:
    """A decode error in the wire contract's own member names (WIRE_FORMAT §6).

    ``expectedShape`` is omitted rather than emitted as null when the decoder
    named none, matching the reference hosts' ``DecodeError`` serialisation.
    """
    payload: dict[str, Any] = {"code": error.code, "path": error.path, "message": error.message}
    if error.expected_shape is not None:
        payload["expectedShape"] = error.expected_shape
    return payload


def _finding_json(finding: Finding) -> dict[str, Any]:
    return {"code": finding.code, "path": finding.path, "message": finding.message}


def _diagnostic_line(code: str, path: str, message: str) -> str:
    return f"  {code} at {path}: {message}"


def _validate_report(
    *, valid: bool, kind: str, diagnostics: list[dict[str, Any]], findings: list[dict[str, Any]]
) -> str:
    """The ``--json`` report.

    ``valid`` / ``kind`` / ``diagnostics`` are the parity members — the shape the
    TypeScript tool's own ``ValidateResult`` carries, so a consumer that reads one
    host's report reads the other's. ``posture`` / ``postureNote`` / ``findings``
    are this host's additions, and are additions precisely because the reference
    front-end has no structural tier to report.
    """
    report = {
        "verb": "validate",
        "valid": valid,
        "kind": kind,
        "diagnostics": diagnostics,
        "posture": POSTURE,
        "postureNote": POSTURE_NOTE,
        "findings": findings,
    }
    return json.dumps(report, indent=2) + "\n"


# --- verbs -------------------------------------------------------------------


def _validate(argv: list[str]) -> CliResult:
    positionals, unknown = _split(argv, ("--kind",), ("--json",))
    if unknown is not None:
        return CliResult(2, "", f"validate: unknown flag {unknown}.\n")

    kind = _flag(argv, "--kind") or "node"
    if kind not in ("node", "op"):
        return CliResult(2, "", f"validate: unknown --kind value: {kind} (expected node|op).\n")
    as_json = "--json" in argv

    if not positionals:
        return CliResult(2, "validate: a file is required.\n")

    text = _read(positionals[0])
    # The two decoders are kept in separate branches rather than joined into one
    # result: a TreeOp and a Node are different types, and the structural walk
    # below applies to one of them only. Joining them first and re-discriminating
    # afterwards would hide that from the reader and from the type checker alike.
    findings: list[Finding] = []
    if kind == "op":
        op_result = decode_op(text)
        if isinstance(op_result, Err):
            error = op_result.error
        else:
            error = None
    else:
        node_result = decode_node(text)
        if isinstance(node_result, Err):
            error = node_result.error
        else:
            error = None
            # A TreeOp carries no tree to walk, so the structural rules are node-only.
            findings = validate_node(node_result.value)

    if error is not None:
        if as_json:
            return CliResult(
                1,
                _validate_report(valid=False, kind=kind, diagnostics=[_diagnostic_json(error)], findings=[]),
            )
        line = _diagnostic_line(error.code, error.path, error.message)
        return CliResult(1, f"invalid ({kind}):\n{line}\n")

    if as_json:
        return CliResult(
            0,
            _validate_report(valid=True, kind=kind, diagnostics=[], findings=[_finding_json(f) for f in findings]),
        )

    commentary = [f"fuaran-ui validate: posture={POSTURE} - {POSTURE_NOTE}"]
    commentary += [_diagnostic_line(f.code, f.path, f.message) for f in findings]
    # Structural findings do not move the exit code: the shared verb's exit code
    # answers "did this document decode", and a host answering a wider question
    # under the same number would not be at parity with the front-ends it claims
    # to match.
    if findings:
        commentary.append(
            f"fuaran-ui validate: {len(findings)} structural finding(s) - advisory; the exit code "
            "reports the decode only, as the reference front-ends' does"
        )
    return CliResult(0, f"valid ({kind})\n", "\n".join(commentary) + "\n")


def _render(argv: list[str]) -> CliResult:
    positionals, unknown = _split(argv, (), ())
    if unknown is not None:
        return CliResult(2, "", f"render: unknown flag {unknown}.\n")
    if not positionals:
        return CliResult(2, "render: a file is required.\n")

    result = decode_node(_read(positionals[0]))
    if isinstance(result, Err):
        error = result.error
        return CliResult(1, f"invalid (node):\n{_diagnostic_line(error.code, error.path, error.message)}\n")

    html = render_html(result.value)
    return CliResult(0, html + "\n", f"fuaran-ui render: {len(html)} byte(s) of body-fragment HTML\n")


_EXPORT_FORMATS = ("markdown", "email", "email-document")


def _export(argv: list[str]) -> CliResult:
    positionals, unknown = _split(argv, ("--format", "--title", "--subject"), ())
    if unknown is not None:
        return CliResult(2, "", f"export: unknown flag {unknown}.\n")

    fmt = _flag(argv, "--format")
    if fmt not in _EXPORT_FORMATS:
        return CliResult(2, "", f"export: --format {'|'.join(_EXPORT_FORMATS)} is required.\n")
    if not positionals:
        return CliResult(2, "export: a file is required.\n")

    result = decode_node(_read(positionals[0]))
    if isinstance(result, Err):
        error = result.error
        return CliResult(1, f"invalid (node):\n{_diagnostic_line(error.code, error.path, error.message)}\n")

    node = result.value
    if fmt == "markdown":
        document = render_markdown(node, title=_flag(argv, "--title"))
    elif fmt == "email":
        document = render_email(node) + "\n"
    else:
        # An email document needs a subject for its <title>; the envelope's own
        # Subject header stays the sender's concern, as the library's note says.
        document = render_email_document(node, _flag(argv, "--subject") or "") + "\n"

    return CliResult(0, document, f"fuaran-ui export: {fmt}, {len(document)} byte(s)\n")


def _sync_corpus_script() -> Path | None:
    """Locate ``conformance/sync_corpus.py`` — a repo-checkout artefact.

    It is not part of the installed package (the wheel ships ``src/fuaran_ui``
    only), so this verb is meaningful in a source checkout and nowhere else. Two
    probes, because both arrangements are ordinary: an editable install still
    sits inside the checkout, and a maintainer may run the console script from
    anywhere beneath it.
    """
    from_package = Path(__file__).resolve().parents[3] / "conformance" / "sync_corpus.py"
    if from_package.is_file():
        return from_package
    here = Path.cwd().resolve()
    for directory in (here, *here.parents):
        candidate = directory / "conformance" / "sync_corpus.py"
        if candidate.is_file():
            return candidate
    return None


def _corpus_sync(argv: list[str]) -> CliResult:
    """Delegate to the checkout's corpus-sync script.

    The one verb whose output this core does not carry: the script writes its own
    progress and drift report to stderr, and re-plumbing it through a captured
    buffer would fork a second copy of behaviour the suite already gates.
    """
    script = _sync_corpus_script()
    if script is None:
        return CliResult(
            2,
            "",
            "corpus-sync: conformance/sync_corpus.py is not present. It is a repo-checkout "
            "artefact - the published wheel ships the library only - so this verb runs from a "
            "fuaran-py checkout, not from an installed package.\n",
        )

    import importlib.util

    spec = importlib.util.spec_from_file_location("fuaran_ui_cli_sync_corpus", script)
    if spec is None or spec.loader is None:  # pragma: no cover - a readable file always yields a spec
        return CliResult(2, "", f"corpus-sync: cannot load {script}.\n")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return CliResult(int(module.main(list(argv))))


# --- dispatch ----------------------------------------------------------------


def dispatch(argv: list[str]) -> CliResult:
    """Run one invocation. Never throws for an input error — it becomes a code."""
    verb, rest = (argv[0], argv[1:]) if argv else (None, [])
    try:
        if verb == "validate":
            return _validate(rest)
        if verb == "render":
            return _render(rest)
        if verb == "export":
            return _export(rest)
        if verb == "corpus-sync":
            return _corpus_sync(rest)
        if verb is None or verb in ("help", "--help", "-h"):
            return CliResult(0, USAGE + "\n")
        return CliResult(2, f'unknown command "{verb}".\n\n{USAGE}\n')
    except OSError as err:
        # An unreadable file is the document failing, not the caller misusing the
        # CLI - exit 1, matching the reference front-ends' catch-all.
        return CliResult(1, f"error: {err}\n")


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point (``fuaran-ui``). Returns the process exit code."""
    result = dispatch(list(sys.argv[1:] if argv is None else argv))
    if result.out:
        sys.stdout.write(result.out)
    if result.err:
        sys.stderr.write(result.err)
    return result.code


if __name__ == "__main__":  # pragma: no cover - exercised as a console script
    raise SystemExit(main())
