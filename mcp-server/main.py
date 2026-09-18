import os

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from research import tools

load_dotenv()

mcp = MCPServer("wire3-gtm-research")


@mcp.tool()
def health_check() -> dict:
    """Reports server status and which search provider is configured/active, without ever returning key values."""
    return {
        "status": "ok",
        "active_provider": os.environ.get("SEARCH_PROVIDER", "tavily"),
        "serpapi_configured": bool(os.environ.get("SERPAPI_API_KEY")),
        "tavily_configured": bool(os.environ.get("TAVILY_API_KEY")),
    }


mcp.tool()(tools.company_overview)
mcp.tool()(tools.competitor_discovery)
mcp.tool()(tools.product_portfolio_mapping)
mcp.tool()(tools.pricing_research)
mcp.tool()(tools.recent_news)
mcp.tool()(tools.validate_source)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
