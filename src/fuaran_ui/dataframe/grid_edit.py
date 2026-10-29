"""Published path of :mod:`fuaran_ui._core.dataframe.grid_edit` — re-exported unchanged.

Grid edits as columnar ops (WIRE_FORMAT §3.2, "Edits as column ops — GridEdit"): the codec,
the op's application and inverse, the row delta under a declared row identity, and an edit
history. The implementation sits behind the private Core boundary (see :mod:`fuaran_ui._core`).
"""

from __future__ import annotations

from .._core.dataframe.grid_edit import *  # noqa: F403
from .._core.dataframe.grid_edit import __all__ as __all__
