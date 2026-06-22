"""``fuaran_py.runtime`` — the interactive client runtime (Pyodide CSR).

The in-browser dispatch→apply→re-render loop that makes Python a co-equal
*interactive* host alongside the F# (Fable) and TypeScript (React) tiers, not only
an authoring + headless-render host. Mount a decoded tree, wire DOM events to a
host update function, fold the resulting ``TreeOp``\\ s through the apply engine,
re-render::

    from fuaran_py.runtime import counter_runtime
    counter_runtime().mount("fuaran-root")    # clicking "+1" re-renders the count

Browser-API access is behind the injectable :class:`BrowserDeps` seam (default: the
Pyodide ``js`` interop module), so the package stays stdlib-only and importable
under plain CPython; tests inject a fake DOM.
"""

from __future__ import annotations

from .runtime import BrowserDeps, EventHandler, FuaranRuntime
from .sample import counter_runtime, counter_tree

__all__ = ["FuaranRuntime", "BrowserDeps", "EventHandler", "counter_runtime", "counter_tree"]
