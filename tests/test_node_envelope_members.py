"""Phase 1654 — the node envelope's member set, derived from the corpus IDL.

**This file exists because a REFUTATION needed somewhere to live.** Phase 1128
reported that this host's decoder "drops ``motion`` and ``extraAttributes`` from
the §3.1 envelope, the same silent-omission class as the ``tooltip`` drop 1128
fixed", and asked for corpus fixtures that would make it go red on every host.
Measured against the tree, the premise is false in both halves:

* **They are not envelope members.** §3.1 enumerates the envelope as ``id`` and
  ``kind`` plus five optional keys — ``state``, ``style``, ``accessibility``,
  ``tooltip``, ``visible`` — and neither of these is among them. The corpus's own
  ``idl.json`` says so in machine-readable form: both carry
  ``optionality: hostOnly`` and a TypeScript surface of ``never``.
* **The reference host drops them too, deliberately.** Its node decoder
  constructs ``Motion = None; ExtraAttributes = None`` under a comment saying the
  encoder does not emit them. They are a host-side consumer hatch (the ``data-*``
  / ``aria-*`` test-hook map) and a host-side animation record; §4d omits the
  first on emit and the second follows the same convention.

So the analogy to ``tooltip`` does not hold — ``tooltip`` IS a §3.1 member, which
is exactly why dropping it was a defect — no fixture is owed, and dropping these
is the CONFORMANT behaviour rather than a silent omission. Phase 1647 authored no
fixture for them, correctly.

What is genuinely worth having is the assertion that goes red if that changes.
Since Phase 2296 the decoder reads its envelope member set FROM the generated
node fields, and ``schema/decode.py`` refuses to import when the IDL declares a
wire member it has no decoder for (or it decodes one the IDL does not declare),
so the both-directions comparison that lived here was deleted with the hand
literal it compared. The refutation itself stays pinned below.
"""

from __future__ import annotations

from fuaran_ui import decode_node
from fuaran_ui.schema import generated
from fuaran_ui.schema.encode import encode_node


def test_motion_and_extraattributes_are_declared_host_only() -> None:
    """The refutation, pinned against the artefact that settles it.

    If either is ever promoted to the wire this goes red, and at that point the
    original finding becomes correct and this host owes it a decode arm.
    """
    by_name = {f.name: f for f in generated.NODE_FIELDS}
    for name in ("motion", "extraAttributes"):
        assert name in by_name, f"{name!r} has left the IDL's nodeFields entirely — re-read §3.1 before acting"
        assert by_name[name].optionality == "hostOnly", (
            f"{name!r} is no longer declared hostOnly in the corpus IDL, so it is now a WIRE member and this "
            "host must decode it. See this module's docstring: it was refuted as a defect precisely because "
            "the IDL said hostOnly and the reference host drops it for that reason."
        )


def test_a_host_only_member_on_the_wire_is_tolerated_and_ignored() -> None:
    """Not refused, and not carried — the §15 forward-compatibility posture.

    Worth pinning explicitly, because both plausible wrong answers look
    defensible. REFUSING would break forward compatibility over a key the
    specification does not define. CARRYING it would re-encode a member no
    emitter produces, so this host's canonical output would stop being
    byte-identical to every other host's for the same input — which is the one
    property the whole codec is evidence of.
    """
    canonical = '{"id":"badge-1","kind":{"$type":"Badge","label":"Beta","variant":"Info"}}'
    source = (
        '{"extraAttributes":{"data-test":"x"},"id":"badge-1",'
        '"kind":{"$type":"Badge","label":"Beta","variant":"Info"},"motion":"Fade"}'
    )
    decoded = decode_node(source)
    assert decoded.ok, f"a host-only key must not be refused: {getattr(decoded, 'error', decoded)}"

    reencoded = encode_node(decoded.value)
    assert "extraAttributes" not in reencoded
    assert "motion" not in reencoded
    assert reencoded == canonical
