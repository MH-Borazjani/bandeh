# Session Management & Authorization

## What exists

- **Roles** — `User.role` is `customer` / `staff` / `admin`
  (`db/models/user.py`). The field already existed; this topic wires it
  to real routes.
- **The gate is a dependency, never an if** — `RequireAdminDep`
  (`core/security.py`, built on `require_roles`) raises 401 for guests
  and 403 for customers before the handler runs. `GET /admin/overview`
  (`api/routes/admin.py`) is the first admin-only route; it returns
  total users and live sessions.

## Refresh-token flow

Login (`POST /auth/otp/verify`) now issues two credentials:

| Credential | Where | Lifetime | Job |
|---|---|---|---|
| Signed session cookie `bandeh_session` | browser, all paths | 30 days | identifies the browser |
| Refresh token cookie `bandeh_refresh` | browser, `/auth/*` only | 30 days | mints fresh sessions |

`POST /auth/refresh` takes the refresh cookie and:

1. Unknown token → 401, both cookies cleared.
2. **Revoked token presented again → possible theft**: every refresh
   token of that user is revoked at once (the family is nuked), 401,
   both cookies cleared.
3. Expired token → 401 (a plain old token, no theft signal, family
   untouched).
4. Valid token → **rotation**: the old token is revoked, a new one is
   issued, a fresh DB session is minted, both cookies are set, 200.

Only SHA-256 hashes are stored (`db/models/refresh_token.py`,
`services/refresh_service.py`). The plaintext token exists only in the
browser's HttpOnly cookie and in the one response that issued it — the
database can never leak a usable token.

## Logout

- `POST /auth/logout` — revokes this device's DB session **and** its
  refresh token, clears both cookies.
- `POST /auth/logout-everywhere` — revokes **every** session and
  refresh token of the user, on all devices (uses the existing
  `revoke_all_user_sessions` plus the new `revoke_all_refresh_tokens`).

## One behavior change worth knowing

`get_current_db_user` used to fall back to the phone inside the cookie
even when the cookie carried a session token that was revoked or
expired. That made revocation toothless: a logged-out user was
re-identified by the same cookie. Now the rule is: **a token present
but dead means None, no fallback**. The fallback survives only for
cookies with no token at all (login while the DB was down) — tested in
`check_session_mgmt.py`.

## What is NOT here yet (honest note)

The session itself is still 30 days, so the refresh flow's job today is
silent re-login when the session cookie is gone — not short-lived
access tokens. Shortening the access session (e.g. 15 minutes) is a
one-line change in `session_service.py`, but it only makes sense once
the frontend calls `/auth/refresh` when a session expires. That call
does not exist yet.

## Tests

`python check_session_mgmt.py` — in-memory SQLite, no server, no live
Postgres. Covers the admin gate (401/403/200), rotation, reuse
detection, expired tokens, logout, logout-everywhere, and the no-token
fallback.
