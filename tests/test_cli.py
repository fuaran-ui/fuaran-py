"""The ``fuaran-py`` console script's own behaviour.

Cross-host parity lives next door in ``test_cli_parity.py``; what is pinned here
is everything this host owns alone — the verbs the reference front-end has no
counterpart for, the posture the validator declares about itself, and the two
places the CLI deliberately says *no*.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from fuaran_py.cli import POSTURE, dispatch, main

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "cli_parity"
VALID = str(FIXTURES / "valid-node.json")
MISSING_KIND = str(FIXTURES / "missing-kind.json")


def test_the_console_script_is_declared_and_points_at_the_entry_point() -> None:
    """The packaging contract for the entry point itself.

    A CLI nothing installs is a library function with a docstring about being a
    CLI; this is the one assertion that the wheel actually grows a ``fuaran-py``
    command, and that it names an importable callable.
    """
    root = Path(__file__).resolve().parent.parent
    with (root / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    scripts = pyproject["project"].get("scripts", {})
    assert scripts.get("fuaran-py") == "fuaran_py.cli:main", (
        f"pyproject.toml must declare [project.scripts] fuaran-py = 'fuaran_py.cli:main' — it declares {scripts!r}"
    )
    assert callable(main)


# --- validate: the posture the report is obliged to carry --------------------


def test_validate_declares_its_subset_posture_on_a_clean_tree() -> None:
    """Said on a CLEAN run, not only when something is found.

    A posture that appears alongside findings tells the reader what a finding
    means; a posture that appears on a clean report tells them what the SILENCE
    means, which is the claim that can be over-read.
    """
    result = dispatch(["validate", VALID])
    assert result.code == 0
    assert f"posture={POSTURE}" in result.err
    assert "SUBSET" in result.err


def test_validate_json_report_carries_the_posture_and_the_parity_members() -> None:
    result = dispatch(["validate", VALID, "--json"])
    assert result.code == 0
    report = json.loads(result.out)
    # The parity members — the shape the TypeScript tool's own ValidateResult carries.
    assert report["valid"] is True
    assert report["kind"] == "node"
    assert report["diagnostics"] == []
    # This host's additions.
    assert report["posture"] == POSTURE
    assert "postureNote" in report and report["postureNote"]
    assert report["findings"] == []


def test_validate_json_report_names_the_decode_failure_in_the_wire_members() -> None:
    result = dispatch(["validate", MISSING_KIND, "--json"])
    assert result.code == 1
    report = json.loads(result.out)
    assert report["valid"] is False
    assert report["diagnostics"] == [
        {"code": "MISSING_FIELD", "path": "$.kind", "message": "missing required field 'kind'"}
    ]


def test_structural_findings_are_reported_without_moving_the_exit_code(tmp_path: Path) -> None:
    """A duplicate id is reported on stderr; the exit code still answers the decode.

    The exit code is the shared verb's parity surface. A host that failed here on
    a rule the reference front-end does not run would return 1 where the other
    returns 0 for the same bytes — parity broken by a finding, which is exactly
    the thing the split streams exist to avoid.
    """
    duplicate = tmp_path / "dup.json"
    duplicate.write_text(
        '{"id":"a","kind":{"$type":"Box","children":['
        '{"id":"a","kind":{"$type":"Badge","label":"x","variant":"Info"}}],'
        '"layout":{"$type":"Flex","direction":"Vertical","wrap":false},"role":"Group"}}',
        encoding="utf-8",
    )
    result = dispatch(["validate", str(duplicate)])
    assert result.code == 0
    assert result.out == "valid (node)\n"
    assert "FUARAN-DUP-ID" in result.err
    assert "advisory" in result.err

    as_json = dispatch(["validate", str(duplicate), "--json"])
    assert as_json.code == 0
    codes = [f["code"] for f in json.loads(as_json.out)["findings"]]
    assert "FUARAN-DUP-ID" in codes


def test_validate_accepts_a_treeop_and_says_which_kind_it_read(tmp_path: Path) -> None:
    op = tmp_path / "op.json"
    op.write_text('{"$type":"RemoveNode","target":"metric-1"}', encoding="utf-8")
    result = dispatch(["validate", str(op), "--kind", "op"])
    assert result.code == 0
    assert result.out == "valid (op)\n"


# --- render / export: this host's own verbs ----------------------------------


def test_render_emits_a_body_fragment_on_stdout() -> None:
    result = dispatch(["render", VALID])
    assert result.code == 0
    assert result.out.startswith("<")
    assert "fuaran-" in result.out  # the reference class vocabulary
    assert "byte(s)" in result.err  # commentary stays off the artefact stream


def test_render_reports_a_decode_failure_the_way_validate_does() -> None:
    result = dispatch(["render", MISSING_KIND])
    assert result.code == 1
    assert result.out == "invalid (node):\n  MISSING_FIELD at $.kind: missing required field 'kind'\n"


def test_export_produces_each_declared_projection() -> None:
    markdown = dispatch(["export", VALID, "--format", "markdown", "--title", "Report"])
    assert markdown.code == 0
    assert markdown.out.startswith("# Report")

    digest = dispatch(["export", VALID, "--format", "email"])
    assert digest.code == 0
    assert "<table" in digest.out

    document = dispatch(["export", VALID, "--format", "email-document", "--subject", "Weekly"])
    assert document.code == 0
    assert document.out.startswith("<!DOCTYPE html>")
    assert "<title>Weekly</title>" in document.out


def test_export_without_a_format_is_a_usage_error() -> None:
    result = dispatch(["export", VALID])
    assert result.code == 2
    assert "--format" in result.err


# --- the two deliberate refusals ---------------------------------------------


def test_an_unrecognised_flag_is_refused_rather_than_skipped() -> None:
    """The documented divergence from the reference front-end.

    It skips unknown flags; this host refuses. A caller who types ``--jsonn`` and
    gets the plain report has been told nothing, and will read the plain text as
    if it were the report they asked for.
    """
    result = dispatch(["validate", VALID, "--jsonn"])
    assert result.code == 2
    assert "--jsonn" in result.err


def test_spec_hash_is_absent_and_the_absence_is_explained() -> None:
    """``spec-hash`` is not a verb, and the module says why in prose a reader meets.

    Pinned so the reasoning cannot be dropped in a tidy-up without the test that
    names it going red: the minting rule's corpus is not shipped here and is not
    checked out by this repo's CI, so a minter would be ungateable where it runs.
    """
    result = dispatch(["spec-hash", VALID])
    assert result.code == 2
    assert 'unknown command "spec-hash"' in result.out

    import fuaran_py.cli.core as core

    assert core.__doc__ is not None
    assert "spec-hash" in core.__doc__
    assert "corpus" in core.__doc__


# --- dispatch's own contract --------------------------------------------------


def test_help_and_no_argument_both_print_the_usage_at_exit_zero() -> None:
    for argv in ([], ["help"], ["--help"], ["-h"]):
        result = dispatch(list(argv))
        assert result.code == 0, argv
        assert result.out.startswith("fuaran-py"), argv


def test_an_unreadable_file_is_the_document_failing_not_a_usage_error(tmp_path: Path) -> None:
    result = dispatch(["validate", str(tmp_path / "absent.json")])
    assert result.code == 1
    assert result.out.startswith("error: ")


def test_corpus_sync_check_resolves_this_checkouts_script() -> None:
    """The verb reaches the checkout's own script rather than a second copy.

    ``--check`` returns 0 (in sync), 1 (unmeasured — no authority beside this
    clone) or 2 (behind), and every one of those is a legitimate answer here; what
    is pinned is that it is not 2-with-the-not-present message, i.e. that the
    delegation resolved at all.
    """
    result = dispatch(["corpus-sync", "--check"])
    assert "is not present" not in result.err
    assert result.code in (0, 1, 2)
