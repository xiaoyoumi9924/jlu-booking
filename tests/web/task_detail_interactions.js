"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function element() {
  return {
    dataset: {}, textContent: "", hidden: false, attributes: {}, children: [], listeners: {},
    classList: {values: new Set(), toggle(name, enabled) { if (enabled) this.values.add(name); else this.values.delete(name); }},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    setAttribute(name, value) { this.attributes[name] = value; },
    getAttribute(name) { return this.attributes[name]; },
    replaceChildren(...children) { this.children = children; },
  };
}

const root = element(); root.dataset.taskStatusUrl = "/tasks/3/status";
const status = element();
const message = element();
const phase = element();
const banner = element();
const statusIcon = element();
const lines = element();
const refresh = element();
const expand = element(); expand.attributes["aria-expanded"] = "false";
const expandLabel = element();
expand.querySelector = (selector) => selector === "[data-expand-label]" ? expandLabel : null;
const logCard = element();
const logView = element(); logView.closest = () => logCard;
const activeAction = element();
const items = {"[data-task-status-url]": root, "[data-task-status]": status, "[data-task-message]": message,
  "[data-task-phase]": phase, "[data-task-status-banner]": banner, "[data-task-status-icon]": statusIcon, "[data-log-lines]": lines,
  "[data-refresh-task]": refresh, "[data-log-expand]": expand, "[data-log-view]": logView};
const document = {
  documentElement: {classList: {add() {}}}, visibilityState: "visible", referrer: "",
  addEventListener() {}, querySelector(selector) { return items[selector] || null; },
  querySelectorAll(selector) { return selector === "[data-task-active-action]" ? [activeAction] : []; },
  createElement() { return element(); },
};
const window = {matchMedia: () => ({matches: false}), addEventListener() {}, location: {origin: "http://test"}};
const fetch = async () => ({ok: true, json: async () => ({status: "success", phase: "finished", log_lines: ["<script>not HTML</script>"]})});
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), {
  document, window, fetch, setInterval() {}, clearInterval() {}, setTimeout() {},
});

(async () => {
  await new Promise(setImmediate);
  assert.equal(status.textContent, "预约成功");
  assert.equal(statusIcon.attributes.href, "#icon-check");
  assert.equal(phase.textContent, "finished");
  assert.equal(lines.children[0].textContent, "<script>not HTML</script>");
  assert.equal(activeAction.hidden, true);
  expand.listeners.click();
  assert.equal(logCard.classList.values.has("is-expanded"), true);
  assert.equal(expandLabel.textContent, "退出全屏");
  expand.listeners.click();
  assert.equal(logCard.classList.values.has("is-expanded"), false);
})().catch((error) => { console.error(error); process.exitCode = 1; });
