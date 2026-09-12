"""WIRE_FORMAT.md §21.7 — the total-document byte ceiling.

The vector is HOST-LOCAL by design and not a corpus fixture, matching the Go
host's reading: committing 32 MiB of padding to a shared repository to assert one
integer comparison is a poor trade, and unlike the depth bounds this is not a
recursion hazard. So this module IS this host's conformance evidence for §21.7 —
which is why the at-the-ceiling half and the probe-verification half are here
beside the refusal, rather than the refusal alone.

Two of these tests exist only on this host. §21.7 is stated in BYTES and a Python
``str`` is code points, so "is this document too big" and "is this string too
long" are genuinely different measurements here, where on the Go and Rust hosts
``len()`` answers both. A ceiling that counted characters would refuse a
multi-byte document a quarter under the limit, and accept one four times over it.
"""

from __future__ import annotations

from fuaran_ui.limits import MAX_DOCUMENT_BYTES
from fuaran_ui.ops.decode import decode_op
from fuaran_ui.result import LIMIT_EXCEEDED
from fuaran_ui.schema.decode import decode_node

_PREFIX = '{"id":"n","kind":{"$type":"Markdown","text":"'
_SUFFIX = '"}}'


def _document_of_bytes(n: int, pad: str = "a") -> str:
    """A syntactically valid node document padded to exactly ``n`` UTF-8 bytes.

    The padding goes inside a STRING literal rather than being whitespace,
    deliberately: the ceiling is checked before the parser runs, so whitespace
    would exercise the same comparison — but a string keeps the document one a
    parser would otherwise have to walk, which is the cost §21.7 exists to refuse
    up front.
    """
    per = len(pad.encode("utf-8"))
    fixed = len(_PREFIX) + len(_SUFFIX)
    assert (n - fixed) % per == 0, "the padding character does not divide the requested size"
    doc = _PREFIX + pad * ((n - fixed) // per) + _SUFFIX
    assert len(doc.encode("utf-8")) == n
    return doc


def test_refuses_a_document_one_byte_past_the_ceiling() -> None:
    result = decode_node(_document_of_bytes(MAX_DOCUMENT_BYTES + 1))
    assert not result.ok
    assert result.error.code == LIMIT_EXCEEDED
    # The breach is a property of the DOCUMENT, so the path is the root — there is
    # no position inside it to name, nothing having been parsed.
    assert result.error.path == "$"
    assert "MAX_DOCUMENT_BYTES" in result.error.message


def test_refuses_an_over_ceiling_op_document_too() -> None:
    # Every public entry point, because every one of them allocates. The check
    # lives at the single parse choke point (`shapeguard.load_bounded`) precisely
    # so that a ceiling on the node reader is not a ceiling on nine other readers.
    result = decode_op(_document_of_bytes(MAX_DOCUMENT_BYTES + 1))
    assert not result.ok
    assert result.error.code == LIMIT_EXCEEDED
    assert result.error.path == "$"
    assert "MAX_DOCUMENT_BYTES" in result.error.message


def test_a_document_at_exactly_the_ceiling_is_not_refused_for_its_size() -> None:
    # The at-the-limit half, which is the half a refusal-only suite passes while
    # enforcing the ceiling one byte too tightly. This document's string is far
    # past §21.6, so it IS refused — but by the string bound rather than by the
    # size ceiling, and the two are told apart by the message, since both report
    # at "$".
    result = decode_node(_document_of_bytes(MAX_DOCUMENT_BYTES))
    assert not result.ok
    assert "MAX_DOCUMENT_BYTES" not in result.error.message, (
        f"a document AT the ceiling was refused as an over-size document: {result.error.message}"
    )
    # ...and it did reach the shape walk, which is what "the size check did not
    # fire" means operationally.
    assert "MAX_STRING_LENGTH" in result.error.message


def test_the_ceiling_counts_utf8_bytes_and_not_code_points() -> None:
    # A document of multi-byte characters that is UNDER the limit in code points
    # and OVER it in bytes. A host measuring `len(str)` accepts this.
    doc = _document_of_bytes(MAX_DOCUMENT_BYTES + 2, pad="é")  # 2 bytes each
    assert len(doc) < MAX_DOCUMENT_BYTES
    result = decode_node(doc)
    assert not result.ok
    assert result.error.code == LIMIT_EXCEEDED
    assert "MAX_DOCUMENT_BYTES" in result.error.message


def test_a_multibyte_document_under_the_ceiling_is_not_refused_for_its_size() -> None:
    # The converse, and the reason the check brackets rather than rounding up: a
    # host that multiplied the code-point count by four would refuse this one.
    doc = _document_of_bytes(MAX_DOCUMENT_BYTES - 2, pad="é")
    result = decode_node(doc)
    assert not result.ok  # by §21.6, as above
    assert "MAX_DOCUMENT_BYTES" not in result.error.message


def test_an_ordinary_document_is_unaffected_by_the_ceiling() -> None:
    # Verify the probe: the ceiling must not be reachable by an ordinary tree, or
    # every test above would pass on a host that refused everything.
    result = decode_node('{"id":"n","kind":{"$type":"Markdown","text":"hello"}}')
    assert result.ok
