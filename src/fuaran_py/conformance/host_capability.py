"""The host capability manifest generator — ``WIRE_FORMAT.md`` §27, this host's leg (Phase 1582).

A projection-conformance harness meets two kinds of shortfall and they have opposite remedies. A
fixture may exercise a construct THIS HOST cannot author, in which case nothing the harness does will
make it pass; or it may exercise one the host models perfectly well and the harness does not emit, in
which case holding it aside hides the harness's own lag. Until §27 the two were told apart by a
hand-written list of fixture ids beside the harness — exactly as honest as its last re-measurement,
and decaying silently in both directions.

This module publishes the half of that judgement that belongs to the host: the set of wire constructs
**this host can author**, generated from the host's own type model. A consumer then computes its
expected-unmodelled fixture set as *corpus minus manifest* rather than listing it.

**Not to be confused with** :mod:`fuaran_py.ui.capability`, which is the invocable-capability
host-registration seam (``Binding.Invoke``). The word is the same and the subject is not: that module
registers behaviour a document may reference; this one describes what documents this host can write.

What the generator reads, and why that and not something else
-------------------------------------------------------------

The **authoring** model, never the decode tables. ``decode.BINDING_CASES`` carries ``Expr`` because
the decoder accepts it; the authoring alias :data:`fuaran_py.schema.types.Binding` does not, and a
manifest built from the decode side would declare a construct this host has no spelling for — the
over-declaration §27.1 forbids, which sends a consumer to fix code that is not wrong.

Concretely, one signal does the work: the **discriminator an authoring record writes**, read off its
``to_wire``. Three verdicts, and the third is the interesting one:

``literal``
    ``to_wire`` builds its object with a string-literal tag (``Obj("Static", …)``). The tag is the
    host's spelling of that wire case, and it is what the token carries.
``untagged``
    ``to_wire`` builds no object at all — :class:`~fuaran_py.schema.types.LiteralText` returns the
    bare string, which is the canonical wire form of ``TextSource.Literal``. It contributes no token
    because no document carries a discriminator there, and that is correct rather than a gap.
``underivable``
    ``to_wire`` builds an object whose tag is a **runtime value** (``Obj(self.kind, {})``). Nothing
    static can enumerate what that host record can spell, so the union it belongs to is put OUT OF
    SCOPE (§27.3) rather than declared from the members that happen to be readable. Declaring it
    would report every column-kind in the corpus as unmodelled.

Coverage, and why the field families are not in it
--------------------------------------------------

Covered: ``kinds``, and ``unionCases`` narrowed by ``scope`` to the unions whose members all yield a
verdict above. Not covered: ``kindFields`` / ``caseFields`` / ``recordFields`` / ``hostedCases``.

The field families are excluded on evidence, not preference. A record's wire keys are readable from
``to_wire`` only where they are written as dict literals; five kinds assign at least one key
conditionally (``fields["autoAdvanceMs"] = …``) and would lose it, and an under-declared field token
turns a fixture that PASSES into a false quarantine — the one failure direction §27.1 calls
unrecoverable in practice, because it accuses the consumer.

``hostedCases`` is excluded for a sharper reason. The compute layer encodes through an ``isinstance``
ladder in :mod:`fuaran_py.dataframe.codec` rather than through per-record ``to_wire``, so neither the
tags it writes nor their absence can be read off the type model at all. Declaring the family would
mean writing those tags by hand, which is the artefact this whole mechanism exists to remove.
"""

from __future__ import annotations

import argparse
import dataclasses
import inspect
import json
import re
import sys
import types as pytypes
import typing
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from .. import __version__
from ..schema import types as _types
from ..schema.decode import KNOWN_KINDS
from ..ui import compute as _compute

__all__ = [
    "MANIFEST_FORMAT",
    "HOST_ID",
    "build",
    "render",
    "main",
]

#: The §27.3 format identifier. A consumer that does not recognise it refuses the document.
MANIFEST_FORMAT = "fuaran.host-capability/1"

#: This host's identifier in a manifest, and in any cross-host projection of one.
HOST_ID = "fuaran-py"

#: The module a reader runs to regenerate the published artefact.
GENERATOR = "fuaran_py.conformance.host_capability"

#: The spec union names (``idl.json`` ``unions[].name``) this host claims a correspondence for, and
#: the attribute in :mod:`fuaran_py.schema.types` that spells each. A name maps to itself unless the
#: host chose a different one; the three exceptions are recorded rather than silently renamed.
#:
#: This is a NAME correspondence, not a token list: it says which of the host's aliases *is* a given
#: wire union, and every token is still derived from the alias's members. A wrong entry cannot
#: survive — ``tests/test_host_capability_manifest.py`` checks each declared union against the
#: corpus IDL, and a host alias may only ever be a SUBSET of the spec union's cases.
SPEC_UNION_ALIASES: Mapping[str, str] = {
    "Action": "Action",
    "Binding": "Binding",
    "BoxLayout": "BoxLayout",
    "CallResultTarget": "CallResultTarget",
    "CellFormat": "CellFormat",
    "CellKindErased": "AnyColumnKind",
    "ChartAnnotation": "ChartAnnotation",
    "ChartAnnotationRange": "AnnotationRange",
    "ChartAnnotationX": "AnnotationX",
    "ColumnWidth": "ColumnWidth",
    "CurveCommand": "CurveCommand",
    "FormFieldKind": "FormFieldKind",
    "Format": "Format",
    "FragmentArg": "FragmentArg",
    "HoleDecl": "HoleDecl",
    "HoleValueSpace": "HoleValueSpace",
    "LocalFlushTrigger": "LocalFlushTrigger",
    "LocaleSource": "LocaleSource",
    "MediaKind": "MediaKind",
    "Scalar": "Scalar",
    "Shape": "Shape",
    "TextSource": "TextSource",
}

#: Authoring records that reach a wire union from OUTSIDE the alias that names it. The host's
#: ``Binding`` authoring surface spans two modules: the scalar cases in ``schema.types``, and the
#: transform binding in ``ui.compute`` where the dataframe algebra lives. Omitting it would under-
#: declare ``Binding.Transform`` and quarantine every transform fixture, sixteen of which round-trip.
SPEC_UNION_EXTRA_MEMBERS: Mapping[str, tuple[type, ...]] = {
    "Binding": (_compute.TransformBinding,),
}

_OBJ_CALL = re.compile(r"\b_?[Oo]bj\(\s*\S")
_OBJ_LITERAL_TAG = re.compile(r"\b_?[Oo]bj\(\s*[\"']([A-Za-z0-9_]+)[\"']")

Verdict = Literal["literal", "untagged", "underivable"]


def _discriminator(cls: type) -> tuple[Verdict, str | None]:
    """The wire tag an authoring record writes, and how confidently — see the module docstring."""
    to_wire = getattr(cls, "to_wire", None)
    if to_wire is None:
        return ("untagged", None)
    try:
        source = inspect.getsource(to_wire)
    except (OSError, TypeError):  # pragma: no cover — a host shipped without sources
        return ("underivable", None)
    literal = _OBJ_LITERAL_TAG.search(source)
    if literal is not None:
        return ("literal", literal.group(1))
    if _OBJ_CALL.search(source) is not None:
        return ("underivable", None)
    return ("untagged", None)


def _union_members(alias: object) -> tuple[type, ...] | None:
    """The case classes of a union alias, or ``None`` when the object is not one.

    Both spellings are admitted — ``typing.Union[…]`` and PEP 604 ``A | B``, whose origin is
    :class:`types.UnionType`. Checking only the first reports every alias in this host as "not a
    union", because it writes the second.
    """
    if typing.get_origin(alias) not in (typing.Union, pytypes.UnionType):
        return None
    return tuple(a for a in typing.get_args(alias) if isinstance(a, type))


def _union_tokens(module: object) -> tuple[list[str], list[str], dict[str, str]]:
    """``(tokens, scope, declined)`` for the ``unionCases`` family."""
    tokens: list[str] = []
    scope: list[str] = []
    declined: dict[str, str] = {}
    for spec_name, host_name in SPEC_UNION_ALIASES.items():
        alias = getattr(module, host_name, None)
        members = _union_members(alias)
        if members is None:
            declined[spec_name] = f"'{host_name}' is not a union alias in this host's authoring model"
            continue
        verdicts = [(cls, *_discriminator(cls)) for cls in (*members, *SPEC_UNION_EXTRA_MEMBERS.get(spec_name, ()))]
        opaque = [cls.__name__ for cls, verdict, _ in verdicts if verdict == "underivable"]
        if opaque:
            declined[spec_name] = (
                f"{', '.join(sorted(opaque))} writes a discriminator computed at run time, "
                "so this host's cases for it cannot be enumerated statically"
            )
            continue
        scope.append(spec_name)
        tokens.extend(f"{spec_name}.{tag}" for _, verdict, tag in verdicts if verdict == "literal" and tag)
    return (tokens, scope, declined)


def _authoring_records(module: object) -> Iterable[type]:
    """Every dataclass in a module that lowers itself to the wire."""
    for name in dir(module):
        candidate = getattr(module, name, None)
        if isinstance(candidate, type) and dataclasses.is_dataclass(candidate) and hasattr(candidate, "to_wire"):
            yield candidate


def _returns_kind_object(cls: type) -> bool:
    """Whether ``to_wire`` is annotated as returning the flat node-kind object.

    :class:`fuaran_py.schema.types.Kind` is a Protocol — structural, so its implementors cannot be
    enumerated — but its one method is annotated ``-> Obj`` where every other authoring record
    returns the wider ``Value`` / ``WireValue``. That annotation is the host's own statement that a
    record occupies a node's ``kind`` slot, and it is what this reads.
    """
    try:
        annotation = inspect.signature(cls.to_wire).return_annotation  # type: ignore[attr-defined]
    except (ValueError, TypeError):  # pragma: no cover — an unintrospectable callable
        return False
    return str(annotation).rsplit(".", 1)[-1] == "Obj"


def _kind_tokens(module: object, union_members: frozenset[type]) -> list[str]:
    """``Kind.<Tag>`` for every node kind this host can author."""
    tokens: list[str] = []
    for cls in _authoring_records(module):
        if cls in union_members or not _returns_kind_object(cls):
            continue
        verdict, tag = _discriminator(cls)
        if verdict == "literal" and tag:
            tokens.append(f"Kind.{tag}")
    return tokens


def build(
    *,
    module: object | None = None,
    host_version: str | None = None,
    corpus_authority: str | None = None,
) -> dict[str, Any]:
    """The manifest for this host, generated from its authoring model.

    ``module`` is the authoring module to read, defaulting to :mod:`fuaran_py.schema.types`. It is a
    parameter so the generation can be falsified: a stand-in module with one kind removed must
    produce a manifest missing exactly that kind's tokens, which is the §27.1 obligation the gate
    proves rather than asserts.
    """
    source = _types if module is None else module

    union_tokens, union_scope, declined = _union_tokens(source)
    union_members: set[type] = set()
    for spec_name in union_scope:
        members = _union_members(getattr(source, SPEC_UNION_ALIASES[spec_name], None))
        union_members.update(members or ())
        union_members.update(SPEC_UNION_EXTRA_MEMBERS.get(spec_name, ()))

    kind_tokens = _kind_tokens(source, frozenset(union_members))

    declared_kinds = {token.split(".", 1)[1] for token in kind_tokens}
    unknown = sorted(declared_kinds - set(KNOWN_KINDS))
    if unknown:
        # A kind this host can author but cannot decode is not a capability, it is a defect: the
        # round trip a manifest exists to describe is author-then-decode. Refuse to publish it.
        raise ValueError(f"authoring kinds absent from the host's decode vocabulary: {', '.join(unknown)}")

    families: dict[str, Any] = {
        "kinds": {"covered": True},
        "unionCases": {
            "covered": True,
            "scope": sorted(union_scope),
            "reason": _scope_reason(declined),
        },
        "kindFields": {"covered": False, "reason": _FIELD_FAMILY_REASON},
        "caseFields": {"covered": False, "reason": _FIELD_FAMILY_REASON},
        "recordFields": {"covered": False, "reason": _FIELD_FAMILY_REASON},
        "hostedCases": {"covered": False, "reason": _HOSTED_FAMILY_REASON},
    }

    return {
        "$manifest": MANIFEST_FORMAT,
        "host": HOST_ID,
        "hostVersion": __version__ if host_version is None else host_version,
        "corpusAuthority": corpus_authority,
        "generator": GENERATOR,
        "families": families,
        "tokens": sorted(set(union_tokens) | set(kind_tokens)),
    }


_FIELD_FAMILY_REASON = (
    "a record's wire keys are readable from its to_wire only where they are written as dict "
    "literals, and several are assigned conditionally instead, so a derived field set would omit "
    "slots this host models and turn passing fixtures into false quarantines"
)

_HOSTED_FAMILY_REASON = (
    "the compute layer lowers through an isinstance ladder in fuaran_py.dataframe.codec rather than "
    "through per-record to_wire, so neither the discriminators it writes nor their absence can be "
    "read off this host's type model"
)


def _scope_reason(declined: Mapping[str, str]) -> str:
    if not declined:
        return "every wire union this host declares an alias for is in scope"
    parts = "; ".join(f"{name} ({why})" for name, why in sorted(declined.items()))
    return f"out of scope: {parts}"


def render(manifest: Mapping[str, Any]) -> str:
    """The published artefact's exact bytes — stable ordering, two-space indent, trailing newline."""
    return json.dumps(manifest, indent=2, sort_keys=False, ensure_ascii=False) + "\n"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_output() -> Path:
    return _repo_root() / "conformance" / "host-capability-manifest.json"


def _snapshot_authority() -> str | None:
    """The corpus commit this host's bundled snapshot was taken from, when there is one.

    Provenance, never a dependency: nothing in the generation reads the corpus, so an installed
    package with no snapshot beside it produces the same tokens with ``corpusAuthority`` null.
    """
    snapshot = _repo_root() / "conformance" / "corpus" / "snapshot.json"
    if not snapshot.is_file():
        return None
    try:
        recorded = json.loads(snapshot.read_text(encoding="utf-8")).get("authorityCommit")
    except (OSError, ValueError):  # pragma: no cover — a torn snapshot is not a generation failure
        return None
    return recorded if isinstance(recorded, str) else None


def main(argv: Sequence[str] | None = None) -> int:
    """``--check`` (default) compares the published artefact; ``--write`` regenerates it."""
    parser = argparse.ArgumentParser(description="Generate or check this host's capability manifest (WIRE_FORMAT §27).")
    parser.add_argument("--write", action="store_true", help="rewrite the published artefact")
    parser.add_argument("--out", type=Path, default=None, help="the artefact path (default: conformance/)")
    args = parser.parse_args(argv)

    out = _default_output() if args.out is None else args.out
    text = render(build(corpus_authority=_snapshot_authority()))

    if args.write:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out}")
        return 0

    if not out.is_file():
        print(f"{out} does not exist — run: python -m {GENERATOR} --write", file=sys.stderr)
        return 1
    if out.read_text(encoding="utf-8") != text:
        print(f"{out} is stale — run: python -m {GENERATOR} --write", file=sys.stderr)
        return 1
    print(f"{out} is current")
    return 0


if __name__ == "__main__":  # pragma: no cover — the CLI entry point
    raise SystemExit(main())
