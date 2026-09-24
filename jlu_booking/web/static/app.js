"use strict";
document.documentElement.classList.add("js-ready");
if (window.matchMedia("(max-width: 900px)").matches) {
  document.querySelector("[data-user-nav]")?.removeAttribute("open");
}
window.JLUBooking = Object.freeze({
  isPageVisible: () => document.visibilityState === "visible",
  setText: (element, value) => { if (element) element.textContent = String(value); }
});

const terminalStates = new Set(["success", "no_result", "token_invalid", "account_blocked", "daily_limit", "submission_unknown", "network_unavailable", "stopped", "error", "cancelled"]);
const taskRoot = document.querySelector("[data-task-status-url]");
let taskTimer = null;
async function refreshTask() {
  if (!taskRoot || document.visibilityState !== "visible") return;
  const response = await fetch(taskRoot.dataset.taskStatusUrl, {headers: {"Accept": "application/json"}});
  if (!response.ok) return;
  const payload = await response.json();
  window.JLUBooking.setText(document.querySelector("[data-task-status]"), payload.status);
  window.JLUBooking.setText(document.querySelector("[data-task-phase]"), payload.phase || "尚未开始");
  window.JLUBooking.setText(document.querySelector("[data-log-region]"),
    payload.log_lines.length ? payload.log_lines.join("\n") :
      (terminalStates.has(payload.status) ? "日志暂不可用" : "等待日志…"));
  if (terminalStates.has(payload.status) && taskTimer) { clearInterval(taskTimer); taskTimer = null; }
}
function configurePolling() {
  if (!taskRoot || document.visibilityState !== "visible" || taskTimer) return;
  refreshTask();
  taskTimer = setInterval(refreshTask, Number(taskRoot.dataset.pollMs || 3000));
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && taskTimer) { clearInterval(taskTimer); taskTimer = null; }
  else configurePolling();
});
configurePolling();

for (const form of document.querySelectorAll("[data-confirm-stop]")) {
  form.addEventListener("submit", (event) => {
    const message = "停止任务不代表预约一定未提交。请确认已理解，并在学校系统核对最终结果。";
    if (!window.confirm(message)) event.preventDefault();
  });
}

const revealedOutputs = new Set();
function clearRevealedTokens() {
  for (const output of revealedOutputs) output.textContent = "";
  revealedOutputs.clear();
}
for (const button of document.querySelectorAll("[data-token-reveal]")) {
  button.addEventListener("click", async () => {
    const body = new URLSearchParams({csrf_token: button.dataset.csrf});
    const response = await fetch(button.dataset.tokenReveal, {method: "POST", body});
    if (!response.ok) return;
    const payload = await response.json();
    const userId = button.dataset.tokenReveal.split("/").at(-3);
    const output = document.querySelector(`[data-token-output="${userId}"]`);
    window.JLUBooking.setText(output, payload.token);
    revealedOutputs.add(output);
    setTimeout(() => { if (output) output.textContent = ""; revealedOutputs.delete(output); }, Number(payload.hide_after) * 1000);
  });
}
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") clearRevealedTokens(); });
window.addEventListener("pagehide", clearRevealedTokens);

for (const button of document.querySelectorAll("[data-priority-up], [data-priority-down]")) {
  button.addEventListener("click", () => {
    const row = button.closest(".priority-row");
    if (!row) return;
    if (button.hasAttribute("data-priority-up") && row.previousElementSibling) row.parentElement.insertBefore(row, row.previousElementSibling);
    if (button.hasAttribute("data-priority-down") && row.nextElementSibling) row.parentElement.insertBefore(row.nextElementSibling, row);
  });
}

const targetDay = document.querySelector("[data-target-day]");
const targetOutput = document.querySelector("[data-target-date]");
const summary = document.querySelector("[data-execution-date]");
function updateTargetDate() {
  if (!targetDay || !targetOutput || !summary) return;
  const parts = summary.dataset.executionDate.split("-").map(Number);
  const date = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2] + (targetDay.value === "tomorrow" ? 1 : 0)));
  targetOutput.textContent = date.toISOString().slice(0, 10);
}
if (targetDay) targetDay.addEventListener("change", updateTargetDate);
updateTargetDate();

const dashboardRoot = document.querySelector("[data-dashboard-poll]");
let dashboardTimer = null;
function configureDashboardPolling() {
  if (!dashboardRoot || document.visibilityState !== "visible" || dashboardTimer) return;
  dashboardTimer = setInterval(() => { if (document.visibilityState === "visible") window.location.reload(); }, Number(dashboardRoot.dataset.dashboardPoll || 15000));
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && dashboardTimer) { clearInterval(dashboardTimer); dashboardTimer = null; }
  else configureDashboardPolling();
});
configureDashboardPolling();

// Keep the GUI's venue choice consistent between live query and new auto tasks.
function syncVenueCards(venue) {
  for (const card of document.querySelectorAll("[data-set-venue]")) {
    const active = card.dataset.setVenue === venue;
    card.classList.toggle("is-active", active);
    if (active) card.setAttribute("aria-current", "true");
    else card.removeAttribute("aria-current");
    const icon = card.querySelector(".workspace-nav-icon");
    const description = card.querySelector(".workspace-venue-copy small");
    if (icon) icon.textContent = active ? "✓" : "馆";
    if (description) description.textContent = active ? "✓ 当前选中" : "点击切换到此场馆";
  }
}
for (const card of document.querySelectorAll("[data-set-venue]")) {
  card.addEventListener("click", () => {
    localStorage.setItem("jlu-preferred-venue", card.dataset.setVenue);
    const venueSelect = document.querySelector("[data-venue-select]");
    if (venueSelect && [...venueSelect.options].some((option) => option.value === card.dataset.setVenue)) {
      venueSelect.value = card.dataset.setVenue;
      venueSelect.dispatchEvent(new Event("change", {bubbles: true}));
    }
  });
}
for (const venueSelect of document.querySelectorAll("[data-venue-select]")) {
  const sportSelect = venueSelect.form?.querySelector("[data-sport-select]");
  if (!sportSelect) continue;
  if (window.location.pathname === "/tasks/new") {
    const remembered = localStorage.getItem("jlu-preferred-venue");
    if (remembered && [...venueSelect.options].some((option) => option.value === remembered)) {
      venueSelect.value = remembered;
    }
  }
  function syncSports() {
    const compatible = [...sportSelect.options].filter((option) => option.dataset.venue === venueSelect.value);
    for (const option of sportSelect.options) {
      option.hidden = option.dataset.venue !== venueSelect.value;
      option.disabled = option.hidden;
    }
    if (!compatible.some((option) => option.selected) && compatible.length) compatible[0].selected = true;
    for (const chip of venueSelect.form.querySelectorAll("[data-select-sport]")) {
      chip.hidden = !compatible.some((option) => option.value === chip.dataset.selectSport);
      chip.setAttribute("aria-pressed", String(chip.dataset.selectSport === sportSelect.value));
    }
    syncVenueCards(venueSelect.value);
  }
  venueSelect.addEventListener("change", () => {
    localStorage.setItem("jlu-preferred-venue", venueSelect.value);
    syncSports();
  });
  syncSports();
}

const sportSelect = document.querySelector("[data-sport-select]");
const daySelect = document.querySelector("[data-day-select]");
for (const chip of document.querySelectorAll("[data-select-sport]")) {
  chip.addEventListener("click", () => {
    if (!sportSelect) return;
    sportSelect.value = chip.dataset.selectSport;
    for (const peer of document.querySelectorAll("[data-select-sport]")) {
      peer.setAttribute("aria-pressed", String(peer === chip));
    }
  });
}
for (const chip of document.querySelectorAll("[data-select-day]")) {
  chip.addEventListener("click", () => {
    if (!daySelect) return;
    daySelect.value = chip.dataset.selectDay;
    for (const peer of document.querySelectorAll("[data-select-day]")) {
      peer.setAttribute("aria-pressed", String(peer === chip));
    }
  });
}
for (const button of document.querySelectorAll("[data-clear-results]")) {
  button.addEventListener("click", () => {
    const body = document.querySelector("[data-results-body]");
    if (body) {
      body.replaceChildren();
      const empty = document.createElement("p");
      empty.className = "empty-state";
      const icon = document.createElement("span"); icon.className = "empty-symbol"; icon.textContent = "◎";
      const heading = document.createElement("strong"); heading.textContent = "等待查询";
      const hint = document.createElement("span"); hint.textContent = "选择运动项目和日期，点击查询即可查看可预约时段";
      empty.append(icon, heading, hint);
      body.append(empty);
    }
    for (const key of ["sport", "date"]) window.JLUBooking.setText(document.querySelector(`[data-summary-${key}]`), "未查询");
    for (const key of ["court", "slot"]) {
      const count = document.querySelector(`[data-${key}-count]`);
      if (count) count.dataset[`${key}Count`] = "0";
      window.JLUBooking.setText(document.querySelector(`[data-${key}-number]`), 0);
    }
    window.JLUBooking.setText(document.querySelector("[data-results-status]"), "准备就绪");
    window.JLUBooking.setText(document.querySelector("[data-results-caption]"), "查询后将在这里展示空闲时段");
  });
}
