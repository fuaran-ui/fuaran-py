"""The default transport against a live in-process mock endpoint.

`test_client.py` locks the contract against a fake transport; this file
proves the *default* standard-library transport end-to-end — in particular
that 401 / 422 arrive as parsed typed results (urllib raises ``HTTPError``
for them; the transport must hand the status + body to the parser, not
throw), and that the session's repair loop carries the tree over real HTTP.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from fuaran_py.client import AccessDenied, FuaranClient, FuaranSession, Produced, TurnFailed
from fuaran_py.ui import encode, fuaran, node

FIRST_TREE = encode(node.bare(fuaran.markdown("md-1", "hello")))
SECOND_TREE = encode(node.bare(fuaran.markdown("md-1", "renamed")))


class MockEndpoint(BaseHTTPRequestHandler):
    """Routes on the prompt text; records every request body it sees."""

    requests: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802 — http.server's required casing
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        MockEndpoint.requests.append(body)

        prompt = str(body.get("Prompt", ""))
        if "deny me" in prompt:
            self._reply(401, {"Reason": "token expired"})
        elif "break me" in prompt:
            self._reply(422, {"Error": {"Stage": "apply", "Code": "APPLY_REJECTED", "Message": "no node"}})
        else:
            tree = SECOND_TREE if body.get("CurrentTreeJson") is not None else FIRST_TREE
            self._reply(200, {"TreeJson": tree, "Ops": [], "Version": "1.2.0"})

    def _reply(self, status: int, payload: dict[str, object]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — http.server's signature
        pass  # keep pytest output clean


@pytest.fixture()
def endpoint() -> Iterator[str]:
    MockEndpoint.requests = []
    server = HTTPServer(("127.0.0.1", 0), MockEndpoint)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/generate"
    finally:
        server.shutdown()
        thread.join()


def test_produced_access_denied_and_turn_failed_over_real_http(endpoint: str) -> None:
    client = FuaranClient(endpoint, access_token="tok", provider_key="sk-key")

    produced = client.generate("a metric card")
    assert isinstance(produced, Produced)
    assert produced.version == "1.2.0"
    decoded = produced.decode_tree()
    assert decoded.ok

    denied = client.generate("deny me")
    assert denied == AccessDenied(reason="token expired")

    failed = client.generate("break me")
    assert isinstance(failed, TurnFailed)
    assert failed.error.stage == "apply"
    assert failed.error.code == "APPLY_REJECTED"


def test_session_repair_loop_carries_the_tree_over_real_http(endpoint: str) -> None:
    session = FuaranSession(FuaranClient(endpoint, access_token="tok", provider_key="sk-key"))

    first = session.next("a metric card")
    assert isinstance(first, Produced)
    assert first.tree_json == FIRST_TREE

    second = session.next("rename it")
    assert isinstance(second, Produced)
    assert second.tree_json == SECOND_TREE

    fresh, repair = MockEndpoint.requests[0], MockEndpoint.requests[1]
    assert "CurrentTreeJson" not in fresh
    assert repair["CurrentTreeJson"] == FIRST_TREE


def test_network_failure_surfaces_as_typed_turn_failed() -> None:
    # A port from the dynamic range with no listener — connection refused.
    client = FuaranClient("http://127.0.0.1:9/generate")
    result = client.generate("p")
    assert isinstance(result, TurnFailed)
    assert result.error.code == "NETWORK"
