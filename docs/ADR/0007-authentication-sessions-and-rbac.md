# ADR-0007: Authentication, browser sessions and workspace RBAC

- Status: Accepted
- Date: 2026-11-07
- Resolves: OD-1 (token format), OD-2 (browser auth transport), OD-3 (workspace on signup)
- Opens: OD-13 (enable PostgreSQL row-level security)

## Context
Step 1.5 adds accounts, login and workspace roles. The owner approved:
- short-lived JWT access tokens;
- rotating, hashed, opaque refresh tokens with reuse detection;
- cookie-only browser authentication with CSRF, Origin and CORS protection;
- a personal workspace created automatically at signup.

The handoff requires Argon2id, authorization enforced in backend services, and workspace isolation that guessing a UUID cannot defeat.

## Decisions

### Accounts and passwords
- **Hashing:** Argon2id via argon2-cffi, with library defaults (RFC 9106 low-memory profile: t=3, m=64 MiB, p=4). Hashing runs in a worker thread. Hashes are upgraded on login when parameters change.
- **Password rules:** 12–128 characters, not blank. Emails are validated and normalized to lower case; uniqueness is case-insensitive at the database level.
- **No account enumeration on login:** an unknown email still performs a dummy Argon2 verification, and wrong-password and unknown-email failures return the same 401 `INVALID_CREDENTIALS`. Registration does reveal an existing email (409), which is unavoidable without email verification. That is mitigated by rate limits.
- **Signup is one transaction:** user, personal workspace ("Personal workspace" or `workspace_name`), OWNER membership and the first session. Any failure rolls all of it back. A unique-index race on email becomes a 409.

### Tokens and sessions
- **Access token:** JWT HS256, 60–900 s (default 900).
  - Claims: `iss`, `aud`, `sub` (user), `sid` (session), `typ=access`, `iat`, `nbf`, `exp`, `jti`. All are required, and `iss`, `aud`, `typ` and expiry are verified, with 10 s clock-skew leeway.
  - The algorithm is pinned server-side; the token's `alg` header is never trusted, and `none` / HS512 are rejected.
- **Key rotation:** `AUTH_JWT_SIGNING_KEYS` is a key ring (`{"kid": "secret"}`, each at least 32 characters) held in the environment, never in Git. Tokens are signed with `AUTH_JWT_ACTIVE_KEY_ID` and carry `kid`.
  - To rotate: add the new key, switch the active ID, then remove the old key after one access-token lifetime.
  - Removing a key immediately invalidates its tokens (emergency rotation).
- **No roles in tokens:** access tokens carry no roles or workspace claims. Every authenticated request reloads the session (`auth_sessions`) and the user, and workspace routes reload the membership. Revocation and role changes therefore take effect on the next request, without waiting for the token to expire.
- **Sessions are refresh-token families:** each login creates one `auth_sessions` row with an absolute expiry (`AUTH_SESSION_MAX_AGE_SECONDS`, default 30 days).
- **Refresh tokens:**
  - 256-bit random values. Only their SHA-256 is stored (`refresh_tokens.token_hash`); a fast hash suffices for high-entropy tokens.
  - Every refresh marks the presented token used and issues a new one, which lasts `AUTH_REFRESH_TOKEN_TTL_SECONDS` but never beyond the session's expiry.
- **Reuse detection:** presenting a token that was already used means it was copied. The whole session is revoked (`REFRESH_TOKEN_REUSE`) and committed before responding 401 `REFRESH_TOKEN_REUSED`. The legitimate holder's newer tokens die with it.
- **Concurrent refreshes** of the same token are serialized with row locks. The second one is treated as reuse, which is strict by design. The frontend must therefore make sure only one refresh request is in flight at a time (Step 1.9).
- **Logout** revokes the session (`LOGOUT`). That kills its refresh tokens, and its access tokens on their next use. It is idempotent and clears all cookies.

### Browser transport (cookies only)
- Tokens are never returned in response bodies and never in `localStorage`.

  | Cookie | Attributes |
  |---|---|
  | access | `HttpOnly`, `SameSite=Lax`, `Path=/` |
  | refresh | `HttpOnly`, `SameSite=Strict`, `Path=/api/v1/auth` (sent only to refresh and logout) |
  | csrf | readable by JavaScript, `SameSite=Lax`, `Path=/` |

- All cookies are **host-only** (no `Domain`). `Secure` is on in production (`AUTH_COOKIE_SECURE`; production rejects `false`), which also enables the `__Host-` / `__Secure-` name prefixes.
- **CSRF:** each session has a random CSRF token. Only its SHA-256 is stored (`auth_sessions.csrf_token_hash`), so it is bound to the session and can't be forged or transplanted from another session. Every unsafe request authenticated by cookie, plus refresh and logout, must send it in `X-CSRF-Token`.
- **Origin check:** every POST/PUT/PATCH/DELETE and every WebSocket handshake must carry an `Origin` (or, as a fallback, `Referer`) in `CORS_ALLOWED_ORIGINS`, otherwise 403 `ORIGIN_NOT_ALLOWED`. This also blocks login CSRF, before any session exists.
- **CORS:** an explicit origin allow-list with credentials. Settings validation rejects wildcards, paths, and `http` origins in production. Allowed request headers are listed explicitly.
- **SSE and WebSockets:**
  - SSE uses `GET` and the access cookie, through the standard dependency.
  - WebSockets use `get_websocket_principal`: the access cookie plus the handshake's Origin check, closing with 1008 on failure.
  - No streaming endpoint exists yet (Phase 4); the dependency is ready and tested.

### Authorization (RBAC)
- **Roles:** VIEWER < MEMBER < ADMIN < OWNER, stored only in `workspace_members`.
- **One check point:** `app/services/authorization.authorize_workspace()`, exposed to routes through `workspace_role(minimum)`. It returns a `WorkspaceAccess` that services require for workspace-scoped work.
- **No information leak:** non-members receive **404 `WORKSPACE_NOT_FOUND`**, the same as for a random UUID, so IDs reveal nothing. Members whose role is too low get 403 `INSUFFICIENT_ROLE`.
- **Granting roles:** owners may grant ADMIN, MEMBER and VIEWER; admins only roles below their own; nobody grants OWNER.
- **Invitations:**
  - `workspace_invitations` stores the invitee email, role, token hash and expiry (`WORKSPACE_INVITATION_TTL_SECONDS`, default 7 days).
  - The raw token is returned once to the inviter, because email delivery is not in the MVP.
  - Acceptance requires the logged-in user's email to match. It is single-use and row-locked. Every invalid case returns the same 404 `INVITATION_NOT_FOUND`.

### Abuse protection
- **Fixed-window counters in Redis**, keyed by hashed identifier:
  - login: per IP **and** per email (`AUTH_LOGIN_ATTEMPTS_PER_MINUTE`);
  - register, refresh, logout and invitation creation: per IP or user (`AUTH_REQUESTS_PER_MINUTE`);
  - invitation acceptance: per user.
- Exceeding a limit returns 429 `RATE_LIMITED` with `Retry-After`.
- **Fails closed:** if Redis is down, these endpoints return 503 `RATE_LIMITER_UNAVAILABLE` rather than run unprotected.
- The client IP is the direct peer. A trusted-proxy configuration must be added before deploying behind a reverse proxy.

### Secrets hygiene
Passwords are `SecretStr` from the request onward, and validation errors never echo input. Tokens appear only in `Set-Cookie`. Log redaction covers keys such as `password` and `*token*` plus configured secrets, now including the JWT keys. Tests assert that passwords and tokens appear in neither logs nor responses.

## Row-level security (designed; enabling is OD-13)
**Why it is not enabled now:** the application connects as `agentflow`, which is a superuser with `BYPASSRLS` and owns every table. PostgreSQL skips RLS for such a role, so policies added today would enforce nothing and give false assurance. Isolation is currently enforced by:
1. centralized membership authorization on every workspace route;
2. repositories that always filter by `workspace_id`;
3. the composite foreign keys from ADR-0006.

**Design to implement under OD-13:**
1. **Two roles.**
   - `agentflow_owner` owns the schema and runs Alembic.
   - `agentflow_app` (`NOSUPERUSER NOBYPASSRLS`, not an owner) has only `SELECT/INSERT/UPDATE/DELETE` on application tables.
   - Separate credentials in `.env`; the compose init script creates both.
2. **Per-transaction context.** After every `BEGIN`, a SQLAlchemy session `after_begin` hook runs `SELECT set_config('app.user_id', :uid, true)`, using the authenticated principal held in a contextvar.
   - `is_local = true` scopes the value to the current transaction, so it resets automatically at COMMIT or ROLLBACK and can never leak to the next user of a pooled connection.
   - With no principal, it sets an empty value, and the policies match nothing.
3. **Policies** on workspace-owned tables (`ENABLE` and `FORCE ROW LEVEL SECURITY`): `USING / WITH CHECK (workspace_id IN (SELECT app_user_workspace_ids()))`.
   - `app_user_workspace_ids()` is a `STABLE SECURITY DEFINER` function that reads `workspace_members` for `current_setting('app.user_id', true)`. That avoids recursive policies on `workspace_members` itself.
4. **Unscoped flows.** `users`, `auth_sessions`, `refresh_tokens` and invitation lookup by token stay outside RLS, guarded by the service layer, because login and refresh run before any user context exists.
5. **Workers** set `app.user_id` (or a dedicated `app.workspace_id` policy path) per task from the task's workspace.
6. **Tests:** cross-tenant reads, writes and updates fail with RLS on even when the application filter is deliberately removed. Pooled connections show no residual context after commit or rollback.

## Alternatives considered
- **Bearer tokens in response bodies or `localStorage`:** rejected by the owner, because XSS could exfiltrate them.
- **Stateless JWT-only sessions:** rejected. Logout and revocation could not take effect before expiry.
- **Roles inside the JWT:** rejected. They would become stale after a membership change.
- **Refresh-reuse grace window:** not adopted. It weakens theft detection; the frontend serializes refreshes instead.
- **Fail-open rate limiting:** rejected. A Redis outage would leave authentication endpoints unprotected.

## Consequences
- Each authenticated request costs one session lookup and, for workspace routes, one membership lookup.
- Non-browser API clients would need a separate authentication scheme (for example API keys). None exists in the MVP.
- Expired sessions, refresh tokens and invitations are deleted by a scheduled cleanup, to be added alongside OD-12 (Step 1.8).
