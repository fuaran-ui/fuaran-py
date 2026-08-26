"""Decoder robustness fuzz — this host's leg.

The threat model's load-bearing claim is that decoding is TOTAL: a malformed or
hostile input yields a structured, typed error, never an exception and never a
hang. Until a fuzz leg exists on a host, that claim rests there on a CURATED
reject corpus — inputs an author chose, which is evidence about the author's
imagination rather than about the decoder.

This module throws hostile bytes at this host's ``decode_node`` / ``decode_op``
instead. It is the demand-side complement to the generative parity floor in
``tests/test_generative_parity.py``, which generates VALID trees and asserts the
encode round-trip; this one generates inputs a conformant emitter would never
produce and asserts the REFUSAL contract.

**A sibling implementation of the generator strategy, not a transpile.** The same
five input families, expressed in this language's own terms. What is shared is
the CLASSIFICATION, because that is what makes two hosts' fuzz results
comparable; what is deliberately not shared is the byte stream, because it cannot
be — the reference host's hostile alphabet includes lone UTF-16 surrogates, which
a ``str`` on this host cannot encode to UTF-8 at all. A generator claiming
byte-identity across every host would be claiming something false about three of
them.

The five families:

1. corpus-seeded mutation — take a real fixture and corrupt it, with a named
   mutator chain so a find is actionable;
2. near-miss vocabulary — a discriminator one edit away from a real one, read
   from the corpus MANIFEST so a newly-admitted kind is fuzzed the day it lands;
3. structure-aware generation — random JSON assembled from REAL wire keys, so it
   reaches the typed decoders rather than bouncing off the first MISSING_FIELD;
4. crossover — prefix of one seed, suffix of another;
5. pathological — depth, width and string length taken past the §21 limits,
   assembled as TEXT (building one as a nested value would exhaust the recursion
   limit while CONSTRUCTING the input, which proves nothing about the decoder).

Run the long form directly::

    python -m fuaran_py.conformance.decoder_fuzz --iterations 250000 --long \\
        --evidence decoder-fuzz.json

which is what makes the published totality figures regenerable by a scheduled
job rather than by someone remembering to re-run them.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..ops import decode_op, encode_op
from ..result import Ok
from ..schema import decode_node, encode_node

# ─── Deterministic PRNG ─────────────────────────────────────────────────────

_MASK = (1 << 64) - 1
_GOLDEN = 0x9E3779B97F4A7C15


class Rng:
    """SplitMix64.

    Chosen over :mod:`random` because replayability is the whole point of the
    seed: the standard generator's stream is a Mersenne Twister whose seeding is
    an implementation detail, so a repro captured on one interpreter need not
    reproduce on another. This one is four lines of arithmetic and reproduces
    everywhere.
    """

    __slots__ = ("_s",)

    def __init__(self, seed: int) -> None:
        self._s = _GOLDEN if seed == 0 else seed & _MASK

    def next_u64(self) -> int:
        self._s = (self._s + _GOLDEN) & _MASK
        z = self._s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
        return (z ^ (z >> 31)) & _MASK

    def next(self, n: int) -> int:
        """Uniform in ``[0, n)``; ``0`` for a non-positive ``n`` so no caller has to guard."""
        return 0 if n <= 1 else self.next_u64() % n

    def range(self, lo: int, hi: int) -> int:
        """Uniform in ``[lo, hi]``, inclusive."""
        return lo if hi <= lo else lo + self.next(hi - lo + 1)

    def boolean(self) -> bool:
        return self.next_u64() % 2 == 1

    def pick[T](self, xs: Sequence[T]) -> T:
        return xs[self.next(len(xs))]


# ─── Corpus seeds + vocabulary ──────────────────────────────────────────────

#: Built-in seeds, so the harness is self-sufficient: the go-red self-test must
#: not depend on the shared corpus being checked out alongside this repo in order
#: to prove that the harness can fail.
BUILTIN_SEEDS: tuple[str, ...] = (
    '{"id":"a","kind":{"$type":"Heading","level":1,"text":"x","variant":"Standard"}}',
    '{"id":"b","kind":{"$type":"Box","children":[],"layout":{"$type":"Auto"},"role":"Group"}}',
    '{"id":"c","kind":{"$type":"Markdown","source":"# hi"}}',
    '{"$type":"RemoveNode","path":["a"]}',
    '{"$type":"Batch","ops":[]}',
    "{}",
    "[]",
    "null",
    "",
)

_FALLBACK_VOCAB: tuple[str, ...] = (
    "Box",
    "Heading",
    "Markdown",
    "Metric",
    "Badge",
    "Form",
    "Button",
    "DataGrid",
    "Chart",
    "Custom",
)


def load_seeds(corpus_root: Path | None) -> list[str]:
    """Every corpus payload the harness can find, as raw text.

    READ-ONLY by construction: the fuzz never writes into the corpus. A REJECT
    fixture is the most productive seed there is, since it already sits one edit
    away from the refusal boundary the fuzz is probing.
    """
    seeds = list(BUILTIN_SEEDS)
    if corpus_root is None:
        return seeds
    for family in ("nodes", "ops", "reject", "lenient"):
        directory = corpus_root / family
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            if path.name.endswith(".expected.json"):
                continue
            try:
                seeds.append(path.read_text(encoding="utf-8"))
            except OSError:
                # A payload we cannot read is one fewer seed, never a harness failure.
                continue
    return seeds


def load_vocabulary(corpus_root: Path | None) -> list[str]:
    """The wire vocabulary the near-miss generators aim just beside.

    Read from the corpus manifest when available, so a newly-admitted kind is
    fuzzed the day it lands rather than whenever someone remembers to extend a
    literal list here.
    """
    if corpus_root is not None:
        try:
            manifest = json.loads((corpus_root / "manifest.json").read_text(encoding="utf-8"))
            kinds = manifest.get("kinds")
            if isinstance(kinds, list) and kinds:
                return [str(k) for k in kinds]
        except (OSError, json.JSONDecodeError):
            pass
    return list(_FALLBACK_VOCAB)


# ─── Alphabets ──────────────────────────────────────────────────────────────

#: Note what is ABSENT relative to the reference host's list: the lone surrogates
#: U+D800 / U+DFFF. A Python ``str`` can hold them, but the decoder is handed
#: ``str`` and the transport this host is certified over is UTF-8, in which they
#: are unencodable — so including them would fuzz the harness's own plumbing
#: rather than the decoder. The gap is stated rather than silently closed.
_HOSTILE_CHARS: tuple[str, ...] = (
    "{",
    "}",
    "[",
    "]",
    '"',
    ":",
    ",",
    "\\",
    "/",
    "-",
    "+",
    ".",
    "e",
    "E",
    "0",
    "9",
    "n",
    "t",
    "f",
    " ",
    "\t",
    "\n",
    "\r",
    "\x00",
    "\x7f",
    "\ufeff",
    "\u2028",
    "\ufffd",
    "é",
    "中",
)

_HOSTILE_TOKENS: tuple[str, ...] = (
    "null",
    "true",
    "false",
    "{}",
    "[]",
    '""',
    "-0",
    "1e999",
    "-1e999",
    "1E-999",
    "NaN",
    "Infinity",
    "-Infinity",
    "0x10",
    "00",
    "01",
    "1.2.3",
    "+1",
    ".5",
    "5.",
    "\\u0000",
    "\\uD800",
    "\\uFFFF",
    "\\x41",
    "\\",
    '\\"',
    '"$type":""',
    '"$type":null',
    '"id":""',
    '"id":null',
    '"id":[]',
    '"kind":"Heading"',
    '"children":"x"',
    ",",
    ":",
    "[",
    "]",
    "{",
    "}",
    '"',
    "'",
    "/*",
    "*/",
    "//",
    "\x00",
    "\ufeff",
    "\r\n",
)

#: REAL wire keys, so a generated near-miss reaches deep into the typed decoders
#: instead of bouncing off the first MISSING_FIELD. ``__proto__`` / ``__class__``
#: are in the list because a JSON decoder is an attribute-injection surface in
#: every host language that has one, and the shared corpus cannot express a trap
#: that only one host has.
_WIRE_KEYS: tuple[str, ...] = (
    "id",
    "kind",
    "$type",
    "children",
    "layout",
    "role",
    "text",
    "level",
    "variant",
    "source",
    "value",
    "label",
    "fields",
    "items",
    "columns",
    "rows",
    "onSubmit",
    "onClick",
    "required",
    "binding",
    "style",
    "props",
    "state",
    "ops",
    "path",
    "node",
    "index",
    "target",
    "name",
    "format",
    "unit",
    "min",
    "max",
    "options",
    "spec",
    "__proto__",
    "__class__",
    "__dict__",
    "",
    " ",
)

_SCALAR_LITERALS: tuple[str, ...] = (
    "0",
    "-1",
    "1e308",
    "-1e308",
    "1e999",
    "3.141592653589793",
    "true",
    "false",
    "null",
    '""',
    '"x"',
    '"Standard"',
    '"Group"',
    "9007199254740993",
    "-0.0",
)


def _near_miss(rng: Rng, word: str) -> str:
    """A near-miss of a real vocabulary word.

    The class of input a model emitter actually produces, and the class a curated
    reject corpus is worst at covering, because a human writing fixtures reaches
    for obvious garbage.
    """
    if not word:
        return "x"
    choice = rng.next(8)
    if choice == 0:
        return word.lower()
    if choice == 1:
        return word.upper()
    if choice == 2:
        return word + "s"
    if choice == 3:
        return word[:-1]
    if choice == 4:
        return word + " "
    if choice == 5:
        return " " + word
    if choice == 6:
        i = rng.next(len(word))
        return word[:i] + word[i + 1 :]
    i = rng.next(len(word))
    return word[:i] + rng.pick(_HOSTILE_CHARS) + word[i:]


# ─── Mutators ───────────────────────────────────────────────────────────────
#
# Each corrupts a seed payload. Named individually so a reported counterexample
# records WHICH transformation produced it: a find whose provenance is only "the
# fuzzer did something" is markedly harder to act on.

_MUTATOR_NAMES: tuple[str, ...] = (
    "flip-char",
    "delete-span",
    "insert-token",
    "duplicate-span",
    "truncate",
    "transpose",
    "repeat-structural",
    "retype-value",
    "near-miss-type",
    "delete-key",
    "duplicate-key",
    "escape-injection",
    "prefix-junk",
    "suffix-junk",
)


def _near_miss_type(rng: Rng, vocab: Sequence[str], s: str) -> str:
    """Replace the value of a randomly-chosen ``"$type":"…"`` with a near-miss."""
    marker = '"$type":"'
    positions: list[int] = []
    i = s.find(marker)
    while i >= 0:
        positions.append(i)
        i = s.find(marker, i + len(marker))
    if not positions:
        # No discriminator to corrupt — append one rather than returning the
        # input untouched. A silently no-op mutator quietly shrinks the effective
        # iteration count and nothing reports that it did.
        return s + '{"$type":"' + _near_miss(rng, rng.pick(vocab)) + '"}'
    start = positions[rng.next(len(positions))] + len(marker)
    close = s.find('"', start)
    if close < 0:
        return s
    replacement = _near_miss(rng, s[start:close]) if rng.boolean() else _near_miss(rng, rng.pick(vocab))
    return s[:start] + replacement + s[close:]


def _delete_key(rng: Rng, s: str) -> str:
    """Delete a whole ``"key":value`` pair, cutting from the key's opening quote to past the next comma."""
    positions: list[int] = []
    i = s.find('":')
    while i >= 0:
        positions.append(i)
        i = s.find('":', i + 2)
    if not positions:
        return s
    colon = positions[rng.next(len(positions))]
    close_quote = colon
    while close_quote > 0 and s[close_quote] != '"':
        close_quote -= 1
    open_quote = close_quote - 1
    while open_quote > 0 and s[open_quote] != '"':
        open_quote -= 1
    cut_from = max(0, open_quote)
    comma = s.find(",", colon)
    cut_to = min(len(s), colon + 8) if comma < 0 else comma + 1
    return s[:cut_from] + s[cut_to:]


def _mutate_once(rng: Rng, vocab: Sequence[str], cfg: Config, s: str) -> tuple[str, str]:
    name = rng.pick(_MUTATOR_NAMES)
    n = len(s)

    if name == "flip-char" and n > 0:
        i = rng.next(n)
        result = s[:i] + rng.pick(_HOSTILE_CHARS) + s[i + 1 :]
    elif name == "delete-span" and n > 1:
        i = rng.next(n)
        result = s[:i] + s[i + min(n - i, rng.range(1, 8)) :]
    elif name == "insert-token":
        i = rng.next(n + 1)
        result = s[:i] + rng.pick(_HOSTILE_TOKENS) + s[i:]
    elif name == "duplicate-span" and n > 1:
        i = rng.next(n)
        take = min(n - i, rng.range(1, 64))
        at = rng.next(n + 1)
        result = s[:at] + s[i : i + take] + s[at:]
    elif name == "truncate" and n > 1:
        result = s[: rng.next(n)]
    elif name == "transpose" and n > 2:
        i = rng.next(n - 1)
        result = s[:i] + s[i + 1] + s[i] + s[i + 2 :]
    elif name == "repeat-structural":
        ch = rng.pick(("[", "{", '"', "]", "}", ","))
        count = min(rng.range(2, 4096), max(2, cfg.max_payload_chars // 4))
        at = rng.next(n + 1)
        result = s[:at] + ch * count + s[at:]
    elif name == "retype-value" and n > 0:
        i = rng.next(n)
        result = s[:i] + rng.pick(_SCALAR_LITERALS) + s[i + min(n - i, rng.range(1, 12)) :]
    elif name == "near-miss-type":
        result = _near_miss_type(rng, vocab, s)
    elif name == "delete-key":
        result = _delete_key(rng, s)
    elif name == "duplicate-key" and n > 4:
        # A duplicated key is a real emitter defect and a classic cross-host
        # parser divergence (first-wins vs last-wins vs refuse) — §20 of the wire
        # specification records the measured matrix and PROPOSES a rule. Fuzzing
        # it for crashes is in scope here; asserting which behaviour is correct
        # is not, until that rule is ratified.
        i = s.find('"')
        j = -1 if i < 0 else s.find(",", i)
        result = s if j < 0 else s[: j + 1] + s[i:j] + "," + s[j + 1 :]
    elif name == "escape-injection" and n > 0:
        i = rng.next(n)
        result = s[:i] + rng.pick(("\\u", "\\uD800", "\\u00", "\\", "\\/", "\\b\\f")) + s[i:]
    elif name == "prefix-junk":
        result = "".join(rng.pick(_HOSTILE_CHARS) for _ in range(rng.range(1, 16))) + s
    elif name == "suffix-junk":
        result = s + "".join(rng.pick(_HOSTILE_CHARS) for _ in range(rng.range(1, 16)))
    else:
        result = s + rng.pick(_HOSTILE_CHARS)

    return name, result[: cfg.max_payload_chars]


# ─── Structure-aware generation ─────────────────────────────────────────────


def _gen_value(rng: Rng, depth: int, out: list[str], size: list[int], vocab: Sequence[str], cfg: Config) -> None:
    def push(text: str) -> None:
        out.append(text)
        size[0] += len(text)

    if size[0] > cfg.max_payload_chars:
        push("0")
        return
    if depth <= 0:
        push(rng.pick(_SCALAR_LITERALS))
        return

    branch = rng.next(12)
    if branch <= 3:
        push(rng.pick(_SCALAR_LITERALS))
    elif branch <= 7:
        push("{")
        for i in range(rng.range(0, 5)):
            if i > 0:
                push(",")
            push('"' + rng.pick(_WIRE_KEYS) + '":')
            _gen_value(rng, depth - 1, out, size, vocab, cfg)
        push("}")
    elif branch <= 10:
        push("[")
        for i in range(rng.range(0, 5)):
            if i > 0:
                push(",")
            _gen_value(rng, depth - 1, out, size, vocab, cfg)
        push("]")
    else:
        # A plausible node shell around a wrong interior: the shape that gets
        # furthest into the typed decoders before it fails, and so the one most
        # likely to reach code a shallow syntax reject never does.
        push('{"id":"g","kind":{"$type":"')
        push(_near_miss(rng, rng.pick(vocab)))
        push('","')
        push(rng.pick(_WIRE_KEYS) + '":')
        _gen_value(rng, depth - 1, out, size, vocab, cfg)
        push("}}")


def _gen_pathological(rng: Rng, cfg: Config) -> str:
    """Depth, width and string length taken past the §21 limits, assembled as TEXT."""
    cap = cfg.max_payload_chars
    branch = rng.next(9)
    if branch == 0:
        n = min(cap // 2, rng.range(64, 200000))
        return "[" * n + "]" * n
    if branch == 1:
        n = min(cap // 6, rng.range(64, 100000))
        return '{"a":' * n + "1" + "}" * n
    if branch == 2:
        # Unterminated as well as over-deep: the depth guard must fire on the way
        # DOWN, before truncation is ever reached.
        n = min(cap // 2, rng.range(64, 200000))
        return "[" * n
    if branch == 3:
        # Deep NODE nesting rather than deep JSON — crosses the tree depth bound
        # while staying far inside the JSON one, isolating the tree limit.
        acc = '{"id":"leaf","kind":{"$type":"Heading","level":1,"text":"x","variant":"Standard"}}'
        for i in range(1, rng.range(2, 400) + 1):
            if len(acc) >= cap:
                break
            acc = (
                '{"id":"n'
                + str(i)
                + '","kind":{"$type":"Box","children":['
                + acc
                + '],"layout":{"$type":"Auto"},"role":"Group"}}'
            )
        return acc
    if branch == 4:
        n = min(cap // 2, rng.range(1000, 200000))
        return '{"id":"a","kind":[' + ",".join(["1"] * n) + "]}"
    if branch == 5:
        n = min(cap, rng.range(1000, 1200000))
        return '{"id":"a","kind":{"$type":"Heading","level":1,"text":"' + "x" * n + '","variant":"Standard"}}'
    if branch == 6:
        acc = '{"$type":"Batch","ops":[]}'
        for _ in range(rng.range(2, 300)):
            if len(acc) >= cap:
                break
            acc = '{"$type":"Batch","ops":[' + acc + "]}"
        return acc
    if branch == 7:
        # Escape-heavy: nearly every character an escape, so the unescape path
        # does the work rather than the structural walk.
        n = min(cap // 6, rng.range(500, 100000))
        return '{"id":"a","kind":{"$type":"Markdown","source":"' + "\\u0041" * n + '"}}'
    n = min(cap // 4, rng.range(500, 50000))
    return "{" + ",".join(f'"k{i}":1' for i in range(n)) + "}"


# ─── The stream ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Config:
    """Names the stream, so a reported find's replay line reconstructs the exact
    configuration as well as the exact seed. Without it the replay command is only
    approximately right, which is worse than obviously wrong."""

    name: str
    #: The bounded gate run keeps this small so the suite stays a few seconds; the
    #: long run raises it past the §21 string bound so that bound is actually crossed.
    max_payload_chars: int
    #: One in this many inputs is a deliberately pathological (large) payload.
    heavy_every_n: int


BOUNDED_CONFIG = Config(name="bounded", max_payload_chars=48 * 1024, heavy_every_n=120)
LONG_CONFIG = Config(name="long", max_payload_chars=2 * 1024 * 1024, heavy_every_n=25)


@dataclass(frozen=True, slots=True)
class Generated:
    payload: str
    origin: str


def generate(rng: Rng, seeds: Sequence[str], vocab: Sequence[str], cfg: Config, iteration: int) -> Generated:
    """Deterministic in ``(seed, iteration, cfg)`` — the replay contract.

    Every branch draws from the same :class:`Rng`, so ADDING a family renumbers
    the stream; that is why a reported find carries its payload too and replay is
    the backstop rather than the primary record.
    """
    if iteration % cfg.heavy_every_n == 0:
        return Generated(_gen_pathological(rng, cfg), "pathological")

    branch = rng.next(10)
    if branch <= 1:
        out: list[str] = []
        _gen_value(rng, rng.range(1, 6), out, [0], vocab, cfg)
        return Generated("".join(out), "structured-generation")
    if branch == 2:
        return Generated("".join(rng.pick(_HOSTILE_CHARS) for _ in range(rng.range(0, 200))), "raw-junk")
    if branch == 3:
        # Crossover: prefix of one seed, suffix of another. Produces half-valid
        # documents no single-seed mutation reaches.
        a = rng.pick(seeds)
        b = rng.pick(seeds)
        i = 0 if not a else rng.next(len(a))
        j = 0 if not b else rng.next(len(b))
        return Generated(a[:i] + b[j:], "crossover")

    acc = rng.pick(seeds)
    names: list[str] = []
    for _ in range(rng.range(1, 4)):
        name, acc = _mutate_once(rng, vocab, cfg, acc)
        names.append(name)
    return Generated(acc, "mutation:" + "+".join(names))


# ─── Subjects, verdicts, invariants ─────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SubjectResult:
    """What one decode entry point did with one input.

    Deliberately string-typed: the harness compares canonical FORMS, so it needs
    no access to the tree type and both entry points share one machinery.
    """

    refused_code: str | None
    canonical: str | None = None
    #: ``None`` when the decoder's OWN canonical output is refused — a real defect.
    re_decoded: str | None = None
    re_decoded_code: str | None = None


@dataclass(frozen=True, slots=True)
class Subject:
    """One decode entry point, or a deliberately-broken stand-in.

    ``run`` is allowed — required, in the self-test's case — to raise: catching is
    the harness's job.
    """

    name: str
    run: Callable[[str], SubjectResult]


def _round_trip(decode: Callable[[str], Any], encode: Callable[[Any], str]) -> Callable[[str], SubjectResult]:
    def run(text: str) -> SubjectResult:
        first = decode(text)
        if not isinstance(first, Ok):
            return SubjectResult(refused_code=first.error.code)
        canonical = encode(first.value)
        again = decode(canonical)
        if not isinstance(again, Ok):
            return SubjectResult(
                refused_code=None, canonical=canonical, re_decoded=None, re_decoded_code=again.error.code
            )
        return SubjectResult(refused_code=None, canonical=canonical, re_decoded=encode(again.value))

    return run


NODE_SUBJECT = Subject(name="decode_node", run=_round_trip(decode_node, encode_node))
OP_SUBJECT = Subject(name="decode_op", run=_round_trip(decode_op, encode_op))

#: BOTH public entry points, since the totality claim is made about the decoder,
#: not about one of its two doors.
REAL_SUBJECTS: tuple[Subject, ...] = (NODE_SUBJECT, OP_SUBJECT)


#: The ONE observed-and-excluded defect class, named rather than numbered so a
#: reader meets the reason at the point of the exclusion.
#:
#: The wire specification's §5 requires every host to EMIT the quoted ``"NaN"`` /
#: ``"Infinity"`` / ``"-Infinity"`` sentinels for a non-finite number, and its §7
#: requires a decoder to ACCEPT them at a float slot. This host emits them and
#: does not accept them at every such slot, so ``decode → encode → decode`` does
#: not close on a document carrying a non-finite number. The specification already
#: records this as a §7 conformance defect rather than an open question.
#:
#: Excluded here, not because it is unimportant, but because it spans more than
#: one host: fixing it in one alone would manufacture a new divergence of exactly
#: the kind the cross-host parity work exists to close. It is COUNTED and PRINTED
#: on every run, and it disappears on its own the moment the decoder accepts what
#: it emits.
#:
#: Keyed on the CAUSE — a sentinel in the canonical form — never on a fixture id
#: or an iteration number: the seed pool is the shared corpus, so the generated
#: stream renumbers whenever the corpus moves, and an exclusion keyed to an
#: iteration would silence a different defect next week.
KNOWN_NONFINITE_HOLE = "known-nonfinite-roundtrip-hole"

_NON_FINITE_SENTINELS = ('"NaN"', '"Infinity"', '"-Infinity"')


def is_known_nonfinite_hole(canonical: str) -> bool:
    return any(s in canonical for s in _NON_FINITE_SENTINELS)


@dataclass(frozen=True, slots=True)
class Verdict:
    """``kind`` is the coarse class; the rest is detail for the report.

    ``rejected`` and ``clean`` are both PASSES — a fuzz harness that treated
    refusal as failure would be asserting the opposite of the claim under test.
    """

    kind: str
    detail: str = ""

    @property
    def is_counterexample(self) -> bool:
        return self.kind not in ("rejected", "clean", KNOWN_NONFINITE_HOLE)

    @property
    def verdict_class(self) -> str:
        """The class used to hold a failure steady while minimising.

        Deliberately drops the payload-specific detail: a smaller input that fails
        the same WAY is the reduction we want, and demanding byte-identical detail
        would refuse almost every candidate.
        """
        return "held" if not self.is_counterexample else self.kind


@dataclass(frozen=True, slots=True)
class Budgets:
    #: Past this, a decode that DID return is reported as a counterexample.
    soft_time_ms: float = 3000.0
    #: Floor on the canonical form's length for an ORDINARY input.
    amplification_floor_chars: int = 64 * 1024
    #: Allowed canonical-form length per input character, above the floor.
    #:
    #: NOT the reference host's invariant, and the difference is stated rather
    #: than smoothed over. That host measures allocated BYTES per input character
    #: from a per-thread counter this runtime has no equivalent of at per-call
    #: cost — ``tracemalloc`` measures it faithfully and roughly doubles the run.
    #: What is measurable here for free is how much CANONICAL OUTPUT an input
    #: buys, which is the amplification an untrusted producer actually controls on
    #: the accept path. The dedicated ``tracemalloc`` leg in the test suite covers
    #: the allocation question over the pathological family, where it matters; the
    #: pair is the invariant, and neither half is the whole of it.
    max_amplification: int = 64


DEFAULT_BUDGETS = Budgets()


@dataclass(frozen=True, slots=True)
class Measured:
    verdict: Verdict
    elapsed_ms: float
    #: ``0`` for a refused input — there is no canonical form to be disproportionate to.
    canonical_chars: int


def check(subject: Subject, budgets: Budgets, text: str) -> Measured:
    """Run one input through one subject and judge it against every invariant.

    Every exception is caught HERE and nowhere else, which is what makes "no
    exception escapes" a measured property rather than a hope. ``RecursionError``
    is the one this host most needs to see: a recursive-descent decoder that lets
    the interpreter's stack limit answer for it has no depth guard, whatever its
    §21 constant says.
    """
    started = time.perf_counter()
    try:
        result = subject.run(text)
    except Exception as exc:  # noqa: BLE001 — catching broadly IS the invariant
        elapsed = (time.perf_counter() - started) * 1000.0
        return Measured(Verdict("escaped-" + type(exc).__name__, str(exc)[:400]), elapsed, 0)
    elapsed = (time.perf_counter() - started) * 1000.0

    # Order matters: an input that both ran long AND over-amplified is reported as
    # the time breach, because that is the one an operator has to act on first.
    if elapsed > budgets.soft_time_ms:
        return Measured(Verdict("timed-out", f"decode returned only after {elapsed:.0f} ms"), elapsed, 0)

    if result.refused_code is not None:
        return Measured(Verdict("rejected", result.refused_code), elapsed, 0)

    canonical = result.canonical or ""
    budget = max(budgets.amplification_floor_chars, budgets.max_amplification * len(text))
    if len(canonical) > budget:
        return Measured(
            Verdict("over-amplified", f"canonical form is {len(canonical)} chars, budget {budget}"),
            elapsed,
            len(canonical),
        )
    if result.re_decoded is None:
        kind = KNOWN_NONFINITE_HOLE if is_known_nonfinite_hole(canonical) else "canonical-refused"
        return Measured(
            Verdict(kind, f"the decoder's own output re-decodes as {result.re_decoded_code}"),
            elapsed,
            len(canonical),
        )
    if canonical != result.re_decoded:
        return Measured(
            Verdict(
                "fixed-point-broken", f"first canonical form {len(canonical)} chars, second {len(result.re_decoded)}"
            ),
            elapsed,
            len(canonical),
        )
    return Measured(Verdict("clean"), elapsed, len(canonical))


# ─── Minimisation ───────────────────────────────────────────────────────────


def minimise(classify: Callable[[str], str], target: str, text: str, time_budget_s: float = 25.0) -> str:
    """Delta-debugging by span deletion.

    Repeatedly cut a chunk and keep the cut if the input still fails the same WAY.
    Bounded by a candidate count AND a wall clock, because the class most worth
    minimising (a time breach) is exactly the one where each probe is expensive.
    """
    started = time.perf_counter()
    best = text
    granularity = 2
    budget = 400
    while budget > 0 and time.perf_counter() - started < time_budget_s:
        chunk = max(1, len(best) // granularity)
        reduced = False
        i = 0
        while i < len(best) and budget > 0 and time.perf_counter() - started < time_budget_s:
            take = min(chunk, len(best) - i)
            candidate = best[:i] + best[i + take :]
            budget -= 1
            if candidate and classify(candidate) == target:
                best = candidate
                reduced = True
            else:
                i += take
        if reduced:
            granularity = max(2, granularity // 2)
        elif chunk > 1:
            granularity *= 2
        else:
            break
    return best


# ─── The run ────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Counterexample:
    subject: str
    iteration: int
    seed: int
    config_name: str
    origin: str
    verdict: Verdict
    original: str
    minimised: str

    def describe(self) -> str:
        preview = self.minimised if len(self.minimised) <= 300 else self.minimised[:300] + " ...(truncated)"
        return "\n".join(
            [
                f"subject: {self.subject}",
                f"seed: {self.seed}, iteration: {self.iteration}, config: {self.config_name}",
                f"origin: {self.origin}",
                f"verdict: {self.verdict.kind} — {self.verdict.detail}",
                f"length: {len(self.original)} chars original, {len(self.minimised)} minimised",
                f"minimised input: {preview!r}",
                "",
                "Counterexample policy: fix the decoder, then land the minimised input as a",
                "permanent reject fixture in the shared corpus, so every conformant host",
                "inherits the case rather than only this one.",
            ]
        )


@dataclass(slots=True)
class RunStats:
    iterations: int = 0
    inputs: int = 0
    seed_count: int = 0
    reject_codes: dict[str, int] = field(default_factory=dict)
    accepted: int = 0
    #: The one EXCLUDED defect class, counted and published rather than dropped:
    #: an exclusion nobody can see reads as "found nothing".
    known_nonfinite_holes: int = 0
    max_decode_ms: float = 0.0
    max_amplification: float = 0.0
    elapsed_seconds: float = 0.0
    seed: int = 0
    counterexamples: list[Counterexample] = field(default_factory=list)


def run(
    subjects: Sequence[Subject],
    budgets: Budgets,
    cfg: Config,
    seed: int,
    iterations: int,
    seeds: Sequence[str],
    vocab: Sequence[str],
    minimise_finds: bool,
) -> RunStats:
    """Run ``iterations`` generated inputs through every subject.

    ``subjects`` is a parameter precisely so the go-red self-test drives the
    IDENTICAL machinery with a broken stand-in. A fuzz harness nobody has ever
    seen fail is decoration.
    """
    rng = Rng(seed)
    started = time.perf_counter()
    stats = RunStats(seed_count=len(seeds), seed=seed)

    for i in range(1, iterations + 1):
        g = generate(rng, seeds, vocab, cfg, i)
        for subject in subjects:
            measured = check(subject, budgets, g.payload)
            stats.inputs += 1
            stats.max_decode_ms = max(stats.max_decode_ms, measured.elapsed_ms)
            if measured.canonical_chars > 0 and g.payload:
                stats.max_amplification = max(stats.max_amplification, measured.canonical_chars / len(g.payload))

            verdict = measured.verdict
            if verdict.kind == "rejected":
                stats.reject_codes[verdict.detail] = stats.reject_codes.get(verdict.detail, 0) + 1
            elif verdict.kind == "clean":
                stats.accepted += 1
            elif verdict.kind == KNOWN_NONFINITE_HOLE:
                stats.known_nonfinite_holes += 1
            else:
                target = verdict.verdict_class

                # A named function rather than a lambda: `minimise` is called
                # inside this loop iteration, so capturing `subject` by closure is
                # correct here — but a reader has to check that, and the check is
                # cheaper against a definition with a signature on it.
                def classify(candidate: str, subject: Subject = subject) -> str:
                    return check(subject, budgets, candidate).verdict.verdict_class

                minimised = minimise(classify, target, g.payload) if minimise_finds else g.payload
                stats.counterexamples.append(
                    Counterexample(
                        subject=subject.name,
                        iteration=i,
                        seed=seed,
                        config_name=cfg.name,
                        origin=g.origin,
                        verdict=verdict,
                        original=g.payload,
                        minimised=minimised,
                    )
                )
        stats.iterations = i

    stats.elapsed_seconds = time.perf_counter() - started
    return stats


def summarise(stats: RunStats) -> str:
    """A one-line human summary, shared by the gate test and the long-run CLI."""
    codes = " ".join(f"{c}={n}" for c, n in sorted(stats.reject_codes.items(), key=lambda kv: -kv[1]))
    per_iteration = 0 if stats.iterations == 0 else stats.inputs // stats.iterations
    return (
        f"{stats.inputs} inputs ({stats.iterations} iterations x {per_iteration} entry points) "
        f"in {stats.elapsed_seconds:.1f} s — accepted {stats.accepted}, refused [{codes}], "
        f"{len(stats.counterexamples)} counterexamples, {stats.known_nonfinite_holes} known "
        f"non-finite round-trip holes (§7, EXCLUDED); max decode {stats.max_decode_ms:.0f} ms; "
        f"max canonical amplification {stats.max_amplification:.1f} x"
    )


def evidence_record(stats: RunStats, cfg: Config, corpus_root: Path | None) -> dict[str, Any]:
    """The machine-readable result a scheduled job collects.

    Regenerated BY the run, so the figures cannot drift from the methodology
    beside them.
    """
    return {
        "host": "fuaran-py",
        "entryPoints": [s.name for s in REAL_SUBJECTS],
        "config": cfg.name,
        "seed": str(stats.seed),
        "iterations": stats.iterations,
        "inputs": stats.inputs,
        "corpusSeeds": stats.seed_count,
        "corpusPresent": corpus_root is not None and (corpus_root / "manifest.json").is_file(),
        "accepted": stats.accepted,
        "rejectCodes": dict(sorted(stats.reject_codes.items())),
        "counterexamples": len(stats.counterexamples),
        "knownNonFiniteRoundTripHoles": stats.known_nonfinite_holes,
        "maxDecodeMs": round(stats.max_decode_ms, 3),
        "maxCanonicalAmplification": round(stats.max_amplification, 3),
        "elapsedSeconds": round(stats.elapsed_seconds, 3),
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _default_corpus_root() -> Path | None:
    # parents: [0] conformance, [1] fuaran_py, [2] src, [3] fuaran-py, [4] the
    # workspace root the corpus sits beside. Counted rather than eyeballed: an
    # off-by-one here does not fail, it silently narrows the seed pool to the
    # nine built-ins and every figure the run publishes is then about a corpus it
    # never read.
    root = Path(__file__).resolve().parents[4] / "wire-format-fixtures"
    return root if (root / "manifest.json").is_file() else None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decoder robustness fuzz — this host's leg.")
    parser.add_argument("--iterations", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=1023)
    parser.add_argument("--long", action="store_true", help="raise the payload cap past the §21 string bound")
    parser.add_argument("--evidence", type=Path, default=None, help="write the machine-readable result here")
    parser.add_argument(
        "--corpus", type=Path, default=None, help="the shared corpus root (default: the sibling checkout)"
    )
    args = parser.parse_args(argv)

    if args.iterations <= 0:
        parser.error(f"--iterations: {args.iterations} is not a positive iteration count")

    corpus_root = args.corpus if args.corpus is not None else _default_corpus_root()
    cfg = LONG_CONFIG if args.long else BOUNDED_CONFIG
    seeds = load_seeds(corpus_root)
    vocab = load_vocabulary(corpus_root)

    stats = run(REAL_SUBJECTS, DEFAULT_BUDGETS, cfg, args.seed, args.iterations, seeds, vocab, minimise_finds=True)
    print(summarise(stats))

    if args.evidence is not None:
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(
            json.dumps(evidence_record(stats, cfg, corpus_root), indent=2) + "\n", encoding="utf-8"
        )

    if stats.counterexamples:
        print(f"\n{len(stats.counterexamples)} counterexample(s):\n", file=sys.stderr)
        for c in stats.counterexamples[:5]:
            print(c.describe() + "\n", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
