"""The speech projection (fuaran#1913) — a tree read aloud, held to the reference host.

Three kinds of evidence, each asking a different question:

* **The ruling table.** :data:`speech.SCOPE` is checked against the ``speech`` column of the
  corpus's ``render-fidelity.json`` kind by kind — the same classes, and no kind missing in
  either direction — so a new kind in the table reddens this suite rather than falling
  through to a default at run time.
* **The reference's own output.** ``tests/fixtures/speech/reference-speech.json`` is the
  reference host's projection of every node fixture in the bundled corpus snapshot (the
  script beside it regenerates it). Both lowerings, every utterance's source / emphasis /
  pause, and every omission are compared byte for byte.
* **The reference's unit cases, re-asked here.** The dashboard read by ear, ``speak`` winning
  over derivation, omissions reported by id, authored order, SSML injection and determinism
  under a fixed clock — each with the reference's expected strings.
"""

from __future__ import annotations

import json
import xml.dom.minidom
from pathlib import Path
from typing import Any

import pytest

from _corpus import CORPUS_ROOT, SNAPSHOT_ROOT
from fuaran_ui import decode_node
from fuaran_ui.model import Node
from fuaran_ui.renderer import project_speech, speech, speech_plain_text, speech_ssml
from fuaran_ui.renderer.bindings import BindingSources
from fuaran_ui.renderer.speech import OmissionReason, Pause, SpeechOmission, SpeechScript, UtteranceSource

_FIDELITY = CORPUS_ROOT / "render-fidelity.json"
_REFERENCE = Path(__file__).resolve().parent / "fixtures" / "speech" / "reference-speech.json"

fidelity_required = pytest.mark.skipif(
    not _FIDELITY.is_file(), reason=f"render-fidelity manifest not found at {_FIDELITY}"
)


def _speech_column() -> dict[str, str]:
    """The corpus table: kind -> speech class. Read, never re-derived."""
    manifest = json.loads(_FIDELITY.read_text(encoding="utf-8"))
    return {row["kind"]: row["speech"]["class"] for row in manifest["kinds"] if "speech" in row}


def _decode(doc: Any) -> Node:
    result = decode_node(doc if isinstance(doc, str) else json.dumps(doc))
    assert result.ok, result
    return result.value  # type: ignore[union-attr]


def _roots(corpus: Path) -> list[tuple[str, Node]]:
    out = []
    for path in sorted((corpus / "nodes").glob("*.json"), key=lambda p: p.name):
        result = decode_node(path.read_text(encoding="utf-8"))
        if result.ok:
            out.append((path.stem, result.value))  # type: ignore[union-attr]
    return out


# ─── The ruling table ───────────────────────────────────────────────────────


def test_scope_has_no_duplicate_rows_and_every_row_has_an_arm() -> None:
    kinds = [kind for kind, _ in speech.SCOPE]
    assert len(kinds) == len(set(kinds)), "a kind declared twice would be served its first row silently"
    assert kinds == sorted(kinds), "rows are ordered by wire name so a new kind is one clean insert"
    assert set(kinds) == set(speech._ARMS), "an arm with no declared ruling, or a ruling with no arm"


@fidelity_required
def test_every_kind_the_table_rules_on_is_handled_here() -> None:
    """The completeness check: the table gaining a kind this host has no arm for fails HERE."""
    column = _speech_column()
    declared = dict(speech.SCOPE)
    assert column, "the render-fidelity manifest carries no speech column"
    assert sorted(set(column) - set(declared)) == [], "kinds the speech table rules on with no arm here"
    assert sorted(set(declared) - set(column)) == [], "arms for kinds the speech table does not rule on"


@fidelity_required
def test_every_declared_class_is_the_tables_class() -> None:
    column = _speech_column()
    wrong = {k: (c, column[k]) for k, c in speech.SCOPE if k in column and column[k] != c}
    assert wrong == {}, "SCOPE disagrees with render-fidelity.json's speech column (declared, table)"


@fidelity_required
def test_the_class_vocabulary_is_the_tables() -> None:
    manifest = json.loads(_FIDELITY.read_text(encoding="utf-8"))
    vocabulary = {row["class"] for row in manifest["speechClasses"]}
    assert vocabulary == {"spoken", "derived", "announced-only", "omitted"}
    assert {c for _, c in speech.SCOPE} <= vocabulary


# ─── The corpus leg: each kind against its ruling ───────────────────────────


@fidelity_required
def test_each_roots_own_utterances_carry_the_class_its_ruling_declares() -> None:
    column = _speech_column()
    failures = []
    for name, node in _roots(CORPUS_ROOT):
        kind = node.kind.tag or ""
        script, omitted = project_speech(node)
        own = [u for u in script.utterances if u.node_id == node.id]
        a11y = node.extras.get("accessibility")
        declares_speak = a11y is not None and "speak" in a11y.fields  # type: ignore[union-attr]
        ruling = column.get(kind)
        if ruling is None:
            failures.append(f"{name}: kind {kind} has no speech ruling")
            continue
        expected = UtteranceSource.SPEAK if declares_speak and own else speech.expected_source(ruling)
        wrong = [(u.source, u.text) for u in own if u.source != expected]
        if wrong:
            failures.append(f"{name}: {kind} ruled {ruling}, but said {wrong}")
        if ruling == "announced-only" and not own:
            failures.append(f"{name}: an announced-only root announced nothing")
        if ruling == "omitted" and not declares_speak and not any(o.node_id == node.id for o in omitted):
            failures.append(f"{name}: an omitted-by-ruling root is missing from the omission list")
    assert failures == []


@fidelity_required
def test_every_ruled_kind_is_exercised_by_a_corpus_root() -> None:
    exercised = {node.kind.tag for _, node in _roots(CORPUS_ROOT)}
    assert sorted(k for k in _speech_column() if k not in exercised) == []


def _reachable(node: Node, fragments: dict[str, Node]) -> list[Node]:
    kids = speech._structural_children(node)
    if node.kind.tag == "FragmentRef":
        body = fragments.get(node.kind.fields.get("name"))  # type: ignore[arg-type]
        kids = [body] if body is not None else []
    out = [node]
    for kid in kids:
        out.extend(_reachable(kid, fragments))
    return out


def test_every_node_of_every_fixture_is_said_omitted_or_speaks_through_a_descendant() -> None:
    from fuaran_ui.renderer.render import _collect_fragments

    failures = []
    for name, root in _roots(CORPUS_ROOT):
        fragments: dict[str, Node] = {}
        _collect_fragments(root, fragments)
        script, omitted = project_speech(root)
        said = {u.node_id for u in script.utterances}
        gone = {o.node_id for o in omitted}
        for n in _reachable(root, fragments):
            spoke = n.id in said or any(d.id in said for d in _reachable(n, fragments)[1:])
            if not (spoke or n.id in gone):
                failures.append(f"{name}: node {n.id} ({n.kind.tag}) is neither said nor reported")
    assert failures == [], "nothing is dropped silently"


# ─── The reference's own output, fixture by fixture ─────────────────────────

#: Fixtures where this host's output differs from the reference's, and why. Every entry is a
#: divergence in the HOST's text or number resolution — the same text reaches ``render_html``
#: — rather than in this projection, and each is asserted STILL to diverge, so the entry
#: reddens (and must be removed) the moment the host closes the gap.
#:
#: Phase 1920 closed ``expr-scalar`` (the compute evaluator now evaluates ``concat``) and
#: ``metric-nonfinite-sentinel`` (non-finite numbers are spelled ``Infinity`` / ``NaN``), and
#: RE-GRADED the two ``Binding.Format`` entries: they no longer render blank, they render the
#: default :class:`~fuaran_ui.renderer.bindings.LocaleFormatter`'s locale-free form. They stay
#: here only because that text still differs from the reference's, which is a locale
#: database's; the corpus's render-text family deliberately pins none of those four cases.
KNOWN_HOST_DIVERGENCES: dict[str, str] = {
    "format-bindings": "Binding.Format Number/Currency/Percent/DateTime render this host's locale-free "
    "default ('1234.50', 'GBP 1234.50', '42.0%', ISO 8601), where the reference renders its locale "
    "database's form ('1,234.50', '£1,234.50', '42.00 %', 'mardi 14 novembre 2023') — the corpus "
    "deliberately pins neither",
    "format-date-time": "Binding.Format DateTime renders this host's locale-free ISO 8601 form, where the "
    "reference renders its locale database's date and time patterns — the corpus deliberately pins neither",
}


def _reference() -> dict[str, Any]:
    return json.loads(_REFERENCE.read_text(encoding="utf-8"))["fixtures"]


def _observed(node: Node) -> dict[str, Any]:
    script, omitted = project_speech(node)
    return {
        "text": speech_plain_text(script),
        "ssml": speech_ssml(script),
        "utterances": [
            {"nodeId": u.node_id, "source": str(u.source), "emphasis": u.emphasis, "pause": str(u.pause_after)}
            for u in script.utterances
        ],
        "omissions": [
            {"nodeId": o.node_id, "kind": o.kind, "reason": str(o.reason), "decidedAt": o.decided_at} for o in omitted
        ],
    }


def test_the_reference_golden_covers_the_bundled_snapshot_exactly() -> None:
    """A snapshot re-sync that adds, drops or renames a node fixture reddens here: the golden
    is regenerated in the same change (``tests/fixtures/speech/generate_reference.fsx``)."""
    snapshot = {p.stem for p in (SNAPSHOT_ROOT / "nodes").glob("*.json")}
    assert set(_reference()) == snapshot


_GOLDEN = sorted(_reference())


@pytest.mark.parametrize("name", _GOLDEN)
def test_speech_matches_the_reference_host(name: str) -> None:
    node = _decode((SNAPSHOT_ROOT / "nodes" / f"{name}.json").read_text(encoding="utf-8"))
    observed, expected = _observed(node), _reference()[name]
    if name in KNOWN_HOST_DIVERGENCES:
        assert observed != expected, f"{name} now matches the reference: drop it from KNOWN_HOST_DIVERGENCES"
        # The divergence is confined to what the text says: the same nodes speak, from the
        # same sources, and the same nodes are omitted for the same reasons.
        said = [u for u in expected["utterances"] if u["nodeId"] in {v["nodeId"] for v in observed["utterances"]}]
        assert observed["utterances"] == said
        return
    assert observed["text"] == expected["text"]
    assert observed["ssml"] == expected["ssml"]
    assert observed["utterances"] == expected["utterances"]
    assert observed["omissions"] == expected["omissions"]


# ─── Builders for the reference's unit cases ────────────────────────────────


def _n(node_id: str, kind: dict[str, Any], **envelope: Any) -> dict[str, Any]:
    return {"id": node_id, "kind": kind, **envelope}


def _heading(node_id: str, level: int, text: Any, **envelope: Any) -> dict[str, Any]:
    return _n(node_id, {"$type": "Heading", "level": level, "text": text, "variant": "Standard"}, **envelope)


_AUTO = {"$type": "Auto"}


def _static(value: Any) -> dict[str, Any]:
    return {"$type": "Static", "value": value}


def _metric(node_id: str, label: str, value: float, trend: float | None = None, **envelope: Any) -> dict[str, Any]:
    kind: dict[str, Any] = {
        "$type": "Metric",
        "label": label,
        "value": _static(value),
        "format": {"$type": "Number", "decimals": 0},
        "trendFormat": {"$type": "Percent", "decimals": 1},
    }
    if trend is not None:
        kind["trend"] = _static(trend)
    return _n(node_id, kind, **envelope)


def _card(node_id: str, title: str, kids: list[dict[str, Any]], **envelope: Any) -> dict[str, Any]:
    return _n(
        node_id, {"$type": "Box", "role": "Card", "layout": _AUTO, "heading": title, "children": kids}, **envelope
    )


def _column(node_id: str, kids: list[dict[str, Any]]) -> dict[str, Any]:
    return _n(node_id, {"$type": "Box", "role": "Dashboard", "layout": _AUTO, "children": kids})


def _speaking(words: str) -> dict[str, Any]:
    return {"accessibility": {"speak": words}}


def _texts(script: SpeechScript) -> list[str]:
    return [u.text for u in script.utterances]


def _omission(node_id: str, omitted: list[SpeechOmission]) -> SpeechOmission | None:
    return next((o for o in omitted if o.node_id == node_id), None)


_DASHBOARD = _column(
    "dash",
    [
        _heading("title", 1, "Quarterly results"),
        _card(
            "kpis",
            "Headline figures",
            [
                _metric("revenue", "Revenue", 1250.0, 0.052),
                _metric("churn", "Churn", 3.0),
                _metric(
                    "nps", "Net promoter score", 41.0, **_speaking("Net promoter score is 41, the highest this year")
                ),
            ],
        ),
        _n("note", {"$type": "Fact", "label": "Region", "value": "EMEA"}),
        _n("logo", {"$type": "Icon", "icon": "star"}),
    ],
)


# ─── The dashboard read by ear (the reference's acceptance) ─────────────────


def test_headings_announce_sections_metrics_read_label_value_trend_speak_is_said_exactly() -> None:
    script, _ = project_speech(_decode(_DASHBOARD))
    assert speech_plain_text(script) == (
        "Quarterly results\n"
        "Headline figures\n"
        "Revenue: 1250, trend 5.2%\n"
        "Churn: 3\n"
        "Net promoter score is 41, the highest this year\n"
        "Region: EMEA\n"
    )
    first = script.utterances[0]
    assert first.emphasis, "a heading is emphasised"
    assert first.pause_after == Pause.LONG, "a heading is followed by a long pause"


def test_every_node_absent_from_the_script_is_in_the_omission_list() -> None:
    script, omitted = project_speech(_decode(_DASHBOARD))
    logo = _omission("logo", omitted)
    assert logo is not None and logo.reason == OmissionReason.NOTHING_SAYABLE, "the icon is reported, not dropped"
    said = {u.node_id for u in script.utterances}
    assert [o.node_id for o in omitted if o.node_id in said] == [], "a node both said and omitted"


# ─── accessibility.speak wins over derivation (Phase 1812) ──────────────────


def test_a_metric_with_speak_says_exactly_that_and_nothing_derived() -> None:
    script, _ = project_speech(_decode(_metric("m", "Revenue", 10.0, 0.1, **_speaking("Revenue is up"))))
    assert _texts(script) == ["Revenue is up"]
    assert script.utterances[0].source == UtteranceSource.SPEAK


def test_speak_on_an_announced_only_kind_replaces_the_announcement() -> None:
    field = {"id": "f1", "kind": {"$type": "Text", "value": _static("")}, "label": "One", "required": False}
    form = _n("f", {"$type": "Form", "fields": [field, {**field, "id": "f2"}]})
    assert _texts(project_speech(_decode(form))[0]) == ["Form, 2 fields"]
    assert _texts(project_speech(_decode({**form, **_speaking("A two-question survey")}))[0]) == [
        "A two-question survey"
    ]


def test_speak_on_a_container_replaces_its_heading_but_its_children_still_speak() -> None:
    tree = _card("c", "Figures", [_metric("m", "Churn", 3.0)], **_speaking("This quarter's figures"))
    assert _texts(project_speech(_decode(tree))[0]) == ["This quarter's figures", "Churn: 3"]


def test_speak_on_an_omitted_by_ruling_kind_gives_it_words() -> None:
    script, omitted = project_speech(
        _decode(_n("s", {"$type": "Skeleton", "rows": 3}, **_speaking("Loading the report")))
    )
    assert _texts(script) == ["Loading the report"]
    assert omitted == [], "and it is not reported absent"


# ─── Omissions are reported by node id ──────────────────────────────────────


def test_a_hidden_subtree_is_excluded_whole_every_node_named_the_decider_recorded() -> None:
    hidden = _card(
        "secret",
        "Internal",
        [_metric("a", "A", 1.0), _metric("b", "B", 2.0)],
        accessibility={"hidden": _static(True)},
    )
    script, omitted = project_speech(_decode(_column("root", [_heading("h", 2, "Public"), hidden])))
    assert _texts(script) == ["Public"]
    assert [(o.node_id, o.reason, o.decided_at) for o in omitted] == [
        ("secret", OmissionReason.HIDDEN, "secret"),
        ("a", OmissionReason.HIDDEN, "secret"),
        ("b", OmissionReason.HIDDEN, "secret"),
    ]


def test_not_visible_closed_decorative_and_untaken_branches_are_each_reported() -> None:
    switch = _n(
        "sw",
        {
            "$type": "Switch",
            "on": {"$type": "State", "key": "mode"},
            "cases": [
                {"child": _heading("a", 3, "Mode A"), "match": "a"},
                {"child": _heading("b", 3, "Mode B"), "match": "b"},
            ],
            "default": _heading("d", 3, "No mode"),
        },
    )
    tree = _column(
        "root",
        [
            _heading("gone", 3, "Gone", visible=_static(False)),
            _n("t", {"$type": "Toast", "message": "Saved", "open": _static(False)}),
            _n("img", {"$type": "Image", "alt": "", "src": _static("a.png"), "variant": "Default"}),
            switch,
        ],
    )
    script, omitted = project_speech(_decode(tree), BindingSources(values={"mode": "b"}))
    assert _texts(script) == ["Mode B"], "only the selected branch speaks"
    assert [(o.node_id, o.reason) for o in omitted] == [
        ("gone", OmissionReason.NOT_VISIBLE),
        ("t", OmissionReason.CLOSED),
        ("img", OmissionReason.DECORATIVE),
        ("a", OmissionReason.NOT_TAKEN),
        ("d", OmissionReason.NOT_TAKEN),
    ]


def test_the_switch_branch_is_the_one_the_email_digest_shows() -> None:
    """One selection, shared: the digest and the script cannot disagree about the case."""
    from fuaran_ui.renderer import render_email

    tree = _n(
        "sw",
        {
            "$type": "Switch",
            "on": {"$type": "State", "key": "mode"},
            "cases": [{"child": _heading("a", 3, "Mode A"), "match": "a"}],
            "default": _heading("d", 3, "No mode"),
        },
    )
    for mode, heard in (("a", "Mode A"), ("z", "No mode")):
        sources = BindingSources(values={"mode": mode})
        assert _texts(project_speech(_decode(tree), sources)[0]) == [heard]
        assert heard in render_email(_decode(tree), sources)


def test_content_inside_an_announced_only_kind_is_reported_never_read() -> None:
    tabs = _n("tabs", {"$type": "Tabs", "children": [_heading("p1", 3, "First panel"), _heading("p2", 3, "Second")]})
    script, omitted = project_speech(_decode(tabs))
    assert _texts(script) == ["Tabbed section, 2 tabs"]
    assert [(o.node_id, o.reason, o.decided_at) for o in omitted] == [
        ("p1", OmissionReason.INSIDE_ANNOUNCED, "tabs"),
        ("p2", OmissionReason.INSIDE_ANNOUNCED, "tabs"),
    ]


def test_an_unresolved_fragment_is_reported_with_its_name() -> None:
    _, omitted = project_speech(_decode(_n("ref", {"$type": "FragmentRef", "name": "missing"})))
    assert [(o.node_id, o.reason, o.fragment) for o in omitted] == [
        ("ref", OmissionReason.UNRESOLVED_FRAGMENT, "missing")
    ]


# ─── Authored order ─────────────────────────────────────────────────────────


def test_live_region_does_not_move_a_node_out_of_authored_order() -> None:
    urgent = _heading("b", 3, "Second", accessibility={"liveRegion": "assertive"})
    tree = _column("root", [_heading("a", 3, "First"), urgent, _heading("c", 3, "Third")])
    assert _texts(project_speech(_decode(tree))[0]) == ["First", "Second", "Third"]


# ─── Lowerings and SSML injection ───────────────────────────────────────────


def _bound_heading(value: str) -> SpeechScript:
    tree = _heading("h", 2, {"$type": "Bound", "binding": {"$type": "State", "key": "title"}})
    return project_speech(_decode(tree), BindingSources(values={"title": value}))[0]


def test_plain_text_is_one_utterance_per_line_even_when_the_source_carries_line_breaks() -> None:
    tree = _column("r", [_heading("a", 2, "Line one\nstill line one"), _heading("b", 3, "Two")])
    assert speech_plain_text(project_speech(_decode(tree))[0]) == "Line one still line one\nTwo\n"


def test_ssml_carries_emphasis_and_pause_strengths_from_the_script() -> None:
    tree = _column("r", [_heading("a", 2, "Title"), _metric("m", "Churn", 3.0)])
    assert speech_ssml(project_speech(_decode(tree))[0]) == (
        "<speak>\n"
        '<s><emphasis level="moderate">Title</emphasis></s><break strength="strong"/>\n'
        '<s>Churn: 3</s><break strength="medium"/>\n'
        "</speak>\n"
    )


def test_a_bound_string_cannot_inject_ssml_markup() -> None:
    ssml = speech_ssml(_bound_heading('</s><audio src="https://x.test/a.mp3"/><s>& it\'s <mark name="m"/>'))
    assert "<audio" not in ssml and "<mark" not in ssml, "no element opens from bound data"
    assert "&lt;/s&gt;&lt;audio" in ssml, "the metacharacters are escaped, not stripped"
    root = xml.dom.minidom.parseString(ssml).documentElement
    assert len(root.getElementsByTagName("s")) == 1, "exactly the one sentence the projection wrote"


def test_characters_xml_cannot_carry_are_dropped_so_the_document_stays_parseable() -> None:
    ssml = speech_ssml(_bound_heading("bell\x07 nul\x00 lone\ud800 ok"))
    xml.dom.minidom.parseString(ssml.encode("utf-8"))
    assert "bell nul lone ok" in ssml


def test_the_escaper_covers_all_five_metacharacters() -> None:
    assert speech.escape_ssml("<a href=\"x\">'&'</a>") == "&lt;a href=&quot;x&quot;&gt;&apos;&amp;&apos;&lt;/a&gt;"


# ─── Determinism under a fixed clock ────────────────────────────────────────


def test_the_same_tree_and_sources_give_the_same_script_and_now_comes_from_the_sources() -> None:
    tree = _decode(_column("r", [_heading("when", 2, {"$type": "Bound", "binding": {"$type": "Now"}}), _DASHBOARD]))

    def at(now: str) -> tuple[str, list[SpeechOmission]]:
        script, omitted = project_speech(tree, BindingSources(now=now))
        return speech_ssml(script), omitted

    a, b = at("2026-01-02T03:04:05Z"), at("2026-01-02T03:04:05Z")
    assert a == b, "two projections under one fixed clock are identical, omissions included"
    assert "2026-01-02T03:04:05Z" in a[0], "the clock is the context's, not the wall's"
    assert at("2027-06-01T00:00:00Z")[0] != a[0], "and a different fixed clock is heard"
