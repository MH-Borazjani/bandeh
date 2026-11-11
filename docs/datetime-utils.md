# Datetime helpers

One rule for the whole app: **store UTC, show local.**

## What lives here (`core/datetime_utils.py`)

- `utcnow()` — timezone-aware current time in UTC. Use this for every
  timestamp written to the database.
- `to_tehran()` / `format_tehran()` / `now_tehran()` — display only.
  Convert at the last moment, never sooner.
- `expires_at(seconds)` — build an expiry timestamp.
- `is_expired(expires_at_value)` — the one expiry check, plus thin
  wrappers `otp_is_expired` (120 s) and `session_is_expired` (30 days).
  Constants `OTP_EXPIRY_SECONDS` and `SESSION_EXPIRY_DAYS` are the single
  source of truth for those numbers.

## The hard rule

Naive datetimes (no timezone) are **rejected loudly** with `ValueError`
instead of silently miscompared. Comparing a naive datetime with an
aware one is a bug, and this module makes it fail at the call site
instead of in production.

## Tests

`python tests/check_datetime.py` — UTC awareness, Tehran conversion
(UTC+03:30), expiry math and boundaries, and naive rejection. Pure
functions, no server, no database.
