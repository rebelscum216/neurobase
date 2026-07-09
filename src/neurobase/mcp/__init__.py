"""MCP server (stdio) — on-demand memory for any MCP client.

Tools-only baseline: memory_search, memory_read_node, memory_list_projects,
memory_remember, recommendations_list. Claude-only sugar: nodes as resources, a
recall prompt. See :mod:`neurobase.mcp.server`.
"""

from __future__ import annotations

from neurobase.mcp.server import build_server, serve

__all__ = ["build_server", "serve"]
