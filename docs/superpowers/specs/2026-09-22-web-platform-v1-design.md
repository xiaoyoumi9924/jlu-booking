# JLU Booking Web Platform V1 Design

## Purpose

JLU Booking Web Platform V1 lets a small group use the existing booking
assistant through a browser. Users do not install Python or keep a personal
computer online. The server stores each user's private configuration and runs
the existing automatic booking program at the configured Beijing-time window.

V1 is intentionally small. It provides public registration, login, per-user
JLU credentials and companion data, one-shot scheduled booking tasks, task
status and logs, and an administrator console. It preserves the existing
desktop GUI, CLI, booking state machine, and request behavior.

## Confirmed Capacity and Operating Constraints

- The server has 2 CPU cores, 3.54 GiB RAM, swap, and 40 GB storage.
- At most 30 active booking-user accounts may exist.
- Public registration is first come, first served.
- A pending registration becomes active only after it binds a valid JLU Token.
- Pending registrations expire after 24 hours and do not consume an active
  account slot.
- At most 10 booking tasks may be accepted for one execution date.
- Each user may have at most one scheduled or running task at a time.
- Each task runs under the user's own Token, companion, configuration, runtime
  directory, success markers, and logs.
- All scheduling boundaries use `ZoneInfo("Asia/Shanghai")`.
- V1 uses a purchased domain, Caddy-managed HTTPS, and a responsive browser UI.
- V1 does not call a real booking interface during automated tests.

## Non-Goals

V1 does not provide social login, email or SMS recovery, email or messaging
notifications, open-ended recurring tasks, a public API, payments, multiple
server nodes, PostgreSQL, Docker-per-user isolation, or a rewrite of the
booking core. It does not remove or replace the desktop GUI or CLI.

## Chosen Architecture

The Web platform is an additional adapter around the current program:

```text
Browser
  -> HTTPS / Caddy
  -> FastAPI Web process
       -> server-side HTML and small JavaScript enhancements
       -> SQLite in WAL mode
       -> encrypted credential store
  -> scheduler process
       -> one isolated subprocess per due booking task
            -> existing `python -m jlu_booking.auto`
            -> user-specific config and runtime directories
            -> university booking API
```

The Web process handles HTTP requests only. The scheduler owns task claiming,
process startup, process observation, and final-state collection. A slow or
restarted Web process therefore does not add delay to an active booking loop.

Each booking subprocess receives these values without changing global server
files:

- `JLU_BOOKING_TOKEN`: the decrypted Token for this process only;
- `JLU_BOOKING_CONFIG_FILE`: an immutable task-specific JSON snapshot;
- `JLU_BOOKING_RUNTIME_DIR`: a task-specific private runtime directory.

The scheduler passes a copied environment directly to `subprocess.Popen`; it
does not write the Token into a shell command, task JSON, database log, or
runtime log. The existing `jlu_booking.auto` process continues to enforce
PREHEAT, CORE, strict candidate priority, failure classification, success
state, and the 07:33 deadline.

## Packages and Runtime

Web support is an optional installation group so desktop installations do not
gain server dependencies. V1 uses:

- FastAPI and Uvicorn for HTTP;
- Jinja2 templates and project-owned CSS with small vanilla JavaScript files;
- Python's `sqlite3` module for storage;
- Argon2 for password hashing;
- `cryptography` for authenticated Token encryption;
- Caddy for TLS termination;
- systemd services for the Web process and scheduler.

No Node.js service or front-end build pipeline runs in production.

## Component Boundaries

The Web subsystem lives below `jlu_booking/web/` and has narrow modules:

- `app.py` creates the FastAPI application and installs middleware.
- `db.py` opens SQLite connections, enables WAL and foreign keys, and applies
  numbered schema migrations.
- `accounts.py` owns registration, account limits, password hashing, account
  states, login throttling, and administrator password resets.
- `credentials.py` owns Token normalization, encryption, blind indexing,
  validation, update, masking, and administrator reveal authorization.
- `tasks.py` validates task input and atomically enforces per-user and
  per-execution-date capacity.
- `scheduler.py` claims due tasks and manages booking subprocesses.
- `worker.py` creates immutable config snapshots and the isolated subprocess
  environment, then translates process and status-file results.
- `sessions.py` owns opaque server-side login sessions and CSRF tokens.
- `audit.py` records sensitive administrator actions.
- `routes/` groups authentication, user, task, and administrator endpoints.
- `templates/` and `static/` implement the responsive interface.

The existing `jlu_booking.auto`, `api`, `config`, `run_status`, and booking
error logic remain the source of truth for booking behavior. Web modules do
not duplicate candidate selection or submission logic.

## Data Model

SQLite uses foreign keys and WAL mode. Schema changes use monotonic migration
numbers recorded in a `schema_migrations` table.

### Users

`users` stores an integer ID, unique normalized username, password hash, role,
status, password-change flag, creation time, activation time, and pending
expiry time. Roles are `user` and `admin`. User states are `pending_token`,
`active`, `disabled`, and `deleted`.

Only active records with role `user` count toward the 30-account limit. The
bootstrap administrator is a management account and does not count toward the
booking-user limit. An administrator who also needs booking access creates a
separate ordinary user account.

Expired pending records are removed by periodic cleanup. Registration and
login endpoints are rate limited by IP and username. At most 100 unexpired
pending accounts may exist. Reaching that limit temporarily closes
registration until pending records expire or an administrator removes them.

### Credentials and Companions

`user_credentials` stores one encrypted JLU Token per user, its keyed blind
index, verification time, and last validation status. The blind index is an
HMAC of the normalized Token under a server key and has a unique constraint,
so the same Token cannot activate multiple accounts without exposing the
Token in searchable form.

`companions` stores the user's companion student number and returned name as
encrypted personal data. V1 supports the single companion required by the
current booking core. Both Token and companion ownership are always scoped by
`user_id`. Pages show a masked student number after it has been saved.

### Booking Tasks and Runs

`booking_tasks` stores owner, execution date, target-day mode, venue, sport,
companion reference, preferred court, ordered time priorities, status, and
timestamps. Task states are `scheduled`, `running`, `success`, `no_result`,
`token_invalid`, `account_blocked`, `daily_limit`, `submission_unknown`,
`network_unavailable`, `stopped`, `error`, and `cancelled`.

`task_runs` stores immutable execution metadata: process identifier while
owned by the scheduler, private runtime path, start and finish times, exit
code, final status, and a sanitized detail message. It never stores a Token.

The database enforces one non-terminal task per user. Task creation uses an
immediate transaction that counts non-cancelled tasks for the execution date
before inserting, so concurrent submissions cannot exceed 10.

### Sessions and Audit Events

`web_sessions` stores a hash of an opaque cookie value, user ID, CSRF secret,
creation time, last-seen time, and expiry time. The raw session value exists
only in the browser cookie. A session expires after 12 hours of inactivity or
seven days after creation, whichever occurs first. Password reset or password
change invalidates all other sessions for that account.

`request_throttles` stores short-lived counters for security-sensitive
actions. Registration allows five attempts per source IP per hour. Login locks
one username and source-IP pair for 15 minutes after 10 failed attempts in 15
minutes. Token validation and administrator reauthentication each allow five
failures per account per 15 minutes. Successful login clears the matching
login-failure counter. Expired counters are deleted by maintenance.

`audit_events` stores administrator ID, action, target user ID, timestamp, IP,
and non-sensitive metadata. It records account changes, password resets,
capacity-affecting actions, task stops, and full-Token reveals. Audit metadata
must not contain credentials.

## Registration and Authentication

Registration is public and first come, first served:

1. A visitor selects a username and password.
2. The server creates a `pending_token` account with a 24-hour expiry.
3. The user logs in only to the Token onboarding screen.
4. The user submits a Token. The server normalizes it and performs the current
   read-only online validation.
5. After successful validation, one database transaction verifies that fewer
   than 30 active booking users exist, verifies that the Token blind index is
   unique, saves the encrypted Token, and activates the user.
6. If capacity was filled while validation ran, activation fails without
   retaining the submitted Token and the page explains that registration is
   full.

Usernames are case-insensitive, contain 3 to 32 ASCII letters, digits,
underscores, or hyphens, and are stored in normalized lowercase form.
Passwords must contain at least 10 characters and are stored only as Argon2
hashes. Login uses generic failure messages and the throttling rules above.
The first administrator is created with a local server CLI that accepts the
password through a hidden prompt. V1 has no default administrator password.

Forgotten user passwords are reset by an administrator. The reset creates a
temporary password and sets `must_change_password`; all other pages redirect
the user to choose a new password after login.

## Credential Security and Administrator Access

Token ciphertext uses authenticated encryption under a master key stored
outside SQLite in a root-owned or service-owned file with mode `0600`. The
blind-index key is distinct from the encryption key. Production startup fails
closed if either required key is absent or insecurely configured.

The same authenticated-encryption service protects companion student numbers
and companion names. These values do not use the Token blind index.

Users can replace their own Token only after the replacement passes read-only
validation. A failed replacement leaves the old Token unchanged. Users see
only a masked Token after saving it.

Administrators can see every task result and sanitized log. Full-Token reveal
is an explicit privileged action:

1. The administrator enters the administrator password again.
2. A successful reauthentication grant is valid for at most five minutes.
3. A separate no-cache endpoint returns one requested Token.
4. The browser displays it for at most 30 seconds and then removes it from the
   document.
5. The server records the reveal in `audit_events`.

Reveal responses use `Cache-Control: no-store`; templates, page source,
exports, ordinary API responses, and logs never contain full Tokens.

## Task Scheduling Semantics

The interface schedules the next automatic run rather than storing a floating
meaning of “today”:

- Before 07:27 Beijing time, the next execution date is the current Beijing
  date.
- At or after 07:27, the next execution date is the following Beijing date.
- The user selects `当天` or `次日` relative to that fixed execution date.
- The page displays both exact dates before the user confirms.

New tasks never join an execution window that has already started. This keeps
capacity decisions deterministic and ensures every accepted task receives the
full PREHEAT and CORE windows.

Users may edit or cancel a scheduled task before 07:27:00 on its execution
date. Cancellation releases the date's capacity. At 07:27:00 the scheduler
atomically claims the task, changes it to `running`, and freezes its complete
configuration snapshot. A running or terminal task cannot be edited.

The scheduler may own at most 10 booking subprocesses because the database
will not accept more than 10 tasks for an execution date. It starts due tasks
without adding sleep, cooldown, staggering, or throttling inside the booking
core. HTTP 429 and the current explicit safety stops remain controlled only by
the existing booking program.

If the scheduler starts after 07:27 but before 07:29:57, it may claim tasks
that have not previously started so they can still participate in the
remaining PREHEAT and full CORE window. It does not start a new task at or
after 07:29:57. A task recorded as already running when the scheduler itself
restarts is not automatically submitted again; it is marked `error` for
administrator review unless its private status file already proves a terminal
result. This avoids an unsafe duplicate after an uncertain prior submission.

## Worker Isolation and State Collection

For each claimed task, the scheduler creates a private directory owned by the
service account and writes a validated, immutable `auto_booking.json` without
the Token. This file has mode `0600`; it necessarily contains the task's
companion number for the existing worker and is deleted after final status
collection. The scheduler launches the existing auto command directly,
without a shell, with the three environment overrides described above.

The scheduler captures standard output and error to the task's private log,
tails only sanitized text to the Web UI, and periodically reads the existing
`last_run.json`. Final task status is derived from that privacy-safe status
file first and process exit information second. `submission_unknown` remains
a terminal status that requires manual checking and is never automatically
retried.

Stopping a scheduled task simply cancels it. Stopping a running task sends a
graceful termination signal, waits five seconds, then kills the process if
necessary and records `stopped`. The UI warns that stopping after a final
request was sent may require checking the university system.

## User Interface

The visual language follows the existing desktop GUI: JLU blue palette, logo,
card layout, Chinese labels, venue and sport selection, date choice, companion
field, preferred court, ordered time priorities, mode explanation, state
badges, and readable log view.

Desktop screens use a two-column configuration and status layout. Narrow
screens stack the same cards into one column, enlarge touch targets, and keep
the log in its own scrollable region. Core screens are:

- public registration and login;
- pending Token onboarding;
- user dashboard with next execution time, task status, and recent result;
- Token and companion settings;
- booking task create/edit confirmation;
- task detail with sanitized live log;
- administrator dashboard, users, task results, audit log, and protected Token
  reveal.

JavaScript improves ordered time controls, live status, and log updates, but
forms remain server validated. Closing the browser has no effect on a task.
An open task page polls sanitized status and the latest 500 log lines every
three seconds while visible; the dashboard polls every 15 seconds. Polling
pauses when the page is hidden. These reads never interact with the booking
subprocess or add delay to it.

## HTTP and Browser Security

Caddy is the only public listener and redirects HTTP to HTTPS. Uvicorn binds
to loopback. Session cookies are `Secure`, `HttpOnly`, and `SameSite=Lax`.
State-changing forms and requests require CSRF validation. Responses use a
restrictive Content Security Policy, deny framing, disable MIME sniffing, and
avoid caching authenticated pages that may contain personal information.

Registration, login, password reauthentication, and Token validation use the
database-backed limits defined above. A user may submit at most 20 task create,
edit, cancel, or stop requests per minute. Forwarded client addresses are
trusted only from the local Caddy proxy. Username, password, Token, student
number, cookie, CSRF secret, and encryption keys are redacted from application
logs.

## Failure Handling

- Invalid or unavailable Token validation does not activate an account and
  does not consume one of the 30 active slots.
- A later Token expiry changes the task to `token_invalid`; the account stays
  active and the user must replace the Token before scheduling again.
- `ACCOUNT_BLOCKED`, daily limit, `BookingOutcomeUnknown`, network failure,
  and rate limiting retain the existing booking-core behavior and surface as
  explicit task results.
- Database lock contention returns a retryable page error; capacity and task
  state transitions stay transactional.
- If the Web process is unavailable, the separate scheduler and running child
  processes continue.
- If the scheduler restarts, it reconciles status files before deciding task
  outcomes and never blindly restarts a previously running task.
- Corrupt or missing task runtime files produce an `error` result without
  exposing another user's directory.
- The browser reads at most 500 recent lines. Each runtime log rotates at
  10 MiB and retains three rotated files until normal retention cleanup.

## Data Retention and Backup

Detailed runtime logs are retained for 30 days. V1 retains task result metadata
and audit events indefinitely; changing that policy requires a later migration
and does not silently delete existing records. A daily local backup copies
SQLite safely and retains 14 daily snapshots. Token encryption keys are backed
up separately from database snapshots; database backups alone cannot decrypt
Tokens.

Expired sessions, expired pending users, old runtime logs, and orphaned task
directories are cleaned by the scheduler's maintenance job. Cleanup always
checks ownership and task state before deleting a directory.

## Deployment

Production runs under a dedicated unprivileged Linux account with these
systemd units:

- `jlu-booking-web.service` for Uvicorn;
- `jlu-booking-scheduler.service` for task scheduling and child processes.

Caddy proxies the selected booking subdomain to Uvicorn over loopback and
manages TLS certificates. The application data directory, SQLite file,
encryption keys, task directories, and backups are outside the Git checkout
and writable only by the service account. Deployment never runs from an
uncommitted worktree.

The first release includes example environment and systemd files with no real
domain, password, Token, private path, or secret committed to the repository.

## Test Strategy

Tests use temporary SQLite databases, temporary runtime directories, fixed
Beijing clocks, fake Token validators, and fake worker processes. They never
call the real university API or perform a real booking.

Coverage includes:

- public registration, pending expiry, 30-user activation limit, and Token
  uniqueness under concurrent activation;
- password hashing, login throttling, sessions, CSRF, authorization, password
  reset, forced password change, and admin reauthentication;
- Token encryption, masking, replacement, reveal expiry, no-store responses,
  and audit events;
- exact next-run calculations around 07:27 Beijing time;
- one-active-task-per-user and atomic 10-task capacity enforcement;
- cancellation and configuration locking at 07:27;
- scheduler startup rules around 07:27, 07:29:57, and 07:33;
- isolated config/runtime paths and no Token in files, commands, output, or
  database logs;
- translation of all existing run-status outcomes, including
  `submission_unknown` and `account_blocked`;
- scheduler and Web restart reconciliation;
- user-to-user isolation for pages, logs, task IDs, and administrator routes;
- responsive page smoke tests and sanitized log rendering;
- the full existing test suite, privacy checker, compile check, and diff check.

## Acceptance Criteria

V1 is complete when:

1. A visitor can register, validate an independent JLU Token, and become one
   of at most 30 active booking users.
2. An active user can save an independently validated companion and schedule
   one task for the next execution date while daily capacity remains below 10.
3. At 07:27 Beijing time, the scheduler starts each accepted task in an
   isolated subprocess using the current booking core without changing CORE
   request timing or candidate behavior.
4. Users can close the browser, return later, and see only their own current
   status, sanitized logs, and result.
5. Administrators can manage users and tasks, inspect all results, and reveal
   a full Token only after reauthentication with an audit record.
6. The desktop GUI and CLI continue to work and all existing tests pass.
7. Automated tests prove isolation, limits, time boundaries, secret handling,
   and scheduler recovery without contacting the real booking service.
8. The service can be deployed behind Caddy on the purchased domain using the
   documented systemd units and secret setup.
