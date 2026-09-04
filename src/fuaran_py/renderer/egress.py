"""Destination policy — typed egress allowlists (WIRE_FORMAT §14.1).

:mod:`fuaran_py.renderer.sanitize` answers *is this URL safe to have*. It does
not answer *is this destination one the composition declared*, and only the
second question closes exfiltration: ``https://collector.example/?s=…`` passes
every rule in the scheme floor — allowlisted scheme, well-formed host, no script
anywhere in it — and in an ``![](…)`` the browser contacts it with **no user act
at all**, because rendering *is* the request.

So the floor gains a second, orthogonal gate: a scheme allowlist says what a URL
may **be**, an origin allowlist says where it may **go**. Both are positive
lists; neither substitutes for the other, and this one runs *after* the other
because there is no point asking where an unsafe URL points.

Two shapes are deliberate and both look like omissions:

* **A rule names a HOST**, never a scheme and never a path. Scheme is already
  reduced to the allowlisted set by the floor, and every "scheme wildcard"
  spelling anyone reaches for (``*://``, ``http*://``, ``https?://``) parses
  differently on different hosts — which makes the wildcard itself the
  vulnerability. Path scoping is likewise refused: a path is not a security
  boundary, and a policy that appears to bound one invites reliance on a bound
  it does not have.
* **The policy is HOST-CONSTRUCTED and never carried on the wire.** A policy an
  emission can supply is a policy a hostile emission can widen, which is not a
  policy. There is deliberately no decoder here.

This module is the Python port of the F# reference (``Sanitize.fs``'s
destination-policy section) and matches it byte-for-byte on the shared markdown
corpus's ``policy``-bearing fixtures.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

# The scheme floor is REUSED, never re-implemented: a second URL parser beside
# the first is a second answer to the same question, and the two would drift
# silently. `_extract_scheme` and `_ascii_lower` are module-private to
# `sanitize` only in the sense that they are not part of this package's public
# API — inside the package they are the single implementation of their rule.
from .sanitize import _ascii_lower, _extract_scheme, sanitize_url

# ── Classes ─────────────────────────────────────────────────────────────────


class EgressClass(Enum):
    """The classes of destination a rule can be scoped to.

    Closed by construction: a policy can say something only about a class this
    enum can name. The *values* are the stable lowercase wire spellings — what
    a refusal marker records — so ``FILE_READ`` is ``"fileRead"`` on the wire
    even though the Python name is snake-cased.
    """

    HYPERLINK = "hyperlink"
    """A rendered ``href`` the user must ACT on — a link, a markdown anchor."""

    MEDIA = "media"
    """A rendered ``src`` the browser fetches with NO user act: an image, a
    stylesheet, a media element. THE exfiltration class — a destination here is
    contacted merely by rendering the tree, which is why it is scoped
    separately from :attr:`HYPERLINK` rather than folded in with it."""

    EMBED = "embed"
    """A third-party DOCUMENT the browser loads into a browsing context with no
    user act (fuaran#1111). Scoped apart from :attr:`MEDIA` because what is
    fetched EXECUTES: a media element renders an asset, an ``<iframe>`` runs
    someone else's page. The class also carries a scheme floor of its own —
    ``https`` and nothing else, not ``http`` and not a schemeless relative
    reference, because a same-origin frame is exactly where ``AllowSameOrigin``
    plus ``AllowScripts`` lets the framed document remove its own sandbox."""

    ROUTE = "route"
    """A navigation the tree asks for."""

    DOWNLOAD = "download"
    """A file download the tree asks for."""

    FILE_READ = "fileRead"
    """A file READ the tree asks for. It carries no URL of its own, but it is
    scoped here so a policy can speak about it in the same vocabulary."""


#: Every class, in wire order. Used by :func:`allow_origin` when a rule is
#: declared without a class scope (which means "every class").
ALL_EGRESS_CLASSES: tuple[EgressClass, ...] = (
    EgressClass.HYPERLINK,
    EgressClass.MEDIA,
    EgressClass.EMBED,
    EgressClass.ROUTE,
    EgressClass.DOWNLOAD,
    EgressClass.FILE_READ,
)


def parse_egress_class(name: str) -> EgressClass | None:
    """Parse a wire spelling, case-insensitively.

    An unknown name is ``None`` rather than a silently-ignored rule: a policy
    that quietly drops a class it did not understand is broader than the one
    its author wrote.
    """
    if name is None:
        return None
    key = _ascii_lower(_trim_ws(name))
    for cls in ALL_EGRESS_CLASSES:
        if _ascii_lower(cls.value) == key:
            return cls
    return None


# ── Origins + rules ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExactHost:
    """Exactly this host. ``example.com`` matches ``example.com`` and nothing
    else — not ``a.example.com``, not ``notexample.com``."""

    host: str


@dataclass(frozen=True, slots=True)
class HostSuffix:
    """This host and any subdomain of it.

    ``example.com`` matches ``example.com`` and ``a.b.example.com``; it never
    matches ``notexample.com``, because the match requires a **label
    boundary**. This is the "registrable suffix" spelling — a suffix, not a
    substring, and not a wildcard.
    """

    suffix: str


type EgressOrigin = ExactHost | HostSuffix


@dataclass(frozen=True, slots=True)
class EgressRule:
    """One rule: an origin, and the classes it is declared FOR.

    An EMPTY ``classes`` allows no class — a rule that names nothing permits
    nothing, which is the only reading consistent with a positive list. Use
    :func:`allow_origin` (whose empty-collection argument means "every class")
    when you mean the ergonomic reading.
    """

    origin: EgressOrigin
    classes: frozenset[EgressClass]


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """A typed egress allowlist."""

    rules: tuple[EgressRule, ...] = ()

    allow_any_origin: bool = False
    """When true, EVERY network origin is permitted and ``rules`` is not
    consulted at all.

    This is the escape hatch, and it is a FIELD rather than the absence of
    rules on purpose: an empty allowlist must read as "nothing is declared",
    never as "everything is fine". Those are opposite postures, and the empty
    list is what a half-built policy looks like — so conflating them would make
    the failure mode of forgetting to declare anything indistinguishable from
    deciding not to.
    """

    allow_local: bool = False
    """Whether SAME-ORIGIN destinations (a relative path, a fragment, an empty
    URL) are permitted. True in both shipped policies: a tree pointing at its
    own host has not left, and denying it would make ordinary in-app links
    unrenderable."""

    allow_non_network: bool = False
    """Whether destinations with no network host (``mailto:``, ``tel:``) are
    permitted. ``mailto:`` IS an egress channel — a body parameter carries
    arbitrary text off the machine — and it has no host for a rule to name, so
    it cannot be allowlisted, only permitted wholesale."""


# ── Destinations + verdicts ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Local:
    """Same-origin: a relative path, a fragment, an empty URL."""


@dataclass(frozen=True, slots=True)
class Remote:
    """An absolute network destination at this host — lowercased, with
    userinfo, port and any trailing root dot removed."""

    host: str


@dataclass(frozen=True, slots=True)
class NonNetwork:
    """A scheme with no network host for a rule to name (``mailto:``, ``tel:``)."""

    scheme: str


@dataclass(frozen=True, slots=True)
class Rejected:
    """The scheme floor rejected the URL, or it declares a network scheme with
    no extractable host."""


type Destination = Local | Remote | NonNetwork | Rejected


@dataclass(frozen=True, slots=True)
class Allowed:
    """Accepted. Carries the NORMALISED URL to emit — the same string
    :func:`~fuaran_py.renderer.sanitize.sanitize_url` would have returned, so an
    accepting call site needs no second pass."""

    url: str


@dataclass(frozen=True, slots=True)
class UnsafeUrl:
    """The scheme floor rejected it before policy was ever consulted."""


@dataclass(frozen=True, slots=True)
class UndeclaredOrigin:
    """A network destination whose host this policy does not declare for this
    class. Carries the HOST ONLY — never the path or query, which is exactly
    where an exfiltrated payload would be sitting."""

    host: str
    cls: EgressClass


@dataclass(frozen=True, slots=True)
class LocalDenied:
    """A same-origin destination under a policy that denies local egress."""

    cls: EgressClass


@dataclass(frozen=True, slots=True)
class NonNetworkDenied:
    """A hostless scheme under a policy that denies non-network egress."""

    scheme: str
    cls: EgressClass


type EgressVerdict = Allowed | UnsafeUrl | UndeclaredOrigin | LocalDenied | NonNetworkDenied


# ── Host normalisation + authority extraction ───────────────────────────────

#: Network schemes — the ones that reach a host a rule can name. A scheme the
#: floor allows but that is absent here (``mailto``, ``tel``) is non-network.
_NETWORK_SCHEMES = frozenset({"http", "https", "ftp", "sftp"})

#: The four C0 *separators* Python's ``str.isspace()`` reports as whitespace and
#: every other conformant host does not. Excluding them is what makes the trim
#: below answer the same question in Python as it does in F#, TS, Go and Rust —
#: the same divergence WIRE_FORMAT §19 calls out for the scheme floor's own
#: normalisation, which is why ``str.strip()`` appears nowhere in URL handling.
_NON_WHITESPACE_SEPARATORS = "\x1c\x1d\x1e\x1f"


def _is_trim_ws(ch: str) -> bool:
    return ch.isspace() and ch not in _NON_WHITESPACE_SEPARATORS


def _trim_ws(s: str) -> str:
    lo, hi = 0, len(s)
    while lo < hi and _is_trim_ws(s[lo]):
        lo += 1
    while hi > lo and _is_trim_ws(s[hi - 1]):
        hi -= 1
    return s[lo:hi]


def normalize_host(host: str) -> str:
    """Trim, lowercase, and drop a single trailing root dot.

    ``example.com.`` and ``example.com`` are the same host to a resolver, so
    they must be the same host to a policy — otherwise the dotted spelling
    walks straight past an exact rule.

    The fold is **ASCII-only** (the package's cross-host casing primitive): a
    DNS host is ASCII or punycode, and a locale-aware fold answers differently
    per host runtime, which is the one thing a cross-host allowlist cannot
    afford. A host carrying non-ASCII uppercase therefore fails to match a rule
    written lowercase — fail-closed, which is the safe direction.
    """
    if host is None:
        return ""
    t = _ascii_lower(_trim_ws(host))
    if t.endswith("."):
        return t[:-1]
    return t


def authority_host(url: str) -> str | None:
    """Extract the host from an absolute URL's authority, WHATWG-style.

    ``\\`` counts as ``/`` when locating the authority, userinfo before the
    **LAST** ``@`` is discarded, a port is dropped, and an IPv6 literal keeps
    its brackets.

    The last-``@`` rule is load-bearing rather than fussy:
    ``https://good.example@evil.example/x`` is a request to ``evil.example``,
    and a naive first-``@`` split reads it as the opposite — the classic
    credential-confusion spelling an allowlist exists to refuse.

    Hand-rolled rather than delegated to :mod:`urllib.parse`, and that is the
    point: the whole value of this extraction is that every host answers
    identically, and a stdlib URL parser answers its own way.
    """
    colon = url.find(":")
    if colon < 0:
        return None
    i = colon + 1
    slashes = 0
    while i < len(url) and url[i] in "/\\":
        slashes += 1
        i += 1
    if slashes < 2:
        return None
    start = i
    j = i
    while j < len(url) and url[j] not in "/\\?#":
        j += 1
    authority = url[start:j]
    at = authority.rfind("@")
    after_user_info = authority[at + 1 :] if at >= 0 else authority
    if after_user_info == "":
        return None
    if after_user_info.startswith("["):
        close = after_user_info.find("]")
        if close < 0:
            return None
        return _ascii_lower(after_user_info[: close + 1])
    port = after_user_info.find(":")
    h = after_user_info[:port] if port >= 0 else after_user_info
    n = normalize_host(h)
    return n if n != "" else None


# ── The check ───────────────────────────────────────────────────────────────


def classify_destination(url: str) -> Destination:
    """Resolve a URL to the destination a policy reasons about.

    Runs the scheme floor FIRST — there is nothing to say about where an unsafe
    URL points.
    """
    safe = sanitize_url(url)
    if safe is None:
        return Rejected()
    if safe == "":
        return Local()
    scheme = _extract_scheme(safe)
    if scheme is None:
        # No scheme reaching here is same-origin: the floor has already refused
        # every protocol-relative spelling, which is the one schemeless shape
        # that leaves the origin.
        return Local()
    if scheme in _NETWORK_SCHEMES:
        host = authority_host(safe)
        return Remote(host) if host is not None else Rejected()
    return NonNetwork(scheme)


def _origin_matches(origin: EgressOrigin, host: str) -> bool:
    if isinstance(origin, ExactHost):
        h = normalize_host(origin.host)
        return h != "" and h == host
    s = normalize_host(origin.suffix)
    # The label boundary is the whole rule: `endswith(s)` alone would match
    # `notdocs.example` against `docs.example`.
    return s != "" and (host == s or host.endswith("." + s))


def is_declared_origin(policy: EgressPolicy, cls: EgressClass, host: str) -> bool:
    """Is this host declared for this class by this policy?"""
    h = normalize_host(host)
    if h == "":
        return False
    if policy.allow_any_origin:
        return True
    return any(cls in rule.classes and _origin_matches(rule.origin, h) for rule in policy.rules)


def check_destination(policy: EgressPolicy, cls: EgressClass, url: str) -> EgressVerdict:
    """The whole check: scheme floor, then destination policy, for one class."""
    dest = classify_destination(url)
    if isinstance(dest, Rejected):
        return UnsafeUrl()
    if isinstance(dest, Local):
        if policy.allow_local:
            return Allowed(sanitize_url(url) or "")
        return LocalDenied(cls)
    if isinstance(dest, NonNetwork):
        if policy.allow_non_network:
            return Allowed(sanitize_url(url) or "")
        return NonNetworkDenied(dest.scheme, cls)
    if is_declared_origin(policy, cls, dest.host):
        return Allowed(sanitize_url(url) or "")
    return UndeclaredOrigin(dest.host, cls)


def describe_egress_verdict(verdict: EgressVerdict) -> str:
    """Log-safe description of a verdict.

    Carries the HOST and the CLASS, never the URL: a refusal record outlives
    the session, and the query string of a refused exfiltration attempt is the
    payload itself.
    """
    if isinstance(verdict, Allowed):
        return "destination allowed"
    if isinstance(verdict, UnsafeUrl):
        return "destination refused: the URL is not safe to render"
    if isinstance(verdict, UndeclaredOrigin):
        return f"destination refused: origin '{verdict.host}' is not declared for '{verdict.cls.value}' egress"
    if isinstance(verdict, LocalDenied):
        return f"destination refused: this policy denies same-origin '{verdict.cls.value}' egress"
    return f"destination refused: scheme '{verdict.scheme}' has no origin to declare for '{verdict.cls.value}' egress"


# ── The refusal shape ───────────────────────────────────────────────────────

EGRESS_REFUSAL_URL = "about:blank#fuaran-egress-refused"
"""The ``href`` / ``src`` a REFUSED destination renders as.

Deliberately NOT the bare ``about:blank`` the scheme floor emits: a silent
neuter is indistinguishable from an authoring mistake, and "nothing happened"
and "this was refused" are different facts. The fragment is inert in every
browser and greppable in a rendered document.
"""

EGRESS_REFUSAL_ATTRIBUTE = "data-fuaran-egress-refused"
"""The attribute name an emission site attaches beside a refused destination.

Passes :func:`~fuaran_py.renderer.sanitize.is_safe_attribute_name` and the
``data-`` prefix rule by construction, so it survives attribute sanitisation
unchanged.
"""


def egress_refusal_marker(verdict: EgressVerdict) -> tuple[str, str] | None:
    """The refusal marker for a verdict, or ``None`` when it was allowed.

    The VALUE names the class and — where there is one — the host; it never
    carries the URL, for the reason :func:`describe_egress_verdict` gives.
    """
    if isinstance(verdict, Allowed):
        return None
    if isinstance(verdict, UnsafeUrl):
        return (EGRESS_REFUSAL_ATTRIBUTE, "unsafe-url")
    if isinstance(verdict, UndeclaredOrigin):
        return (EGRESS_REFUSAL_ATTRIBUTE, verdict.cls.value + ":" + verdict.host)
    if isinstance(verdict, LocalDenied):
        return (EGRESS_REFUSAL_ATTRIBUTE, verdict.cls.value + ":local")
    return (EGRESS_REFUSAL_ATTRIBUTE, verdict.cls.value + ":" + verdict.scheme)


def sanitize_url_for_egress(policy: EgressPolicy, cls: EgressClass, url: str) -> tuple[str, list[tuple[str, str]]]:
    """The one-call render seam: the URL to emit, plus the refusal attributes.

    An emission site adopts this by replacing its ``sanitize_url_or_blank`` call
    and splicing the returned attribute list — which is the whole adoption, per
    call site.
    """
    verdict = check_destination(policy, cls, url)
    if isinstance(verdict, Allowed):
        return (verdict.url, [])
    marker = egress_refusal_marker(verdict)
    return (EGRESS_REFUSAL_URL, [marker] if marker is not None else [])


def sanitize_embed_src_for_egress(policy: EgressPolicy, url: str) -> tuple[str | None, list[tuple[str, str]]]:
    """The `Embed` seam (fuaran#1111), and it differs from
    :func:`sanitize_url_for_egress` in ONE way that is the whole point: a refusal
    returns ``None`` rather than the refusal URL.

    An `<img src>` at the refusal URL renders a broken image; an `<iframe src>`
    at it RENDERS THAT PAGE. So the attribute is omitted entirely and the refusal
    is recorded beside it — the frame is still emitted, still named and still
    sandboxed, and it simply has nothing in it.

    The scheme floor runs BEFORE the policy and is not a policy rule: `https`
    and nothing else. A policy can widen which ORIGINS an embed may reach; it
    cannot admit a scheme this class refuses.
    """
    normalised = _trim_ws(url)
    if normalised == "" or not _ascii_lower(normalised).startswith("https://"):
        return (None, [(EGRESS_REFUSAL_ATTRIBUTE, "embed:unsafe-url")])
    verdict = check_destination(policy, EgressClass.EMBED, normalised)
    if isinstance(verdict, Allowed):
        return (verdict.url, [])
    marker = egress_refusal_marker(verdict)
    return (None, [marker] if marker is not None else [])


# ── Shipped policies ────────────────────────────────────────────────────────

DENY_NON_LOCAL_EGRESS = EgressPolicy(rules=(), allow_any_origin=False, allow_local=True, allow_non_network=False)
"""Deny every destination that leaves the origin.

THE DEFAULT FOR A DECODED (WIRE) TREE. An emission cannot declare its own
egress, so absent a host's declaration it gets none.
"""

PERMISSIVE_EGRESS = EgressPolicy(rules=(), allow_any_origin=True, allow_local=True, allow_non_network=True)
"""Permit every destination.

The posture for a HAND-AUTHORED tree, where the author is the trust boundary.
Named rather than default so reaching it is a deliberate, greppable act.
"""


def allow_origin(
    origin: EgressOrigin,
    classes: Iterable[EgressClass] = (),
    policy: EgressPolicy = DENY_NON_LOCAL_EGRESS,
) -> EgressPolicy:
    """Declare an origin for a set of classes, returning a new policy.

    An empty ``classes`` is taken as EVERY class — the ergonomic reading of
    "allow this origin", deliberately distinct from an :class:`EgressRule`
    whose ``classes`` is empty, which permits nothing. The two readings are
    split across the constructor and the record on purpose: the record is data
    and says exactly what it lists; the helper is a convenience and says what a
    caller writing one line means.
    """
    chosen = frozenset(classes)
    if not chosen:
        chosen = frozenset(ALL_EGRESS_CLASSES)
    return EgressPolicy(
        rules=policy.rules + (EgressRule(origin=origin, classes=chosen),),
        allow_any_origin=policy.allow_any_origin,
        allow_local=policy.allow_local,
        allow_non_network=policy.allow_non_network,
    )


def has_non_local_egress(policy: EgressPolicy) -> bool:
    """Whether a policy permits anything beyond its own origin — the cheap
    answer to the question a manifest reader asks first."""
    if policy.allow_any_origin or policy.allow_non_network:
        return True
    return any(rule.classes for rule in policy.rules)


__all__ = [
    "ALL_EGRESS_CLASSES",
    "DENY_NON_LOCAL_EGRESS",
    "EGRESS_REFUSAL_ATTRIBUTE",
    "EGRESS_REFUSAL_URL",
    "PERMISSIVE_EGRESS",
    "Allowed",
    "Destination",
    "EgressClass",
    "EgressOrigin",
    "EgressPolicy",
    "EgressRule",
    "EgressVerdict",
    "ExactHost",
    "HostSuffix",
    "Local",
    "LocalDenied",
    "NonNetwork",
    "NonNetworkDenied",
    "Rejected",
    "Remote",
    "UndeclaredOrigin",
    "UnsafeUrl",
    "allow_origin",
    "authority_host",
    "check_destination",
    "classify_destination",
    "describe_egress_verdict",
    "egress_refusal_marker",
    "has_non_local_egress",
    "is_declared_origin",
    "normalize_host",
    "parse_egress_class",
    "sanitize_url_for_egress",
]
