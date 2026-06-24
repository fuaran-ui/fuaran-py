"""``fuaran_py.conformance`` — corpus round-trip smoke harness."""

from __future__ import annotations

from .harness import FixtureResult, run_corpus, run_dag_corpus, run_fixture

__all__ = ["run_corpus", "run_dag_corpus", "run_fixture", "FixtureResult"]
