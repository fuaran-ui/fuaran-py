"""Phase 285 — the Python capability host-registration seam (the §3 unblocked half).

The capability/invoke *wire* shape is gated on the F# Capability/Invoke phase; until it
ships, this exercises the wire-independent registration + default-deny-by-shape
validation that a Python host (server / Pyodide island) uses to resolve an invocation.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT, corpus_required
from fuaran_py.ui import encode, invoke, node
from fuaran_py.ui import fuaran as F
from fuaran_py.ui.capability import (
    CapabilityRegistry,
    InvokeError,
    any_string,
    capability,
    enum,
    int_range,
    parse_invocation,
)


def test_register_and_invoke() -> None:
    reg = CapabilityRegistry()
    reg.register(capability("scale", lambda a: a["x"] * 10, holes=[("x", int_range(0, 100))]))
    assert reg.invoke("scale", {"x": 5}) == 50
    assert reg.ids() == ["scale"]


def test_default_deny_out_of_space() -> None:
    reg = CapabilityRegistry()
    reg.register(capability("scale", lambda a: a["x"], holes=[("x", int_range(0, 10))]))
    with pytest.raises(InvokeError):
        reg.invoke("scale", {"x": 999})  # outside the declared space


def test_default_deny_unknown_arg_and_missing() -> None:
    reg = CapabilityRegistry()
    reg.register(capability("pick", lambda a: a["choice"], holes=[("choice", enum("a", "b"))]))
    with pytest.raises(InvokeError):
        reg.invoke("pick", {"choice": "a", "extra": 1})  # arg addresses no declared hole
    with pytest.raises(InvokeError):
        reg.invoke("pick", {})  # missing required arg
    with pytest.raises(InvokeError):
        reg.invoke("pick", {"choice": "z"})  # not an enum member


def test_unknown_capability_and_double_register() -> None:
    reg = CapabilityRegistry()
    with pytest.raises(InvokeError):
        reg.invoke("missing", {})
    reg.register(capability("c", lambda a: 1))
    with pytest.raises(InvokeError):
        reg.register(capability("c", lambda a: 2))


# ── The Invoke wire (Binding.Invoke / Action.Invoke) ─────────────────────────


@corpus_required
def test_authored_metric_invoke_matches_corpus() -> None:
    """``Binding.Invoke`` as a Metric source, authored end-to-end, matches the fixture."""
    from fuaran_py.ui import format

    expected = (CORPUS_ROOT / "nodes" / "metric-invoke.json").read_text(encoding="utf-8").rstrip("\n")
    metric = node.bare(
        F.metric(
            "metric-invoke",
            label="Revenue",
            value=invoke("forecast.revenue", horizon="12", scenario="base"),
            format=format.currency("GBP"),
            tone="Brand",
            icon="trending-up",
            subtext="vs last month",
        )
    )
    assert encode(metric) == expected


@corpus_required
def test_authored_button_invoke_matches_corpus() -> None:
    """``Action.Invoke`` as a Button onClick, authored end-to-end, matches the fixture."""
    expected = (CORPUS_ROOT / "nodes" / "btn-invoke.json").read_text(encoding="utf-8").rstrip("\n")
    btn = node.bare(
        F.button("btn-invoke", label="Run model", on_click=invoke("model.score", rows="all"), variant="Primary")
    )
    assert encode(btn) == expected


def test_invoke_wire_round_trips_and_dispatches() -> None:
    """``invoke(...)`` → wire → ``parse_invocation`` → registry dispatch (the host bridge)."""
    inv = invoke("forecast.revenue", horizon="12", scenario="base")
    parsed = json.loads(inv.to_json())
    decoded = parse_invocation(parsed)
    assert decoded.capability_id == "forecast.revenue"
    assert decoded.args == {"horizon": "12", "scenario": "base"}

    reg = CapabilityRegistry()
    reg.register(
        capability(
            "forecast.revenue",
            lambda a: f"{a['scenario']}@{a['horizon']}",
            holes=[("horizon", any_string()), ("scenario", any_string())],
        )
    )
    assert reg.invoke_wire(parsed) == "base@12"


def test_parse_invocation_rejects_malformed() -> None:
    with pytest.raises(InvokeError):
        parse_invocation({"$type": "NotInvoke"})
    with pytest.raises(InvokeError):
        parse_invocation({"$type": "Invoke", "capabilityId": "c", "args": [{"addr": "x"}]})
