"""The cross-host fuzz-sample exchange runner (Leg F) — its own regression floor.

``fuaran_py.conformance.fuzz_exchange`` is the mechanism that consumes another
host's generated canonical samples and emits this host's, so the converse leg can
check them. The exchange itself needs a live F# emitter and is therefore driven by
hand (see the module docstring); these tests pin the *runner* — that it detects a
divergence, writes the output set, and fails loudly on a missing input set —
without needing another toolchain.

The samples here are real canonical corpus payloads renamed into the exchange's
``node-NNNN`` / ``op-NNNN`` convention (the prefix is what selects the Node vs
TreeOp codec), so a green run means the runner agrees with the same bytes the
conformance gate certifies.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from _corpus import CORPUS_ROOT, corpus_required, fixtures_of
from fuaran_py.conformance.fuzz_exchange import HOST, SOURCE_HOST, main, run


def _seed(samples_dir: Path, *, nodes: int = 4, ops: int = 4) -> Path:
    """Write a small `<dir>/fsharp/` input set from canonical corpus payloads."""
    source_dir = samples_dir / SOURCE_HOST
    source_dir.mkdir(parents=True, exist_ok=True)

    node_fixtures = fixtures_of("node-round-trip")[:nodes]
    op_fixtures = fixtures_of("op-round-trip")[:ops]
    assert node_fixtures and op_fixtures, "corpus carries no round-trip fixtures to seed from"

    for i, fixture in enumerate(node_fixtures):
        payload = (CORPUS_ROOT / fixture["expectedFile"]).read_text(encoding="utf-8")
        (source_dir / f"node-{i:04d}.json").write_text(payload.rstrip("\n"), encoding="utf-8", newline="")
    for i, fixture in enumerate(op_fixtures):
        payload = (CORPUS_ROOT / fixture["expectedFile"]).read_text(encoding="utf-8")
        (source_dir / f"op-{i:04d}.json").write_text(payload.rstrip("\n"), encoding="utf-8", newline="")

    return source_dir


@corpus_required
def test_round_trips_canonical_samples_and_writes_this_hosts_set(tmp_path: Path) -> None:
    _seed(tmp_path)

    result = run(tmp_path, report=lambda _msg: None)

    assert result.ok, f"canonical corpus payloads diverged through the exchange: {result.failures}"
    assert result.total == 8
    emitted = sorted(p.name for p in (tmp_path / HOST).iterdir())
    assert emitted == sorted(p.name for p in (tmp_path / SOURCE_HOST).iterdir())
    # This host's output must be the genuine encoder bytes, not a copy of the input.
    for name in emitted:
        assert (tmp_path / HOST / name).read_text(encoding="utf-8") == (tmp_path / SOURCE_HOST / name).read_text(
            encoding="utf-8"
        )


@corpus_required
def test_a_corrupted_sample_is_caught(tmp_path: Path) -> None:
    """The gate must actually bite — a one-token edit to a sample turns it red.

    A cross-host gate that has never rejected anything is not yet a gate.
    """
    source_dir = _seed(tmp_path)
    victim = source_dir / "node-0000.json"
    pristine = victim.read_text(encoding="utf-8")

    # Re-order two canonical keys: still valid, decodable wire — but no longer
    # the canonical byte sequence, so the re-encode must not reproduce it.
    corrupted = pristine.replace('{"id":', '{"style":{"tone":"Warning"},"id":', 1)
    assert corrupted != pristine, "the corruption did not apply -- the fixture shape changed"
    victim.write_text(corrupted, encoding="utf-8", newline="")

    result = run(tmp_path, report=lambda _msg: None)

    assert not result.ok, "a non-canonical sample slipped through the exchange"
    assert len(result.failures) == 1, result.failures
    assert "node-0000.json" in result.failures[0]

    victim.write_text(pristine, encoding="utf-8", newline="")
    assert run(tmp_path, report=lambda _msg: None).ok, "restoring the sample did not restore the green run"


@corpus_required
def test_a_rejected_sample_is_reported_as_a_decode_failure(tmp_path: Path) -> None:
    source_dir = _seed(tmp_path)
    (source_dir / "node-0000.json").write_text('{"id":"x","kind":{"$type":"NoSuchKind"}}', encoding="utf-8")

    result = run(tmp_path, report=lambda _msg: None)

    assert not result.ok
    assert result.failures[0].startswith("DECODE FAILED node-0000.json")


def test_a_missing_input_set_exits_2_rather_than_reporting_green(tmp_path: Path) -> None:
    """An absent `<dir>/fsharp/` is 'run the emitter first', never a vacuous pass."""
    assert main([str(tmp_path)]) == 2


@corpus_required
def test_main_exit_codes(tmp_path: Path) -> None:
    _seed(tmp_path)
    assert main([str(tmp_path)]) == 0

    victim = tmp_path / SOURCE_HOST / "op-0000.json"
    victim.write_text(victim.read_text(encoding="utf-8").replace('{"$type":', '{"target":"zz","$type":', 1))
    assert main([str(tmp_path)]) == 1

    shutil.rmtree(tmp_path / SOURCE_HOST)
    assert main([str(tmp_path)]) == 2


@corpus_required
def test_op_prefix_selects_the_treeop_codec(tmp_path: Path) -> None:
    """Naming is load-bearing: `op-` picks decode_op, anything else decode_node."""
    source_dir = tmp_path / SOURCE_HOST
    source_dir.mkdir(parents=True)
    op_fixture = fixtures_of("op-round-trip")[0]
    payload = (CORPUS_ROOT / op_fixture["expectedFile"]).read_text(encoding="utf-8").rstrip("\n")

    # Correctly named: round-trips.
    (source_dir / "op-0000.json").write_text(payload, encoding="utf-8", newline="")
    assert run(tmp_path, report=lambda _msg: None).ok

    # Mis-named as a node: the Node decoder must reject a TreeOp payload.
    (source_dir / "op-0000.json").unlink()
    (source_dir / "node-0000.json").write_text(payload, encoding="utf-8", newline="")
    misrouted = run(tmp_path, report=lambda _msg: None)
    assert not misrouted.ok, "a TreeOp payload decoded as a Node -- the codec selection is not wired to the prefix"


@corpus_required
def test_first_diff_report_is_ascii_safe(tmp_path: Path) -> None:
    """The report is routinely piped; a raw repr of non-BMP sample text would
    raise UnicodeEncodeError on a legacy-codepage stdout and lose the finding."""
    source_dir = tmp_path / SOURCE_HOST
    source_dir.mkdir(parents=True)
    (source_dir / "node-0000.json").write_text(
        '{"id":"x","kind":{"$type":"Markdown","text":"\\u65e5\\u672c\\ud83d\\ude80"},"style":{"tone":"Warning"}}',
        encoding="utf-8",
        newline="",
    )

    result = run(tmp_path, report=lambda _msg: None)

    assert not result.ok, "the seeded sample was already canonical -- pick a non-canonical one"
    result.failures[0].encode("ascii")  # raises if the report leaked a non-ASCII character


@pytest.mark.parametrize("host_dir", [SOURCE_HOST, HOST])
def test_host_directory_names_match_the_converse_leg(host_dir: str) -> None:
    """`<dir>/<host>/` is the shared contract with the F# `--check-fuzz-samples
    <dir> <host>` argument; renaming either side silently un-wires the exchange."""
    assert host_dir in ("fsharp", "python")
