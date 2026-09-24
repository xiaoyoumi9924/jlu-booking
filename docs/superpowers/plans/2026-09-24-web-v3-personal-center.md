# Web V3 Personal Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `/profile` into a GUI-styled personal center for Token/companion settings and the signed-in user's automatic and manual booking history.

**Architecture:** Query existing SQLite task and manual-attempt tables through a small read-only history module. Keep Token replacement and companion verification on their current POST routes. Link history rows to the already owner-checked task detail and manual result pages; do not create a second log store or new booking path.

**Tech Stack:** Python 3.10+, SQLite, FastAPI, Jinja2, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-web-v3-gui-visual-design.md`. Execute after `docs/superpowers/plans/2026-09-24-web-v3-gui-workspace.md`.

## Global Constraints

- History contains only the signed-in Web user's automatic tasks and submitted manual attempts; legacy CLI script logs are outside this scope.
- No database migration or change to task creation, manual submission, school API, CORE timing, scheduler, or account isolation.
- Never render a full Token, student number, encrypted value, raw school response, or another user's record.
- Automatic log detail continues to use the existing owner-checked `/tasks/{task_id}/status` with safe runtime-path and redaction rules; manual detail continues to use `/manual/result/{attempt_id}`.
- Only submitted manual attempts appear; `prechecked` means no booking was submitted and is excluded.
- All tests use disposable accounts, temporary SQLite, and fake school functions; no live booking or external request.

## Review Focus

1. Alice requests Bob's task/result URL: both still return 404; Task 2 test covers this.
2. A manual attempt remains `prechecked`: no history row suggests a real booking; Task 1 test covers this.
3. More than 20 records: page 1 and page 2 contain disjoint rows in stable order; Task 1 test covers this.
4. Auto run log file is missing or empty: task status remains visible and the page says logs are unavailable rather than implying failure; Task 2 test covers this.
5. Invalid Token replacement or companion verification: the profile form still shows its error and does not expose or overwrite the previous credential; Task 2 existing-route regression covers this.

---

### Task 1: Read-only, owner-scoped booking history

**Files:**
- Create: `jlu_booking/web/profile_history.py`
- Create: `tests/web/test_profile_history.py`

**Interfaces:**
- Consumes: SQLite `booking_tasks`, `manual_booking_attempts`, `manual_candidates`; all rows have `user_id` and ISO timestamp text.
- Produces: `BookingHistoryItem(kind: str, record_id: str, occurred_at: str, target_date: str, venue: str, sport: str, status: str, source: str)`, `BookingHistoryPage(items: tuple[BookingHistoryItem, ...], page: int, has_next: bool)`, and `list_history(connection, user_id: int, *, page: int = 1, page_size: int = 20) -> BookingHistoryPage`.

- [ ] **Step 1: Write the failing service tests.** In a temporary migrated SQLite database with two registered users and verified companions, create one automatic task for Alice through `TaskService.create`, one candidate/precheck through the fake `ManualBookingService`, and submit that attempt through the fake booking function. Check that Alice sees those two rows, Bob sees none, and a second unsubmitted precheck does not appear. Add 25 cancelled tasks for Alice before the manual success, then assert page 1 has 20 rows, page 2 has the remainder, and IDs do not overlap.

```python
from jlu_booking.web.profile_history import list_history

alice_page = list_history(connection, alice_id)
bob_page = list_history(connection, bob_id)
assert {item.kind for item in alice_page.items} == {"auto", "manual"}
assert bob_page.items == ()
assert all(item.status != "prechecked" for item in alice_page.items)
assert len(list_history(connection, alice_id, page=1).items) == 20
assert set(x.record_id for x in list_history(connection, alice_id, page=1).items).isdisjoint(
    x.record_id for x in list_history(connection, alice_id, page=2).items
)
```

Use the existing fake-API fixture pattern from `tests/web/test_manual_booking_routes.py`; no test calls the real school endpoint. Test `page=0` and `page_size=0` raise `ValueError` rather than creating a negative offset or unbounded query.
- [ ] **Step 2: Run the focused test and observe failure.** Run `.venv/bin/python -m pytest tests/web/test_profile_history.py -q`; expect an import failure for `profile_history`.
- [ ] **Step 3: Implement the small read model.** Use a parameterized `UNION ALL` over auto tasks and manual attempts joined to their candidates. Filter both arms by `user_id`, exclude manual `prechecked`, sort by `occurred_at DESC, record_id DESC`, and fetch `page_size + 1` rows to derive `has_next`. For auto, compute target date from execution date plus `target_day`; for manual, take `manual_candidates.query_date`. Use `created_at` for auto and `updated_at` for manual. Do not select Token, companion number, runtime paths or raw response fields.

```sql
SELECT 'auto' AS kind, CAST(t.id AS TEXT) AS record_id,
       t.created_at AS occurred_at, t.execution_date AS execution_date,
       t.target_day, t.venue, t.sport, t.status, t.source
FROM booking_tasks AS t WHERE t.user_id = ?
UNION ALL
SELECT 'manual', a.id, a.updated_at, c.query_date,
       'today', c.venue, c.sport, a.status, 'manual'
FROM manual_booking_attempts AS a
JOIN manual_candidates AS c ON c.id = a.candidate_id
WHERE a.user_id = ? AND a.status != 'prechecked'
ORDER BY occurred_at DESC, record_id DESC LIMIT ? OFFSET ?
```

Map auto `target_day='tomorrow'` to the next Beijing calendar date in Python using `date.fromisoformat`; manual's `execution_date` placeholder column is already the target date. Keep `page_size` bounded to at most 50 to avoid unbounded reads.
- [ ] **Step 4: Verify filtering and pagination.** Run `.venv/bin/python -m pytest tests/web/test_profile_history.py -q`; expect all service tests to pass.
- [ ] **Step 5: Commit the read model.** `git add jlu_booking/web/profile_history.py tests/web/test_profile_history.py && git commit -m 'feat(web): list owned booking history'`.

### Task 2: Personal-center page and log links

**Files:**
- Modify: `jlu_booking/web/routes/profile.py`
- Modify: `jlu_booking/web/templates/profile.html`
- Modify: `jlu_booking/web/routes/task_routes.py`
- Modify: `jlu_booking/web/templates/task_detail.html`
- Modify: `jlu_booking/web/static/app.js`
- Modify: `jlu_booking/web/static/app.css`
- Test: `tests/web/test_auth_routes.py`
- Test: `tests/web/test_manual_booking_routes.py`
- Test: `tests/web/test_web_workspaces.py`

**Interfaces:**
- Consumes: Task 1's `list_history(connection, user_id, *, page, page_size=20)` and the GUI shell from the workspace plan.
- Produces: GET `/profile?page=N` context key `history_page`; all current `/profile/token` and `/profile/companion` POST fields/routes remain unchanged.

- [ ] **Step 1: Write failing route tests.** After creating two users' auto and manual records via fake services, log in as Alice and assert `/profile` shows only Alice's links and a masked Token. Log in as Bob and confirm Alice's links disappear; direct requests to Alice's `/tasks/{id}` and `/manual/result/{attempt_id}` remain 404. Assert the `page=2` link is present when there are more than 20 records, and that a bad token POST still displays its error plus the history section without leaking the Token.

```python
page = client.get("/profile")
assert "个人中心" in page.text
assert "我的预约记录" in page.text
assert f'href="/tasks/{alice_task_id}"' in page.text
assert f'href="/manual/result/{alice_attempt_id}"' in page.text
assert "private-token-alice" not in page.text
assert client.get(f"/tasks/{bob_task_id}").status_code == 404
assert client.get(f"/manual/result/{bob_attempt_id}").status_code == 404
```

Add a terminal task with no log file and assert `/tasks/{id}` keeps its task status while displaying “日志暂不可用”. Assert an admin GET `/profile` redirects to `/admin`, and a disabled user cannot load history. Test a valid existing Token-replacement and companion-save path from `tests/web/test_auth_routes.py` still works.
- [ ] **Step 2: Run the focused tests and observe failure.** Run `.venv/bin/python -m pytest tests/web/test_auth_routes.py tests/web/test_manual_booking_routes.py tests/web/test_web_workspaces.py -q`; expect new history and unavailable-log assertions to fail.
- [ ] **Step 3: Connect read-only data and render it.** Add `page: int = Query(1, ge=1)` to GET `/profile`, call `list_history` from `_profile_context`, and pass `history_page` to the template on normal and form-error renderings. Extend the existing profile guard so only an active ordinary user reaches the personal-center read and credential forms; an admin GET redirects to `/admin`, while unauthorized POST remains rejected. Keep the current Token and companion validation/save services untouched. Rename the page title to “个人中心”; render distinct credential, companion and record sections. Use existing owner-checked URLs for details, status labels such as `scheduled→已排程`, `running→运行中`, `success→预约成功`, `rejected→未接受`, `unknown→结果不明`, `cancelled→已取消` (other statuses keep safe existing copy), source labels `daily→每日自动`, `one_shot→一次性自动`, `manual→手动`, prev/next links preserving only the page number, and an empty state. On task detail, detect a terminal task with no private log lines and show “日志暂不可用”; in `app.js`, keep that message after polling returns an empty log list.

```python
history_page = list_history(request.app.state.services.connection, user.id, page=page)
context["history_page"] = history_page
```

The `list_history` query must never run for anonymous, pending-token, disabled or admin sessions; add the missing active-user and role checks before calling it. Do not expose log paths in HTML.
- [ ] **Step 4: Verify behavior and safety.** Run `.venv/bin/python -m pytest tests/web/test_profile_history.py tests/web/test_auth_routes.py tests/web/test_manual_booking_routes.py tests/web/test_web_workspaces.py tests/web/test_web_smoke.py -q`; expect all to pass. Visually inspect `/profile` at desktop and phone widths under the GUI shell.
- [ ] **Step 5: Commit and run the final project checks.** `git add jlu_booking/web/routes/profile.py jlu_booking/web/templates/profile.html jlu_booking/web/routes/task_routes.py jlu_booking/web/templates/task_detail.html jlu_booking/web/static/app.js jlu_booking/web/static/app.css tests/web && git commit -m 'feat(web): add personal center booking history'`. Then run `.venv/bin/python -m pytest`, `.venv/bin/python tools/privacy_check.py`, `.venv/bin/python -m compileall -q jlu_booking`, and `git diff --check`. Report exact outputs before considering GitHub or server updates.
