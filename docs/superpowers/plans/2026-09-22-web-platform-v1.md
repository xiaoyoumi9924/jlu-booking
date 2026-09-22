# JLU Booking Web Platform V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a lightweight multi-user Web interface that schedules up to 10 isolated uses of the existing booking worker for at most 30 active users without changing booking-core behavior.

**Architecture:** FastAPI and server-rendered templates provide the browser interface, while a separate scheduler process claims SQLite-backed tasks and launches the unchanged `jlu_booking.auto` module with per-task config, runtime, and Token environment values. SQLite WAL, authenticated encryption, opaque server-side sessions, transactional capacity checks, and per-user filesystem paths provide persistence and isolation on the existing 2-core server.

**Tech Stack:** Python 3.10+, FastAPI, Uvicorn, Jinja2, vanilla JavaScript/CSS, SQLite, Argon2, `cryptography.Fernet`, pytest, Caddy, systemd

**Spec:** `docs/superpowers/specs/2026-09-22-web-platform-v1-design.md`

## Global Constraints

- Preserve the existing desktop GUI, CLI, `jlu_booking.auto` state machine, strict candidate order, 07:29:57 CORE start, ordinary CORE zero-wait behavior, and 07:33 hard deadline.
- Use `ZoneInfo("Asia/Shanghai")` for all scheduling and date decisions.
- Reject naive service-layer datetimes and store timestamps as offset-aware ISO 8601 strings.
- Limit active booking users to 30, unexpired pending users to 100, and accepted tasks per execution date to 10.
- Give every task a private config file, runtime directory, success state, event log, timing log, Token, and companion.
- Never put a full Token in a command, config file, log, template, URL, audit event, or ordinary JSON response.
- Store Tokens, companion numbers, and companion names with authenticated encryption; store passwords only as Argon2 hashes.
- Do not contact the real university API from automated tests.
- Keep Web dependencies optional so the current desktop installation remains valid.
- Use public registration, administrator-managed password recovery, and a separate management-only bootstrap administrator.
- Run the production Web process and scheduler as separate systemd services behind Caddy HTTPS.

## Review Focus

- Two simultaneous Token activations for the final user slot must produce exactly one active user; Task 4 adds the concurrent transaction test.
- Two simultaneous task submissions for the final execution-date slot must produce exactly one tenth task; Task 5 adds the concurrent capacity test.
- Boundary times 07:26:59.999999, 07:27:00, 07:29:56.999999, 07:29:57, and 07:33:00 must select the documented scheduling behavior; Tasks 5 and 7 add exact-clock tests.
- A Token, companion number, or cross-user task identifier must never escape through files, commands, logs, routes, or object lookup; Tasks 2, 6, 9, and 10 add leakage and authorization tests.
- Scheduler restart after a task was claimed must never blindly submit again, especially after `submission_unknown`; Task 7 adds reconciliation tests for terminal, missing, and uncertain status files.

---

### Task 1: Optional Web Runtime, Settings, and SQLite Schema

**Files:**
- Modify: `pyproject.toml`
- Create: `jlu_booking/web/__init__.py`
- Create: `jlu_booking/web/settings.py`
- Create: `jlu_booking/web/db.py`
- Create: `tests/web/test_db.py`

**Interfaces:**
- Produces: `WebSettings.from_env(environ: Mapping[str, str], *, strict_permissions: bool = True) -> WebSettings`
- Produces: `connect_database(path: Path) -> sqlite3.Connection`
- Produces: `migrate_database(connection: sqlite3.Connection) -> None`
- Produces: `transaction(connection: sqlite3.Connection, *, immediate: bool = False) -> ContextManager[sqlite3.Connection]`
- Consumes: no Web interfaces from earlier tasks

- [ ] **Step 1: Add failing settings and migration tests**

Create `tests/web/test_db.py` with tests that build keys and a database under
`tmp_path`, then assert:

```python
def test_database_migration_is_idempotent_and_enables_safety_pragmas(tmp_path):
    connection = connect_database(tmp_path / "web.sqlite3")
    migrate_database(connection)
    migrate_database(connection)

    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "schema_migrations",
        "users",
        "user_credentials",
        "companions",
        "booking_tasks",
        "task_runs",
        "web_sessions",
        "request_throttles",
        "audit_events",
    } <= tables


def test_web_settings_rejects_group_readable_secret_files(tmp_path):
    token_key = tmp_path / "token.key"
    blind_key = tmp_path / "blind.key"
    token_key.write_text("token-key", encoding="ascii")
    blind_key.write_text("blind-key", encoding="ascii")
    token_key.chmod(0o644)
    blind_key.chmod(0o600)

    with pytest.raises(ValueError, match="权限"):
        WebSettings.from_env(
            {
                "JLU_BOOKING_WEB_DATA_DIR": str(tmp_path / "data"),
                "JLU_BOOKING_WEB_TOKEN_KEY_FILE": str(token_key),
                "JLU_BOOKING_WEB_BLIND_KEY_FILE": str(blind_key),
            }
        )
```

Also assert the partial unique index for one open task per user exists and
that every foreign key is enabled on a new connection.

- [ ] **Step 2: Run the database tests and verify the missing module failure**

Run: `python3 -m pytest tests/web/test_db.py -v`

Expected: FAIL because `jlu_booking.web.db` and `jlu_booking.web.settings` do
not exist.

- [ ] **Step 3: Add optional dependencies and settings**

Add this optional group and console command to `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = ["pytest>=8,<10"]
package = ["pyinstaller>=6,<7"]
web = [
  "argon2-cffi>=23.1,<26",
  "cryptography>=43,<47",
  "fastapi>=0.115,<1",
  "httpx>=0.27,<1",
  "jinja2>=3.1,<4",
  "python-multipart>=0.0.12,<1",
  "uvicorn>=0.30,<1",
]

[project.scripts]
jlu-booking-auto = "jlu_booking.auto:main"
jlu-booking-token = "jlu_booking.token_cli:main"
jlu-booking-status = "jlu_booking.status_cli:main"
jlu-booking-web = "jlu_booking.web.cli:main"
```

Implement `WebSettings` as a frozen dataclass with `data_dir`, `database_path`,
`runtime_root`, `backup_dir`, `token_key_file`, `blind_key_file`,
`cookie_secure`, `trusted_proxy`, `user_limit=30`, `pending_limit=100`, and
`daily_task_limit=10`. `from_env` resolves paths, rejects missing key files,
and on POSIX rejects any key file whose mode has group or other permission
bits. It validates `token.key` as a Fernet key and decodes `blind.key` as
URL-safe base64 containing exactly 32 bytes. Development tests may pass
`strict_permissions=False`; production CLI uses the default `True`.

- [ ] **Step 4: Implement the first migration and transaction helper**

Create the schema with the exact tables and constraints from the spec. The
first migration must include these critical constraints:

```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'admin')),
    status TEXT NOT NULL CHECK (
        status IN ('pending_token', 'active', 'disabled', 'deleted')
    ),
    must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (
        must_change_password IN (0, 1)
    ),
    created_at TEXT NOT NULL,
    activated_at TEXT,
    pending_expires_at TEXT,
    disabled_at TEXT,
    deleted_at TEXT
);

CREATE TABLE user_credentials (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token_ciphertext BLOB NOT NULL,
    token_blind_index BLOB NOT NULL UNIQUE,
    verified_at TEXT NOT NULL,
    last_status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE companions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    student_number_ciphertext BLOB NOT NULL,
    name_ciphertext BLOB NOT NULL,
    verified_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE booking_tasks (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    execution_date TEXT NOT NULL,
    target_day TEXT NOT NULL CHECK (target_day IN ('today', 'tomorrow')),
    venue TEXT NOT NULL,
    sport TEXT NOT NULL,
    companion_id INTEGER NOT NULL REFERENCES companions(id),
    preferred_court_number INTEGER NOT NULL CHECK (preferred_court_number > 0),
    time_priority_json TEXT NOT NULL,
    real_booking_enabled INTEGER NOT NULL DEFAULT 1 CHECK (
        real_booking_enabled IN (0, 1)
    ),
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    claimed_at TEXT,
    stop_requested_at TEXT,
    cancelled_at TEXT
);

CREATE UNIQUE INDEX one_open_task_per_user
ON booking_tasks(user_id)
WHERE status IN ('scheduled', 'running');

CREATE INDEX tasks_by_execution_date
ON booking_tasks(execution_date, status);

CREATE TABLE task_runs (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL UNIQUE REFERENCES booking_tasks(id) ON DELETE CASCADE,
    process_id INTEGER,
    runtime_path TEXT NOT NULL,
    log_path TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    exit_code INTEGER,
    final_status TEXT,
    detail TEXT
);

CREATE TABLE web_sessions (
    id INTEGER PRIMARY KEY,
    token_hash BLOB NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_secret TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    reauthenticated_at TEXT
);

CREATE TABLE request_throttles (
    bucket_key TEXT PRIMARY KEY,
    window_started_at TEXT NOT NULL,
    counter INTEGER NOT NULL CHECK (counter > 0)
);

CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY,
    admin_id INTEGER NOT NULL REFERENCES users(id),
    action TEXT NOT NULL,
    target_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    source_ip TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

Constrain `booking_tasks.status` to `scheduled`, `running`, `success`,
`no_result`, `token_invalid`, `account_blocked`, `daily_limit`,
`submission_unknown`, `network_unavailable`, `stopped`, `error`, or
`cancelled`; constrain `task_runs.final_status` to the terminal subset when it
is not null.
`connect_database` sets `row_factory=sqlite3.Row`, `PRAGMA foreign_keys=ON`,
`PRAGMA journal_mode=WAL`, and `PRAGMA busy_timeout=5000`. The transaction
context manager uses `BEGIN IMMEDIATE` when requested and always rolls back on
exceptions.

- [ ] **Step 5: Run focused tests**

Run: `python3 -m pytest tests/web/test_db.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the storage foundation**

```bash
git add pyproject.toml jlu_booking/web tests/web/test_db.py
git commit -m "feat(web): add settings and database schema"
```

### Task 2: Passwords, Encryption, Sessions, CSRF, and Throttling

**Files:**
- Create: `jlu_booking/web/security.py`
- Create: `jlu_booking/web/sessions.py`
- Create: `tests/web/test_security.py`

**Interfaces:**
- Consumes: `connect_database`, `transaction`, `WebSettings`
- Produces: `PasswordService.hash(password: str) -> str`
- Produces: `PasswordService.verify(password_hash: str, candidate: str) -> bool`
- Produces: `CredentialCipher.encrypt(value: str) -> bytes`
- Produces: `CredentialCipher.decrypt(ciphertext: bytes) -> str`
- Produces: `CredentialCipher.token_index(token: str) -> bytes`
- Produces: `SessionService.create(user_id: int, now: datetime) -> SessionGrant`
- Produces: `SessionService.resolve(raw_token: str, now: datetime) -> SessionRecord | None`
- Produces: `SessionService.require_csrf(session: SessionRecord, supplied: str) -> None`
- Produces: `ThrottleService.require_available(key: str, *, limit: int, window: timedelta, now: datetime) -> None`
- Produces: `ThrottleService.consume(key: str, *, limit: int, window: timedelta, now: datetime) -> None`

- [ ] **Step 1: Write failing cryptography, session, and throttle tests**

Create tests using generated Fernet and blind-index keys:

```python
def test_cipher_round_trip_and_blind_index_do_not_expose_token():
    cipher = CredentialCipher(Fernet.generate_key(), b"b" * 32)
    encrypted = cipher.encrypt("private-token")

    assert b"private-token" not in encrypted
    assert cipher.decrypt(encrypted) == "private-token"
    assert cipher.token_index("private-token") == cipher.token_index(
        "private-token"
    )
    assert cipher.token_index("private-token") != cipher.token_index(
        "other-token"
    )


def test_session_obeys_idle_and_absolute_expiry(database, beijing_now):
    service = SessionService(database)
    grant = service.create(user_id=1, now=beijing_now)

    assert service.resolve(grant.raw_token, beijing_now + timedelta(hours=11))
    assert service.resolve(grant.raw_token, beijing_now + timedelta(days=7)) is None


def test_throttle_blocks_eleventh_login_failure(database, beijing_now):
    throttles = ThrottleService(database)
    for _ in range(10):
        throttles.consume(
            "login:alice:127.0.0.1",
            limit=10,
            window=timedelta(minutes=15),
            now=beijing_now,
        )

    with pytest.raises(RateLimitExceeded):
        throttles.consume(
            "login:alice:127.0.0.1",
            limit=10,
            window=timedelta(minutes=15),
            now=beijing_now,
        )
```

Add tests for Argon2 verification, password minimum length, encrypted
companion data, CSRF equality using constant-time comparison, idle expiry,
session revocation, and the five-per-15-minute Token/reauth limits.

- [ ] **Step 2: Run security tests and confirm failure**

Run: `python3 -m pytest tests/web/test_security.py -v`

Expected: FAIL because the security services do not exist.

- [ ] **Step 3: Implement security primitives**

Use `argon2.PasswordHasher` with its maintained defaults. Normalize Tokens
through the existing `normalize_token`. Implement Token indexing exactly as:

```python
def token_index(self, token: str) -> bytes:
    normalized = normalize_token(token).encode("utf-8")
    return hmac.digest(self._blind_key, normalized, "sha256")
```

`CredentialCipher.encrypt` rejects empty strings and returns Fernet bytes.
`mask_secret` shows at most the first four and final four characters and never
returns an input unchanged. Password validation rejects fewer than 10
characters before calling Argon2.

- [ ] **Step 4: Implement opaque server-side sessions and throttles**

Generate 32-byte URL-safe session values and store only SHA-256 hashes. Store
a separate 32-byte CSRF value in the session row. `resolve` checks the 12-hour
idle and seven-day absolute limits, updates `last_seen_at`, and deletes expired
rows. `invalidate_user_sessions(user_id, except_session_id=None)` supports
password change and reset.

`ThrottleService.consume` uses an immediate transaction. It creates a counter
for a new window, resets an expired window, increments an open window, and
raises `RateLimitExceeded(retry_after_seconds)` before incrementing past the
limit. `require_available` performs the same window-expiry check without
incrementing, so login, Token validation, and reauthentication can count only
failed attempts. `clear(key)` removes a successful-attempt counter.

- [ ] **Step 5: Run security and database tests**

Run: `python3 -m pytest tests/web/test_security.py tests/web/test_db.py -v`

Expected: PASS.

- [ ] **Step 6: Commit the security foundation**

```bash
git add jlu_booking/web/security.py jlu_booking/web/sessions.py tests/web/test_security.py
git commit -m "feat(web): secure credentials and sessions"
```

### Task 3: Account Lifecycle and Administrator Bootstrap CLI

**Files:**
- Create: `jlu_booking/web/accounts.py`
- Create: `jlu_booking/web/cli.py`
- Create: `tests/web/test_accounts.py`
- Create: `tests/web/test_web_cli.py`

**Interfaces:**
- Consumes: `PasswordService`, `SessionService`, `ThrottleService`, database transaction helpers
- Produces: `AccountService.register_pending(username: str, password: str, *, source_ip: str, now: datetime) -> UserRecord`
- Produces: `AccountService.authenticate(username: str, password: str, *, source_ip: str, now: datetime) -> UserRecord`
- Produces: `AccountService.create_admin(username: str, password: str, *, now: datetime) -> UserRecord`
- Produces: `AccountService.reset_password(user_id: int, temporary_password: str, *, now: datetime) -> None`
- Produces: `AccountService.change_password(user_id: int, current_password: str, new_password: str, *, now: datetime) -> None`
- Produces: `AccountService.cleanup_expired_pending(now: datetime) -> int`
- Produces: `jlu-booking-web generate-keys` and `jlu-booking-web create-admin USERNAME`

- [ ] **Step 1: Write failing account lifecycle tests**

Add tests for username normalization, allowed characters, pending expiry,
the 100-pending cap, disabled/deleted login refusal, generic authentication
failure, password reset, forced change, and session invalidation:

```python
def test_register_pending_normalizes_username_and_expires_in_24_hours(
    accounts, beijing_now
):
    user = accounts.register_pending(
        "Alice_01",
        "correct horse battery staple",
        source_ip="127.0.0.1",
        now=beijing_now,
    )

    assert user.username == "alice_01"
    assert user.status == "pending_token"
    assert user.pending_expires_at == beijing_now + timedelta(hours=24)


def test_password_reset_forces_change_and_revokes_sessions(
    accounts, sessions, active_user, beijing_now
):
    grant = sessions.create(active_user.id, beijing_now)
    accounts.reset_password(
        active_user.id,
        "temporary password 42",
        now=beijing_now,
    )

    assert sessions.resolve(grant.raw_token, beijing_now) is None
    user = accounts.authenticate(
        active_user.username,
        "temporary password 42",
        source_ip="127.0.0.1",
        now=beijing_now,
    )
    assert user.must_change_password is True
```

- [ ] **Step 2: Run account tests and verify failure**

Run: `python3 -m pytest tests/web/test_accounts.py tests/web/test_web_cli.py -v`

Expected: FAIL because `AccountService` and the CLI do not exist.

- [ ] **Step 3: Implement account transactions**

Use the username expression `^[A-Za-z0-9_-]{3,32}$` before lowercase
normalization. Registration consumes `register:{source_ip}` with limit 5 per
hour, removes expired pending rows, checks the pending count, hashes the
password, and inserts `pending_token` in one immediate transaction.

Authentication checks whether the failure bucket is available, always performs
one password verification path, returns the same public error for an unknown
user or wrong password, consumes the bucket only on failure, refuses disabled
and deleted accounts, and clears the matching failure throttle on success.
Re-enabling a disabled ordinary user must atomically recheck the 30-active-user
limit.

- [ ] **Step 4: Implement key generation and administrator creation commands**

`generate-keys --directory PATH` writes `token.key` and `blind.key` with mode
`0600`, refuses to overwrite either file, and prints only their paths. Generate
`token.key` with `Fernet.generate_key()` and `blind.key` with URL-safe base64 of
32 bytes from `secrets.token_bytes(32)`.
`create-admin USERNAME` uses `getpass.getpass` twice, rejects mismatch, opens
and migrates the configured database, and calls `create_admin`. It never
accepts the password as a command-line argument.

Use an argparse dispatcher that imports future `serve`, `scheduler`, and
`backup` handlers only inside their selected branches so `create-admin` works
before those modules are implemented.

- [ ] **Step 5: Run focused account and CLI tests**

Run: `python3 -m pytest tests/web/test_accounts.py tests/web/test_web_cli.py -v`

Expected: PASS, including an assertion that captured CLI output contains no
password or generated key bytes.

- [ ] **Step 6: Commit account management**

```bash
git add jlu_booking/web/accounts.py jlu_booking/web/cli.py tests/web/test_accounts.py tests/web/test_web_cli.py
git commit -m "feat(web): add account lifecycle and admin bootstrap"
```

### Task 4: Per-User Token Activation and Companion Validation

**Files:**
- Create: `jlu_booking/web/credentials.py`
- Create: `tests/web/test_credentials.py`

**Interfaces:**
- Consumes: `CredentialCipher`, `ThrottleService`, `AccountService`, `validate_token_online`, `get_companion_user`
- Produces: `CredentialService.activate_user(user_id: int, token: str, *, now: datetime) -> CredentialSummary`
- Produces: `CredentialService.replace_token(user_id: int, token: str, *, now: datetime) -> CredentialSummary`
- Produces: `CredentialService.save_companion(user_id: int, student_number: str, *, now: datetime) -> CompanionSummary`
- Produces: `CredentialService.decrypt_token(user_id: int) -> str`
- Produces: `CredentialService.decrypt_companion(user_id: int) -> DecryptedCompanion`
- Produces: `CredentialService.reveal_token(admin_id: int, target_user_id: int, *, source_ip: str, now: datetime) -> str`

- [ ] **Step 1: Write failing credential tests with fake gateways**

Inject these callables rather than patching network internals:

```python
def valid_token(_token):
    return TokenValidationResult(status="valid", reason="accepted")


def valid_companion(student_number, token):
    assert student_number == "20260001"
    assert token == "private-token"
    return {"id": 42, "name": "测试同学"}
```

Test successful activation, invalid/unavailable/account-blocked validation,
duplicate Token rejection, failed replacement retaining old ciphertext,
encrypted companion storage, masked summaries, and no credential text in
raised errors. The sixth failed Token validation for one account inside 15
minutes must raise `RateLimitExceeded` before calling the injected validator.

Add a concurrency test with two SQLite connections and two pending users when
29 active users already exist. Run both activation transactions against two
valid unique Tokens and assert exactly one user becomes active and the other
receives `ActiveUserLimitReached`.

- [ ] **Step 2: Run credential tests and verify failure**

Run: `python3 -m pytest tests/web/test_credentials.py -v`

Expected: FAIL because `CredentialService` does not exist.

- [ ] **Step 3: Implement the online validation adapter**

The default Token callable invokes `validate_token_online` using the current
Beijing date and returns only its status and reason. The default companion
callable invokes `get_companion_user(student_number, token)` and requires both
an internal `id` and non-empty `name`. Tests always inject fakes and therefore
make no university requests.

- [ ] **Step 4: Implement atomic activation and safe replacement**

Require availability from the account's five-failures-per-15-minute Token
validation bucket before the online call. Validate outside the database
transaction. Consume the bucket on invalid, unavailable, or account-blocked
results. After a valid result, clear the failure throttle, encrypt the
normalized Token, and compute its blind index. In one `BEGIN IMMEDIATE`
transaction, re-read the pending user, remove expired pending users, count
active ordinary users, enforce `< 30`, insert the unique credential, and set
the user to active. On any failure, do not retain the submitted ciphertext.

Replacement validates first, then atomically updates ciphertext, blind index,
verification time, and status. An invalid or unavailable replacement leaves
the old row byte-for-byte unchanged.

- [ ] **Step 5: Implement companion encryption and reveal auditing**

Decrypt the user's Token only for the companion validation call. Encrypt the
student number and returned name before upsert. `reveal_token` requires an
unexpired administrator reauthentication grant created by Task 10; until that
service exists, accept an injected `reauth_checker(admin_id, now) -> bool` and
raise `ReauthenticationRequired` when false. Insert an audit event before
returning the decrypted value. Never include it in the audit metadata.

- [ ] **Step 6: Run credential, security, and account tests**

Run: `python3 -m pytest tests/web/test_credentials.py tests/web/test_security.py tests/web/test_accounts.py -v`

Expected: PASS.

- [ ] **Step 7: Commit credential isolation**

```bash
git add jlu_booking/web/credentials.py tests/web/test_credentials.py
git commit -m "feat(web): isolate user credentials and companions"
```

### Task 5: One-Shot Task Scheduling and Transactional Capacity

**Files:**
- Create: `jlu_booking/web/tasks.py`
- Create: `tests/web/test_tasks.py`

**Interfaces:**
- Consumes: `validate_auto_config`, database transaction helpers, active users and companions
- Produces: `TaskDraft`
- Produces: `TaskService.next_execution_date(now: datetime) -> date`
- Produces: `TaskService.target_date(execution_date: date, target_day: str) -> date`
- Produces: `TaskService.create(user_id: int, draft: TaskDraft, *, now: datetime) -> BookingTask`
- Produces: `TaskService.update(user_id: int, task_id: int, draft: TaskDraft, *, now: datetime) -> BookingTask`
- Produces: `TaskService.cancel(user_id: int, task_id: int, *, now: datetime) -> BookingTask`
- Produces: `TaskService.request_stop(user_id: int, task_id: int, *, now: datetime) -> BookingTask`
- Produces: `TaskService.get_for_user(user_id: int, task_id: int) -> BookingTask`

- [ ] **Step 1: Write failing exact-time and validation tests**

Use aware Beijing datetimes and pin the boundary behavior:

```python
@pytest.mark.parametrize(
    ("clock", "expected"),
    [
        ("2026-09-22T07:26:59.999999+08:00", date(2026, 9, 22)),
        ("2026-09-22T07:27:00+08:00", date(2026, 9, 23)),
    ],
)
def test_next_execution_date_uses_beijing_0727_boundary(
    task_service, clock, expected
):
    assert task_service.next_execution_date(datetime.fromisoformat(clock)) == expected


def test_tomorrow_target_is_fixed_relative_to_execution_date(task_service):
    assert task_service.target_date(date(2026, 9, 23), "tomorrow") == date(
        2026, 9, 24
    )
```

Add tests that reject an inactive user, missing companion, invalid venue/sport,
invalid preferred court, invalid priority, a second open task for one user,
an absent or non-valid credential status, editing at 07:27:00, cancelling at
07:27:00, requesting a stop for a running task, and accessing another user's
task.

- [ ] **Step 2: Add a concurrent tenth-slot test**

Create nine scheduled tasks for one execution date, then use two connections
and two users to submit the final slot concurrently. Assert one call returns a
task, one raises `ExecutionDateFull`, and the final database count is 10.

- [ ] **Step 3: Run task tests and verify failure**

Run: `python3 -m pytest tests/web/test_tasks.py -v`

Expected: FAIL because `TaskService` does not exist.

- [ ] **Step 4: Implement immutable dates and validated drafts**

Define `TaskDraft` with `target_day`, `venue`, `sport`, `companion_id`,
`preferred_court_number`, `time_priority`, and `real_booking_enabled`. Store
target-day values as `today` or `tomorrow`; translate them to the existing
config values `今天` and `明天` only when building a worker snapshot. Keep the
existing safe default `real_booking_enabled=False` until the user explicitly
selects and confirms real booking.

Before storing, call `validate_auto_config` with the draft's
`real_booking_enabled` value and the decrypted companion number represented by
a non-empty sentinel. Store the normalized priority as compact JSON. Compute
the next execution date from Beijing time exactly as documented.

- [ ] **Step 5: Implement transactional capacity and ownership checks**

Task creation uses `BEGIN IMMEDIATE`, rechecks the user and companion ownership,
requires that the user's credential `last_status` is `valid`, counts rows for
the execution date whose status is not `cancelled`, enforces the limit of 10,
and inserts `scheduled`. Convert the partial-index collision into
`OpenTaskExists`.

Update and cancellation select by both `task_id` and `user_id`, require
`scheduled`, and compare the injected Beijing clock with 07:27:00 on the fixed
execution date. A missing or foreign ID raises the same `TaskNotFound`.
`request_stop` requires `running` and sets `stop_requested_at` exactly once;
it never sends a process signal from the Web process.

- [ ] **Step 6: Run task tests**

Run: `python3 -m pytest tests/web/test_tasks.py tests/web/test_db.py -v`

Expected: PASS, including both concurrency tests.

- [ ] **Step 7: Commit scheduling domain logic**

```bash
git add jlu_booking/web/tasks.py tests/web/test_tasks.py
git commit -m "feat(web): schedule capacity-limited booking tasks"
```

### Task 6: Isolated Existing-Worker Adapter

**Files:**
- Create: `jlu_booking/web/worker.py`
- Create: `tests/web/test_worker.py`
- Modify: `jlu_booking/app_runner.py`
- Modify: `tests/test_app_runner.py`

**Interfaces:**
- Consumes: `build_auto_worker_command`, `CredentialService`, `TaskService`, `WebSettings`, `load_run_status`
- Produces: `WorkerLaunch(command: tuple[str, ...], environment: Mapping[str, str], config_path: Path, runtime_dir: Path, log_path: Path)`
- Produces: `WorkerAdapter.prepare(task: BookingTask) -> WorkerLaunch`
- Produces: `WorkerAdapter.start(launch: WorkerLaunch) -> RunningWorker`
- Produces: `RunningWorker.poll() -> int | None`
- Produces: `RunningWorker.terminate(grace_seconds: float = 5.0) -> int`
- Produces: `WorkerAdapter.collect_result(task: BookingTask, launch: WorkerLaunch, exit_code: int | None) -> WorkerResult`
- Produces: `WorkerAdapter.cleanup_snapshot(launch: WorkerLaunch) -> None`

- [ ] **Step 1: Write failing isolation and secret-leakage tests**

Build a task with unique secrets and assert:

```python
def test_worker_launch_isolates_paths_and_keeps_token_out_of_files(
    worker_adapter, task, tmp_path
):
    launch = worker_adapter.prepare(task)
    config_text = launch.config_path.read_text(encoding="utf-8")

    assert launch.environment["JLU_BOOKING_TOKEN"] == "private-token"
    assert launch.environment["JLU_BOOKING_CONFIG_FILE"] == str(
        launch.config_path
    )
    assert launch.environment["JLU_BOOKING_RUNTIME_DIR"] == str(
        launch.runtime_dir
    )
    assert "private-token" not in config_text
    assert "private-token" not in " ".join(launch.command)
    assert oct(launch.config_path.stat().st_mode & 0o777) == "0o600"


def test_log_pump_redacts_token_and_companion(worker_adapter, task):
    line = "token=private-token companion=20260001"
    assert worker_adapter.sanitize_line(task, line) == (
        "token=[REDACTED] companion=[REDACTED]"
    )
```

Add tests that two users receive disjoint paths, the snapshot maps `today` and
`tomorrow` correctly, the existing seven time priorities remain ordered, the
snapshot is deleted after collection, real mode omits `--dry-run`, scan mode
includes `--dry-run`, and status values map without converting
`submission_unknown` into a retryable state.

- [ ] **Step 2: Run worker tests and verify failure**

Run: `python3 -m pytest tests/web/test_worker.py tests/test_app_runner.py -v`

Expected: FAIL because the adapter does not exist and the existing command
builder cannot accept the Web worker's explicit Python executable cleanly.

- [ ] **Step 3: Extend the command builder without changing current callers**

Add an optional `module: str = "jlu_booking.auto"` keyword to
`build_auto_worker_command`; keep every existing result unchanged. Add a test
that source mode returns `[python, "-m", module]` and that `--dry-run` behavior
is unchanged.

- [ ] **Step 4: Implement private snapshots and direct subprocess startup**

Create `runtime_root / f"user-{user_id}" / f"task-{task_id}"`, reject symlinked
components, and set directories to `0700`. Build config through
`validate_auto_config` and `save_auto_config`, then set the file to `0600`.
Build `Popen` arguments as a list with `shell=False`, `stdin=DEVNULL`, and a
copied environment. The Token appears only in the child environment mapping.

Start a log-pump thread that reads text lines from the child pipe, replaces the
exact Token and companion number with `[REDACTED]`, and writes UTF-8 text. Rotate
the sanitized log before it exceeds 10 MiB, retaining `.1`, `.2`, and `.3`.
The pump must continuously drain output so the booking subprocess cannot block.

- [ ] **Step 5: Implement privacy-safe result collection**

Read `runtime/state/last_run.json` with `load_run_status`. Accept only the
existing allowed statuses and map missing/corrupt state plus exit code into
`error`. Preserve `success`, `no_result`, `token_invalid`, `account_blocked`,
`daily_limit`, `submission_unknown`, `network_unavailable`, and `stopped`
exactly. Delete the config snapshot in a `finally` block after the child and
log pump finish; retain sanitized logs and state files.

- [ ] **Step 6: Run worker and existing runner tests**

Run: `python3 -m pytest tests/web/test_worker.py tests/test_app_runner.py tests/test_auto.py -v`

Expected: PASS with no changes to booking-core assertions.

- [ ] **Step 7: Commit the worker adapter**

```bash
git add jlu_booking/web/worker.py tests/web/test_worker.py jlu_booking/app_runner.py tests/test_app_runner.py
git commit -m "feat(web): launch isolated existing booking workers"
```

### Task 7: Scheduler Service, Recovery, Maintenance, and Backup

**Files:**
- Create: `jlu_booking/web/scheduler.py`
- Create: `jlu_booking/web/maintenance.py`
- Create: `tests/web/test_scheduler.py`
- Create: `tests/web/test_maintenance.py`
- Modify: `jlu_booking/web/cli.py`
- Modify: `tests/web/test_web_cli.py`

**Interfaces:**
- Consumes: `TaskService`, `WorkerAdapter`, database transactions, `WebSettings`
- Produces: `Scheduler.run_once(now: datetime) -> SchedulerTick`
- Produces: `Scheduler.run_forever(*, stop_event: threading.Event | None = None) -> None`
- Produces: `Scheduler.reconcile(now: datetime) -> ReconciliationResult`
- Produces: `MaintenanceService.run(now: datetime) -> MaintenanceResult`
- Produces: `BackupService.create(now: datetime) -> Path`
- Produces: CLI commands `scheduler` and `backup`

- [ ] **Step 1: Write failing scheduler boundary tests**

Use a fake worker that records task IDs without starting Python:

```python
@pytest.mark.parametrize(
    ("clock", "expected_starts"),
    [
        ("2026-09-22T07:26:59.999999+08:00", 0),
        ("2026-09-22T07:27:00+08:00", 1),
        ("2026-09-22T07:29:56.999999+08:00", 1),
        ("2026-09-22T07:29:57+08:00", 0),
        ("2026-09-22T07:33:00+08:00", 0),
    ],
)
def test_scheduler_only_claims_in_start_window(
    scheduler_factory, clock, expected_starts
):
    scheduler, fake_worker = scheduler_factory(clock)
    scheduler.run_once(datetime.fromisoformat(clock))
    assert len(fake_worker.started_task_ids) == expected_starts
```

Also assert one tick claims all due tasks without calls to `time.sleep`, starts
no more than accepted database tasks, writes one `task_runs` row per claim,
and terminates an owned worker when `stop_requested_at` appears.

- [ ] **Step 2: Add restart and terminal-state reconciliation tests**

Cover these exact cases:

- previously running + status `success` -> task becomes `success`, no restart;
- previously running + status `submission_unknown` -> remains
  `submission_unknown`, no restart;
- previously running + missing/corrupt status -> task becomes `error`, no restart;
- scheduled + service recovery at 07:28 -> starts once;
- scheduled + service recovery at 07:29:57 -> becomes `error` with missed-window
  detail and never starts.

- [ ] **Step 3: Run scheduler tests and verify failure**

Run: `python3 -m pytest tests/web/test_scheduler.py tests/web/test_maintenance.py -v`

Expected: FAIL because scheduler and maintenance services do not exist.

- [ ] **Step 4: Implement atomic claiming and process observation**

In `run_once`, reconcile finished in-memory workers first. During the startup
window `[07:27:00, 07:29:57)`, use one immediate transaction to select all
`scheduled` rows for today's Beijing execution date, update each to `running`,
insert a `task_runs` row, then commit before starting subprocesses. If startup
fails, mark only that task `error` and continue starting other claimed tasks.

`run_forever` calls `run_once(now_beijing())` and waits up to one second on the
stop event. This scheduler wait never reaches a booking subprocess and does
not alter CORE timing. On termination, gracefully stop owned children.
Each tick also reads `stop_requested_at` for its owned running tasks, invokes
the worker's five-second graceful termination, and stores `stopped`.

- [ ] **Step 5: Implement restart reconciliation and maintenance**

At service startup, query database tasks marked `running`, inspect each private
status file, and apply the cases tested above without relaunching. Mark
unstarted scheduled tasks as missed only once 07:29:57 has been reached.
When worker collection reports `token_invalid` or `account_blocked`, update the
owner's credential `last_status` to that value in the same finalization
transaction so new tasks are rejected until a valid replacement Token is
saved.

Maintenance deletes expired sessions, throttle counters, and pending accounts;
removes runtime logs older than 30 days only below the configured runtime root;
and never follows symlinks. Run maintenance once at startup and at 03:15
Beijing time each day.

- [ ] **Step 6: Implement SQLite backup and scheduler CLI commands**

Use `sqlite3.Connection.backup` into a temporary file, `fsync`, atomic rename
to `backup_dir / YYYY-MM-DD.sqlite3`, mode `0600`, and delete backups older
than the latest 14 daily files. Extend CLI commands:

```text
jlu-booking-web scheduler
jlu-booking-web backup
```

The scheduler command installs SIGTERM/SIGINT handlers that set its stop event.
The backup command prints only the backup path.

- [ ] **Step 7: Run scheduler, maintenance, worker, and CLI tests**

Run: `python3 -m pytest tests/web/test_scheduler.py tests/web/test_maintenance.py tests/web/test_worker.py tests/web/test_web_cli.py -v`

Expected: PASS.

- [ ] **Step 8: Commit the scheduler service**

```bash
git add jlu_booking/web/scheduler.py jlu_booking/web/maintenance.py jlu_booking/web/cli.py tests/web/test_scheduler.py tests/web/test_maintenance.py tests/web/test_web_cli.py
git commit -m "feat(web): run and recover scheduled booking workers"
```

### Task 8: FastAPI Application Shell, Registration, Login, and Onboarding

**Files:**
- Create: `jlu_booking/web/app.py`
- Create: `jlu_booking/web/dependencies.py`
- Create: `jlu_booking/web/routes/__init__.py`
- Create: `jlu_booking/web/routes/auth.py`
- Create: `jlu_booking/web/routes/profile.py`
- Create: `jlu_booking/web/templates/base.html`
- Create: `jlu_booking/web/templates/login.html`
- Create: `jlu_booking/web/templates/register.html`
- Create: `jlu_booking/web/templates/change_password.html`
- Create: `jlu_booking/web/templates/token_onboarding.html`
- Create: `jlu_booking/web/templates/profile.html`
- Create: `jlu_booking/web/static/app.css`
- Create: `jlu_booking/web/static/app.js`
- Create: `jlu_booking/web/static/JLU_LOGO.png` as a byte-for-byte copy of `assets/JLU_LOGO.png`
- Create: `tests/web/test_auth_routes.py`
- Modify: `pyproject.toml`
- Modify: `jlu_booking/web/cli.py`

**Interfaces:**
- Consumes: account, credential, session, throttle, and database services
- Produces: `AppServices`
- Produces: `create_app(settings: WebSettings, services: AppServices | None = None) -> FastAPI`
- Produces: HTML routes `/login`, `/register`, `/logout`, `/change-password`, `/onboarding/token`, and `/profile`
- Produces: CLI command `serve`

- [ ] **Step 1: Write failing route tests with injected fake validators**

Use `TestClient` with `cookie_secure=False` and a fake Token validator. Test
registration, login, generic failure text, CSRF rejection, pending-user
redirects, Token activation, logout, forced password change, and companion
validation. A representative isolation test is:

```python
def test_pending_user_cannot_open_profile_before_token_activation(client):
    register_and_login_pending(client, "alice")
    response = client.get("/profile", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/onboarding/token"


def test_state_change_requires_csrf(client, active_login):
    response = client.post(
        "/profile/companion",
        data={"student_number": "20260001"},
    )
    assert response.status_code == 403
```

Assert password, Token, companion number, raw session cookie, and CSRF secret
are absent from response bodies and captured application logs. Assert
authenticated pages use `Cache-Control: no-store`, session cookies use
`HttpOnly`, `SameSite=Lax`, and the configured `Secure` flag, and every response
sets Content Security Policy, `X-Frame-Options: DENY`, and
`X-Content-Type-Options: nosniff`.

- [ ] **Step 2: Run auth route tests and verify failure**

Run: `python3 -m pytest tests/web/test_auth_routes.py -v`

Expected: FAIL because the FastAPI app and routes do not exist.

- [ ] **Step 3: Implement application construction and request dependencies**

`create_app` migrates the database, constructs default services only when they
are not injected, mounts package static files, and installs security headers.
It must not start the scheduler. Request dependencies resolve the opaque
session cookie, load the user, redirect pending users to onboarding, redirect
forced-change users to the password page, and enforce admin role separately.

Use a 303 redirect after every successful form post. Validation failures render
the same form with safe Chinese messages and no secret value echoed back.
Read client IP from the direct socket unless the direct peer equals the single
configured trusted proxy.

- [ ] **Step 4: Implement authentication and profile routes**

Registration calls `register_pending`; login calls `authenticate` and creates
a session; logout revokes the current session. Change-password verifies the
current password, stores the new hash, clears `must_change_password`, revokes
other sessions, and rotates the current session.

Token onboarding calls `activate_user`. Profile Token replacement and
companion save call `CredentialService` with the injected validators. Never
place submitted secrets in redirect URLs, flash cookies, or template context.

- [ ] **Step 5: Add the base responsive visual system**

Use the existing GUI colors (`#144399`, `#0B2F70`, `#EAF1FF`, `#F2F5FA`,
`#172033`) and JLU logo asset. CSS provides a centered auth card, desktop
two-column shell above 900 px, one-column layout below 900 px, minimum 44 px
touch controls, visible keyboard focus, accessible error text, and a separate
scrollable log region. JavaScript contains only page-visibility and safe text
update helpers; no HTML from logs is inserted with `innerHTML`.

- [ ] **Step 6: Package templates and add `serve`**

Add exact template/static globs under `[tool.setuptools.package-data]`. The
`serve` command imports Uvicorn lazily and binds `127.0.0.1:8000` by default,
uses exactly one worker, and allows host and port overrides only through
explicit CLI flags. It always loads settings with strict secret-file
permissions.

- [ ] **Step 7: Run auth routes and package-data tests**

Run: `python3 -m pytest tests/web/test_auth_routes.py tests/test_build_package.py -v`

Expected: PASS and the package manifest includes every HTML, CSS, JavaScript,
and image required by the Web pages.

- [ ] **Step 8: Commit the authenticated Web shell**

```bash
git add pyproject.toml jlu_booking/web/app.py jlu_booking/web/dependencies.py jlu_booking/web/routes jlu_booking/web/templates jlu_booking/web/static jlu_booking/web/cli.py tests/web/test_auth_routes.py tests/test_build_package.py
git commit -m "feat(web): add registration and authenticated browser shell"
```

### Task 9: User Dashboard, Booking Form, Status, and Sanitized Logs

**Files:**
- Create: `jlu_booking/web/routes/dashboard.py`
- Create: `jlu_booking/web/routes/task_routes.py`
- Create: `jlu_booking/web/templates/dashboard.html`
- Create: `jlu_booking/web/templates/task_form.html`
- Create: `jlu_booking/web/templates/task_detail.html`
- Modify: `jlu_booking/web/app.py`
- Modify: `jlu_booking/web/static/app.css`
- Modify: `jlu_booking/web/static/app.js`
- Create: `tests/web/test_task_routes.py`

**Interfaces:**
- Consumes: `TaskService`, authenticated user dependency, `VENUES`, user-private sanitized logs
- Produces: HTML routes `/`, `/tasks/new`, `/tasks/{task_id}`, `/tasks/{task_id}/edit`, `/tasks/{task_id}/cancel`, and `/tasks/{task_id}/stop`
- Produces: JSON route `/tasks/{task_id}/status` containing status, phase, update time, and at most 500 sanitized log lines

- [ ] **Step 1: Write failing task-route ownership and capacity tests**

Test complete create/edit/cancel flows with CSRF, exact displayed execution and
target dates, the 10-task-full message, locked forms after 07:27, and stop
requests. Assert the twenty-first task mutation inside one minute returns HTTP
429 without calling `TaskService`. Hold an exclusive SQLite lock past the
five-second busy timeout and assert the form returns a retryable HTTP 503 page
without creating a task. Add object-level authorization assertions:

```python
@pytest.mark.parametrize(
    "path",
    [
        "/tasks/{task_id}",
        "/tasks/{task_id}/edit",
        "/tasks/{task_id}/status",
    ],
)
def test_user_cannot_read_another_users_task(client, alice_login, bob_task, path):
    response = client.get(path.format(task_id=bob_task.id))
    assert response.status_code == 404


def test_status_response_never_contains_private_values(
    client, alice_login, alice_task
):
    response = client.get(f"/tasks/{alice_task.id}/status")
    text = response.text
    assert "private-token" not in text
    assert "20260001" not in text
    assert len(response.json()["log_lines"]) <= 500
```

- [ ] **Step 2: Run task route tests and verify failure**

Run: `python3 -m pytest tests/web/test_task_routes.py -v`

Expected: FAIL because task routes and templates do not exist.

- [ ] **Step 3: Implement dashboard and booking forms**

The dashboard shows account state, masked Token and companion, next exact
execution time, active task, latest result, and remaining date capacity. The
task form derives venue/sport choices from `api.VENUES`, preserves the existing
default priority, supports moving priorities up and down, presents the current
`仅扫描` and `真实预约` choices with scan selected by default, and shows the
exact execution and target dates before submission.

Every mutation calls `TaskService`; route code does not perform a separate
capacity precheck. This keeps concurrent requests subject to the transaction.
Map domain errors to clear Chinese messages while keeping the submitted Token
and companion out of form contexts.

Before calling the service, each task create, edit, cancel, or stop route
consumes `task-mutation:{user_id}` with limit 20 and a one-minute window.

- [ ] **Step 4: Implement user-scoped status and log-tail endpoints**

Look up every task with `(user_id, task_id)`. Read only the stored private path
for that task, resolve it under `runtime_root`, reject symlinks or path escape,
and return the final 500 UTF-8 lines with invalid bytes replaced. Return logs
as JSON strings and render them with `textContent`.

The task page polls every three seconds while visible. The dashboard polls
every 15 seconds. `visibilitychange` stops and resumes timers. Terminal states
stop task-detail polling after the final update.

The running-task stop form calls `TaskService.request_stop`; the independent
scheduler observes the database flag on its next tick. The Web process never
signals or kills a child process directly.

- [ ] **Step 5: Complete responsive desktop/mobile task pages**

On desktop, place the configuration card beside task state and result cards;
on narrow screens stack them. Match current GUI labels for venue, sport,
当天/次日, companion, preferred court, time priority, and real-booking warning.
Add phase badges for waiting, warmup, core, finished, success, and error states.

- [ ] **Step 6: Run user route and domain tests**

Run: `python3 -m pytest tests/web/test_task_routes.py tests/web/test_tasks.py tests/web/test_auth_routes.py -v`

Expected: PASS.

- [ ] **Step 7: Commit user booking pages**

```bash
git add jlu_booking/web/routes/dashboard.py jlu_booking/web/routes/task_routes.py jlu_booking/web/templates jlu_booking/web/static jlu_booking/web/app.py tests/web/test_task_routes.py
git commit -m "feat(web): add user booking dashboard and task status"
```

### Task 10: Administrator Console and Protected Token Reveal

**Files:**
- Create: `jlu_booking/web/audit.py`
- Create: `jlu_booking/web/routes/admin.py`
- Create: `jlu_booking/web/templates/admin/dashboard.html`
- Create: `jlu_booking/web/templates/admin/users.html`
- Create: `jlu_booking/web/templates/admin/user_detail.html`
- Create: `jlu_booking/web/templates/admin/tasks.html`
- Create: `jlu_booking/web/templates/admin/audit.html`
- Create: `jlu_booking/web/templates/admin/reauth.html`
- Modify: `jlu_booking/web/app.py`
- Modify: `jlu_booking/web/static/app.js`
- Create: `tests/web/test_admin_routes.py`

**Interfaces:**
- Consumes: administrator dependency, `AccountService`, `CredentialService`, `TaskService`, `SessionService`
- Produces: `AuditService.record(admin_id: int, action: str, target_user_id: int | None, source_ip: str, metadata: Mapping[str, str], now: datetime) -> None`
- Produces: `ReauthenticationService.grant(admin_id: int, password: str, *, now: datetime) -> None`
- Produces: `ReauthenticationService.is_valid(admin_id: int, now: datetime) -> bool`
- Produces: administrator routes under `/admin`

- [ ] **Step 1: Write failing role and reveal tests**

Cover ordinary-user denial for every admin page and mutation, admin listing,
disable/re-enable, pending deletion, password reset, task stop, audit viewing,
and reveal behavior:

```python
def test_token_reveal_requires_recent_admin_password(
    client, admin_login, active_user
):
    response = client.post(
        f"/admin/users/{active_user.id}/token/reveal",
        data={"csrf_token": admin_login.csrf_token},
    )
    assert response.status_code == 403


def test_revealed_token_is_no_store_temporary_and_audited(
    client, admin_reauthenticated, active_user, database
):
    response = client.post(
        f"/admin/users/{active_user.id}/token/reveal",
        data={"csrf_token": admin_reauthenticated.csrf_token},
    )
    assert response.json() == {"token": "private-token", "hide_after": 30}
    assert response.headers["cache-control"] == "no-store"
    event = database.execute(
        "SELECT action, metadata_json FROM audit_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert event["action"] == "token_revealed"
    assert "private-token" not in event["metadata_json"]
```

Add a JavaScript-facing route test that a second reveal after the five-minute
grant returns 403 and that the ordinary user cannot infer whether another
user ID exists.

- [ ] **Step 2: Run admin tests and verify failure**

Run: `python3 -m pytest tests/web/test_admin_routes.py -v`

Expected: FAIL because admin routes and audit services do not exist.

- [ ] **Step 3: Implement audit and five-minute reauthentication grants**

Store reauthentication grants server-side against the current admin session,
not in a browser-readable cookie. Require availability from the
five-failures-per-15-minute reauthentication bucket, verify the Argon2
password, consume the bucket only on failure, clear it on success, and set
`reauthenticated_at`. A grant is valid when the same session is active and age
is at most five minutes.

`AuditService` allowlists action names and serializes only allowlisted metadata
keys. Explicitly reject metadata keys or values containing `token`, `password`,
`cookie`, `csrf`, `student_number`, or the decrypted values supplied by the
credential service.

- [ ] **Step 4: Implement administrator operations**

List users with masked Token and companion summaries, active/pending counts,
and task results. Disable, re-enable, delete pending accounts, reset passwords,
stop tasks, and expose audit events. Every state-changing action uses CSRF and
records success or failure without sensitive values.

Re-enable calls the account service's transactional 30-user check. Password
reset generates a cryptographically random 16-character temporary password,
stores only its hash, invalidates sessions, and shows the plaintext exactly
once in a `Cache-Control: no-store` response.

- [ ] **Step 5: Implement protected Token display**

The reveal route requires the recent grant, calls `CredentialService.reveal_token`,
returns only `{token, hide_after: 30}`, and sets `Cache-Control: no-store,
private`. JavaScript writes the value through `textContent`, clears it after 30
seconds, clears it on page hide/navigation, and never saves it to localStorage,
sessionStorage, a URL, or a form field.

- [ ] **Step 6: Run administrator and security tests**

Run: `python3 -m pytest tests/web/test_admin_routes.py tests/web/test_security.py tests/web/test_credentials.py -v`

Expected: PASS.

- [ ] **Step 7: Commit administrator controls**

```bash
git add jlu_booking/web/audit.py jlu_booking/web/routes/admin.py jlu_booking/web/templates/admin jlu_booking/web/app.py jlu_booking/web/static/app.js tests/web/test_admin_routes.py
git commit -m "feat(web): add audited administrator console"
```

### Task 11: Deployment Files, Operations Guide, and Privacy Checks

**Files:**
- Create: `deploy/Caddyfile.example`
- Create: `deploy/jlu-booking-web.service`
- Create: `deploy/jlu-booking-scheduler.service`
- Create: `deploy/jlu-booking-web.env.example`
- Create: `docs/web-deployment.md`
- Modify: `.gitignore`
- Modify: `README.md`
- Modify: `tools/privacy_check.py`
- Modify: `tests/test_privacy.py`
- Modify: `tests/test_build_package.py`

**Interfaces:**
- Consumes: `jlu-booking-web serve`, `scheduler`, `backup`, `generate-keys`, and `create-admin`
- Produces: reviewable deployment examples with no real domain, IP, user data, or secret
- Produces: privacy-check coverage for Web database, runtime, backup, environment, and deployment files

- [ ] **Step 1: Add failing deployment and privacy tests**

Extend package and privacy tests to assert:

```python
def test_deployment_examples_contain_no_real_secret_or_host():
    text = "\n".join(
        Path(path).read_text(encoding="utf-8")
        for path in (
            "deploy/Caddyfile.example",
            "deploy/jlu-booking-web.env.example",
            "deploy/jlu-booking-web.service",
            "deploy/jlu-booking-scheduler.service",
        )
    )
    assert "example.com" in text
    assert "JLU_BOOKING_TOKEN=" not in text
    assert "/Users/" not in text
    assert "private-token" not in text
```

Add checks that likely Web runtime databases, key files, backups, task
directories, and `.env` files are gitignored and rejected by
`tools/privacy_check.py` if staged.

- [ ] **Step 2: Run deployment/privacy tests and verify failure**

Run: `python3 -m pytest tests/test_privacy.py tests/test_build_package.py -v`

Expected: FAIL because deployment examples and Web privacy rules do not exist.

- [ ] **Step 3: Add safe Caddy and systemd examples**

Use `booking.example.com`, proxy only to `127.0.0.1:8000`, and include no real
server identity. Both services use a dedicated `jlu-booking` user, a fixed
working directory, an `EnvironmentFile` under `/etc/jlu-booking/web.env`,
`NoNewPrivileges=true`, `PrivateTmp=true`, `ProtectSystem=strict`, and explicit
`ReadWritePaths=/var/lib/jlu-booking`.

Set scheduler `KillMode=control-group`, `Restart=on-failure`, and a stop timeout
longer than the worker's five-second graceful stop. The example environment
contains paths and boolean settings only; keys remain in separate `0600` files.

- [ ] **Step 4: Write the end-to-end deployment guide**

Document, in order:

1. install Python and Caddy;
2. create the unprivileged service user and `/var/lib/jlu-booking`;
3. install `.[web]` from a committed checkout;
4. generate keys and create the bootstrap admin;
5. set the purchased subdomain's A record;
6. install and start both systemd units;
7. install the Caddy site and verify HTTPS;
8. run a dry test with fake/local fixtures only;
9. inspect service status, logs, backups, and disk usage;
10. update through a committed revision and restart Web before scheduler.

State clearly that automated verification must not call the real booking API.
Include rollback instructions that stop services, restore one SQLite backup,
restore its matching key files, and restart services.

- [ ] **Step 5: Update README and privacy tooling**

Add a short Web section linking the deployment guide and distinguishing the
optional server interface from the retained desktop application. Extend
`.gitignore` with `*.sqlite3`, `*.sqlite3-*`, `*.key`, `.env`,
`runtime/web/`, and `backups/`. Make privacy checking inspect deployment,
template, JavaScript, and new test fixtures while allowing only
`booking.example.com` and explicit `fake-*` test credentials.

- [ ] **Step 6: Run docs, privacy, and packaging checks**

Run:

```bash
python3 -m pytest tests/test_privacy.py tests/test_build_package.py -v
python3 tools/privacy_check.py
git diff --check
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit operations documentation**

```bash
git add deploy docs/web-deployment.md README.md .gitignore tools/privacy_check.py tests/test_privacy.py tests/test_build_package.py
git commit -m "docs(web): add secure server deployment workflow"
```

### Task 12: Full Regression, Resource Smoke Test, and Release Readiness

**Files:**
- Create: `tests/web/test_web_smoke.py`
- Modify: `docs/web-deployment.md`
- Modify: only files proven necessary by failures found in this task

**Interfaces:**
- Consumes: the complete V1 Web subsystem and all existing desktop/CLI behavior
- Produces: one repeatable local smoke test and final verification evidence

- [ ] **Step 1: Add a no-network end-to-end smoke test**

The smoke test must use fake Token and companion validators plus a fake worker:

```python
def test_public_registration_to_finished_task_without_network(web_harness):
    user = web_harness.register("alice", "correct horse battery staple")
    web_harness.activate(user, token="fake-token")
    web_harness.save_companion(user, "20260001", name="测试同学")
    task = web_harness.schedule_next_run(user, target_day="today")
    web_harness.scheduler_start(task)
    web_harness.worker_finish(task, status="success")

    page = web_harness.task_page(user, task)
    assert "预约成功" in page
    assert "fake-token" not in page
    assert "20260001" not in page
```

Add a second flow with 10 users and fake workers to record peak process count,
database task count, isolated paths, and successful final collection. The test
does not assert machine-specific RAM values; it asserts no eleventh task starts
and no user path overlaps.

- [ ] **Step 2: Run the complete Web test suite**

Run: `python3 -m pytest tests/web -v`

Expected: PASS with no network calls. Configure the Web test fixture to raise
immediately if `requests.Session.request` is reached without an explicit fake.

- [ ] **Step 3: Run the complete existing regression suite**

Run: `python3 -m pytest`

Expected: all existing and new tests PASS; no test reads real user state under
`~/.local/state`, real Token files, or the university API.

- [ ] **Step 4: Run static and privacy verification**

Run:

```bash
python3 tools/privacy_check.py
python3 -m compileall -q jlu_booking
git diff --check
```

Expected: all commands exit 0 with no privacy finding or syntax error.

- [ ] **Step 5: Perform a local HTTP smoke run with fake validators**

Start the app against a temporary database with secure-cookie enforcement
disabled only for localhost, request login and registration pages, verify CSS
and JavaScript return 200, then stop it. Do not configure a real Token and do
not start the real scheduler command.

Record the exact command and result in the validation section of
`docs/web-deployment.md` without committing temporary paths or machine data.

- [ ] **Step 6: Review the complete branch against the spec**

Check every acceptance criterion in
`docs/superpowers/specs/2026-09-22-web-platform-v1-design.md`, inspect
`git diff main...HEAD`, verify no booking-core timing line changed without an
explicit test reason, and confirm every child-process path is user/task scoped.

- [ ] **Step 7: Commit final test and readiness fixes**

```bash
git add tests/web/test_web_smoke.py docs/web-deployment.md
git add -u
git commit -m "test(web): verify isolated multi-user workflow"
```

- [ ] **Step 8: Request code review and follow the branch completion workflow**

Use `superpowers:requesting-code-review`, resolve findings with
`superpowers:receiving-code-review`, rerun the full verification commands, and
then use `superpowers:finishing-a-development-branch`. Do not deploy from an
uncommitted worktree and do not call the real booking API during review.
