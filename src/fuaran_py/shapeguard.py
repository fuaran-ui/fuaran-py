"""The §21 shape guard for parsed-but-not-yet-decoded documents.

Both wire decoders (``schema.decode`` for nodes, ``ops.decode`` for ops) share
one entry shape: hand the text to ``json.loads``, then walk the resulting Python
object recursively. This module bounds both halves of that.

**The parse half.** CPython's ``json`` is recursive and raises ``RecursionError``
on deep input, which is *not* a ``ValueError`` — so the decoders' ``except
ValueError`` around ``json.loads`` did not catch it and it escaped as a throw.
:func:`load_bounded` catches it and returns the typed refusal §21.2 rule 3
requires. That alone closes the remote-kill class.

**The shape half.** ``json.loads`` gives us no hook for syntactic depth, string
length or array length, and writing a whole parser to get one would be a large
change for limits that are about refusing hostile input rather than about
parsing it precisely. So :func:`check_shape` walks the *parsed* object instead,
and the walk is **iterative over an explicit stack, never recursive**. That
matters twice: it cannot itself overflow on the very input it exists to refuse
(a recursive checker would be the bug it is checking for), and it runs *before*
the recursive decode walk, so the decoder is never entered with a document that
is already known to be out of bounds.

The honest limit of this arrangement, stated rather than glossed: the document
has already been materialised in memory by ``json.loads`` before the shape check
runs, so these bounds are not "on the way down" in the sense §21.2 rule 4 means
for a hand-rolled parser. Two things make that acceptable rather than a hole.
``RecursionError`` already bounds what ``json.loads`` will build, so the
unbounded case is closed by the catch above; and §21.1 is explicit that these
limits bound *structure* and not total payload size, with the transport-level
byte cap remaining the host's own responsibility. The node-depth and node-count
bounds, which are the ones protecting the recursive walk, ARE enforced on the way
down — in the decoders themselves.
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


def load_bounded(text: str) -> tuple[Any, DecodeError | None]:
    """Parse ``text``, mapping both failure modes onto typed errors.

    Returns ``(value, None)`` on success and ``(None, error)`` on failure.

    ``ValueError`` is genuine malformation and stays ``INVALID_JSON``.
    ``RecursionError`` is a well-formed document that is merely too deep, so it
    is ``LIMIT_EXCEEDED`` — reporting it as ``INVALID_JSON`` is what §21.2
    rule 2 forbids, because it sends the author to repair the wrong thing.
    """
    try:
        return json.loads(text), None
    except RecursionError:
        return None, DecodeError(
            LIMIT_EXCEEDED,
            "$",
            f"JSON nesting exceeds what this host can parse (the wire limit is MAX_JSON_DEPTH = {MAX_JSON_DEPTH})",
            f"a document nesting no more than {MAX_JSON_DEPTH} levels deep",
        )
    except ValueError:
        return None, DecodeError(INVALID_JSON, "$", "input is not syntactically valid JSON")


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
            return DecodeError(
                LIMIT_EXCEEDED,
                "$",
                f"JSON nesting deeper than the wire limit MAX_JSON_DEPTH = {MAX_JSON_DEPTH}",
                f"a document nesting no more than {MAX_JSON_DEPTH} levels deep",
            )

        if isinstance(current, str):
            if len(current) > MAX_STRING_LENGTH:
                return DecodeError(
                    LIMIT_EXCEEDED,
                    "$",
                    f"a string is longer than the wire limit MAX_STRING_LENGTH = {MAX_STRING_LENGTH}",
                    f"strings of no more than {MAX_STRING_LENGTH} characters",
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
                        f"keys of no more than {MAX_STRING_LENGTH} characters",
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
