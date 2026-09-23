# Web Workspaces and Live Availability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send each role to its own responsive workspace and let ordinary users query real venue availability using their own server-held Token.

**Architecture:** Keep FastAPI, Jinja, SQLite and the existing encrypted credential service. A read-only availability service validates venue, sport and Beijing target date, calls the shared API in a worker thread, and returns sanitized slots to the user workspace. Existing administrator routes keep their authorization and get a dedicated template shell.

**Tech Stack:** Python 3.10+, FastAPI, Jinja2, vanilla JavaScript/CSS, pytest, SQLite.

**Spec:** `docs/superpowers/specs/2026-09-23-web-gui-daily-booking-admin-design.md`

## Global Constraints

- Never call the live JLU API from tests; inject fake query functions.
- Preserve desktop GUI, CLI, automatic booking core, 07:29:57–07:33 CORE and existing task/safety behavior.
- Venue and sport choices come from `jlu_booking.api.VENUES`; dates use `ZoneInfo("Asia/Shanghai")`.
- Never render or log plaintext Token, student number, or raw school response.
- The web process does not run the automatic worker; the separate scheduler remains its owner.

## Review Focus

- An administrator at `/` or after login must land at `/admin`, never the user query page (Task 1 test).
- A disabled or pending account cannot query even with a valid cookie (Task 2 test).
- A mismatched venue/sport pair is rejected before an external call (Task 2 test).
- Two users querying concurrently only use their own Tokens and see only their own results (Task 2 test).
- A narrow viewport keeps navigation usable and results readable (Task 3 markup/CSS test and visual check).

---

### Task 1: Role destinations and separate page shells

**Files:** Modify `jlu_booking/web/routes/auth.py`, `jlu_booking/web/routes/dashboard.py`, `jlu_booking/web/templates/base.html`, `jlu_booking/web/templates/profile.html`, `jlu_booking/web/templates/task_form.html`, `jlu_booking/web/templates/task_detail.html`, `jlu_booking/web/templates/admin/dashboard.html`, `jlu_booking/web/templates/admin/users.html`, `jlu_booking/web/templates/admin/user_detail.html`, `jlu_booking/web/templates/admin/tasks.html`, `jlu_booking/web/templates/admin/task_detail.html`, `jlu_booking/web/templates/admin/audit.html`, `jlu_booking/web/templates/admin/reauth.html`; create `jlu_booking/web/templates/admin/shell.html`, `jlu_booking/web/templates/user_shell.html`; test `tests/web/test_web_workspaces.py`.

**Interfaces:** `_auth_destination(user) -> str` returns `/admin` for active admins and `/` for active users after existing onboarding guards. `GET /` redirects admins to `/admin`. The shells expose Jinja `content` and `page_class` blocks; all administrator templates extend `admin/shell.html`.

- [ ] **Step 1: Write failing role tests.** In `tests/web/test_web_workspaces.py`, use the existing `TestClient`/temporary-database fixture pattern from `tests/web/test_admin_routes.py` and fake Token validation. Assert login `Location` is `/` for an active user and `/admin` for an administrator; `GET /` as administrator redirects `/admin`; user requests to `/admin`, `/admin/users`, `/admin/tasks` still return 404. Verify each admin page contains `data-admin-nav`, while ordinary dashboard contains `data-user-nav`.

  ```python
  assert user_login.headers["location"] == "/"
  assert admin_login.headers["location"] == "/admin"
  assert admin_client.get("/", follow_redirects=False).headers["location"] == "/admin"
  assert "data-admin-nav" in admin_client.get("/admin").text
  assert user_client.get("/admin").status_code == 404
  ```
- [ ] **Step 2: Run the focused test.** `.venv/bin/python -m pytest tests/web/test_web_workspaces.py -q`; expect failure because active login currently redirects `/profile` and shells do not exist.
- [ ] **Step 3: Implement role routing and shells.** Keep password-change and pending-token branches first, then return `/admin` for `user.role == "admin"`, else `/`. In `dashboard.dashboard`, redirect admin before constructing user context. Add a base-template `navigation` block and small user/admin shells; update all admin templates to extend the admin shell and existing authenticated user templates to extend the user shell without changing route permissions. Preserve masked Token and five-minute administrator reauthentication.

  ```python
  if user.status == "pending_token":
      return "/onboarding/token"
  return "/admin" if user.role == "admin" else "/"
  ```

  ```jinja2
  {# admin/shell.html #}
  {% extends "base.html" %}
  {% block navigation %}<nav data-admin-nav aria-label="管理导航"><a href="/admin">概览</a><a href="/admin/users">用户</a><a href="/admin/tasks">任务</a><a href="/admin/audit">审计</a></nav>{% endblock %}
  ```
- [ ] **Step 4: Run role and existing admin tests.** `.venv/bin/python -m pytest tests/web/test_web_workspaces.py tests/web/test_auth_routes.py tests/web/test_admin_routes.py -q`; expect all pass after updating assertions that intentionally depended on `/profile` login destination.
- [ ] **Step 5: Commit.** `git add jlu_booking/web/routes jlu_booking/web/templates tests/web && git commit -m 'feat(web): route users and admins to distinct workspaces'`.

### Task 2: Read-only availability service and scoped endpoint

**Files:** Create `jlu_booking/web/availability.py`, `jlu_booking/web/routes/availability.py`, `tests/web/test_availability.py`; modify `jlu_booking/web/app.py`, `jlu_booking/web/routes/dashboard.py`.

**Interfaces:** `AvailabilityService(credentials, throttles, *, query_func=query_courts, slots_func=extract_available_slots)` exposes `query(user_id: int, venue: str, sport: str, target_day: str, now: datetime) -> AvailabilityResult`. The result contains `venue`, `sport`, `query_date`, `queried_at`, and sanitized `slots`. `ALLOWED_SLOT_KEYS = ("court_name", "court_id", "place_short_name", "start", "end")` is the allowlist. `POST /availability/query` accepts CSRF and form fields, runs service via `anyio.to_thread.run_sync`, and renders the same user dashboard with results or a safe error.

- [ ] **Step 1: Write failing service/route tests.** With injected `query_func` recording `query_date`, `shop_num`, `sport_short_name`, and Token, test today/tomorrow at a Beijing date boundary; unsupported sport for a venue raises `ValueError` before the fake is invoked. With two active users, assert separate decrypted Tokens and no cross-user slots. Call the route as a pending, disabled, and administrator account and assert it never queries; test CSRF and per-user limit of six queries per minute. Fake HTTP 429, invalid Token, and a generic school error containing a marker Token; assert distinct safe user messages, no marker or raw response, and no retry by the web query route.

  ```python
  result = service.query(alice.id, "前卫体育馆", "羽毛球", "tomorrow", utc_now)
  assert result.query_date == "2026-09-24"
  assert captured["token"] == "alice-token"
  with pytest.raises(ValueError):
      service.query(alice.id, "前卫体育馆", "排球", "today", utc_now)
  assert len(calls) == 1
  ```
- [ ] **Step 2: Run focused tests.** `.venv/bin/python -m pytest tests/web/test_availability.py -q`; expect missing module/route failures.
- [ ] **Step 3: Implement the service.** Resolve `(shop_num, sport_short_name)` using `resolve_venue_sport`, convert `now` to `Asia/Shanghai`, validate `target_day in {"today", "tomorrow"}`, consume a `manual-query:{user_id}` throttle bucket (`limit=6`, `window=timedelta(minutes=1)`), decrypt only this user's Token, call `query_func`, normalize `slots_func(data)` into a small allowlisted dict containing `court_name`, `court_id`, `place_short_name`, `start`, `end`, and return the result. Map explicit 429 and Token-invalid signals to distinct safe messages; map other API/network errors to a generic Chinese message without exception text or retry. Keep database transactions short and outside the blocking school request so concurrent users do not hold a SQLite write lock during network waits.

  ```python
  shop_num, short_name = resolve_venue_sport(venue, sport)
  local = require_aware(now).astimezone(ZoneInfo("Asia/Shanghai"))
  query_date = (local.date() + timedelta(days=target_day == "tomorrow")).isoformat()
  token = self._credentials.decrypt_token(user_id)
  data = self._query_func(query_date, short_name, shop_num, token)
  slots = tuple({key: slot[key] for key in ALLOWED_SLOT_KEYS} for slot in self._slots_func(data))
  ```
- [ ] **Step 4: Wire a request-scoped endpoint.** Add optional `availability` field at the end of `AppServices` so positional test fixtures remain valid, instantiate it in `create_app` when absent, and include the new router. Use `require_active_user` plus explicit `user.role == "user" and user.status == "active"`, `require_csrf`, and `to_thread.run_sync`; render through one dashboard-context helper that works for both GET and POST.
- [ ] **Step 5: Run tests and commit.** `.venv/bin/python -m pytest tests/web/test_availability.py tests/web/test_task_routes.py -q`; then `git add jlu_booking/web tests/web && git commit -m 'feat(web): add user-scoped live availability query'`.

### Task 3: GUI-based responsive user and administrator UI

**Files:** Modify `jlu_booking/web/templates/dashboard.html`, `jlu_booking/web/templates/admin/dashboard.html`, `jlu_booking/web/templates/user_shell.html`, `jlu_booking/web/templates/admin/shell.html`, `jlu_booking/web/static/app.css`, `jlu_booking/web/static/app.js`, `jlu_booking/web/routes/dashboard.py`; test `tests/web/test_web_workspaces.py`, `tests/web/test_availability.py`.

**Interfaces:** User dashboard form posts venue, sport, `target_day`, and CSRF to `/availability/query`; result slots carry structured `data-*` fields for the later manual-booking plan. A native `details`/`summary` navigation remains usable on mobile without JavaScript. Administrator dashboard links to the existing user, task, and audit pages.

- [ ] **Step 1: Write failing markup tests.** Assert user dashboard contains all names from `VENUES`, each venue's sport mapping as machine-readable JSON or option `data-venue`, today/tomorrow inputs, `data-query-results`, query timestamp, grouped court names and slot times from a fake result, an empty-state message, and the current one-shot task link. Assert admin dashboard contains user/task/audit navigation and no query form. Assert CSS has a responsive breakpoint and no fixed width wider than the viewport; inspect a 390px and desktop render using the browser preview without a real API call.

  ```python
  html = user_client.get("/").text
  assert all(name in html for name in VENUES)
  assert 'name="target_day"' in html and 'data-query-results' in html
  assert 'data-user-nav' in html and 'data-admin-nav' not in html
  assert 'data-admin-nav' in admin_client.get("/admin").text
  ```
- [ ] **Step 2: Run the tests.** `.venv/bin/python -m pytest tests/web/test_web_workspaces.py tests/web/test_availability.py -q`; expect new markup assertions to fail.
- [ ] **Step 3: Implement the templates and styles.** Reproduce the GUI's blue venue/function rail and card hierarchy, not its Tk window chrome. Use `<button>`/`<label>` and visible focus states. Add JavaScript only to filter sport choices when venue changes and to keep the chosen venue shared between query and automatic sections; the server still validates every value. Group `slots` by `court_name` then start time. Keep masked credential and task status cards accessible, and mobile navigation collapsible.

  ```jinja2
  <form method="post" action="/availability/query">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <select name="venue" data-venue-select>{% for name in venues %}<option value="{{ name }}">{{ name }}</option>{% endfor %}</select>
    <button type="submit">查询可预约场地</button>
  </form>
  <section data-query-results aria-live="polite">{% for court, slots in grouped_slots.items() %}<h3>{{ court }}</h3>{% endfor %}</section>
  ```
- [ ] **Step 4: Verify and commit.** Run `.venv/bin/python -m pytest tests/web/test_web_workspaces.py tests/web/test_availability.py tests/web/test_web_smoke.py -q`, `.venv/bin/python tools/privacy_check.py`, and `git diff --check`; fix any changed login/smoke expectations; commit `git add jlu_booking/web tests/web && git commit -m 'feat(web): adapt GUI workspace for browsers'`.

## Handoff

This plan leaves automatic task creation unchanged. Execute the daily-booking plan next, then the manual-booking plan. After all three plans, run the full offline suite, privacy check, compileall, and diff check together.
