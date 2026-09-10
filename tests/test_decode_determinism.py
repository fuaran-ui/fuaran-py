"""WIRE_FORMAT.md §20 / §21 — decode determinism and decoder totality.

The corpus pins each §20 row with a reject fixture, and ``test_reject.py`` runs
them. What it cannot pin is everything below:

* the §21.6 boundary, whose vectors are deliberately host-local (a megabyte of
  padding committed to a shared repository to assert one integer comparison is a
  poor trade) — and which is asserted from BOTH sides and for BOTH alphabets,
  because a host that ports only the BMP pair has not changed its unit and will
  not notice;
* the §20.1 obligation that a row binds an ENTRY POINT rather than a host, which
  is a statement about every reader in this package and about no single fixture;
* the §21.2 rule 3 totality promise at each of those entry points;
* re-entrancy, which is a property of the host and not of the format.

Each is asserted from both sides where a one-sided assertion could pass
vacuously.
"""

from __future__ import annotations

import json
import sys
import threading

import pytest

from fuaran_ui.client.wire import _parse_json
from fuaran_ui.dag import decode_dag_record
from fuaran_ui.dataframe.codec import decode_pipeline, decode_source
from fuaran_ui.elicitation import decode_elicitation
from fuaran_ui.envelope import decode_envelope
from fuaran_ui.limits import MAX_NODE_DEPTH, MAX_STRING_LENGTH
from fuaran_ui.ops.decode import decode_op
from fuaran_ui.result import INVALID_JSON, LIMIT_EXCEEDED
from fuaran_ui.schema.decode import decode_node
from fuaran_ui.shapeguard import check_shape, load_bounded
from fuaran_ui.teleport import _json_loads as decode_teleport_envelope
from fuaran_ui.theme_manifest.decode import decode as decode_theme

# ── §20.2: the rows, at the guard rather than through one decoder ────────────
#
# ``test_reject.py`` runs each row's corpus fixture through ``decode_node``.
# These assert the same rows at the guard every entry point shares, and pair
# each refusal with a CORRECTED TWIN — a refusal-only suite would pass on a
# guard that refused everything.

_ROWS: list[tuple[str, str, str, str]] = [
    ("row 1 — repeated member", '{"a":1,"a":2}', '{"a":1,"b":2}', INVALID_JSON),
    ("row 2 — content after the root value", '{"a":1} {"b":2}', '{"a":1}', INVALID_JSON),
    ("row 3 — leading plus", '{"a":+1}', '{"a":1}', INVALID_JSON),
    ("row 3 — leading zero", '{"a":01}', '{"a":1}', INVALID_JSON),
    ("row 3 — no integer part", '{"a":.5}', '{"a":0.5}', INVALID_JSON),
    ("row 3 — trailing point", '{"a":1.}', '{"a":1}', INVALID_JSON),
    ("row 3 — empty exponent", '{"a":1e}', '{"a":1e1}', INVALID_JSON),
    ("row 4 — bare NaN", '{"a":NaN}', '{"a":"NaN"}', INVALID_JSON),
    ("row 4 — bare Infinity", '{"a":Infinity}', '{"a":"Infinity"}', INVALID_JSON),
    ("row 4 — bare -Infinity", '{"a":-Infinity}', '{"a":"-Infinity"}', INVALID_JSON),
    ("row 5 — raw C0 control character", '{"a":"x\ty"}', '{"a":"x\\ty"}', INVALID_JSON),
    ("row 6 — lone high surrogate", '{"a":"\\ud83d"}', '{"a":"\\ud83d\\ude00"}', INVALID_JSON),
    ("row 6 — lone low surrogate", '{"a":"\\ude00"}', '{"a":"\\ud83d\\ude00"}', INVALID_JSON),
    ("row 6 — split pair", '{"a":"\\ud83d x \\ude00"}', '{"a":"\\ud83d\\ude00 x"}', INVALID_JSON),
    ("row 6 — raw lone surrogate", '{"a":"x\ud83dy"}', '{"a":"xy"}', INVALID_JSON),
]


@pytest.mark.parametrize(("label", "bad", "good", "code"), _ROWS, ids=[r[0] for r in _ROWS])
def test_row_is_refused_and_its_twin_is_accepted(label: str, bad: str, good: str, code: str) -> None:
    _, error = load_bounded(bad)
    assert error is not None, f"{label}: accepted"
    assert error.code == code, f"{label}: {error.code}"
    assert error.path == "$", f"{label}: {error.path}"

    value, twin_error = load_bounded(good)
    assert twin_error is None, f"{label}: the corrected twin was refused — {twin_error}"
    assert check_shape(value) is None


def test_row_7_overflowing_exponent_still_decodes_to_infinity() -> None:
    """The one row that ratifies an ACCEPT, and it sits beside row 4 refusing
    the same three values written as bare literals. ``1e999`` is a well-formed
    JSON number whose value is not representable; ``NaN`` is not a token at all.
    """
    value, error = load_bounded('{"a":1e999,"b":-1e999}')
    assert error is None
    assert value["a"] == float("inf")
    assert value["b"] == float("-inf")


def test_row_8_quoted_sentinels_are_untouched() -> None:
    value, error = load_bounded('{"a":"NaN","b":"Infinity","c":"-Infinity"}')
    assert error is None
    assert value == {"a": "NaN", "b": "Infinity", "c": "-Infinity"}


# ── §21.6: the string bound is counted in CODE POINTS ────────────────────────
#
# Four cases, and the astral pair is the one that fails on a UTF-16-counting or
# byte-counting host: 'x' * max is inside every unit, so a host that ports only
# the BMP pair has not changed its unit and will not notice.


def _text_node(text: str) -> str:
    return json.dumps({"id": "n", "kind": {"$type": "Markdown", "text": text}}, ensure_ascii=False)


def test_bmp_string_at_exactly_the_limit_decodes() -> None:
    assert decode_node(_text_node("a" * MAX_STRING_LENGTH)).ok


def test_bmp_string_one_code_point_over_is_refused() -> None:
    result = decode_node(_text_node("a" * (MAX_STRING_LENGTH + 1)))
    assert not result.ok
    assert result.error.code == LIMIT_EXCEEDED


def test_astral_string_at_exactly_the_limit_decodes() -> None:
    """MAX astral characters: MAX code points, 2·MAX UTF-16 units, 4·MAX UTF-8
    bytes. A host counting either of the other two units refuses this, and the
    document is conformant — refusing it is non-conformance, not caution."""
    assert decode_node(_text_node("\U0001d11e" * MAX_STRING_LENGTH)).ok


def test_astral_string_one_code_point_over_is_refused() -> None:
    result = decode_node(_text_node("\U0001d11e" * (MAX_STRING_LENGTH + 1)))
    assert not result.ok
    assert result.error.code == LIMIT_EXCEEDED


# ── §20.1 + §21.2 rule 3: every entry point, not just the two primary ones ───
#
# ``test_limits.py`` covers ``decode_node`` and ``decode_op``. Eight further
# readers in this package take wire text, and each called ``json.loads``
# directly: a deep payload escaped them as a ``RecursionError`` — the very defect
# §21 was written for, closed at two entry points and open at eight — and each
# answered the §20 rows however CPython's defaults happened to.

# 50 000 levels, not 5 000: CPython's own scanner parses 5 000 levels happily,
# so a shallower vector would pass against a bare ``json.loads`` and prove
# nothing. At this depth the stdlib raises ``RecursionError``, which is not a
# ``ValueError`` and so escaped every ``except ValueError`` in this package.
_DEEP = "[" * 50000 + "]" * 50000
_MALFORMED_DEEP = "[" * 50000


def _entry_points() -> list[tuple[str, object]]:
    return [
        ("decode_node", lambda t: decode_node(t)),
        ("decode_op", lambda t: decode_op(t)),
        ("decode_envelope", lambda t: decode_envelope(t)),
        ("decode_dag_record", lambda t: decode_dag_record(t)),
        ("decode_elicitation", lambda t: decode_elicitation(t)),
        ("teleport envelope", lambda t: decode_teleport_envelope(t)),
        ("decode_source", lambda t: decode_source(t)),
        ("decode_pipeline", lambda t: decode_pipeline(t)),
        ("decode_theme", lambda t: decode_theme(t)),
        ("client _parse_json", lambda t: _parse_json(t)),
    ]


@pytest.mark.parametrize(("name", "reader"), _entry_points(), ids=[n for n, _ in _entry_points()])
@pytest.mark.parametrize("text", [_DEEP, _MALFORMED_DEEP], ids=["well-formed-deep", "malformed-deep"])
def test_deep_input_returns_it_never_raises(name: str, reader: object, text: str) -> None:
    """Every wire-facing reader answers rather than throwing.

    ``RecursionError`` is not a ``ValueError``, so an ``except ValueError``
    around ``json.loads`` never caught it — which is what made ``[[[[…`` a
    one-request remote kill. Some of these readers raise a typed domain error by
    contract (``decode_teleport``, ``decode_theme``) and some return one; both
    are answers. What must not happen is an untyped throw.
    """
    try:
        reader(text)  # type: ignore[operator]
    except (RecursionError, MemoryError) as exc:  # pragma: no cover - the defect
        pytest.fail(f"{name} raised {type(exc).__name__} on deep input")
    except Exception as exc:  # noqa: BLE001 - a typed domain refusal is an answer
        assert type(exc).__name__ in {
            "TeleportError",
            "ValueError",
            "_Fail",
        }, f"{name} raised an unexpected {type(exc).__name__}: {exc}"


@pytest.mark.parametrize(("name", "reader"), _entry_points(), ids=[n for n, _ in _entry_points()])
def test_every_entry_point_refuses_a_repeated_member(name: str, reader: object) -> None:
    """§20.1 rule 1 — the row binds the ENTRY POINT.

    A repeated member is the row that changes what a document MEANS, silently,
    with no error anywhere: this host kept the first occurrence at one reader and
    the last at another. A reader that ACCEPTS this document is answering the row
    differently from ``decode_node``, which §20.1 rule 2 permits only as a
    DECLARED divergence — and there is none to declare here, because every reader
    now shares one parse.
    """
    doc = '{"id":"a","id":"b"}'
    try:
        result = reader(doc)  # type: ignore[operator]
    except Exception:  # noqa: BLE001 - a typed refusal is the answer we want
        return
    ok = getattr(result, "ok", False)
    assert not ok, f"{name} accepted a repeated member"
    if result is not None and not hasattr(result, "ok"):
        # ``_parse_json`` answers ``None``; anything else would be an accept.
        assert result is None, f"{name} accepted a repeated member"


# ── §21 counters are per-walk, not per-process ───────────────────────────────


_OPEN_BOX = (
    '{"id":"n","kind":{"$type":"Box","role":"Group",'
    '"layout":{"$type":"Flex","direction":"Vertical","wrap":false},"children":['
)
_LEAF = (
    '{"id":"leaf","kind":{"$type":"Box","role":"Group",'
    '"layout":{"$type":"Flex","direction":"Vertical","wrap":false},"children":[]}}'
)


def _tree(depth: int, siblings: int = 60) -> str:
    """A chain ``depth`` nodes deep whose innermost level carries ``siblings``
    leaves.

    The width is what makes the interference observable rather than theoretical.
    A 24-node chain decodes in microseconds, so with the default 5 ms switch
    interval two threads essentially never interleave INSIDE one walk and a
    shared counter passes the test it should fail. Widening the innermost level
    makes each walk long enough to be preempted many times over; the tests below
    shorten the switch interval as well, for the same reason.
    """
    inner = ",".join([_LEAF] * siblings)
    return _OPEN_BOX * (depth - 1) + inner + "]}}" * (depth - 1)


def _max_depth_tree() -> str:
    return _tree(MAX_NODE_DEPTH)


def test_concurrent_decodes_of_a_max_depth_tree_all_succeed() -> None:
    """The route shape a threaded host actually runs.

    An ASGI handler dispatched to a thread-pool worker decodes concurrently in
    one process, and this repo ships exactly that as a sample. With the depth and
    node counters as module globals, N walks share one counter: each increments
    what the others are reading, so a document at exactly the limit is refused
    with a LIMIT_EXCEEDED naming a bound it never breached — and, in the other
    direction, a walk unwinding while another descends decrements a counter it
    does not own and lets a document PAST the bound.

    Red against module globals, green against ``ContextVar``s. The tree is at
    EXACTLY the limit so any interference at all shows.
    """
    doc = _max_depth_tree()
    threads = 8
    rounds = 12
    failures: list[str] = []
    barrier = threading.Barrier(threads)

    def worker() -> None:
        barrier.wait()
        for _ in range(rounds):
            result = decode_node(doc)
            if not result.ok:
                failures.append(f"{result.error.code} at {result.error.path}: {result.error.message}")

    workers = [threading.Thread(target=worker) for _ in range(threads)]
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for t in workers:
            t.start()
        for t in workers:
            t.join()
    finally:
        sys.setswitchinterval(previous)

    assert not failures, f"{len(failures)} concurrent decodes of a conformant tree failed: {failures[:3]}"


def test_a_concurrent_over_limit_decode_does_not_let_another_past() -> None:
    """The other direction, which the test above cannot see.

    A shared counter breaks symmetrically: interference can also ADMIT. One set
    of threads decodes a tree one level past the limit while another decodes one
    at the limit; every over-limit decode must be refused and every at-limit
    decode must succeed, whatever the interleaving.
    """
    at_limit = _max_depth_tree()
    over_limit = _tree(MAX_NODE_DEPTH + 1)

    wrongly_accepted: list[str] = []
    wrongly_refused: list[str] = []
    barrier = threading.Barrier(8)

    def over() -> None:
        barrier.wait()
        for _ in range(12):
            if decode_node(over_limit).ok:
                wrongly_accepted.append("over-limit tree decoded")

    def under() -> None:
        barrier.wait()
        for _ in range(12):
            result = decode_node(at_limit)
            if not result.ok:
                wrongly_refused.append(result.error.code)

    threads = [threading.Thread(target=over) for _ in range(4)]
    threads += [threading.Thread(target=under) for _ in range(4)]
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(previous)

    assert not wrongly_accepted, wrongly_accepted[:3]
    assert not wrongly_refused, wrongly_refused[:3]
