"""Text-source, binding, and number-format resolution for the server renderer.

The decoded tree is *structural* — bindings and text sources survive decode as
``Obj`` discriminated objects rather than the rich F# union. The baseline
server renderer resolves what it can statically (the ``Static`` binding, the
``Literal`` text source) and falls back to the same placeholders the F# SSR
renderer uses for an unresolved binding (an em-dash ``—``). A host can supply a
``sources`` map (binding-key → value) to resolve ``Query`` / ``State`` bindings,
mirroring the F# ``BindingResolver.BindingSources`` seam.

Render-time compute (Phase 648, mirroring F# Phase 647 / fuaran-ts ea16811): a
``Bound`` ``Transform`` binding is resolved through the corpus-certified compute
evaluator (:mod:`fuaran_py.compute`). A **row context** (a data-bearing node's
``source`` slot) resolves to the transformed rows; a **scalar slot** (a
``TextSource.Bound``, a Metric / LabelValueRow value/trend) resolves to the lone
cell of an exactly-1×1 pipeline result (the Phase 632 scalar law — loud on
ambiguity, ``0`` for a trailing global ``count`` over an empty frame). A
``Selection`` / ``Filter`` binding with no host value resolves to its declared
``defaultValue`` (Phase 629), so preselected master-detail renders resolved.
The evaluator itself is untouched — this module is render wiring over it.
"""

from __future__ import annotations

import math
from typing import cast

from ..compute import ComputeErr, ComputeOk, evaluate_transform
from ..model import Arr, Obj, Value

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
            # Phase 632/648 — a text slot resolves through the scalar path, so a
            # `Bound` `Transform` yields its 1×1 result cell (never the rows
            # list); any other binding resolves exactly as before. Same dispatch
            # as the client renderer (both surfaces share `render_html`).
            resolved = resolve_scalar_text(text.fields.get("binding"), sources)
            return resolved if resolved is not None else ""
        if text.tag == "I18n":
            key = text.fields.get("key", "")
            return f"[i18n:{key}]"
    return ""


def resolve_binding(binding: Value, sources: BindingSources | None = None) -> object | None:
    """Resolve a decoded binding to its value, or ``None`` if not resolvable.

    ``Static`` → its embedded value; ``State`` / ``Query`` / ``Filter`` /
    ``Selection`` → the host ``sources`` map when the binding's identity key is
    present. When it is absent, a declared ``defaultValue`` resolves (Phase 629
    for ``Selection``, the 0.2.0 pre-selected-filter gap for ``Filter``, the
    always-present ``State`` default) — matching the F#/TS ``BindingResolver``.
    Otherwise ``None`` (the F# SSR "NotResolved" branch).
    """
    if isinstance(binding, Obj):
        if binding.tag == "Static":
            return binding.fields.get("value")
        # `State` keys on `key`; `Query` / `Filter` key on `name`; `Selection`
        # keys on `nodeId` (0.2.0 — the accessor sentinel is off the wire, the
        # name/id IS the lookup key).
        key = binding.fields.get("key")
        if key is None:
            key = binding.fields.get("name")
        if key is None:
            key = binding.fields.get("nodeId")
        if sources and isinstance(key, str) and key in sources:
            return sources[key]
        # Phase 629 — an unwritten `Selection` / `Filter` (or a `State` with no
        # host value) resolves to its declared default: resolution-time
        # defaulting IS the preselected mechanism, no store seeding.
        if "defaultValue" in binding.fields:
            return binding.fields.get("defaultValue")
    return None


def resolve_display_string(binding: Value, sources: BindingSources | None = None) -> str | None:
    """Resolve a binding to its display-string form, or ``None``.

    The twin of the Rust host's ``try_string``: :func:`resolve_binding` for the
    value, then the display form — strings as-is, numbers in the deterministic
    canonical layout, bools as ``true`` / ``false``. A structured value has no
    display form and yields ``None``, so a caller never renders a container into
    a text position.
    """
    resolved = resolve_binding(binding, sources)
    if resolved is None:
        return None
    if isinstance(resolved, (str, bool, int, float)):
        return _cell_value_to_text(resolved)
    return None


# ── Render-time compute resolution (Phase 648) ───────────────────────────────
#
# A `Bound` `Transform` binding is resolved through the corpus-certified
# `fuaran_py.compute` evaluator. Two entry points, mirroring the TS/F# split:
# `resolve_source` (row context → the transformed rows) and
# `resolve_scalar_text` / `resolve_scalar_number` (scalar slot → the lone cell
# of an exactly-1×1 result — the Phase 632 law). The flat `sources` map doubles
# as the compute-parameter state store (a `Filter` / `State` param reads it by
# name/key), exactly as the interactive runtime passes it.


def _is_transform(binding: Value) -> bool:
    return isinstance(binding, Obj) and binding.tag == "Transform"


def _transform_state(sources: BindingSources | None) -> dict[str, object]:
    return dict(sources) if sources else {}


def resolve_source(source: Value, sources: BindingSources | None = None) -> object | None:
    """Resolve a data-bearing node's ``source`` slot to a row collection.

    A ``Transform`` binding evaluates through the certified compute evaluator to
    an ``Arr`` of row objects (one ``Obj`` per row, column-keyed); an evaluation
    failure resolves to ``None`` (the caller's empty / placeholder path). Any
    other binding falls back to :func:`resolve_binding` (e.g. a ``Static`` row
    list). This is the **row context** — never the 1×1 scalar law.
    """
    if _is_transform(source):
        assert isinstance(source, Obj)
        result = evaluate_transform(source, _transform_state(sources))
        if isinstance(result, ComputeOk):
            # `rows_of` boxes each cell to a scalar wire value (null → None), so
            # a row dict is a `dict[str, Value]` — the shape `Obj.fields` wants.
            rows: list[Value] = [Obj(None, {k: cast("Value", v) for k, v in row.items()}) for row in result.rows]
            return Arr(rows)
        return None
    return resolve_binding(source, sources)


def _cell_value_to_text(value: object) -> str:
    """Coerce a resolved scalar cell value to a text-slot string (mirrors F#
    ``cellToText``): ``bool`` → ``true`` / ``false``; a number formats
    invariantly (integral floats without ``.0``); a string / date passes through."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _plain_number(value)
    return str(value)


def _cell_value_to_float(value: object) -> float | None:
    """Coerce a resolved scalar cell value to a numeric-slot float, or ``None``
    for a non-numeric cell (a text / bool / date cell in a numeric slot is a loud
    miss, not a silent zero) — mirrors F# ``cellToFloat``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _trailing_global_count(transform: Obj) -> bool:
    """True when the pipeline ends in a global single-``count`` ``groupBy`` (keys
    ``[]``, one ``count`` agg) — the terminal whose empty-frame result the host
    completes to ``0`` ("the count of nothing is 0")."""
    pipeline = transform.fields.get("pipeline")
    if not isinstance(pipeline, Arr) or not pipeline.items:
        return False
    last = pipeline.items[-1]
    if not isinstance(last, Obj) or last.tag != "groupBy":
        return False
    keys = last.fields.get("keys")
    if not isinstance(keys, Arr) or keys.items:
        return False
    aggs = last.fields.get("aggs")
    if not isinstance(aggs, Arr) or len(aggs.items) != 1:
        return False
    agg = aggs.items[0]
    return isinstance(agg, Obj) and agg.fields.get("fn") == "count"


# The scalar-slot resolution outcome: ``("resolved", value)`` (a single non-null
# cell, or ``0`` for the trailing-count completion), ``("empty", None)`` (an
# unresolved / empty slot — renders absence), or ``("error", None)`` (an
# ambiguous >1×1 result or a failed pipeline — loud, never a silent first cell).
_ScalarOutcome = tuple[str, object]


def _scalar_cell(transform: Obj, sources: BindingSources | None) -> _ScalarOutcome:
    result = evaluate_transform(transform, _transform_state(sources))
    if isinstance(result, ComputeErr):
        return ("error", None)
    rows = result.rows
    if len(rows) == 1:
        row = rows[0]
        if len(row) == 1:
            value = next(iter(row.values()))
            return ("empty", None) if value is None else ("resolved", value)
        return ("error", None)  # 1 row × >1 col — ambiguous
    if len(rows) == 0:
        if _trailing_global_count(transform):
            return ("resolved", 0)
        return ("empty", None)
    return ("error", None)  # >1 row — ambiguous


def resolve_scalar_text(binding: Value, sources: BindingSources | None = None) -> str | None:
    """Resolve a binding in a **text scalar slot** to a plain string, or ``None``
    when unresolved / ambiguous (the caller renders ``""``). A ``Transform``
    resolves to its 1×1 result cell; every other binding resolves as
    :func:`resolve_binding` then stringifies."""
    if _is_transform(binding):
        assert isinstance(binding, Obj)
        tag, value = _scalar_cell(binding, sources)
        return _cell_value_to_text(value) if tag == "resolved" else None
    resolved = resolve_binding(binding, sources)
    return str(resolved) if resolved is not None else None


def resolve_scalar_number(binding: Value, sources: BindingSources | None = None) -> float | None:
    """Resolve a binding in a **numeric scalar slot** to a float, or ``None`` when
    unresolved / ambiguous / non-numeric (the caller renders the em-dash). A
    ``Transform`` resolves to its 1×1 result cell (coerced numerically); every
    other binding resolves as :func:`resolve_binding` then coerces."""
    if _is_transform(binding):
        assert isinstance(binding, Obj)
        tag, value = _scalar_cell(binding, sources)
        return _cell_value_to_float(value) if tag == "resolved" else None
    resolved = resolve_binding(binding, sources)
    if isinstance(resolved, bool):
        return None
    if isinstance(resolved, (int, float)):
        return float(resolved)
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


# ── Duration / relative-time rendering (Phase 819) ──────────────────────────
#
# Mirrors the F# ``Formatting.formatDuration`` / ``formatRelativeEnglish``
# exactly (shared hand-rolled implementations — a duration is deliberately
# LOCALE-INDEPENDENT, and the cell vocabulary has no locale dimension, so the
# English relative form IS the canonical cell rendering). Rounding is
# half-to-even — CPython's built-in ``round`` matches .NET ``round``.

_DURATION_UNIT_SECONDS = {"Seconds": 1.0, "Minutes": 60.0, "Hours": 3600.0}


def format_duration(unit: str, style: str, value: float) -> str:
    """Render ``value`` (a signed count of ``unit``s) per the bounded ``DurationStyle``."""
    total_seconds = value * _DURATION_UNIT_SECONDS.get(unit, 1.0)
    total = int(round(abs(total_seconds)))  # half-to-even, matching .NET `round`
    sign = "-" if total_seconds < 0.0 and total > 0 else ""
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60
    if style == "Clock":
        # "h:mm:ss" from one hour up, "m:ss" below it.
        body = f"{hours}:{minutes:02d}:{seconds:02d}" if hours >= 1 else f"{minutes}:{seconds:02d}"
    elif style == "Long":
        # English words, singular/plural, zero components omitted; zero -> "0 minutes".
        def part(n: int, word: str) -> str | None:
            return None if n == 0 else (f"1 {word}" if n == 1 else f"{n} {word}s")

        parts = [p for p in (part(hours, "hour"), part(minutes, "minute"), part(seconds, "second")) if p is not None]
        body = " ".join(parts) if parts else "0 minutes"
    else:  # Compact
        # Largest two grains, zero tails omitted: "1h 20m" / "2h" / "5m 30s" /
        # "42s"; zero -> "0s".
        if hours >= 1:
            body = f"{hours}h {minutes}m" if minutes > 0 else f"{hours}h"
        elif minutes >= 1:
            body = f"{minutes}m {seconds}s" if seconds > 0 else f"{minutes}m"
        else:
            body = f"{seconds}s"
    return sign + body


def format_relative_english(unit: str, value: float) -> str:
    """English relative-time rendering over a signed count of ``unit`` — "in 2
    hours" / "3 minutes ago" / "this minute" (Phase 819)."""
    n = int(round(value))  # half-to-even, matching .NET `round`
    unit_word = unit.lower()
    if n == 0:
        return f"this {unit_word}"
    magnitude = abs(n)
    plural = unit_word if magnitude == 1 else f"{unit_word}s"
    return f"{magnitude} {plural} ago" if n < 0 else f"in {magnitude} {plural}"


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
    if tag == "Duration":
        # Phase 819 — locale-independent by design (see format_duration above).
        return format_duration(str(fields.get("unit")), str(fields.get("style")), num)
    if tag == "RelativeTime":
        # Phase 819 — the English form IS the canonical cell rendering.
        return format_relative_english(str(fields.get("unit")), num)
    # Date / Custom: structural — fall back to the plain numeric form.
    return _plain_number(num)
