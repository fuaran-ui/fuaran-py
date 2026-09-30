"""Phase 1920 — text resolution at parity with the reference host.

Three defects in this host's shared text resolution, each visible on every static
projection (HTML, email, markdown document, speech) because all of them resolve text and
figures through :mod:`fuaran_ui.renderer.bindings`:

* **Non-finite numbers** were spelled Python's way (``inf`` / ``nan``); the reference
  writes ``Infinity`` / ``NaN``.
* **``Expr`` ``concat``** did not evaluate, so a bound text built from it rendered blank.
* **The four locale-database ``Format`` cases** (``Number`` / ``Currency`` / ``Percent``
  / ``DateTime``) resolved to ABSENCE in a slot while the same host's cell formatter
  rendered them. They now render through ONE seam, :class:`LocaleFormatter`, whose
  stdlib default is locale-free; the cell formatter delegates to that same default.

The seam is proven honoured on every render path by installing a stub formatter — per
render pass and process-wide — and finding its marker in each projection's output.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from _corpus import SNAPSHOT_ROOT
from fuaran_ui import decode_node
from fuaran_ui.model import Node, Obj
from fuaran_ui.renderer import project_speech, render_email, render_html, render_markdown, speech_plain_text
from fuaran_ui.renderer.bindings import (
    LOCALE_FREE_FORMATTER,
    BindingSources,
    LocaleFormatter,
    LocaleFreeFormatter,
    active_locale_formatter,
    format_number,
    install_locale_formatter,
    render_text,
    resolve_scalar_text,
)


def _decode(payload: dict[str, Any] | str) -> Node:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    result = decode_node(raw)
    assert result.ok, getattr(result, "error", result)
    return result.value


def _fixture(name: str) -> Node:
    return _decode((SNAPSHOT_ROOT / "nodes" / f"{name}.json").read_text(encoding="utf-8"))


def _child_text(root: Node, node_id: str, sources: BindingSources | None = None) -> str:
    children = root.kind.fields["children"]
    for child in children.items:  # type: ignore[union-attr]
        if isinstance(child, Node) and child.id == node_id:
            return render_text(child.kind.fields.get("text"), sources)
    raise AssertionError(f"{node_id} is not a child of {root.id}")


# ─── The stdlib default: locale-free, never blank ────────────────────────────


def test_the_default_renders_every_locale_database_case_in_its_locale_free_form() -> None:
    root = _fixture("format-bindings")
    assert _child_text(root, "fmt-number") == "1234.50"
    assert _child_text(root, "fmt-currency") == "GBP 1234.50"
    assert _child_text(root, "fmt-percent") == "42.0%"
    assert _child_text(root, "fmt-date") == "2023-11-14"


def test_the_default_renders_the_declared_date_time_style_pair_as_iso_8601() -> None:
    root = _fixture("format-date-time")
    assert _child_text(root, "fmt-date-time") == "2023-11-14T22:13"  # Medium date + Short time
    assert _child_text(root, "fmt-time-only") == "22:13"  # Short time alone
    assert _child_text(root, "fmt-time-full") == "2023-11-14T22:13:20"  # a longer time style carries seconds


def test_the_default_honours_no_locale_tag_and_says_so() -> None:
    """An explicit tag is knowingly NOT honoured by the default — the text is the same under any."""
    assert LocaleFreeFormatter.honours_locale is False
    assert LOCALE_FREE_FORMATTER.number(1234.5, 2, "fr-FR") == LOCALE_FREE_FORMATTER.number(1234.5, 2, "en-US")
    assert LOCALE_FREE_FORMATTER.date_time(1700000000, "Full", None, "de-DE") == "2023-11-14"


def test_the_default_covers_the_undeclared_and_out_of_range_edges() -> None:
    assert LOCALE_FREE_FORMATTER.number(3.0, None, None) == "3"  # the plain canonical number
    assert LOCALE_FREE_FORMATTER.percent(0.425, 2, None) == "42.50%"
    assert LOCALE_FREE_FORMATTER.date_time(1700000000, None, None, None) == "2023-11-14"
    # A value no calendar date names renders as the number, never as an invented date.
    assert LOCALE_FREE_FORMATTER.date_time(1e20, "Short", None, None) == format_number(None, 1e20)
    assert LOCALE_FREE_FORMATTER.date_time(float("nan"), "Short", None, None) == "NaN"


def test_the_cell_path_delegates_to_the_same_default() -> None:
    """The slot and cell paths share ONE implementation, so they cannot drift again."""
    for value in (1234.5, -0.125, 0.0, float("inf"), float("nan")):
        assert format_number(Obj("Number", {"decimals": 2}), value) == LOCALE_FREE_FORMATTER.number(value, 2, None)
        assert format_number(Obj("Currency", {"code": "GBP"}), value) == LOCALE_FREE_FORMATTER.currency(
            value, "GBP", None
        )
        assert format_number(Obj("Percent", {}), value) == LOCALE_FREE_FORMATTER.percent(value, None, None)


# ─── Non-finite numbers are spelled as the reference spells them ─────────────


@pytest.mark.parametrize(
    ("fmt", "value", "expected"),
    [
        (None, float("inf"), "Infinity"),
        (None, float("-inf"), "-Infinity"),
        (None, float("nan"), "NaN"),
        (Obj("Number", {"decimals": 1}), float("inf"), "Infinity"),
        (Obj("Currency", {"code": "GBP"}), "Infinity", "GBP Infinity"),
        (Obj("Percent", {"decimals": 1}), "NaN", "NaN%"),
        (Obj("SignificantDigits", {"digits": 3}), float("-inf"), "-Infinity"),
    ],
)
def test_format_number_spells_non_finite_values_as_the_reference_does(
    fmt: Obj | None, value: object, expected: str
) -> None:
    assert format_number(fmt, value) == expected


def test_every_projection_inherits_the_non_finite_spelling() -> None:
    node = _fixture("metric-nonfinite-sentinel")
    script, _ = project_speech(node)
    outputs = {
        "html": render_html(node),
        "email": render_email(node),
        "markdown": render_markdown(node),
        "speech": speech_plain_text(script),
    }
    for name, text in outputs.items():
        assert "GBP Infinity" in text, name
        assert "NaN%" in text, name
        assert "inf" not in text.replace("Infinity", ""), name
        assert "nan" not in text.lower().replace("nan%", ""), name


# ─── Expr concat evaluates as the reference evaluator does ───────────────────


def _concat_text(*args: dict[str, Any]) -> str | None:
    binding = {"$type": "Expr", "expr": {"$type": "apply", "fn": "concat", "args": list(args)}}
    node = _decode({"id": "t", "kind": {"$type": "Markdown", "text": {"$type": "Bound", "binding": binding}}})
    return resolve_scalar_text(node.kind.fields["text"].fields["binding"])  # type: ignore[union-attr]


def _lit(cell: dict[str, Any]) -> dict[str, Any]:
    return {"$type": "lit", "cell": cell}


def test_concat_joins_its_arguments() -> None:
    assert _concat_text(_lit({"$type": "Str", "value": "Ada"}), _lit({"$type": "Str", "value": " Lovelace"})) == (
        "Ada Lovelace"
    )


def test_concat_stringifies_a_non_string_argument_through_the_canonical_layout() -> None:
    assert _concat_text(
        _lit({"$type": "Str", "value": "n="}),
        _lit({"$type": "Float", "value": 2.5}),
        _lit({"$type": "Int", "value": 3}),
        _lit({"$type": "Bool", "value": True}),
    ) == ("n=2.53true")


def test_concat_propagates_a_null_argument() -> None:
    """Any null argument makes the whole result null — ABSENCE in a text slot."""
    assert _concat_text(_lit({"$type": "Str", "value": "a"}), _lit({"$type": "Null"})) is None


def test_the_expr_scalar_fixture_renders_its_concat_on_every_projection() -> None:
    node = _fixture("expr-scalar")
    script, _ = project_speech(node)
    for text in (render_html(node), render_email(node), render_markdown(node), speech_plain_text(script)):
        assert "Ada Lovelace" in text


# ─── The seam is honoured on every render path ───────────────────────────────


class _StampingFormatter:
    """A stub :class:`LocaleFormatter` whose output names the call it answered."""

    def number(self, value: float, decimals: int | None, locale: str | None) -> str:
        return f"STUB-number-{decimals}-{locale}"

    def currency(self, value: float, code: str, locale: str | None) -> str:
        return f"STUB-currency-{code}-{locale}"

    def percent(self, value: float, decimals: int | None, locale: str | None) -> str:
        return f"STUB-percent-{decimals}-{locale}"

    def date_time(
        self, epoch_seconds: float, date_style: str | None, time_style: str | None, locale: str | None
    ) -> str:
        return f"STUB-dateTime-{date_style}-{time_style}-{locale}"


_STAMPS = (
    "STUB-number-2-en-US",
    "STUB-currency-GBP-en-GB",
    "STUB-percent-None-nl-NL",  # Ambient reads the pass's locale
    "STUB-dateTime-Medium-None-fr-FR",
)


def _projections(node: Node, sources: BindingSources | None) -> dict[str, str]:
    script, _ = project_speech(node, sources)
    return {
        "html": render_html(node, sources),
        "email": render_email(node, sources),
        "markdown": render_markdown(node, sources),
        "speech": speech_plain_text(script),
    }


def test_a_per_render_formatter_is_honoured_on_every_render_path() -> None:
    node = _fixture("format-bindings")
    sources = BindingSources(locale="nl-NL", formatter=_StampingFormatter())
    for name, text in _projections(node, sources).items():
        for stamp in _STAMPS:
            assert stamp in text, f"{name} did not render {stamp} through the seam"


@pytest.fixture
def installed_stub() -> Iterator[LocaleFormatter]:
    stub = _StampingFormatter()
    previous = install_locale_formatter(stub)
    try:
        yield stub
    finally:
        install_locale_formatter(previous)


def test_a_process_wide_formatter_is_honoured_on_every_render_path(installed_stub: LocaleFormatter) -> None:
    assert active_locale_formatter(None) is installed_stub
    node = _fixture("format-bindings")
    for name, text in _projections(node, BindingSources(locale="nl-NL")).items():
        for stamp in _STAMPS:
            assert stamp in text, f"{name} did not render {stamp} through the installed formatter"


def test_a_per_render_formatter_wins_over_the_installed_one(installed_stub: LocaleFormatter) -> None:
    assert active_locale_formatter(BindingSources(formatter=LOCALE_FREE_FORMATTER)) is LOCALE_FREE_FORMATTER
    assert _child_text(_fixture("format-bindings"), "fmt-number", BindingSources(formatter=LOCALE_FREE_FORMATTER)) == (
        "1234.50"
    )


def test_installing_none_restores_the_default(installed_stub: LocaleFormatter) -> None:
    previous = install_locale_formatter(None)
    try:
        assert previous is installed_stub
        assert active_locale_formatter() is LOCALE_FREE_FORMATTER
    finally:
        install_locale_formatter(previous)


def test_a_cell_never_goes_through_an_installed_formatter(installed_stub: LocaleFormatter) -> None:
    """A cell carries no locale source, so it keeps the locale-free form whatever is installed."""
    assert format_number(Obj("Currency", {"code": "GBP"}), 1234.5) == "GBP 1234.50"
