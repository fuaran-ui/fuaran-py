"""The structural layer is a projection of ``idl.json`` (Phase 2296) — the two drift checks.

``fuaran_ui.schema.generated`` is written by this host's own generator from the BUNDLED snapshot's
``idl.json``. Two ways it can fall out of step, and one check for each:

* **the module was hand-edited, or the generator changed and was not re-run** — a regeneration into
  a temp path must be byte-identical to the committed module;
* **the IDL moved and the module was not regenerated** — the module records the SHA-256 of the
  ``idl.json`` bytes it was generated from, and that digest must equal the bundled file's.

Neither needs the authority corpus: both read only what this checkout carries, which is what makes
the offline build reproducible. The falsifiers below prove each check can go red.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fuaran_ui.schema import generate, generated

_REPO = Path(__file__).resolve().parents[1]
_BUNDLED_IDL = _REPO / "conformance" / "corpus" / "idl.json"
_MODULE = _REPO / "src" / "fuaran_ui" / "schema" / "generated.py"
_REGENERATE = "python -m fuaran_ui.schema.generate"


def test_the_committed_module_is_a_regeneration_of_the_bundled_idl(tmp_path: Path) -> None:
    out = tmp_path / "generated.py"
    generate.generate(_BUNDLED_IDL, out)
    assert out.read_bytes() == _MODULE.read_bytes(), (
        f"src/fuaran_ui/schema/generated.py differs from a regeneration of {_BUNDLED_IDL.name} — "
        f"never hand-edit it; run `{_REGENERATE}`"
    )


def test_the_recorded_idl_digest_is_the_bundled_idls() -> None:
    digest = hashlib.sha256(_BUNDLED_IDL.read_bytes()).hexdigest()
    assert generated.IDL_SHA256 == digest, (
        "the bundled idl.json moved and the structural module was not regenerated — "
        f"run `{_REGENERATE}` (the corpus sync does it for you)"
    )


def test_the_regeneration_check_goes_red_when_the_idl_moves(tmp_path: Path) -> None:
    """Falsifier for both checks: one added enum case moves the digest AND the module text."""
    idl = json.loads(_BUNDLED_IDL.read_text(encoding="utf-8"))
    idl["enums"][0]["cases"].append("ProbeCase")
    moved = tmp_path / "idl.json"
    moved.write_text(json.dumps(idl, indent=2), encoding="utf-8")

    assert not generate.check(moved, _MODULE), "a moved idl.json must make the committed module stale"
    assert hashlib.sha256(moved.read_bytes()).hexdigest() != generated.IDL_SHA256

    out = tmp_path / "generated.py"
    generate.generate(moved, out)
    assert generate.check(moved, out), "a regeneration from the moved idl.json is current with it"
    assert '"ProbeCase"' in out.read_text(encoding="utf-8")


def test_the_module_carries_every_row_of_the_idl() -> None:
    """Completeness by construction, read back: every IDL kind, enum, union, record and op is a row."""
    idl = json.loads(_BUNDLED_IDL.read_text(encoding="utf-8"))
    assert set(generated.KINDS) == {k["tag"] for k in idl["kinds"]}
    assert {name: list(cases) for name, cases in generated.ENUM_CASES.items()} == {
        e["name"]: e["cases"] for e in idl["enums"]
    }
    assert {name: list(cases) for name, cases in generated.UNION_CASES.items()} == {
        u["name"]: [c["tag"] for c in u["cases"]] for u in idl["unions"]
    }
    assert set(generated.RECORD_FIELDS) == {r["name"] for r in idl["records"]}
    assert set(generated.OP_FIELDS) == {o["tag"] for o in idl["ops"]}
    for kind in idl["kinds"]:
        assert [f.name for f in generated.KIND_FIELDS[kind["tag"]]] == [f["name"] for f in kind["fields"]]
    assert [f.name for f in generated.NODE_FIELDS] == [f["name"] for f in idl["nodeFields"]]


def test_the_check_cli_names_a_stale_module(tmp_path: Path) -> None:
    stale = tmp_path / "generated.py"
    stale.write_text("# not a regeneration\n", encoding="utf-8")
    assert generate.main(["--idl", str(_BUNDLED_IDL), "--out", str(stale), "--check"]) == 1
    assert generate.main(["--idl", str(_BUNDLED_IDL), "--out", str(_MODULE), "--check"]) == 0
