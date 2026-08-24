"""WIRE_FORMAT.md §21 resource limits — the shape half of the totality claim.

§6 promises every wire-shape violation surfaces a structured, recoverable error
and never a throw. That held on semantics and was false on shape: ``decode_node``
caught ``ValueError`` around ``json.loads`` while CPython raises
``RecursionError`` on deep nesting, so a payload of ``[[[[[…`` escaped the
decoder as a throw.

Both halves are asserted here, and the second is the one easy to leave out: a
limit that refused everything would pass a refusal-only suite. So each bound is
tested from BOTH sides — the largest conformant document MUST decode (§21.2
rule 1: refusing it is non-conformance, not conservatism) and the smallest
over-limit one MUST be refused (rule 2).
"""

from __future__ import annotations

import json

from fuaran_py.limits import (
    MAX_ARRAY_LENGTH,
    MAX_JSON_DEPTH,
    MAX_NODE_DEPTH,
    MAX_STRING_LENGTH,
)
from fuaran_py.ops.decode import decode_op
from fuaran_py.result import CODES, INVALID_JSON, LIMIT_EXCEEDED
from fuaran_py.schema.decode import decode_node

_BOX_OPEN = (
    '{"id":"n","kind":{"$type":"Box","role":"Group",'
    '"layout":{"$type":"Flex","direction":"Vertical","wrap":false},"children":['
)
_BOX_LEAF = (
    '{"id":"leaf","kind":{"$type":"Box","role":"Group",'
    '"layout":{"$type":"Flex","direction":"Vertical","wrap":false},"children":[]}}'
)


def nested_nodes(n: int) -> str:
    """A chain of ``n`` nested Box nodes, innermost an empty Box."""
    return _BOX_OPEN * (n - 1) + _BOX_LEAF + "]}}" * (n - 1)


def nested_batch(n: int) -> str:
    """A chain of ``n`` nested ``Batch`` ops, innermost a ``RemoveNode``."""
    return '{"$type":"Batch","ops":[' * (n - 1) + '{"$type":"RemoveNode","target":"x"}' + "]}" * (n - 1)


# ── the code itself ─────────────────────────────────────────────────────────


def test_limit_exceeded_is_a_canonical_code() -> None:
    assert LIMIT_EXCEEDED in CODES


# ── the node-depth bound ────────────────────────────────────────────────────


def test_accepts_a_tree_at_exactly_max_node_depth() -> None:
    # Rule 1 — refusing a conformant document is non-conformance, not caution.
    result = decode_node(nested_nodes(MAX_NODE_DEPTH))
    assert result.ok, getattr(result, "error", None)


def test_refuses_one_level_past_max_node_depth() -> None:
    result = decode_node(nested_nodes(MAX_NODE_DEPTH + 1))
    assert not result.ok
    assert result.error.code == LIMIT_EXCEEDED
    # Rule 2 — a limit breach is not a syntax error.
    assert result.error.code != INVALID_JSON
    assert str(MAX_NODE_DEPTH) in result.error.message


def test_refuses_a_deep_tree_by_returning_not_raising() -> None:
    # The original defect in one line: this used to escape as RecursionError.
    result = decode_node(nested_nodes(5000))
    assert not result.ok
    assert result.error.code == LIMIT_EXCEEDED


# ── the op-decoder axis ─────────────────────────────────────────────────────
#
# §21.5's note for implementers: bounding the node decoder is NOT sufficient,
# because Batch makes the op decoder self-recursive on a separate axis.


def test_accepts_nested_batch_at_exactly_max_node_depth() -> None:
    result = decode_op(nested_batch(MAX_NODE_DEPTH))
    assert result.ok, getattr(result, "error", None)


def test_refuses_nested_batch_one_level_past_the_limit() -> None:
    result = decode_op(nested_batch(MAX_NODE_DEPTH + 1))
    assert not result.ok
    assert result.error.code == LIMIT_EXCEEDED


def test_refuses_deeply_nested_batch_by_returning_not_raising() -> None:
    result = decode_op(nested_batch(5000))
    assert not result.ok
    assert result.error.code == LIMIT_EXCEEDED


def test_op_axis_is_counted_separately_from_the_node_axis() -> None:
    # A Batch chain within the op bound whose payload node is within the node
    # bound must decode. If the two shared one counter this would breach at the
    # sum of the two depths.
    inner = '{"$type":"ReplaceRoot","node":' + nested_nodes(MAX_NODE_DEPTH) + "}"
    doc = '{"$type":"Batch","ops":[' * (MAX_NODE_DEPTH - 1) + inner + "]}" * (MAX_NODE_DEPTH - 1)
    result = decode_op(doc)
    assert result.ok, getattr(result, "error", None)


# ── the syntactic and linear bounds ─────────────────────────────────────────


def test_refuses_bare_nesting_past_max_json_depth() -> None:
    n = MAX_JSON_DEPTH + 1
    result = decode_node("[" * n + "]" * n)
    assert not result.ok
    # Well-formed but too deep — LIMIT_EXCEEDED, never INVALID_JSON (rule 2).
    assert result.error.code == LIMIT_EXCEEDED


def test_accepts_bare_nesting_at_exactly_max_json_depth() -> None:
    # It is not a valid NODE, so the decode fails — but it must fail on SHAPE,
    # not on the limit. This is what stops the syntactic guard being set one
    # level too tight, which a refusal-only test could never detect.
    n = MAX_JSON_DEPTH
    result = decode_node("[" * n + "]" * n)
    assert not result.ok
    assert result.error.code != LIMIT_EXCEEDED


def test_still_calls_genuinely_malformed_input_invalid_json() -> None:
    # Non-vacuity for the classification: it must distinguish, not relabel.
    result = decode_node("}{ not json")
    assert not result.ok
    assert result.error.code == INVALID_JSON


def test_refuses_an_over_long_string() -> None:
    doc = json.dumps({"id": "x", "kind": {"$type": "Text", "text": "a" * (MAX_STRING_LENGTH + 1)}})
    result = decode_node(doc)
    assert not result.ok
    assert result.error.code == LIMIT_EXCEEDED


def test_refuses_an_over_long_array() -> None:
    doc = "[" + ",".join(["1"] * (MAX_ARRAY_LENGTH + 1)) + "]"
    result = decode_node(doc)
    assert not result.ok
    assert result.error.code == LIMIT_EXCEEDED


# ── the counters do not leak between calls ──────────────────────────────────
#
# The counters are module-level, so the one way this goes wrong is a walk
# raising part-way and leaving one poisoned for the next caller. The decoders
# decrement in `finally` and the entry points reset; these say so.


def test_a_refused_deep_decode_does_not_poison_the_next() -> None:
    assert not decode_node(nested_nodes(MAX_NODE_DEPTH + 1)).ok
    assert decode_node(nested_nodes(MAX_NODE_DEPTH)).ok


def test_a_shape_failure_does_not_poison_the_next_decode() -> None:
    assert not decode_node('{"id":"x","kind":{"$type":"NoSuchKind"}}').ok
    assert decode_node(nested_nodes(MAX_NODE_DEPTH)).ok


def test_a_refused_op_decode_does_not_poison_the_next() -> None:
    assert not decode_op(nested_batch(MAX_NODE_DEPTH + 1)).ok
    assert decode_op(nested_batch(MAX_NODE_DEPTH)).ok
