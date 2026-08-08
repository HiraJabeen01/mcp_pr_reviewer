import logging
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx
import opik
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from config import settings
import utils.opik_utils as opik_utils

opik_utils.configure()
logger = logging.getLogger("github_server")
logging.basicConfig(level=logging.INFO)

GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"
GITHUB_HTTP_TIMEOUT_SECONDS = 30.0
GITHUB_SSE_READ_TIMEOUT_SECONDS = 300.0
SERVER_CONFIG = {
    "url": GITHUB_MCP_URL,
    "headers": {
        "Authorization": f"Bearer {settings.GITHUB_ACCESS_TOKEN}",
        "Accept": "text/event-stream",
    },
}
REMOTE_PULL_REQUEST_METHODS = {
    "get_pull_request": "get",
    "get_pull_request_comments": "get_comments",
    "get_pull_request_diff": "get_diff",
    "get_pull_request_files": "get_files",
    "get_pull_request_reviews": "get_reviews",
    "get_pull_request_status": "get_status",
}

github_mcp = FastMCP("github_proxy")


def _sanitize_error_message(error: BaseException | str) -> str:
    """Return an actionable error message without credentials."""
    if isinstance(error, BaseExceptionGroup):
        message = "; ".join(
            _sanitize_error_message(nested_error)
            for nested_error in error.exceptions
        )
    else:
        message = str(error).strip() or type(error).__name__
    authorization = SERVER_CONFIG.get("headers", {}).get("Authorization", "")
    secrets = (settings.GITHUB_ACCESS_TOKEN, authorization)

    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTED]")

    message = re.sub(
        r"(?i)\bbearer\s+[^\s,;'\"\]\}]+",
        "Bearer [REDACTED]",
        message,
    )
    return message


def _remote_error_message(result: Any) -> str:
    messages = [
        content.text
        for content in getattr(result, "content", [])
        if isinstance(getattr(content, "text", None), str)
    ]
    if not messages:
        return "the remote GitHub MCP server returned an error"
    return _sanitize_error_message("; ".join(messages))


@asynccontextmanager
async def _github_session() -> AsyncIterator[ClientSession]:
    """Open one authenticated HTTP client for the complete MCP session."""
    async with httpx.AsyncClient(
        headers=dict(SERVER_CONFIG.get("headers", {})),
        follow_redirects=True,
        timeout=httpx.Timeout(
            GITHUB_HTTP_TIMEOUT_SECONDS,
            read=GITHUB_SSE_READ_TIMEOUT_SECONDS,
        ),
    ) as http_client:
        async with streamable_http_client(
            SERVER_CONFIG["url"],
            http_client=http_client,
        ) as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session


async def _call_github_tool(tool_name: str, arguments: dict[str, Any]):
    try:
        async with _github_session() as session:
            method = REMOTE_PULL_REQUEST_METHODS[tool_name]
            result = await session.call_tool(
                "pull_request_read",
                {**arguments, "method": method},
            )
            if getattr(result, "isError", False):
                raise ToolError(
                    f"GitHub MCP tool '{tool_name}' failed: "
                    f"{_remote_error_message(result)}"
                )
            return result
    except ToolError:
        raise
    except Exception as error:
        message = _sanitize_error_message(error)
        logger.error("GitHub MCP tool '%s' failed: %s", tool_name, message)
        raise ToolError(
            f"GitHub MCP tool '{tool_name}' failed: {message}"
        ) from None



@github_mcp.tool(
    description="Get pull request details",
    tags={"github", "pull_request", "details"},
    annotations={"title": "Get Pull Request", "readOnlyHint": True, "openWorldHint": True},
)
@opik.track(name="github-get-pull-request", type="tool")
async def get_pull_request(owner: str, repo: str, pullNumber: int):
    logger.info("Fetching pull request %s for %s/%s", pullNumber, owner, repo)
    return await _call_github_tool(
        "get_pull_request",
        {"owner": owner, "repo": repo, "pullNumber": pullNumber},
    )


@github_mcp.tool(
    description="Get pull request comments",
    tags={"github", "pull_request", "comments"},
    annotations={"title": "Get Pull Request Comments", "readOnlyHint": True, "openWorldHint": True},
)
@opik.track(name="github-get-pull-request-comments", type="tool")
async def get_pull_request_comments(owner: str, repo: str, pullNumber: int):
    return await _call_github_tool(
        "get_pull_request_comments",
        {"owner": owner, "repo": repo, "pullNumber": pullNumber},
    )

@github_mcp.tool(
    description="Get pull request diff",
    tags={"github", "pull_request", "diff"},
    annotations={"title": "Get Pull Request Diff", "readOnlyHint": True, "openWorldHint": True},
)
@opik.track(name="github-get-pull-request-diff", type="tool")
async def get_pull_request_diff(owner: str, repo: str, pullNumber: int):
    return await _call_github_tool(
        "get_pull_request_diff",
        {"owner": owner, "repo": repo, "pullNumber": pullNumber},
    )


@github_mcp.tool(
    description="Get pull request files",
    tags={"github", "pull_request", "files"},
    annotations={"title": "Get Pull Request Files", "readOnlyHint": True, "openWorldHint": True},
)
@opik.track(name="github-get-pull-request-files", type="tool")
async def get_pull_request_files(owner: str, repo: str, pullNumber: int, page: int = 1, perPage: int = 100):
    return await _call_github_tool(
        "get_pull_request_files",
        {
            "owner": owner,
            "repo": repo,
            "pullNumber": pullNumber,
            "page": page,
            "perPage": perPage,
        },
    )


@github_mcp.tool(
    description="Get pull request reviews",
    tags={"github", "pull_request", "reviews"},
    annotations={"title": "Get Pull Request Reviews", "readOnlyHint": True, "openWorldHint": True},
)
@opik.track(name="github-get-pull-request-reviews", type="tool")
async def get_pull_request_reviews(owner: str, repo: str, pullNumber: int):
    return await _call_github_tool(
        "get_pull_request_reviews",
        {"owner": owner, "repo": repo, "pullNumber": pullNumber},
    )


@github_mcp.tool(
    description="Get pull request status checks",
    tags={"github", "pull_request", "status"},
    annotations={"title": "Get Pull Request Status", "readOnlyHint": True, "openWorldHint": True},
)
@opik.track(name="github-get-pull-request-status", type="tool")
async def get_pull_request_status(owner: str, repo: str, pullNumber: int):
    return await _call_github_tool(
        "get_pull_request_status",
        {"owner": owner, "repo": repo, "pullNumber": pullNumber},
    )

# github_mcp.run(transport="streamable-http", host="localhost", port=8004)
