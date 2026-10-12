"""Background loop that sends queued emails through the Resend API."""

import asyncio
import logging

import httpx

from app.db import FETCH_DUE_SQL, MARK_FAILED_SQL, MARK_SENT_SQL

BATCH_SIZE = 10
POLL_SECONDS = 5
RESEND_URL = "https://api.resend.com/emails"

log = logging.getLogger("email-service.worker")


def _short_error(exc):
    """Turn an exception into a short error message safe to store and log."""
    return str(exc)[:200]


async def _send_one(client, settings, row):
    """Send a single outbox row through Resend and return the provider message id."""
    payload = {
        "from": row["from_address"],
        "to": [row["to_address"]],
        "subject": row["subject"],
    }
    if row["body_html"]:
        payload["html"] = row["body_html"]
    if row["body_text"]:
        payload["text"] = row["body_text"]
    if row["reply_to"]:
        payload["reply_to"] = row["reply_to"]

    response = await client.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        json=payload,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"resend returned {response.status_code}: {response.text[:150]}")
    data = response.json()
    return data.get("id")


async def _process_batch(conn, client, settings):
    """Send every due outbox row and record the result on each row."""
    rows = await conn.fetch(FETCH_DUE_SQL, BATCH_SIZE)
    for row in rows:
        try:
            message_id = await _send_one(client, settings, row)
        except Exception as exc:
            error = _short_error(exc)
            await conn.execute(MARK_FAILED_SQL, row["id"], error)
            log.info("failed id=%s attempt=%s error=%s", row["id"], row["attempts"] + 1, error)
        else:
            await conn.execute(MARK_SENT_SQL, row["id"], message_id)
            log.info("sent id=%s message_id=%s", row["id"], message_id)


async def run_worker(settings, pool, stop_event):
    """Keep sending due outbox rows until the stop event is set."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        while not stop_event.is_set():
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await _process_batch(conn, client, settings)
            except Exception:
                log.exception("worker iteration crashed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_SECONDS)
            except TimeoutError:
                pass
