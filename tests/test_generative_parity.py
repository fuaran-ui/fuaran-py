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
from fuaran_py.schema import types as t  # noqa: E402
from fuaran_py.ui import action, binding, encode, fuaran  # noqa: E402
from fuaran_py.ui import node as node_ops  # noqa: E402 — `node` is a test parameter name below

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


# ── Phase 1579 — the geometry / statement / boundary kinds ───────────────────
#
# `Drawing` is the interesting one here: it is the only kind whose payload is a
# RECURSIVE closed DU of its own (`Shape.Group` nests shapes, `Curve` nests path
# commands), so its generated space is not reachable from the node recursion
# above. `Fact` and `Mount` are shallow, and are included because a kind that is
# only ever exercised by its curated fixtures is exercised by the author's
# imagination.
_shape = st.recursive(
    st.one_of(
        st.builds(lambda x, y, w, h: t.Rectangle(x, y, w, h), _numbers, _numbers, _numbers, _numbers),
        st.builds(lambda a, b, c, d: t.Line(a, b, c, d), _numbers, _numbers, _numbers, _numbers),
        st.builds(lambda x, y, r: t.Circle(x, y, r), _numbers, _numbers, _numbers),
        st.builds(
            lambda pts: t.Polyline(tuple(t.DrawPoint(x, y) for x, y in pts)),
            st.lists(st.tuples(_numbers, _numbers), max_size=4),
        ),
        st.builds(
            lambda x, y, text, anchor: t.Label(x, y, t.LiteralText(text), style=t.DrawStyle(text_anchor=anchor)),
            _numbers,
            _numbers,
            _text,
            st.sampled_from(["Start", "Middle", "End"]),
        ),
        st.builds(
            lambda to: t.Curve((t.MoveTo(t.DrawPoint(*to)), t.LineTo(t.DrawPoint(*to)), t.Close())),
            st.tuples(_numbers, _numbers),
        ),
    ),
    lambda children: st.builds(lambda kids: t.Group(tuple(kids)), st.lists(children, max_size=3)),
    max_leaves=6,
)


def _leaf() -> st.SearchStrategy:
    return st.one_of(
        st.builds(fuaran.markdown, id=_ids, body=_text),
        st.builds(
            lambda i, box, shapes: fuaran.drawing(i, view_box=t.ViewBox(*box), shapes=shapes),
            _ids,
            st.tuples(_numbers, _numbers, _numbers, _numbers),
            st.lists(_shape, max_size=4),
        ),
        st.builds(
            lambda i, label, value, tone, emphasis: fuaran.fact(
                i, label=label, value=value, tone=tone, emphasis=emphasis
            ),
            _ids,
            _text,
            _text,
            st.sampled_from(["Default", "Brand", "Critical"]),
            st.booleans(),
        ),
        st.builds(
            lambda i, scope, caps, direction, on_bubble: fuaran.mount(
                i,
                scope_id=scope,
                capabilities=caps,
                channel=t.GuestChannel(direction),
                on_bubble=on_bubble,
            ),
            _ids,
            _text,
            st.lists(_text, max_size=3),
            st.sampled_from(["OutOnly", "TwoWay"]),
            st.booleans(),
        ),
        # Phase 1580 — the five binding / action / text-source cases the typed
        # unions gained. Reached HERE and not by the node recursion, because they
        # are not kinds: nothing in the tree walk above generates a `Query`
        # source, an `Invoke` in either position, a `Call`'s three result
        # spellings, an `AiTool` payload or an `I18n` bag, so without these arms
        # the floor would be silent about every one of them.
        st.builds(
            lambda i, label, name, deps: fuaran.metric(i, label=label, value=binding.query(name, *deps)),
            _ids,
            _text,
            _text,
            st.lists(_text, max_size=3),
        ),
        st.builds(
            lambda i, label, cap, arg: fuaran.metric(i, label=label, value=binding.invoke(cap, addr=arg)),
            _ids,
            _text,
            _text,
            _text,
        ),
        st.builds(
            lambda i, alt, key, year: fuaran.image(i, src="/x.png", alt=alt, caption=binding.i18n(key, year=year)),
            _ids,
            _text,
            _text,
            _numbers,
        ),
        st.builds(
            lambda i, label, key: node_ops.with_tooltip(binding.i18n(key), fuaran.markdown(i, label)),
            _ids,
            _text,
            _text,
        ),
        st.builds(
            lambda i, label, endpoint, into: fuaran.button(i, label=label, on_click=action.call(endpoint, into=into)),
            _ids,
            _text,
            _text,
            st.one_of(
                st.none(),
                st.builds(action.into_state, _text),
                st.builds(action.into_query, _text),
            ),
        ),
        st.builds(
            lambda i, label, endpoint: fuaran.button(i, label=label, on_click=action.call(endpoint, on_result=True)),
            _ids,
            _text,
            _text,
        ),
        st.builds(
            lambda i, label, tool, args: fuaran.button(i, label=label, on_click=action.ai_tool(tool, args)),
            _ids,
            _text,
            _text,
            st.dictionaries(_text, st.one_of(_numbers, _text, st.booleans()), max_size=3),
        ),
        st.builds(
            lambda i, label, cap, arg: fuaran.button(i, label=label, on_click=action.invoke(cap, addr=arg)),
            _ids,
            _text,
            _text,
            _text,
        ),
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
