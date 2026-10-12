"""FastAPI app exposing the internal email-sending API."""

import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, model_validator

from app.config import load_settings
from app.db import create_pool, ensure_schema, insert_email
from app.worker import run_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("email-service")


class SendRequest(BaseModel):
    """The JSON body accepted by POST /send."""

    to: str
    subject: str
    html: str | None = None
    text: str | None = None
    from_address: str | None = None
    reply_to: str | None = None
    project: str | None = None

    @model_validator(mode="after")
    def require_html_or_text(self):
        """Reject the request when both html and text are missing."""
        if not self.html and not self.text:
            raise ValueError("at least one of html or text is required")
        return self


@asynccontextmanager
async def lifespan(app):
    """Open the DB pool, start the worker, then stop both cleanly on shutdown."""
    settings = load_settings()
    pool = await create_pool(settings.database_url)
    await ensure_schema(pool)
    stop_event = asyncio.Event()
    worker = asyncio.create_task(run_worker(settings, pool, stop_event))
    app.state.settings = settings
    app.state.pool = pool
    yield
    stop_event.set()
    await worker
    await pool.close()


app = FastAPI(title="email-service", lifespan=lifespan)


@app.get("/health")
async def health():
    """Report that the service is up (used by uptime checks, no auth)."""
    return {"ok": True}


@app.post("/send")
async def send(
    payload: SendRequest,
    request: Request,
    x_internal_token: str | None = Header(default=None),
):
    """Check the shared token, queue the email in the outbox, and return its id."""
    settings = request.app.state.settings
    provided = x_internal_token.encode("utf-8") if x_internal_token is not None else None
    expected = settings.internal_token.encode("utf-8")
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid or missing token")

    email_id = await insert_email(
        request.app.state.pool,
        to=payload.to,
        subject=payload.subject,
        html=payload.html,
        text=payload.text,
        from_address=payload.from_address or settings.default_from,
        reply_to=payload.reply_to,
        project=payload.project,
    )
    log.info("queued id=%s project=%s", email_id, payload.project)
    return {"id": str(email_id), "status": "queued"}
