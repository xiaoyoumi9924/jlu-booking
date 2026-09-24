# Web V4 Workspaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver GUI-faithful user navigation and booking forms plus redesigned daily, profile, and admin workspaces.

**Architecture:** Keep the FastAPI routes, database, scheduling, and authorization intact. Compose the existing Jinja templates into a consistent user shell and a separate admin shell. Use progressive enhancement for chip selectors so native selects remain functional without JavaScript.

**Tech Stack:** Python, FastAPI, Jinja, CSS, browser JavaScript, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-web-v4-workspaces.md`

## Global Constraints

- No real JLU requests; use isolated TestClient fixtures and browser previews with fake data.
- Preserve CSRF, role isolation, token masking, time-priority order, daily enable/disable semantics, and all existing booking controls.
- Desktop follows `gui.py`; mobile remains responsive with no page-level horizontal overflow.

## Review Focus

- JavaScript off: each form still submits venue, compatible sport, day, priority, and mode.
- Switching venues: incompatible sport cannot be submitted and the visible sport selection updates.
- Editing a task: existing priority order and selected values remain visible.
- Daily plan: saving configuration does not silently enable it; disable keeps existing semantics.
- Admin: user pages and sensitive token reveal remain role restricted and never display raw secrets by default.

---

### Task 1: Navigation and single-task form

**Files:** `jlu_booking/web/templates/user_shell.html`, `jlu_booking/web/templates/task_form.html`, `jlu_booking/web/static/app.css`, `jlu_booking/web/static/app.js`, `tests/web/test_web_workspaces.py`, `tests/web/frontend_interactions.js`.

- [ ] Add failing DOM/route tests for four navigation links, no venue sidebar, and GUI-form controls with unchanged submission field names.
- [ ] Run targeted tests; confirm expected failures.
- [ ] Implement the new sidebar and booking panel, progressive selectors, and responsive styles.
- [ ] Run targeted and existing task-route tests; fix compatibility failures.
- [ ] Commit the tested change.

### Task 2: Daily booking and personal center

**Files:** `jlu_booking/web/templates/daily_plan.html`, `jlu_booking/web/templates/profile.html`, `jlu_booking/web/static/app.css`, `tests/web/test_web_workspaces.py`, `tests/web/test_daily_plan_routes.py`, `tests/web/test_profile_history.py`.

- [ ] Add failing tests for status-first daily flow, save/enable/disable forms, masked credentials, and owned record links.
- [ ] Run targeted tests; confirm expected failures.
- [ ] Implement daily and personal-center layout without changing route behavior.
- [ ] Run targeted tests and verify no raw credential values in HTML.
- [ ] Commit the tested change.

### Task 3: Administrator console

**Files:** `jlu_booking/web/templates/admin/*.html`, `jlu_booking/web/static/app.css`, `tests/web/test_admin_routes.py`, `tests/web/test_web_workspaces.py`.

- [ ] Add failing tests for independent admin shell, overview, tables, status and action forms.
- [ ] Run targeted tests; confirm expected failures.
- [ ] Implement cohesive admin layout and responsive table containers, retaining the token reveal gate and stop confirmation.
- [ ] Run targeted tests and role-isolation suite.
- [ ] Commit the tested change.

### Task 4: Visual and full verification

**Files:** Any V4 stylesheet/template files needing visually observed fixes.

- [ ] Review desktop and 390px mobile screenshots with isolated fake users, no real school API.
- [ ] Fix layout defects and run affected tests.
- [ ] Run `python -m pytest`, `python tools/privacy_check.py`, `python -m compileall -q jlu_booking`, and `git diff --check`.
- [ ] Commit final corrections and summarize changed files and any deployment boundary.
