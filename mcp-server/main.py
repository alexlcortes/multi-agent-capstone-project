import os

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

load_dotenv()

mcp = MCPServer("wire3-gtm-research")


@mcp.tool()
def health_check() -> dict:
    """Reports server status and which search provider is configured, without ever returning key values."""
    return {
        "status": "ok",
        "serpapi_configured": bool(os.environ.get("SERPAPI_API_KEY")),
        "tavily_configured": bool(os.environ.get("TAVILY_API_KEY")),
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
