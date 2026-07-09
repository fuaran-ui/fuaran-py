"""Phase 238 — smoke test for the FastAPI server-proxied BYOK host sample.

Drives ``samples/fastapi-host/app.py`` with FastAPI's ``TestClient`` and a **mock
transport** (no real endpoint): a turn completes, the tree carries across a second
(repair) turn, access-denied maps to 401 and turn-failed to 422, and — the
load-bearing assertion — the server-held access token + BYOK key never appear in
any response the browser receives.

Skips when ``fastapi`` is not installed (``fuaran-py`` itself stays
standard-library-only; FastAPI is a sample-only dependency).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="fastapi (sample dependency) is required for this smoke test")
pytest.importorskip("httpx", reason="httpx is required by fastapi.testclient")

from fastapi.testclient import TestClient  # noqa: E402

from fuaran_py.client import FuaranClient  # noqa: E402
from fuaran_py.ui import encode, fuaran, node  # noqa: E402

# Distinctive secret values — the whole test is that these never reach the client.
ACCESS_TOKEN = "SECRET-ACCESS-TOKEN-abc123"
PROVIDER_KEY = "SECRET-BYOK-KEY-xyz789"

FIRST_TREE = encode(node.bare(fuaran.markdown("md-1", "hello")))
REPAIRED_TREE = encode(node.bare(fuaran.markdown("md-1", "renamed")))

_SAMPLE_APP = Path(__file__).resolve().parents[1] / "samples" / "fastapi-host" / "app.py"


def _load_sample_module() -> object:
    # The sample's module-level `app = create_app()` fails fast if FUARAN_ENDPOINT
    # is unset (correct for `uvicorn app:app`); give it a placeholder so the module
    # imports. The test drives its own app via create_app(client=<mock>).
    import os

    os.environ.setdefault("FUARAN_ENDPOINT", "https://placeholder.invalid/generate")
    spec = importlib.util.spec_from_file_location("fuaran_fastapi_sample", _SAMPLE_APP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass / pydantic forward-ref resolution
    # (which reads sys.modules[cls.__module__]) can find the module.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _MockTransport:
    """A fake endpoint routing on the prompt; records every server-side request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, str]:
        parsed = json.loads(body.decode("utf-8"))
        self.requests.append({"headers": dict(headers), "body": parsed})
        prompt = str(parsed.get("Prompt", ""))
        if "deny me" in prompt:
            return 401, json.dumps({"Reason": "token expired"})
        if "break me" in prompt:
            return 422, json.dumps({"Error": {"Stage": "apply", "Code": "APPLY_REJECTED", "Message": "no node"}})
        tree = REPAIRED_TREE if parsed.get("CurrentTreeJson") is not None else FIRST_TREE
        return 200, json.dumps({"TreeJson": tree, "Ops": [], "Version": "1.2.0"})


@pytest.fixture()
def transport() -> _MockTransport:
    return _MockTransport()


@pytest.fixture()
def client(transport: _MockTransport) -> Iterator[TestClient]:
    module = _load_sample_module()
    sdk_client = FuaranClient(
        "https://proxy.example/generate",
        access_token=ACCESS_TOKEN,
        provider_key=PROVIDER_KEY,
        transport=transport,
    )
    app = module.create_app(client=sdk_client)  # type: ignore[attr-defined]
    with TestClient(app) as test_client:
        yield test_client


def test_completes_a_turn_and_renders(client: TestClient) -> None:
    resp = client.post("/generate", json={"prompt": "a metric card showing revenue"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "produced"
    assert data["tree"] == FIRST_TREE
    assert data["html"]  # server-rendered markup is present
    assert data["version"] == "1.2.0"


def test_turn_loop_carries_the_tree_as_a_repair(client: TestClient, transport: _MockTransport) -> None:
    client.post("/generate", json={"prompt": "a metric"})
    resp = client.post("/generate", json={"prompt": "rename it", "current_tree_json": FIRST_TREE})
    assert resp.status_code == 200
    assert resp.json()["tree"] == REPAIRED_TREE
    # the server forwarded the held tree as the current tree (a repair, not a regen)
    assert transport.requests[-1]["body"]["CurrentTreeJson"] == FIRST_TREE  # type: ignore[index]


def test_access_denied_maps_to_401(client: TestClient) -> None:
    resp = client.post("/generate", json={"prompt": "deny me"})
    assert resp.status_code == 401
    assert resp.json()["status"] == "access_denied"


def test_turn_failed_maps_to_422(client: TestClient) -> None:
    resp = client.post("/generate", json={"prompt": "break me"})
    assert resp.status_code == 422
    data = resp.json()
    assert data["status"] == "turn_failed"
    assert data["error"]["stage"] == "apply"


def test_credentials_never_reach_the_client(client: TestClient, transport: _MockTransport) -> None:
    prompts = ["a metric card", "deny me", "break me"]
    for prompt in prompts:
        resp = client.post("/generate", json={"prompt": prompt})
        body = resp.text
        assert ACCESS_TOKEN not in body, f"access token leaked in response to {prompt!r}"
        assert PROVIDER_KEY not in body, f"BYOK key leaked in response to {prompt!r}"
    # sanity: the index page carries no credential either
    index = client.get("/")
    assert ACCESS_TOKEN not in index.text and PROVIDER_KEY not in index.text
    # and the secrets WERE injected server-side (proving the proxy hop, not a no-op)
    server_side = json.dumps(transport.requests)
    assert PROVIDER_KEY in server_side, "the BYOK key should reach the endpoint from the server"
