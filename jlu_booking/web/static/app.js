"use strict";
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
  window.JLUBooking.setText(document.querySelector("[data-log-region]"), payload.log_lines.join("\n"));
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
