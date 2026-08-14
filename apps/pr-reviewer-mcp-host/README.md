# Automated OpenAI PR Reviewer Host

This FastAPI service turns a GitHub `pull_request.opened` webhook into a fully
automated OpenAI Responses API run. OpenAI connects directly to the existing
remote MCP registry, reads the pull request and matching Asana requirements,
posts the review to Slack, and creates an Asana review task.

## Flow

1. GitHub sends a signed PR-open delivery to `POST /webhook`; the host validates
   the signature and queues the delivery once.
2. OpenAI calls the allowlisted GitHub, Asana, and Slack MCP tools and the host
   records the run as completed only after every required read and write call
   succeeds.

## Requirements

- Python 3.11+
- An OpenAI Platform API key. A ChatGPT subscription or an MCP registration in
  ChatGPT does not itself provide API credentials.
- A public HTTPS MCP registry URL that OpenAI can reach. `localhost` cannot be
  used as `TOOL_REGISTRY_URL`.
- GitHub, Slack, and Asana credentials configured in the MCP server project.
- A public HTTPS URL for this webhook host, such as an ngrok tunnel in local
  development.

## Configuration

Copy `.env.example` to `.env` and fill in the values:

```dotenv
OPENAI_API_KEY="<your_openai_platform_api_key>"
OPENAI_MODEL="gpt-5.6"
OPENAI_TIMEOUT_SECONDS="180"

TOOL_REGISTRY_URL="https://your-mcp-host.example.com/mcp"
MCP_AUTHORIZATION=""
SLACK_CHANNEL_ID="C01234567"

GITHUB_WEBHOOK_SECRET="<use_a_long_random_secret>"
WEBHOOK_DELIVERY_DB="webhook_deliveries.sqlite3"
WEBHOOK_MAX_ATTEMPTS="3"
WEBHOOK_RETRY_BASE_SECONDS="2"
WEBHOOK_PROCESSING_STALE_SECONDS="900"
```

`MCP_AUTHORIZATION` is optional. Set it only when the MCP endpoint expects an
OAuth access token. The webhook secret must exactly match the secret configured
in the GitHub repository webhook.

## Run

Start the remote MCP registry first. Then run the webhook host:

```powershell
cd apps/pr-reviewer-mcp-host
uv sync --group dev
uv run uvicorn api.webhook:app --app-dir src --host 0.0.0.0 --port 5001
```

Expose port `5001` over HTTPS for local testing:

```powershell
ngrok http 5001
```

In GitHub repository settings, create a webhook with:

- Payload URL: `https://<your-webhook-host>/webhook`
- Content type: `application/json`
- Secret: the value of `GITHUB_WEBHOOK_SECRET`
- Events: Pull requests only

The application verifies `X-Hub-Signature-256`, requires
`X-GitHub-Event: pull_request`, and processes only the `opened` action.

## Operations

- `GET /healthz` checks that the web service is running.
- `GET /deliveries/{X-GitHub-Delivery}` reports `processing`, `completed`, or
  `failed`, including the OpenAI response ID when available.
- Delivery IDs are stored in SQLite to prevent duplicate Slack messages and
  Asana tasks from repeated webhook deliveries.
- Failed OpenAI/MCP runs retry with exponential backoff only before any write
  tool may have executed. This avoids duplicating Slack messages or Asana tasks
  after a partial run. A delivery is marked completed only when all GitHub reads
  plus exactly one Slack post and exactly one Asana task creation are present in
  the OpenAI response.

## Tests

The tests are isolated from GitHub, OpenAI, Slack, and Asana; they use mock API
responses and a temporary SQLite database.

```powershell
cd apps/pr-reviewer-mcp-host
uv run --group dev pytest -q
```

## Security notes

The OpenAI request imports only these MCP tools:

- `github_get_pull_request`
- `github_get_pull_request_diff`
- `github_get_pull_request_files`
- `asana_find_task`
- `slack_post_message`
- `asana_create_task`

Automatic approval is enabled for those tools because this is a headless
workflow. Only use it with an MCP registry you control, keep its credentials
scoped to the intended repositories/projects/channel, and protect the registry
with authentication when it is exposed publicly.
