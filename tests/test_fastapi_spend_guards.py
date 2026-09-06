"""The FastAPI sample route is not an open spend endpoint.

The sample holds a BYOK key and answers whoever can reach it. Before these
guards it authorised nobody, capped nothing, and rate-limited nothing: reaching
the port was sufficient to spend the operator's provider budget at whatever rate
the caller chose. Each test below is red without its guard.

The three refusals all happen BEFORE the SDK call, which is why the transport
double asserts it was never invoked: a guard that refuses after the turn has
already paid for it.

Skips when ``fastapi`` is not installed (``fuaran-py`` itself stays
standard-library-only; FastAPI is a sample-only dependency).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="fastapi (sample dependency) is required for this smoke test")
pytest.importorskip("httpx", reason="httpx is required by fastapi.testclient")

from fastapi.testclient import TestClient  # noqa: E402

from fuaran_py.client import FuaranClient  # noqa: E402
from fuaran_py.ui import encode, fuaran, node  # noqa: E402

TREE = encode(node.bare(fuaran.markdown("md-1", "hello")))
SECRET = "s3cret-shared-with-the-page"

_SAMPLE_APP = Path(__file__).resolve().parents[1] / "samples" / "fastapi-host" / "app.py"


def _sample_module() -> object:
    import os

    os.environ.setdefault("FUARAN_ENDPOINT", "https://placeholder.invalid/generate")
    os.environ.setdefault("FUARAN_SAMPLE_ANONYMOUS", "1")
    spec = importlib.util.spec_from_file_location("fuaran_fastapi_sample_guards", _SAMPLE_APP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _CountingTransport:
    """Counts endpoint calls. A refused request must not reach it."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, headers: Mapping[str, str], body: bytes) -> tuple[int, str]:
        self.calls += 1
        return 200, json.dumps(
            {
                "version": "1.6.0",
                "tree": json.loads(TREE),
                "opsApplied": 0,
                "provider": "mock",
                "snapshot": {"state": "mock"},
            }
        )


def _app(module: object, policy: object) -> tuple[TestClient, _CountingTransport]:
    transport = _CountingTransport()
    sdk = FuaranClient("https://proxy.example/generate", transport=transport)
    app = module.create_app(client=sdk, policy=policy)  # type: ignore[attr-defined]
    return TestClient(app), transport


def test_the_route_refuses_to_start_with_no_authorisation_arm(monkeypatch: pytest.MonkeyPatch) -> None:
    # There is no third answer: a sample that quietly served everyone when a
    # variable was unset would be copied into something that quietly serves
    # everyone.
    module = _sample_module()
    monkeypatch.delenv("FUARAN_SAMPLE_SECRET", raising=False)
    monkeypatch.delenv("FUARAN_SAMPLE_ANONYMOUS", raising=False)
    with pytest.raises(RuntimeError) as raised:
        module.build_policy()  # type: ignore[attr-defined]
    assert "FUARAN_SAMPLE_SECRET" in str(raised.value)
    assert "FUARAN_SAMPLE_ANONYMOUS" in str(raised.value)


def test_the_anonymous_arm_must_be_asked_for_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _sample_module()
    monkeypatch.delenv("FUARAN_SAMPLE_SECRET", raising=False)
    monkeypatch.setenv("FUARAN_SAMPLE_ANONYMOUS", "1")
    policy, described = module.build_policy()  # type: ignore[attr-defined]
    assert policy.expected_secret is None
    assert "ANONYMOUS" in described


def test_a_caller_without_the_secret_is_refused_without_spending(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _sample_module()
    policy = module.Policy(expected_secret=SECRET, rate_limiter=module.RateLimiter())  # type: ignore[attr-defined]
    client, transport = _app(module, policy)

    resp = client.post("/generate", json={"prompt": "a metric card"})

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "ACCESS_DENIED"
    assert transport.calls == 0


def test_the_right_secret_is_admitted() -> None:
    module = _sample_module()
    policy = module.Policy(expected_secret=SECRET, rate_limiter=module.RateLimiter())  # type: ignore[attr-defined]
    client, transport = _app(module, policy)

    resp = client.post(
        "/generate",
        json={"prompt": "a metric card"},
        headers={module.SECRET_HEADER: SECRET},  # type: ignore[attr-defined]
    )

    assert resp.status_code == 200
    assert transport.calls == 1


def test_a_prompt_past_the_cap_is_refused_without_spending() -> None:
    module = _sample_module()
    policy = module.Policy(expected_secret=None, max_prompt_length=16, rate_limiter=module.RateLimiter())  # type: ignore[attr-defined]
    client, transport = _app(module, policy)

    resp = client.post("/generate", json={"prompt": "x" * 17})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "PROMPT_TOO_LONG"
    # Prompt length is the input-token bill, and none of it was paid.
    assert transport.calls == 0


def test_an_empty_prompt_is_refused_without_spending() -> None:
    module = _sample_module()
    policy = module.Policy(expected_secret=None, rate_limiter=module.RateLimiter())  # type: ignore[attr-defined]
    client, transport = _app(module, policy)

    resp = client.post("/generate", json={"prompt": "   "})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "BAD_REQUEST"
    assert transport.calls == 0


def test_a_caller_past_the_window_is_rate_limited_and_the_window_resets() -> None:
    module = _sample_module()
    now = [0.0]
    limiter = module.RateLimiter(max_turns=2, window_seconds=60.0, now=lambda: now[0])  # type: ignore[attr-defined]
    policy = module.Policy(expected_secret=None, rate_limiter=limiter)  # type: ignore[attr-defined]
    client, transport = _app(module, policy)

    assert client.post("/generate", json={"prompt": "one"}).status_code == 200
    assert client.post("/generate", json={"prompt": "two"}).status_code == 200

    third = client.post("/generate", json={"prompt": "three"})
    assert third.status_code == 429
    assert third.json()["error"]["code"] == "RATE_LIMITED"
    assert transport.calls == 2, "the refused turn cost nothing"

    # A window, not a permanent ban.
    now[0] = 120.0
    assert client.post("/generate", json={"prompt": "four"}).status_code == 200


def test_the_limiter_is_per_caller() -> None:
    module = _sample_module()
    limiter = module.RateLimiter(max_turns=1, window_seconds=60.0, now=lambda: 0.0)  # type: ignore[attr-defined]

    assert limiter.admit("a") is True
    assert limiter.admit("a") is False
    assert limiter.admit("b") is True, "one caller's spend must not exhaust another's"
