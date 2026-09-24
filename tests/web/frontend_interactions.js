"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function element() {
  return {
    dataset: {}, textContent: "", listeners: {}, children: [],
    classList: {add() {}, toggle() {}},
    addEventListener(name, callback) { this.listeners[name] = callback; },
    append(...items) { this.children.push(...items); },
    replaceChildren() { this.children = []; },
    setAttribute() {}, removeAttribute() {},
  };
}

const cards = ["前卫体育馆", "宋治平体育馆"].map((venue) => {
  const card = element();
  card.dataset.setVenue = venue;
  const icon = element();
  const label = element();
  card.querySelector = (selector) => selector.includes("icon") ? icon : label;
  return card;
});
const clear = element();
const body = element();
const caption = element(); caption.textContent = "查询时间：旧时间";
const status = element(); status.textContent = "查询完成";
const counts = {court: element(), slot: element()};
const numbers = {court: element(), slot: element()};
const summaries = {sport: element(), date: element()};
const storage = new Map([["jlu-preferred-venue", "前卫体育馆"]]);
const document = {
  documentElement: element(), visibilityState: "visible", addEventListener() {},
  querySelectorAll(selector) {
    if (selector === "[data-set-venue]") return cards;
    if (selector === "[data-clear-results]") return [clear];
    return [];
  },
  querySelector(selector) {
    if (selector === "[data-results-body]") return body;
    if (selector === "[data-results-caption]") return caption;
    if (selector === "[data-results-status]") return status;
    const count = selector.match(/^\[data-(court|slot)-count\]$/);
    if (count) return counts[count[1]];
    const number = selector.match(/^\[data-(court|slot)-number\]$/);
    if (number) return numbers[number[1]];
    const summary = selector.match(/^\[data-summary-(sport|date)\]$/);
    return summary ? summaries[summary[1]] : null;
  },
  createElement() { return element(); },
};
const window = {
  matchMedia: () => ({matches: false}),
  location: {pathname: "/profile"},
  addEventListener() {},
};
const localStorage = {
  getItem(key) { return storage.get(key) || null; },
  setItem(key, value) { storage.set(key, value); },
};
const source = fs.readFileSync(process.argv[2], "utf8");
vm.runInNewContext(source, {document, window, localStorage, setInterval() {}, clearInterval() {}});
cards[1].listeners.click();
assert.equal(localStorage.getItem("jlu-preferred-venue"), "宋治平体育馆");
clear.listeners.click();
assert.equal(caption.textContent, "查询后将在这里展示空闲时段");
assert.equal(status.textContent, "准备就绪");
assert.equal(counts.court.dataset.courtCount, "0");
assert.equal(counts.slot.dataset.slotCount, "0");
assert.equal(body.children.length, 1);
