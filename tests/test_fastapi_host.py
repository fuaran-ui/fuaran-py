"""Phase 238 — smoke test for the FastAPI server-proxied BYOK host sample.

Drives ``samples/fastapi-host/app.py`` with FastAPI's ``TestClient`` and a **mock
transport** (no real endpoint): a turn completes, the tree carries across a second
(repair) turn, access-denied maps to 401 and turn-failed to 422, and — the
load-bearing assertion — the server-held access token + BYOK key never appear in
any response the browser receives.

Skips when the ``live-host`` extra is not installed (``fuaran-py`` itself stays
standard-library-only; FastAPI is a sample-only dependency). CI runs one matrix row
WITH the extra so these assertions gate, and one without it so the skip is proven
clean — for a year it only ever ran the second, and reported green.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

_EXTRA = 'install the sample extra: pip install "fuaran-py[live-host]"'
fastapi = pytest.importorskip("fastapi", reason=f"fastapi (sample dependency) is absent — {_EXTRA}")
pytest.importorskip("httpx", reason=f"httpx (needed by fastapi.testclient) is absent — {_EXTRA}")

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
    # is unset or no authorisation arm is chosen (both correct for
    # `uvicorn app:app`); give it a placeholder and the anonymous arm so the
    # module imports. The test drives its own app via create_app(client=<mock>).
    import os

    os.environ.setdefault("FUARAN_ENDPOINT", "https://placeholder.invalid/generate")
    # The route is fail-closed on authorisation, so module import (which builds
    # `app = create_app()` for `uvicorn app:app`) refuses without an arm. Naming
    # the anonymous one here is the opt-in the sample requires of any host.
    os.environ.setdefault("FUARAN_SAMPLE_ANONYMOUS", "1")
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
        prompt = str(parsed.get("prompt", ""))
        if "deny me" in prompt:
            return 401, json.dumps({"error": {"code": "ACCESS_DENIED", "message": "token expired"}})
        if "break me" in prompt:
            return 422, json.dumps(
                {"error": {"stage": "apply", "code": "APPLY_REJECTED", "message": "no node"}}
            )
        if "undecodable" in prompt:
            # A 200 whose tree the host cannot decode. It is a canonical-looking
            # object, so the SDK admits it; the HOST is what must refuse.
            return 200, json.dumps(
                {
                    "version": "1.6.0",
                    "tree": {"id": "x", "kind": {"$type": "NotAKindThatExists"}},
                    "opsApplied": 0,
                    "provider": "mock",
                    "snapshot": {"state": "mock"},
                }
            )
        tree = REPAIRED_TREE if parsed.get("currentTree") is not None else FIRST_TREE
        return 200, json.dumps(
            {
                "version": "1.6.0",
                "tree": json.loads(tree),
                "opsApplied": 0,
                "provider": "openai",
                "servedModel": "gpt-4o-2024-11-20",
                "snapshot": {"state": "warm"},
            }
        )


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
    # The route is fail-closed on authorisation; the smoke drives the ANONYMOUS
    # arm explicitly, which is the point of naming it rather than defaulting to
    # it. `test_spend_guards.py` drives the secret arm.
    app = module.create_app(  # type: ignore[attr-defined]
        client=sdk_client,
        policy=module.Policy(expected_secret=None, rate_limiter=module.RateLimiter()),  # type: ignore[attr-defined]
    )
    with TestClient(app) as test_client:
        yield test_client


def test_completes_a_turn_and_renders(client: TestClient) -> None:
    resp = client.post("/generate", json={"prompt": "a metric card showing revenue"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "produced"
    assert data["tree"] == FIRST_TREE
    assert data["html"]  # server-rendered markup is present
    assert data["version"] == "1.6.0"
    # The deployment facts survive the proxy hop rather than being dropped at it.
    assert data["provider"] == "openai"
    assert data["servedModel"] == "gpt-4o-2024-11-20"


def test_turn_loop_carries_the_tree_as_a_repair(client: TestClient, transport: _MockTransport) -> None:
    client.post("/generate", json={"prompt": "a metric"})
    resp = client.post("/generate", json={"prompt": "rename it", "current_tree_json": FIRST_TREE})
    assert resp.status_code == 200
    assert resp.json()["tree"] == REPAIRED_TREE
    # the server forwarded the held tree as the current tree (a repair, not a regen)
    assert transport.requests[-1]["body"]["currentTree"] == FIRST_TREE  # type: ignore[index]


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
    # and the secrets WERE injected server-side (proving the proxy hop, not a
    # no-op) — as HEADERS, never in a body the endpoint would refuse.
    for request in transport.requests:
        headers = request["headers"]
        assert headers["x-fuaran-provider-key"] == PROVIDER_KEY  # type: ignore[index]
        assert headers["authorization"] == f"Bearer {ACCESS_TOKEN}"  # type: ignore[index]
        assert PROVIDER_KEY not in json.dumps(request["body"])
        assert ACCESS_TOKEN not in json.dumps(request["body"])


def test_a_tree_the_host_cannot_decode_is_a_failure_not_an_empty_panel(client: TestClient) -> None:
    # Red before: the host replied 200 with `html: ""`, which the page rendered
    # as an empty panel and the caller recorded as a successful turn — while
    # still holding the undecodable tree, so every later repair built on it.
    resp = client.post("/generate", json={"prompt": "undecodable please"})
    assert resp.status_code == 502
    body = resp.json()
    assert body["status"] == "refused"
    assert body["error"]["code"] == "MALFORMED_TREE"
