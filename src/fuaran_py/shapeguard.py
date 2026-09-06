"""The §20 / §21 guard for wire text on its way into a decoder.

Every wire-facing entry point in this host shares one shape: hand the text to
``json.loads``, then walk the resulting Python object. This module bounds both
halves of that, and is the single place the §20 decode-determinism rules are
enforced — so a secondary reader (an envelope, a teleport bundle, a theme
manifest, a dataframe payload) answers a §20 row exactly as ``decode_node``
does. §20.1 makes that a normative requirement rather than tidiness: a row binds
an *entry point*, and a host whose readers disagree has an undeclared divergence
rather than a second dialect.

**The syntax pass (on the way down).** :func:`load_bounded` scans the raw text
*before* parsing it. That pass is where the two rules §20.2 says must be
enforced on the way down actually are:

* **syntactic depth** — counted over the raw brackets, so a hostile document is
  refused before ``json.loads`` materialises it. It also fixes a diagnosis: a
  document that is both too deep *and* malformed used to reach the parser and
  come back ``INVALID_JSON``, sending the author to repair the wrong thing,
  where a streaming host answers ``LIMIT_EXCEEDED``;
* **unpaired surrogates** (§20.2 row 6) — a ``\\uD800``–``\\uDBFF`` escape must be
  followed immediately by a ``\\uDC00``–``\\uDFFF`` escape and a low half must be
  preceded by one. A check written over the *assembled* string cannot tell a
  pair from two lone halves, which is why it is here; a raw (unescaped)
  surrogate code unit in the input text is refused by the same pass.

**The parse pass.** ``json.loads`` is given two hooks that close the remaining
§20 rows CPython's parser leaves open: ``parse_constant`` refuses the bare
``NaN`` / ``Infinity`` / ``-Infinity`` tokens (row 4 — §7's *quoted* sentinels
are strings and are unaffected, and row 7's ``1e999`` is a well-formed number
token that never reaches this hook), and ``object_pairs_hook`` refuses a
repeated member (row 1). CPython already answers rows 2, 3 and 5 — trailing
content, the RFC 8259 number grammar, and raw C0 control characters inside a
string — so those need no hook, and the host's suite pins each of them.

``RecursionError`` from the parser is still caught: the depth scan bounds a
*well-formed* nesting, and a defensive catch costs nothing.

**The shape pass.** ``json.loads`` gives no hook for string length or array
width, so :func:`check_shape` walks the *parsed* object, iteratively over an
explicit stack and never recursively — it cannot itself overflow on the input it
exists to refuse, and it runs before the recursive decode walk. The string bound
is counted in Unicode **code points** per §21.6, which is what ``len`` on a
Python ``str`` already measures: a surrogate pair is one code point here because
it is one character, and §20.2 row 6 has already refused the unpaired halves.
"""

from __future__ import annotations

import json
from typing import Any

from .limits import (
    MAX_ARRAY_LENGTH,
    MAX_JSON_DEPTH,
    MAX_STRING_LENGTH,
)
from .result import INVALID_JSON, LIMIT_EXCEEDED, DecodeError

_HEX = "0123456789abcdefABCDEF"


class _WireRefusal(ValueError):
    """A §20 row breached inside ``json.loads``'s own hooks.

    A ``ValueError`` subclass so that a caller which has only the stdlib
    contract in view still sees malformation; :func:`load_bounded` catches it
    first and keeps the specific message.
    """


def _refuse_constant(token: str) -> Any:
    raise _WireRefusal(
        f"the bare `{token}` literal is not a JSON number (§20.2 row 4); "
        'the wire spelling of a non-finite value is the quoted sentinel "NaN" / "Infinity" / "-Infinity"'
    )


def _refuse_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    for key, value in pairs:
        if key in seen:
            raise _WireRefusal(
                f"the object member '{key}' appears more than once (§20.2 row 1); "
                "hosts disagreed on which occurrence wins, so the same bytes meant different trees"
            )
        seen[key] = value
    return seen


def _limit_exceeded_depth() -> DecodeError:
    return DecodeError(
        LIMIT_EXCEEDED,
        "$",
        f"JSON nesting deeper than the wire limit MAX_JSON_DEPTH = {MAX_JSON_DEPTH}",
        f"a document nesting no more than {MAX_JSON_DEPTH} levels deep",
    )


def _invalid_json(message: str) -> DecodeError:
    return DecodeError(INVALID_JSON, "$", message)


def scan_syntax(text: str) -> DecodeError | None:
    """Refuse over-deep nesting and unpaired surrogates from the RAW text.

    One linear pass, no recursion and no allocation beyond the counters. Returns
    ``None`` when the text breaches neither rule; it is deliberately *not* a
    parser and says nothing about whether the text is valid JSON.
    """
    depth = 0
    i = 0
    n = len(text)
    in_string = False

    while i < n:
        ch = text[i]

        if in_string:
            if ch == "\\":
                # An escape. Only \u carries a surrogate half.
                if i + 1 < n and text[i + 1] == "u" and i + 5 < n and all(c in _HEX for c in text[i + 2 : i + 6]):
                    code = int(text[i + 2 : i + 6], 16)
                    if 0xD800 <= code <= 0xDBFF:
                        # A high half must be followed IMMEDIATELY by a low half.
                        low = text[i + 6 : i + 12]
                        paired = (
                            len(low) == 6
                            and low[0] == "\\"
                            and low[1] == "u"
                            and all(c in _HEX for c in low[2:])
                            and 0xDC00 <= int(low[2:], 16) <= 0xDFFF
                        )
                        if not paired:
                            return _invalid_json(
                                f"an unpaired HIGH surrogate escape \\u{code:04X} (§20.2 row 6): a "
                                "\\uD800-\\uDBFF escape must be followed immediately by a \\uDC00-\\uDFFF escape"
                            )
                        i += 12
                        continue
                    if 0xDC00 <= code <= 0xDFFF:
                        # A low half is only ever reached above, as the second
                        # element of a pair — so reaching it here is unpaired.
                        return _invalid_json(
                            f"an unpaired LOW surrogate escape \\u{code:04X} (§20.2 row 6): a "
                            "\\uDC00-\\uDFFF escape must be preceded immediately by a \\uD800-\\uDBFF escape"
                        )
                    i += 6
                    continue
                i += 2
                continue
            if ch == '"':
                in_string = False
            elif "\ud800" <= ch <= "\udfff":
                return _invalid_json(
                    f"a raw unpaired surrogate code unit U+{ord(ch):04X} inside a string (§20.2 row 6)"
                )
            i += 1
            continue

        if ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                return _limit_exceeded_depth()
        elif ch in "}]":
            depth -= 1
        elif "\ud800" <= ch <= "\udfff":
            return _invalid_json(f"a raw unpaired surrogate code unit U+{ord(ch):04X} in the document (§20.2 row 6)")
        i += 1

    return None


def load_bounded(text: str) -> tuple[Any, DecodeError | None]:
    """Parse ``text`` under §20 and §21, mapping every failure onto a typed error.

    Returns ``(value, None)`` on success and ``(None, error)`` on failure. This
    is the ONLY parse entry point in this host: a wire-facing reader that calls
    ``json.loads`` directly answers the §20 rows differently and is an
    undeclared divergent entry point under §20.1.
    """
    syntax_error = scan_syntax(text)
    if syntax_error is not None:
        return None, syntax_error

    try:
        parsed = json.loads(
            text,
            parse_constant=_refuse_constant,
            object_pairs_hook=_refuse_duplicate_keys,
        )
    except _WireRefusal as refusal:
        return None, _invalid_json(str(refusal))
    except RecursionError:
        # The scan above bounds a well-formed nesting; this stays as a defensive
        # catch so decoding is total whatever the parser does.
        return None, DecodeError(
            LIMIT_EXCEEDED,
            "$",
            f"JSON nesting exceeds what this host can parse (the wire limit is MAX_JSON_DEPTH = {MAX_JSON_DEPTH})",
            f"a document nesting no more than {MAX_JSON_DEPTH} levels deep",
        )
    except ValueError:
        return None, _invalid_json("input is not syntactically valid JSON")

    return parsed, None


def check_shape(value: Any) -> DecodeError | None:
    """Bound syntactic depth, string length and array/object width.

    Iterative by construction — an explicit stack, no recursion — so it cannot
    overflow on the input it exists to refuse. Returns ``None`` when the
    document is within every bound.
    """
    # Each frame is (value, depth). Depth counts the outermost value as 1.
    stack: list[tuple[Any, int]] = [(value, 1)]

    while stack:
        current, depth = stack.pop()

        if depth > MAX_JSON_DEPTH:
            return _limit_exceeded_depth()

        if isinstance(current, str):
            # ``len`` on a ``str`` is a count of Unicode code points — the §21.6
            # unit — so an astral character costs one here and not the two a
            # UTF-16-counting host would charge it.
            if len(current) > MAX_STRING_LENGTH:
                return DecodeError(
                    LIMIT_EXCEEDED,
                    "$",
                    f"a string is longer than the wire limit MAX_STRING_LENGTH = {MAX_STRING_LENGTH}",
                    f"strings of no more than {MAX_STRING_LENGTH} code points",
                )
        elif isinstance(current, dict):
            if len(current) > MAX_ARRAY_LENGTH:
                return DecodeError(
                    LIMIT_EXCEEDED,
                    "$",
                    f"an object has more members than the wire limit MAX_ARRAY_LENGTH = {MAX_ARRAY_LENGTH}",
                    f"objects of no more than {MAX_ARRAY_LENGTH} members",
                )
            for key, item in current.items():
                # Keys are strings on the wire and are bounded like any other.
                if len(key) > MAX_STRING_LENGTH:
                    return DecodeError(
                        LIMIT_EXCEEDED,
                        "$",
                        f"a key is longer than the wire limit MAX_STRING_LENGTH = {MAX_STRING_LENGTH}",
                        f"keys of no more than {MAX_STRING_LENGTH} code points",
                    )
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            if len(current) > MAX_ARRAY_LENGTH:
                return DecodeError(
                    LIMIT_EXCEEDED,
                    "$",
                    f"an array is longer than the wire limit MAX_ARRAY_LENGTH = {MAX_ARRAY_LENGTH}",
                    f"arrays of no more than {MAX_ARRAY_LENGTH} elements",
                )
            for item in current:
                stack.append((item, depth + 1))

    return None
