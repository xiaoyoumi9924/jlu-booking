"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

const form = {action: "/admin/users/7/reset-password", dataset: {resetUserId: "7"}, listeners: {}, addEventListener(name, handler) { this.listeners[name] = handler; }};
const output = {textContent: ""};
let requested = "";
const document = {
  documentElement: {classList: {add() {}}},
  visibilityState: "visible",
  addEventListener() {},
  querySelectorAll(selector) { return selector === "[data-once-response]" ? [form] : []; },
  querySelector(selector) { return selector === '[data-password-output="7"]' ? output : null; },
};
const window = {matchMedia: () => ({matches: false}), addEventListener() {}, confirm: () => true};
const fetch = async (url) => { requested = url; return {ok: true, json: async () => ({temporary_password: "example-only-temporary"})}; };
vm.runInNewContext(fs.readFileSync(process.argv[2], "utf8"), {
  document, window, fetch, FormData: class FormData {}, setInterval() {}, clearInterval() {}, setTimeout() {},
});

(async () => {
  assert.equal(typeof form.listeners.submit, "function");
  let prevented = false;
  await form.listeners.submit({preventDefault() { prevented = true; }});
  assert.equal(prevented, true);
  assert.equal(requested, form.action);
  assert.match(output.textContent, /example-only-temporary/);
})().catch((error) => { console.error(error); process.exitCode = 1; });
