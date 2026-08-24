"""Deterministic GFM markdown renderer — cross-host conformance gate (Phase 292).

Loads the workspace-root corpus ``../wire-format-fixtures/markdown/corpus.json``
and asserts the Python renderer (``fuaran_py.renderer.markdown``) reproduces
every ``source -> html`` pair byte-for-byte. The F# reference renderer emits the
corpus; this is the Python leg of the §11.1-style cross-host gate
(``Py == corpus``), which together with the F# and TS legs proves
``F# == TS == Py``. Skipped when the corpus is absent (standalone checkout).

**Destination policy (WIRE_FORMAT §14.1).** A fixture MAY carry a ``policy``
naming the policy the render is performed under. The corpus never carries a
policy as *data* — a policy an emission can supply is a policy a hostile
emission can widen — so the host maps the NAME to a policy it CONSTRUCTS, which
is what :data:`_POLICIES` below is.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py.renderer import markdown
from fuaran_py.renderer.egress import (
    DENY_NON_LOCAL_EGRESS,
    PERMISSIVE_EGRESS,
    EgressClass,
    EgressPolicy,
    ExactHost,
    HostSuffix,
    allow_origin,
)

_MARKDOWN_CORPUS = CORPUS_ROOT / "markdown" / "corpus.json"

# `declaredExample` per §14.1's table: denyNonLocal, plus exact host
# `cdn.example` scoped to MEDIA, plus host suffix `docs.example` scoped to
# HYPERLINK. It is what makes the gate falsifiable in both directions — a host
# that refused every non-local destination unconditionally fails its allowed
# fixtures, and one that ignored the policy fails the denyNonLocal ones.
_DECLARED_EXAMPLE = allow_origin(
    HostSuffix("docs.example"),
    [EgressClass.HYPERLINK],
    allow_origin(ExactHost("cdn.example"), [EgressClass.MEDIA], DENY_NON_LOCAL_EGRESS),
)

_POLICIES: dict[str, EgressPolicy] = {
    "permissive": PERMISSIVE_EGRESS,
    "denyNonLocal": DENY_NON_LOCAL_EGRESS,
    "declaredExample": _DECLARED_EXAMPLE,
}


def _policy_for(fixture: dict) -> EgressPolicy:
    """The policy this fixture's render is performed under.

    An UNKNOWN name fails loudly rather than falling back to permissive: a
    silent fallback turns a fixture this host cannot evaluate into one it
    appears to pass, which is the one failure mode a conformance gate must not
    have.
    """
    name = fixture.get("policy")
    if name is None:
        return PERMISSIVE_EGRESS
    policy = _POLICIES.get(name)
    if policy is None:
        pytest.fail(
            f"fixture {fixture['id']!r} names an unknown destination policy {name!r}; "
            f"this host constructs only {sorted(_POLICIES)} — see WIRE_FORMAT §14.1"
        )
    return policy


def _markdown_fixtures() -> list[dict]:
    if not _MARKDOWN_CORPUS.is_file():
        return []
    return json.loads(_MARKDOWN_CORPUS.read_text(encoding="utf-8"))["fixtures"]


@corpus_required
def test_markdown_corpus_non_empty() -> None:
    assert len(_markdown_fixtures()) > 0


@corpus_required
def test_markdown_corpus_exercises_a_non_permissive_policy() -> None:
    """At least one fixture must render under a non-permissive policy.

    Without this the whole gate runs on the permissive path, and a host that
    never implemented §14.1 at all would be green on every fixture — the gate
    would be measuring the pure renderer and reporting on the policy.
    """
    named = {f.get("policy") for f in _markdown_fixtures()} - {None, "permissive"}
    assert named, "corpus carries no non-permissive `policy` fixture — the §14.1 leg is vacuous"


@corpus_required
@pytest.mark.parametrize("fixture", _markdown_fixtures(), ids=lambda f: f["id"])
def test_markdown_render_matches_corpus(fixture: dict) -> None:
    policy = _policy_for(fixture)
    assert markdown.to_html_with_egress(policy, fixture["source"]) == fixture["html"], fixture["id"]


@corpus_required
@pytest.mark.parametrize(
    "fixture",
    [f for f in _markdown_fixtures() if f.get("policy", "permissive") == "permissive"],
    ids=lambda f: f["id"],
)
def test_pure_to_html_is_the_permissive_case(fixture: dict) -> None:
    """``to_html`` IS ``to_html_with_egress PERMISSIVE_EGRESS``, byte-for-byte.

    Pinned rather than asserted in prose: the pure function is published surface
    that this corpus has fixed since Phase 292, and it must not acquire a policy
    by default in the act of the renderer gaining one.
    """
    assert markdown.to_html(fixture["source"]) == fixture["html"], fixture["id"]
