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
