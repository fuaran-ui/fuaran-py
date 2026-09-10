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
evaluator (:mod:`fuaran_ui.compute`). A **row context** (a data-bearing node's
``source`` slot) resolves to the transformed rows; a **scalar slot** (a
``TextSource.Bound``, a Metric / LabelValueRow value/trend) resolves to the lone
cell of an exactly-1×1 pipeline result (the Phase 632 scalar law — loud on
ambiguity, ``0`` for a trailing global ``count`` over an empty frame). A
``Selection`` / ``Filter`` binding with no host value resolves to its declared
``defaultValue`` (Phase 629), so preselected master-detail renders resolved.
The evaluator itself is untouched — this module is render wiring over it.

**The seam has an ERROR channel (Phase 1667).** Resolution answers a value,
ABSENCE (``None`` — the slot's empty state), or an ERROR: the document asked for
something no decoded tree can answer, and there is no value that could stand in
without being read as an answer. Python's error channel is an exception, so the
error is raised rather than returned — see :exc:`WireSurvivabilityError`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import cast

from ..compute import ComputeErr, ComputeOk, evaluate_transform
from ..model import Arr, Obj, Value


@dataclass(frozen=True)
class BindingSources:
    """What the host furnishes a render pass (Phase 1663).

    Three members, and the reference host's shape with its four identity-keyed
    maps collapsed into the one this host carries:

    * ``values`` — the identity-keyed map that WAS this whole type: a ``State``
      key, a ``Query`` or ``Filter`` name, a ``Selection`` nodeId → the host's
      resolved value. It doubles as the compute-parameter store, exactly as
      before.
    * ``now`` — the host instant as an **ISO-8601 UTC** string
      (``2026-08-02T06:59:24Z``), for ``Binding.Now`` and ``Format.Since``.
    * ``locale`` — the ambient BCP-47 tag a ``LocaleSource.Ambient`` reads.

    **The clock lives HERE, never on the wire and never read during
    resolution.** That is what keeps a tree a pure value: a replayed op-stream
    re-supplies the instant it recorded, so a replay reproduces the original
    render instead of drifting to whatever "now" means at replay time. Resolve
    it ONCE per render pass and hold it for the whole pass, or two ``Now`` slots
    in one tree can disagree.

    ``now = ""`` is the identity default and means **this host furnishes no
    clock**: the slot resolves to ABSENCE, which a text slot renders as the
    empty string and a numeric slot as the em-dash. Deliberately loud — a host
    that forgets the instant must not silently render a plausible wrong date,
    and a relative time computed against an invented "now" is worse than a
    visible blank. It is NOT the :exc:`WireSurvivabilityError` channel: the
    document is answerable, the host simply furnished nothing, which is the same
    fact as an unwritten ``Query``.

    ``locale = ""`` is the identity default meaning "the runtime default
    locale". Every rendering this host produces for the locale-independent
    ``Format`` cases (``Since`` / ``RelativeTime`` / ``Duration``) ignores the
    tag by design — those are unit glyphs and English words, not CLDR-driven
    forms — so the member exists for the cases a locale-aware host renders and
    this one declines (see :func:`_format_projection`).
    """

    values: Mapping[str, object] = field(default_factory=dict)
    now: str = ""
    locale: str = ""

    def merged_with(self, extra: Mapping[str, object]) -> BindingSources:
        """This record with ``extra`` layered over ``values``; the two scalars ride along."""
        return replace(self, values={**self.values, **extra})


#: What a caller may PASS where binding sources are wanted. A bare mapping is
#: accepted and normalised to ``BindingSources(values=mapping)`` — a boundary
#: coercion rather than a second design, because a bare mapping can mean nothing
#: else and this host is published. Every function below normalises through
#: :func:`as_sources` on entry, so nothing downstream sees the union.
type BindingSourcesLike = BindingSources | Mapping[str, object]


def as_sources(sources: BindingSourcesLike | None) -> BindingSources:
    """Normalise a caller's argument to a :class:`BindingSources`.

    ``None`` is the headless baseline (no values, no clock, no locale) — the
    same thing it has always meant here.
    """
    if sources is None:
        return BindingSources()
    if isinstance(sources, BindingSources):
        return sources
    return BindingSources(values=sources)


# ── The host instant: grain truncation, epoch conversion, the Since reduction ─
#
# Phase 1663 — the three shared reductions the reference host keeps above its
# own pipeline split, transcribed. All three are pure functions of their
# arguments and consult no clock: the instant is always the caller's.

#: ``(prefix length, suffix)`` per ``TimeGrain``. ``Second`` is the identity and
#: is absent by construction — a grain the map does not carry truncates nothing,
#: which is the right answer for the identity and for a token this host does not
#: recognise.
_GRAIN_TRUNCATION: dict[str, tuple[int, str]] = {
    "Minute": (16, ":00Z"),
    "Hour": (13, ":00:00Z"),
    "Day": (10, ""),
}


def truncate_to_grain(grain: str, instant: str) -> str:
    """``instant`` truncated to ``grain`` (``Second`` / ``Minute`` / ``Hour`` / ``Day``).

    An instant too short to slice is returned as it came: a partial instant is
    already at or below the requested grain, and inventing digits to fill it
    would be a wrong answer rather than a coarse one.
    """
    slicing = _GRAIN_TRUNCATION.get(grain)
    if slicing is None:
        return instant
    length, suffix = slicing
    return instant[:length] + suffix if len(instant) >= length else instant


def epoch_seconds_of_instant(instant: str) -> float | None:
    """Whole Unix-epoch seconds for a canonical instant, or ``None``.

    Deliberately tolerant of what follows the seconds (a fractional part, a
    ``Z``, an offset suffix) and deliberately INTOLERANT of a missing or
    non-numeric date: truncating to a grain leaves ``YYYY-MM-DDTHH:MM:00Z`` and
    ``YYYY-MM-DD``, both of which must parse, and anything shorter is not an
    instant at all. ``None`` is surfaced as unresolved by the caller, never as
    an invented instant.

    Proleptic-Gregorian integer arithmetic (Howard Hinnant's
    ``days_from_civil``), not :mod:`datetime`, so this host and every other
    agree to the second on dates outside the platform's calendar range.
    """

    def digits(start: int, length: int) -> int | None:
        if len(instant) < start + length:
            return None
        chunk = instant[start : start + length]
        return int(chunk) if chunk.isdigit() and chunk.isascii() else None

    year, month, day = digits(0, 4), digits(5, 2), digits(8, 2)
    if year is None or month is None or day is None:
        return None
    if year < 1 or not (1 <= month <= 12) or not (1 <= day <= 31):
        return None
    hours = digits(11, 2) or 0
    minutes = digits(14, 2) or 0
    seconds = digits(17, 2) or 0
    y = year - 1 if month <= 2 else year
    era = (y if y >= 0 else y - 399) // 400
    yoe = y - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    days = era * 146097 + doe - 719468
    return float(days) * 86400.0 + float(hours * 3600 + minutes * 60 + seconds)


#: Seconds in one ``RelativeTimeUnit``. ``Month`` and ``Year`` are the mean
#: Gregorian lengths — FIXED constants rather than calendar arithmetic, because
#: "2 months ago" is a rounded human phrase and a calendar-exact answer would
#: make the same delta read differently depending on which months it spanned, on
#: hosts that must agree to the byte.
_RELATIVE_UNIT_SECONDS: dict[str, float] = {
    "Second": 1.0,
    "Minute": 60.0,
    "Hour": 3600.0,
    "Day": 86400.0,
    "Week": 604800.0,
    "Month": 2629746.0,
    "Year": 31556952.0,
}

#: The auto-selection ladder: the largest unit whose length does not exceed the
#: magnitude of the delta. Ordered coarsest-threshold-last.
_SINCE_LADDER: tuple[tuple[float, str], ...] = (
    (60.0, "Second"),
    (3600.0, "Minute"),
    (86400.0, "Hour"),
    (604800.0, "Day"),
    (2629746.0, "Week"),
    (31556952.0, "Month"),
)


def since_unit_and_count(declared: str | None, delta_seconds: float) -> tuple[str, float]:
    """The ``Format.Since`` reduction: a signed second-delta → ``(unit, count)``.

    ``declared is None`` is the AUTO-SELECTION request (not a default): the unit
    is the largest whose length does not exceed the magnitude, from the fixed
    ladder above. The count TRUNCATES toward zero rather than rounding, so 3599
    seconds is "59 minutes" and never "1 hour" — the ladder and the count then
    agree at every boundary, which rounding would break exactly at the point a
    reader is most likely to check.
    """
    unit = declared
    if unit is None:
        magnitude = abs(delta_seconds)
        unit = "Year"
        for threshold, candidate in _SINCE_LADDER:
            if magnitude < threshold:
                unit = candidate
                break
    return unit, float(int(delta_seconds / _RELATIVE_UNIT_SECONDS.get(unit, 1.0)))


class WireSurvivabilityError(Exception):
    """A DECODED host-only projection a reader tried to run (Phase 1667).

    It exists because the alternative — answering absence — is indistinguishable
    from a real answer at the slot: an unresolved ``Query`` and a
    ``Binding.Computed`` whose whole payload erased on the wire both used to
    resolve ``None`` here, and the second is not a slot that has no value yet, it
    is a slot that can never have one.

    Raised BY the resolution seam and caught by nobody in this package except
    the two predicate seams whose own rule is to render anyway
    (:func:`is_node_visible`, :func:`select_switch_case`). So a headless caller
    asking the seam sees the error, and a caller of :func:`render_html` sees it
    propagate rather than receiving a page with a plausible hole in it.
    """


# The one message a decoded ``Binding.Computed`` carries — byte-identical to the
# reference host's, so every host renders the same sentence and a test can pin
# it. A constant rather than a literal at the raise site for exactly that
# reason.
DECODED_COMPUTED_MESSAGE = (
    "Binding.Computed has no wire projection (decoded from a '<closure>' sentinel)"
    " — use Binding.Expr / Transform / State"
)


def render_text(text: Value, sources: BindingSourcesLike | None = None) -> str:
    """Resolve a decoded text-source to a plain (un-escaped) string.

    ``Literal`` → its text; ``Bound`` → the resolved source or ``""``; ``I18n``
    → ``[i18n:key]`` when the catalogue lacks the key. The caller escapes the
    result on the way into HTML (see :func:`fuaran_ui.renderer.html.escape_text`).
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


def resolve_binding(binding: Value, sources: BindingSourcesLike | None = None) -> object | None:
    """Resolve a decoded binding to its value, or ``None`` if not resolvable.

    ``Static`` → its embedded value; ``State`` / ``Query`` / ``Filter`` /
    ``Selection`` → the host ``sources`` map when the binding's identity key is
    present. When it is absent, a declared ``defaultValue`` resolves (Phase 629
    for ``Selection``, the 0.2.0 pre-selected-filter gap for ``Filter``, the
    always-present ``State`` default) — matching the F#/TS ``BindingResolver``.
    Otherwise ``None`` (the F# SSR "NotResolved" branch).

    :raises WireSurvivabilityError: for a decoded ``Computed`` (Phase 1667) —
        the ERROR channel, which is a third outcome beside a value and absence.
    """
    if isinstance(binding, Obj):
        if binding.tag == "Static":
            return binding.fields.get("value")
        if binding.tag == "Expr":
            # Phase 1534 - the scalar expression in a slot with no coercion. A
            # null result is ABSENCE (``None``), which is what this function's
            # unresolved branch already means; a failed evaluation is the same
            # ``None`` here because this seam has no error channel - a text or
            # numeric slot goes through ``resolve_scalar_*`` above, which does.
            tag, value = _scalar_cell(_expr_as_transform(binding), sources)
            return value if tag == "resolved" else None
        if binding.tag == "Computed":
            # A decoded ``Computed`` has nothing to compute WITH: the case's whole
            # payload is a host closure and it crosses the wire as
            # ``"<closure>"``. WIRE_FORMAT §5 therefore says it resolves to an
            # ERROR naming its replacements, never to a value — and Phase 1667
            # gave this seam the channel to say so. This arm is explicit rather
            # than a fall-through so it stays that way: the case carries no
            # ``key`` / ``name`` / ``nodeId`` today, but a future member named
            # like one of those would otherwise turn a host-only computation into
            # a resolved value through the lookup below.
            #
            # It RAISES rather than returning a sentinel because ``None`` here
            # already means the slot has no value YET, and a slot that can never
            # have one is a different fact. An exception is Python's channel for
            # that distinction, and it is total in the only sense available: this
            # arm never answers.
            raise WireSurvivabilityError(DECODED_COMPUTED_MESSAGE)
        if binding.tag == "Now":
            # Phase 1663 — the host-furnished instant. The clock is NOT read
            # here: ``sources.now`` was resolved once, host-side, for the whole
            # render pass, which is what makes a replayed op-stream reproduce its
            # original render instead of drifting to replay-time "now".
            #
            # An unset instant is ABSENCE (``None``), so the slot renders its
            # empty surface. Deliberately loud — a host that forgets to furnish
            # the clock must not silently render a plausible wrong date.
            #
            # The declared GRAIN truncates the instant BEFORE anything projects
            # it. Before, not after: a ``Transform`` param projecting this into
            # ``dateDiffDays`` reads only the leading ``YYYY-MM-DD``, so the
            # truncation has to be upstream of every projection or it is not the
            # document's declaration at all. Absent grain is ``Second``, the
            # identity.
            instant = as_sources(sources).now
            if not instant:
                return None
            grain = binding.fields.get("grain")
            return truncate_to_grain(grain, instant) if isinstance(grain, str) else instant
        if binding.tag == "Format":
            # Phase 1663 — the locale-aware numeric projection. Only the
            # locale-INDEPENDENT cases render here; see ``_format_projection``
            # for which, and why the rest resolve to absence on this host.
            return _format_projection(binding, sources)
        # `State` keys on `key`; `Query` / `Filter` key on `name`; `Selection`
        # keys on `nodeId` (0.2.0 — the accessor sentinel is off the wire, the
        # name/id IS the lookup key).
        key = binding.fields.get("key")
        if key is None:
            key = binding.fields.get("name")
        if key is None:
            key = binding.fields.get("nodeId")
        values = as_sources(sources).values
        if isinstance(key, str) and key in values:
            return values[key]
        # Phase 629 — an unwritten `Selection` / `Filter` (or a `State` with no
        # host value) resolves to its declared default: resolution-time
        # defaulting IS the preselected mechanism, no store seeding.
        if "defaultValue" in binding.fields:
            return binding.fields.get("defaultValue")
    return None


def resolve_display_string(binding: Value, sources: BindingSourcesLike | None = None) -> str | None:
    """Resolve a binding in a DISPLAY (scalar) slot to its display-string form, or ``None``.

    The twin of the Rust host's ``try_scalar_string``: a ``Transform`` or an
    ``Expr`` resolves to its 1x1 result cell, and every other binding case goes
    through :func:`resolve_binding` then the display form — strings as-is,
    numbers in the deterministic canonical layout, bools as ``true`` / ``false``.
    A structured value has no display form and yields ``None``, so a caller never
    renders a container into a text position.

    **Phase 1665 added the scalar dispatch, and it is a defect fix rather than a
    widening.** A display slot is a scalar slot by definition, and this function's
    one caller is the accessibility trait's name slot (``_a11y_name``), which the
    wire specifies as an ordinary ``Binding[str]``. Routing it through
    :func:`resolve_binding` alone meant a ``Transform`` yielding the one cell an
    author obviously meant — "name this region after what is in it" — resolved to
    the rows list, which has no display form, so this host emitted no
    ``aria-label`` at all. The reference host and the erased host each got it
    wrong differently (a caught cast error; the rows array in the attribute), and
    on the one trait with no visible output none of the three was reported.

    The non-Transform path is deliberately NOT delegated to
    :func:`resolve_scalar_text`: that function stringifies with ``str()``, which
    spells a bool ``True``, where every host's display form spells it ``true``.
    The two coercions are different functions on purpose and this slot needs this
    one.
    """
    if _is_expr(binding) or _is_transform(binding):
        assert isinstance(binding, Obj)
        transform = _expr_as_transform(binding) if _is_expr(binding) else binding
        tag, value = _scalar_cell(transform, sources)
        return _cell_value_to_text(value) if tag == "resolved" else None
    resolved = resolve_binding(binding, sources)
    if resolved is None:
        return None
    if isinstance(resolved, (str, bool, int, float)):
        return _cell_value_to_text(resolved)
    return None


# ── Render-time compute resolution (Phase 648) ───────────────────────────────
#
# A `Bound` `Transform` binding is resolved through the corpus-certified
# `fuaran_ui.compute` evaluator. Two entry points, mirroring the TS/F# split:
# `resolve_source` (row context → the transformed rows) and
# `resolve_scalar_text` / `resolve_scalar_number` (scalar slot → the lone cell
# of an exactly-1×1 result — the Phase 632 law). The flat `sources` map doubles
# as the compute-parameter state store (a `Filter` / `State` param reads it by
# name/key), exactly as the interactive runtime passes it.


def _is_transform(binding: Value) -> bool:
    return isinstance(binding, Obj) and binding.tag == "Transform"


def _is_expr(binding: Value) -> bool:
    return isinstance(binding, Obj) and binding.tag == "Expr"


def _expr_as_transform(expr_binding: Obj) -> Obj:
    """Phase 1534 - a ``Binding.Expr`` as the equivalent one-row ``Transform``.

    The whole implementation, on purpose: param resolution, list-param
    substitution and the evaluator are then literally the code the pipeline
    runs, so an expression cannot mean one thing inside a ``derive`` and another
    inside an ``Expr``. A second evaluator here would be a second thing to
    specify, certify on five hosts, and keep in step.

    The frame carries one column of one row so ``derive`` has a row to produce;
    the expression never reads it (a ``col`` reference is refused at decode),
    and the trailing ``project`` drops it so the result is 1x1 by construction
    rather than by inspection.
    """
    unit_frame = Obj(
        None,
        {
            "columns": Obj(None, {"__unit": Arr([True])}),
        },
    )
    pipeline = Arr(
        [
            Obj("derive", {"expr": expr_binding.fields["expr"], "name": "__value"}),
            Obj("project", {"cols": Arr([Obj(None, {"a": "__value", "b": "__value"})])}),
        ]
    )
    fields: dict[str, Value] = {"pipeline": pipeline, "source": unit_frame}
    params = expr_binding.fields.get("params")
    if params is not None:
        fields["params"] = params
    return Obj("Transform", fields)


def _transform_state(sources: BindingSourcesLike | None) -> dict[str, object]:
    return dict(as_sources(sources).values)


def resolve_source(source: Value, sources: BindingSourcesLike | None = None) -> object | None:
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


def _scalar_cell(transform: Obj, sources: BindingSourcesLike | None) -> _ScalarOutcome:
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


def resolve_scalar_text(binding: Value, sources: BindingSourcesLike | None = None) -> str | None:
    """Resolve a binding in a **text scalar slot** to a plain string, or ``None``
    when unresolved / ambiguous (the caller renders ``""``). A ``Transform``
    resolves to its 1×1 result cell; every other binding resolves as
    :func:`resolve_binding` then stringifies."""
    if _is_expr(binding):
        assert isinstance(binding, Obj)
        tag, value = _scalar_cell(_expr_as_transform(binding), sources)
        return _cell_value_to_text(value) if tag == "resolved" else None
    if _is_transform(binding):
        assert isinstance(binding, Obj)
        tag, value = _scalar_cell(binding, sources)
        return _cell_value_to_text(value) if tag == "resolved" else None
    resolved = resolve_binding(binding, sources)
    return str(resolved) if resolved is not None else None


def resolve_scalar_number(binding: Value, sources: BindingSourcesLike | None = None) -> float | None:
    """Resolve a binding in a **numeric scalar slot** to a float, or ``None`` when
    unresolved / ambiguous / non-numeric (the caller renders the em-dash). A
    ``Transform`` resolves to its 1×1 result cell (coerced numerically); every
    other binding resolves as :func:`resolve_binding` then coerces."""
    if _is_expr(binding):
        assert isinstance(binding, Obj)
        tag, value = _scalar_cell(_expr_as_transform(binding), sources)
        return _cell_value_to_float(value) if tag == "resolved" else None
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


def resolve_scalar_bool(binding: Value, sources: BindingSourcesLike | None = None) -> bool | None:
    """Resolve a binding in a **boolean scalar slot**, or ``None`` when
    unresolved / ambiguous / non-boolean (fuaran#1535).

    The third of the trio beside :func:`resolve_scalar_text` and
    :func:`resolve_scalar_number`, so a ``Transform`` or an ``Expr`` reaches a
    boolean slot through the same 1×1 seam a text or numeric one does.

    STRICT: only a genuine boolean resolves. ``0``, ``""`` and ``"false"`` are
    all ``None`` rather than ``False``, because every language that has guessed
    at truthiness has guessed differently and five hosts agreeing on a rendering
    is the whole point of the corpus. The vocabulary already carries the total
    spellings (``isNull``, ``=``, ``not``), so refusing costs an author nothing
    but the explicit operator.
    """
    if _is_expr(binding):
        assert isinstance(binding, Obj)
        tag, value = _scalar_cell(_expr_as_transform(binding), sources)
        return value if tag == "resolved" and isinstance(value, bool) else None
    if _is_transform(binding):
        assert isinstance(binding, Obj)
        tag, value = _scalar_cell(binding, sources)
        return value if tag == "resolved" and isinstance(value, bool) else None
    resolved = resolve_binding(binding, sources)
    return resolved if isinstance(resolved, bool) else None


# ── Conditional presence and predicate branching (fuaran#1535) ───────────────
#
# Two decisions a renderer takes BEFORE it draws anything. Both live here rather
# than in the render module because every rendering surface in this package (the
# HTML renderer, the email projection, the notebook one) must agree on them, and
# because they are the same two rules the other four hosts state.


def is_node_visible(visible: Value | None, sources: BindingSourcesLike | None = None) -> bool:
    """THE rule for whether a node reaches the output at all (WIRE_FORMAT §3.1).

    A node is removed ONLY on a resolved ``False``. An absent predicate, an
    unresolved one and an errored one all RENDER, and the asymmetry is the design
    rather than a leniency: a ``False`` is an author saying "not now", and every
    other outcome is the renderer failing to answer the question. Content that
    vanishes because a source was missing is the one failure a reader cannot see,
    cannot report and cannot work around.

    Note this takes the SLOT rather than the node, because this package's node
    model carries envelope traits in an untyped ``extras`` map and a caller
    already holding the slot should not have to reconstruct a node to ask.

    "Errored" above means the compute evaluator could not produce a single cell —
    the renderer failing to answer the question, which is what the leniency is
    for. It does NOT cover :exc:`WireSurvivabilityError` (Phase 1667), which
    propagates: that is the document asking a question no decoded tree can
    answer, and it is reported rather than rendered around. The failure this
    asymmetry protects against is content disappearing INVISIBLY, and a raised
    error is the opposite of invisible.
    """
    if visible is None:
        return True
    return resolve_scalar_bool(visible, sources) is not False


def select_switch_case(
    cases: Value | None, selector: str | None, sources: BindingSourcesLike | None = None
) -> Value | None:
    """First-match-wins case selection over BOTH kinds of case (fuaran#1535).

    ``selector`` is the switch's already-resolved ``on`` value (``None`` when it
    did not resolve). A ``match`` case compares against it; a ``when`` case
    evaluates its predicate and consults no selector at all — which is why a
    switch whose cases are all predicates needs no selector.

    A predicate case is taken ONLY on a resolved ``True``; ``False``, unresolved
    and errored all fall through to the next case and ultimately to ``default``.
    That is the OPPOSITE default from :func:`is_node_visible`, and deliberately
    so: falling through here lands on a ``default`` branch the author wrote, so
    no content disappears — whereas a node with no verdict has no fallback.
    "Errored" carries the same reading it does there: the evaluator could not
    answer, not :exc:`WireSurvivabilityError`, which propagates.

    Returns the selected case's ``child``, or ``None`` for the default.
    """
    if not isinstance(cases, Arr):
        return None
    for case in cases.items:
        if not isinstance(case, Obj):
            continue
        match = case.fields.get("match")
        if match is not None:
            if selector is not None and match == selector:
                return case.fields.get("child")
            continue
        when = case.fields.get("when")
        # A case carrying neither is unreachable from the wire (the decoder
        # refuses it) and reported pre-emit; it is skipped rather than asserted
        # away because a tree built in-process can still hold one.
        if when is not None and resolve_scalar_bool(when, sources) is True:
            return case.fields.get("child")
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


#: The ``Format`` cases this host renders (Phase 1663). Locale-INDEPENDENT by
#: declaration: ``Duration`` is unit glyphs and English words with exact
#: cross-pipeline parity, and the relative-time pair reduces to ``(unit, count)``
#: through the one shared ladder and then phrases it in the deterministic English
#: form — the fallback tier. The four cases NOT here (``Number`` / ``Currency`` /
#: ``Percent`` / ``Date``) take their text from a locale database, so a
#: stdlib-only host has no canonical answer to give and resolves them to absence
#: exactly as it did before this seam existed. The corpus's render-text family
#: enumerates that exclusion with its reason.
_LOCALE_INDEPENDENT_FORMATS = frozenset({"Duration", "RelativeTime", "Since"})


def resolve_locale_tag(binding: Value, sources: BindingSourcesLike | None = None) -> str | None:
    """The BCP-47 tag a ``Binding.Format`` renders under, or ``None``.

    ``LocaleSource.Explicit`` pins its own tag; ``LocaleSource.Ambient`` reads
    the host's :attr:`BindingSources.locale`, whose ``""`` identity default means
    "the runtime default locale". The resolution rule is the SEAM's rather than
    each caller's, which is why this is public: a host that wants to render the
    locale-dependent ``Format`` cases itself — with ``babel``, or ``Intl`` under
    Pyodide — needs the tag the document asked for, and must not re-derive the
    precedence.
    """
    if not isinstance(binding, Obj) or binding.tag != "Format":
        return None
    locale = binding.fields.get("locale")
    if not isinstance(locale, Obj):
        return None
    if locale.tag == "Explicit":
        tag = locale.fields.get("tag")
        return tag if isinstance(tag, str) else None
    if locale.tag == "Ambient":
        return as_sources(sources).locale
    return None


def _format_projection(binding: Obj, sources: BindingSourcesLike | None) -> str | None:
    """``Binding.Format`` in a slot: its numeric source projected to a string.

    Renders the locale-independent cases and resolves every other to absence —
    see :data:`_LOCALE_INDEPENDENT_FORMATS`. ``Since`` is the one case whose
    text is a function of the HOST INSTANT as well as of its source, so the
    delta is taken here, where the instant lives, and the phrasing helpers stay
    pure projections of their arguments.
    """
    fmt = binding.fields.get("format")
    if not isinstance(fmt, Obj) or not isinstance(fmt.tag, str) or fmt.tag not in _LOCALE_INDEPENDENT_FORMATS:
        return None
    value = resolve_scalar_number(binding.fields.get("source"), sources)
    if value is None:
        return None
    if fmt.tag == "Duration":
        return format_duration(str(fmt.fields.get("unit")), str(fmt.fields.get("style")), value)
    if fmt.tag == "RelativeTime":
        return format_relative_english(str(fmt.fields.get("unit")), value)
    # ``Since``. The source is read as an instant in whole Unix-epoch seconds
    # (``Date``'s convention) and the sign follows ``Intl.RelativeTimeFormat``'s:
    # negative is the past. An unset or unreadable host instant is ABSENCE, for
    # exactly the reason ``Binding.Now`` gives — a relative time computed against
    # an invented "now" is a plausible wrong answer, which is worse than a
    # visible placeholder.
    now_epoch = epoch_seconds_of_instant(as_sources(sources).now)
    if now_epoch is None:
        return None
    declared = fmt.fields.get("unit")
    unit, count = since_unit_and_count(declared if isinstance(declared, str) else None, value - now_epoch)
    return format_relative_english(unit, count)


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
    if tag == "Since":
        # Phase 1663 — ``Format.Since`` now RENDERS on this host, through
        # :func:`_format_projection`, where the host instant
        # (:attr:`BindingSources.now`) lives. This arm is the CELL vocabulary's,
        # and ``CellFormat`` carries no ``Since`` case, so it is unreachable from
        # the wire: the decoder refuses the tag in a cell position. It is kept
        # because this function is public and a caller may hand it a hand-built
        # tag, and its answer is the honest one for a function with no sources —
        # the empty string, NOT the plain number, because an epoch integer
        # rendered where a reader expects "3 hours ago" is a silently wrong
        # answer. A caller that wants the phrase resolves the binding rather
        # than the format.
        return ""
    # Date / Custom: structural — fall back to the plain numeric form.
    return _plain_number(num)
