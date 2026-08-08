import anyio
import sys
from servers.tool_registry import McpServersRegistry
from config import settings
import utils.opik_utils as opik_utils

def print_status(message: str, success: bool = True):
    indicator = "✅" if success else "❌"
    try:
        print(f"{indicator} {message}", file=sys.stderr)
    except UnicodeEncodeError:
        fallback = "[OK]" if success else "[FAIL]"
        print(f"{fallback} {message}", file=sys.stderr)

def main():
    mcp_tool_manager = McpServersRegistry()
    try:
        anyio.run(mcp_tool_manager.initialize)
        print_status("MCP Servers Registry initialized successfully", True)
    except Exception as e:
        print_status(f"MCP Servers Registry initialization failed: {e}", False)
        raise e
    
    if len(sys.argv) > 1 and sys.argv[1] == "sse":
        print_status(f"Starting Tool Registry server on http://localhost:{settings.REGISTRY_PORT}/mcp ...", True)
        mcp_tool_manager.get_registry().run(
            transport="streamable-http", host="localhost", port=settings.REGISTRY_PORT
        )
    else:
        print_status("Starting Tool Registry server via stdio transport...", True)
        mcp_tool_manager.get_registry().run()

if __name__ == "__main__":
    opik_utils.configure()
    main()