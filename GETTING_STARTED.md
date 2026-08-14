# Getting Started: Automated OpenAI PR Reviewer

The project has two running services:

1. The MCP registry exposes GitHub read tools, Asana lookup/create tools, and
   Slack message tools.
2. The webhook host receives a signed GitHub PR-open event and starts an OpenAI
   Responses API run against that remote MCP registry.

When the run succeeds, the complete review appears in Slack and a `PR Review`
task containing the same review is created in Asana.

## 1. Configure the MCP registry

Create `apps/pr-reviewer-mcp-servers/.env`:

```dotenv
ASANA_TOKEN="<your_asana_token>"
ASANA_PROJECT_GID="<your_asana_project_gid>"

SLACK_CLIENT_ID="<your_slack_client_id>"
SLACK_CLIENT_SECRET="<your_slack_client_secret>"
SLACK_BOT_TOKEN="xoxb-..."

GITHUB_CLIENT_ID="<your_github_client_id>"
GITHUB_CLIENT_SECRET="<your_github_client_secret>"
GITHUB_ACCESS_TOKEN="<your_github_access_token>"
```

Start the registry:

```powershell
cd apps/pr-reviewer-mcp-servers
uv sync
uv run python -u src/main.py sse
```

OpenAI must be able to reach the registry over public HTTPS. Expose port 8000
through your hosted environment, a secure MCP tunnel, or an HTTPS development
tunnel. Keep the resulting `/mcp` URL for `TOOL_REGISTRY_URL`.

## 2. Configure the OpenAI webhook host

Create `apps/pr-reviewer-mcp-host/.env` from `.env.example`:

```dotenv
OPENAI_API_KEY="<your_openai_platform_api_key>"
OPENAI_MODEL="gpt-5.6"

TOOL_REGISTRY_URL="https://your-public-mcp-host.example.com/mcp"
MCP_AUTHORIZATION=""
SLACK_CHANNEL_ID="C01234567"

GITHUB_WEBHOOK_SECRET="<use_a_long_random_secret>"
WEBHOOK_DELIVERY_DB="webhook_deliveries.sqlite3"
WEBHOOK_MAX_ATTEMPTS="3"
WEBHOOK_RETRY_BASE_SECONDS="2"
WEBHOOK_PROCESSING_STALE_SECONDS="900"
```

The OpenAI Platform API key is separate from your ChatGPT subscription. Your
existing ChatGPT MCP registration can continue to use the same MCP server URL,
but automatic webhook runs use the Responses API credentials above.

Start the host:

```powershell
cd apps/pr-reviewer-mcp-host
uv sync --group dev
uv run uvicorn api.webhook:app --app-dir src --host 0.0.0.0 --port 5001
```

## 3. Register the GitHub webhook

Expose port 5001 through public HTTPS, then create a repository webhook:

- Payload URL: `https://<your-host>/webhook`
- Content type: `application/json`
- Secret: exactly the value of `GITHUB_WEBHOOK_SECRET`
- Event selection: Pull requests

Opening a PR now returns an accepted delivery immediately. Use the delivery GUID
shown in GitHub's webhook delivery headers to inspect processing:

```text
GET https://<your-host>/deliveries/<X-GitHub-Delivery>
```

A successful response has `status: completed` and an OpenAI `response_id`.

## 4. Verify before using a real PR

Run the host's isolated tests:

```powershell
cd apps/pr-reviewer-mcp-host
uv run --group dev pytest -q
```

Then verify:

- `GET /healthz` returns `{"status":"ok"}`.
- The MCP URL is public and reachable, not `localhost`.
- The Slack bot can post to `SLACK_CHANNEL_ID`.
- The Asana token can read and create tasks in `ASANA_PROJECT_GID`.
- GitHub webhook deliveries show HTTP 202 for newly opened pull requests.

## Manual ChatGPT usage

Registering the MCP server with ChatGPT remains useful for interactive reviews.
In a chat, provide a PR URL and ask ChatGPT to use the GitHub, Asana, and Slack
tools. That manual path is independent of the automated webhook host.
