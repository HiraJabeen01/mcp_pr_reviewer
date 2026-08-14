import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from api import webhook
from config import Settings
from host.host import AutomationIncompleteError, ReviewResult


SECRET = "test-webhook-secret"


def make_settings(tmp_path) -> Settings:
    return Settings.model_validate(
        {
            "OPENAI_API_KEY": "test-openai-key",
            "OPENAI_MODEL": "test-model",
            "TOOL_REGISTRY_URL": "https://mcp.example.test/mcp",
            "SLACK_CHANNEL_ID": "C123",
            "GITHUB_WEBHOOK_SECRET": SECRET,
            "WEBHOOK_DELIVERY_DB": str(tmp_path / "deliveries.sqlite3"),
            "WEBHOOK_MAX_ATTEMPTS": 1,
            "WEBHOOK_RETRY_BASE_SECONDS": 0,
        }
    )


class FakeHost:
    def __init__(self, _settings):
        self.calls = []
        self.closed = False

    async def review_pull_request(self, **context):
        self.calls.append(context)
        return ReviewResult(
            response_id="resp_webhook",
            summary="done",
            called_tools=("slack_post_message", "asana_create_task"),
        )

    async def cleanup(self):
        self.closed = True


class PartialWriteHost(FakeHost):
    async def review_pull_request(self, **context):
        self.calls.append(context)
        raise AutomationIncompleteError(
            "Asana creation was not observed after Slack posted",
            called_tools=("slack_post_message",),
        )


def payload_bytes(action="opened") -> bytes:
    return json.dumps(
        {
            "action": action,
            "repository": {
                "name": "widgets",
                "owner": {"login": "acme"},
            },
            "pull_request": {
                "number": 42,
                "html_url": "https://github.com/acme/widgets/pull/42",
                "title": "FFM-2 Improve widgets",
                "body": "Implements the task.",
                "user": {"login": "octocat"},
            },
        },
        separators=(",", ":"),
    ).encode()


def signature(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def headers(body: bytes, delivery_id="delivery-42") -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": delivery_id,
        "X-Hub-Signature-256": signature(body),
    }


def test_opened_pull_request_runs_once_and_records_completion(tmp_path):
    fake_host = FakeHost(make_settings(tmp_path))
    app = webhook.create_app(
        settings_override=make_settings(tmp_path),
        host_factory=lambda _settings: fake_host,
    )
    body = payload_bytes()

    with TestClient(app) as client:
        response = client.post("/webhook", content=body, headers=headers(body))
        duplicate = client.post("/webhook", content=body, headers=headers(body))
        delivery = client.get("/deliveries/delivery-42")

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert duplicate.json()["status"] == "duplicate"
    assert len(fake_host.calls) == 1
    assert fake_host.calls[0]["owner"] == "acme"
    assert fake_host.calls[0]["pull_number"] == 42
    assert delivery.json()["status"] == "completed"
    assert delivery.json()["response_id"] == "resp_webhook"
    assert fake_host.closed is True


def test_invalid_signature_is_rejected(tmp_path):
    app = webhook.create_app(
        settings_override=make_settings(tmp_path),
        host_factory=FakeHost,
    )
    body = payload_bytes()
    invalid_headers = headers(body)
    invalid_headers["X-Hub-Signature-256"] = "sha256=invalid"

    with TestClient(app) as client:
        response = client.post("/webhook", content=body, headers=invalid_headers)

    assert response.status_code == 403


def test_non_opened_pull_request_is_ignored(tmp_path):
    fake_host = FakeHost(make_settings(tmp_path))
    app = webhook.create_app(
        settings_override=make_settings(tmp_path),
        host_factory=lambda _settings: fake_host,
    )
    body = payload_bytes(action="closed")

    with TestClient(app) as client:
        response = client.post("/webhook", content=body, headers=headers(body))

    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    assert fake_host.calls == []


def test_partial_write_failure_is_not_retried(tmp_path):
    runtime_settings = make_settings(tmp_path)
    runtime_settings.WEBHOOK_MAX_ATTEMPTS = 3
    partial_host = PartialWriteHost(runtime_settings)
    app = webhook.create_app(
        settings_override=runtime_settings,
        host_factory=lambda _settings: partial_host,
    )
    body = payload_bytes()

    with TestClient(app) as client:
        response = client.post("/webhook", content=body, headers=headers(body))
        delivery = client.get("/deliveries/delivery-42")

    assert response.status_code == 202
    assert len(partial_host.calls) == 1
    assert delivery.json()["status"] == "failed"
