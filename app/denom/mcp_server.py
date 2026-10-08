"""MCP server over the denomination knowledge base (D2; INTERFACES §7).

Run (stdio):            python -m app.denom.mcp_server
Run (HTTP, open endpoint bonus): python -m app.denom.mcp_server --http 8765
Works with mcp 2.x (MCPServer) and mcp 1.x (FastMCP).
"""
from __future__ import annotations

import argparse
import json

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from ..models import PreferenceProfile
from .kb import get_kb

INSTRUCTIONS = ("Reference data about 217 US religious groups (2020 US Religion Census) with sourced fields. "
                "Values are what a denomination TYPICALLY holds, not facts about a particular congregation. "
                "Sensitive fields are only returned when original or human-reviewed.")

server = _Server(name="churchfocus-denominations", instructions=INSTRUCTIONS)


@server.tool()
def find_denomination(text: str, k: int = 5) -> list[dict]:
    """Find denominations by name, abbreviation (e.g. 'ELCA', 'PCA') or a church's name ('First Mennonite Church')."""
    return get_kb().find(text, k)


@server.tool()
def get_denomination(denomination_id: str) -> dict:
    """All known fields for one denomination id, each with its value, status and up to two source quotes."""
    return get_kb().get(denomination_id)


@server.tool()
def compare_denominations(a: str, b: str, features: list[str]) -> list[dict]:
    """Compare two denominations on ChurchFocus feature ids (see contracts/features.yaml)."""
    return get_kb().compare(a, b, features)


@server.tool()
def match_profile(profile_json: str, k: int = 8) -> list[dict]:
    """Likely denominations for a ChurchFocus PreferenceProfile (JSON string), best first, with reasons."""
    return get_kb().match_profile(PreferenceProfile.model_validate(json.loads(profile_json)), k)


@server.tool()
def denomination_prior(denomination_id: str, feature_id: str) -> dict | None:
    """What a denomination typically holds for one feature id, with its source. None if unknown."""
    e = get_kb().prior(denomination_id, feature_id)
    return e.model_dump(mode="json") if e else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", type=int, help="serve streamable HTTP on this port instead of stdio")
    a = ap.parse_args()
    if a.http:
        try:
            server.run("streamable-http", port=a.http)
        except TypeError:   # older SDKs take host/port from settings
            server.settings.port = a.http
            server.run("streamable-http")
    else:
        server.run()


if __name__ == "__main__":
    main()
