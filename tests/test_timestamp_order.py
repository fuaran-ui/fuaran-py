"""Phase 2206: timestamp cells order by the instant, not by their canonical text."""

from __future__ import annotations

import pytest

from fuaran_ui._core.dataframe.evaluate import _compare_cells
from fuaran_ui._core.dataframe.model import TIMESTAMP, Cell


@pytest.mark.parametrize(
    ("a", "b", "want"),
    [
        ("2025-07-08T18:40:00Z", "2025-07-08T18:40:00.5Z", -1),
        ("2025-07-08T18:40:00.5Z", "2025-07-08T18:40:00Z", 1),
        ("2025-07-08T18:40:00.12Z", "2025-07-08T18:40:00.123456789Z", -1),
        ("2025-07-08T18:40:00.9Z", "2025-07-08T18:40:01Z", -1),
        ("2025-07-08T18:40:00.5Z", "2025-07-08T18:40:00.5Z", 0),
    ],
)
def test_timestamp_cells_order_by_instant(a: str, b: str, want: int) -> None:
    assert _compare_cells(Cell(TIMESTAMP, a), Cell(TIMESTAMP, b)) == want
