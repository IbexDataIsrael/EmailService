-- One-time setup against the existing postgres container:
--   1. Create the database (once, needs a superuser):
--        psql -U <user> -c "CREATE DATABASE emaildb"
--   2. Create the table:
--        psql -U <user> -d emaildb -f schema.sql
-- The app also creates this table at startup, so step 2 is optional.
-- This table is the send history: rows are never deleted.

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
);

-- Speeds up the worker's query for due rows.
CREATE INDEX IF NOT EXISTS outbox_due_idx ON outbox (status, next_attempt_at);
