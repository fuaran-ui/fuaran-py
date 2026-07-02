"""The generation-endpoint client: contract lock + turn loop.

The wire mapping is the contract lock — these tests pin the request body keys,
the status → result discrimination, and the tolerant response parsing against
the surface contract, so a drift on either side fails here before it fails in
an integration. The client/session tests run against an injected fake
transport (the mock endpoint); `test_client_mock_endpoint.py` exercises the
real default transport against a live in-process HTTP server.
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from fuaran_py.client import (
    SURFACE_VERSION,
    AccessDenied,
    AppliedOp,
    FuaranClient,
    FuaranSession,
    Produced,
    TurnFailed,
    is_surface_version_compatible,
    parse_turn_response,
    to_wire_body,
)
from fuaran_py.ui import encode, fuaran, node

TREE_JSON = encode(node.bare(fuaran.markdown("md-1", "hello")))
OP_JSON = '{"$type": "RemoveNode", "target": "md-1"}'


# ── to_wire_body: the canonical request keys ─────────────────────────────────


def test_wire_body_minimal_is_prompt_only() -> None:
    assert to_wire_body("a metric card") == {"Prompt": "a metric card"}


def test_wire_body_carries_every_supplied_field() -> None:
    body = to_wire_body(
        "rename it",
        current_tree_json=TREE_JSON,
        byok_key="sk-key",
        access_token="tok",
        disable_corpus_read=True,
        contribute_corpus=False,
    )
    assert body == {
        "Prompt": "rename it",
        "CurrentTreeJson": TREE_JSON,
        "ByokKey": "sk-key",
        "AccessToken": "tok",
        "DisableCorpusRead": True,
        "ContributeCorpus": False,
    }


def test_wire_body_omits_absent_fields_rather_than_sending_null() -> None:
    body = to_wire_body("p", access_token="tok")
    assert "CurrentTreeJson" not in body
    assert "ByokKey" not in body
    assert "DisableCorpusRead" not in body
    assert "ContributeCorpus" not in body


# ── parse_turn_response: status → typed result ───────────────────────────────


def test_200_parses_produced_with_ops_and_version_echo() -> None:
    body = json.dumps(
        {
            "TreeJson": TREE_JSON,
            "Ops": [{"OpId": "op-1", "OpJson": OP_JSON}],
            "Version": "1.2.0",
        }
    )
    result = parse_turn_response(200, body)
    assert isinstance(result, Produced)
    assert result.tree_json == TREE_JSON
    assert result.ops == (AppliedOp(op_id="op-1", op_json=OP_JSON),)
    assert result.version == "1.2.0"
    assert is_surface_version_compatible(result.version)


def test_200_parses_camel_case_alias_keys() -> None:
    body = json.dumps({"treeJson": TREE_JSON, "ops": [{"opId": "a", "opJson": OP_JSON}], "version": "1.1.0"})
    result = parse_turn_response(200, body)
    assert isinstance(result, Produced)
    assert result.tree_json == TREE_JSON
    assert result.ops[0].op_id == "a"


def test_200_tolerates_missing_ops_and_malformed_entries() -> None:
    result = parse_turn_response(200, json.dumps({"TreeJson": TREE_JSON, "Ops": ["junk", None, 3], "Version": "1.2.0"}))
    assert isinstance(result, Produced)
    assert result.ops == ()


def test_401_parses_access_denied() -> None:
    result = parse_turn_response(401, json.dumps({"Reason": "token expired"}))
    assert result == AccessDenied(reason="token expired")


def test_401_with_empty_body_defaults_the_reason() -> None:
    assert parse_turn_response(401, "") == AccessDenied(reason="access denied")


def test_422_parses_flat_envelope() -> None:
    result = parse_turn_response(422, json.dumps({"Stage": "apply", "Code": "APPLY_REJECTED", "Message": "no node"}))
    assert isinstance(result, TurnFailed)
    assert result.error.stage == "apply"
    assert result.error.code == "APPLY_REJECTED"
    assert result.error.message == "no node"


def test_422_parses_error_nested_envelope() -> None:
    body = json.dumps({"Error": {"stage": "parse", "code": "BAD_EMISSION", "message": "unparseable"}})
    result = parse_turn_response(422, body)
    assert isinstance(result, TurnFailed)
    assert result.error.stage == "parse"
    assert result.error.code == "BAD_EMISSION"


def test_422_unknown_stage_falls_back_to_provider() -> None:
    result = parse_turn_response(422, json.dumps({"Stage": "quantum", "Code": "X", "Message": "m"}))
    assert isinstance(result, TurnFailed)
    assert result.error.stage == "provider"


def test_unexpected_status_surfaces_as_provider_stage_http_failure() -> None:
    result = parse_turn_response(503, "service unavailable")
    assert isinstance(result, TurnFailed)
    assert result.error.stage == "provider"
    assert result.error.code == "HTTP_503"
    assert "service unavailable" in result.error.message


def test_unexpected_status_with_empty_body_still_carries_a_message() -> None:
    result = parse_turn_response(500, "")
    assert isinstance(result, TurnFailed)
    assert result.error.message == "unexpected status 500"


# ── typed decode access via the wire codec ───────────────────────────────────


def test_produced_decode_tree_yields_the_typed_node() -> None:
    produced = Produced(tree_json=TREE_JSON, ops=(), version=SURFACE_VERSION)
    decoded = produced.decode_tree()
    assert decoded.ok
    assert decoded.value.id == "md-1"


def test_applied_op_decode_yields_the_typed_op() -> None:
    decoded = AppliedOp(op_id="op-1", op_json=OP_JSON).decode()
    assert decoded.ok
    assert decoded.value.tag == "RemoveNode"


def test_surface_version_compatibility_is_a_major_version_check() -> None:
    assert is_surface_version_compatible("1.0.0")
    assert is_surface_version_compatible("1.9.3")
    assert not is_surface_version_compatible("2.0.0")
    assert not is_surface_version_compatible("")


# ── FuaranClient against a fake transport ────────────────────────────────────


class FakeTransport:
    """Records each request and replays a scripted (status, body) response."""

    def __init__(self, responses: list[tuple[int, str]]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, str]:
        self.requests.append((url, dict(headers), json.loads(body.decode("utf-8"))))
        return self.responses.pop(0)


def _produced_body(tree_json: str, version: str = "1.2.0") -> str:
    return json.dumps({"TreeJson": tree_json, "Ops": [], "Version": version})


def test_generate_round_trips_the_contract() -> None:
    transport = FakeTransport([(200, _produced_body(TREE_JSON))])
    client = FuaranClient(
        "https://example.test/generate", access_token="tok", provider_key="sk-key", transport=transport
    )

    result = client.generate("a metric card", disable_corpus_read=True)

    assert isinstance(result, Produced)
    url, headers, body = transport.requests[0]
    assert url == "https://example.test/generate"
    assert headers["content-type"] == "application/json"
    assert headers["authorization"] == "Bearer tok"
    assert body == {
        "Prompt": "a metric card",
        "ByokKey": "sk-key",
        "AccessToken": "tok",
        "DisableCorpusRead": True,
    }


def test_generate_per_call_credentials_override_the_config() -> None:
    transport = FakeTransport([(200, _produced_body(TREE_JSON))])
    client = FuaranClient(
        "https://example.test/generate", access_token="cfg-tok", provider_key="cfg-key", transport=transport
    )

    client.generate("p", access_token="call-tok", provider_key="call-key")

    _, headers, body = transport.requests[0]
    assert body["AccessToken"] == "call-tok"
    assert body["ByokKey"] == "call-key"
    assert headers["authorization"] == "Bearer call-tok"


def test_generate_server_proxied_pattern_sends_no_credentials() -> None:
    transport = FakeTransport([(200, _produced_body(TREE_JSON))])
    client = FuaranClient("/api/fuaran", transport=transport)

    client.generate("p")

    _, headers, body = transport.requests[0]
    assert "ByokKey" not in body
    assert "AccessToken" not in body
    assert "authorization" not in headers


def test_generate_bearer_header_is_suppressible() -> None:
    transport = FakeTransport([(200, _produced_body(TREE_JSON))])
    client = FuaranClient("https://example.test/g", access_token="tok", send_bearer_header=False, transport=transport)

    client.generate("p")

    _, headers, body = transport.requests[0]
    assert "authorization" not in headers
    assert body["AccessToken"] == "tok"


def test_generate_transport_error_surfaces_as_network_turn_failure() -> None:
    def exploding(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, str]:
        raise RuntimeError("connection refused")

    client = FuaranClient("https://example.test/g", transport=exploding)
    result = client.generate("p")

    assert isinstance(result, TurnFailed)
    assert result.error.stage == "provider"
    assert result.error.code == "NETWORK"
    assert "connection refused" in result.error.message


def test_client_requires_an_endpoint() -> None:
    try:
        FuaranClient("   ")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a blank endpoint")


# ── no-key-leak posture ──────────────────────────────────────────────────────


def test_byok_key_appears_in_no_repr_header_or_error_message() -> None:
    sentinel = "sk-SENTINEL-DO-NOT-LEAK"

    def exploding(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, str]:
        # The key travels ONLY in the request body — never in a header.
        assert all(sentinel not in v for v in headers.values())
        raise RuntimeError("boom")

    client = FuaranClient("https://example.test/g", access_token="tok", provider_key=sentinel, transport=exploding)
    result = client.generate("p")

    assert sentinel not in repr(client)
    assert isinstance(result, TurnFailed)
    assert sentinel not in result.error.message


# ── FuaranSession: the turn loop carries the tree forward ────────────────────


def test_session_first_turn_is_fresh_then_repairs_against_the_produced_tree() -> None:
    second_tree = encode(node.bare(fuaran.markdown("md-1", "renamed")))
    transport = FakeTransport([(200, _produced_body(TREE_JSON)), (200, _produced_body(second_tree))])
    session = FuaranSession(FuaranClient("https://example.test/g", transport=transport))

    assert session.current_tree_json is None
    first = session.next("a metric card")
    assert isinstance(first, Produced)
    assert session.current_tree_json == TREE_JSON

    second = session.next("rename it")
    assert isinstance(second, Produced)
    # The second request carried the first turn's tree — a repair, not a regeneration.
    assert transport.requests[0][2].get("CurrentTreeJson") is None
    assert transport.requests[1][2]["CurrentTreeJson"] == TREE_JSON
    assert session.current_tree_json == second_tree


def test_session_holds_the_tree_across_a_failed_turn() -> None:
    transport = FakeTransport(
        [
            (200, _produced_body(TREE_JSON)),
            (422, json.dumps({"Stage": "apply", "Code": "APPLY_REJECTED", "Message": "no node"})),
            (401, json.dumps({"Reason": "expired"})),
        ]
    )
    session = FuaranSession(FuaranClient("https://example.test/g", transport=transport))

    session.next("build it")
    session.next("bad repair")
    session.next("another")

    # Both failures left the held tree unchanged, so the caller can retry.
    assert session.current_tree_json == TREE_JSON
    assert transport.requests[2][2]["CurrentTreeJson"] == TREE_JSON


def test_session_seeds_from_an_initial_tree_and_resets() -> None:
    transport = FakeTransport([(200, _produced_body(TREE_JSON))])
    session = FuaranSession(
        FuaranClient("https://example.test/g", transport=transport),
        initial_tree_json=TREE_JSON,
    )

    session.next("tweak it")
    # Seeded ⇒ the very first turn is already a repair.
    assert transport.requests[0][2]["CurrentTreeJson"] == TREE_JSON

    session.reset()
    assert session.current_tree_json is None
