"""Phase 2499 — a currency figure can set its fraction digits.

Every figure case carries ``decimals``. Absent, a currency renders exactly as
before (``CODE 0.00`` on the locale-free form); present, it is the digit count
on the locale-free form and is handed to the pass's :class:`LocaleFormatter`
as ``decimals`` on the located and ``Binding.Format`` routes. A formatter
written before the member existed still renders every document that does not
use it, because the host passes ``decimals`` only when declared.
"""

from __future__ import annotations

import json

from _corpus import SNAPSHOT_ROOT
from fuaran_ui import decode_node
from fuaran_ui.model import Obj
from fuaran_ui.renderer import render_html
from fuaran_ui.renderer.bindings import BindingSources, format_number, resolve_scalar_text
from fuaran_ui.schema import types as t
from fuaran_ui.schema.encode import encode_node
from fuaran_ui.ui import format as cell_format


def _cell(payload: dict[str, object]) -> Obj:
    node = decode_node(
        json.dumps(
            {
                "id": "m",
                "kind": {"$type": "Metric", "format": payload, "label": "", "value": {"$type": "Static", "value": 0}},
            }
        )
    )
    assert node.ok, node
    fmt = node.value.kind.fields["format"]
    assert isinstance(fmt, Obj)
    return fmt


def test_a_present_decimals_sets_the_locale_free_currency_digits() -> None:
    assert format_number(_cell({"$type": "Currency", "code": "GBP", "decimals": 0}), 412380.4) == "GBP 412380"
    assert format_number(_cell({"$type": "Currency", "code": "USD", "decimals": 3}), 1.5) == "USD 1.500"
    assert format_number(_cell({"$type": "Currency", "code": "GBP"}), 412380) == "GBP 412380.00"


class _Recording:
    """A locale-aware formatter stand-in that records what the host handed it."""

    honours_locale = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, str | None]] = []

    def number(self, value: float, decimals: int | None, locale: str | None) -> str:
        self.calls.append(("Number", decimals, locale))
        return "n"

    def currency(self, value: float, code: str, locale: str | None, decimals: int | None = None) -> str:
        self.calls.append(("Currency", decimals, locale))
        return "c"

    def percent(self, value: float, decimals: int | None, locale: str | None) -> str:
        self.calls.append(("Percent", decimals, locale))
        return "p"

    def date_time(
        self, epoch_seconds: float, date_style: str | None, time_style: str | None, locale: str | None
    ) -> str:
        return "d"


class _PreDecimals:
    """A formatter written before Phase 2499: its ``currency`` takes no ``decimals``."""

    honours_locale = True

    def number(self, value: float, decimals: int | None, locale: str | None) -> str:
        return "n"

    def currency(self, value: float, code: str, locale: str | None) -> str:
        return f"{code}@{locale}"

    def percent(self, value: float, decimals: int | None, locale: str | None) -> str:
        return "p"

    def date_time(
        self, epoch_seconds: float, date_style: str | None, time_style: str | None, locale: str | None
    ) -> str:
        return "d"


def test_every_located_figure_case_hands_its_decimals_to_the_formatter() -> None:
    rec = _Recording()
    sources = BindingSources(formatter=rec)
    en_gb = {"$type": "Explicit", "tag": "en-GB"}
    format_number(_cell({"$type": "Currency", "code": "GBP", "decimals": 0, "locale": en_gb}), 412380, sources)
    format_number(_cell({"$type": "Percent", "decimals": 1, "locale": en_gb}), 0.0425, sources)
    format_number(_cell({"$type": "Number", "decimals": 0, "locale": en_gb}), 1234567.6, sources)
    assert rec.calls == [("Currency", 0, "en-GB"), ("Percent", 1, "en-GB"), ("Number", 0, "en-GB")]


def test_a_formatter_without_the_member_still_renders_every_document_that_omits_it() -> None:
    sources = BindingSources(formatter=_PreDecimals())
    located = _cell({"$type": "Currency", "code": "GBP", "locale": {"$type": "Explicit", "tag": "en-GB"}})
    assert format_number(located, 412380, sources) == "GBP@en-GB"


def test_binding_format_currency_hands_its_decimals_to_the_formatter() -> None:
    rec = _Recording()
    text = resolve_scalar_text(_format_binding(t.FmtCurrency("GBP", 0)), BindingSources(formatter=rec))
    assert text == "c"
    assert rec.calls == [("Currency", 0, "en-GB")]


def _format_binding(fmt: t.FmtCurrency) -> object:
    payload = {
        "id": "f",
        "kind": {
            "$type": "Fact",
            "label": "",
            "value": {
                "$type": "Bound",
                "binding": {
                    "$type": "Format",
                    "format": json.loads(json.dumps(_wire(fmt))),
                    "locale": {"$type": "Explicit", "tag": "en-GB"},
                    "source": {"$type": "Static", "value": 412380},
                },
            },
        },
    }
    node = decode_node(json.dumps(payload))
    assert node.ok, node
    value = node.value.kind.fields["value"]
    assert isinstance(value, Obj)
    return value.fields["binding"]


def _wire(fmt: t.FmtCurrency) -> dict[str, object]:
    obj = fmt.to_wire()
    assert isinstance(obj, Obj)
    return {"$type": obj.tag, **obj.fields}


def test_the_authoring_surface_spells_the_member() -> None:
    assert _wire(t.FmtCurrency("GBP", 0)) == {"$type": "Currency", "isoCode": "GBP", "decimals": 0}
    wire = cell_format.currency("GBP", t.Explicit("en-GB"), 0).to_wire()
    assert isinstance(wire, Obj)
    assert wire.fields["decimals"] == 0


def test_the_corpus_fixture_round_trips_and_renders() -> None:
    raw = (SNAPSHOT_ROOT / "nodes" / "figure-decimals-en-gb.json").read_text(encoding="utf-8")
    node = decode_node(raw)
    assert node.ok, node
    assert encode_node(node.value) == raw
    html = render_html(node.value)
    assert "GBP 412380" in html
    assert "1234568" in html
