"""``fuaran_py.ui.controls`` — controls whose slot feeds a Compute parameter.

:mod:`fuaran_py.ui.quick` makes a dashboard out of the data a notebook already holds.
This makes that dashboard **answer back**: a control declares a state slot, a pipeline
reads that slot through :func:`~fuaran_py.ui.compute.param`, and a host with no Python
present re-derives the rows when the reader moves the control::

    from fuaran_py.ui import col, control, frame, fuaran, node, param, quick

    region = control.select("region", options=col("region").unique(), source=frame(rows))
    fr = frame(rows).filter(col("region").eq(param("region"))).bind(region)

    app = quick.dashboard(
        "Revenue",
        region,
        node.bare(fuaran.chart("rev", source=fr.to_transform_binding(), x_field="month", y_fields=["revenue"])),
    )

Three properties, and each is a decision rather than an implementation detail.

**The slot is seeded, so the first render is already an answer.** Every control's value
binding is a ``Binding.State`` carrying the declared default, which WIRE_FORMAT §24.4
makes the value of that slot for every reader in the tree — including the pipeline's own
parameter — before anything is written. A document opened for the first time therefore
shows the default's view, not an empty one, and it does so on a renderer that holds no
store at all.

**The control writes its own slot, and that is why no handler is emitted.** A renderer
arms its write-back default only for a control that declares NO change handler: a
declared handler is a host closure, which cannot cross the wire, so a document carrying
one describes a control whose changes go somewhere the document cannot reach. These
constructors pass ``on_change=False`` for that reason, and it is the single fact that
makes an exported file interactive rather than merely pretty.

**A parameter no control fills is refused when the binding is lowered.** ``.bind(...)``
is what makes a name resolvable, and :meth:`~fuaran_py.ui.compute.Frame.to_transform_binding`
refuses a pipeline that reads an undeclared one, naming it. The evaluator's
``UNBOUND_PARAM`` still exists and is still correct; it is the backstop for a pipeline
that arrived some other way, not the first thing an author should meet.

Parameter names — the one thing to read before writing a filter
---------------------------------------------------------------

A control's parameter is named after the control, and a RANGE has two ends, so it has two
parameters. A ``Transform`` parameter resolves to one scalar and the algebra has no way to
project one end out of a pair, so a range that a pipeline can actually compare against is
two scalar slots rather than one pair-valued one:

===========================================  ====================================
``control.select("region")``                 ``region``
``control.multi_select("regions")``          ``regions``  (a LIST parameter — read
                                             it with ``col(...).is_in(param(...))``)
``control.range("revenue")``                 ``revenue_min``, ``revenue_max``
``control.date_range("window")``             ``window_from``, ``window_to``
===========================================  ====================================

An end left unseeded is an **absent constraint**, not a zero: the parameter is unbound, so
the filter step reading it is pruned and that end is open. Which is why
``control.range("revenue", low=0)`` is "at least nothing" rather than "exactly nothing".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from ..model import Arr
from ..model import Value as WireValue
from ..schema import types as t
from ..schema.types import Binding, SelectOption, UiNode
from . import fuaran
from .compute import Frame, OptionSource, ParamDecl, TransformBinding
from .quick import derive_id

__all__ = ["Control", "control", "select", "multi_select", "range", "date_range"]


@dataclass(frozen=True)
class Control(UiNode):
    """A node that is also a parameter declaration.

    It IS a :class:`~fuaran_py.schema.types.UiNode` — drop it straight into a dashboard's
    children — and it additionally carries the ``params`` a frame binds. Subclassing
    rather than pairing the two is what keeps ``quick.dashboard(title, region, chart)``
    readable: an author should not have to unwrap a control to place it.
    """

    params: tuple[ParamDecl, ...] = ()


#: How a control's own name becomes its visible label when none is given. The same rule
#: `quick` applies to a grid column header, and for the same reason — the name is already
#: the human-meaningful word, so a second argument to repeat it is noise.
def _label_of(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _state(key: str, default: WireValue) -> Binding:
    """The control's slot: a ``State`` binding carrying its declared default.

    The SAME binding is used for the control's value and for the parameter's source, and
    that is deliberate. §24.4 makes a declared default seed the slot for every reader, so
    the two agree by construction rather than by coincidence — and a resolver that seeds
    nothing (a host driving the pipeline directly) still binds the parameter, because the
    default it falls back to is the one the control declares.
    """
    return t.State(key, default)


# ── Option lists ─────────────────────────────────────────────────────────────


def _literal_options(options: object) -> list[SelectOption] | None:
    """The option list as literal ``SelectOption``\\ s, or ``None`` if this is not one."""
    if isinstance(options, Mapping):
        return [SelectOption(t.LiteralText(str(label)), str(value)) for value, label in options.items()]
    if isinstance(options, Sequence) and not isinstance(options, str):
        out: list[SelectOption] = []
        for entry in options:
            if isinstance(entry, SelectOption):
                out.append(entry)
            elif isinstance(entry, tuple):
                value, label = entry
                out.append(SelectOption(t.LiteralText(str(label)), str(value)))
            else:
                out.append(SelectOption(t.LiteralText(str(entry)), str(entry)))
        return out
    return None


def _options_binding(options: object, source: Frame | None) -> Binding:
    """Lower an option list to the binding a control's option slot holds.

    A derived list (``col('region').unique()``) needs the data it is derived FROM, which
    is what ``source`` is; the result is an ordinary ``Binding.Transform``, so the option
    list is evaluated by the same evaluator the rest of the tree's data goes through and
    is as much data as the rows are.
    """
    if isinstance(options, OptionSource):
        if source is None:
            raise TypeError(
                f"options=col({options.column!r}).unique() is derived from data, so the control "
                "needs the frame it is derived from: pass source=frame(...)."
            )
        derived = replace(source, pipeline=(*source.pipeline, *options.pipeline()))
        return _as_binding(derived.to_transform_binding())
    if isinstance(options, Frame):
        return _as_binding(options.to_transform_binding())
    literal = _literal_options(options)
    if literal is None:
        return options  # type: ignore[return-value]  # already a Binding
    return t.Static(Arr([option.to_wire() for option in literal]))


def _as_binding(transform: TransformBinding) -> Binding:
    """``TransformBinding`` is the seventh wire binding case and lives a layer above the
    closed ``Binding`` union (it needs the dataframe codec ``types.py`` must not depend
    on). It lowers through the same ``to_wire`` protocol; the cast is that layering, not
    a fudge — the same one :mod:`fuaran_py.ui.quick` makes for a node's ``source``."""
    return transform  # type: ignore[return-value]


# ── Constructors ─────────────────────────────────────────────────────────────


def select(
    name: str,
    *,
    options: object,
    default: str | None = None,
    label: str | None = None,
    source: Frame | None = None,
    placeholder: str = "—",
    occurrence: int = 0,
) -> Control:
    """One value from a closed set, feeding the scalar parameter ``name``.

    ``options`` takes values, ``(value, label)`` pairs, a ``{value: label}`` mapping, an
    options binding, or ``col(column).unique()`` with the ``source`` frame it derives
    from. Omitting ``default`` leaves the slot unseeded, so the parameter is unbound and
    the filter reading it prunes — the placeholder row shows and every row is visible.
    """
    slot = _state(name, default)
    node = fuaran.select(
        derive_id("select", name, occurrence),
        label=label if label is not None else _label_of(name),
        source=_options_binding(options, source),
        value=slot,
        placeholder=placeholder,
        on_change=False,
    )
    return Control(node.id, node.kind, node.accessibility, params=(ParamDecl(name, slot),))


def multi_select(
    name: str,
    *,
    options: object,
    default: Sequence[str] = (),
    label: str | None = None,
    source: Frame | None = None,
    occurrence: int = 0,
) -> Control:
    """Several values from a closed set, feeding the LIST parameter ``name``.

    Read it with ``col('region').is_in(param('regions'))``. An EMPTY selection is the
    absence of a constraint rather than a constraint nothing satisfies, so deselecting
    everything shows the unfiltered table — which is why the default is the empty list
    and not "everything".

    The single-value slot carries a value-less ``Static``: a multi-select's selection is
    the list, and a scalar beside it would be a second answer to the same question.
    """
    slot = _state(name, Arr([str(v) for v in default]))
    node = fuaran.select(
        derive_id("multiselect", name, occurrence),
        label=label if label is not None else _label_of(name),
        source=_options_binding(options, source),
        value=t.Static(None),
        multiple=True,
        values=slot,
        on_change=False,
    )
    return Control(node.id, node.kind, node.accessibility, params=(ParamDecl(name, slot),))


def range(  # noqa: A001 — the control's name; `builtins.range` is not what a dashboard means
    name: str,
    *,
    low: float | None = None,
    high: float | None = None,
    label: str | None = None,
    occurrence: int = 0,
) -> Control:
    """A numeric range, feeding the two scalar parameters ``<name>_min`` / ``<name>_max``.

    Written as two comparisons, which is also how it reads::

        fr = (frame(rows)
              .filter(col("revenue") >= param("revenue_min"))
              .filter(col("revenue") <= param("revenue_max"))
              .bind(control.range("revenue", low=0, high=20000)))

    Two slots rather than one pair-valued slot, because a ``Transform`` parameter
    resolves to a single scalar and the expression algebra has no projection from a pair
    to its ends: a parameter bound to a pair is a LIST parameter, which can only test
    membership. The pair-valued control the wire also has would render more prettily and
    compute nothing.

    An unseeded end is an open one — its parameter is unbound, so its filter step prunes.
    """
    min_key, max_key = f"{name}_min", f"{name}_max"
    low_slot, high_slot = _state(min_key, low), _state(max_key, high)
    heading = label if label is not None else _label_of(name)
    node = fuaran.filters(
        derive_id("range", name, occurrence),
        items=[
            t.FilterSpec(min_key, t.LiteralText(f"{heading} from"), t.NumberField(low_slot, on_change=False)),
            t.FilterSpec(max_key, t.LiteralText(f"{heading} to"), t.NumberField(high_slot, on_change=False)),
        ],
    )
    return Control(
        node.id,
        node.kind,
        node.accessibility,
        params=(ParamDecl(min_key, low_slot), ParamDecl(max_key, high_slot)),
    )


def date_range(
    name: str,
    *,
    start: str | None = None,
    end: str | None = None,
    variant: t.DateVariant = "Date",
    label: str | None = None,
    occurrence: int = 0,
) -> Control:
    """A date range, feeding the two scalar parameters ``<name>_from`` / ``<name>_to``.

    ``start`` / ``end`` are ISO-8601 strings in the ``variant``'s shape, and they seed the
    two ends. Same-variant ISO-8601 strings sort chronologically as ordinary strings, so
    ``col('day') >= param('window_from')`` is a date comparison with nothing to parse and
    no locale in it — which is what lets the whole comparison happen in a browser holding
    no date library.

    Two slots, for :func:`range`'s reason.
    """
    from_key, to_key = f"{name}_from", f"{name}_to"
    from_slot, to_slot = _state(from_key, start), _state(to_key, end)
    heading = label if label is not None else _label_of(name)
    node = fuaran.filters(
        derive_id("daterange", name, occurrence),
        items=[
            t.FilterSpec(from_key, t.LiteralText(f"{heading} from"), t.DateField(from_slot, variant, on_change=False)),
            t.FilterSpec(to_key, t.LiteralText(f"{heading} to"), t.DateField(to_slot, variant, on_change=False)),
        ],
    )
    return Control(
        node.id,
        node.kind,
        node.accessibility,
        params=(ParamDecl(from_key, from_slot), ParamDecl(to_key, to_slot)),
    )


class control:  # noqa: N801 — namespace object, mirroring `fuaran.*` / `binding.*` / `quick.*`
    """The control namespace: ``control.select`` / ``multi_select`` / ``range`` / ``date_range``."""

    select = staticmethod(select)
    multi_select = staticmethod(multi_select)
    range = staticmethod(range)
    date_range = staticmethod(date_range)
