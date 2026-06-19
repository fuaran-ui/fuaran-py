"""The canonical-JSON encoder (WIRE_FORMAT.md §2).

This is the load-bearing half of the codec: two structurally-equal inputs must
produce byte-for-byte identical output across hosts, so the encoder follows the
canonical rules directly rather than delegating to ``json.dumps`` (whose number
and key formatting would *not* match the canonical form):

* object keys sorted by Unicode code point (``$type`` therefore always first);
* numbers in the canonical layout of §2 rule 5 — integers plain, finite floats
  via the shortest-round-trip ``.NET "R"`` layout, specials as quoted
  sentinels, ``-0`` collapsed to ``0``;
* strings escaped per §2 rule 6 only (``"`` / ``\\`` / control chars; everything
  else, including non-ASCII, passes through literally);
* no insignificant whitespace.

``format_finite_double`` re-lays-out CPython's shortest ``repr(float)`` digits
into the canonical form. CPython's ``repr`` produces the same shortest
round-trip *digit sequence* as .NET ``"R"`` / V8 ``Number.toString`` (David Gay
family); only the *layout* differs (exponent threshold, sign padding, case),
which this function pins.
"""

from __future__ import annotations

import math

from .model import Arr, Node, Obj, Value


def format_finite_double(n: float) -> str:
    """Render a finite ``float`` in the canonical ``.NET "R"`` layout (§2 rule 5).

    Fixed-point when the leading-digit base-10 exponent is in ``[-4, 16]``,
    otherwise scientific with an uppercase ``E``, an always-present sign, and a
    ≥2-digit zero-padded exponent. ``-0.0`` collapses to ``"0"``.
    """
    if n == 0.0:  # also collapses -0.0 (-0.0 == 0.0)
        return "0"

    neg = n < 0.0
    s = repr(abs(n))  # CPython shortest round-trip digits

    e_idx = s.find("e")
    if e_idx >= 0:
        mant = s[:e_idx]
        mant_exp = int(s[e_idx + 1 :])
        dot = mant.find(".")
        if dot < 0:
            digits = mant
            exp = mant_exp + (len(mant) - 1)
        else:
            digits = mant[:dot] + mant[dot + 1 :]
            exp = mant_exp + (dot - 1)
    else:
        dot = s.find(".")
        if dot < 0:
            digits = s
            exp = len(s) - 1
        else:
            int_part = s[:dot]
            frac_part = s[dot + 1 :]
            if int_part == "0":
                trimmed = frac_part.lstrip("0")
                leading_zeros = len(frac_part) - len(trimmed)
                digits = frac_part[leading_zeros:]
                exp = -(leading_zeros + 1)
            else:
                digits = int_part + frac_part
                exp = len(int_part) - 1

    # Reduce to shortest significant digits (only trailing zeros can drop).
    digits = digits.rstrip("0") or "0"

    if -4 <= exp <= 16:
        # Fixed-point layout.
        if exp >= 0:
            if len(digits) <= exp + 1:
                out = digits + "0" * (exp + 1 - len(digits))
            else:
                out = digits[: exp + 1] + "." + digits[exp + 1 :]
        else:
            out = "0." + "0" * (-exp - 1) + digits
    else:
        # Scientific layout: uppercase E, signed, ≥2-digit zero-padded exponent.
        mantissa = digits if len(digits) == 1 else digits[0] + "." + digits[1:]
        exp_sign = "+" if exp >= 0 else "-"
        exp_digits = str(abs(exp)).rjust(2, "0")
        out = mantissa + "E" + exp_sign + exp_digits

    return "-" + out if neg else out


def _encode_float(n: float) -> str:
    if math.isnan(n):
        return '"NaN"'
    if math.isinf(n):
        return '"Infinity"' if n > 0 else '"-Infinity"'
    return format_finite_double(n)


def escape_string(s: str) -> str:
    """Quote + escape a string per §2 rule 6 (only ``"`` / ``\\`` / control chars)."""
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch < " ":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _write(value: Value, out: list[str]) -> None:
    # bool is a subclass of int — test it first.
    if value is None:
        out.append("null")
    elif isinstance(value, bool):
        out.append("true" if value else "false")
    elif isinstance(value, int):
        out.append(str(value))
    elif isinstance(value, float):
        out.append(_encode_float(value))
    elif isinstance(value, str):
        out.append(escape_string(value))
    elif isinstance(value, Arr):
        out.append("[")
        for i, item in enumerate(value.items):
            if i:
                out.append(",")
            _write(item, out)
        out.append("]")
    elif isinstance(value, Node):
        fields: dict[str, Value] = {"id": value.id, "kind": value.kind, **value.extras}
        _write_object(fields, out)
    elif isinstance(value, Obj):
        if value.tag is None:
            _write_object(value.fields, out)
        else:
            _write_object({"$type": value.tag, **value.fields}, out)
    else:
        raise TypeError(f"cannot encode value of type {type(value)!r}")


def _write_object(fields: dict[str, Value], out: list[str]) -> None:
    out.append("{")
    first = True
    for key in sorted(fields):  # Unicode-code-point order == StringComparer.Ordinal for ASCII keys
        if not first:
            out.append(",")
        first = False
        out.append(escape_string(key))
        out.append(":")
        _write(fields[key], out)
    out.append("}")


def encode_value(value: Value) -> str:
    """Encode a structural-model value to canonical wire JSON."""
    out: list[str] = []
    _write(value, out)
    return "".join(out)
