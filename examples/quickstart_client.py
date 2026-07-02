"""Quickstart — the Fuaran generation endpoint from Python in ~10 lines.

Construct a client, run a fresh generation, read the decoded tree, then let
the session carry the tree so the next prompt is a cheap repair diff.

Credentials come from the environment (never commit either one):
``FUARAN_ENDPOINT`` — the generation endpoint URL (or your own proxy path);
``FUARAN_ACCESS_TOKEN`` — the paid access token; ``PROVIDER_API_KEY`` — your
BYOK LLM-provider key. In a server-proxied deployment omit both credentials
and point the endpoint at your proxy — see the README's "BYOK key and access
token" section.
"""

from __future__ import annotations

import os

from fuaran_py.client import FuaranClient, FuaranSession, Produced

client = FuaranClient(
    os.environ["FUARAN_ENDPOINT"],
    access_token=os.environ["FUARAN_ACCESS_TOKEN"],
    provider_key=os.environ["PROVIDER_API_KEY"],
)
session = FuaranSession(client)

result = session.next("a metric card showing monthly revenue")  # fresh generation
if isinstance(result, Produced):
    decoded = result.decode_tree()  # typed Node via the wire codec
    print(f"surface {result.version}: tree root {decoded.value.id if decoded.ok else decoded.error.code}")

result = session.next("rename the metric to ARR")  # repair diff against the held tree
if isinstance(result, Produced):
    print(f"repaired with {len(result.ops)} op(s)")
else:
    print(f"turn did not produce: {result}")
