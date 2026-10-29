"""The unit algebra's text: parse a unit and render its one canonical text (Phase 2207).

The twin of the reference's unit algebra (fuaran-core#426, ``Fuaran.Core.Unit``) over the part a
host needs to carry a column's unit on the wire (WIRE_FORMAT §3.6.26): the PARSE of UCUM's
case-sensitive syntax over the admitted subset, the canonical RENDER, and equality. A unit is a
product of atoms, each under an optional SI prefix, with non-zero integer exponents, held in one
canonical form — factors ordered by symbol then prefix, ordinally; equal factors merged; zero
exponents dropped — so two units are equal exactly when they are one product (``km/h`` and ``m/s``
are different units). The atom table is the reference's in full — each atom's ``metric`` flag
(whether an SI prefix applies), scale and dimensions — and the non-ratio and arbitrary sets are
complete, so every token is refused in the reference's class. Since Phase 2208 the algebra the
dataframe evaluator's unit check reads is here too: :func:`mul_unit`, :func:`div_unit`,
:func:`pow_unit`, :func:`compatible_units` and the exact :func:`conversion_factor`.

Every refusal is a :class:`UnitRefusal` naming its class, the offending token as written and its
position (the zero-based index of the token's first character).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

__all__ = [
    "ANNOTATION",
    "ARBITRARY_UNIT",
    "DIMENSIONLESS",
    "EMPTY",
    "MALFORMED",
    "NON_RATIO_UNIT",
    "NUMERIC_FACTOR",
    "PREFIX_NOT_ALLOWED",
    "UNKNOWN_ATOM",
    "UnitOfMeasure",
    "UnitRefusal",
    "compatible_units",
    "conversion_factor",
    "div_unit",
    "mul_unit",
    "parse_unit",
    "pow_unit",
    "ratio_to_float",
    "refusal_text",
    "render_unit",
]

# ── The admitted vocabulary ──────────────────────────────────────────────────


@dataclass(frozen=True)
class _AtomDef:
    """An admitted atom: whether SI prefixes apply, its scale relative to the coherent base (metre,
    GRAM, second, ampere, kelvin, mole, candela) as ``num/den``, and its exponents over
    ``(length, mass, time, current, temperature, amount, luminous intensity)`` — the reference's
    ``AtomDef``."""

    metric: bool
    num: int
    den: int
    dims: tuple[int, int, int, int, int, int, int]


#: Every admitted atom by its canonical symbol.
_ATOMS: Final[dict[str, _AtomDef]] = {
    # The seven SI base dimensions; mass at the gram, UCUM's base.
    "m": _AtomDef(True, 1, 1, (1, 0, 0, 0, 0, 0, 0)),
    "g": _AtomDef(True, 1, 1, (0, 1, 0, 0, 0, 0, 0)),
    "s": _AtomDef(True, 1, 1, (0, 0, 1, 0, 0, 0, 0)),
    "A": _AtomDef(True, 1, 1, (0, 0, 0, 1, 0, 0, 0)),
    "K": _AtomDef(True, 1, 1, (0, 0, 0, 0, 1, 0, 0)),
    "mol": _AtomDef(True, 1, 1, (0, 0, 0, 0, 0, 1, 0)),
    "cd": _AtomDef(True, 1, 1, (0, 0, 0, 0, 0, 0, 1)),
    # The SI coherent derived units (degree Celsius excepted: it is affine).
    "rad": _AtomDef(True, 1, 1, (0, 0, 0, 0, 0, 0, 0)),
    "sr": _AtomDef(True, 1, 1, (0, 0, 0, 0, 0, 0, 0)),
    "Hz": _AtomDef(True, 1, 1, (0, 0, -1, 0, 0, 0, 0)),
    "N": _AtomDef(True, 1000, 1, (1, 1, -2, 0, 0, 0, 0)),
    "Pa": _AtomDef(True, 1000, 1, (-1, 1, -2, 0, 0, 0, 0)),
    "J": _AtomDef(True, 1000, 1, (2, 1, -2, 0, 0, 0, 0)),
    "W": _AtomDef(True, 1000, 1, (2, 1, -3, 0, 0, 0, 0)),
    "C": _AtomDef(True, 1, 1, (0, 0, 1, 1, 0, 0, 0)),
    "V": _AtomDef(True, 1000, 1, (2, 1, -3, -1, 0, 0, 0)),
    "F": _AtomDef(True, 1, 1000, (-2, -1, 4, 2, 0, 0, 0)),
    "Ohm": _AtomDef(True, 1000, 1, (2, 1, -3, -2, 0, 0, 0)),
    "S": _AtomDef(True, 1, 1000, (-2, -1, 3, 2, 0, 0, 0)),
    "Wb": _AtomDef(True, 1000, 1, (2, 1, -2, -1, 0, 0, 0)),
    "T": _AtomDef(True, 1000, 1, (0, 1, -2, -1, 0, 0, 0)),
    "H": _AtomDef(True, 1000, 1, (2, 1, -2, -2, 0, 0, 0)),
    "lm": _AtomDef(True, 1, 1, (0, 0, 0, 0, 0, 0, 1)),
    "lx": _AtomDef(True, 1, 1, (-2, 0, 0, 0, 0, 0, 1)),
    "Bq": _AtomDef(True, 1, 1, (0, 0, -1, 0, 0, 0, 0)),
    "Gy": _AtomDef(True, 1, 1, (2, 0, -2, 0, 0, 0, 0)),
    "Sv": _AtomDef(True, 1, 1, (2, 0, -2, 0, 0, 0, 0)),
    "kat": _AtomDef(True, 1, 1, (0, 0, -1, 0, 0, 1, 0)),
    # Metric non-SI units.
    "L": _AtomDef(True, 1, 1000, (3, 0, 0, 0, 0, 0, 0)),
    "t": _AtomDef(True, 1000000, 1, (0, 1, 0, 0, 0, 0, 0)),
    "bar": _AtomDef(True, 100000000, 1, (-1, 1, -2, 0, 0, 0, 0)),
    # Non-metric units: time, ratios, and the international customary set.
    "min": _AtomDef(False, 60, 1, (0, 0, 1, 0, 0, 0, 0)),
    "h": _AtomDef(False, 3600, 1, (0, 0, 1, 0, 0, 0, 0)),
    "d": _AtomDef(False, 86400, 1, (0, 0, 1, 0, 0, 0, 0)),
    "wk": _AtomDef(False, 604800, 1, (0, 0, 1, 0, 0, 0, 0)),
    "a": _AtomDef(False, 31557600, 1, (0, 0, 1, 0, 0, 0, 0)),
    "%": _AtomDef(False, 1, 100, (0, 0, 0, 0, 0, 0, 0)),
    "[ppm]": _AtomDef(False, 1, 1000000, (0, 0, 0, 0, 0, 0, 0)),
    "[ppb]": _AtomDef(False, 1, 1000000000, (0, 0, 0, 0, 0, 0, 0)),
    "[in_i]": _AtomDef(False, 127, 5000, (1, 0, 0, 0, 0, 0, 0)),
    "[ft_i]": _AtomDef(False, 381, 1250, (1, 0, 0, 0, 0, 0, 0)),
    "[yd_i]": _AtomDef(False, 1143, 1250, (1, 0, 0, 0, 0, 0, 0)),
    "[mi_i]": _AtomDef(False, 201168, 125, (1, 0, 0, 0, 0, 0, 0)),
    "[lb_av]": _AtomDef(False, 45359237, 100000, (0, 1, 0, 0, 0, 0, 0)),
    "[oz_av]": _AtomDef(False, 45359237, 1600000, (0, 1, 0, 0, 0, 0, 0)),
}

#: Spellings read as another atom: UCUM's lower-case litre. Rendered as the canonical symbol.
_ALIASES: Final[dict[str, str]] = {"l": "L"}

#: UCUM's twenty prefixes and their powers of ten, in their TRY order — ``da`` first, so that
#: ``dam`` reads as decametre (a dict keeps insertion order).
_PREFIX_POWERS: Final[dict[str, int]] = {
    "da": 1,
    "Y": 24,
    "Z": 21,
    "E": 18,
    "P": 15,
    "T": 12,
    "G": 9,
    "M": 6,
    "k": 3,
    "h": 2,
    "d": -1,
    "c": -2,
    "m": -3,
    "u": -6,
    "n": -9,
    "p": -12,
    "f": -15,
    "a": -18,
    "z": -21,
    "y": -24,
}

#: UCUM special units whose conversion is not a ratio: affine temperatures and logarithms.
_NON_RATIO: Final[frozenset[str]] = frozenset(
    {
        "Cel",
        "[degF]",
        "[degRe]",
        "[pH]",
        "Np",
        "B",
        "B[SPL]",
        "B[V]",
        "B[mV]",
        "B[uV]",
        "B[10.nV]",
        "B[W]",
        "B[kW]",
        "bit_s",
        "[p'diop]",
        "%[slope]",
        "[hp_X]",
        "[hp_C]",
        "[hp_M]",
        "[hp_Q]",
        "[kp_X]",
        "[kp_C]",
        "[kp_M]",
        "[kp_Q]",
    }
)

#: UCUM's arbitrary units. Several have a currency's shape (``[CFU]``), so this set is read before
#: the currency rule.
_ARBITRARY: Final[frozenset[str]] = frozenset(
    {
        "[iU]",
        "[IU]",
        "[arb'U]",
        "[USP'U]",
        "[GPL'U]",
        "[MPL'U]",
        "[APL'U]",
        "[beth'U]",
        "[anti'Xa'U]",
        "[todd'U]",
        "[dye'U]",
        "[smgy'U]",
        "[bdsk'U]",
        "[ka'U]",
        "[knk'U]",
        "[mclg'U]",
        "[tb'U]",
        "[CCID_50]",
        "[TCID_50]",
        "[EID_50]",
        "[PFU]",
        "[FFU]",
        "[CFU]",
        "[IR]",
        "[BAU]",
        "[AU]",
        "[Amb'a'1'U]",
        "[PNU]",
        "[Lf]",
        "[D'ag'U]",
        "[FEU]",
        "[ELU]",
        "[EU]",
    }
)

#: Exponents are 32-bit integers in ±2147483647; a written or merged exponent outside is refused.
_MAX_EXP: Final = 2147483647
_EXP_RANGE: Final = "an exponent within ±2147483647"


def _is_upper(c: str) -> bool:
    return "A" <= c <= "Z"


def _is_currency(tok: str) -> bool:
    """An ISO 4217 alphabetic code in brackets — ``[`` three ASCII capitals ``]`` — and not one of
    UCUM's arbitrary units. The SHAPE is checked, not membership of the current code list."""
    return (
        len(tok) == 5
        and tok[0] == "["
        and tok[4] == "]"
        and _is_upper(tok[1])
        and _is_upper(tok[2])
        and _is_upper(tok[3])
        and tok not in _ARBITRARY
    )


def _try_atom(tok: str) -> str | None:
    """The canonical symbol of an admitted atom, through the aliases."""
    canonical = _ALIASES.get(tok, tok)
    return canonical if canonical in _ATOMS else None


# ── The unit and its refusal ─────────────────────────────────────────────────


@dataclass(frozen=True)
class UnitOfMeasure:
    """A unit as a value: its factors ``(symbol, prefix, exponent)`` in the ONE canonical form —
    ordered by symbol then prefix, ordinally; equal factors merged; no zero exponent. Built by
    :func:`parse_unit`; ``str()`` is its canonical text (:func:`render_unit`)."""

    factors: tuple[tuple[str, str, int], ...]

    def __str__(self) -> str:
        return render_unit(self)


#: The dimensionless unit, ``1``.
DIMENSIONLESS: Final = UnitOfMeasure(())

# The refusal classes — the reference's ``UnitRefusal`` cases.
EMPTY: Final = "Empty"
MALFORMED: Final = "Malformed"
UNKNOWN_ATOM: Final = "UnknownAtom"
ANNOTATION: Final = "Annotation"
ARBITRARY_UNIT: Final = "ArbitraryUnit"
NON_RATIO_UNIT: Final = "NonRatioUnit"
PREFIX_NOT_ALLOWED: Final = "PrefixNotAllowed"
NUMERIC_FACTOR: Final = "NumericFactor"


@dataclass(frozen=True)
class UnitRefusal:
    """Why a text is not a unit. ``kind`` is the class; every class but ``Empty`` names the
    offending ``token`` as written and its ``position``. A ``Malformed`` refusal's ``token`` is what
    stands where the grammar left off (empty at the end of the text) and ``expected`` names what
    the grammar wanted instead."""

    kind: str
    token: str = ""
    position: int = 0
    expected: str = ""


def refusal_text(r: UnitRefusal) -> str:
    """The refusal in the reference's words (its columnar codec's ``unitRefusalText``)."""
    if r.kind == EMPTY:
        return "the text is empty"
    if r.kind == MALFORMED:
        return f"expected {r.expected} at position {r.position}, found '{r.token}'"
    what = {
        UNKNOWN_ATOM: "unknown unit symbol",
        ANNOTATION: "annotation",
        ARBITRARY_UNIT: "arbitrary unit",
        NON_RATIO_UNIT: "non-ratio unit",
        PREFIX_NOT_ALLOWED: "prefix not allowed on",
        NUMERIC_FACTOR: "numeric factor",
    }[r.kind]
    return f"{what} '{r.token}' at position {r.position}"


def render_unit(u: UnitOfMeasure) -> str:
    """The unit's ONE canonical text: the positive-exponent factors joined by ``.``, then each
    negative-exponent factor as ``/`` and the factor; an exponent as its magnitude, omitted where
    it is 1; ``1`` for the dimensionless unit; a unit with only negative exponents starts with
    ``/``."""

    def factor(symbol: str, prefix: str, e: int) -> str:
        e = abs(e)
        return prefix + symbol + ("" if e == 1 else str(e))

    num = [factor(s, p, e) for s, p, e in u.factors if e > 0]
    den = [factor(s, p, e) for s, p, e in u.factors if e < 0]
    if not num and not den:
        return "1"
    return ".".join(num) + "".join("/" + t for t in den)


# ── Parse ────────────────────────────────────────────────────────────────────

type _Atom = tuple[str, str]  # (symbol, prefix)
type _Factors = dict[_Atom, int]


def _resolve(tok: str, pos: int) -> _Atom | UnitRefusal:
    """One symbol token to an atom, or to the refusal naming its class."""
    sym = _try_atom(tok)
    if sym is not None:
        return (sym, "")
    if tok in _NON_RATIO:
        return UnitRefusal(NON_RATIO_UNIT, tok, pos)
    if tok in _ARBITRARY:
        return UnitRefusal(ARBITRARY_UNIT, tok, pos)
    if _is_currency(tok):
        return (tok, "")
    for p in _PREFIX_POWERS:
        if len(tok) > len(p) and tok.startswith(p):
            rest = tok[len(p) :]
            sym = _try_atom(rest)
            if sym is not None:
                return (sym, p) if _ATOMS[sym].metric else UnitRefusal(PREFIX_NOT_ALLOWED, tok, pos)
            if rest in _NON_RATIO:
                return UnitRefusal(NON_RATIO_UNIT, tok, pos)
            if rest in _ARBITRARY:
                return UnitRefusal(ARBITRARY_UNIT, tok, pos)
            if _is_currency(rest):
                return (rest, p)
    return UnitRefusal(UNKNOWN_ATOM, tok, pos)


def _try_combine(a: _Factors, sign: int, b: _Factors) -> _Factors | None:
    """``a`` times ``b`` raised to ``sign``, zero exponents dropped; ``None`` where an exponent
    leaves ±2147483647."""
    out = dict(a)
    for atom, e in b.items():
        total = out.get(atom, 0) + sign * e
        if total > _MAX_EXP or total < -_MAX_EXP:
            return None
        out[atom] = total
    return {k: v for k, v in out.items() if v != 0}


def _of_factors(f: _Factors) -> UnitOfMeasure:
    return UnitOfMeasure(tuple((s, p, e) for (s, p), e in sorted(f.items())))


@dataclass
class _Frame:
    """One open term: its product so far, the sign the next component takes, the sign the whole
    term takes in the term that encloses it, and where its ``(`` stood."""

    acc: _Factors
    next: int
    outer: int
    opened: int


def _is_letter(c: str) -> bool:
    return ("a" <= c <= "z") or ("A" <= c <= "Z")


def _is_digit(c: str) -> bool:
    return "0" <= c <= "9"


def _annotation(text: str, pos: int) -> UnitRefusal:
    close = text.find("}", pos)
    if close < 0:
        return UnitRefusal(MALFORMED, text[pos:], pos, "'}' closing the annotation")
    return UnitRefusal(ANNOTATION, text[pos : close + 1], pos)


def parse_unit(text: str) -> UnitOfMeasure | UnitRefusal:  # noqa: C901 — the reference's one loop
    """Read a unit from its text: atoms under an optional prefix, each with an optional signed
    integer exponent; products ``.``, left-associative quotients ``/`` (with an optional leading
    ``/`` at the start of a term), parentheses, and the factor ``1``. No white space is read. The
    reference's iterative parse, so nesting depth never reaches the machine stack."""
    if not text:
        return UnitRefusal(EMPTY)
    n = len(text)
    stack: list[_Frame] = [_Frame({}, 1, 1, 0)]
    pos = 0
    expect_component = True
    term_start = True
    while True:
        if expect_component:
            if pos >= n:
                return UnitRefusal(MALFORMED, "", pos, "a unit")
            c = text[pos]
            if term_start and c == "/":
                stack[-1].next = -1
                pos += 1
                term_start = False
            elif c == "(":
                stack.append(_Frame({}, 1, stack[-1].next, pos))
                pos += 1
                term_start = True
            elif _is_digit(c):
                start = pos
                while pos < n and _is_digit(text[pos]):
                    pos += 1
                if pos < n and text[pos] in "*^":
                    pos += 1
                    if pos < n and text[pos] in "+-":
                        pos += 1
                    while pos < n and _is_digit(text[pos]):
                        pos += 1
                tok = text[start:pos]
                if tok != "1":
                    return UnitRefusal(NUMERIC_FACTOR, tok, start)
                expect_component = False
                term_start = False
            elif c == "{":
                return _annotation(text, pos)
            elif _is_letter(c) or c in "[%":
                start = pos
                while pos < n and (_is_letter(text[pos]) or text[pos] in "_'%["):
                    if text[pos] == "[":
                        close = text.find("]", pos)
                        if close < 0:
                            return UnitRefusal(MALFORMED, text[pos:], pos, "']' closing the symbol")
                        pos = close + 1
                    else:
                        pos += 1
                tok = text[start:pos]
                atom = _resolve(tok, start)
                if isinstance(atom, UnitRefusal):
                    return atom
                exp_start = pos
                sign = 1
                if pos < n and text[pos] in "+-":
                    if text[pos] == "-":
                        sign = -1
                    pos += 1
                digits_start = pos
                value = 0
                over = False
                while pos < n and _is_digit(text[pos]):
                    if not over:
                        value = value * 10 + (ord(text[pos]) - ord("0"))
                        over = value > _MAX_EXP
                    pos += 1
                if pos == digits_start and pos > exp_start:
                    return UnitRefusal(MALFORMED, text[pos : pos + 1], pos, "the digits of an exponent")
                if over:
                    return UnitRefusal(MALFORMED, text[exp_start:pos], exp_start, _EXP_RANGE)
                e = 1 if pos == exp_start else sign * value
                comp: _Factors = {} if e == 0 else {atom: e}
                top = stack[-1]
                acc = _try_combine(top.acc, top.next, comp)
                if acc is None:
                    return UnitRefusal(MALFORMED, text[start:pos], start, _EXP_RANGE)
                top.acc = acc
                expect_component = False
                term_start = False
            else:
                return UnitRefusal(MALFORMED, c, pos, "a unit")
        elif pos >= n:
            if len(stack) == 1:
                return _of_factors(stack[0].acc)
            return UnitRefusal(MALFORMED, "", pos, f"')' closing the '(' at {stack[-1].opened}")
        else:
            c = text[pos]
            if c == ".":
                stack[-1].next = 1
                pos += 1
                expect_component = True
            elif c == "/":
                stack[-1].next = -1
                pos += 1
                expect_component = True
            elif c == ")":
                if len(stack) < 2:
                    return UnitRefusal(MALFORMED, ")", pos, "'.', '/' or the end")
                at = pos
                pos += 1
                inner = stack.pop()
                outer = stack[-1]
                acc = _try_combine(outer.acc, inner.outer, inner.acc)
                if acc is None:
                    return UnitRefusal(MALFORMED, ")", at, _EXP_RANGE)
                outer.acc = acc
            elif c == "{":
                return _annotation(text, pos)
            else:
                expected = "'.', '/' or ')'" if len(stack) > 1 else "'.', '/' or the end"
                return UnitRefusal(MALFORMED, c, pos, expected)


# ── The algebra (Phase 2208) ─────────────────────────────────────────────────
#
# What the evaluator's unit check reads (the reference's ``Unit.mul`` / ``div`` / ``pow`` /
# ``compatible`` / ``conversionFactor``). Where the reference raises because an exponent would
# leave ±2147483647, these answer ``None`` and the caller names the refusal.


def _factors_of(u: UnitOfMeasure) -> _Factors:
    return {(sym, prefix): e for sym, prefix, e in u.factors}


def mul_unit(a: UnitOfMeasure, b: UnitOfMeasure) -> UnitOfMeasure | None:
    """The product of two units, or ``None`` where an exponent would leave ±2147483647."""
    f = _try_combine(_factors_of(a), 1, _factors_of(b))
    return None if f is None else _of_factors(f)


def div_unit(a: UnitOfMeasure, b: UnitOfMeasure) -> UnitOfMeasure | None:
    """The quotient ``a / b``, or ``None`` where an exponent would leave ±2147483647."""
    f = _try_combine(_factors_of(a), -1, _factors_of(b))
    return None if f is None else _of_factors(f)


def pow_unit(u: UnitOfMeasure, n: int) -> UnitOfMeasure | None:
    """``u`` to the integer power ``n`` (``1`` at ``n = 0``), or ``None`` where an exponent leaves
    the range."""
    if n == 0:
        return DIMENSIONLESS
    factors = tuple((sym, prefix, e * n) for sym, prefix, e in u.factors)
    if any(abs(e) > _MAX_EXP for _, _, e in factors):
        return None
    return UnitOfMeasure(factors)


def _dimension(u: UnitOfMeasure) -> dict[str, int]:
    """What a unit measures: non-zero exponents by base dimension, and one per currency."""
    m: dict[str, int] = {}
    for sym, _, e in u.factors:
        if _is_currency(sym):
            m["[" + sym] = m.get("[" + sym, 0) + e
        else:
            for i, x in enumerate(_ATOMS[sym].dims):
                m[str(i)] = m.get(str(i), 0) + x * e
    return {k: x for k, x in m.items() if x != 0}


def compatible_units(a: UnitOfMeasure, b: UnitOfMeasure) -> bool:
    """Whether two units measure one dimension. Every currency is a dimension of its own."""
    return _dimension(a) == _dimension(b)


def _scale(u: UnitOfMeasure) -> tuple[int, int]:
    """The unit's scale relative to the coherent base, ``(num, den)`` in lowest terms."""
    num, den, ten = 1, 1, 0
    for sym, prefix, e in u.factors:
        ten += (_PREFIX_POWERS[prefix] if prefix else 0) * e
        if _is_currency(sym):
            continue
        atom = _ATOMS[sym]
        if e > 0:
            num *= atom.num**e
            den *= atom.den**e
        else:
            num *= atom.den**-e
            den *= atom.num**-e
    if ten > 0:
        num *= 10**ten
    elif ten < 0:
        den *= 10**-ten
    g = math.gcd(num, den)
    return num // g, den // g


def conversion_factor(source: UnitOfMeasure, target: UnitOfMeasure) -> tuple[int, int] | None:
    """The exact factor ``(num, den)`` that turns a value in ``source`` into the same quantity in
    ``target`` (``km/h`` to ``m/s`` is ``(5, 18)``), or ``None`` where they are not compatible."""
    if not compatible_units(source, target):
        return None
    q = div_unit(source, target)
    return None if q is None else _scale(q)


def ratio_to_float(num: int, den: int) -> float:
    """A ratio read into a float literal as the reference reads it: each side to its nearest double,
    then divided (never the correctly-rounded quotient, which can differ in the last place)."""
    return float(num) / float(den)
