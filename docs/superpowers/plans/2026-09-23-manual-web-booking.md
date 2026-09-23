# Manual Web Booking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a signed-in ordinary user select a live query result, precheck it, and explicitly submit one real booking without accidental duplicate submissions or collision with that user's automatic task.

**Architecture:** The server stores short-lived, user-owned query candidates and durable manual submission attempts in SQLite. The browser sends opaque IDs only. A manual booking service performs `canBook`, requires a one-use confirmation nonce, persists the in-flight state before `freeBuyPlace`, and treats any uncertain post-submit outcome as terminal until the user checks the school system. Scheduler/task admission honor the same account/date guards.

**Tech Stack:** Python 3.10+, FastAPI/Jinja, sqlite3 WAL, existing `jlu_booking.api`, pytest with fake API calls.

**Spec:** `docs/superpowers/specs/2026-09-23-web-gui-daily-booking-admin-design.md`

## Global Constraints

- Execute after `2026-09-23-web-workspaces-and-query.md` and `2026-09-23-daily-booking-plans.md`.
- Never call the real school API in tests or submit a real booking during development/acceptance.
- Preserve automatic CORE speed and deadline; do not add sleep, cooldown, or fixed waits to its worker.
- Browser HTML/JSON/logs never expose plaintext Token or companion student number.
- Manual submission does not consume the daily 10 automatic-task slots, but obeys school limits and account blocks.

## Review Focus

- A candidate ID from another account or an expired query cannot reach `canBook` (Task 1 test).
- Double-click/replayed confirmation cannot call `freeBuyPlace` twice (Task 2 test).
- A crash after recording `submitting` cannot silently replay the request (Task 2 test).
- A running automatic task and manual submission cannot call the school concurrently for the same user (Task 3 test).
- A successful or unknown manual result blocks same-date automatic booking even after restart (Task 3 test).

---

### Task 1: Short-lived owned candidates and precheck

**Files:** Modify `jlu_booking/web/db.py`, `jlu_booking/web/app.py`, `jlu_booking/web/routes/availability.py`, `jlu_booking/web/templates/dashboard.html`; create `jlu_booking/web/manual_booking.py`, `jlu_booking/web/routes/manual_booking.py`, `jlu_booking/web/templates/manual_confirm.html`, `tests/web/test_manual_booking.py`.

**Interfaces:** Schema migration 3 creates `manual_candidates` (opaque random `id`, `user_id`, validated venue/sport/target date/court/short name/start/end, `created_at`, `expires_at`) and `manual_booking_attempts` (random ID, owner, candidate, current companion row ID, school companion user ID, credential update timestamp, confirmation hash, status, timestamps, sanitized detail). `ManualBookingService(database_path: Path, credentials: CredentialService, *, can_book_func=can_book, book_place_func=book_place, companion_func=get_companion_user)` uses a fresh `connect_database(database_path)` connection for each short DB phase, never across a school network call. `register_candidates(user_id: int, result: AvailabilityResult) -> list[dict]` returns candidate IDs with allowlisted display fields; `precheck(user_id: int, candidate_id: str, now: datetime) -> ManualPrecheck` runs `can_book`, revalidates the saved companion through `get_companion_user` using the owner's Token, and yields a one-use confirmation value. Candidates expire after two minutes.

- [ ] **Step 1: Write failing migration and precheck tests.** Migrate a v2 temporary database twice and assert one version-3 row. Query with a fake school function, register candidates for Alice, then assert Bob/anonymous/disabled user cannot precheck Alice's ID and expired IDs cannot reach fake `can_book`. Assert malformed venue/sport/short name in client form is ignored because only the database candidate is used. Fake a `can_book` rejection and ensure no confirmation nonce and no submission call. Assert candidate rows contain no Token or student number.

  ```python
  candidates = service.register_candidates(alice.id, result)
  with pytest.raises(CandidateUnavailable):
      service.precheck(bob.id, candidates[0]["candidate_id"], now=clock)
  with pytest.raises(CandidateUnavailable):
      service.precheck(alice.id, candidates[0]["candidate_id"], now=clock + timedelta(minutes=2))
  assert can_book_calls == [] and book_place_calls == []
  ```
- [ ] **Step 2: Run `.venv/bin/python -m pytest tests/web/test_manual_booking.py -q`;** expect missing migration/service failures.
- [ ] **Step 3: Implement migration and candidate service.** Use `secrets.token_urlsafe(24)` IDs, `ZoneInfo("Asia/Shanghai")`, owner-scoped SELECT, and exact expiry comparison. Store only allowlisted fields from the validated `AvailabilityResult`; never serialize the original school response. Inject `can_book`, `get_companion_user`, and `book_place` callables into the service for tests. Validate active ordinary user, verified Token and owned companion before precheck; obtain the school's companion user ID from the saved encrypted student number, store that ID and the companion row ID in the attempt, and reject submit if the companion row changed. Hash the confirmation nonce in SQLite. Add a partial unique index for one `submitting` attempt per user.

  ```sql
  CREATE TABLE manual_candidates (
    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    venue TEXT NOT NULL, sport TEXT NOT NULL, query_date TEXT NOT NULL,
    court_name TEXT NOT NULL, place_short_name TEXT NOT NULL,
    start_time TEXT NOT NULL, end_time TEXT NOT NULL,
    created_at TEXT NOT NULL, expires_at TEXT NOT NULL
  );
  CREATE TABLE manual_booking_attempts (
    id TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
    candidate_id TEXT NOT NULL REFERENCES manual_candidates(id),
    companion_id INTEGER NOT NULL REFERENCES companions(id),
    school_companion_id TEXT NOT NULL,
    credential_updated_at TEXT NOT NULL,
    confirmation_hash BLOB NOT NULL UNIQUE, status TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, detail TEXT NOT NULL DEFAULT ''
  );
  CREATE UNIQUE INDEX one_manual_submission_per_user
  ON manual_booking_attempts(user_id) WHERE status='submitting';
  ```
- [ ] **Step 4: Wire UI.** The existing `/availability/query` route registers returned slots and renders per-slot POST forms to `/manual/precheck` with CSRF and opaque candidate ID. The precheck route calls the service in a worker thread and renders `manual_confirm.html` with venue/date/court/time, masked companion, and a final explicit confirmation button; it never calls `book_place`. Errors render a safe explanation and a link to query again.

  ```jinja2
  <form method="post" action="/manual/precheck">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <input type="hidden" name="candidate_id" value="{{ slot.candidate_id }}">
    <button type="submit">检查并选择 {{ slot.court_name }} {{ slot.start }}–{{ slot.end }}</button>
  </form>
  ```
- [ ] **Step 5: Run tests and commit.** `.venv/bin/python -m pytest tests/web/test_manual_booking.py tests/web/test_availability.py -q`; `git add jlu_booking/web tests/web && git commit -m 'feat(web): precheck owned availability candidates'`.

### Task 2: Durable one-use manual submission

**Files:** Modify `jlu_booking/web/manual_booking.py`, `jlu_booking/web/routes/manual_booking.py`, `jlu_booking/web/templates/manual_confirm.html`, `jlu_booking/web/app.py`; create `jlu_booking/web/templates/manual_result.html`, `tests/web/test_manual_booking_routes.py`; test `tests/web/test_manual_booking.py`.

**Interfaces:** `ManualBookingService.submit(user_id: int, attempt_id: str, nonce: str, now: datetime) -> ManualResult` atomically transitions `prechecked → submitting`, performs final `can_book`, then exactly one `book_place` call. Explicit success becomes `success`, explicit school rejection becomes `rejected`, and post-submit transport/parse error becomes `unknown`. `reconcile_incomplete(now)` marks orphaned `submitting` attempts `unknown` at application startup; it never replays requests. Add `manual_booking` as an optional trailing `AppServices` field, initialized by `create_app` and available to availability/manual routes.

- [ ] **Step 1: Write failing service/route tests.** Assert precheck alone never books; submit always performs final `can_book` then one `book_place`; replay of the same nonce, concurrent submits and browser refresh all observe the prior result with no second school call. If final `can_book` fails, `book_place` remains at zero. If `book_place` explicitly rejects, record `rejected`; if it times out or raises an unexpected parse error, record `unknown` and refuse retries. Verify explicit HTTP 429, ACCOUNT_BLOCKED, Token invalid, and school daily limit show distinct safe messages with no automatic retry. Seed an orphaned `submitting` row and assert startup reconciliation changes it to `unknown` without invoking the fake API. Replacing Token or companion after precheck, or crossing Beijing midnight so the target is in the past, must reject before `book_place`. Assert CSRF, ownership, active role, `no-store` and Token redaction.

  ```python
  first = service.submit(alice.id, attempt.id, nonce, now=clock)
  second = service.submit(alice.id, attempt.id, nonce, now=clock)
  assert first.status == second.status == "success"
  assert len(book_place_calls) == 1
  ```
- [ ] **Step 2: Run `.venv/bin/python -m pytest tests/web/test_manual_booking.py tests/web/test_manual_booking_routes.py -q`;** expect missing submit flow failures.
- [ ] **Step 3: Implement the state transitions.** Within `BEGIN IMMEDIATE`, validate attempt ownership, nonce hash, candidate expiry, target date not before today's Beijing date, unchanged companion row and credential `updated_at`, status, no conflicting active attempt, and no automatic success/unknown for the target date; commit `submitting` before any external call. Use a blocking school call in `to_thread.run_sync` from the route, but keep all DB writes short. A final `can_book` failure is a pre-submit `rejected` result and must never call `book_place`; an explicit school refusal from `book_place` is also `rejected`. For any error after `book_place` is invoked without an explicit rejection, mark `unknown`, never schedule a retry. Pass the saved school companion user ID to `book_place`. Never print exception text containing request URLs/Token. Successful school response is stored even if the browser disconnects.

  ```python
  with closing(connect_database(self._database_path)) as connection:
      with transaction(connection, immediate=True):
          attempt = self._claim_once(connection, user_id, attempt_id, nonce, now)
  try:
      self._can_book_from_attempt(attempt)
  except Exception:
      return self._finish(attempt.id, "rejected", now)
  try:
      self._book_place_from_attempt(attempt, companion_user_ids=[attempt.school_companion_id])
  except ServerResponseError:
      return self._finish(attempt.id, "rejected", now)
  except Exception:
      return self._finish(attempt.id, "unknown", now)
  return self._finish(attempt.id, "success", now)
  ```
- [ ] **Step 4: Render a result page.** Show `success`, `rejected`, or `unknown` with the target details and a clear instruction to inspect the school system on unknown; a successful/unknown attempt has no second submit control. Include a link back to the workbench. Add app-startup reconciliation before serving requests, respecting the one-Web-worker deployment assumption.
- [ ] **Step 5: Run tests and commit.** `.venv/bin/python -m pytest tests/web/test_manual_booking.py tests/web/test_manual_booking_routes.py -q`; `.venv/bin/python tools/privacy_check.py`; `git add jlu_booking/web tests/web && git commit -m 'feat(web): submit manual bookings once with durable outcomes'`.

### Task 3: Account/date exclusion shared with automatic tasks

**Files:** Modify `jlu_booking/web/manual_booking.py`, `jlu_booking/web/tasks.py`, `jlu_booking/web/daily_plans.py`, `jlu_booking/web/scheduler.py`; test `tests/web/test_manual_booking.py`, `tests/web/test_scheduler.py`, `tests/web/test_daily_plans.py`.

**Interfaces:** `manual_booking_attempts` provides account-scoped `submitting` and target-date `success/unknown` guards. Automatic task creation rejects a same-user/target-date success or unknown; daily materialization skips it. Scheduler `_claim_due` does not claim a task when the account has an active manual submission; when manual success/unknown for its target date exists, it calls a new `_finalize_skipped(task: BookingTask, now: datetime, detail: str) -> None` helper that writes a `stopped` task/run row without launching the worker. Manual `submit` rejects any running automatic task.

- [ ] **Step 1: Write failing race/dedup tests.** Add a local `seed_manual_attempt(connection, user_id, target_date, status)` fixture helper that inserts an owned candidate and attempt. At 07:27, seed `submitting` and assert scheduler launches zero workers for that user; after a recorded `rejected` result, a later tick in the start window may claim normally. Seed `success` and `unknown` and assert no worker is launched for the same target date. Assert `TaskService.create` and `DailyPlanService.materialize` cannot create a conflicting same-date task. Seed a running auto task and assert manual `submit` does not call either school function. Verify another user remains unaffected and 07:29:57/07:33 boundaries retain existing behavior.

  ```python
  seed_manual_attempt(connection, user_id=alice.id, target_date=day, status="submitting")
  scheduler.run_once(datetime(2026, 9, 23, 7, 27, tzinfo=BEIJING))
  assert alice_task.id not in worker.started_task_ids
  assert bob_task.id in worker.started_task_ids
  ```
- [ ] **Step 2: Run `.venv/bin/python -m pytest tests/web/test_manual_booking.py tests/web/test_scheduler.py tests/web/test_daily_plans.py -q`;** expect guards to fail.
- [ ] **Step 3: Add the shared guards.** All guards query SQLite inside the existing transaction before automatic claim or manual transition. On successful or unknown manual submission, atomically cancel still-`scheduled` same-target-date tasks; never terminate already running work. Do not mutate `jlu_booking.auto`; scheduler simply skips or finalizes before launching a worker. Keep manual activity checks per user so one person's manual booking cannot delay another's CORE.

  ```python
  conflict = connection.execute("SELECT 1 FROM manual_booking_attempts a JOIN manual_candidates c ON c.id=a.candidate_id WHERE a.user_id=? AND c.query_date=? AND a.status IN ('success','unknown') LIMIT 1", (task.user_id, TaskService.target_date(task.execution_date, task.target_day).isoformat())).fetchone()
  if conflict is not None:
      self._finalize_skipped(task, now, "同日已有手动预约或结果不明，未启动自动任务。")
      continue
  ```
- [ ] **Step 4: Verify and commit.** `.venv/bin/python -m pytest tests/web/test_manual_booking.py tests/web/test_scheduler.py tests/web/test_daily_plans.py tests/web/test_tasks.py -q`; `git add jlu_booking/web tests/web && git commit -m 'fix(web): prevent manual and automatic duplicate submissions'`.

### Task 4: End-to-end offline acceptance and documentation

**Files:** Modify `README.md`, `docs/web-deployment.md`, `docs/usage.md` as needed; test `tests/web/test_web_smoke.py`.

**Interfaces:** The public web route documents one explicit live-query/precheck/submit path; deployment guide keeps committed-revision and no-live-booking-test requirements.

- [ ] **Step 1: Extend offline smoke tests.** With fake `query_courts`, `can_book`, `book_place`, and a temporary database, follow register→Token activation→user workspace→query→precheck→confirm→result; assert exactly one fake submission. Test admin login lands at `/admin`, user cannot see admin data, daily toggle creates/cancels its task, and all fake requests remain in process. Patch `requests.Session.request` to raise if any test touches the real network.

  ```python
  monkeypatch.setattr(requests.Session, "request", lambda *_a, **_k: pytest.fail("live network call"))
  result = client.post("/manual/submit", data={"csrf_token": csrf_token, "attempt_id": attempt_id, "nonce": nonce})
  assert result.status_code in {200, 303}
  assert len(book_place_calls) == 1
  ```
- [ ] **Step 2: Run `.venv/bin/python -m pytest tests/web/test_web_smoke.py -q`;** expect failures until the complete flow is wired.
- [ ] **Step 3: Update documentation.** Explain user/admin landing pages, live query and manual confirmation, recurring queue/10-task cap, daily off behavior, unknown-result checks, and the offline-only acceptance rule. Keep desktop GUI/CLI guidance intact.

  ```markdown
  手动预约必须先查询并检查场地，再点击“确认真实预约”。如果提交结果显示“不确定”，请到学校系统核对；不要直接重复提交。
  每日自动预约开启后按开启时间排队，每个执行日最多接纳 10 个自动任务；关闭会取消尚未启动的当日每日任务。
  ```
- [ ] **Step 4: Run final checks.** `.venv/bin/python -m pytest`; `.venv/bin/python tools/privacy_check.py`; `.venv/bin/python -m compileall -q jlu_booking`; `git diff --check`. Fix only failures attributable to this work, rerun affected checks, and verify no real API call was made.
- [ ] **Step 5: Commit.** `git add README.md docs tests/web && git commit -m 'docs: explain web booking workspace and safety flow'`.

## Handoff

After all three plans and final checks pass, review the branch and prepare a clean integration/deployment handoff. Do not update the live server from uncommitted work or call a real booking API during acceptance.
