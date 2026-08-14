import json
from types import SimpleNamespace

import pytest

from config import Settings
from host.host import AutomationIncompleteError, MCPHost, MCP_TOOL_NAMES


def make_settings(tmp_path) -> Settings:
    return Settings.model_validate(
        {
            "OPENAI_API_KEY": "test-openai-key",
            "OPENAI_MODEL": "test-model",
            "TOOL_REGISTRY_URL": "https://mcp.example.test/mcp",
            "SLACK_CHANNEL_ID": "C123",
            "GITHUB_WEBHOOK_SECRET": "test-webhook-secret",
            "WEBHOOK_DELIVERY_DB": str(tmp_path / "deliveries.sqlite3"),
        }
    )


def mcp_call(name: str, output: str = '{"status":"success"}'):
    return SimpleNamespace(type="mcp_call", name=name, error=None, output=output)


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


class FakeOpenAI:
    def __init__(self, response):
        self.responses = FakeResponses(response)
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_review_uses_allowlisted_mcp_tools_and_completes_writes(tmp_path):
    output = [
        mcp_call("github_get_pull_request", "{}"),
        mcp_call("github_get_pull_request_diff", "diff"),
        mcp_call("github_get_pull_request_files", "[]"),
        mcp_call("asana_find_task", '{"status":"not_found"}'),
        mcp_call("slack_post_message", '{"status":"created"}'),
        mcp_call("asana_create_task", '{"status":"created"}'),
    ]
    response = SimpleNamespace(
        id="resp_test",
        output=output,
        output_text="Review posted and task created.",
    )
    fake_openai = FakeOpenAI(response)
    host = MCPHost(settings=make_settings(tmp_path), client=fake_openai)

    result = await host.review_pull_request(
        owner="acme",
        repo="widgets",
        pull_number=42,
        pr_url="https://github.com/acme/widgets/pull/42",
        title="FFM-2 Improve widgets",
        body="Implements the widget requirement.",
        author="octocat",
        delivery_id="delivery-42",
    )

    request = fake_openai.responses.kwargs
    assert request["model"] == "test-model"
    assert request["tool_choice"] == "required"
    assert request["tools"][0]["allowed_tools"] == list(MCP_TOOL_NAMES)
    assert request["tools"][0]["require_approval"] == "never"
    workflow_input = json.loads(request["input"])
    assert workflow_input["pull_request_number"] == 42
    assert workflow_input["slack_channel_id"] == "C123"
    assert result.response_id == "resp_test"
    assert result.called_tools.count("slack_post_message") == 1
    assert result.called_tools.count("asana_create_task") == 1


@pytest.mark.asyncio
async def test_review_fails_if_a_required_write_did_not_happen(tmp_path):
    response = SimpleNamespace(
        id="resp_incomplete",
        output=[
            mcp_call("github_get_pull_request", "{}"),
            mcp_call("github_get_pull_request_diff", "diff"),
            mcp_call("github_get_pull_request_files", "[]"),
            mcp_call("slack_post_message", '{"status":"created"}'),
        ],
        output_text="Incomplete",
    )
    host = MCPHost(settings=make_settings(tmp_path), client=FakeOpenAI(response))

    with pytest.raises(AutomationIncompleteError, match="asana_create_task"):
        await host.review_pull_request(
            owner="acme",
            repo="widgets",
            pull_number=42,
            pr_url="https://github.com/acme/widgets/pull/42",
            title="Review me",
            body="",
            author="octocat",
            delivery_id="delivery-42",
        )


@pytest.mark.asyncio
async def test_review_fails_on_unsuccessful_slack_result(tmp_path):
    response = SimpleNamespace(
        id="resp_failed",
        output=[
            mcp_call("github_get_pull_request", "{}"),
            mcp_call("github_get_pull_request_diff", "diff"),
            mcp_call("github_get_pull_request_files", "[]"),
            mcp_call("slack_post_message", '{"status":"error"}'),
            mcp_call("asana_create_task", '{"status":"created"}'),
        ],
        output_text="Failed",
    )
    host = MCPHost(settings=make_settings(tmp_path), client=FakeOpenAI(response))

    with pytest.raises(AutomationIncompleteError, match="slack_post_message"):
        await host.review_pull_request(
            owner="acme",
            repo="widgets",
            pull_number=42,
            pr_url="https://github.com/acme/widgets/pull/42",
            title="Review me",
            body="",
            author="octocat",
            delivery_id="delivery-42",
        )
