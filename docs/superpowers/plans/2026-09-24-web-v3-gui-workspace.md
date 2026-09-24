# Web V3 GUI Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the ordinary-user browser workspace closely match the supplied `gui.py` screenshot at desktop size while preserving every existing query and booking path.

**Architecture:** Keep FastAPI/Jinja and the present form endpoints. Give user pages a shared GUI-style shell, rebuild the query page with server-backed controls and statistics, then apply the same visual components to the remaining user pages. Admin and public authentication pages keep their own shell.

**Tech Stack:** Python 3.10+, FastAPI, Jinja2, plain CSS/JavaScript, pytest, local Chrome for visual QA.

**Spec:** `docs/superpowers/specs/2026-09-24-web-v3-gui-visual-design.md`; visual reference: `docs/superpowers/specs/assets/web-v3-gui-reference.png`.

## Global Constraints

- Keep `jlu_booking/auto.py`, scheduler behavior, 07:29:57–07:33 CORE, database schema, school API calls, and booking confirmation semantics unchanged.
- No tests may contact the real school interface or submit a real booking.
- Use `api.VENUES` for venue/sport choices and server validation; the screenshot's text is not a data source.
- Preserve CSRF, role isolation, user-owned Token, and existing manual confirmation flow.
- Query desktop layout must closely match the 2324×1642 reference after excluding the Tk window title bar; mobile stays usable without horizontal scrolling.
- Develop locally; deployment may use only a committed, tested GitHub revision and must not touch unrelated server scripts.

## Review Focus

1. Venue changed to one without the previously selected sport: the form selects a valid sport for the new venue; Task 2 test covers this.
2. JavaScript disabled: venue, sport, and date remain selectable and queryable; Task 2 test and markup review cover this.
3. Query error or stale response: summary does not show stale counts or imply a fresh success; Task 2 test covers this.
4. A user on a narrow screen: sidebar expands, buttons wrap, and no horizontal page overflow occurs; Task 3 viewport check covers this.
5. Admin visits its workspace: user GUI shell never appears and admin links still work; Task 1 and Task 3 tests cover this.

---

### Task 1: GUI-style shared user shell

**Files:**
- Modify: `jlu_booking/web/templates/base.html`
- Modify: `jlu_booking/web/templates/user_shell.html`
- Modify: `jlu_booking/web/app.py`
- Modify: `jlu_booking/web/static/app.css`
- Test: `tests/web/test_web_workspaces.py`

**Interfaces:**
- Consumes: `api.VENUES`, `request.url.path`, the current template `user` object, and the existing `selected_venue` context on query pages.
- Produces: Jinja global `user_venues`; `base.html` blocks `header` and `content_header`; user-only classes `user-workspace`, `workspace-nav`, `workspace-head` for later tasks.

- [ ] **Step 1: Write failing shell tests.** Extend `test_workspace_shows_gui_venue_choices_and_responsive_navigation` and add an admin assertion:

```python
html = user_client.get("/").text
assert 'class="workspace-brand"' in html
assert "JILIN UNIVERSITY" in html
assert "服务场馆" in html
assert 'data-user-nav' in html and 'data-admin-nav' not in html
assert 'class="topbar"' not in html
assert 'class="workspace-head"' in html
assert 'action="/logout"' in html
assert 'class="workspace-brand"' not in admin_client.get("/admin").text
```

- [ ] **Step 2: Run the focused test and observe failure.** Run `.venv/bin/python -m pytest tests/web/test_web_workspaces.py -q`; expect missing GUI brand/head assertions to fail.
- [ ] **Step 3: Implement the shell.** Add overridable header/content-header blocks to `base.html`; override the header to empty in `user_shell.html`. Define nested `workspace_kicker` and `workspace_title` blocks in the shared `content_header`, defaulting to `体育场馆` and `场地预约查询`; Task 3 supplies each other page's text. Render the existing logo and `user_venues` as a branded sidebar with two primary navigation cards, auxiliary daily-plan/profile links, venue cards, GUI's footer text, a clear personal-center entry and a CSRF-protected logout form. Give venue cards real `href="/?venue={{ name|urlencode }}"` links so navigation works without JavaScript. Keep `data-user-nav` and a `<details>/<summary>` mobile navigation wrapper. In `create_app`, set `app.state.templates.env.globals["user_venues"] = VENUES` after template construction. Scope desktop styles to `.user-workspace`/`.workspace-nav` so admin and auth retain their existing shell. Use `gui.py` color values and a 300–320px desktop rail; page status copy describes only actual Web state, never unverified school availability.

```python
from ..api import VENUES
app.state.templates.env.globals["user_venues"] = VENUES
```

- [ ] **Step 4: Verify role and navigation behavior.** Run `.venv/bin/python -m pytest tests/web/test_web_workspaces.py tests/web/test_admin_routes.py -q`; expect both suites to pass.
- [ ] **Step 5: Commit the independently reviewable shell.** `git add jlu_booking/web/app.py jlu_booking/web/templates/base.html jlu_booking/web/templates/user_shell.html jlu_booking/web/static/app.css tests/web/test_web_workspaces.py && git commit -m 'feat(web): add GUI-style user workspace shell'`.

### Task 2: High-fidelity venue query page

**Files:**
- Modify: `jlu_booking/web/routes/dashboard.py`
- Modify: `jlu_booking/web/routes/availability.py`
- Modify: `jlu_booking/web/templates/dashboard.html`
- Modify: `jlu_booking/web/static/app.css`
- Modify: `jlu_booking/web/static/app.js`
- Test: `tests/web/test_web_workspaces.py`
- Test: `tests/web/test_availability.py`

**Interfaces:**
- Consumes: Task 1's shell, `dashboard_context(request, session, user, *, result=None, error=None, selection=None)` where `selection` is a `(venue, sport, target_day)` tuple from the POST form, `AvailabilityResult.slots`, and existing `/availability/query` POST fields (`venue`, `sport`, `target_day`, `csrf_token`).
- Produces: query context values `selected_venue`, `selected_sport`, `selected_day`, `today_label`, `tomorrow_label`, `court_count`, `slot_count`; DOM targets `data-query-results`, `data-set-venue`, `data-clear-results`, `data-court-count`, and `data-slot-count`.

- [ ] **Step 1: Write failing query and edge-case tests.** Add tests for a valid `/?venue=宋治平体育馆` selection, bad venue fallback, button-style choices with real select fields, four summaries, no stale counts on query error, and a fake result with two courts/three slots:

```python
page = client.get("/?venue=宋治平体育馆")
assert page.status_code == 200
assert 'name="venue"' in page.text and '宋治平体育馆' in page.text
assert 'name="target_day"' in page.text
assert 'value="today"' in page.text and 'value="tomorrow"' in page.text
assert 'data-select-sport' in page.text and 'data-select-day' in page.text
assert 'data-clear-results' in page.text
assert client.get("/?venue=invalid").status_code == 200
assert '前卫体育馆' in client.get("/?venue=invalid").text
```

Also exercise the existing fake `AvailabilityService`: set `services.availability.query` to return an `AvailabilityResult("前卫体育馆", "羽毛球", "2026-09-23", NOW, slots)` where `slots` has two different `court_name` values and three entries; submit a valid query and assert `data-court-count="2"`, `data-slot-count="3"`, then make the fake raise `ValueError("query failed")` and assert both attributes are `"0"`. Ensure the selected sport belongs to the selected venue. Assert venue/sport/date `<select>` controls remain in ordinary HTML for no-JavaScript use.

- [ ] **Step 2: Run focused tests and observe failure.** Run `.venv/bin/python -m pytest tests/web/test_web_workspaces.py tests/web/test_availability.py -q`; expect the new markup/context assertions to fail.
- [ ] **Step 3: Implement query context and markup.** Add optional `selection: tuple[str, str, str] | None` to `dashboard_context`; pass `(venue, sport, target_day)` from `availability.query_availability` on both success and error. Validate `request.query_params.get("venue")` against `VENUES` on GET; let a fresh result take precedence. Build Beijing today/tomorrow labels in `dashboard_context`. Derive counts from `result.slots` and grouped court names; on errors return empty counts and keep the attempted venue/sport/day only if valid. Keep the single existing venue/sport/date `<select name=...>` controls as the real submitted fields and no-JavaScript fallback. JavaScript adds `js-ready` to `<html>`; only then CSS hides fallback selects and shows GUI-style `type=button` chips (`data-select-sport`, `data-select-day`) that synchronize those selects. Avoid duplicate submitted names. Clicking a sidebar venue link stores the preference before navigation; existing `/tasks/new` selection reads it. Render GUI's query card, four summary cards, and large results card in that order; move Token/task cards to their existing dedicated destinations. Keep each real slot's existing `/manual/precheck` form and CSRF token intact.

```python
selected_venue = result.venue if result else requested_venue if requested_venue in VENUES else next(iter(VENUES))
court_count = len({str(slot["court_name"]) for slot in result.slots}) if result else 0
slot_count = len(result.slots) if result else 0
```

The code should use explicit intermediate statements if the ternary harms readability. Add `data-clear-results` handling that clears only the current page's rendered result and resets the summary, with no fetch or booking call. Keep valid sport selection after venue switches; the server remains authoritative.
- [ ] **Step 4: Verify functionality.** Run `.venv/bin/python -m pytest tests/web/test_web_workspaces.py tests/web/test_availability.py tests/web/test_manual_booking_routes.py -q`; expect all to pass and no network access.
- [ ] **Step 5: Commit the query page.** `git add jlu_booking/web/routes/dashboard.py jlu_booking/web/routes/availability.py jlu_booking/web/templates/dashboard.html jlu_booking/web/static/app.css jlu_booking/web/static/app.js tests/web && git commit -m 'feat(web): match GUI venue query layout'`.

### Task 3: Consistent user pages and visual acceptance

**Files:**
- Modify: `jlu_booking/web/templates/task_form.html`
- Modify: `jlu_booking/web/templates/daily_plan.html`
- Modify: `jlu_booking/web/templates/task_detail.html`
- Modify: `jlu_booking/web/templates/manual_confirm.html`
- Modify: `jlu_booking/web/templates/manual_result.html`
- Modify: `jlu_booking/web/templates/profile.html`
- Modify: `jlu_booking/web/static/app.css`
- Test: `tests/web/test_web_workspaces.py`
- Test: `tests/web/test_web_smoke.py`

**Interfaces:**
- Consumes: Task 1's `content_header`/user shell and Task 2's visual tokens. The profile page will later receive Task 2 of the personal-center plan.
- Produces: consistent user-page headings/cards without changing POST endpoints or form fields.

- [ ] **Step 1: Write failing page consistency tests.** After authenticating a test user, fetch `/`, `/tasks/new`, `/daily-plan`, `/profile`, and existing task/manual detail pages from the fake-data fixtures. Assert every page has the GUI brand, a page-specific `workspace-head`, a personal-center link, and the expected existing form action; assert admin pages have `data-admin-nav` and not `data-user-nav`. Check that the stylesheet contains `@media` rules for tablet and phone widths.

```python
for path in ("/", "/tasks/new", "/daily-plan", "/profile"):
    html = user_client.get(path).text
    assert 'class="workspace-head"' in html
    assert 'href="/profile"' in html
assert 'action="/daily-plan"' in user_client.get("/daily-plan").text
assert 'action="/profile/token"' in user_client.get("/profile").text
```

- [ ] **Step 2: Run focused tests and observe failure.** Run `.venv/bin/python -m pytest tests/web/test_web_workspaces.py tests/web/test_web_smoke.py -q`; expect missing consistent header/classes to fail.
- [ ] **Step 3: Apply shared visual components.** Give each user template a page-specific header via the `content_header` block, remove duplicate in-card hero headings, and use reusable CSS classes for form sections, state labels and actions. Preserve every existing field name, hidden CSRF value, warning, form action, and task polling element. Rename the `/profile` navigation label to “个人中心”; its records are added by the separate personal-center plan. Keep auth/admin CSS selectors isolated from the user-page styles.
- [ ] **Step 4: Run functional and visual checks.** Run `.venv/bin/python -m pytest tests/web/test_web_workspaces.py tests/web/test_web_smoke.py tests/web/test_daily_plan_routes.py tests/web/test_task_routes.py tests/web/test_manual_booking_routes.py -q`. Serve only a local fake-data/test account, render Chrome screenshots at 2324×1642 and 390×844, and compare the desktop page against `docs/superpowers/specs/assets/web-v3-gui-reference.png` after cropping its native title bar. Correct sidebar width, card order/spacing, typography and result-panel height until the main regions align; confirm phone content has no horizontal overflow. Do not use a real Token or live school API for this review.

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --window-size=2324,1642 --screenshot=/tmp/jlu-web-v3-desktop.png http://127.0.0.1:8000/
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --disable-gpu --window-size=390,844 --screenshot=/tmp/jlu-web-v3-phone.png http://127.0.0.1:8000/
```

Use an authenticated disposable local browser profile or a rendered fake-data preview; unauthenticated login screenshots do not satisfy this step.
- [ ] **Step 5: Commit and verify the visual layer.** `git add jlu_booking/web/templates jlu_booking/web/static/app.css tests/web && git commit -m 'feat(web): unify user pages with GUI visuals'`. Then run `.venv/bin/python -m pytest`, `.venv/bin/python tools/privacy_check.py`, `.venv/bin/python -m compileall -q jlu_booking`, and `git diff --check`; report exact results before any merge or deployment.
