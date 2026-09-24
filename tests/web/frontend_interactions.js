"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function element(dataset = {}) {
  return {
    dataset, textContent: "", listeners: {}, children: [], hidden: false, attributes: {},
    classList: {add() {}, toggle() {}},
    addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); },
    append(...items) { this.children.push(...items); },
    replaceChildren() { this.children = []; },
    setAttribute(name, value) { this.attributes[name] = value; },
    removeAttribute(name) { delete this.attributes[name]; },
    fire(name) { for (const listener of this.listeners[name] || []) listener({type: name}); },
    dispatchEvent(event) { this.fire(event.type); },
  };
}
function select(values, initialValue) {
  const control = element();
  let selectedIndex = values.findIndex(([value]) => value === initialValue);
  control.options = values.map(([value, venue], index) => {
    const option = {value, dataset: {venue}, hidden: false, disabled: false};
    Object.defineProperty(option, "selected", {
      get() { return selectedIndex === index; },
      set(selected) { if (selected) selectedIndex = index; },
    });
    return option;
  });
  Object.defineProperty(control, "value", {
    get() { return control.options[selectedIndex]?.value || ""; },
    set(value) { selectedIndex = control.options.findIndex((option) => option.value === value); },
  });
  return control;
}

const venue = select([["前卫体育馆"], ["宋治平体育馆"]], "前卫体育馆");
const sport = select([["羽毛球", "前卫体育馆"], ["乒乓球", "前卫体育馆"], ["排球", "宋治平体育馆"]], "羽毛球");
const day = select([["today"], ["tomorrow"]], "today");
const venueBadge = element();
const bookingSummary = element();
const choice = (name, value, venueName) => element({choiceName: name, choiceValue: value, venue: venueName});
const choices = [
  choice("venue", "前卫体育馆"), choice("venue", "宋治平体育馆"),
  choice("sport", "羽毛球", "前卫体育馆"), choice("sport", "排球", "宋治平体育馆"),
  choice("target_day", "today"), choice("target_day", "tomorrow"),
];
const bookingForm = {
  elements: {venue, sport, target_day: day},
  querySelector(selector) {
    if (selector === "[data-venue-select]") return venue;
    if (selector === "[data-sport-select]") return sport;
    if (selector === "[data-target-day]") return day;
    if (selector === "[data-booking-venue]") return venueBadge;
    if (selector === "[data-booking-summary]") return bookingSummary;
    return null;
  },
  querySelectorAll(selector) { return selector === "[data-booking-choice]" ? choices : []; },
};
venue.form = bookingForm;
sport.form = bookingForm;
day.form = bookingForm;

const clear = element();
const body = element();
const caption = element(); caption.textContent = "查询时间：旧时间";
const status = element(); status.textContent = "查询完成";
const counts = {court: element(), slot: element()};
const numbers = {court: element(), slot: element()};
const summaries = {sport: element(), date: element()};
const targetDate = element();
const executionDate = element({executionDate: "2026-09-24"});
const userNav = element(); userNav.setAttribute("open", "");
const media = {matches: true, addEventListener(_name, listener) { this.listener = listener; }};
const document = {
  documentElement: element(), visibilityState: "visible", addEventListener() {},
  querySelectorAll(selector) {
    if (selector === "[data-booking-form]") return [bookingForm];
    if (selector === "[data-venue-select]") return [venue];
    if (selector === "[data-clear-results]") return [clear];
    return [];
  },
  querySelector(selector) {
    if (selector === "[data-user-nav]") return userNav;
    if (selector === "[data-target-day]") return day;
    if (selector === "[data-target-date]") return targetDate;
    if (selector === "[data-execution-date]") return executionDate;
    if (selector === "[data-sport-select]") return sport;
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
const window = {matchMedia: () => media, location: {pathname: "/tasks/new"}, addEventListener() {}};
const source = fs.readFileSync(process.argv[2], "utf8");
vm.runInNewContext(source, {document, window, Event: class Event { constructor(type) { this.type = type; } }, setInterval() {}, clearInterval() {}});

assert.equal(userNav.attributes.open, undefined);
media.matches = false;
media.listener({matches: false});
assert.equal(userNav.attributes.open, "");

assert.equal(bookingSummary.textContent, "当前选择：前卫体育馆 · 羽毛球 · 今天");
choices[1].fire("click");
assert.equal(venue.value, "宋治平体育馆");
assert.equal(sport.value, "排球");
assert.equal(choices[2].hidden, true);
assert.equal(choices[3].hidden, false);
assert.equal(bookingSummary.textContent, "当前选择：宋治平体育馆 · 排球 · 今天");
choices[5].fire("click");
assert.equal(day.value, "tomorrow");
assert.equal(targetDate.textContent, "2026-09-25");
assert.equal(choices[5].attributes["aria-pressed"], "true");

clear.fire("click");
assert.equal(caption.textContent, "查询后将在这里展示空闲时段");
assert.equal(status.textContent, "准备就绪");
assert.equal(counts.court.dataset.courtCount, "0");
assert.equal(counts.slot.dataset.slotCount, "0");
assert.equal(body.children.length, 1);
