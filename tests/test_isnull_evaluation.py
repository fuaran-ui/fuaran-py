"""Phase 1654 / fuaran-core#90 — the ``IsNull`` evaluator arm.

``IsNull`` decoded and re-encoded byte-perfectly here long before it could be
EVALUATED: the codec carried it, the corpus round-trip was green, and
``eval_pipeline([Filter(IsNull(Col(...)))], ...)`` answered
``TYPE_ERROR: unknown ColExpr ... IsNull``. A round-trip corpus cannot see that
class at all, which is why these assertions are about evaluation and not bytes.

**The verb is TOTAL, and the tests below are shaped around that rather than
around the three-valued rule its neighbours follow.** ``Coalesce``, ``Binary``,
``Not`` and ``InList`` all propagate null, because a null operand makes their
question unanswerable. ``IsNull`` is the verb that ASKS the question, so it must
always answer: ``Bool true`` for a null subject, ``Bool false`` otherwise, and
never null — an ``IsNull`` that could itself be null would be testable only by
another ``IsNull``. Both reference hosts say exactly this at their own arms, and
the totality claim is what the last test pins.
"""

from __future__ import annotations

from fuaran_py.dataframe import (
    Cast,
    Cell,
    Coalesce,
    Col,
    Column,
    Derive,
    Filter,
    IsNull,
    Lit,
    Not,
    Table,
    eval_pipeline,
)
from fuaran_py.dataframe.model import NULL


def _table() -> Table:
    """Two columns, each with one null and two present cells, misaligned on purpose.

    The misalignment is the discriminator for a per-ROW read: an implementation that
    tested the column rather than the cell would answer the same for both columns.
    """
    return Table(
        schema=[("dept", "string"), ("salary", "number")],
        columns=[
            Column("dept", "string", [Cell("string", "eng"), NULL, Cell("string", "ops")]),
            Column("salary", "number", [NULL, Cell("number", 10.0), Cell("number", 20.0)]),
        ],
    )


def _derived(expr, name: str = "n") -> list[Cell]:
    result = eval_pipeline([Derive(name, expr)], _table())
    assert result.ok, result.error
    return next(c for c in result.value.columns if c.name == name).cells


def test_is_null_is_true_exactly_where_the_cell_is_null() -> None:
    assert [c.value for c in _derived(IsNull(Col("dept")))] == [False, True, False]
    assert [c.value for c in _derived(IsNull(Col("salary")))] == [True, False, False]


def test_the_derived_column_is_typed_bool() -> None:
    result = eval_pipeline([Derive("n", IsNull(Col("dept")))], _table())
    assert result.ok, result.error
    assert next(c for c in result.value.columns if c.name == "n").type == "bool"


def test_filter_keeps_the_null_rows_and_drops_the_rest() -> None:
    result = eval_pipeline([Filter(IsNull(Col("dept")))], _table())
    assert result.ok, result.error
    assert [c.value for c in result.value.columns[0].cells] == [None]
    assert [c.value for c in result.value.columns[1].cells] == [10.0]


def test_not_is_null_is_the_presence_test() -> None:
    """``Not(IsNull(x))`` is total too — which is only true because ``IsNull`` is.

    ``Not`` propagates null, so a three-valued ``IsNull`` would make the obvious
    spelling of "is present" return null on exactly the rows it is asked about.
    """
    assert [c.value for c in _derived(Not(IsNull(Col("dept"))))] == [True, False, True]


def test_it_answers_for_a_subject_the_neighbouring_verbs_cannot() -> None:
    """The totality claim, pinned against the operators that DO propagate null.

    Each subject here evaluates to null through a different mechanism — a coalesce
    whose first candidate is null and whose second is a null cell, a cast of null,
    a negation of null. Every one of them yields ``Bool true``, never null.

    (Not arithmetic: this host refuses a null arithmetic operand with a loud
    ``TYPE_ERROR`` rather than propagating null, so it belongs in the error test
    below rather than here. Whether that refusal matches the reference hosts is a
    separate question this phase did not open.)
    """
    for subject in (
        Coalesce([Lit(NULL), Col("salary")]),
        Cast("number", Lit(NULL)),
        Not(Lit(NULL)),
    ):
        cells = _derived(IsNull(subject))
        assert all(c.kind == "bool" for c in cells), (subject, cells)
        assert cells[0].value is True, (subject, cells)


def test_a_failure_inside_the_subject_still_propagates() -> None:
    """Total over CELLS is not the same as swallowing an evaluation ERROR.

    An unknown column is a structured failure of the pipeline, not a null cell, and
    reporting it as ``IsNull -> true`` would turn a typo into a plausible answer.
    """
    result = eval_pipeline([Derive("n", IsNull(Col("nope")))], _table())
    assert not result.ok
    assert result.error.code == "UNKNOWN_COLUMN"
