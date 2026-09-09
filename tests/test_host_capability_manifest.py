"""The host capability manifest — generation, falsification, and the corpus attestation.

``WIRE_FORMAT.md`` §27 asks three things of a host that publishes a manifest, and this file is where
each becomes executable rather than asserted:

* it is **generated in the gate**, and the gate fails when the published artefact is stale — so the
  artefact cannot quietly become a hand-maintained list;
* the generation is **falsifiable**: removing a construct from the host's model removes exactly that
  construct's tokens and nothing else. Three probes, one per mechanism the generator relies on —
  a kind, a union case, and the runtime-discriminator verdict that puts a union out of scope;
* the declared correspondence between this host's aliases and the wire's union names is **checked
  against the corpus IDL**, in the one direction that matters: a host alias may be a SUBSET of a wire
  union's cases (it lags) and may never invent one (it would over-declare).

The generation itself reads no corpus — a manifest is a projection of this host's type model, and
must be producible from an installed package with no sibling checkout. The attestation below is
where the corpus enters, which is why it is corpus-gated and the first two tests are not.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import operator
import typing
from types import SimpleNamespace

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import __version__
from fuaran_py.conformance import host_capability as hc
from fuaran_py.model import Obj
from fuaran_py.schema import types as real_types

PUBLISHED = hc._default_output()


def _authoring_stub(**overrides: object) -> SimpleNamespace:
    """A stand-in for the authoring module, with named attributes replaced or removed.

    ``None`` removes the attribute. This is how the generation is falsified against the REAL
    generator rather than against a re-implementation of it.
    """
    attrs = {name: getattr(real_types, name) for name in dir(real_types) if not name.startswith("__")}
    for name, value in overrides.items():
        if value is None:
            attrs.pop(name, None)
        else:
            attrs[name] = value
    return SimpleNamespace(**attrs)


def _tokens(**overrides: object) -> set[str]:
    return set(hc.build(module=_authoring_stub(**overrides))["tokens"])


BASELINE = set(hc.build()["tokens"])


# ── the artefact is generated, and the gate proves it ─────────────────────────


def test_the_published_manifest_is_current() -> None:
    """The gate's whole job: a manifest regenerated from the model must equal the committed bytes."""
    assert PUBLISHED.is_file(), f"{PUBLISHED} is missing — run: python -m {hc.GENERATOR} --write"
    expected = hc.render(hc.build(corpus_authority=hc._snapshot_authority()))
    assert PUBLISHED.read_text(encoding="utf-8") == expected, (
        f"{PUBLISHED} is stale — run: python -m {hc.GENERATOR} --write"
    )


def test_the_manifest_declares_this_host_and_this_release() -> None:
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    assert published["$manifest"] == hc.MANIFEST_FORMAT
    assert published["host"] == hc.HOST_ID
    # §27.4 rule 5 binds a consumer to the version; a manifest naming the wrong release is worse
    # than none at all, because it looks computed.
    assert published["hostVersion"] == __version__


def test_every_token_lies_in_a_covered_family_and_its_scope() -> None:
    """§27.3: a token outside the covered families and their scope is a defect."""
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    union_scope = set(published["families"]["unionCases"]["scope"])
    for token in published["tokens"]:
        head, _, rest = token.partition(".")
        if head == "Kind":
            assert "." not in rest, f"{token} is a kindFields token, and that family is not covered"
            continue
        assert head in union_scope, f"{token} names a union outside the declared unionCases scope"
        assert "." not in rest, f"{token} is a caseFields token, and that family is not covered"


# ── the generation is falsifiable (§27.1) ────────────────────────────────────


def test_removing_a_kind_removes_exactly_that_kinds_tokens() -> None:
    """The §27.1 obligation, and this phase's acceptance criterion, over the real generator."""
    assert "Kind.Badge" in BASELINE
    assert BASELINE - _tokens(Badge=None) == {"Kind.Badge"}


def test_removing_a_union_case_removes_exactly_that_case_token() -> None:
    cases = [c for c in typing.get_args(real_types.Binding) if c is not real_types.Static]
    assert "Binding.Static" in BASELINE
    assert BASELINE - _tokens(Binding=functools.reduce(operator.or_, cases)) == {"Binding.Static"}


@dataclasses.dataclass(frozen=True)
class _RuntimeTagged:
    """An authoring record whose discriminator is a value, not a literal — the ``ColumnKind`` shape."""

    tag: str

    def to_wire(self) -> Obj:
        return Obj(self.tag, {})


def test_a_runtime_discriminator_takes_its_whole_union_out_of_scope() -> None:
    """The mechanism that keeps a partly-unreadable union from being declared as a set of gaps.

    Without it, a union one of whose members computes its tag would be declared from the members
    that happen to be readable — reporting every document that uses the others as unmodelled.
    """
    widened = functools.reduce(operator.or_, [*typing.get_args(real_types.AnnotationX), _RuntimeTagged])
    stub = _authoring_stub(AnnotationX=widened)
    manifest = hc.build(module=stub)
    scope = manifest["families"]["unionCases"]["scope"]
    assert "ChartAnnotationX" not in scope
    assert not [t for t in manifest["tokens"] if t.startswith("ChartAnnotationX.")]
    assert "_RuntimeTagged" in manifest["families"]["unionCases"]["reason"]
    # …and nothing else moved: the loss is exactly that union's cases.
    assert BASELINE - set(manifest["tokens"]) == {t for t in BASELINE if t.startswith("ChartAnnotationX.")}


def test_the_two_out_of_scope_unions_are_the_recorded_ones() -> None:
    """Pins the decision rather than the count: both exclusions have a stated mechanism.

    ``CellKindErased`` because this host spells a column kind with a runtime discriminator, and
    ``ColumnWidth`` because this host has no union alias for it at all. Either becoming derivable is
    a widening to take deliberately, not a diff to wave through.
    """
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    declared = set(hc.SPEC_UNION_ALIASES) - set(published["families"]["unionCases"]["scope"])
    assert declared == {"CellKindErased", "ColumnWidth"}
    reason = published["families"]["unionCases"]["reason"]
    assert "CellKindErased" in reason and "ColumnWidth" in reason


# ── the corpus attestation ───────────────────────────────────────────────────
#
# `idl.json` and `schemas/` are NOT part of the offline snapshot `conformance/sync_corpus.py`
# copies, so a standalone checkout resolves `CORPUS_ROOT` to the snapshot and has neither. These
# three attest against the authority when it is beside us and skip by name when it is not —
# `corpus_required` alone would fail there, which reads as a defect in this host rather than as an
# absent oracle.


def _idl() -> dict:
    path = CORPUS_ROOT / "idl.json"
    if not path.is_file():
        pytest.skip(f"{path} is absent — the offline snapshot does not carry the IDL")
    return json.loads(path.read_text(encoding="utf-8"))


@corpus_required
def test_no_scoped_union_invents_a_case_the_wire_does_not_have() -> None:
    """The one direction that matters: this host may LAG a wire union, never widen it.

    A declared token the corpus does not know is an over-declaration — the failure §27.1 calls
    unrecoverable, because a consumer reads a genuine host gap as its own lag and is sent to fix
    code that is not wrong.
    """
    idl = _idl()
    spec_cases = {u["name"]: {c["tag"] for c in u["cases"]} for u in idl["unions"]}
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))

    for union in published["families"]["unionCases"]["scope"]:
        assert union in spec_cases, f"'{union}' is in scope but is not a union in the corpus IDL"

    invented = [
        token
        for token in published["tokens"]
        if not token.startswith("Kind.") and token.split(".")[1] not in spec_cases[token.split(".")[0]]
    ]
    assert not invented, f"declared union cases the wire does not have: {sorted(invented)}"


@corpus_required
def test_no_declared_kind_is_outside_the_corpus_vocabulary() -> None:
    idl = _idl()
    spec_kinds = {k["tag"] for k in idl["kinds"]}
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    declared = {t.split(".", 1)[1] for t in published["tokens"] if t.startswith("Kind.")}
    assert not declared - spec_kinds, f"declared kinds the wire does not have: {sorted(declared - spec_kinds)}"


@corpus_required
def test_the_manifest_matches_its_published_schema() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema_path = CORPUS_ROOT / "schemas" / "host-capability-manifest.v1.json"
    if not schema_path.is_file():  # an older corpus snapshot predates §27
        pytest.skip(f"{schema_path} is absent — the corpus predates WIRE_FORMAT §27")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(json.loads(PUBLISHED.read_text(encoding="utf-8")), schema)
