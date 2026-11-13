import type { Translate } from "./translate";

import type { Rule, Status } from "../api/client";

/** SPEC weekdays: 0 = Monday … 6 = Sunday. */
export const WEEKDAYS = [0, 1, 2, 3, 4, 5, 6] as const;

export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}

export function formatMinutes(seconds: number): number {
  return Math.round(seconds / 60);
}

/**
 * Compact weekday list: consecutive runs become ranges ("Mon–Fri"), the full
 * week becomes "every day".
 */
export function summarizeDays(days: readonly number[], t: Translate): string {
  const sorted = [...new Set(days)].filter((d) => d >= 0 && d <= 6).sort((a, b) => a - b);
  if (sorted.length === 7) return t("days.every");
  const runs: number[][] = [];
  for (const d of sorted) {
    const last = runs[runs.length - 1];
    if (last && last[last.length - 1] === d - 1) last.push(d);
    else runs.push([d]);
  }
  return runs
    .map((run) =>
      run.length >= 3
        ? `${t(`days.short.${run[0]}`)}–${t(`days.short.${run[run.length - 1]}`)}`
        : run.map((d) => t(`days.short.${d}`)).join(", "),
    )
    .join(", ");
}

/** Plain-language rule summary for the rule cards (SPEC §9.2). */
export function summarizeRule(rule: Rule, groupNames: (id: number) => string, t: Translate): string {
  const scope = rule.all_groups ? t("rules.scopeAll") : (rule.group_ids ?? []).map(groupNames).join(" + ");
  const parts: string[] = [];
  if (rule.mode === "block") {
    parts.push(t("rules.summary.block", { scope }));
  } else {
    if (rule.max_items != null) parts.push(t("rules.summary.items", { count: rule.max_items, scope }));
    if (rule.max_minutes != null) parts.push(t("rules.summary.minutes", { count: rule.max_minutes, scope }));
    if (rule.gem_cost != null) parts.push(t("rules.summary.gems", { count: rule.gem_cost, scope }));
    for (const w of rule.lockouts ?? []) parts.push(t("rules.summary.lockout", { start: w.start, end: w.end, scope }));
    if (parts.length === 0) parts.push(t("rules.summary.none", { scope }));
  }
  if ((rule.grace_minutes ?? 0) > 0) parts.push(t("rules.summary.grace", { count: rule.grace_minutes }));
  return `${parts.join(" · ")} · ${summarizeDays(rule.days, t)}`;
}

/** What the child sees for a group right now, from the server's reason code (SPEC §3.2). */
export function statusText(status: Status, t: Translate): string {
  if (!status.allowed) {
    const params = (status.params ?? {}) as Record<string, unknown>;
    switch (status.reason) {
      case "lockout":
        return t("status.lockout", { until: String(params.until ?? "") });
      case "blocked_today":
        return t("status.blocked_today");
      case "item_limit":
        return t("status.item_limit");
      case "minute_limit":
        return t("status.minute_limit");
      case "gems":
        return t("status.gems", { need: String(params.need ?? "?"), have: String(params.have ?? "?") });
      default:
        return t("status.other", { reason: status.reason ?? "?" });
    }
  }
  const bits: string[] = [];
  if (status.items_left != null) bits.push(t("status.items_left", { count: status.items_left }));
  if (status.minutes_left != null) bits.push(t("status.minutes_left", { count: status.minutes_left }));
  if (status.gem_cost != null) bits.push(t("status.gem_cost", { count: status.gem_cost }));
  return bits.length ? bits.join(" · ") : t("status.unlimited");
}

/** The API's range for a listening budget (SPEC §3.1); empty stays "unlimited". */
export function clampReplay(value: number | null | undefined): number | null {
  return value == null ? null : Math.min(1000, Math.max(100, value));
}

/** The API's range for a gem cost (SPEC §3.1); empty or 0 means no gems needed. */
export function clampGems(value: number | null | undefined): number | null {
  return value == null || value <= 0 ? null : Math.min(20, value);
}
