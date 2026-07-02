"""The turn-loop helper.

A session holds the current tree between turns so each subsequent prompt is a
*repair* against it (a cheap diff), not a from-scratch regeneration — the
token-saving ergonomic the whole model hinges on. The first ``next(prompt)``
is a fresh generation; once a turn produces a tree the session remembers it,
and the next ``next(prompt)`` sends it as the current tree automatically.
"""

from __future__ import annotations

from .client import FuaranClient
from .contract import Produced, TurnResult


class FuaranSession:
    """A turn loop over a :class:`~fuaran_py.client.client.FuaranClient` that
    carries the current tree forward. Stateful by design — one session is one
    editing conversation."""

    def __init__(self, client: FuaranClient, *, initial_tree_json: str | None = None) -> None:
        """Pass ``initial_tree_json`` (an existing tree's canonical wire JSON)
        to seed the session so the first turn is already a repair; omit it to
        start with a fresh generation."""
        self._client = client
        self._current_tree_json = initial_tree_json

    @property
    def current_tree_json(self) -> str | None:
        """The canonical wire JSON of the tree the session is holding, or
        ``None`` before the first produced turn."""
        return self._current_tree_json

    def next(
        self,
        prompt: str,
        *,
        provider_key: str | None = None,
        access_token: str | None = None,
        disable_corpus_read: bool | None = None,
        contribute_corpus: bool | None = None,
    ) -> TurnResult:
        """Run the next turn.

        The held tree (if any) is sent as the current tree, so this prompt
        repairs it rather than regenerating. On a produced result the session
        advances to the new tree; on access-denied / turn-failed the held tree
        is left unchanged, so the caller can retry the same repair.
        """
        result = self._client.generate(
            prompt,
            current_tree_json=self._current_tree_json,
            provider_key=provider_key,
            access_token=access_token,
            disable_corpus_read=disable_corpus_read,
            contribute_corpus=contribute_corpus,
        )
        if isinstance(result, Produced):
            self._current_tree_json = result.tree_json
        return result

    def reset(self) -> None:
        """Forget the held tree — the next turn is a fresh generation again."""
        self._current_tree_json = None
