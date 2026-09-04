"""Sparkline → Drawing lowering — cross-host byte-parity (Phase 1099).

The Python lowering (:func:`fuaran_py.charts.try_lower_sparkline`) must reproduce
the shared ``wire-format-fixtures/sparkline-lowering/*`` goldens byte-for-byte —
the same fixtures the reference host emits and every adopting host certifies
against, the discipline ``chart-lowering/`` already runs on. Each case ships an
``<name>.input.json`` (``{"series": [...]}``, the *resolved* value of the
``Sparkline`` source) and an ``<name>.expected.json`` (the canonical wire JSON of
the ``Drawing`` node, or the literal ``null`` when there is nothing to draw).
Skipped when the corpus is absent (a standalone checkout), mirroring the
chart-lowering pattern.

**The series crosses through the shipped DECODER, never through ``json.loads``
alone.** A fixture's ``series`` is spliced into a real ``Sparkline`` wire document
and decoded with :func:`fuaran_py.decode_node`, then resolved with the renderer's
own binding resolver and float-series reader — so what the lowering is handed
here is what the renderer hands it in production, including the §5/§7 non-finite
sentinels arriving as floats. A harness that built a raw ``list`` itself would
certify the lowering against a shape no decoded tree ever produces, and the
sentinel vector — the whole reason this family exists — would prove nothing. Both
steps assert rather than degrade: an unrecognised shape fails the test loudly
instead of resolving to a default.
"""

from __future__ import annotations

import json

import pytest

from _corpus import CORPUS_ROOT, corpus_available
from fuaran_py import decode_node
from fuaran_py.charts import try_lower_sparkline, try_lower_sparkline_node
from fuaran_py.model import Node, Obj
from fuaran_py.renderer import render_html
from fuaran_py.renderer.bindings import resolve_binding

# The renderer's resolved-source reader — module-private, reached deliberately so
# the harness feeds the lowering through the SAME extractor the render path uses.
from fuaran_py.renderer.render import _float_series
from fuaran_py.schema.encode import encode_node

_SPARKLINE_LOWERING_DIR = CORPUS_ROOT / "sparkline-lowering"


def _cases() -> list[str]:
    if not corpus_available() or not _SPARKLINE_LOWERING_DIR.is_dir():
        return []
    return sorted(p.name[: -len(".input.json")] for p in _SPARKLINE_LOWERING_DIR.glob("*.input.json"))


def _series_of(name: str) -> list[float]:
    """The fixture's series, as the RENDERER would hold it.

    ``json.loads`` of the fixture gives the wire spelling (numbers, and the three
    quoted non-finite sentinels); everything after that is the shipped path.
    """
    raw = json.loads((_SPARKLINE_LOWERING_DIR / f"{name}.input.json").read_text(encoding="utf-8"))
    assert isinstance(raw, dict) and "series" in raw, f"{name}: unexpected input shape {raw!r}"
    wire = json.dumps(
        {
            "id": f"sparkline-{name}",
            "kind": {"$type": "Sparkline", "source": {"$type": "Static", "value": raw["series"]}},
        }
    )
    decoded = decode_node(wire)
    assert decoded.ok, f"{name}: the fixture series is not decodable — {getattr(decoded, 'error', decoded)}"
    node = decoded.value
    assert isinstance(node.kind, Obj) and node.kind.tag == "Sparkline"
    series = _float_series(resolve_binding(node.kind.fields.get("source")))
    assert series is not None, f"{name}: the decoded source did not resolve to a float series"
    return series


def _expected(name: str) -> str:
    return (_SPARKLINE_LOWERING_DIR / f"{name}.expected.json").read_text(encoding="utf-8")


_cases_available = pytest.mark.skipif(
    not _cases(),
    reason=f"sparkline-lowering fixtures not found at {_SPARKLINE_LOWERING_DIR}",
)


@_cases_available
@pytest.mark.parametrize("name", _cases())
def test_lowers_byte_identical_to_golden(name: str) -> None:
    lowered = try_lower_sparkline_node(f"sparkline-{name}", _series_of(name))
    # `null` IS the contract for a case with nothing to draw: the host's fallback
    # is its own em-dash element rather than a `Shape`, so the lowering must not
    # pretend to express it by emitting an empty canvas.
    got = "null" if lowered is None else encode_node(lowered)
    assert got == _expected(name), f"{name}: lowering drifted from golden"


@_cases_available
def test_every_golden_vector_is_exercised() -> None:
    # A parametrized suite that silently collected nothing reads exactly like a
    # passing one. Pin the family's membership so a vector added upstream (or a
    # glob that stopped matching) is a failure rather than a quieter run.
    assert set(_cases()) == {
        "empty",
        "flat",
        "flat-boundary",
        "nonfinite-sentinel",
        "normal",
        "single-point",
        "two-points",
    }


@_cases_available
@pytest.mark.parametrize("name", _cases())
def test_golden_comparison_discriminates(name: str) -> None:
    """The go-red proof: the assertion above must be able to FAIL.

    A byte-equality test whose two sides are both derived from the same file
    passes whatever the lowering does. Here the golden is perturbed by one
    character and the comparison is required to reject it — including for
    ``empty``, whose ``null`` is the easiest contract to satisfy accidentally.
    """
    lowered = try_lower_sparkline_node(f"sparkline-{name}", _series_of(name))
    got = "null" if lowered is None else encode_node(lowered)
    perturbed = _expected(name).replace("0", "9", 1) if "0" in _expected(name) else _expected(name) + " "
    assert perturbed != _expected(name), f"{name}: the perturbation did not change the golden"
    assert got != perturbed, f"{name}: the comparison cannot distinguish a wrong lowering"


def test_empty_and_absent_series_have_nothing_to_draw() -> None:
    # The seam's guard, stated once here rather than repeated in each host arm.
    assert try_lower_sparkline([]) is None
    assert try_lower_sparkline(None) is None
    assert try_lower_sparkline_node("x", []) is None


@_cases_available
@pytest.mark.parametrize("name", [c for c in _cases() if c != "empty"])
def test_ssr_renders_the_golden_geometry(name: str) -> None:
    """The other half: the render arm reaches the lowering, and the SVG it emits
    is the one the golden's own ``Drawing`` node renders.

    The em-dash placeholder this host emitted before Phase 1099 is gone for a
    resolved series, and the ``fuaran-sparkline`` hook stays on the container.
    """
    raw = json.loads((_SPARKLINE_LOWERING_DIR / f"{name}.input.json").read_text(encoding="utf-8"))
    wire = json.dumps(
        {
            "id": f"sparkline-{name}",
            "kind": {"$type": "Sparkline", "source": {"$type": "Static", "value": raw["series"]}},
        }
    )
    decoded = decode_node(wire)
    assert decoded.ok, getattr(decoded, "error", decoded)
    html = render_html(decoded.value)

    golden = decode_node(_expected(name))
    assert golden.ok, f"{name}: the golden is not a decodable Drawing node — {getattr(golden, 'error', golden)}"
    golden_html = render_html(golden.value)
    svg = golden_html[golden_html.index("<svg") : golden_html.rindex("</svg>") + len("</svg>")]

    assert svg in html, f"{name}: the rendered sparkline is not the golden Drawing's SVG"
    assert '<div class="fuaran-sparkline">' in html
    assert "fuaran-sparkline-empty" not in html
    assert "—" not in html


def test_ssr_keeps_the_em_dash_when_the_series_does_not_resolve() -> None:
    # The declared fallback, still the contract for an unresolved or empty series.
    for source in ({"$type": "Static", "value": []}, {"$type": "Query", "name": "unbound"}):
        decoded = decode_node(json.dumps({"id": "s", "kind": {"$type": "Sparkline", "source": source}}))
        assert decoded.ok, getattr(decoded, "error", decoded)
        html = render_html(decoded.value)
        assert "fuaran-sparkline-empty" in html
        assert "—" in html
        assert "<svg" not in html


def test_no_hand_written_sparkline_svg_survives() -> None:
    # The acceptance's third clause, asserted rather than reviewed: the render
    # arm builds no geometry and no SVG of its own — it calls the shared seam and
    # the shared builder. A source-reading test, because "did anyone copy the
    # polyline back in" is a question about the source, not about the output.
    import inspect

    from fuaran_py.renderer.render import Renderer

    # Comments are stripped first: the arm's own commentary NAMES the geometry it
    # no longer builds (a `currentColor` stroke, the retired placeholder), and a
    # check that could not tell prose from code would forbid explaining the
    # change in the place it happened.
    body = "\n".join(
        line for line in inspect.getsource(Renderer._sparkline).splitlines() if not line.lstrip().startswith("#")
    )
    assert "try_lower_sparkline_node" in body, "the render arm must reach the shared lowering"
    assert "_drawing_svg" in body, "…and the shared SVG builder"
    for forbidden in ("<svg", "<polyline", "points", "viewBox", "stroke"):
        assert forbidden not in body, f"the render arm builds its own geometry ({forbidden!r})"


def test_node_envelope_wraps_the_lowered_kind() -> None:
    node = try_lower_sparkline_node("spark-1", [1.0, 2.0, 3.0, 2.0, 4.0])
    assert isinstance(node, Node)
    assert node.id == "spark-1"
    assert isinstance(node.kind, Obj) and node.kind.tag == "Drawing"
