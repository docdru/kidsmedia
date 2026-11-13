import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api, type HistoryDay, type Rule, type UsageDay } from "../api/client";
import { formatDuration, formatMinutes } from "../lib/format";

export default function Activity() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const today = useQuery({
    queryKey: ["usage"],
    queryFn: () => api<UsageDay>("GET", "/api/admin/usage"),
    refetchInterval: 30_000,
  });
  const [days, setDays] = useState<7 | 30>(7);
  const history = useQuery({
    queryKey: ["history", days],
    queryFn: () => api<HistoryDay[]>("GET", `/api/admin/usage/history?days=${days}`),
  });
  const rules = useQuery({ queryKey: ["rules"], queryFn: () => api<Rule[]>("GET", "/api/admin/rules") });
  const reset = useMutation({
    mutationFn: () => api<void>("POST", "/api/admin/usage/reset-today"),
    onSuccess: () => {
      for (const key of ["usage", "history", "preview"]) void queryClient.invalidateQueries({ queryKey: [key] });
    },
  });

  // Today's limits per group, for "1 / 2 items" (the strictest enabled limit).
  const weekday = (new Date().getDay() + 6) % 7; // SPEC: 0 = Monday
  const limitFor = (groupId: number, field: "max_items" | "max_minutes"): number | null => {
    const values = (rules.data ?? [])
      .filter((r) => r.enabled !== false && r.mode !== "block" && r.days.includes(weekday))
      .filter((r) => r.all_groups || (r.group_ids ?? []).includes(groupId))
      .map((r) => r[field])
      .filter((v): v is number => v != null);
    return values.length ? Math.min(...values) : null;
  };

  const time = (iso: string) => new Date(iso).toLocaleTimeString(i18n.resolvedLanguage, { timeStyle: "short" });
  const dayLabel = (day: string) =>
    new Date(`${day}T12:00:00`).toLocaleDateString(i18n.resolvedLanguage, { weekday: "short", day: "numeric", month: "short" });
  const maxSeconds = Math.max(60, ...(history.data ?? []).map((d) => d.seconds));

  return (
    <div className="stack">
      <div className="row spread">
        <h2>{t("activity.today")}</h2>
        <button
          type="button"
          className="danger"
          disabled={reset.isPending}
          onClick={() => {
            if (window.confirm(t("activity.confirmReset"))) reset.mutate();
          }}
        >
          {t("activity.reset")}
        </button>
      </div>
      {today.isPending && <p className="muted">{t("common.loading")}</p>}
      <div className="tiles">
        {today.data?.groups.map((g) => {
          const items = limitFor(g.group_id, "max_items");
          const minutes = limitFor(g.group_id, "max_minutes");
          return (
            <div key={g.group_id} className="card tile">
              <strong>{g.name}</strong>
              <span>
                {items != null ? t("activity.itemsOf", { used: g.items, limit: items }) : t("activity.items", { count: g.items })}
              </span>
              <span>
                {minutes != null
                  ? t("activity.minutesOf", { used: formatMinutes(g.seconds), limit: minutes })
                  : t("activity.minutes", { count: formatMinutes(g.seconds) })}
              </span>
            </div>
          );
        })}
        {today.data?.groups.length === 0 && <p className="muted">{t("activity.nothingYet")}</p>}
      </div>

      <section className="card">
        <h3>{t("activity.log")}</h3>
        {today.data?.plays.length === 0 && <p className="muted">{t("activity.nothingYet")}</p>}
        <table className="log">
          <tbody>
            {today.data?.plays.map((p, i) => (
              <tr key={`${p.media_id}-${p.started_at}-${i}`}>
                <td>{time(p.started_at)}</td>
                <td className="grow">{p.title}</td>
                <td>{formatDuration(p.seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <div className="row spread">
          <h3>{t("activity.history", { count: days })}</h3>
          <div className="row">
            {([7, 30] as const).map((n) => (
              <button key={n} type="button" className={n === days ? "pill on" : "pill"} aria-pressed={n === days} onClick={() => setDays(n)}>
                {t("activity.days", { count: n })}
              </button>
            ))}
          </div>
        </div>
        <ul className="bars">
          {history.data?.map((d) => (
            <li key={d.day}>
              <span className="label">{dayLabel(d.day)}</span>
              <span className="bar" style={{ width: `${(d.seconds / maxSeconds) * 100}%` }} />
              <span className="value">
                {t("activity.minutes", { count: formatMinutes(d.seconds) })} · {t("activity.items", { count: d.items })}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
