"""The teleport state bundle (WIRE_FORMAT.md §17).

Serialise a running app (the tree, its ``Binding.State`` values, an optional
bounded op-history window, and the op-chain head hash) into one URL/QR-sized
string and resume it on any device.

String format (§17.1): ``FT1.<base64url(deflate(canonical-JSON envelope))>`` —
raw RFC 1951 deflate (no zlib/gzip wrapper), unpadded base64url. This host encodes
deterministically via :mod:`zlib` (raw deflate, negative ``wbits``) and decodes the
full RFC 1951 range, so a bundle from any standard deflate library interoperates;
the cross-host *byte-identical* reference-encoder certification is a documented
follow-on, so this host — like the Go twin ``teleport/teleport.go`` — certifies its
own round-trip + the digest / size / version rejects + the budget. stdlib-only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import zlib
from dataclasses import dataclass, field

from .canonical import encode_value
from .model import Arr, Node, Obj, Value, from_json
from .ops import decode_op
from .schema import decode_node
from .validator import validate_node

# ── Format constants ────────────────────────────────────────────────────────
_PREFIX = "FT1."
_BUNDLE_VERSION = "teleport@1"
_DIGEST_PREFIX = "fuaran-teleport:v1|"
# maxInput bounds the encoded string accepted before any decompression work
# (§17.4 step 1); maxInflate caps the inflated output (a deflate bomb).
_MAX_INPUT = 64 * 1024
_MAX_INFLATE = 1024 * 1024

# ── Size budgets (§17.5), in encoded characters (= bytes; the string is ASCII).
BUDGET_QR_HARD = 2953  # byte-mode capacity at QR version 40, EC level L
BUDGET_QR_COMFORTABLE = 1273  # ≈ version 25-L
BUDGET_URL_PRACTICAL = 8000  # shared-link surfaces degrade beyond a few KB

# ── Typed error kinds ───────────────────────────────────────────────────────
OVERSIZE = "Oversize"
INVALID_FORMAT = "InvalidFormat"
INVALID_JSON = "InvalidJson"
INVALID_ENVELOPE = "InvalidEnvelope"
UNSUPPORTED_VERSION = "UnsupportedVersion"
DIGEST_MISMATCH = "DigestMismatch"
TREE_DECODE = "TreeDecode"
HISTORY_DECODE = "HistoryDecode"
TREE_INVALID = "TreeInvalid"


class TeleportError(Exception):
    """A typed, recoverable teleport failure."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class Bundle:
    """A decoded teleport state bundle.

    ``state`` is omitted from the wire when empty; ``history`` is a bounded
    op-history window (newest-last, provenance only — resume does not re-apply
    it), omitted when empty; ``chain_head`` is the optional op-chain head hash.
    """

    tree: Node
    state: dict[str, Value] = field(default_factory=dict)
    history: tuple[Obj, ...] = ()
    chain_head: str | None = None


# ── canonical envelope build ────────────────────────────────────────────────


def _envelope_fields(bundle: Bundle) -> dict[str, Value]:
    """Build the envelope's canonical fields WITHOUT the digest (the pre-image)."""
    fields: dict[str, Value] = {"bundle": _BUNDLE_VERSION, "tree": bundle.tree}
    if bundle.state:
        fields["state"] = Obj(None, dict(bundle.state))
    if bundle.history:
        fields["history"] = Arr(list(bundle.history))
    if bundle.chain_head is not None:
        fields["chainHead"] = bundle.chain_head
    return fields


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def encode(bundle: Bundle) -> str:
    """Serialise a bundle to its ``FT1`` teleport string. Deterministic."""
    fields = _envelope_fields(bundle)
    preimage = encode_value(Obj(None, fields))
    fields["digest"] = _sha256_hex(_DIGEST_PREFIX + preimage)
    envelope_json = encode_value(Obj(None, fields))
    compressed = _deflate(envelope_json.encode("utf-8"))
    return _PREFIX + _b64url_encode(compressed)


def encode_within(bundle: Bundle, budget: int) -> str:
    """Encode a bundle, refusing (``Oversize``) if the result exceeds ``budget`` characters (§17.5)."""
    s = encode(bundle)
    if len(s) > budget:
        raise TeleportError(OVERSIZE, "encoded bundle exceeds the budget")
    return s


# ── decode → validate → resume (§17.4) ──────────────────────────────────────


def decode(s: str) -> Bundle:
    """Run the §17.4 pipeline in order, raising a typed :class:`TeleportError` on failure.

    size gate → unwrap → envelope shape/version → digest → wire decode → pre-emit
    validation → state re-seat.
    """
    # 1 — size gate.
    if len(s) > _MAX_INPUT:
        raise TeleportError(OVERSIZE, "encoded input exceeds the size gate")
    # 2 — unwrap.
    if not s.startswith(_PREFIX):
        raise TeleportError(INVALID_FORMAT, "missing FT1. prefix")
    try:
        compressed = _b64url_decode(s[len(_PREFIX) :])
    except (binascii.Error, ValueError) as exc:
        raise TeleportError(INVALID_FORMAT, "invalid base64url") from exc
    inflated = _inflate(compressed)
    try:
        text = inflated.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TeleportError(INVALID_FORMAT, "inflated payload is not valid UTF-8") from exc
    try:
        raw = _json_loads(text)
    except ValueError as exc:
        raise TeleportError(INVALID_JSON, "envelope is not valid JSON") from exc
    if not isinstance(raw, dict):
        raise TeleportError(INVALID_ENVELOPE, "envelope is not an object")
    obj: dict = raw
    # 3 — envelope shape + version.
    bundle_tag = obj.get("bundle")
    if not isinstance(bundle_tag, str):
        raise TeleportError(INVALID_ENVELOPE, "missing 'bundle' version")
    if bundle_tag != _BUNDLE_VERSION:
        raise TeleportError(UNSUPPORTED_VERSION, f"unsupported bundle version '{bundle_tag}'")
    digest = obj.get("digest")
    if not isinstance(digest, str) or not _is_hex64(digest):
        raise TeleportError(INVALID_ENVELOPE, "missing/invalid 'digest'")
    if "tree" not in obj:
        raise TeleportError(INVALID_ENVELOPE, "missing 'tree'")
    # 4 — digest verification (before any payload decode).
    minus_digest: dict[str, Value] = {k: from_json(v) for k, v in obj.items() if k != "digest"}
    preimage = encode_value(Obj(None, minus_digest))
    if _sha256_hex(_DIGEST_PREFIX + preimage) != digest:
        raise TeleportError(DIGEST_MISMATCH, "digest does not verify — the bundle was tampered or corrupted")
    # 5 — standard wire decode of tree + each history op.
    tree_result = decode_node(_json_dumps(obj["tree"]))
    if not tree_result.ok:
        raise TeleportError(TREE_DECODE, tree_result.error.message)  # type: ignore[union-attr]
    tree = tree_result.value  # type: ignore[union-attr]
    history: list[Obj] = []
    raw_history = obj.get("history")
    if isinstance(raw_history, list):
        for item in raw_history:
            op_result = decode_op(_json_dumps(item))
            if not op_result.ok:
                raise TeleportError(HISTORY_DECODE, op_result.error.message)  # type: ignore[union-attr]
            history.append(op_result.value)  # type: ignore[union-attr]
    # 6 — pre-emit validation (node-identity defects refuse).
    for finding in validate_node(tree):
        if finding.code in ("EMPTY_NODE_ID", "DUPLICATE_NODE_ID"):
            raise TeleportError(TREE_INVALID, f"tree has a node-identity defect: {finding.message}")
    # 7 — state re-seat.
    state: dict[str, Value] = {}
    raw_state = obj.get("state")
    if isinstance(raw_state, dict):
        state = {k: from_json(v) for k, v in raw_state.items()}
    chain_head = obj["chainHead"] if isinstance(obj.get("chainHead"), str) else None
    return Bundle(tree=tree, state=state, history=tuple(history), chain_head=chain_head)


def _is_hex64(s: str) -> bool:
    return len(s) == 64 and all(c in "0123456789abcdef" for c in s)


# ── deflate helpers (raw RFC 1951) ──────────────────────────────────────────


def _deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def _inflate(data: bytes) -> bytes:
    decompressor = zlib.decompressobj(-15)
    try:
        out = decompressor.decompress(data, _MAX_INFLATE + 1)
        if len(out) <= _MAX_INFLATE:
            out += decompressor.flush()
    except zlib.error as exc:
        raise TeleportError(INVALID_FORMAT, "inflate failed") from exc
    if len(out) > _MAX_INFLATE or decompressor.unconsumed_tail:
        raise TeleportError(OVERSIZE, "inflated output exceeds the decompression cap (bomb)")
    return out


# ── base64url (unpadded, matching Go's RawURLEncoding) ───────────────────────


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    # validate=True so a non-alphabet character is a hard error (InvalidFormat),
    # not silently discarded — matching Go's RawURLEncoding.DecodeString.
    return base64.b64decode(s.replace("-", "+").replace("_", "/") + "=" * (-len(s) % 4), validate=True)


def _json_loads(text: str) -> object:
    return json.loads(text)


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)
