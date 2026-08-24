"""Destination policy — the rules the corpus exercises only indirectly (§14.1).

The markdown corpus pins the RENDERED BYTES; these pin the policy's own
decisions, so a rule that happens not to be reached by a fixture still cannot
regress silently. Four of them are the ones a re-implementation gets wrong:
label-boundary suffix matching, the last-``@`` userinfo rule, class scoping, and
trailing-root-dot normalisation.
"""

from __future__ import annotations

import pytest

from fuaran_py.renderer.egress import (
    ALL_EGRESS_CLASSES,
    DENY_NON_LOCAL_EGRESS,
    EGRESS_REFUSAL_ATTRIBUTE,
    EGRESS_REFUSAL_URL,
    PERMISSIVE_EGRESS,
    Allowed,
    EgressClass,
    EgressPolicy,
    EgressRule,
    ExactHost,
    HostSuffix,
    Local,
    LocalDenied,
    NonNetwork,
    NonNetworkDenied,
    Rejected,
    Remote,
    UndeclaredOrigin,
    UnsafeUrl,
    allow_origin,
    authority_host,
    check_destination,
    classify_destination,
    egress_refusal_marker,
    has_non_local_egress,
    is_declared_origin,
    normalize_host,
    parse_egress_class,
    sanitize_url_for_egress,
)

# ── Class vocabulary ────────────────────────────────────────────────────────


def test_class_wire_spellings_are_the_camelcase_forms() -> None:
    # `fileRead` is camelCase ON THE WIRE even though the Python name is
    # snake-cased — a marker value that spelled it `file_read` would not match
    # any other host's.
    assert [c.value for c in ALL_EGRESS_CLASSES] == ["hyperlink", "media", "route", "download", "fileRead"]


@pytest.mark.parametrize("spelling", ["fileRead", "FILEREAD", "  fileread  "])
def test_parse_class_is_case_insensitive_and_trims(spelling: str) -> None:
    assert parse_egress_class(spelling) is EgressClass.FILE_READ


def test_parse_unknown_class_is_none_not_ignored() -> None:
    # A policy that quietly dropped a class it did not understand would be
    # BROADER than the one its author wrote.
    assert parse_egress_class("hyperlinks") is None


# ── Host normalisation ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Example.COM", "example.com"),
        ("  example.com  ", "example.com"),
        # One trailing root dot: `example.com.` and `example.com` are the same
        # host to a resolver, so the dotted spelling must not walk past an exact
        # rule.
        ("example.com.", "example.com"),
        # ONE, not all: a second dot is not a root dot.
        ("example.com..", "example.com."),
        ("", ""),
    ],
)
def test_normalize_host(raw: str, expected: str) -> None:
    assert normalize_host(raw) == expected


def test_trailing_root_dot_still_matches_an_exact_rule() -> None:
    policy = allow_origin(ExactHost("cdn.example"), [EgressClass.MEDIA], DENY_NON_LOCAL_EGRESS)
    verdict = check_destination(policy, EgressClass.MEDIA, "https://cdn.example./p.png")
    assert isinstance(verdict, Allowed)


# ── Authority extraction ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/x", "example.com"),
        ("https://EXAMPLE.com:8443/x", "example.com"),
        ("https://example.com?q=1", "example.com"),
        ("https://example.com#frag", "example.com"),
        # `\` counts as `/` when locating the authority, in any mix.
        ("https:/\\example.com/x", "example.com"),
        ("https:\\\\example.com/x", "example.com"),
        # An IPv6 literal keeps its brackets, and its inner colons are not a port.
        ("https://[2001:db8::1]:443/x", "[2001:db8::1]"),
        # Fewer than two slashes is not an authority at all.
        ("https:/example.com/x", None),
        ("https:example.com/x", None),
        ("relative/path", None),
    ],
)
def test_authority_host(url: str, expected: str | None) -> None:
    assert authority_host(url) == expected


def test_userinfo_is_split_at_the_LAST_at_sign() -> None:
    # `https://good.example@evil.example/x` is a request to evil.example. A
    # naive FIRST-`@` split reads it as the opposite, which is the classic
    # credential-confusion spelling an allowlist exists to refuse.
    assert authority_host("https://good.example@evil.example/x") == "evil.example"
    assert authority_host("https://a@b@evil.example/x") == "evil.example"


def test_userinfo_confusion_does_not_reach_a_declared_origin() -> None:
    policy = allow_origin(ExactHost("good.example"), [EgressClass.HYPERLINK], DENY_NON_LOCAL_EGRESS)
    verdict = check_destination(policy, EgressClass.HYPERLINK, "https://good.example@evil.example/x")
    assert verdict == UndeclaredOrigin("evil.example", EgressClass.HYPERLINK)


# ── Origin matching ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("host", "declared"),
    [
        ("docs.example", True),
        ("eu.docs.example", True),
        ("a.b.docs.example", True),
        # A SUFFIX, not a substring: the match requires a label boundary.
        ("notdocs.example", False),
        ("docs.example.evil", False),
    ],
)
def test_host_suffix_matches_only_at_a_label_boundary(host: str, declared: bool) -> None:
    policy = allow_origin(HostSuffix("docs.example"), [EgressClass.HYPERLINK], DENY_NON_LOCAL_EGRESS)
    assert is_declared_origin(policy, EgressClass.HYPERLINK, host) is declared


@pytest.mark.parametrize(
    ("host", "declared"),
    [("cdn.example", True), ("a.cdn.example", False), ("notcdn.example", False)],
)
def test_exact_host_matches_nothing_else(host: str, declared: bool) -> None:
    policy = allow_origin(ExactHost("cdn.example"), [EgressClass.MEDIA], DENY_NON_LOCAL_EGRESS)
    assert is_declared_origin(policy, EgressClass.MEDIA, host) is declared


# ── Class scoping ───────────────────────────────────────────────────────────


def test_a_rule_is_scoped_to_its_declared_classes() -> None:
    policy = allow_origin(ExactHost("cdn.example"), [EgressClass.MEDIA], DENY_NON_LOCAL_EGRESS)
    assert is_declared_origin(policy, EgressClass.MEDIA, "cdn.example")
    assert not is_declared_origin(policy, EgressClass.HYPERLINK, "cdn.example")


def test_a_rule_with_no_classes_permits_nothing() -> None:
    # The RECORD is data and says exactly what it lists. (The `allow_origin`
    # CONSTRUCTOR reads an empty collection the other way — see below.)
    policy = EgressPolicy(rules=(EgressRule(origin=ExactHost("cdn.example"), classes=frozenset()),), allow_local=True)
    for cls in ALL_EGRESS_CLASSES:
        assert not is_declared_origin(policy, cls, "cdn.example")


def test_allow_origin_with_no_classes_means_every_class() -> None:
    policy = allow_origin(ExactHost("cdn.example"), (), DENY_NON_LOCAL_EGRESS)
    for cls in ALL_EGRESS_CLASSES:
        assert is_declared_origin(policy, cls, "cdn.example")


def test_allow_origin_does_not_mutate_the_policy_it_extends() -> None:
    extended = allow_origin(ExactHost("cdn.example"), [EgressClass.MEDIA], DENY_NON_LOCAL_EGRESS)
    assert extended.rules != ()
    assert DENY_NON_LOCAL_EGRESS.rules == ()


# ── Classification ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("", Local()),
        ("/guide#top", Local()),
        ("#anchor", Local()),
        ("https://collector.example/x?s=secret", Remote("collector.example")),
        ("ftp://files.example/x", Remote("files.example")),
        ("mailto:hello@collector.example", NonNetwork("mailto")),
        ("tel:+441234", NonNetwork("tel")),
        # The scheme floor runs FIRST — there is nothing to say about where an
        # unsafe URL points.
        ("javascript:alert(1)", Rejected()),
        ("//evil.example/x", Rejected()),
        # A network scheme with no extractable host is rejected, not remote.
        ("https://", Rejected()),
    ],
)
def test_classify_destination(url: str, expected: object) -> None:
    assert classify_destination(url) == expected


# ── Verdicts + the refusal shape ────────────────────────────────────────────


def test_deny_non_local_permits_same_origin_and_refuses_the_rest() -> None:
    p = DENY_NON_LOCAL_EGRESS
    assert check_destination(p, EgressClass.HYPERLINK, "/guide") == Allowed("/guide")
    assert check_destination(p, EgressClass.HYPERLINK, "https://collector.example/x") == UndeclaredOrigin(
        "collector.example", EgressClass.HYPERLINK
    )
    assert check_destination(p, EgressClass.HYPERLINK, "mailto:a@b.example") == NonNetworkDenied(
        "mailto", EgressClass.HYPERLINK
    )


def test_permissive_permits_everything_the_floor_accepts() -> None:
    p = PERMISSIVE_EGRESS
    for url in ["", "/guide", "https://collector.example/x?s=secret", "mailto:a@b.example"]:
        assert isinstance(check_destination(p, EgressClass.MEDIA, url), Allowed)
    # …and nothing the floor rejects.
    assert check_destination(p, EgressClass.MEDIA, "javascript:alert(1)") == UnsafeUrl()


def test_a_policy_denying_local_refuses_a_relative_destination() -> None:
    p = EgressPolicy(allow_local=False)
    assert check_destination(p, EgressClass.ROUTE, "/guide") == LocalDenied(EgressClass.ROUTE)
    assert egress_refusal_marker(LocalDenied(EgressClass.ROUTE)) == (EGRESS_REFUSAL_ATTRIBUTE, "route:local")


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        (Allowed("/x"), None),
        (UnsafeUrl(), (EGRESS_REFUSAL_ATTRIBUTE, "unsafe-url")),
        (UndeclaredOrigin("evil.example", EgressClass.MEDIA), (EGRESS_REFUSAL_ATTRIBUTE, "media:evil.example")),
        (LocalDenied(EgressClass.HYPERLINK), (EGRESS_REFUSAL_ATTRIBUTE, "hyperlink:local")),
        (NonNetworkDenied("mailto", EgressClass.HYPERLINK), (EGRESS_REFUSAL_ATTRIBUTE, "hyperlink:mailto")),
    ],
)
def test_refusal_marker_shape(verdict: object, expected: object) -> None:
    assert egress_refusal_marker(verdict) == expected  # type: ignore[arg-type]


def test_a_refusal_record_never_carries_the_path_or_query() -> None:
    # The query string of a refused exfiltration attempt IS the payload, so a
    # refusal record that quoted it would become the disclosure it exists to
    # prevent.
    url, markers = sanitize_url_for_egress(
        DENY_NON_LOCAL_EGRESS, EgressClass.MEDIA, "https://collector.example/p.png?s=SECRET&u=me"
    )
    assert url == EGRESS_REFUSAL_URL
    assert markers == [(EGRESS_REFUSAL_ATTRIBUTE, "media:collector.example")]
    assert "SECRET" not in repr(markers)
    assert "p.png" not in repr(markers)


def test_refusal_attribute_survives_the_extra_attribute_gate() -> None:
    from fuaran_py.renderer.sanitize import is_allowed_extra_attribute_key

    assert is_allowed_extra_attribute_key(EGRESS_REFUSAL_ATTRIBUTE)


# ── The manifest-reader question ────────────────────────────────────────────


def test_has_non_local_egress() -> None:
    assert not has_non_local_egress(DENY_NON_LOCAL_EGRESS)
    assert has_non_local_egress(PERMISSIVE_EGRESS)
    assert has_non_local_egress(allow_origin(ExactHost("cdn.example"), [EgressClass.MEDIA], DENY_NON_LOCAL_EGRESS))
    # An empty allowlist reads as "nothing is declared", never "everything is
    # fine" — and a rule scoped to no class declares nothing either.
    empty_rule = EgressPolicy(rules=(EgressRule(origin=ExactHost("cdn.example"), classes=frozenset()),))
    assert not has_non_local_egress(empty_rule)


# ── The renderer seam ───────────────────────────────────────────────────────


def test_scheme_floor_refusal_keeps_its_own_answer_in_the_renderer() -> None:
    from fuaran_py.renderer.markdown import to_html_with_egress

    # The floor's refusal is a DIFFERENT FACT from a policy refusal: bare
    # `about:blank`, no marker, exactly as it has rendered since Phase 292.
    out = to_html_with_egress(DENY_NON_LOCAL_EGRESS, "[click](javascript:alert(1))")
    assert out == '<p><a href="about:blank">click</a></p>\n'


def test_marker_is_emitted_last_after_every_existing_attribute() -> None:
    from fuaran_py.renderer.markdown import to_html_with_egress

    out = to_html_with_egress(DENY_NON_LOCAL_EGRESS, '![alt text](https://evil.example/p.png "Caption")')
    assert out == (
        '<p><img src="about:blank#fuaran-egress-refused" alt="alt text" title="Caption" '
        'data-fuaran-egress-refused="media:evil.example" /></p>\n'
    )


def test_email_autolink_emits_original_bytes_when_permitted() -> None:
    from fuaran_py.renderer.markdown import to_html, to_html_with_egress

    # The `mailto:` is the RENDERER's, so the policy is asked about the
    # destination the renderer is about to emit — but on acceptance the original
    # bytes are emitted, so a permissive render is unchanged to the byte.
    assert to_html_with_egress(PERMISSIVE_EGRESS, "<a@b.example>") == to_html("<a@b.example>")
    assert to_html("<a@b.example>") == '<p><a href="mailto:a@b.example">a@b.example</a></p>\n'


def test_policy_is_threaded_not_global() -> None:
    from fuaran_py.renderer.markdown import to_html_with_egress

    # Two renders under different policies must not affect one another — the
    # reason the policy is a parameter rather than module state.
    src = "[x](https://evil.example/x)"
    strict_before = to_html_with_egress(DENY_NON_LOCAL_EGRESS, src)
    loose = to_html_with_egress(PERMISSIVE_EGRESS, src)
    strict_after = to_html_with_egress(DENY_NON_LOCAL_EGRESS, src)
    assert strict_before == strict_after
    assert loose == '<p><a href="https://evil.example/x">x</a></p>\n'
