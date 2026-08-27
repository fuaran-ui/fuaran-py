"""``FormField.rule`` — the declared field constraint (fuaran#864).

Three legs, and the third is where this host's adoption is a decision rather than
a port.

The CODEC leg is certified by the corpus itself (``nodes/form-field-rules.json``
plus the three reject vectors), which the round-trip and reject suites already
run over the whole manifest; what is pinned here is the AUTHORING leg — a
co-equal authoring tier that can decode a rule and not write one is a codec with
extra steps — and the VALIDATOR leg.

On the validator leg this host implements **FUARAN100 and FUARAN101 and not
FUARAN099**, which is a declared subset and not an oversight: the first two are
decidable from the field alone, and the third asks whether anything IN THE TREE
writes a state key. Reasoning from that absence needs a tree-wide write-key
projection and an opaque-writer stand-down this host does not have, and a partial
port would fire on every form whose compare reads a key a sibling handler writes
— a false accusation at Error severity, on an ordinary shape. The declaration
lives in ``validator-coverage.json``; this module is the half that keeps it
honest by exercising what it claims.
"""

from __future__ import annotations

import json
import re

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py import decode_node, encode_node, validate_node
from fuaran_py.renderer import render_html
from fuaran_py.schema import types as t
from fuaran_py.ui import binding, encode, fuaran, rule


def _form(field: str) -> str:
    return (
        f'{{"id":"f1","kind":{{"$type":"Form","fields":[{field}],'
        '"onSubmit":{"$type":"Dispatch"},"submitLabel":"Save"}}'
    )


def _findings(wire: str, code: str) -> list:
    result = decode_node(wire)
    assert result.ok, getattr(result, "error", result)
    return [f for f in validate_node(result.value) if f.code == code]


# ── The codec, against the corpus ────────────────────────────────────────────


@corpus_required
def test_the_corpus_rule_fixture_round_trips_byte_identically() -> None:
    src = (CORPUS_ROOT / "nodes/form-field-rules.json").read_text(encoding="utf-8")
    result = decode_node(src)
    assert result.ok, getattr(result, "error", result)
    assert encode_node(result.value) == src


@corpus_required
@pytest.mark.parametrize(
    "fixture",
    ["reject-fieldrule-empty", "reject-fieldrule-length-unordered", "reject-formfield-near-miss-validation"],
)
def test_the_corpus_rule_rejects_are_refused(fixture: str) -> None:
    """Named individually rather than left to the whole-corpus reject sweep, so a
    fixture silently dropped from the manifest fails here by name."""
    src = (CORPUS_ROOT / "reject" / f"{fixture}.json").read_text(encoding="utf-8")
    assert not decode_node(src).ok


# ── The authoring surface ────────────────────────────────────────────────────


def test_the_authoring_surface_can_express_every_rule_slot() -> None:
    tree = fuaran.form(
        "f1",
        fields=[
            t.FormField(
                "email", t.LiteralText("Work email"), t.TextField(t.Static("")), True, rule=rule.format("email")
            ),
            t.FormField(
                "postcode",
                t.LiteralText("Postcode"),
                t.TextField(t.Static("")),
                True,
                rule=rule.pattern("[A-Z]{1,2}[0-9][A-Z0-9]?", "Enter a UK postcode"),
            ),
            t.FormField(
                "username", t.LiteralText("Username"), t.TextField(t.Static("")), True, rule=rule.length(3, 24)
            ),
            t.FormField(
                "end",
                t.LiteralText("End date"),
                t.DateField(t.Static(""), "Date"),
                True,
                rule=rule.compare(binding.state("start", ""), "gte", "End must not precede start"),
            ),
        ],
        on_submit=None,
    )
    wire = encode(tree)
    assert '"rule":{"format":"email"}' in wire
    assert '"pattern":"[A-Z]{1,2}[0-9][A-Z0-9]?"' in wire
    assert '"maxLength":24,"message"' not in wire  # message omitted where not given
    assert '"minLength":3' in wire
    assert '"compare":{"against":{"$type":"State"' in wire
    # And what it writes, it can read back.
    assert decode_node(wire).ok


def test_a_field_with_no_rule_is_byte_unchanged() -> None:
    """The slot is an optional record field precisely so that every form authored
    before it existed encodes identically — an omitted-vs-null distinction the
    ``_obj`` builder makes structurally, not by remembering to."""
    field = t.FormField("email", t.LiteralText("Work email"), t.TextField(t.Static("")), True)
    assert '"rule"' not in encode(fuaran.form("f1", fields=[field], on_submit=None))


# ── FUARAN100 — a slot the control cannot honour ─────────────────────────────


@pytest.mark.parametrize(
    ("control", "slot_json", "expect"),
    [
        ('{"$type":"Text","onChange":"<closure>"}', '"format":"email"', False),
        ('{"$type":"Text","onChange":"<closure>"}', '"minLength":3', False),
        # TextArea carries a string, so the text bounds apply; the format
        # shorthands do not — a multi-line control is not where an email lives.
        ('{"$type":"TextArea","onChange":"<closure>","rows":4}', '"minLength":3', False),
        ('{"$type":"TextArea","onChange":"<closure>","rows":4}', '"format":"email"', True),
        ('{"$type":"Checkbox","onToggle":"<closure>"}', '"format":"email"', True),
        ('{"$type":"Checkbox","onToggle":"<closure>"}', '"pattern":"a+"', True),
        ('{"$type":"Number","onChange":"<closure>"}', '"maxLength":8', True),
        ('{"$type":"Date","onChange":"<closure>","variant":"Date"}', '"pattern":"a+"', True),
    ],
)
def test_a_rule_slot_the_control_cannot_honour_is_flagged(control: str, slot_json: str, expect: bool) -> None:
    wire = _form(f'{{"id":"x","kind":{control},"label":"L","required":true,"rule":{{{slot_json}}}}}')
    found = _findings(wire, "FUARAN100")
    assert bool(found) is expect
    if expect:
        assert found[0].path.startswith("$.kind.fields.0.rule.")
        assert "cannot honour it" in found[0].message


def test_compare_is_never_unhonourable() -> None:
    """``compare`` is deliberately absent from the honourable-slot table: it
    compares the field's VALUE, which every control has. A rule that fired on it
    would refuse the ordered-pair shape the slot exists for."""
    wire = _form(
        '{"id":"x","kind":{"$type":"Checkbox","onToggle":"<closure>"},"label":"L","required":true,'
        '"rule":{"compare":{"against":{"$type":"State","key":"other"},"op":"eq"}}}'
    )
    assert _findings(wire, "FUARAN100") == []


# ── FUARAN101 — a literal operand duplicating a declared bound ───────────────


@pytest.mark.parametrize(
    ("op", "bounds", "expect"),
    [
        ("gte", '"min":1979,"max":2028,', True),
        ("gt", '"min":1979,"max":2028,', True),
        ("lte", '"min":1979,"max":2028,', True),
        ("lt", '"min":1979,"max":2028,', True),
        # eq / neq duplicate neither bound and are silent.
        ("eq", '"min":1979,"max":2028,', False),
        ("neq", '"min":1979,"max":2028,', False),
        # A lower-bound comparison against a control declaring only an upper one
        # duplicates nothing.
        ("gte", '"max":2028,', False),
        ("lte", '"min":1979,', False),
        ("gte", "", False),
    ],
)
def test_a_literal_compare_duplicating_a_declared_bound_is_flagged(op: str, bounds: str, expect: bool) -> None:
    wire = _form(
        f'{{"id":"year","kind":{{"$type":"RangedNumber",{bounds}"onChange":"<closure>","step":1}},'
        f'"label":"Year","required":true,"rule":{{"compare":{{"against":{{"$type":"Static","value":1990}},"op":"{op}"}}}}}}'
    )
    found = _findings(wire, "FUARAN101")
    assert bool(found) is expect
    if expect:
        assert found[0].path == "$.kind.fields.0.rule.compare"
        assert "RangedNumber." in found[0].message


def test_a_state_operand_is_never_a_duplicated_bound() -> None:
    """The go-red partner for the rule above, and the more important direction: a
    ``State`` operand reads something that CHANGES, which is exactly what the
    slot is for, so flagging it would name the correct shape as the defect."""
    wire = _form(
        '{"id":"year","kind":{"$type":"RangedNumber","min":1979,"max":2028,"onChange":"<closure>","step":1},'
        '"label":"Year","required":true,'
        '"rule":{"compare":{"against":{"$type":"State","key":"floor"},"op":"gte"}}}'
    )
    assert _findings(wire, "FUARAN101") == []


def test_an_unbounded_control_carries_no_duplicated_bound() -> None:
    wire = _form(
        '{"id":"n","kind":{"$type":"Number","onChange":"<closure>"},"label":"N","required":true,'
        '"rule":{"compare":{"against":{"$type":"Static","value":10},"op":"gte"}}}'
    )
    assert _findings(wire, "FUARAN101") == []


# ── The render leg — the platform's own constraint attributes ────────────────


@corpus_required
def test_the_corpus_fixture_projects_the_reference_constraint_attributes() -> None:
    """The Phase 801 discipline: the declared behaviour reaches the platform's OWN
    vocabulary, in the same order the reference server renderer emits it, so an
    enhancement script reading one host's markup reads the other's unchanged."""
    src = (CORPUS_ROOT / "nodes/form-field-rules.json").read_text(encoding="utf-8")
    result = decode_node(src)
    assert result.ok
    html = render_html(result.value)
    assert '<input class="fuaran-form-field-control" data-fuaran-field="work-email" type="email" />' in html
    assert 'data-fuaran-field="postcode" pattern="[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}" />' in html
    assert 'data-fuaran-field="username" minlength="3" maxlength="24" />' in html


@corpus_required
def test_compare_is_DECLARED_rather_than_claimed() -> None:
    """`compare` has no HTML equivalent, so the honest projection is a declaration
    a reader can see — never a silent drop, and never a claim of coverage. Nothing
    in the platform reads this attribute; the constraint is enforced by a
    rendering host's submit gate and by a server-side re-check."""
    src = (CORPUS_ROOT / "nodes/form-field-rules.json").read_text(encoding="utf-8")
    result = decode_node(src)
    assert result.ok
    assert 'data-fuaran-field-compare="gte:hire-start-date"' in render_html(result.value)


def test_a_ruleless_field_renders_byte_unchanged() -> None:
    """The whole point of the slot being optional: a form authored before it
    existed renders exactly the bytes it rendered before. That includes the
    control `type`, which this host's baseline has never emitted and which a
    declared `format` — and only a declared `format` — now adds."""
    wire = _form('{"id":"x","kind":{"$type":"Text","onChange":"<closure>"},"label":"L","required":true}')
    result = decode_node(wire)
    assert result.ok
    html = render_html(result.value)
    assert '<input class="fuaran-form-field-control" data-fuaran-field="x" />' in html
    # Scoped to the INPUT: the submit button legitimately carries `type="submit"`.
    assert not re.search(r"<input[^>]*type=", html)


def test_a_textarea_gets_no_pattern_attribute() -> None:
    """HTML has no `pattern` on `<textarea>`. Emitting one would look like
    coverage and be inert — the reference host declines it for the same reason."""
    wire = _form(
        '{"id":"x","kind":{"$type":"TextArea","onChange":"<closure>","rows":4},"label":"L","required":true,'
        '"rule":{"maxLength":200,"pattern":"a+"}}'
    )
    result = decode_node(wire)
    assert result.ok
    html = render_html(result.value)
    assert "pattern=" not in html
    assert 'maxlength="200"' in html


# ── The declared subset ──────────────────────────────────────────────────────


@corpus_required
def test_the_corpus_rule_fixture_raises_no_rule_family_finding() -> None:
    """The fixture is a correctly-authored form, so a rule that fired on it would
    be accusing the specification's own worked example."""
    src = (CORPUS_ROOT / "nodes/form-field-rules.json").read_text(encoding="utf-8")
    result = decode_node(src)
    assert result.ok
    codes = {f.code for f in validate_node(result.value)}
    assert codes.isdisjoint({"FUARAN099", "FUARAN100", "FUARAN101"})


@pytest.mark.parametrize("code", ["FUARAN099", "FUARAN105"])
def test_the_declined_codes_are_declared_abstentions_rather_than_silence(code: str) -> None:
    """669's whole thesis is that a deliberate subset is legitimate as long as it
    is DECLARED. An unported rule left to the blanket default reads as "not got
    round to it"; these two were considered and declined, and the reason has to
    survive in the artefact rather than in a phase outcome nobody greps."""
    from pathlib import Path

    decl = json.loads((Path(__file__).resolve().parents[1] / "validator-coverage.json").read_text(encoding="utf-8"))
    assert code in decl["abstained"], f"{code} was declined deliberately — say so, and say why"
    assert code not in decl["implemented"]
    assert len(decl["abstained"][code]) > 200
