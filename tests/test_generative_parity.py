"""Generative cross-host parity (WIRE_FORMAT.md §11.1 Legs C/D analogue, Stage 5).

The fixed corpus pins named traps; ``hypothesis`` reaches the *generated*
tree-space it can't enumerate. Over arbitrary trees built from the ``fuaran_py.ui``
authoring surface — with values drawn from the **cross-host-safe subspace** (finite
numbers, full-unicode strings) — this asserts the Python codec is self-consistent:

* **decode accepts** every canonically-encoded tree, and
* **within-host idempotence:** ``encode(decode(encode x)) == encode x`` — the
  canonical form is a fixed point, the property the FsCheck / fast-check floors
  assert for the F# and TS hosts (≥1000 cases).

The F# → Py / Py → F# *fuzz-sample exchange* (Legs F/G) needs the F#-side
``--emit-fuzz-samples`` / ``--check-fuzz-samples`` tooling in the ``fuaran`` repo
(outside this host's cross-section) — that cross-repo leg is deferred; this suite
delivers the in-host generative floor.

``hypothesis`` is a dev-only dependency (never imported by ``src/fuaran_py``).
"""

from __future__ import annotations

import pytest

hypothesis = pytest.importorskip("hypothesis", reason="hypothesis (dev extra) drives generative parity")
from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from fuaran_py.schema import decode_node, encode_node  # noqa: E402
from fuaran_py.ui import encode, fuaran  # noqa: E402

# ── The cross-host-safe value subspace (WIRE_FORMAT.md §5) ───────────────────
# Non-empty ids (empty → EMPTY_NODE_ID); strings span quotes / backslash / control
# chars / non-BMP to exercise canonical escaping; numbers are finite (NaN/Infinity
# are not valid wire numbers) and span the plain-decimal and exponent zones.
_ids = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x2FF, blacklist_characters='"\\'),
    min_size=1,
    max_size=8,
)
_text = st.text(max_size=24)
_numbers: st.SearchStrategy[int | float] = st.one_of(
    st.integers(min_value=-(2**53), max_value=2**53),
    st.floats(allow_nan=False, allow_infinity=False, width=64),
)


def _leaf() -> st.SearchStrategy:
    return st.one_of(
        st.builds(fuaran.markdown, id=_ids, body=_text),
        st.builds(fuaran.heading, id=_ids, text=_text, level=st.integers(1, 6)),
        st.builds(lambda i, label, value: fuaran.metric(i, label=label, value=value), _ids, _text, _numbers),
        st.builds(lambda i, label: fuaran.badge(i, label=label), _ids, _text),
        st.builds(lambda i, label, value: fuaran.label_value_row(i, label=label, value=value), _ids, _text, _numbers),
        st.builds(fuaran.math, id=_ids, source=_text),
        st.builds(fuaran.divider, id=_ids),
        st.builds(lambda i, code, lang: fuaran.code_block(i, code=code, language=lang), _ids, _text, _text),
    )


def _tree() -> st.SearchStrategy:
    # Bounded-depth recursion: containers wrap up to a few children.
    return st.recursive(
        _leaf(),
        lambda children: st.one_of(
            st.builds(lambda i, kids: fuaran.dashboard(i, children=kids), _ids, st.lists(children, max_size=4)),
            st.builds(lambda i, kids: fuaran.stack(i, children=kids), _ids, st.lists(children, max_size=4)),
            st.builds(
                lambda i, kids, h: fuaran.card(i, children=kids, heading=h),
                _ids,
                st.lists(children, max_size=3),
                st.one_of(st.none(), _text),
            ),
        ),
        max_leaves=12,
    )


@settings(max_examples=1000, deadline=None)
@given(_tree())
def test_encode_decode_is_idempotent(node: object) -> None:
    wire = encode(node)
    decoded = decode_node(wire)
    assert decoded.ok, f"decode rejected a canonically-encoded tree: {getattr(decoded, 'error', None)}\n{wire}"
    assert encode_node(decoded.value) == wire, "canonical form is not a fixed point of decode∘encode"


@settings(max_examples=250, deadline=None)
@given(_tree())
def test_generated_wire_is_schema_valid(node: object) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    import json
    from pathlib import Path

    schema_path = Path(__file__).resolve().parents[1] / "conformance" / "corpus" / "schema.json"
    if not schema_path.is_file():  # fall back to the authority when the snapshot is absent
        schema_path = Path(__file__).resolve().parents[2] / "wire-format-fixtures" / "schema.json"
    validator = jsonschema.Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8")))

    wire = encode(node)
    errors = sorted(validator.iter_errors(json.loads(wire)), key=lambda e: list(e.absolute_path))
    assert not errors, "generated wire violates schema.json:\n" + "\n".join(
        f"  {list(e.absolute_path) or '$'}: {e.message}" for e in errors
    )
