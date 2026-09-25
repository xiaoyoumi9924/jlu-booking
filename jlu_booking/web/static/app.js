"use strict";
document.documentElement.classList.add("js-ready");
const compactNavigation = window.matchMedia("(max-width: 900px)");
function syncNavigationSize(event) {
  for (const nav of [document.querySelector("[data-user-nav]"), document.querySelector("[data-admin-nav]")]) {
    if (!nav) continue;
    if (event.matches) nav.removeAttribute("open");
    else nav.setAttribute("open", "");
  }
}
syncNavigationSize(compactNavigation);
compactNavigation.addEventListener?.("change", syncNavigationSize);
window.JLUBooking = Object.freeze({
  isPageVisible: () => document.visibilityState === "visible",
  setText: (element, value) => { if (element) element.textContent = String(value); }
});

const terminalStates = new Set(["success", "no_result", "token_invalid", "account_blocked", "daily_limit", "submission_unknown", "network_unavailable", "stopped", "error", "cancelled"]);
const taskStatusLabels = Object.freeze({scheduled: "等待启动", running: "运行中", success: "预约成功", no_result: "未找到场地", token_invalid: "登录失效", account_blocked: "账号受限", daily_limit: "预约已达上限", submission_unknown: "结果待核对", network_unavailable: "网络不可用", stopped: "已停止", error: "运行出错", cancelled: "已取消"});
const taskStatusMessages = Object.freeze({scheduled: "任务已创建，等待开始。", running: "正在查询并尝试预约；可在右侧查看进度。", success: "预约已成功，请以学校系统中的最终记录为准。", no_result: "本次未找到符合条件的可预约场地。", token_invalid: "JLU Token 已失效，请在个人中心更新。", account_blocked: "学校账号暂时无法预约，请在学校系统核对。", daily_limit: "学校系统提示预约已达上限，本任务已结束。", submission_unknown: "提交结果尚无法确认，请到学校系统核对。", network_unavailable: "网络暂不可用，本任务已结束。", stopped: "任务已停止。若此前已提交，请到学校系统核对。", error: "任务运行出错，请查看右侧日志。", cancelled: "任务已取消。"});
const taskRoot = document.querySelector("[data-task-status-url]");
let taskTimer = null;
async function refreshTask() {
  if (!taskRoot || document.visibilityState !== "visible") return;
  const response = await fetch(taskRoot.dataset.taskStatusUrl, {headers: {"Accept": "application/json"}});
  if (!response.ok) return;
  const payload = await response.json();
  window.JLUBooking.setText(document.querySelector("[data-task-status]"), taskStatusLabels[payload.status] || payload.status);
  window.JLUBooking.setText(document.querySelector("[data-task-phase]"), payload.phase || "尚未开始");
  window.JLUBooking.setText(document.querySelector("[data-task-message]"), taskStatusMessages[payload.status] || "请查看任务状态与运行日志。");
  const banner = document.querySelector("[data-task-status-banner]");
  if (banner) banner.className = `task-status-banner status-${payload.status}`;
  const statusIcon = document.querySelector("[data-task-status-icon]");
  if (statusIcon) statusIcon.setAttribute("href", payload.status === "success" ? "#icon-check" :
    ["error", "submission_unknown", "token_invalid", "account_blocked"].includes(payload.status) ? "#icon-alert" : "#icon-clock");
  const lines = Array.isArray(payload.log_lines) && payload.log_lines.length ? payload.log_lines :
    [terminalStates.has(payload.status) ? "日志暂不可用" : "等待日志…"];
  const logList = document.querySelector("[data-log-lines]");
  if (logList && logList.dataset.current !== JSON.stringify(lines)) {
    logList.replaceChildren(...lines.map((line) => { const item = document.createElement("li"); item.textContent = line; return item; }));
    logList.dataset.current = JSON.stringify(lines);
  }
  window.JLUBooking.setText(document.querySelector("[data-log-region]"), lines.join("\n"));
  if (terminalStates.has(payload.status)) for (const action of document.querySelectorAll("[data-task-active-action]")) action.hidden = true;
  if (terminalStates.has(payload.status) && taskTimer) { clearInterval(taskTimer); taskTimer = null; }
}
function configurePolling() {
  if (!taskRoot || document.visibilityState !== "visible" || taskTimer) return;
  refreshTask().catch(() => {});
  taskTimer = setInterval(() => { refreshTask().catch(() => {}); }, Number(taskRoot.dataset.pollMs || 3000));
}
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden" && taskTimer) { clearInterval(taskTimer); taskTimer = null; }
  else configurePolling();
});
configurePolling();
document.querySelector("[data-refresh-task]")?.addEventListener("click", () => { refreshTask().catch(() => {}); });
const logExpand = document.querySelector("[data-log-expand]");
const logView = document.querySelector("[data-log-view]");
function setLogExpanded(expanded) {
  if (!logExpand || !logView) return;
  logView.closest(".task-log-card")?.classList.toggle("is-expanded", expanded);
  logExpand.setAttribute("aria-expanded", String(expanded));
  const label = logExpand.querySelector("[data-expand-label]");
  if (label) label.textContent = expanded ? "退出全屏" : "全屏查看";
}
logExpand?.addEventListener("click", () => setLogExpanded(logExpand.getAttribute("aria-expanded") !== "true"));
document.addEventListener("keydown", (event) => { if (event.key === "Escape") setLogExpanded(false); });
document.querySelector("[data-go-back]")?.addEventListener("click", (event) => {
  if (document.referrer && new URL(document.referrer).origin === window.location.origin) {
    event.preventDefault();
    window.history.back();
  }
});

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

// A reset returns its one-time password as JSON; show it only in this admin row.
for (const form of document.querySelectorAll("[data-once-response]")) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!window.confirm("确定重置此用户的登录密码？")) return;
    const output = document.querySelector(`[data-password-output="${form.dataset.resetUserId}"]`);
    try {
      const response = await fetch(form.action, {method: "POST", body: new FormData(form)});
      if (!response.ok) throw new Error("重置失败，请刷新后重试。");
      const payload = await response.json();
      if (!payload.temporary_password) throw new Error("服务器没有返回临时密码。");
      window.JLUBooking.setText(output, `临时密码（仅显示 60 秒）：${payload.temporary_password}`);
      if (output) {
        revealedOutputs.add(output);
        setTimeout(() => { output.textContent = ""; revealedOutputs.delete(output); }, 60000);
      }
    } catch (error) { window.JLUBooking.setText(output, error.message); }
  });
}

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

for (const venueSelect of document.querySelectorAll("[data-venue-select]")) {
  const sportSelect = venueSelect.form?.querySelector("[data-sport-select]");
  if (!sportSelect) continue;
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
  }
  venueSelect.addEventListener("change", syncSports);
  syncSports();
}

// GUI-like chip controls enhance the native selects used by the booking forms.
for (const form of document.querySelectorAll("[data-booking-form]")) {
  const venueSelect = form.querySelector("[data-venue-select]");
  const sportSelect = form.querySelector("[data-sport-select]");
  const daySelect = form.querySelector("[data-target-day]");
  if (!venueSelect || !sportSelect || !daySelect) continue;
  const choices = [...form.querySelectorAll("[data-booking-choice]")];
  function syncChoices() {
    for (const choice of choices) {
      const select = form.elements[choice.dataset.choiceName];
      if (choice.dataset.choiceName === "sport") choice.hidden = choice.dataset.venue !== venueSelect.value;
      choice.setAttribute("aria-pressed", String(
        select.value === choice.dataset.choiceValue &&
        (choice.dataset.choiceName !== "sport" || choice.dataset.venue === venueSelect.value)
      ));
    }
    window.JLUBooking.setText(form.querySelector("[data-booking-venue]"), venueSelect.value);
    window.JLUBooking.setText(form.querySelector("[data-booking-summary]"),
      `当前选择：${venueSelect.value} · ${sportSelect.value} · ${daySelect.value === "today" ? "今天" : "明天"}`);
  }
  for (const select of [venueSelect, sportSelect, daySelect]) select.addEventListener("change", syncChoices);
  for (const choice of choices) {
    choice.addEventListener("click", () => {
      const select = form.elements[choice.dataset.choiceName];
      if (choice.dataset.choiceName === "sport") {
        const option = [...select.options].find((item) => item.value === choice.dataset.choiceValue && item.dataset.venue === venueSelect.value);
        if (!option) return;
        option.selected = true;
      } else select.value = choice.dataset.choiceValue;
      select.dispatchEvent(new Event("change", {bubbles: true}));
      syncChoices();
    });
  }
  syncChoices();
}

for (const form of document.querySelectorAll('form[action="/tasks/new"]')) {
  const submit = form.querySelector("[data-booking-submit]");
  const modes = [...form.querySelectorAll('input[name="mode"]')];
  const syncSubmit = () => {
    if (submit) submit.textContent = modes.find((mode) => mode.checked)?.value === "scan"
      ? "立即开始扫描" : "立即启动预约";
  };
  modes.forEach((mode) => mode.addEventListener("change", syncSubmit));
  syncSubmit();
}

const sportSelect = document.querySelector("[data-sport-select]");
const daySelect = document.querySelector("[data-day-select]");
for (const chip of document.querySelectorAll("[data-select-sport]")) {
  chip.addEventListener("click", () => {
    if (!sportSelect) return;
    const venueSelect = sportSelect.form?.querySelector("[data-venue-select]");
    const option = [...sportSelect.options].find((item) => item.value === chip.dataset.selectSport && item.dataset.venue === venueSelect?.value);
    if (!option) return;
    option.selected = true;
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
