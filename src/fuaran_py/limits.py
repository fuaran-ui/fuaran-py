"""Decode-side resource limits for untrusted wire input (``WIRE_FORMAT.md`` §21).

Why this exists
---------------
§6 promises that decoding is *total* — a malformed or hostile input yields a
structured, typed error, never an exception. That promise held on **semantics**
(every wrong-shaped field is a ``DecodeError``) and was false on **shape**:
``decode_node`` caught ``ValueError`` around ``json.loads``, and CPython raises
``RecursionError`` on deeply nested input, which is not a ``ValueError``. It
escaped the decoder as a throw, so a payload of ``[[[[[…`` — two bytes per level
— was a one-request remote kill for any host decoding untrusted input.

These are this host's expression of the normative limits in §21.1. They are
**protocol** limits, not implementation details: a conformant host MUST refuse a
payload beyond them with a typed ``LIMIT_EXCEEDED`` error rather than a throw,
and MUST accept one within them. Changing a value here is a protocol change — it
moves in ``WIRE_FORMAT.md`` §21 and across every host, never here alone.

Why two depth numbers
---------------------
They are not derivable from each other in either direction. One tree level costs
several JSON levels (a ``Box`` costs three: the node object, its ``children``
array, the child object), and a rule-12 structured payload nests freely *within*
one node and consumes no node depth at all. A host must never report a node-depth
breach as a syntax-depth breach, because that diagnosis sends the author to
repair the wrong thing.

§21.4 records how ``MAX_NODE_DEPTH`` was derived on the reference host, by
bisecting each walk's true overflow depth. The figure is not re-derived per host:
it is a number in the format. A host that measures a *tighter* budget on some
walk of its own bounds that walk under §21.2 rule 5 rather than proposing a
smaller wire limit.
"""

from __future__ import annotations

#: Maximum NODE nesting depth of a wire tree (the root is depth 1). Bounds the
#: structural decoder and — per §21.2 rule 5 — every later walk over a decoded
#: tree. The same figure bounds ``Batch`` nesting in the op decoder: a different
#: axis, counted separately, held to the same ceiling.
MAX_NODE_DEPTH = 24

#: Maximum SYNTACTIC JSON nesting depth (the outermost value is depth 1). Every
#: ``{`` and ``[`` counts, whether it carries a node, a spec, or a rule-12
#: payload.
MAX_JSON_DEPTH = 256

#: Maximum length in characters of a single decoded JSON string.
MAX_STRING_LENGTH = 1048576

#: Maximum number of elements in a single JSON array, and members in a single
#: JSON object.
MAX_ARRAY_LENGTH = 100000

#: Maximum total node count of one document, summed across the whole tree.
#:
#: Needed even once depth is bounded, because the depth, string and array limits
#: together still admit a document that is hostile by being **wide** — 24 levels
#: of 100 000 siblings is within every other limit. Its cost is linear in the
#: input, but the constant is not: a decoded tree is far larger in memory than
#: the bytes that produced it.
MAX_NODES = 100000

# Maximum number of ``ColExpr`` nodes in ONE ``Binding.Expr`` expression
# (WIRE_FORMAT.md §21.8, Phase 1534). Counted per expression, not per document:
# a tree may carry many ``Expr`` bindings, each bounded here, with the whole
# still bounded by ``MAX_DOCUMENT_BYTES``. A breach is ``LIMIT_EXCEEDED`` at the
# path of the ``expr`` member.
#
# ONE count and not a count plus a depth: depth <= node count for every
# expression, so an expression 600 deep is already 600 nodes and already
# refused, and a second number would be one more figure to keep in step across
# the hosts while refusing nothing this one does not.
#
# Its SCOPE is ``Binding.Expr`` and nothing else. A ``ColExpr`` inside a
# ``Binding.Transform`` pipeline is NOT bounded by it, and was not bounded
# before it either - stated rather than left to be inferred, because a limit
# whose scope is guessed at is worse than no limit.
MAX_EXPR_NODES = 512

# Maximum value of ONE ``Skeleton`` node's ``rows`` slot (WIRE_FORMAT.md §21.9,
# Phase 1666). Counted per node, not per document: a tree may carry many
# ``Skeleton`` nodes, each bounded here, with the whole still bounded by
# ``MAX_NODES`` and ``MAX_DOCUMENT_BYTES``. A breach is ``LIMIT_EXCEEDED`` at the
# path of the ``rows`` member.
#
# It is the first bound here that a document breaches with four digits rather
# than with bulk, and §21.8's argument applies more sharply because this is not
# even an evaluation - the rows are simply not present in the input. A
# server-side renderer emits one row of markup per count, so
# ``{"$type":"Skeleton","rows":100000000}`` is a document well inside every other
# limit (a handful of bytes, one node, three JSON levels) that names a hundred
# million rendered rows. Every structural limit is satisfied, and each is
# satisfied because none of them is looking at the value.
#
# §7.1 decides FIRST, and the ORDER is what keeps the two rules apart. §7.1 says
# what a typed integer slot can HOLD, and 2147483647 is finite, fraction-free and
# inside signed 32-bit, so §7.1 admits it; this bound then refuses it for the work
# it names. A non-integer therefore stays ``WRONG_TYPE`` and a 32-bit-valid value
# past the bound is ``LIMIT_EXCEEDED`` - never the reverse. Collapsing the two into
# a narrower integer read would also refuse the at-the-bound document §21.2 rule 1
# obliges every host to accept.
#
# An UPPER bound only. A negative ``rows`` is not a resource breach - nothing
# expands - and reporting one as ``LIMIT_EXCEEDED`` would be the actively-wrong
# diagnosis rule 2 forbids. It is an authoring defect and belongs to the pre-emit
# validator family (``FUARAN152``), which this package does not implement.
MAX_SKELETON_ROWS = 10000
