import json
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

os.environ.update(
    {
        "ASANA_TOKEN": "unit-test-asana-token",
        "ASANA_PROJECT_GID": "unit-test-project",
        "SLACK_CLIENT_ID": "unit-test-slack-client",
        "SLACK_CLIENT_SECRET": "unit-test-slack-secret",
        "SLACK_BOT_TOKEN": "unit-test-slack-token",
        "GITHUB_CLIENT_ID": "unit-test-github-client",
        "GITHUB_CLIENT_SECRET": "unit-test-github-secret",
        "GITHUB_ACCESS_TOKEN": "unit-test-github-token",
        "OPIK_API_KEY": "",
    }
)


def _identity_track(*args, **kwargs):
    return lambda function: function


_missing_module = object()
_previous_opik = sys.modules.get("opik", _missing_module)
_previous_opik_utils = sys.modules.get("utils.opik_utils", _missing_module)
try:
    sys.modules["opik"] = SimpleNamespace(track=_identity_track)
    sys.modules["utils.opik_utils"] = SimpleNamespace(configure=lambda: None)
    from servers import github_server, slack_server  # noqa: E402
finally:
    if _previous_opik is _missing_module:
        sys.modules.pop("opik", None)
    else:
        sys.modules["opik"] = _previous_opik
    if _previous_opik_utils is _missing_module:
        sys.modules.pop("utils.opik_utils", None)
    else:
        sys.modules["utils.opik_utils"] = _previous_opik_utils


class FakeHttpClient:
    def __init__(self, state: SimpleNamespace, **kwargs: Any):
        self.state = state
        self.kwargs = kwargs
        self.is_closed = True
        state.http_client = self
        state.http_kwargs = kwargs

    async def __aenter__(self):
        self.is_closed = False
        self.state.events.append("http_enter")
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.state.events.append("http_exit")
        self.is_closed = True


class FakeTransport:
    def __init__(self, state: SimpleNamespace, http_client: FakeHttpClient):
        self.state = state
        self.http_client = http_client

    async def __aenter__(self):
        assert not self.http_client.is_closed
        self.state.events.append("transport_enter")
        return "read-stream", "write-stream", lambda: None

    async def __aexit__(self, exc_type, exc, traceback):
        assert not self.http_client.is_closed
        self.state.events.append("transport_exit")


class FakeSession:
    def __init__(self, state: SimpleNamespace, read_stream: Any, write_stream: Any):
        self.state = state
        self.read_stream = read_stream
        self.write_stream = write_stream

    async def __aenter__(self):
        assert not self.state.http_client.is_closed
        self.state.events.append("session_enter")
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        assert not self.state.http_client.is_closed
        self.state.events.append("session_exit")

    async def initialize(self):
        self.state.events.append("initialize")
        if self.state.failure_stage == "initialize":
            self._raise_failure()

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]):
        self.state.events.append("call_tool")
        self.state.tool_call = (tool_name, arguments)
        if self.state.failure_stage == "call_tool":
            self._raise_failure()
        return self.state.result

    def _raise_failure(self):
        if isinstance(self.state.error_message, BaseException):
            raise self.state.error_message
        raise RuntimeError(self.state.error_message)


def install_transport_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failure_stage: str | None = None,
    error_message: str = "simulated failure",
    result: Any = None,
) -> SimpleNamespace:
    state = SimpleNamespace(
        events=[],
        failure_stage=failure_stage,
        error_message=error_message,
        result=result if result is not None else SimpleNamespace(isError=False),
    )

    monkeypatch.setattr(
        github_server.httpx,
        "AsyncClient",
        lambda **kwargs: FakeHttpClient(state, **kwargs),
    )

    def fake_streamable_http_client(url: str, **kwargs: Any):
        state.transport_url = url
        state.transport_kwargs = kwargs
        assert "headers" not in kwargs
        assert kwargs["http_client"] is state.http_client
        return FakeTransport(state, kwargs["http_client"])

    monkeypatch.setattr(
        github_server,
        "streamable_http_client",
        fake_streamable_http_client,
    )
    monkeypatch.setattr(
        github_server,
        "ClientSession",
        lambda read_stream, write_stream: FakeSession(
            state,
            read_stream,
            write_stream,
        ),
    )
    return state


@pytest.mark.asyncio
async def test_transport_uses_authenticated_http_client_for_entire_session(monkeypatch):
    token = "transport-test-token"
    monkeypatch.setitem(
        github_server.SERVER_CONFIG["headers"],
        "Authorization",
        f"Bearer {token}",
    )
    state = install_transport_fakes(monkeypatch)

    result = await github_server._call_github_tool(
        "get_pull_request",
        {"owner": "owner", "repo": "repo", "pullNumber": 3},
    )

    assert result is state.result
    assert state.transport_kwargs == {"http_client": state.http_client}
    assert state.tool_call == (
        "pull_request_read",
        {
            "owner": "owner",
            "repo": "repo",
            "pullNumber": 3,
            "method": "get",
        },
    )
    assert state.http_kwargs["headers"]["Authorization"] == f"Bearer {token}"
    assert state.http_kwargs["follow_redirects"] is True
    assert state.http_kwargs["timeout"].connect == 30.0
    assert state.http_kwargs["timeout"].read == 300.0
    assert state.events == [
        "http_enter",
        "transport_enter",
        "session_enter",
        "initialize",
        "call_tool",
        "session_exit",
        "transport_exit",
        "http_exit",
    ]
    assert state.http_client.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["initialize", "call_tool"])
async def test_resources_close_when_session_work_fails(monkeypatch, failure_stage):
    state = install_transport_fakes(monkeypatch, failure_stage=failure_stage)

    with pytest.raises(ToolError, match="simulated failure"):
        await github_server._call_github_tool("get_pull_request", {})

    assert state.http_client.is_closed
    assert state.events[-3:] == ["session_exit", "transport_exit", "http_exit"]


@pytest.mark.asyncio
async def test_tokens_are_redacted_from_errors_and_logs(monkeypatch, caplog):
    token = "github_pat_do-not-leak"
    monkeypatch.setattr(github_server.settings, "GITHUB_ACCESS_TOKEN", token)
    monkeypatch.setitem(
        github_server.SERVER_CONFIG["headers"],
        "Authorization",
        f"Bearer {token}",
    )
    state = install_transport_fakes(
        monkeypatch,
        failure_stage="call_tool",
        error_message=ExceptionGroup(
            "unhandled errors in a TaskGroup",
            [RuntimeError(f"request failed with Authorization: Bearer {token}")],
        ),
    )

    with caplog.at_level(logging.ERROR, logger="github_server"):
        with pytest.raises(ToolError) as exc_info:
            await github_server._call_github_tool("get_pull_request", {})

    assert token not in str(exc_info.value)
    assert token not in caplog.text
    assert "[REDACTED]" in str(exc_info.value)
    assert "request failed with Authorization" in str(exc_info.value)
    assert state.http_client.is_closed


@pytest.mark.asyncio
async def test_remote_github_errors_become_useful_tool_errors(monkeypatch):
    remote_error = SimpleNamespace(
        isError=True,
        content=[SimpleNamespace(text="GitHub API returned 404: pull request not found")],
    )
    state = install_transport_fakes(monkeypatch, result=remote_error)

    with pytest.raises(ToolError) as exc_info:
        await github_server._call_github_tool("get_pull_request", {})

    message = str(exc_info.value)
    assert "get_pull_request" in message
    assert "GitHub API returned 404: pull request not found" in message
    assert "Traceback" not in message
    assert state.http_client.is_closed


@pytest.mark.asyncio
async def test_slack_tools_are_unchanged_and_use_only_a_mock_client(monkeypatch):
    await slack_server.slack_client.client.aclose()

    class FakeSlackClient:
        async def get_last_messages(self, channel_name: str, limit: int):
            return [{"channel": channel_name, "limit": limit}]

        async def send_message(self, channel_name: str, message: str):
            return {"ok": True, "channel": channel_name, "message": message}

    monkeypatch.setattr(slack_server, "slack_client", FakeSlackClient())

    async with Client(slack_server.slack_mcp) as client:
        history = await client.call_tool(
            "get_last_messages",
            {"channel_name": "test-channel", "limit": 2},
        )
        posted = await client.call_tool(
            "post_message",
            {"channel_name": "test-channel", "message": "mock-only"},
        )

    history_payload = json.loads(history.content[0].text)
    posted_payload = json.loads(posted.content[0].text)
    assert history_payload == {
        "status": "success",
        "messages": [{"channel": "test-channel", "limit": 2}],
    }
    assert posted_payload["status"] == "created"
    assert posted_payload["message"]["ok"] is True
