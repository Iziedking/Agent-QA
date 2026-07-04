"""MCP server layer for Agent QA (Step 3).

Wraps the reliability engine as a Model Context Protocol server with FastMCP,
exposing a single tool, ``evaluate_mcp_endpoint``, that any AI can call.
"""

__version__ = "0.1.0"
