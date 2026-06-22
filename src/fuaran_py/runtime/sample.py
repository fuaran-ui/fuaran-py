"""A minimal interactive sample — author a tree, mount it, handle one dispatch.

``counter_runtime`` authors a tiny app with the Phase 278 surface (a metric + an
increment button), wires the button's click to a ``ReplaceBinding`` that bumps the
metric's value, and returns a ready-to-:meth:`~fuaran_py.runtime.FuaranRuntime.mount`
runtime. Under Pyodide:

    from fuaran_py.runtime import counter_runtime
    counter_runtime().mount("fuaran-root")    # clicking "+1" re-renders the count

and in tests it is driven against an injected fake DOM. It is the end-to-end
"author **and run** a Fuaran UI in Python" demonstration.
"""

from __future__ import annotations

from .. import decode_node
from ..model import Node, Obj
from ..result import Ok
from ..ui import encode, fuaran
from .runtime import BrowserDeps, FuaranRuntime

_COUNT_ID = "count"
_INC_ID = "inc"


def counter_tree() -> Node:
    """The authored counter app: a stack of a value metric + an increment button."""
    app = fuaran.stack(
        "counter-root",
        children=[
            fuaran.metric(_COUNT_ID, label="Count", value=0),
            fuaran.button(_INC_ID, label="+1", variant="Primary"),
        ],
    )
    decoded = decode_node(encode(app))
    assert isinstance(decoded, Ok), decoded
    return decoded.value


def counter_runtime(deps: BrowserDeps | None = None) -> FuaranRuntime:
    """A runtime whose ``+1`` button increments the displayed count via a tree-op."""
    state = {"n": 0}

    def on_event(node_id: str, event: str) -> Obj | None:
        if node_id == _INC_ID and event == "click":
            state["n"] += 1
            return Obj(
                "ReplaceBinding",
                {"binding": Obj("Static", {"value": state["n"]}), "slot": "Source", "target": _COUNT_ID},
            )
        return None

    return FuaranRuntime(counter_tree(), on_event=on_event, deps=deps)


__all__ = ["counter_tree", "counter_runtime"]
