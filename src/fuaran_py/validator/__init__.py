"""``fuaran_py.validator`` — pre-emit structural validation."""

from __future__ import annotations

from .validate import Finding, validate_node

__all__ = ["validate_node", "Finding"]
