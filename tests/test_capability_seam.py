"""Phase 285 — the Python capability host-registration seam (the §3 unblocked half).

The capability/invoke *wire* shape is gated on the F# Capability/Invoke phase; until it
ships, this exercises the wire-independent registration + default-deny-by-shape
validation that a Python host (server / Pyodide island) uses to resolve an invocation.
"""

from __future__ import annotations

import pytest

from fuaran_py.ui.capability import (
    CapabilityRegistry,
    InvokeError,
    capability,
    enum,
    int_range,
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
