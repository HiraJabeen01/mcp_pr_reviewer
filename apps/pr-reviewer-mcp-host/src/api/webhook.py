import asyncio
import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from typing import Callable

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, status

from config import Settings, get_settings
from host.delivery_store import DeliveryStore
from host.host import AutomationIncompleteError, MCPHost, REQUIRED_WRITE_TOOLS


logger = logging.getLogger("github_webhook")
logging.basicConfig(level=logging.INFO)


def verify_github_signature(
    payload_body: bytes,
    secret: str,
    signature_header: str | None,
) -> None:
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-Hub-Signature-256 header is missing.",
        )
    expected_signature = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        msg=payload_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, signature_header):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook signature is invalid.",
        )


def _pull_request_context(payload: dict, delivery_id: str) -> dict:
    try:
        pull_request = payload["pull_request"]
        repository = payload["repository"]
        owner = repository["owner"]["login"]
        repo = repository["name"]
        pull_number = int(pull_request["number"])
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Malformed pull_request webhook payload: {error}",
        ) from None

    return {
        "owner": owner,
        "repo": repo,
        "pull_number": pull_number,
        "pr_url": str(pull_request.get("html_url") or pull_request.get("url") or ""),
        "title": str(pull_request.get("title") or ""),
        "body": str(pull_request.get("body") or ""),
        "author": str((pull_request.get("user") or {}).get("login") or "unknown"),
        "delivery_id": delivery_id,
    }


async def _run_delivery(app: FastAPI, delivery_id: str, context: dict) -> None:
    runtime_settings: Settings = app.state.settings
    last_error: Exception | None = None

    for attempt in range(1, runtime_settings.WEBHOOK_MAX_ATTEMPTS + 1):
        try:
            result = await app.state.host.review_pull_request(**context)
            app.state.delivery_store.complete(delivery_id, result.response_id)
            logger.info(
                "GitHub delivery %s completed through OpenAI response %s",
                delivery_id,
                result.response_id,
            )
            return
        except Exception as error:
            last_error = error
            logger.exception(
                "GitHub delivery %s failed on attempt %s/%s",
                delivery_id,
                attempt,
                runtime_settings.WEBHOOK_MAX_ATTEMPTS,
            )
            write_may_have_happened = isinstance(
                error, AutomationIncompleteError
            ) and bool(REQUIRED_WRITE_TOOLS.intersection(error.called_tools))
            if write_may_have_happened:
                logger.error(
                    "Not retrying delivery %s because a write tool may already have run",
                    delivery_id,
                )
                break
            if attempt < runtime_settings.WEBHOOK_MAX_ATTEMPTS:
                delay = runtime_settings.WEBHOOK_RETRY_BASE_SECONDS * (2 ** (attempt - 1))
                await asyncio.sleep(delay)

    error_message = str(last_error) if last_error else "Unknown automation failure"
    app.state.delivery_store.fail(delivery_id, error_message)


def create_app(
    settings_override: Settings | None = None,
    host_factory: Callable[[Settings], MCPHost] = MCPHost,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime_settings = settings_override or get_settings()
        app.state.settings = runtime_settings
        app.state.host = host_factory(runtime_settings)
        app.state.delivery_store = DeliveryStore(runtime_settings.WEBHOOK_DELIVERY_DB)
        try:
            yield
        finally:
            await app.state.host.cleanup()

    application = FastAPI(lifespan=lifespan)

    @application.get("/healthz")
    async def healthcheck():
        return {"status": "ok"}

    @application.get("/deliveries/{delivery_id}")
    async def delivery_status(request: Request, delivery_id: str):
        delivery = request.app.state.delivery_store.status(delivery_id)
        if delivery is None:
            raise HTTPException(status_code=404, detail="Delivery not found.")
        return {
            key: delivery[key]
            for key in (
                "delivery_id",
                "status",
                "attempts",
                "response_id",
                "updated_at",
            )
        }

    @application.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
    async def handle_github_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
    ):
        raw_body = await request.body()
        verify_github_signature(
            raw_body,
            request.app.state.settings.GITHUB_WEBHOOK_SECRET,
            request.headers.get("X-Hub-Signature-256"),
        )

        try:
            payload = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Webhook body is not valid JSON.",
            ) from None

        event_name = request.headers.get("X-GitHub-Event")
        action = payload.get("action") if isinstance(payload, dict) else None
        if event_name != "pull_request" or action != "opened":
            return {
                "status": "ignored",
                "event": event_name,
                "action": action,
            }

        delivery_id = request.headers.get("X-GitHub-Delivery")
        if not delivery_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="X-GitHub-Delivery header is missing.",
            )

        context = _pull_request_context(payload, delivery_id)
        claimed = request.app.state.delivery_store.claim(
            delivery_id,
            request.app.state.settings.WEBHOOK_PROCESSING_STALE_SECONDS,
        )
        if not claimed:
            return {"status": "duplicate", "delivery_id": delivery_id}

        background_tasks.add_task(
            _run_delivery,
            request.app,
            delivery_id,
            context,
        )
        logger.info(
            "Accepted PR-open delivery %s for %s/%s#%s",
            delivery_id,
            context["owner"],
            context["repo"],
            context["pull_number"],
        )
        return {"status": "accepted", "delivery_id": delivery_id}

    return application


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5001)
