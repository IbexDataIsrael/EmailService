"""Connection pool and the plain SQL used by the email service."""

import asyncio

import asyncpg

# Creates the outbox table; same DDL as schema.sql. Runs at startup so the
# service works even before schema.sql is applied manually.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS outbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at timestamptz NOT NULL DEFAULT now(),
    project text,
    to_address text NOT NULL,
    subject text NOT NULL,
    body_text text,
    body_html text,
    from_address text NOT NULL,
    reply_to text,
    status text NOT NULL DEFAULT 'queued',
    attempts int NOT NULL DEFAULT 0,
    last_error text,
    provider_message_id text,
    sent_at timestamptz,
    next_attempt_at timestamptz NOT NULL DEFAULT now()
)
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS outbox_due_idx ON outbox (status, next_attempt_at)
"""

# Rows the worker should try next. FOR UPDATE SKIP LOCKED makes sure two
# workers never grab the same row at the same time.
FETCH_DUE_SQL = """
SELECT id, to_address, subject, body_html, body_text, from_address, reply_to, attempts
FROM outbox
WHERE status = 'queued' AND attempts < 5 AND next_attempt_at <= now()
ORDER BY created_at
LIMIT $1
FOR UPDATE SKIP LOCKED
"""

MARK_SENT_SQL = """
UPDATE outbox
SET status = 'sent', provider_message_id = $2, sent_at = now(), last_error = NULL
WHERE id = $1
"""

# attempts + 1 is the new attempt count; backoff is (new attempts)^2 minutes.
# After the 5th failed attempt the row is marked 'failed' and never picked again.
MARK_FAILED_SQL = """
UPDATE outbox
SET attempts = attempts + 1,
    last_error = $2,
    next_attempt_at = now() + ((attempts + 1) * (attempts + 1)) * interval '1 minute',
    status = CASE WHEN attempts + 1 >= 5 THEN 'failed' ELSE 'queued' END
WHERE id = $1
"""

INSERT_SQL = """
INSERT INTO outbox (to_address, subject, body_html, body_text, from_address, reply_to, project)
VALUES ($1, $2, $3, $4, $5, $6, $7)
RETURNING id
"""


async def create_pool(database_url):
    """Create the asyncpg connection pool, retrying while Postgres starts up."""
    last_error = None
    for attempt in range(10):
        try:
            return await asyncpg.create_pool(dsn=database_url, min_size=1, max_size=5)
        except OSError as exc:
            last_error = exc
            await asyncio.sleep(2)
    raise RuntimeError(f"could not connect to Postgres after 10 tries: {last_error}")


async def ensure_schema(pool):
    """Create the outbox table and its index if they do not exist yet."""
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLE_SQL)
        await conn.execute(CREATE_INDEX_SQL)


async def insert_email(pool, to, subject, html, text, from_address, reply_to, project):
    """Save a new email as 'queued' in the outbox and return its id."""
    row = await pool.fetchrow(
        INSERT_SQL, to, subject, html, text, from_address, reply_to, project
    )
    return row["id"]
