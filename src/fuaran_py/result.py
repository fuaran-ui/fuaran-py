"""Decode result + the six canonical ``DecodeError`` codes.

The decode side never throws on malformed input — it returns a structured,
recoverable result mirroring the language-neutral wire contract
(``WIRE_FORMAT.md`` §6): ``{ok: True, value}`` | ``{ok: False, error}``.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── The six canonical decode-error codes (WIRE_FORMAT.md §6) ────────────────
INVALID_JSON = "INVALID_JSON"
MISSING_FIELD = "MISSING_FIELD"
WRONG_TYPE = "WRONG_TYPE"
UNKNOWN_DU_CASE = "UNKNOWN_DU_CASE"
WRONG_NODE_KIND = "WRONG_NODE_KIND"
EMPTY_NODE_ID = "EMPTY_NODE_ID"

CODES = frozenset({INVALID_JSON, MISSING_FIELD, WRONG_TYPE, UNKNOWN_DU_CASE, WRONG_NODE_KIND, EMPTY_NODE_ID})


@dataclass(frozen=True)
class DecodeError:
    """A structured, AI-readable wire-shape violation (WIRE_FORMAT.md §6)."""

    code: str
    path: str
    message: str
    expected_shape: str | None = None


@dataclass(frozen=True)
class Ok[T]:
    """A successful decode carrying the decoded value."""

    value: T

    @property
    def ok(self) -> bool:
        return True


@dataclass(frozen=True)
class Err:
    """A failed decode carrying the canonical error."""

    error: DecodeError

    @property
    def ok(self) -> bool:
        return False


type DecodeResult[T] = Ok[T] | Err
