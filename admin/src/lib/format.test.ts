import { beforeAll, describe, expect, it } from "vitest";

import type { Rule } from "../api/client";
import i18n from "../i18n";
import { clampGems, clampReplay, formatDuration, statusText, summarizeDays, summarizeRule } from "./format";

const t = i18n.t.bind(i18n);

beforeAll(async () => {
  await i18n.changeLanguage("en");
});

const rule = (patch: Partial<Rule>): Rule => ({
  id: 1,
  name: "r",
  days: [5, 6],
  enabled: true,
  all_groups: false,
  group_ids: [1, 2],
  mode: "limit",
  max_items: null,
  max_minutes: null,
  lockouts: [],
  grace_minutes: 0,
  count_after_seconds: 10,
  replay_percent: 120,
  gem_cost: null,
  ...patch,
});
const names = (id: number) => ({ 1: "Moon Pirates", 2: "Detective Fox" })[id] ?? "?";

describe("summarizeDays", () => {
  it("collapses runs and the full week", () => {
    expect(summarizeDays([0, 1, 2, 3, 4], t)).toBe("Mon–Fri");
    expect(summarizeDays([5, 6], t)).toBe("Sat, Sun");
    expect(summarizeDays([6, 0, 1, 2, 3], t)).toBe("Mon–Thu, Sun");
    expect(summarizeDays([0, 1, 2, 3, 4, 5, 6], t)).toBe("every day");
  });
});

describe("summarizeRule (SPEC §9.2)", () => {
  it("describes the §3.4 weekend audiobook rule", () => {
    expect(summarizeRule(rule({ max_items: 1 }), names, t)).toBe(
      "Max 1 file per day from Moon Pirates + Detective Fox · Sat, Sun",
    );
  });
  it("describes bedtime with grace", () => {
    const bedtime = rule({ all_groups: true, days: [0, 1, 2, 3, 4, 5, 6], lockouts: [{ start: "19:30", end: "07:00" }], grace_minutes: 10 });
    expect(summarizeRule(bedtime, names, t)).toBe("all groups locked 19:30–07:00 · 10 minutes grace · every day");
  });
  it("describes a gem rule (SPEC §16.4)", () => {
    expect(summarizeRule(rule({ group_ids: [1], days: [0, 1, 2, 3, 4, 5, 6], gem_cost: 2 }), names, t)).toBe(
      "Each file from Moon Pirates costs 2 💎 per listen · every day",
    );
  });
  it("describes a block", () => {
    expect(summarizeRule(rule({ mode: "block", days: [0, 1, 2, 3, 4] }), names, t)).toBe(
      "Moon Pirates + Detective Fox blocked · Mon–Fri",
    );
  });
});

describe("statusText", () => {
  it("turns reason codes into text", () => {
    expect(statusText({ allowed: false, reason: "lockout", params: { until: "07:00" } }, t)).toBe("Locked until 07:00");
    expect(statusText({ allowed: true, items_left: 1, minutes_left: 30 }, t)).toBe("1 file left · 30 minutes left");
    expect(statusText({ allowed: true }, t)).toBe("Available");
    expect(statusText({ allowed: false, reason: "gems", params: { need: 2, have: 1 } }, t)).toBe("Needs 2 💎, has 1");
    expect(statusText({ allowed: true, items_left: 1, gem_cost: 2 }, t)).toBe("1 file left · 2 💎 per new file");
  });
});

it("formats durations", () => {
  expect(formatDuration(65)).toBe("1:05");
  expect(formatDuration(3725)).toBe("1:02:05");
});

describe("clampReplay", () => {
  it("pulls typed values into the API's 100–1000 % and keeps empty as unlimited", () => {
    expect(clampReplay(50)).toBe(100);
    expect(clampReplay(0)).toBe(100);
    expect(clampReplay(5000)).toBe(1000);
    expect(clampReplay(130)).toBe(130);
    expect(clampReplay(null)).toBeNull();
    expect(clampReplay(undefined)).toBeNull();
  });
});

describe("clampGems", () => {
  it("pulls typed values into the API's 1–20 and keeps empty or 0 as no gems", () => {
    expect(clampGems(25)).toBe(20);
    expect(clampGems(2)).toBe(2);
    expect(clampGems(0)).toBeNull();
    expect(clampGems(null)).toBeNull();
    expect(clampGems(undefined)).toBeNull();
  });
});
