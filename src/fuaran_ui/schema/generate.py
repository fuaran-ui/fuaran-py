"""Generate :mod:`fuaran_ui.schema.generated` from the published ``idl.json`` (Phase 2296).

**The ruling (operator, 2026-10-16) — host-owned generator over the published IDL.** The
vocabulary is declared once, in the specification corpus's ``idl.json``; everything STRUCTURAL in
this host is a projection of it. The generator is this host's own, a small standard-library script,
and it reads the IDL as DATA: it is not a Core build target, not shared with any other host, and not
a transpile of another host's code. Hosts are siblings — each carries generated structure plus hand
behaviour (decode policy, leniency, refusal messages, rendering).

**Placement.** This script lives beside its one output, ``generated.py``, and reads the BUNDLED
snapshot's ``idl.json`` (``conformance/corpus/idl.json``), so an offline build regenerates exactly
the module that is committed. It runs from:

* the corpus sync step — ``python conformance/sync_corpus.py`` regenerates after copying the
  snapshot, so a moved ``idl.json`` and its regeneration land in one change;
* ``python -m fuaran_ui.schema.generate`` (or this file by path) — regenerate in place;
* ``--check`` — exit 1 when the committed module differs from a regeneration (the gate's form).

The test suite carries the same two drift checks (``tests/test_generated_structural.py``): the
committed module equals a regeneration, and its recorded ``IDL_SHA256`` equals the bundled
``idl.json``'s digest.

**What is generated:** the enum vocabularies (as ``Literal`` aliases and case tables), the kind set
with its categories, every kind's / record's / union case's / op's field table (optionality, type,
declared default), the node envelope's fields and the hardening table — and, derived from those, the
required-field, omit-at-default, child-slot and binding-slot tables. **What is not:** anything the
IDL marks ``hostSurface`` (a host-language declaration, nothing observable on the wire) and every
behaviour of the decoder.

The output is laid out so ``ruff format`` leaves it byte-identical: one row per line, magic
trailing commas on every exploded collection, double-quoted strings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
#: ``src/fuaran_ui/schema/generate.py`` -> the repository root.
_REPO = _HERE.parents[3]
DEFAULT_IDL = _REPO / "conformance" / "corpus" / "idl.json"
DEFAULT_OUT = _HERE.parent / "generated.py"

_LINE_LIMIT = 120


def _q(text: str) -> str:
    """A double-quoted Python string literal (``json.dumps`` is a valid Python literal for ASCII)."""
    return json.dumps(text, ensure_ascii=False)


class _Types:
    """Interns every distinct IDL type as one named ``T(...)`` row, nested types by reference.

    Interning keeps each emitted line short (a nested type is a NAME, never an inline call), so the
    module is stable under ``ruff format`` however deep the IDL nests; a row's name spells the type.
    """

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}  # name -> constructor call
        self._by_key: dict[str, str] = {}  # canonical JSON of the type -> name

    @staticmethod
    def _spell(t: dict) -> str:
        tag = t["$type"]
        name = t.get("name")
        if tag in ("union", "record", "enum", "var"):
            head = f"{tag}_{name}"
        else:
            head = tag
        inner = t.get("of") or t.get("values")
        if inner is not None:
            head += "_" + _Types._spell(inner)
        for a in t.get("args") or []:
            head += "_" + _Types._spell(a)
        wire = t.get("wire")
        if isinstance(wire, dict):
            head += "_as_" + _Types._spell(wire)
        elif isinstance(wire, str) and tag == "hosted":
            head += "_" + ("json" if wire == "json" else "x")
        return head

    def ref(self, t: dict) -> str:
        key = json.dumps({k: v for k, v in t.items() if k != "hostSurface"}, sort_keys=True)
        if key in self._by_key:
            return self._by_key[key]
        parts = [_q(t["$type"])]
        if "name" in t:
            parts.append(_q(t["name"]))
        inner = t.get("of") or t.get("values")
        if inner is not None:
            parts.append(f"of={self.ref(inner)}")
        args = t.get("args") or []
        if args:
            parts.append("args=(" + ", ".join(self.ref(a) for a in args) + ",)")
        wire = t.get("wire")
        if isinstance(wire, str):
            parts.append(f"wire={_q(wire)}")
        elif isinstance(wire, dict):
            parts.append(f"wire_type={self.ref(wire)}")
        base = "T_" + "".join(c if c.isalnum() else "_" for c in self._spell(t))
        name = base
        n = 2
        while name in self.rows:
            name = f"{base}_{n}"
            n += 1
        self.rows[name] = "T(" + ", ".join(parts) + ")"
        self._by_key[key] = name
        return name


def _default_value(v: dict) -> object:
    """Lower an IDL default-value node to the wire JSON value it denotes."""
    tag = v["$type"]
    if tag in ("int", "float", "bool", "str"):
        return v["value"]
    if tag == "enum":
        return v["case"]
    if tag == "list":
        return [_default_value(i) for i in v["items"]]
    if tag == "record":
        return {f["name"]: _default_value(f["value"]) for f in v["fields"]}
    if tag == "union":
        out: dict[str, object] = {"$type": v["tag"]}
        out.update({f["name"]: _default_value(f["value"]) for f in v["fields"]})
        return out
    raise ValueError(f"idl.json: unsupported default value kind {tag!r}: {v!r}")


def _py_literal(value: object) -> str:
    """A Python literal for a JSON value (``True``/``False``/``None``, double-quoted strings)."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _q(value)
    if isinstance(value, list):
        return "[" + ", ".join(_py_literal(i) for i in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_q(k)}: {_py_literal(i)}" for k, i in value.items()) + "}"
    raise ValueError(f"cannot render {value!r}")


def _field(types: _Types, f: dict) -> str:
    opt = f["optionality"]
    parts = [_q(f["name"]), _q(opt["$type"]), types.ref(f["type"])]
    if opt["$type"] == "omitDefault":
        parts.append(f"default={_py_literal(_default_value(opt['default']))}")
    return "F(" + ", ".join(parts) + ")"


class _Out:
    """Line accumulator that refuses a line ``ruff format`` would re-wrap."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, line: str = "") -> None:
        if len(line) > _LINE_LIMIT:
            raise ValueError(f"generated line exceeds {_LINE_LIMIT} columns; split it in the emitter: {line}")
        self.lines.append(line)

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _str_tuple(out: _Out, head: str, items: list[str], indent: str = "") -> None:
    """``head(items)`` on one line when it fits, else one item per line with a trailing comma."""
    inline = head + "(" + ", ".join(_q(i) for i in items) + ("," if len(items) == 1 else "") + ")"
    if len(indent + inline) + 1 <= _LINE_LIMIT:
        out(indent + inline)
        return
    pad = indent + " " * (len(head) - len(head.lstrip(" ")))
    out(indent + head + "(")
    for i in items:
        out(f"{pad}    {_q(i)},")
    out(pad + ")")


def _field_tuple(out: _Out, types: _Types, indent: str, key: str | None, fields: list[dict]) -> None:
    """Emit one ``(F(...), ...)`` row tuple (keyed ``key: (...)``, or bare), ruff-stable in every arity.

    An empty tuple and a one-row tuple that fits stay inline: a one-element tuple's comma is syntax,
    not a magic trailing comma, so ``ruff format`` would collapse an exploded one.
    """
    head = f"{indent}{key}: " if key is not None else indent
    tail = "," if key is not None else ""
    if not fields:
        out(f"{head}(){tail}")
        return
    if len(fields) == 1:
        inline = f"{head}({_field(types, fields[0])},){tail}"
        if len(inline) <= _LINE_LIMIT:
            out(inline)
            return
    out(f"{head}(")
    for f in fields:
        row = f"{indent}    {_field(types, f)},"
        if len(row) <= _LINE_LIMIT:
            out(row)
        else:
            _explode_field(out, types, indent + "    ", f)
    out(f"{indent}){tail}")


def render(idl_bytes: bytes) -> str:
    """Render the generated module's full text from the raw bytes of ``idl.json``."""
    idl = json.loads(idl_bytes.decode("utf-8"))
    digest = hashlib.sha256(idl_bytes).hexdigest()
    out = _Out()
    types = _Types()
    out('"""GENERATED from ``idl.json`` by ``fuaran_ui/schema/generate.py`` — DO NOT EDIT BY HAND.')
    out("")
    out("Regenerate with ``python -m fuaran_ui.schema.generate`` (or ``python conformance/sync_corpus.py``,")
    out("which regenerates after re-syncing the bundled snapshot). The structural vocabulary of the wire")
    out("format, as data: what each kind, record, union case and op carries, how optional each member is,")
    out("and the default an omitted member means. Behaviour lives in the hand-written modules that read it.")
    out('"""')
    out("")
    out("from __future__ import annotations")
    out("")
    out("from dataclasses import dataclass")
    out("from typing import Literal")
    out("")
    out("#: SHA-256 of the exact ``idl.json`` bytes this module was generated from.")
    out(f"IDL_SHA256 = {_q(digest)}")
    out(f"IDL_VERSION = {int(idl['version'])}")
    out("")
    out("")
    out("@dataclass(frozen=True, slots=True)")
    out("class T:")
    out('    """One IDL type: ``kind`` is its ``$type``; ``of`` the list element / map value type.')
    out("")
    out("    ``wire`` names a sentinel wire class (``<closure>`` / ``json``); ``wire_type`` the wire type a")
    out("    host-surface member carries instead.")
    out('    """')
    out("")
    out("    kind: str")
    out("    name: str | None = None")
    out("    of: T | None = None")
    out("    args: tuple[T, ...] = ()")
    out("    wire: str | None = None")
    out("    wire_type: T | None = None")
    out("")
    out("")
    out("@dataclass(frozen=True, slots=True)")
    out("class F:")
    out('    """One member: ``optionality`` is ``required`` | ``optional`` | ``omitDefault`` | ``hostOnly``."""')
    out("")
    out("    name: str")
    out("    optionality: str")
    out("    type: T")
    out("    default: object = None")
    out("")
    out("    @property")
    out("    def required(self) -> bool:")
    out('        return self.optionality == "required"')
    out("")
    out("    @property")
    out("    def on_wire(self) -> bool:")
    out('        """False for a ``hostOnly`` member, which carries nothing observable on the wire."""')
    out('        return self.optionality != "hostOnly"')
    out("")
    out("")
    types_at = len(out.lines)

    # ── Enums ────────────────────────────────────────────────────────────
    out("# ── Enum vocabularies ─────────────────────────────────────────────────────")
    out("")
    enums = sorted(idl["enums"], key=lambda e: e["name"])
    for e in enums:
        inline = f"{e['name']} = Literal[" + ", ".join(_q(c) for c in e["cases"]) + "]"
        if len(inline) <= _LINE_LIMIT:
            out(inline)
        else:
            out(f"{e['name']} = Literal[")
            for c in e["cases"]:
                out(f"    {_q(c)},")
            out("]")
    out("")
    out("#: Every enum's wire cases, in declaration order.")
    out("ENUM_CASES: dict[str, tuple[str, ...]] = {")
    for e in enums:
        _str_tuple(out, f"    {_q(e['name'])}: ", e["cases"])
        out.lines[-1] += ","
    out("}")
    out("")
    out("#: The host-language spellings of the enums whose wire tokens are not PascalCase.")
    out("ENUM_HOST_CASES: dict[str, tuple[str, ...]] = {")
    for e in enums:
        if "hostCases" in e:
            _str_tuple(out, f"    {_q(e['name'])}: ", e["hostCases"])
            out.lines[-1] += ","
    out("}")
    out("")
    out("ENUM_SETS: dict[str, frozenset[str]] = {name: frozenset(cases) for name, cases in ENUM_CASES.items()}")
    out("")
    out("")

    # ── Kinds ────────────────────────────────────────────────────────────
    kinds = sorted(idl["kinds"], key=lambda k: k["tag"])
    out("# ── Node kinds ────────────────────────────────────────────────────────────")
    out("")
    out("NodeKindTag = Literal[")
    for k in kinds:
        out(f"    {_q(k['tag'])},")
    out("]")
    out("")
    out("#: Every node-kind discriminator, sorted.")
    out("KINDS: tuple[str, ...] = (")
    for k in kinds:
        out(f"    {_q(k['tag'])},")
    out(")")
    out("")
    out("KIND_CATEGORY: dict[str, str] = {")
    for k in kinds:
        out(f"    {_q(k['tag'])}: {_q(k['category'])},")
    out("}")
    out("")
    out("#: Each kind's members in declaration order.")
    out("KIND_FIELDS: dict[str, tuple[F, ...]] = {")
    for k in kinds:
        _field_tuple(out, types, "    ", _q(k["tag"]), k["fields"])
    out("}")
    out("")
    out("")

    # ── Node envelope ────────────────────────────────────────────────────
    out("# ── The node envelope ─────────────────────────────────────────────────────")
    out("")
    out("NODE_FIELDS: tuple[F, ...] = (")
    for f in idl["nodeFields"]:
        out(f"    {_field(types, f)},")
    out(")")
    out("")
    out("")

    # ── Records ──────────────────────────────────────────────────────────
    out("# ── Records ───────────────────────────────────────────────────────────────")
    out("")
    out("RECORD_FIELDS: dict[str, tuple[F, ...]] = {")
    for r in sorted(idl["records"], key=lambda r: r["name"]):
        _field_tuple(out, types, "    ", _q(r["name"]), r["fields"])
    out("}")
    out("")
    out("")

    # ── Unions ───────────────────────────────────────────────────────────
    unions = sorted(idl["unions"], key=lambda u: u["name"])
    out("# ── Unions ────────────────────────────────────────────────────────────────")
    out("")
    out("#: Every union's case discriminators, in declaration order.")
    out("UNION_CASES: dict[str, tuple[str, ...]] = {")
    for u in unions:
        _str_tuple(out, f"    {_q(u['name'])}: ", [c["tag"] for c in u["cases"]])
        out.lines[-1] += ","
    out("}")
    out("")
    out("#: The case whose bare-value shorthand is the canonical form (WIRE_FORMAT §16).")
    out("UNION_TRANSPARENT_CASE: dict[str, str] = {")
    for u in unions:
        if "transparentCase" in u:
            out(f"    {_q(u['name'])}: {_q(u['transparentCase'])},")
    out("}")
    out("")
    out("#: Each union case's members, in declaration order.")
    out("UNION_CASE_FIELDS: dict[str, dict[str, tuple[F, ...]]] = {")
    for u in unions:
        out(f"    {_q(u['name'])}: {{")
        for c in u["cases"]:
            _field_tuple(out, types, "        ", _q(c["tag"]), c["fields"])
        out("    },")
    out("}")
    out("")
    out("")

    # ── Ops ──────────────────────────────────────────────────────────────
    ops = sorted(idl["ops"], key=lambda o: o["tag"])
    out("# ── Tree ops ──────────────────────────────────────────────────────────────")
    out("")
    out("OP_FIELDS: dict[str, tuple[F, ...]] = {")
    for o in ops:
        _field_tuple(out, types, "    ", _q(o["tag"]), o["fields"])
    out("}")
    out("")
    out("#: The §21 hardening vocabulary: the gated kind, the placeholder and the literal-case names.")
    out("HARDEN: dict[str, object] = {")
    for key in sorted(idl["harden"]):
        out(f"    {_q(key)}: {_py_literal(idl['harden'][key])},")
    out("}")
    out("")
    out("")

    # ── Derived projections ──────────────────────────────────────────────
    out("# ── Derived tables (projections of the rows above) ────────────────────────")
    out("")
    out("KINDS_BY_CATEGORY: dict[str, frozenset[str]] = {")
    for cat in sorted({k["category"] for k in kinds}):
        out(f"    {_q(cat)}: frozenset(k for k, c in KIND_CATEGORY.items() if c == {_q(cat)}),")
    out("}")
    out("")
    out("#: Each kind's REQUIRED wire members.")
    out("REQUIRED_FIELDS: dict[str, frozenset[str]] = {")
    out("    kind: frozenset(f.name for f in fields if f.required) for kind, fields in KIND_FIELDS.items()")
    out("}")
    out("")
    out("#: Each kind's omit-at-default members and the wire value an omission means.")
    out("OMIT_DEFAULTS: dict[str, dict[str, object]] = {")
    out(
        '    kind: {f.name: f.default for f in fields if f.optionality == "omitDefault"} for kind, fields in '
        "KIND_FIELDS.items()"
    )
    out("}")
    out("")
    out("")
    out("def _is_child(t: T) -> bool:")
    out('    return t.kind == "node" or (t.kind == "list" and t.of is not None and t.of.kind == "node")')
    out("")
    out("")
    out("#: Each kind's child slots: the members that carry a node or a list of nodes.")
    out("CHILD_SLOTS: dict[str, tuple[str, ...]] = {")
    out("    kind: tuple(f.name for f in fields if _is_child(f.type)) for kind, fields in KIND_FIELDS.items()")
    out("}")
    out("")
    out("#: Each kind's binding slots: the members typed ``Binding<_>``.")
    out("BINDING_SLOTS: dict[str, tuple[str, ...]] = {")
    out('    kind: tuple(f.name for f in fields if f.type.kind == "union" and f.type.name == "Binding")')
    out("    for kind, fields in KIND_FIELDS.items()")
    out("}")
    # The interned type table goes before the first row that names one of its entries.
    table = ["# ── Types (each distinct IDL type once; nested types by name) ──────────────", ""]
    table += [f"{name} = {call}" for name, call in types.rows.items()]
    for line in table:
        if len(line) > _LINE_LIMIT:
            raise ValueError(f"generated type row exceeds {_LINE_LIMIT} columns: {line}")
    out.lines[types_at:types_at] = [*table, "", ""]
    return out.text()


def _explode_field(out: _Out, types: _Types, indent: str, f: dict) -> None:
    """One ``F(...)`` call, one argument per line — the shape ruff's formatter gives a long call."""
    opt = f["optionality"]
    out(f"{indent}F(")
    out(f"{indent}    {_q(f['name'])},")
    out(f"{indent}    {_q(opt['$type'])},")
    out(f"{indent}    {types.ref(f['type'])},")
    if opt["$type"] == "omitDefault":
        out(f"{indent}    default={_py_literal(_default_value(opt['default']))},")
    out(f"{indent}),")


def generate(idl: Path = DEFAULT_IDL, out: Path = DEFAULT_OUT) -> Path:
    """Regenerate ``out`` from ``idl``; returns the path written."""
    out.write_text(render(idl.read_bytes()), encoding="utf-8", newline="\n")
    return out


def check(idl: Path = DEFAULT_IDL, out: Path = DEFAULT_OUT) -> bool:
    """True when ``out`` is byte-identical to a regeneration from ``idl``."""
    return out.is_file() and out.read_bytes() == render(idl.read_bytes()).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--idl", type=Path, default=DEFAULT_IDL, help="the idl.json to read (default: bundled)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="the module to write (default: in place)")
    parser.add_argument("--check", action="store_true", help="exit 1 when the module differs from a regeneration")
    args = parser.parse_args(argv)
    if not args.idl.is_file():
        print(f"generate: no idl.json at {args.idl}", file=sys.stderr)
        return 2
    if args.check:
        if check(args.idl, args.out):
            print(f"generate: {args.out.name} is current with {args.idl}")
            return 0
        print(
            f"generate: {args.out} is STALE against {args.idl} — regenerate with `python -m fuaran_ui.schema.generate`",
            file=sys.stderr,
        )
        return 1
    print(f"generate: wrote {generate(args.idl, args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
