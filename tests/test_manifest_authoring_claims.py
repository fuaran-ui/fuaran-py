"""The capability manifest claims only what the builders can author (Phase 2699).

``Kind.<Tag>`` in the manifest's ``tokens`` is generated from the host's *record* types
(:mod:`fuaran_ui.schema.types`), which is wider than the public builder surface: a record gains a
member the day the wire does, and a builder gains the parameter only when someone writes it. At
0.11.0 that gap let the manifest say the host could author option groups when ``fuaran.select`` had
no ``groups`` parameter. This file closes it in two halves:

* the two option-group fixtures are built THROUGH ``fuaran.select`` and compared with the corpus
  byte for byte;
* every ``Kind`` token the manifest claims names the builders that author it, and every field of
  that kind's record is a parameter of one of them (by its name or a :data:`RENAMED` one), a
  :data:`DERIVED` one that the choice of builder fixes, or a reasoned :data:`GAPS` entry. A claim
  with no builder, a field no builder reaches, and a declaration that has gone stale are each red.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from _corpus import CORPUS_ROOT
from fuaran_ui.schema import types as t
from fuaran_ui.ui import encode, fuaran
from fuaran_ui.ui.controls import control

MANIFEST = Path(__file__).resolve().parents[1] / "conformance" / "host-capability-manifest.json"

# ── The two option-group fixtures, through the builder ───────────────────────────────────────


def _static_options(*pairs: tuple[str, str]) -> t.Static:
    return t.Static(tuple(t.SelectOption(label, value) for label, value in pairs))


def _bare(node: t.UiNode) -> str:
    """The node without the accessibility trait the builder adds and these fixtures do not carry."""
    return encode(dataclasses.replace(node, accessibility=None))


def _grouped() -> str:
    return _bare(
        fuaran.select(
            "select-option-groups",
            label="Chart",
            source=_static_options(
                ("Revenue", "revenue-trend"),
                ("Orders", "orders-trend"),
                ("By region", "region-breakdown"),
                ("By channel", "channel-breakdown"),
            ),
            value=t.Static("revenue-trend"),
            on_change=False,
            groups=[
                t.SelectOptionGroup("Trend", ("orders-trend", "revenue-trend")),
                t.SelectOptionGroup("Breakdown", ("region-breakdown", "channel-breakdown")),
            ],
        )
    )


def _remainder() -> str:
    return _bare(
        fuaran.select(
            "select-option-groups-remainder",
            label="Chart",
            source=_static_options(
                ("Summary", "summary"),
                ("Revenue", "revenue-trend"),
                ("By region", "region-breakdown"),
                ("Notes", "notes"),
            ),
            value=t.State("chart", default_value="summary"),
            on_change=False,
            groups=[
                t.SelectOptionGroup("Trend", ("revenue-trend",)),
                t.SelectOptionGroup("Breakdown", ("region-breakdown",)),
            ],
        )
    )


@pytest.mark.parametrize(
    ("fixture", "built"),
    [("select-option-groups", _grouped), ("select-option-groups-remainder", _remainder)],
)
def test_the_select_builder_spells_option_groups_byte_identically(fixture: str, built: object) -> None:
    corpus = (CORPUS_ROOT / "nodes" / f"{fixture}.json").read_text(encoding="utf-8")
    assert built() == corpus.strip()  # type: ignore[operator]


@pytest.mark.parametrize("groups", [None, []])
def test_an_absent_or_empty_groups_writes_no_member(groups: list[t.SelectOptionGroup] | None) -> None:
    source = _static_options(("A", "a"))
    assert '"groups"' not in encode(fuaran.select("s", label="L", source=source, groups=groups))


def test_the_control_select_forwards_groups() -> None:
    grouped = control.select("pick", options=["a", "b"], groups=[t.SelectOptionGroup("G", ("b",))])
    assert grouped.kind.groups == (t.SelectOptionGroup("G", ("b",)),)  # type: ignore[attr-defined]
    assert control.select("pick", options=["a", "b"]).kind.groups == ()  # type: ignore[attr-defined]


# ── The manifest's authoring claims, checked against the builders ────────────────────────────

#: Each claimed ``Kind`` and the ``fuaran`` builders that construct it.
BUILDERS: dict[str, tuple[str, ...]] = {
    "Badge": ("badge",),
    "Box": ("divider", "stack", "grid_layout", "masonry_layout", "card", "dashboard"),
    "Button": ("button",),
    "Callout": ("callout",),
    "Chart": ("chart",),
    "CodeBlock": ("code_block",),
    "Custom": ("custom",),
    "DataGrid": ("grid",),
    "Disclosure": ("disclosure",),
    "Drawing": ("drawing",),
    "Embed": ("embed",),
    "ErrorBoundary": ("error_boundary",),
    "Fact": ("fact",),
    "FileUpload": ("file_upload",),
    "Filters": ("filters",),
    "Form": ("form",),
    "FragmentDecl": ("fragment_decl",),
    "FragmentRef": ("fragment_ref",),
    "Heading": ("heading",),
    "Icon": ("icon",),
    "Image": ("image",),
    "LabelValueRow": ("label_value_row",),
    "Link": ("link",),
    "List": ("list",),
    "Map": ("map",),
    "Markdown": ("markdown",),
    "Math": ("math",),
    "Media": ("video", "audio"),
    "Metric": ("metric",),
    "Modal": ("modal",),
    "Mount": ("mount",),
    "Progress": ("progress",),
    "ScrollArea": ("scroll_area",),
    "Select": ("select",),
    "Skeleton": ("skeleton",),
    "Sparkline": ("sparkline",),
    "SplitPanel": ("split_panel",),
    "Stepper": ("stepper",),
    "SummaryList": ("summary_list",),
    "Switch": ("switch",),
    "Tabs": ("tabs",),
    "Toast": ("toast",),
    "Tree": ("tree",),
}

#: A field authored under another parameter name.
RENAMED: dict[tuple[str, str], str] = {
    ("Icon", "icon"): "name",
    ("Markdown", "text"): "body",
}

#: A field the CHOICE of builder fixes, so no builder takes it as a parameter.
DERIVED: dict[tuple[str, str], str] = {
    ("Media", "kind"): "video writes Video and audio writes Audio",
    ("Box", "layout"): "stack / grid_layout / masonry_layout choose the layout and take its own parameters",
    ("Box", "role"): "card / dashboard / divider / stack choose the role",
}

#: A field of a claimed kind that NO builder reaches: the kind is authorable, the member only by
#: building the record by hand. Each is a finding of this phase, named in its outcome.
GAPS: dict[tuple[str, str], str] = {
    ("Box", "keep_together"): "no container builder takes the print flag (fuaran#1473); the record only",
    ("Box", "break_before"): "no container builder takes the print flag (fuaran#1473); the record only",
}

_MANIFEST = json.loads(MANIFEST.read_text(encoding="utf-8"))
_CLAIMED = sorted(token.split(".", 1)[1] for token in _MANIFEST["tokens"] if token.startswith("Kind."))


def _parameters(kind: str) -> set[str]:
    names: set[str] = set()
    for builder in BUILDERS[kind]:
        names |= set(inspect.signature(getattr(fuaran, builder)).parameters)
    return names


def _unauthored(kind: str) -> list[str]:
    params = _parameters(kind)
    return [
        f.name
        for f in dataclasses.fields(getattr(t, kind))
        if f.name not in params and RENAMED.get((kind, f.name)) not in params
    ]


def test_every_claimed_kind_names_its_builders() -> None:
    assert _CLAIMED == sorted(BUILDERS), "a Kind claimed with no builder row, or a row for an unclaimed Kind"
    for kind, builders in BUILDERS.items():
        for builder in builders:
            assert callable(getattr(fuaran, builder, None)), f"{kind}: no builder fuaran.{builder}"


@pytest.mark.parametrize("kind", _CLAIMED)
def test_every_field_of_a_claimed_kind_is_authorable(kind: str) -> None:
    undeclared = [f for f in _unauthored(kind) if (kind, f) not in DERIVED and (kind, f) not in GAPS]
    assert not undeclared, f"{kind}: no builder parameter authors {undeclared}; add one or declare the gap"


def test_a_declaration_that_has_gone_stale_is_red() -> None:
    stale: list[str] = []
    for kind, field in {**DERIVED, **GAPS}:
        if kind not in BUILDERS or field not in {f.name for f in dataclasses.fields(getattr(t, kind))}:
            stale.append(f"{kind}.{field}: names no field of a claimed kind")
        elif field not in _unauthored(kind):
            stale.append(f"{kind}.{field}: a builder authors it now; retire the declaration")
    for (kind, field), param in RENAMED.items():
        if param not in _parameters(kind):
            stale.append(f"{kind}.{field}: renamed to {param}, which no builder takes")
    assert not stale, stale
