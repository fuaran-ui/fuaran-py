"""The value-space vocabulary — the twin of the reference ``Fuaran.Core.Function`` ``ValueSpace``.

A closed contract over what a hole accepts: an int range, a float range, a string length, an
enum, or any string. The function registry (:mod:`fuaran_ui._core.function.registry`) matches
signatures by space subsumption over it, and the capability registry validates an invocation's
arguments against it — so it is ONE type, owned here inside the Core boundary.

Private — the published import path is :mod:`fuaran_ui.ui.capability`, which re-exports
:class:`HoleSpace` unchanged (Phase 1872).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HoleSpace:
    """A declared argument's value space — the validation envelope for an invocation arg.

    A closed contract by shape (default-deny): an arg outside its space is rejected before
    the body runs. Mirrors the reference hole-space vocabulary (int/float range, string
    length, enum, any-string)."""

    kind: str  # "intRange" | "floatRange" | "stringLen" | "enum" | "anyString"
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] = ()

    def accepts(self, value: object) -> bool:
        if self.kind == "intRange":
            return isinstance(value, int) and not isinstance(value, bool) and self._in_range(value)
        if self.kind == "floatRange":
            return isinstance(value, (int, float)) and not isinstance(value, bool) and self._in_range(float(value))
        if self.kind == "stringLen":
            return isinstance(value, str) and self._in_range(len(value))
        if self.kind == "enum":
            return isinstance(value, str) and value in self.choices
        if self.kind == "anyString":
            return isinstance(value, str)
        return False

    def _in_range(self, n: float) -> bool:
        if self.min is not None and n < self.min:
            return False
        return not (self.max is not None and n > self.max)
