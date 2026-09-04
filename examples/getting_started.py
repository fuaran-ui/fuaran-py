"""Getting started — the six-lesson tour, in Python.

Six short lessons that between them explain what this language is for. Run the whole
tour, or one lesson at a time::

    python examples/getting_started.py            # all six
    python examples/getting_started.py replay     # just one

**Five of the six need no key, no network and no browser.** Only the last one calls a
model, and only when you supply your own key — so nothing here is unrunnable because
you have not signed up for anything. Everything imports from the standard library and
``fuaran_py``; there is no third-party dependency in this file.

The same six lessons exist for the F# and TypeScript hosts. They are siblings, not
ports: all three are conformant hosts of one wire format, so a tree authored in any of
them is read by all of them.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

from fuaran_py import FunctionEntry, FunctionRegistry, SigEntry, Signature, decode_node, encode_node
from fuaran_py.op_stream import (
    GENESIS_PREVIOUS_HASH,
    SUCCESS,
    Actor,
    AgentActor,
    HumanActor,
    OpRecord,
    ReplayOk,
    actor_id,
    apply_to,
    compute_hash,
    verify_chain,
)
from fuaran_py.ops import apply, decode_op
from fuaran_py.renderer import render_html
from fuaran_py.ui import encode, fuaran
from fuaran_py.ui import format as fmt
from fuaran_py.ui.capability import any_string
from fuaran_py.validator import validate_node

# ─────────────────────────────────────────────────────────────────────────────
#  LESSON 1 — A user interface is a value.
#
#  There is no template language here, and no component to instantiate. You build a
#  typed tree with ordinary functions, and the canonical encoder turns it into JSON
#  that any conformant host can render. Because it is a value, you can hold it in a
#  variable, put it in a list, return it from a function, send it over a socket and
#  compare two of them for equality — none of which is true of a rendered view.
#
#  What to notice in the output: the JSON has no code in it. Not "no code we execute" —
#  no code at all. A tree can carry a `set_state` action or a declarative data
#  pipeline, both of which are DATA the host interprets. It cannot carry a function,
#  which is why an untrusted emission is safe to render (lesson 4).
# ─────────────────────────────────────────────────────────────────────────────


def sales_dashboard():
    """A small sales dashboard. Every constructor is a plain function over typed
    keyword arguments, so a wrong field name fails at the call rather than showing up
    as a blank area on a page."""
    return fuaran.dashboard(
        "sales",
        children=[
            fuaran.heading("sales-title", level=1, text="Q4 sales"),
            fuaran.grid_layout(
                "sales-kpis",
                cols=3,
                children=[
                    fuaran.metric(
                        "sales-revenue",
                        label="Revenue",
                        value=142500,
                        format=fmt.currency("GBP"),
                        tone="Brand",
                    ),
                    fuaran.metric(
                        "sales-orders",
                        label="Orders",
                        value=1284,
                        format=fmt.number(0),
                    ),
                    fuaran.metric(
                        "sales-conversion",
                        label="Conversion",
                        value=0.043,
                        format=fmt.percent(1),
                        tone="Success",
                    ),
                ],
            ),
            fuaran.callout(
                "sales-note",
                tone="Info",
                heading="Where this came from",
                body="This whole page is one value. The JSON below is all a renderer needs.",
            ),
        ],
    )


def lesson_authoring(_argv: list[str]) -> None:
    tree = sales_dashboard()
    print("The tree, as canonical wire JSON:")
    print()
    print(encode(tree))
    print()

    # The encoder is canonical: the same tree always produces the same bytes, with
    # object keys in a fixed order and floats in a pinned format. That is what makes a
    # tree hashable, cacheable, diffable and comparable ACROSS hosts — the property
    # lesson 3 leans on to replay a session exactly.
    print(f"Encoded twice, byte-identical: {encode(tree) == encode(tree)}")

    # And it renders to HTML with no browser and no bundler — the same tree a
    # JavaScript client would draw, drawn here by Python.
    decoded = decode_node(encode(tree))
    html = render_html(decoded.value)
    print(f"Rendered HTML: {len(html)} characters, starting {html[:60]}…")


# ─────────────────────────────────────────────────────────────────────────────
#  LESSON 2 — Edit the tree, don't regenerate it.
#
#  The obvious way to change an AI-authored interface is to ask the model for a new
#  one. It is also the wrong way, for three reasons that have nothing to do with cost:
#  the model may change parts you did not ask about, you cannot say what changed, and
#  you cannot undo it.
#
#  A tree op is the alternative — a typed, addressed edit. "Set the label of
#  sales-revenue to Net revenue" is one op against one node. It applies
#  deterministically, it fails BY NAME when it does not fit, and it is small enough to
#  log, review, reverse and replay (lesson 3).
# ─────────────────────────────────────────────────────────────────────────────


def _op(op: dict[str, object]):
    """An op crosses the wire as JSON, so this is the honest shape: author the op as
    the bytes a model would emit, and let the strict op decoder turn it into a value.
    An op is untrusted input in exactly the way a tree is (lesson 4)."""
    decoded = decode_op(json.dumps(op))
    if not decoded.ok:
        raise SystemExit(f"the sample's own op is malformed: {decoded.error}")
    return decoded.value


RENAME_REVENUE: dict[str, object] = {
    "$type": "UpdateProp",
    "target": "sales-revenue",
    "path": "Label",
    "value": "Net revenue",
}

WARN_ON_REVENUE: dict[str, object] = {
    "$type": "UpdateProp",
    "target": "sales-revenue",
    "path": "Tone",
    "value": "Warning",
}

ADDRESSES_NOTHING: dict[str, object] = {
    "$type": "UpdateProp",
    "target": "no-such-node",
    "path": "Label",
    "value": "…",
}


def lesson_ops(_argv: list[str]) -> None:
    before = decode_node(encode(sales_dashboard())).value

    tree = before
    for op in (RENAME_REVENUE, WARN_ON_REVENUE):
        result = apply(_op(op), tree)
        if not result.ok:
            print(f"unexpected apply failure: {result.error}")
            return
        tree = result.value

    print("Two typed ops applied. What changed:")
    print()
    for b, a in zip(encode_node(before).split("},{"), encode_node(tree).split("},{"), strict=False):
        if b != a:
            print(f"  before: {b}")
            print(f"  after:  {a}")
    print()
    print("Every other node in the tree is byte-identical.")

    # A refusal is a value, not an exception. An orchestrator reads the error, tells
    # the model what was wrong, and asks again — a loop that converges, rather than a
    # crash that needs a human.
    refused = apply(_op(ADDRESSES_NOTHING), before)
    print()
    if refused.ok:
        print("the bad op unexpectedly succeeded")
    else:
        print("An op that addresses nothing is refused by name:")
        print(f"  {refused.error}")


# ─────────────────────────────────────────────────────────────────────────────
#  LESSON 3 — A session is a hash-chained list of ops, and it replays exactly.
#
#  Lesson 2 made an edit addressable. This makes a SESSION reproducible: keep the ops,
#  in order, each carrying the hash of the one before it, and the tree at any point is
#  a fold over a prefix. Three things follow:
#
#    * EXACT REPLAY — not "renders the same", the same tree byte-for-byte under the
#      canonical encoder, which is what makes a bug report reproducible.
#    * TIME TRAVEL FOR FREE — any prefix is a real state, so "what did this look like
#      three edits ago" needs no snapshot machinery.
#    * TAMPER EVIDENCE — each record's hash covers the op, its position, the actor,
#      the timestamp and the outcome, so a record edited after the fact is named.
#
#  Note the actor: every record says whether a human or a model made the edit, and it
#  is inside the hash, so provenance cannot be edited off afterwards.
#
#  AND THE HASHES BELOW ARE NOT PYTHON'S. Run the same lesson in the F# host's tour
#  (`samples/getting-started`, lesson `replay`) and the two chain hashes printed are
#  character for character the ones printed here — the pre-image is a shared,
#  versioned envelope over the canonical op bytes, so a chain written by one host
#  verifies in another. That is the whole reason to have a specification rather than
#  a library.
# ─────────────────────────────────────────────────────────────────────────────

FIXED_TIMESTAMP = 1_767_268_800  # 2026-01-01T12:00:00Z — stable output, run to run.


def _link(stream_id: str, actor: Actor, previous: OpRecord | None, op) -> OpRecord:
    sequence = 1 if previous is None else previous.sequence + 1
    previous_hash = GENESIS_PREVIOUS_HASH if previous is None else previous.hash
    return OpRecord(
        stream_id=stream_id,
        sequence=sequence,
        previous_hash=previous_hash,
        hash=compute_hash(previous_hash, op, sequence, FIXED_TIMESTAMP, actor, None, SUCCESS),
        op=op,
        actor=actor,
        timestamp_unix_seconds=FIXED_TIMESTAMP,
        result_envelope=SUCCESS,
    )


def lesson_replay(_argv: list[str]) -> None:
    seed = decode_node(encode(sales_dashboard())).value

    # Two edits: one a person made, one a model made on their behalf.
    steps = [
        (HumanActor("ada"), _op(RENAME_REVENUE)),
        (AgentActor("some-model", "1", "assistant"), _op(WARN_ON_REVENUE)),
    ]

    records: list[OpRecord] = []
    for actor, op in steps:
        records.append(_link("getting-started", actor, records[-1] if records else None, op))

    replayed = apply_to(seed, records)
    if not isinstance(replayed, ReplayOk):
        print(f"replay failed: {replayed}")
        return

    print(f"Replayed {len(records)} records. Chain:")
    for record in records:
        print(f"  {record.sequence}  {actor_id(record.actor):<28}  {record.hash[:12]}…")

    # Replay is a pure function of (seed, records), so a second run of the same input
    # is the same output. This is the property the whole provenance story rests on.
    again = apply_to(seed, records)
    print()
    print(f"Replayed twice, byte-identical: {encode_node(replayed.value) == encode_node(again.value)}")

    # Any PREFIX is a real state — time travel with no snapshot machinery.
    one_step_back = apply_to(seed, records[:1])
    print(
        "State after 1 of 2 ops differs from the final state: "
        f"{encode_node(one_step_back.value) != encode_node(replayed.value)}"
    )

    # Tamper with a record's op AFTER it was hashed and the chain no longer verifies.
    # Nothing is applied — the replay refuses the whole segment rather than applying
    # the good prefix and stopping.
    tampered = list(records)
    tampered[1] = OpRecord(
        **{
            **tampered[1].__dict__,
            "op": _op({"$type": "UpdateProp", "target": "sales-revenue", "path": "Tone", "value": "Critical"}),
        }
    )
    #
    # NOTE A HOST DIFFERENCE, because it matters and the sample would be lying if it
    # hid it: this host's `apply_to` FOLDS, it does not verify. Verification is a
    # separate call, `verify_chain`, and a host that folds without it has a replay
    # engine but no tamper detection. The F# host's equivalent verifies first and
    # refuses the whole segment. Same chain, same hashes, different default — so on
    # this host, verify explicitly.
    broken = verify_chain(tampered)
    print()
    if broken is None:
        print("the tampered chain unexpectedly verified")
    else:
        print("A record edited after the fact breaks the chain:")
        print(f"  {broken}")
        print()
        print("  `apply_to` on this host folds without verifying, so this check is a")
        print("  separate call. Run it before you trust a stream you did not write.")


# ─────────────────────────────────────────────────────────────────────────────
#  LESSON 4 — Safety is a property of the shape, not of a filter.
#
#  The usual way to make model output safe is to inspect it: scan for script tags,
#  strip attributes, sanitise. That is a losing position, because it asks you to
#  enumerate what is dangerous.
#
#  Here the argument runs the other way. The wire format can express a closed set of
#  node kinds with typed fields — and executable code is not one of them, so there is
#  nothing to strip. An emission is either a well-formed tree from that closed
#  vocabulary or it is REFUSED, and the refusal says which field, at which path, and
#  what was expected.
#
#  Two gates, answering different questions: the DECODER asks "is this a tree at all",
#  and the VALIDATOR asks "is this tree coherent" — decodable, and still not something
#  you want to render.
# ─────────────────────────────────────────────────────────────────────────────

REFUSALS: list[tuple[str, str]] = [
    (
        "a node kind that does not exist",
        '{"id":"x","kind":{"$type":"ScriptBlock","code":"alert(1)"}}',
    ),
    (
        "a required field left out",
        '{"id":"x","kind":{"$type":"Metric","label":"Revenue"}}',
    ),
    (
        "a field of the wrong type",
        '{"id":"x","kind":{"$type":"Heading","level":"one","text":"Hi"}}',
    ),
    (
        "an attempt to smuggle markup through a text field",
        '{"id":"x","kind":{"$type":"Heading","level":1,"text":{"$type":"Html","raw":"<script>alert(1)</script>"}}}',
    ),
]


def lesson_safety(_argv: list[str]) -> None:
    good = encode(sales_dashboard())
    decoded = decode_node(good)
    if decoded.ok:
        print(f"A well-formed emission decodes. ({len(good)} bytes)")
    else:
        print(f"unexpected: the good emission failed to decode: {decoded.error}")

    print()
    print("And these do not:")
    print()
    for what, wire in REFUSALS:
        result = decode_node(wire)
        if result.ok:
            print(f"  {what:<46} ACCEPTED — this is a defect, please report it")
        else:
            print(f"  {what:<46} refused: {result.error}")

    # Note what did NOT happen: nothing was sanitised, no allow-list was consulted, and
    # no string was inspected for dangerous content. The last case fails for the same
    # structural reason as the others — `Html` is not in the closed text vocabulary —
    # not because anything recognised `<script>`.
    print()
    print("Nothing above was sanitised. There is no code case in the vocabulary to strip.")

    # The second gate. This tree decodes perfectly and is still incoherent: two
    # switch cases match the same value, so the second branch can never render. A
    # user experiences that as "the app ignores one of my options"; a developer
    # never sees it in a log, because nothing failed.
    dead_branch = fuaran.dashboard(
        "dead-branch",
        children=[
            fuaran.switch(
                "mode",
                state_key="mode",
                cases=[
                    ("calm", fuaran.markdown("calm", "Quiet tones, generous spacing.")),
                    ("calm", fuaran.markdown("calm-again", "This branch can never render.")),
                ],
                default=fuaran.markdown("neither", "_Pick a mode._"),
            )
        ],
    )
    findings = validate_node(decode_node(encode(dead_branch)).value)
    print()
    if not findings:
        print("the incoherent tree unexpectedly validated")
    else:
        print("A tree can decode and still be incoherent. The validator:")
        for finding in findings:
            print(f"  {finding.code} at {finding.path}")
            print(f"    {finding.message}")


# ─────────────────────────────────────────────────────────────────────────────
#  LESSON 5 — Declare the operations, and most prompts stop needing a model.
#
#  THE CONTRAST IS THE LESSON, so read the two halves of the output side by side.
#
#  A control can PUBLISH the operations it supports: each one a name, a typed signature
#  of holes, and a declared effect. That declaration is data, so it can be searched.
#  Ask "what can I run with the context I have" and you get an answer computed by
#  structural matching over the registry — deterministic, total, in memory, offline,
#  and identical on every host and every run. No model call. No network.
#
#  The model is then reserved for what genuinely needs judgement. That is a much
#  smaller job, and — because the operations are typed — its output is checkable
#  before it runs.
#
#  What no sample can show you is a bank LEARNED from a corpus of real sessions. That
#  is not part of the open language tier, and its absence is deliberate.
# ─────────────────────────────────────────────────────────────────────────────


def _text_hole(addr: str, name: str) -> SigEntry:
    return SigEntry(addr=addr, name=name, kind="value", space=any_string(), required=True)


def _declared_bank() -> tuple[FunctionRegistry, dict[str, str]]:
    registry = FunctionRegistry()
    titles: dict[str, str] = {}

    for entry_id, title, result_type, holes in [
        ("sample.kpi-tile", "KPI tile", "Metric", [_text_hole("kpi.label", "label"), _text_hole("kpi.value", "value")]),
        (
            "sample.notice",
            "Notice banner",
            "Callout",
            [_text_hole("notice.heading", "heading"), _text_hole("notice.body", "body")],
        ),
    ]:
        registry = registry.register(
            FunctionEntry(
                id=entry_id,
                result_type=result_type,
                signature=Signature(name=title, holes=tuple(holes), effect="pureDeterministic"),
            )
        )
        titles[entry_id] = title

    return registry, titles


def _build_kpi(label: str, value: float):
    return fuaran.metric("kpi", label=label, value=value, format=fmt.currency("GBP"), tone="Brand")


def lesson_operations(_argv: list[str]) -> None:
    registry, titles = _declared_bank()

    print("WITHOUT a model — a structural search over what is declared.")
    print()

    context = [_text_hole("kpi.label", "label"), _text_hole("kpi.value", "value")]
    runnable = registry.find_by_signature(context, None)
    print("  Context: a label and a value.")
    print(f"  Runnable right now: {', '.join(titles[e.id] for e in runnable)}")

    want_callout = registry.find_by_signature(
        [_text_hole("notice.heading", "heading"), _text_hole("notice.body", "body")], "Callout"
    )
    print(f"  Asking specifically for a Callout: {', '.join(titles[e.id] for e in want_callout)}")

    if runnable:
        print()
        print(f"  Dispatched {runnable[0].id} -> {encode(_build_kpi('Net revenue', 142500))}")

    print()
    print("WITH a model — for the request no declaration covers.")
    print()
    print('  "Show me last quarter\'s revenue as a KPI"')
    print("     -> the search above answers this. Deterministic, offline, no key.")
    print()
    print('  "Rework this page so a colour-blind reader can still tell the')
    print('   at-risk workstreams from the healthy ones, and explain why"')
    print("     -> no declaration covers that. It needs judgement, and it is")
    print("        exactly the kind of request worth paying a model for.")
    print()
    print("  The point is not that models are unnecessary. It is that most requests")
    print("  in a real application are the first kind, and answering those by search")
    print("  rather than by generation makes them instant, free, offline and repeatable.")


# ─────────────────────────────────────────────────────────────────────────────
#  LESSON 6 — Bring your own key: prompt, decode, render.
#
#      prompt -> the model emits wire JSON -> DECODE STRICTLY -> render
#
#  The middle step is the one that matters. The model's output is untrusted text; the
#  decoder turns it into a typed tree or refuses it by name (lesson 4). Nothing between
#  those two points inspects the string for danger, because by the time you hold a tree
#  there is nothing dangerous left to find.
#
#  This lesson needs your own key, and it is the only one that does. There is no SDK:
#  `urllib` from the standard library, and one JSON body.
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = (
    "You emit user interfaces as canonical Fuaran wire-format JSON and nothing else. "
    'A node is {"id":"…","kind":{"$type":"…",…}}. Useful kinds: Box (role Dashboard or '
    "Card, with children), Heading (level, text), Metric (label, value "
    '{"$type":"Static","value":n}, format {"$type":"Currency","code":"GBP"} or '
    '{"$type":"Number","decimals":0}), Markdown (text), Callout (tone '
    "Info|Success|Warning|Critical, body). Text fields are plain JSON strings. Reply "
    "with ONE JSON object and no prose, no explanation and no code fence."
)

PROMPT = (
    "A dashboard for a small bookshop: this month's revenue in pounds, books sold, "
    "and a short note welcoming the reader."
)


def _key_from(argv: list[str]) -> str | None:
    for flag, value in zip(argv, argv[1:], strict=False):
        if flag == "--key":
            return value
    return os.environ.get("ANTHROPIC_API_KEY") or None


def _first_json_object(text: str) -> str | None:
    """Pull the first balanced JSON object out of a reply, so a model that wraps its
    answer in a sentence or a code fence still works. This is presentation tolerance,
    NOT safety tolerance — whatever comes out still faces the strict decoder."""
    start = text.find("{")
    if start < 0:
        return None
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
        elif ch == "\\" and in_string:
            escaped = True
        elif ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def lesson_ai(argv: list[str]) -> None:
    key = _key_from(argv)
    if key is None:
        print("No key, so no call was made.")
        print()
        print("  Set ANTHROPIC_API_KEY (or pass --key <k>) and re-run to see the whole loop:")
        print("  prompt -> emitted wire JSON -> strict decode -> rendered HTML.")
        print()
        print("  The key is read from this process's environment, sent to the provider you")
        print("  chose, and nothing else. This script stores nothing and logs nothing.")
        print()
        print("  The prompt it would send:")
        print(f"    {PROMPT}")
        return

    print(f"Prompt: {PROMPT}")
    print()

    body = json.dumps(
        {
            "model": "claude-sonnet-4-5",
            "max_tokens": 2000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": PROMPT}],
        }
    ).encode()

    request = urllib.request.Request(  # noqa: S310 — a fixed https endpoint, not user input
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        print(f"The call failed: {exc.code} {exc.read().decode(errors='replace')[:300]}")
        return
    except OSError as exc:
        print(f"The call failed: {exc}")
        return

    reply = "".join(block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text")
    wire = _first_json_object(reply)
    if wire is None:
        print("The model replied with no JSON object at all:")
        print(f"  {reply[:300]}")
        return

    print(f"Emitted {len(wire)} bytes of wire JSON.")
    print()

    # THE GATE. Everything before this is untrusted text.
    decoded = decode_node(wire)
    if not decoded.ok:
        print("Refused by the strict decoder — and this is the system working:")
        print(f"  {decoded.error}")
        print()
        print("  A real orchestrator hands that error back to the model and asks again.")
        print("  The error names the path and the expectation, so the second attempt")
        print("  usually lands. Nothing was rendered, and nothing had to be sanitised.")
        return

    print("Decoded. Rendering it, with no browser:")
    print()
    print(render_html(decoded.value))


# ─────────────────────────────────────────────────────────────────────────────

LESSONS: list[tuple[str, str, object]] = [
    ("authoring", "A user interface is a value", lesson_authoring),
    ("ops", "Edit the tree, don't regenerate it", lesson_ops),
    ("replay", "A session replays exactly", lesson_replay),
    ("safety", "Safety is a property of the shape", lesson_safety),
    ("operations", "Declared operations need no model", lesson_operations),
    ("ai", "Bring your own key: prompt, decode, render", lesson_ai),
]


def main(argv: list[str]) -> int:
    # This file's prose uses em dashes and box-drawing rules, and a Windows console
    # still defaults to a legacy code page — which would end the tour in a
    # UnicodeEncodeError before the first lesson ran. Ask for UTF-8 and carry on if
    # the stream does not support the request.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

    names = {name for name, _, _ in LESSONS}
    requested = [a for a in argv if a in names]
    selected = LESSONS if not requested else [le for le in LESSONS if le[0] in requested]

    if not selected:
        print("No such lesson. Available:")
        for name, title, _ in LESSONS:
            print(f"  {name:<12} {title}")
        return 1

    for name, title, run in selected:
        heading = f"{name} — {title}"
        print()
        print(f"══ {heading} {'═' * max(1, 66 - len(heading))}")
        print()
        run(argv)  # type: ignore[operator]

    print()
    print(f"══ done {'═' * 61}")
    print()
    print("Next: examples/quickstart_reactive_data_app.py for the Compute layer, and")
    print("examples/notebook_display.ipynb for the same tree inside a notebook.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
