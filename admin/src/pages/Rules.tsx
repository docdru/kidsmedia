import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, type GemsAdmin, type Group, type GroupStatus, type Rule, type RuleIn } from "../api/client";
import { errorText } from "../lib/errors";
import { WEEKDAYS, clampGems, clampReplay, statusText, summarizeRule } from "../lib/format";

const EMPTY: RuleIn = {
  name: "",
  enabled: true,
  days: [0, 1, 2, 3, 4, 5, 6],
  all_groups: false,
  group_ids: [],
  mode: "limit",
  max_items: null,
  max_minutes: null,
  lockouts: [],
  grace_minutes: 0,
  count_after_seconds: 10,
  replay_percent: 120,
  gem_cost: null,
};

export default function Rules() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const rules = useQuery({ queryKey: ["rules"], queryFn: () => api<Rule[]>("GET", "/api/admin/rules") });
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => api<Group[]>("GET", "/api/admin/groups") });
  const [editing, setEditing] = useState<{ id: number | null; draft: RuleIn } | null>(null);

  const changed = () => {
    void queryClient.invalidateQueries({ queryKey: ["rules"] });
    void queryClient.invalidateQueries({ queryKey: ["preview"] });
  };
  const toggle = useMutation({
    mutationFn: (r: Rule) => api<Rule>("PATCH", `/api/admin/rules/${r.id}`, { enabled: !r.enabled }),
    onSuccess: changed,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api<void>("DELETE", `/api/admin/rules/${id}`),
    onSuccess: changed,
  });
  const bonus = useMutation({
    mutationFn: (body: { rule_id: number; extra_items?: number; extra_minutes?: number }) =>
      api<void>("POST", "/api/admin/usage/bonus", body),
    onSuccess: () => {
      for (const key of ["preview", "usage"]) void queryClient.invalidateQueries({ queryKey: [key] });
    },
  });

  const groupList = groups.data ?? [];
  const nameOf = (id: number) => groupList.find((g) => g.id === id)?.name ?? `#${id}`;

  return (
    <div className="split">
      <section className="grow stack">
        <div className="row spread">
          <h2>{t("rules.title")}</h2>
          <button type="button" className="primary" onClick={() => setEditing({ id: null, draft: { ...EMPTY } })}>
            {t("rules.new")}
          </button>
        </div>
        <GemsCard />
        {editing && (
          <RuleEditor
            id={editing.id}
            initial={editing.draft}
            groups={groupList}
            onDone={() => {
              setEditing(null);
              changed();
            }}
          />
        )}
        {rules.isPending && <p className="muted">{t("common.loading")}</p>}
        {rules.data?.length === 0 && <p className="muted">{t("rules.none")}</p>}
        {bonus.isSuccess && (
          <p role="status" className="ok">
            {t("rules.bonusGiven")}
          </p>
        )}
        {rules.data?.map((r) => (
          <article key={r.id} className={r.enabled ? "card rule" : "card rule off"}>
            <div className="row spread wrap">
              <div className="grow">
                <h3>{r.name}</h3>
                <p>{summarizeRule(r, nameOf, t)}</p>
                {r.enabled !== false && r.mode !== "block" && (r.max_items != null || r.max_minutes != null) && (
                  <div className="row wrap small">
                    <span className="muted">{t("rules.bonusToday")}</span>
                    {r.max_items != null && (
                      <button type="button" className="small" onClick={() => bonus.mutate({ rule_id: r.id, extra_items: 1 })}>
                        {t("rules.bonusItem")}
                      </button>
                    )}
                    {r.max_minutes != null && (
                      <button type="button" className="small" onClick={() => bonus.mutate({ rule_id: r.id, extra_minutes: 15 })}>
                        {t("rules.bonusMinutes", { count: 15 })}
                      </button>
                    )}
                  </div>
                )}
              </div>
              <div className="row">
                <label className="check">
                  <input type="checkbox" checked={r.enabled ?? true} onChange={() => toggle.mutate(r)} />
                  {t("rules.enabled")}
                </label>
                <button type="button" onClick={() => setEditing({ id: r.id, draft: toInput(r) })}>
                  {t("common.edit")}
                </button>
                <button
                  type="button"
                  className="danger"
                  onClick={() => {
                    if (window.confirm(t("rules.confirmDelete", { name: r.name }))) remove.mutate(r.id);
                  }}
                >
                  {t("common.delete")}
                </button>
              </div>
            </div>
          </article>
        ))}
      </section>
      <aside className="sidebar">
        <RightNow />
      </aside>
    </div>
  );
}

function toInput(r: Rule): RuleIn {
  return {
    name: r.name,
    enabled: r.enabled,
    days: r.days,
    all_groups: r.all_groups,
    group_ids: r.group_ids,
    mode: r.mode,
    max_items: r.max_items,
    max_minutes: r.max_minutes,
    lockouts: r.lockouts,
    grace_minutes: r.grace_minutes,
    count_after_seconds: r.count_after_seconds,
    replay_percent: r.replay_percent,
    gem_cost: r.gem_cost,
    message_en: r.message_en,
    message_de: r.message_de,
    message_el: r.message_el,
  };
}

function numberOrNull(value: string): number | null {
  if (value.trim() === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(0, Math.round(n)) : null;
}

function RuleEditor({
  id,
  initial,
  groups,
  onDone,
}: {
  id: number | null;
  initial: RuleIn;
  groups: Group[];
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [rule, setRule] = useState<RuleIn>(initial);
  const set = (patch: Partial<RuleIn>) => setRule((r) => ({ ...r, ...patch }));
  const save = useMutation({
    mutationFn: () => {
      // Out-of-range input (typed or pasted) is pulled into range instead of failing with a 422.
      const body = { ...rule, replay_percent: clampReplay(rule.replay_percent), gem_cost: clampGems(rule.gem_cost) };
      return id === null ? api<Rule>("POST", "/api/admin/rules", body) : api<Rule>("PUT", `/api/admin/rules/${id}`, body);
    },
    onSuccess: onDone,
  });

  const days = rule.days;
  const groupIds = rule.group_ids ?? [];
  const lockouts = rule.lockouts ?? [];
  const scopeMissing = !rule.all_groups && groupIds.length === 0;
  // count_after_seconds and replay_percent apply with an item limit or a gem cost (SPEC §16.2).
  const counts = rule.max_items != null || rule.gem_cost != null;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <form className="card stack editor" onSubmit={submit}>
      <label>
        {t("rules.name")}
        <input value={rule.name} onChange={(e) => set({ name: e.target.value })} required maxLength={80} />
      </label>

      <fieldset>
        <legend>{t("rules.scope")}</legend>
        <label className="check">
          <input type="checkbox" checked={rule.all_groups ?? false} onChange={(e) => set({ all_groups: e.target.checked })} />
          {t("rules.scopeAll")}
        </label>
        {!rule.all_groups && (
          <div className="row wrap">
            {groups.map((g) => (
              <label key={g.id} className="check">
                <input
                  type="checkbox"
                  checked={groupIds.includes(g.id)}
                  onChange={(e) =>
                    set({ group_ids: e.target.checked ? [...groupIds, g.id] : groupIds.filter((x) => x !== g.id) })
                  }
                />
                {g.name}
              </label>
            ))}
          </div>
        )}
      </fieldset>

      <fieldset>
        <legend>{t("rules.days")}</legend>
        <div className="row wrap">
          {WEEKDAYS.map((d) => (
            <button
              key={d}
              type="button"
              className={days.includes(d) ? "pill on" : "pill"}
              aria-pressed={days.includes(d)}
              onClick={() => set({ days: days.includes(d) ? days.filter((x) => x !== d) : [...days, d].sort((a, b) => a - b) })}
            >
              {t(`days.short.${d}`)}
            </button>
          ))}
        </div>
      </fieldset>

      <fieldset>
        <legend>{t("rules.mode")}</legend>
        <label className="check">
          <input type="radio" name="mode" checked={rule.mode !== "block"} onChange={() => set({ mode: "limit" })} />
          {t("rules.modeLimit")}
        </label>
        <label className="check">
          <input type="radio" name="mode" checked={rule.mode === "block"} onChange={() => set({ mode: "block" })} />
          {t("rules.modeBlock")}
        </label>
      </fieldset>

      {rule.mode !== "block" && (
        <>
          <div className="row wrap">
            <label>
              {t("rules.maxItems")}
              <input
                type="number"
                min={0}
                max={100}
                value={rule.max_items ?? ""}
                placeholder={t("rules.noLimit")}
                onChange={(e) => set({ max_items: numberOrNull(e.target.value) })}
              />
            </label>
            <label>
              {t("rules.gemCost")}
              <span className="row">
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={rule.gem_cost ?? ""}
                  placeholder={t("rules.gemCostNone")}
                  onChange={(e) => set({ gem_cost: numberOrNull(e.target.value) })}
                  onBlur={() => set({ gem_cost: clampGems(rule.gem_cost) })}
                />
                <span>{t("rules.gemCostUnit")}</span>
              </span>
            </label>
            {counts && (
              <label>
                {t("rules.countAfter")}
                <input
                  type="number"
                  min={0}
                  max={600}
                  value={rule.count_after_seconds ?? 10}
                  onChange={(e) => set({ count_after_seconds: Math.min(600, numberOrNull(e.target.value) ?? 0) })}
                />
              </label>
            )}
            {counts && (
              <label>
                {t("rules.replay")}
                <input
                  type="number"
                  min={100}
                  max={1000}
                  step={10}
                  value={rule.replay_percent ?? ""}
                  placeholder={t("rules.replayUnlimited")}
                  onChange={(e) => set({ replay_percent: numberOrNull(e.target.value) })}
                  onBlur={() => set({ replay_percent: clampReplay(rule.replay_percent) })}
                />
              </label>
            )}
            <label>
              {t("rules.maxMinutes")}
              <input
                type="number"
                min={0}
                max={1440}
                value={rule.max_minutes ?? ""}
                placeholder={t("rules.noLimit")}
                onChange={(e) => set({ max_minutes: numberOrNull(e.target.value) })}
              />
            </label>
            <label>
              {t("rules.grace")}
              <input
                type="number"
                min={0}
                max={120}
                value={rule.grace_minutes ?? 0}
                onChange={(e) => set({ grace_minutes: numberOrNull(e.target.value) ?? 0 })}
              />
            </label>
          </div>
          {counts && (
            <>
              <p className="muted small">{t("rules.countAfterHelp")}</p>
              <p className="muted small">{t("rules.replayHelp")}</p>
            </>
          )}
          {rule.gem_cost != null && <p className="muted small">{t("rules.gemCostHelp")}</p>}
          <fieldset>
            <legend>{t("rules.lockouts")}</legend>
            <p className="muted small">{t("rules.lockoutHelp")}</p>
            {lockouts.map((w, i) => (
              <div key={i} className="row">
                <input
                  type="time"
                  aria-label={t("rules.from")}
                  value={w.start}
                  onChange={(e) =>
                    set({ lockouts: lockouts.map((x, j) => (j === i ? { ...x, start: e.target.value } : x)) })
                  }
                  required
                />
                <span>–</span>
                <input
                  type="time"
                  aria-label={t("rules.to")}
                  value={w.end}
                  onChange={(e) => set({ lockouts: lockouts.map((x, j) => (j === i ? { ...x, end: e.target.value } : x)) })}
                  required
                />
                <button type="button" className="link" onClick={() => set({ lockouts: lockouts.filter((_, j) => j !== i) })}>
                  {t("common.remove")}
                </button>
              </div>
            ))}
            {lockouts.length < 8 && (
              <button type="button" onClick={() => set({ lockouts: [...lockouts, { start: "19:30", end: "07:00" }] })}>
                {t("rules.addLockout")}
              </button>
            )}
          </fieldset>
        </>
      )}

      <fieldset>
        <legend>{t("rules.message")}</legend>
        <p className="muted small">{t("rules.messageHelp")}</p>
        <label>
          {t("language.en")}
          <input
            value={rule.message_en ?? ""}
            maxLength={140}
            placeholder={t("rules.messagePlaceholder")}
            onChange={(e) => set({ message_en: e.target.value || null })}
          />
        </label>
        <label>
          {t("language.de")}
          <input
            value={rule.message_de ?? ""}
            maxLength={140}
            placeholder={t("rules.messagePlaceholder")}
            onChange={(e) => set({ message_de: e.target.value || null })}
          />
        </label>
        <label>
          {t("language.el")}
          <input
            value={rule.message_el ?? ""}
            maxLength={140}
            placeholder={t("rules.messagePlaceholder")}
            onChange={(e) => set({ message_el: e.target.value || null })}
          />
        </label>
      </fieldset>

      {scopeMissing && <p className="error">{t("rules.pickScope")}</p>}
      {save.error && <p className="error">{errorText(save.error, t)}</p>}
      <div className="row">
        <button type="submit" className="primary" disabled={save.isPending || scopeMissing || days.length === 0}>
          {t("common.save")}
        </button>
        <button type="button" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

/** The child's gems: balance, quick buttons and the last entries (SPEC §16.4). */
function GemsCard() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const gems = useQuery({ queryKey: ["gems"], queryFn: () => api<GemsAdmin>("GET", "/api/admin/gems?limit=10") });
  const [note, setNote] = useState("");
  // The entry an Undo created, so it doesn't offer to undo itself.
  const [undoEntry, setUndoEntry] = useState<number | null>(null);
  const change = useMutation({
    mutationFn: ({ delta, note }: { delta: number; note?: string; undo?: boolean }) =>
      api<GemsAdmin>("POST", "/api/admin/gems", note ? { delta, note } : { delta }),
    onSuccess: (data, body) => {
      queryClient.setQueryData(["gems"], data);
      // The balance decides the gems reason in the preview (SPEC §16.3).
      void queryClient.invalidateQueries({ queryKey: ["preview"] });
      if (body.undo) setUndoEntry(data.entries[0]?.id ?? null);
      else setNote("");
    },
  });

  const lang = i18n.resolvedLanguage ?? "en";
  const when = (iso: string) => new Date(iso).toLocaleString(lang, { dateStyle: "medium", timeStyle: "short" });
  const balance = gems.data?.balance ?? 0;
  const entries = gems.data?.entries ?? [];
  const lastAdmin = entries.find((e) => e.kind === "admin");
  const give = (delta: number) => change.mutate({ delta, note: note.trim() || undefined });
  const undo = (delta: number, original: string | null | undefined) => {
    const label = t("rules.gems.undoNote");
    change.mutate({ delta: -delta, note: (original ? `${label}: ${original}` : label).slice(0, 140), undo: true });
  };

  return (
    <section className="card stack" aria-labelledby="rules-gems">
      <h3 id="rules-gems">{t("rules.gems.title")}</h3>
      {gems.isPending && <p className="muted">{t("common.loading")}</p>}
      {gems.isError && <p className="error">{errorText(gems.error, t)}</p>}
      {gems.data && (
        <>
          <p className="gems-balance">{t("rules.gems.balance", { count: balance })}</p>
          <div className="row wrap">
            <button type="button" disabled={change.isPending || balance < 1} onClick={() => give(-1)}>
              {t("rules.gems.remove", { count: 1 })}
            </button>
            <button type="button" disabled={change.isPending} onClick={() => give(1)}>
              {t("rules.gems.add", { count: 1 })}
            </button>
            <button type="button" disabled={change.isPending} onClick={() => give(5)}>
              {t("rules.gems.add", { count: 5 })}
            </button>
          </div>
          <label>
            {t("rules.gems.note")}
            <input
              value={note}
              maxLength={140}
              placeholder={t("rules.gems.notePlaceholder")}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          {change.error && <p className="error">{errorText(change.error, t)}</p>}
          {entries.length === 0 ? (
            <p className="muted">{t("rules.gems.none")}</p>
          ) : (
            <ul className="gems-log">
              {entries.map((e) => (
                <li key={e.id} className="row wrap">
                  <strong>{e.delta < 0 ? `−${-e.delta}` : `+${e.delta}`}</strong>
                  <span className="grow">
                    {e.note || e.title || (e.kind === "spend" ? t("rules.gems.spent") : t("rules.gems.byParent"))}
                  </span>
                  <span className="muted small">{when(e.created_at)}</span>
                  {e.id === lastAdmin?.id && e.id !== undoEntry && (
                    <button type="button" className="link" disabled={change.isPending} onClick={() => undo(e.delta, e.note)}>
                      {t("rules.gems.undo")}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

/** What the child can play at this moment, from the same evaluation code (SPEC §3.5). */
export function RightNow() {
  const { t } = useTranslation();
  // Simulate (SPEC §3.5): a local date and time, "YYYY-MM-DDTHH:MM"; empty = now.
  const [at, setAt] = useState("");
  const preview = useQuery({
    queryKey: ["preview", at],
    queryFn: () =>
      api<GroupStatus[]>("GET", at ? `/api/admin/preview?at=${encodeURIComponent(at)}` : "/api/admin/preview"),
    refetchInterval: at ? false : 30_000,
  });
  return (
    <div className="card stack">
      <h2>{at ? t("rules.simulated") : t("rules.rightNow")}</h2>
      <label>
        {t("rules.simulate")}
        <span className="row">
          <input type="datetime-local" value={at} onChange={(e) => setAt(e.target.value)} />
          {at && (
            <button type="button" className="link" onClick={() => setAt("")}>
              {t("rules.backToNow")}
            </button>
          )}
        </span>
      </label>
      {preview.isPending && <p className="muted">{t("common.loading")}</p>}
      <ul className="statuslist">
        {preview.data?.map((g) => (
          <li key={g.group_id} className={g.status.allowed ? "ok" : "no"}>
            <strong>{g.name}</strong>
            <span>{statusText(g.status, t)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
