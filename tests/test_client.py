"""The generation-endpoint client: contract lock + turn loop + hardening.

The wire mapping is the contract lock — these tests pin the request body keys,
the status → result discrimination, and the tolerant response parsing against
the DEPLOYED endpoint shape, so a drift on either side fails here before it
fails in an integration. The client/session tests run against an injected fake
transport (the mock endpoint); ``test_client_mock_endpoint.py`` exercises the
real default transport against a live in-process HTTP server.

The wire this pins was corrected: the client used to write
``{Prompt, CurrentTreeJson, ByokKey, AccessToken, …}`` and read
``{TreeJson, Ops, Version}`` — faithful to the endpoint's published document
and refused by the endpoint itself, which reads ``prompt`` / ``currentTree``,
takes secrets from headers only, and replies
``{version, tree, opsApplied, provider, servedModel?, snapshot}`` with one
``{"error": {...}}`` envelope at every refusal.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping

from fuaran_ui.client import (
    SURFACE_VERSION,
    AccessDenied,
    AppliedOp,
    ClientCode,
    FuaranClient,
    FuaranSession,
    Produced,
    TurnFailed,
    is_secure_endpoint,
    is_surface_version_compatible,
    parse_produced_detail,
    parse_turn_response,
    to_wire_body,
)
from fuaran_ui.ui import encode, fuaran, node

TREE_JSON = encode(node.bare(fuaran.markdown("md-1", "hello")))
OP_JSON = '{"$type": "RemoveNode", "target": "md-1"}'


def _produced_body(tree_json: str = TREE_JSON, *, version: str = "1.6.0", ops_applied: int = 0) -> str:
    """The endpoint's canonical 200: the tree as an OBJECT, ``opsApplied`` as a
    COUNT, and the deployment facts beside them."""
    return json.dumps(
        {
            "version": version,
            "tree": json.loads(tree_json),
            "opsApplied": ops_applied,
            "provider": "openai",
            "servedModel": "gpt-4o-2024-11-20",
            "snapshot": {"state": "warm", "version": "7", "contentHash": "sha256:abc"},
        }
    )


# ── to_wire_body: the canonical request keys ─────────────────────────────────


def test_wire_body_minimal_is_prompt_only() -> None:
    assert to_wire_body("a metric card") == {"prompt": "a metric card"}


def test_wire_body_carries_every_supplied_field() -> None:
    body = to_wire_body(
        "rename it",
        current_tree_json=TREE_JSON,
        disable_corpus_read=True,
        contribute_corpus=False,
        interaction_id="corr-1",
    )
    assert body == {
        "prompt": "rename it",
        "currentTree": TREE_JSON,
        "disableCorpusRead": True,
        "contributeCorpus": False,
        "interactionId": "corr-1",
    }


def test_wire_body_has_no_secret_parameter_at_all() -> None:
    # The endpoint REFUSES a body carrying `ByokKey` / `AccessToken`, and this
    # module gives no way to write one. Asserted structurally rather than by
    # example: the parameters do not exist.
    parameters = set(inspect.signature(to_wire_body).parameters)
    assert parameters.isdisjoint({"byok_key", "access_token"})


def test_wire_body_omits_absent_fields_rather_than_sending_null() -> None:
    body = to_wire_body("p")
    assert "currentTree" not in body
    assert "disableCorpusRead" not in body
    assert "contributeCorpus" not in body
    assert "interactionId" not in body


# ── parse_turn_response: status → typed result ───────────────────────────────


def test_200_parses_produced_from_the_deployed_object_tree() -> None:
    result = parse_turn_response(200, _produced_body())
    assert isinstance(result, Produced)
    assert result.decode_tree().ok
    assert result.version == "1.6.0"


def test_200_parses_a_proxy_stringified_tree_and_its_op_list() -> None:
    body = json.dumps({"tree": TREE_JSON, "ops": [{"opId": "op-1", "opJson": OP_JSON}], "version": "1.6.0"})
    result = parse_turn_response(200, body)
    assert isinstance(result, Produced)
    assert result.tree_json == TREE_JSON
    assert result.ops == (AppliedOp(op_id="op-1", op_json=OP_JSON),)


def test_200_tolerates_the_retired_pascal_case_reply() -> None:
    body = json.dumps({"TreeJson": TREE_JSON, "Ops": [{"OpId": "a", "OpJson": OP_JSON}], "Version": "1.2.0"})
    result = parse_turn_response(200, body)
    assert isinstance(result, Produced)
    assert result.tree_json == TREE_JSON
    assert result.ops[0].op_id == "a"


def test_200_tolerates_missing_ops_and_malformed_entries() -> None:
    body = json.dumps({"tree": json.loads(TREE_JSON), "ops": ["junk", None, 3], "version": "1.6.0"})
    result = parse_turn_response(200, body)
    assert isinstance(result, Produced)
    assert result.ops == ()


def test_401_parses_access_denied_from_the_nested_envelope() -> None:
    body = json.dumps({"error": {"code": "ACCESS_DENIED", "message": "token expired"}})
    assert parse_turn_response(401, body) == AccessDenied(reason="token expired")


def test_401_still_reads_the_retired_bare_reason_member() -> None:
    body = json.dumps({"Reason": "token expired"})
    assert parse_turn_response(401, body) == AccessDenied(reason="token expired")


def test_401_with_empty_body_defaults_the_reason() -> None:
    assert parse_turn_response(401, "") == AccessDenied(reason="access denied")


def test_422_parses_the_nested_envelope() -> None:
    body = json.dumps({"error": {"stage": "apply", "code": "APPLY_REJECTED", "message": "no node"}})
    result = parse_turn_response(422, body)
    assert isinstance(result, TurnFailed)
    assert result.error.stage == "apply"
    assert result.error.code == "APPLY_REJECTED"
    assert result.error.message == "no node"


def test_422_still_reads_the_retired_flat_envelope() -> None:
    result = parse_turn_response(422, json.dumps({"Stage": "parse", "Code": "BAD_EMISSION", "Message": "x"}))
    assert isinstance(result, TurnFailed)
    assert result.error.stage == "parse"
    assert result.error.code == "BAD_EMISSION"


def test_422_unknown_stage_falls_back_to_provider() -> None:
    body = json.dumps({"error": {"stage": "quantum", "code": "X", "message": "m"}})
    result = parse_turn_response(422, body)
    assert isinstance(result, TurnFailed)
    assert result.error.stage == "provider"


def test_a_400_refusal_keeps_the_endpoints_own_code() -> None:
    # The whole reason the endpoint codes its refusals: a key sent in the body
    # must be rotated, a missing key header must be supplied. HTTP_400 says
    # neither.
    body = json.dumps({"error": {"code": "SECRETS_IN_BODY", "message": "rotate it"}})
    result = parse_turn_response(400, body)
    assert isinstance(result, TurnFailed)
    assert result.error.code == "SECRETS_IN_BODY"


def test_405_500_503_all_arrive_through_the_same_envelope() -> None:
    for status, code in ((405, "METHOD_NOT_ALLOWED"), (500, "TURN_FAULTED"), (503, "HOST_NOT_CONFIGURED")):
        body = json.dumps({"error": {"code": code, "message": "m"}})
        result = parse_turn_response(status, body)
        assert isinstance(result, TurnFailed)
        assert result.error.code == code


def test_unexpected_status_with_no_envelope_is_synthesised() -> None:
    result = parse_turn_response(502, "")
    assert isinstance(result, TurnFailed)
    assert result.error.stage == "provider"
    assert result.error.code == "HTTP_502"
    assert result.error.message == "unexpected status 502"


# ── parse_produced_detail: the deployment facts ──────────────────────────────


def test_produced_detail_reads_the_deployment_facts() -> None:
    detail = parse_produced_detail(200, _produced_body(ops_applied=3))
    assert detail is not None
    assert detail.ops_applied == 3
    assert detail.provider == "openai"
    assert detail.served_model == "gpt-4o-2024-11-20"
    assert detail.snapshot is not None
    assert detail.snapshot.state == "warm"


def test_an_absent_served_model_reads_as_unreported() -> None:
    body = json.loads(_produced_body())
    del body["servedModel"]
    detail = parse_produced_detail(200, json.dumps(body))
    assert detail is not None
    assert detail.served_model is None
    assert detail.provider == "openai"


def test_an_op_list_without_a_count_still_yields_ops_applied() -> None:
    body = json.dumps({"tree": TREE_JSON, "ops": [{"opId": "a", "opJson": OP_JSON}], "version": "1.6.0"})
    detail = parse_produced_detail(200, body)
    assert detail is not None
    assert detail.ops_applied == 1


def test_no_detail_for_a_refusal_or_a_malformed_200() -> None:
    assert parse_produced_detail(422, json.dumps({"error": {"code": "X", "message": "m"}})) is None
    assert parse_produced_detail(200, json.dumps({"version": "1.6.0", "opsApplied": 0})) is None


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


def test_generate_round_trips_the_contract_with_secrets_in_headers() -> None:
    transport = FakeTransport([(200, _produced_body())])
    client = FuaranClient(
        "https://example.test/generate",
        access_token="tok",
        provider_key="sk-key",
        provider="openai",
        transport=transport,
    )

    result = client.generate("a metric card", disable_corpus_read=True)

    assert isinstance(result, Produced)
    url, headers, body = transport.requests[0]
    assert url == "https://example.test/generate"
    assert headers["content-type"] == "application/json"
    assert headers["authorization"] == "Bearer tok"
    assert headers["x-fuaran-provider-key"] == "sk-key"
    assert headers["x-fuaran-provider"] == "openai"
    assert body == {"prompt": "a metric card", "disableCorpusRead": True}


def test_generate_per_call_credentials_override_the_config() -> None:
    transport = FakeTransport([(200, _produced_body())])
    client = FuaranClient(
        "https://example.test/generate", access_token="cfg-tok", provider_key="cfg-key", transport=transport
    )

    client.generate("p", access_token="call-tok", provider_key="call-key")

    _, headers, body = transport.requests[0]
    assert headers["authorization"] == "Bearer call-tok"
    assert headers["x-fuaran-provider-key"] == "call-key"
    assert "call-key" not in json.dumps(body)


def test_generate_server_proxied_pattern_sends_no_credentials() -> None:
    transport = FakeTransport([(200, _produced_body())])
    client = FuaranClient("/api/fuaran", transport=transport)

    client.generate("p")

    _, headers, body = transport.requests[0]
    assert "authorization" not in headers
    assert "x-fuaran-provider-key" not in headers
    assert body == {"prompt": "p"}


def test_generate_bearer_header_is_suppressible() -> None:
    transport = FakeTransport([(200, _produced_body())])
    client = FuaranClient("https://example.test/g", access_token="tok", send_bearer_header=False, transport=transport)

    client.generate("p")

    _, headers, body = transport.requests[0]
    assert "authorization" not in headers
    # …and the token does not fall back into the body: it has nowhere else to go.
    assert "tok" not in json.dumps(body)


def test_generate_detailed_hands_back_the_deployment_facts() -> None:
    transport = FakeTransport([(200, _produced_body(ops_applied=2))])
    client = FuaranClient("https://example.test/g", transport=transport)

    result, detail = client.generate_detailed("p")

    assert isinstance(result, Produced)
    assert detail is not None
    assert detail.ops_applied == 2
    assert detail.served_model == "gpt-4o-2024-11-20"


def test_client_requires_an_endpoint() -> None:
    try:
        FuaranClient("   ")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for a blank endpoint")


# ── hardening ────────────────────────────────────────────────────────────────


def test_a_200_with_no_tree_is_malformed_response_not_produced_empty() -> None:
    # Red before: this returned Produced(tree_json="") and the session then held
    # "" and repaired nothing on every subsequent turn — a fault that surfaces
    # one turn later than the reply that caused it.
    for body in (json.dumps({"version": "1.6.0", "opsApplied": 0}), "{}", "not json", ""):
        result = parse_turn_response(200, body)
        assert isinstance(result, TurnFailed), body
        assert result.error.code == ClientCode.MALFORMED_RESPONSE


def test_a_malformed_200_leaves_the_sessions_held_tree_alone() -> None:
    transport = FakeTransport([(200, _produced_body()), (200, json.dumps({"version": "1.6.0", "opsApplied": 0}))])
    session = FuaranSession(FuaranClient("https://example.test/g", transport=transport))

    session.next("build it")
    held = session.current_tree_json
    assert held is not None

    second = session.next("break it")
    assert isinstance(second, TurnFailed)
    assert second.error.code == ClientCode.MALFORMED_RESPONSE
    assert session.current_tree_json == held


def test_a_plaintext_non_loopback_endpoint_is_refused_before_anything_is_sent() -> None:
    transport = FakeTransport([(200, _produced_body())])
    client = FuaranClient(
        "http://api.example.com/generate",
        access_token="tok",
        provider_key="sk-key",
        transport=transport,
    )

    result = client.generate("p")

    assert isinstance(result, TurnFailed)
    assert result.error.code == ClientCode.INSECURE_ENDPOINT
    assert transport.requests == []


def test_loopback_https_and_a_relative_proxy_path_are_all_admitted() -> None:
    for endpoint in (
        "http://127.0.0.1:8123",
        "http://localhost:8123/generate",
        "https://api.example.com/generate",
        "/api/fuaran",
    ):
        assert is_secure_endpoint(endpoint), endpoint
        transport = FakeTransport([(200, _produced_body())])
        client = FuaranClient(endpoint, transport=transport)
        assert isinstance(client.generate("p"), Produced), endpoint


def test_allow_insecure_endpoint_is_the_deliberate_opt_out() -> None:
    transport = FakeTransport([(200, _produced_body())])
    client = FuaranClient("http://api.example.com/generate", allow_insecure_endpoint=True, transport=transport)

    assert isinstance(client.generate("p"), Produced)
    assert len(transport.requests) == 1


def test_a_transport_timeout_is_network_with_a_fixed_message() -> None:
    def timing_out(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, str]:
        raise TimeoutError("timed out after 30s reaching internal-proxy.corp:9443")

    result = FuaranClient("https://example.test/g", transport=timing_out).generate("p")

    assert isinstance(result, TurnFailed)
    assert result.error.code == ClientCode.NETWORK
    assert "timed out" in result.error.message
    assert "internal-proxy" not in result.error.message


def test_no_upstream_exception_text_reaches_the_caller() -> None:
    # Red before: the message was str(error) verbatim, and this result is
    # routinely rendered into a page.
    def exploding(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, str]:
        raise RuntimeError("connection to https://internal-proxy.corp:9443 refused")

    result = FuaranClient("https://example.test/g", transport=exploding).generate("p")

    assert isinstance(result, TurnFailed)
    assert result.error.stage == "provider"
    assert result.error.code == ClientCode.NETWORK
    assert "internal-proxy" not in result.error.message
    assert "refused" not in result.error.message


# ── no-key-leak posture ──────────────────────────────────────────────────────


def test_byok_key_appears_in_no_repr_body_or_error_message() -> None:
    sentinel = "sk-SENTINEL-DO-NOT-LEAK"
    seen: list[dict[str, object]] = []

    def exploding(url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, str]:
        # The key travels ONLY in its header — never in the body, which is the
        # thing an intermediary is most likely to log wholesale.
        assert headers["x-fuaran-provider-key"] == sentinel
        seen.append(json.loads(body.decode("utf-8")))
        raise RuntimeError("boom")

    client = FuaranClient("https://example.test/g", access_token="tok", provider_key=sentinel, transport=exploding)
    result = client.generate("p")

    assert sentinel not in json.dumps(seen)
    assert sentinel not in repr(client)
    assert isinstance(result, TurnFailed)
    assert sentinel not in result.error.message


# ── FuaranSession: the turn loop carries the tree forward ────────────────────


def test_session_first_turn_is_fresh_then_repairs_against_the_produced_tree() -> None:
    second_tree = encode(node.bare(fuaran.markdown("md-1", "renamed")))
    transport = FakeTransport([(200, _produced_body()), (200, _produced_body(second_tree))])
    session = FuaranSession(FuaranClient("https://example.test/g", transport=transport))

    assert session.current_tree_json is None
    first = session.next("a metric card")
    assert isinstance(first, Produced)
    held = session.current_tree_json
    assert held is not None

    second = session.next("rename it")
    assert isinstance(second, Produced)
    # The second request carried the first turn's tree — a repair, not a regeneration.
    assert transport.requests[0][2].get("currentTree") is None
    assert transport.requests[1][2]["currentTree"] == held


def test_session_holds_the_tree_across_a_failed_turn() -> None:
    transport = FakeTransport(
        [
            (200, _produced_body()),
            (422, json.dumps({"error": {"stage": "apply", "code": "APPLY_REJECTED", "message": "no node"}})),
            (401, json.dumps({"error": {"code": "ACCESS_DENIED", "message": "expired"}})),
        ]
    )
    session = FuaranSession(FuaranClient("https://example.test/g", transport=transport))

    session.next("build it")
    held = session.current_tree_json
    session.next("bad repair")
    session.next("another")

    # Both failures left the held tree unchanged, so the caller can retry.
    assert session.current_tree_json == held
    assert transport.requests[2][2]["currentTree"] == held


def test_session_seeds_from_an_initial_tree_and_resets() -> None:
    transport = FakeTransport([(200, _produced_body())])
    session = FuaranSession(
        FuaranClient("https://example.test/g", transport=transport),
        initial_tree_json=TREE_JSON,
    )

    session.next("tweak it")
    # Seeded ⇒ the very first turn is already a repair.
    assert transport.requests[0][2]["currentTree"] == TREE_JSON

    session.reset()
    assert session.current_tree_json is None
