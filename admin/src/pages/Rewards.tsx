import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import {
  api,
  type Group,
  type RewardsAdmin,
  type Rule,
  type Swap,
  type SwapIn,
  type Wish,
  type WishIn,
  type WishTexts,
} from "../api/client";
import { errorText } from "../lib/errors";
import { LANGS, pick, type Lang } from "../lib/ideas";
import type { Translate } from "../lib/translate";

/** Query key of GET /api/admin/rewards. */
export const REWARDS_KEY = ["rewards"] as const;

type SwapDraft = {
  rule_id: number | null;
  kind: "items" | "minutes";
  amount: number | null;
  stars: number | null;
  max_per_day: number | null;
  enabled: boolean;
};

type WishDraft = { names: Record<Lang, string>; stars: number | null; enabled: boolean };

const EMPTY_WISH: WishDraft = { names: { de: "", en: "", el: "" }, stars: 10, enabled: true };

function numberOrNull(value: string): number | null {
  if (value.trim() === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? Math.max(0, Math.round(n)) : null;
}

/** A wish's name in the UI language (falling back like the tablet), or its id. */
export function wishName(wish: Wish | undefined, lang: string, id: number): string {
  return (wish && pick(wish.texts, lang)?.name) || `#${id}`;
}

/** "Stories (Moon + Sun)": the rule's name and the groups it covers. */
export function ruleLabel(rule: Rule | undefined, id: number, groupName: (id: number) => string, t: Translate): string {
  if (!rule) return t("rewards.unknownRule", { id });
  const groups = rule.all_groups ? t("rules.scopeAll") : (rule.group_ids ?? []).map(groupName).join(" + ");
  return groups ? t("rewards.ruleGroups", { name: rule.name, groups }) : rule.name;
}

/** "1 more file today" or "15 more minutes today". */
export function swapGives(swap: Pick<Swap, "extra_items" | "extra_minutes">, t: Translate): string {
  return (swap.extra_items ?? 0) > 0
    ? t("rewards.giveItems", { count: swap.extra_items })
    : t("rewards.giveMinutes", { count: swap.extra_minutes ?? 0 });
}

/** The body of POST/PATCH …/swaps: either extra files or extra minutes, never both (SPEC §14.4). */
export function swapBody(d: SwapDraft): SwapIn {
  const amount = d.amount ?? 0;
  return {
    stars: d.stars ?? 0,
    rule_id: d.rule_id ?? 0,
    extra_items: d.kind === "items" ? amount : 0,
    extra_minutes: d.kind === "minutes" ? amount : 0,
    max_per_day: d.max_per_day,
    enabled: d.enabled,
  };
}

/** The body of POST/PATCH …/wishes: only the languages with a name. */
export function wishBody(d: WishDraft): WishIn {
  const texts: Partial<Record<Lang, WishTexts>> = {};
  for (const l of LANGS) {
    const name = d.names[l].trim();
    if (name) texts[l] = { name };
  }
  return { texts: texts as WishIn["texts"], stars: d.stars ?? 0, enabled: d.enabled };
}

/** Stars, swaps, wishes and the parent's approvals (SPEC §14.4). */
export default function Rewards() {
  const { t, i18n } = useTranslation();
  const state = useQuery({
    queryKey: REWARDS_KEY,
    queryFn: () => api<RewardsAdmin>("GET", "/api/admin/rewards"),
    // The child asks for wishes on the tablet; show new requests without a reload.
    refetchInterval: 30_000,
  });
  const rules = useQuery({ queryKey: ["rules"], queryFn: () => api<Rule[]>("GET", "/api/admin/rules") });
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => api<Group[]>("GET", "/api/admin/groups") });
  const lang = i18n.resolvedLanguage ?? "en";

  if (state.isPending) return <p className="muted">{t("common.loading")}</p>;
  if (state.isError) return <p className="error">{errorText(state.error, t)}</p>;
  const data = state.data;
  const ruleList = rules.data ?? [];
  const groupList = groups.data ?? [];
  const groupName = (id: number) => groupList.find((g) => g.id === id)?.name ?? `#${id}`;
  const ruleName = (id: number) => ruleLabel(ruleList.find((r) => r.id === id), id, groupName, t);

  return (
    <div className="stack">
      <div>
        <h2>{t("rewards.title")}</h2>
        <p className="muted">{t("rewards.intro")}</p>
      </div>
      <BalanceCard data={data} />
      <RequestsCard data={data} lang={lang} />
      <SwapsCard data={data} rules={ruleList} ruleName={ruleName} />
      <WishesCard data={data} lang={lang} />
      <LedgerCard data={data} lang={lang} ruleName={ruleName} />
    </div>
  );
}

function BalanceCard({ data }: { data: RewardsAdmin }) {
  const { t } = useTranslation();
  return (
    <section className="card stack" aria-labelledby="rewards-balance">
      <h3 id="rewards-balance">{t("rewards.balance")}</h3>
      <p>
        <strong>{t("rewards.now", { count: data.stars })}</strong>
      </p>
      <p className="muted">{t("rewards.earnedSpent", { earned: data.earned, spent: data.spent })}</p>
    </section>
  );
}

function RequestsCard({ data, lang }: { data: RewardsAdmin; lang: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "approve" | "decline" }) =>
      api<RewardsAdmin>("POST", `/api/admin/rewards/requests/${id}/${action}`),
    onSuccess: (state) => queryClient.setQueryData(REWARDS_KEY, state),
    // Someone else may have decided it, or the stars changed: show what is true now.
    onError: () => void queryClient.invalidateQueries({ queryKey: REWARDS_KEY }),
  });
  const wishes = new Map(data.wishes.map((w) => [w.id, w]));
  const pending = data.requests.filter((r) => r.status === "pending");
  const when = (iso: string) => new Date(iso).toLocaleString(lang, { dateStyle: "medium", timeStyle: "short" });

  return (
    <section className="card stack" aria-labelledby="rewards-requests">
      <h3 id="rewards-requests">{t("rewards.requests")}</h3>
      {pending.length === 0 && <p className="muted">{t("rewards.requestsNone")}</p>}
      {decide.error && (
        <p role="alert" className="error">
          {errorText(decide.error, t)}
        </p>
      )}
      {pending.map((r) => {
        const wish = wishes.get(r.wish_id);
        return (
          <div key={r.id} className="row spread wrap">
            <div className="grow">
              <strong>{wishName(wish, lang, r.wish_id)}</strong>
              <p className="muted small">
                {wish && `${t("rewards.starCount", { count: wish.stars })} · `}
                {t("rewards.requestedAt", { when: when(r.created_at) })}
              </p>
            </div>
            <div className="row">
              <button
                type="button"
                className="primary"
                disabled={decide.isPending}
                onClick={() => decide.mutate({ id: r.id, action: "approve" })}
              >
                {t("rewards.approve")}
              </button>
              <button type="button" disabled={decide.isPending} onClick={() => decide.mutate({ id: r.id, action: "decline" })}>
                {t("rewards.decline")}
              </button>
            </div>
          </div>
        );
      })}
    </section>
  );
}

function SwapsCard({ data, rules, ruleName }: { data: RewardsAdmin; rules: Rule[]; ruleName: (id: number) => string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<{ id: number | null; draft: SwapDraft } | null>(null);

  const changed = () => void queryClient.invalidateQueries({ queryKey: REWARDS_KEY });
  const toggle = useMutation({
    mutationFn: (s: Swap) => api<Swap>("PATCH", `/api/admin/rewards/swaps/${s.id}`, { enabled: !(s.enabled ?? true) }),
    onSuccess: changed,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api<void>("DELETE", `/api/admin/rewards/swaps/${id}`),
    onSuccess: changed,
  });

  const fresh = (): SwapDraft => ({
    rule_id: rules[0]?.id ?? null,
    kind: "items",
    amount: 1,
    stars: 5,
    max_per_day: 1,
    enabled: true,
  });
  const toDraft = (s: Swap): SwapDraft => ({
    rule_id: s.rule_id,
    kind: (s.extra_items ?? 0) > 0 ? "items" : "minutes",
    amount: (s.extra_items ?? 0) > 0 ? (s.extra_items ?? 0) : (s.extra_minutes ?? 0),
    stars: s.stars,
    max_per_day: s.max_per_day ?? null,
    enabled: s.enabled ?? true,
  });

  return (
    <section className="card stack" aria-labelledby="rewards-swaps">
      <div className="row spread">
        <h3 id="rewards-swaps">{t("rewards.swaps")}</h3>
        <button type="button" className="primary" onClick={() => setEditing({ id: null, draft: fresh() })}>
          {t("rewards.newSwap")}
        </button>
      </div>
      <p className="muted small">{t("rewards.swapsHelp")}</p>
      {editing && (
        <SwapEditor
          key={editing.id ?? "new"}
          id={editing.id}
          initial={editing.draft}
          rules={rules}
          ruleName={ruleName}
          onDone={() => {
            setEditing(null);
            changed();
          }}
        />
      )}
      {data.swaps.length === 0 && <p className="muted">{t("rewards.swapsNone")}</p>}
      {(toggle.error || remove.error) && <p className="error">{errorText(toggle.error ?? remove.error, t)}</p>}
      {data.swaps.map((s) => (
        <article key={s.id} className={s.enabled !== false ? "card" : "card off"}>
          <div className="row spread wrap">
            <div className="grow">
              <strong>
                {t("rewards.starCount", { count: s.stars })} → {swapGives(s, t)}
              </strong>
              <p className="muted small">
                {ruleName(s.rule_id)} ·{" "}
                {s.max_per_day == null ? t("rewards.perDayUnlimited") : t("rewards.perDay", { count: s.max_per_day })}
              </p>
            </div>
            <div className="row">
              <label className="check">
                <input type="checkbox" checked={s.enabled ?? true} onChange={() => toggle.mutate(s)} />
                {t("rewards.enabled")}
              </label>
              <button type="button" onClick={() => setEditing({ id: s.id, draft: toDraft(s) })}>
                {t("common.edit")}
              </button>
              <button
                type="button"
                className="danger"
                onClick={() => {
                  if (window.confirm(t("rewards.confirmDeleteSwap"))) remove.mutate(s.id);
                }}
              >
                {t("common.delete")}
              </button>
            </div>
          </div>
        </article>
      ))}
    </section>
  );
}

function SwapEditor({
  id,
  initial,
  rules,
  ruleName,
  onDone,
}: {
  id: number | null;
  initial: SwapDraft;
  rules: Rule[];
  ruleName: (id: number) => string;
  onDone: () => void;
}) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<SwapDraft>(initial);
  const set = (patch: Partial<SwapDraft>) => setDraft((d) => ({ ...d, ...patch }));
  // A new swap opened before the rules loaded takes the first rule once they are there.
  const ruleId = draft.rule_id ?? rules[0]?.id ?? null;
  const save = useMutation({
    mutationFn: () => {
      const body = swapBody({ ...draft, rule_id: ruleId });
      return id === null
        ? api<Swap>("POST", "/api/admin/rewards/swaps", body)
        : api<Swap>("PATCH", `/api/admin/rewards/swaps/${id}`, body);
    },
    onSuccess: onDone,
  });
  // The rule picker also offers the swap's current rule if the rules haven't loaded yet.
  const ruleIds = rules.map((r) => r.id);
  if (ruleId != null && !ruleIds.includes(ruleId)) ruleIds.push(ruleId);
  const incomplete = ruleId == null || !draft.stars || !draft.amount;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <form className="card stack editor" onSubmit={submit}>
      <p className="muted small">{t("rewards.swapExample")}</p>
      {ruleIds.length === 0 ? (
        <p className="error">{t("rewards.needRule")}</p>
      ) : (
        <label>
          {t("rewards.rule")}
          <select value={ruleId ?? ""} onChange={(e) => set({ rule_id: Number(e.target.value) })} required>
            {ruleIds.map((rid) => (
              <option key={rid} value={rid}>
                {ruleName(rid)}
              </option>
            ))}
          </select>
        </label>
      )}
      <fieldset>
        <legend>{t("rewards.gives")}</legend>
        <label className="check">
          <input
            type="radio"
            name="swap-kind"
            checked={draft.kind === "items"}
            onChange={() => set({ kind: "items", amount: 1 })}
          />
          {t("rewards.extraItems")}
        </label>
        <label className="check">
          <input
            type="radio"
            name="swap-kind"
            checked={draft.kind === "minutes"}
            onChange={() => set({ kind: "minutes", amount: 15 })}
          />
          {t("rewards.extraMinutes")}
        </label>
      </fieldset>
      <div className="row wrap">
        <label>
          {t("rewards.amount")}
          <input
            type="number"
            min={1}
            max={draft.kind === "items" ? 20 : 600}
            value={draft.amount ?? ""}
            onChange={(e) => set({ amount: numberOrNull(e.target.value) })}
            required
          />
        </label>
        <label>
          {t("rewards.cost")}
          <input
            type="number"
            min={1}
            max={500}
            value={draft.stars ?? ""}
            onChange={(e) => set({ stars: numberOrNull(e.target.value) })}
            required
          />
        </label>
        <label>
          {t("rewards.maxPerDay")}
          <input
            type="number"
            min={1}
            max={20}
            value={draft.max_per_day ?? ""}
            placeholder={t("rewards.unlimited")}
            onChange={(e) => set({ max_per_day: numberOrNull(e.target.value) || null })}
          />
        </label>
      </div>
      <label className="check">
        <input type="checkbox" checked={draft.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
        {t("rewards.enabled")}
      </label>
      {save.error && <p className="error">{errorText(save.error, t)}</p>}
      <div className="row">
        <button type="submit" className="primary" disabled={save.isPending || incomplete}>
          {t("common.save")}
        </button>
        <button type="button" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

function WishesCard({ data, lang }: { data: RewardsAdmin; lang: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<{ id: number | null; draft: WishDraft } | null>(null);

  const changed = () => void queryClient.invalidateQueries({ queryKey: REWARDS_KEY });
  const toggle = useMutation({
    mutationFn: (w: Wish) => api<Wish>("PATCH", `/api/admin/rewards/wishes/${w.id}`, { enabled: !(w.enabled ?? true) }),
    onSuccess: changed,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api<void>("DELETE", `/api/admin/rewards/wishes/${id}`),
    onSuccess: changed,
  });

  const toDraft = (w: Wish): WishDraft => {
    const texts = w.texts as Partial<Record<Lang, WishTexts>>;
    return {
      names: { de: texts.de?.name ?? "", en: texts.en?.name ?? "", el: texts.el?.name ?? "" },
      stars: w.stars,
      enabled: w.enabled ?? true,
    };
  };

  return (
    <section className="card stack" aria-labelledby="rewards-wishes">
      <div className="row spread">
        <h3 id="rewards-wishes">{t("rewards.wishes")}</h3>
        <button
          type="button"
          className="primary"
          onClick={() => setEditing({ id: null, draft: { ...EMPTY_WISH, names: { ...EMPTY_WISH.names } } })}
        >
          {t("rewards.newWish")}
        </button>
      </div>
      <p className="muted small">{t("rewards.wishesHelp")}</p>
      {editing && (
        <WishEditor
          key={editing.id ?? "new"}
          id={editing.id}
          initial={editing.draft}
          onDone={() => {
            setEditing(null);
            changed();
          }}
        />
      )}
      {data.wishes.length === 0 && <p className="muted">{t("rewards.wishesNone")}</p>}
      {(toggle.error || remove.error) && <p className="error">{errorText(toggle.error ?? remove.error, t)}</p>}
      {data.wishes.map((w) => {
        const name = wishName(w, lang, w.id);
        return (
          <article key={w.id} className={w.enabled !== false ? "card" : "card off"}>
            <div className="row spread wrap">
              <div className="grow">
                <strong>{name}</strong>
                <p className="muted small">{t("rewards.starCount", { count: w.stars })}</p>
              </div>
              <div className="row">
                <label className="check">
                  <input type="checkbox" checked={w.enabled ?? true} onChange={() => toggle.mutate(w)} />
                  {t("rewards.enabled")}
                </label>
                <button type="button" onClick={() => setEditing({ id: w.id, draft: toDraft(w) })}>
                  {t("common.edit")}
                </button>
                <button
                  type="button"
                  className="danger"
                  onClick={() => {
                    if (window.confirm(t("rewards.confirmDeleteWish", { name }))) remove.mutate(w.id);
                  }}
                >
                  {t("common.delete")}
                </button>
              </div>
            </div>
          </article>
        );
      })}
    </section>
  );
}

function WishEditor({ id, initial, onDone }: { id: number | null; initial: WishDraft; onDone: () => void }) {
  const { t } = useTranslation();
  const [draft, setDraft] = useState<WishDraft>(initial);
  const save = useMutation({
    mutationFn: () => {
      const body = wishBody(draft);
      return id === null
        ? api<Wish>("POST", "/api/admin/rewards/wishes", body)
        : api<Wish>("PATCH", `/api/admin/rewards/wishes/${id}`, body);
    },
    onSuccess: onDone,
  });
  const incomplete = !draft.stars || LANGS.every((l) => draft.names[l].trim() === "");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <form className="card stack editor" onSubmit={submit}>
      <fieldset>
        <legend>{t("rewards.wishName")}</legend>
        <p className="muted small">{t("rewards.wishNameHelp")}</p>
        {LANGS.map((l) => (
          <label key={l}>
            {t(`language.${l}`)}
            <input
              value={draft.names[l]}
              maxLength={60}
              placeholder={t("rewards.wishExample")}
              onChange={(e) => setDraft((d) => ({ ...d, names: { ...d.names, [l]: e.target.value } }))}
            />
          </label>
        ))}
      </fieldset>
      <label>
        {t("rewards.cost")}
        <input
          type="number"
          min={1}
          max={500}
          value={draft.stars ?? ""}
          onChange={(e) => setDraft((d) => ({ ...d, stars: numberOrNull(e.target.value) }))}
          required
        />
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={draft.enabled}
          onChange={(e) => setDraft((d) => ({ ...d, enabled: e.target.checked }))}
        />
        {t("rewards.enabled")}
      </label>
      {save.error && <p className="error">{errorText(save.error, t)}</p>}
      <div className="row">
        <button type="submit" className="primary" disabled={save.isPending || incomplete}>
          {t("common.save")}
        </button>
        <button type="button" onClick={onDone}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

function LedgerCard({ data, lang, ruleName }: { data: RewardsAdmin; lang: string; ruleName: (id: number) => string }) {
  const { t } = useTranslation();
  const swaps = new Map(data.swaps.map((s) => [s.id, s]));
  const wishes = new Map(data.wishes.map((w) => [w.id, w]));
  const requests = new Map(data.requests.map((r) => [r.id, r]));
  const when = (iso: string) => new Date(iso).toLocaleString(lang, { dateStyle: "medium", timeStyle: "short" });

  // ref_id is the swap for a swap, the wish request for a wish (SPEC §14.5); either may be gone.
  const what = (kind: "swap" | "wish", ref: number | null): string => {
    if (kind === "swap") {
      const s = ref == null ? undefined : swaps.get(ref);
      return s ? `${t("rewards.ledgerSwap")}: ${ruleName(s.rule_id)}, ${swapGives(s, t)}` : t("rewards.ledgerSwap");
    }
    const r = ref == null ? undefined : requests.get(ref);
    const w = r && wishes.get(r.wish_id);
    return w ? `${t("rewards.ledgerWish")}: ${wishName(w, lang, w.id)}` : t("rewards.ledgerWish");
  };

  return (
    <section className="card stack" aria-labelledby="rewards-ledger">
      <h3 id="rewards-ledger">{t("rewards.ledger")}</h3>
      {data.ledger.length === 0 ? (
        <p className="muted">{t("rewards.ledgerNone")}</p>
      ) : (
        <table className="log">
          <thead>
            <tr>
              <th scope="col">{t("rewards.ledgerWhen")}</th>
              <th scope="col">{t("rewards.ledgerWhat")}</th>
              <th scope="col">{t("rewards.ledgerStars")}</th>
            </tr>
          </thead>
          <tbody>
            {data.ledger.map((e) => (
              <tr key={e.id}>
                <td>{when(e.created_at)}</td>
                <td className="grow">{what(e.kind, e.ref_id)}</td>
                <td>{e.delta < 0 ? `−${-e.delta}` : `+${e.delta}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
