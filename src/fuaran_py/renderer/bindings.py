"""Text-source, binding, and number-format resolution for the server renderer.

The decoded tree is *structural* — bindings and text sources survive decode as
``Obj`` discriminated objects rather than the rich F# union. The baseline
server renderer resolves what it can statically (the ``Static`` binding, the
``Literal`` text source) and falls back to the same placeholders the F# SSR
renderer uses for an unresolved binding (an em-dash ``—``). A host can supply a
``sources`` map (binding-key → value) to resolve ``Query`` / ``State`` bindings,
mirroring the F# ``BindingResolver.BindingSources`` seam.
"""

from __future__ import annotations

import math

from ..model import Obj, Value

# A host-supplied binding source: maps a binding key (e.g. a Query name) to a
# resolved value. Empty by default — the headless baseline resolves `Static`
# bindings with no host input.
type BindingSources = dict[str, object]


def render_text(text: Value, sources: BindingSources | None = None) -> str:
    """Resolve a decoded text-source to a plain (un-escaped) string.

    ``Literal`` → its text; ``Bound`` → the resolved source or ``""``; ``I18n``
    → ``[i18n:key]`` when the catalogue lacks the key. The caller escapes the
    result on the way into HTML (see :func:`fuaran_py.renderer.html.escape_text`).
    """
    if isinstance(text, str):
        return text
    if isinstance(text, Obj):
        if text.tag == "Literal":
            value = text.fields.get("text", "")
            return value if isinstance(value, str) else str(value)
        if text.tag == "Bound":
            resolved = resolve_binding(text.fields.get("binding"), sources)
            return str(resolved) if resolved is not None else ""
        if text.tag == "I18n":
            key = text.fields.get("key", "")
            return f"[i18n:{key}]"
    return ""


def resolve_binding(binding: Value, sources: BindingSources | None = None) -> object | None:
    """Resolve a decoded binding to its value, or ``None`` if not resolvable.

    ``Static`` → its embedded value; ``Query`` / ``State`` / … → the host
    ``sources`` map when a ``key`` is present, else ``None`` (the F# SSR
    "NotResolved" branch).
    """
    if isinstance(binding, Obj):
        if binding.tag == "Static":
            return binding.fields.get("value")
        # `State` keys on `key`; `Query` / `Filter` key on `name` (0.2.0 —
        # the accessor sentinel is off the wire, the name IS the lookup key).
        key = binding.fields.get("key")
        if key is None:
            key = binding.fields.get("name")
        if sources and isinstance(key, str) and key in sources:
            return sources[key]
    return None


def _plain_number(value: float) -> str:
    """Mirror F# ``string (value: float)``: integral floats print without ``.0``."""
    if isinstance(value, bool):  # defensive: bool is an int subclass
        return str(value)
    if isinstance(value, int):
        return str(value)
    if math.isfinite(value) and value == math.floor(value):
        return str(int(value))
    return repr(value)


def format_number(fmt: Value, value: object) -> str:
    """Format a numeric value through a decoded ``CellFormat`` (mirrors F# ``formatNumber``)."""
    try:
        num = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return str(value)

    if not isinstance(fmt, Obj):
        return _plain_number(num)
    tag = fmt.tag
    fields = fmt.fields

    if tag == "None" or tag is None:
        return _plain_number(num)
    if tag == "Number":
        decimals = fields.get("decimals")
        if isinstance(decimals, int):
            return f"{num:.{decimals}f}"
        return _plain_number(num)
    if tag == "Currency":
        code = fields.get("code", "")
        return f"{code} {num:.2f}"
    if tag == "Percent":
        decimals = fields.get("decimals")
        places = decimals if isinstance(decimals, int) else 1
        return f"{num * 100:.{places}f}%"
    if tag == "SignificantDigits":
        digits = fields.get("digits")
        return f"{num:.{digits}g}" if isinstance(digits, int) else _plain_number(num)
    # Date / Custom: structural — fall back to the plain numeric form.
    return _plain_number(num)
