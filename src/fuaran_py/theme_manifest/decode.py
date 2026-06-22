"""JSON → :class:`ThemeManifest` — Python port of ``Fuaran.UI.ThemeManifest.Decode``.

Two top-level shapes are accepted:

1. A Fuaran manifest wrapper — ``{ meta, tokens, roles, invariants }``.
2. A vanilla DTCG file — the token group tree at top level, no wrapper (decodes to
   a manifest with tokens populated, empty roles/invariants).

Detection: presence of a top-level ``tokens`` key selects shape (1). Uses the
stdlib :mod:`json` parser (dependency-light).
"""

from __future__ import annotations

import json
from typing import Any

from .manifest import (
    ANONYMOUS_META,
    DEFAULT_WEIGHT,
    EMPTY_MANIFEST,
    ContrastFloor,
    Invariant,
    InvariantKind,
    ManifestMeta,
    ManifestRole,
    ManifestToken,
    MotionBudget,
    MotionVoice,
    NamedRole,
    RoleBinding,
    ThemeManifest,
    ToneRole,
    UsageBudget,
    tone_of_string,
)


def _as_obj(v: Any) -> dict[str, Any] | None:
    return v if isinstance(v, dict) else None


def _as_str(v: Any) -> str | None:
    return v if isinstance(v, str) else None


def _as_num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# ── DTCG token-tree walk ──────────────────────────────────────────────────────


def _walk_tokens(prefix: str, node: Any) -> list[ManifestToken]:
    obj = _as_obj(node)
    if obj is None:
        return []
    if "$value" in obj:
        role = None
        ext = _as_obj(obj.get("$extensions"))
        fuaran = _as_obj(ext.get("fuaran")) if ext is not None else None
        if fuaran is not None:
            role = _as_str(fuaran.get("role"))
        return [
            ManifestToken(
                name=prefix,
                type=_as_str(obj.get("$type")) or "",
                value=_as_str(obj.get("$value")) or "",
                description=_as_str(obj.get("$description")),
                role=role,
            )
        ]
    out: list[ManifestToken] = []
    for k, child in obj.items():
        if k.startswith("$"):
            continue
        out.extend(_walk_tokens(k if prefix == "" else f"{prefix}.{k}", child))
    return out


# ── roles + invariants ────────────────────────────────────────────────────────


def _parse_role(j: Any) -> ManifestRole:
    obj = _as_obj(j)
    if obj is None:
        return NamedRole("")
    tone = _as_str(obj.get("tone"))
    if tone is not None:
        validated = tone_of_string(tone)
        return ToneRole(validated) if validated is not None else NamedRole(tone)
    return NamedRole(_as_str(obj.get("named")) or "")


def _parse_role_binding(j: Any) -> RoleBinding | None:
    obj = _as_obj(j)
    if obj is None:
        return None
    token = _as_str(obj.get("token"))
    if token is None:
        return None
    role = _parse_role(obj["role"]) if "role" in obj else NamedRole("")
    return RoleBinding(role=role, token_name=token)


def _parse_invariant(j: Any) -> Invariant | None:
    obj = _as_obj(j)
    if obj is None:
        return None
    weight = _as_num(obj.get("weight"))
    weight = weight if weight is not None else DEFAULT_WEIGHT
    kind_str = _as_str(obj.get("kind"))
    inner: InvariantKind
    if kind_str == "ContrastFloor":
        inner = ContrastFloor(role=_as_str(obj.get("role")) or "", min_ratio=_as_num(obj.get("minRatio")) or 0.0)
    elif kind_str == "UsageBudget":
        inner = UsageBudget(
            token=_as_str(obj.get("token")) or "",
            target_pct=_as_num(obj.get("targetPct")) or 0.0,
            tolerance_pct=_as_num(obj.get("tolerancePct")) or 0.0,
        )
    elif kind_str == "MotionVoice":
        inner = MotionVoice(
            MotionBudget(max_duration_ms=int(_as_num(obj.get("maxDurationMs")) or 0), easing=_as_str(obj.get("easing")))
        )
    else:
        return None
    return Invariant(inner, weight)


def _parse_meta(obj: dict[str, Any]) -> ManifestMeta:
    return ManifestMeta(
        name=_as_str(obj.get("name")) or "",
        version=_as_str(obj.get("version")) or "",
        description=_as_str(obj.get("description")),
    )


# ── Top-level ─────────────────────────────────────────────────────────────────


def of_json(root: Any) -> ThemeManifest:
    """Build a manifest from a parsed JSON value (exposed for tests / tooling)."""
    obj = _as_obj(root)
    if obj is None:
        return EMPTY_MANIFEST
    if "tokens" in obj:
        meta_obj = _as_obj(obj.get("meta"))
        roles = [b for b in (_parse_role_binding(r) for r in (obj.get("roles") or [])) if b is not None]
        invariants = [i for i in (_parse_invariant(x) for x in (obj.get("invariants") or [])) if i is not None]
        return ThemeManifest(
            meta=_parse_meta(meta_obj) if meta_obj is not None else ANONYMOUS_META,
            tokens=_walk_tokens("", obj["tokens"]),
            roles=roles,
            invariants=invariants,
        )
    return ThemeManifest(tokens=_walk_tokens("", root))


def decode(json_str: str) -> ThemeManifest:
    """Decode a manifest from JSON. Raises :class:`json.JSONDecodeError` on a parse failure."""
    return of_json(json.loads(json_str))
