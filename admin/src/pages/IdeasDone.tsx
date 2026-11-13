import { useInfiniteQuery, useMutation, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api, type DoneEntry, type DonePage } from "../api/client";
import { errorText } from "../lib/errors";
import { DONE_KEY, IDEAS_KEY } from "../lib/ideas";
import type { Translate } from "../lib/translate";
import { Modal } from "./Pickers";
import { REWARDS_KEY } from "./Rewards";

function donePath(beforeId: number | null): string {
  return beforeId == null ? "/api/admin/activities/done" : `/api/admin/activities/done?before_id=${beforeId}`;
}

/** The cached pages with one entry changed (or removed with `null`). */
function withEntry(data: InfiniteData<DonePage> | undefined, id: number, next: DoneEntry | null) {
  if (!data) return data;
  return {
    ...data,
    pages: data.pages.map((page) => ({
      ...page,
      entries: page.entries.flatMap((e) => (e.id !== id ? [e] : next ? [next] : [])),
    })),
  };
}

/** Draw and write are checked with a photo (SPEC §15.3). */
const PHOTO_KINDS: readonly string[] = ["draw", "write"];

/** "all right first time" / "3 tries" for calc, the rounds through the questions for read (SPEC §17.2). */
function triesText(entry: DoneEntry, t: Translate): string | null {
  if (entry.attempts == null) return null;
  if (entry.kind === "calc") return entry.attempts === 1 ? t("ideas.calcFirstTry") : t("ideas.calcTries", { count: entry.attempts });
  if (entry.kind === "read") return t("ideas.attempts", { count: entry.attempts });
  return null;
}

/** Every done task, newest first, with its pictures, a bonus star and "Open again" (SPEC §17.2). */
export function DoneCard() {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en";
  const [open, setOpen] = useState<number | null>(null);
  const list = useInfiniteQuery({
    queryKey: DONE_KEY,
    queryFn: ({ pageParam }) => api<DonePage>("GET", donePath(pageParam)),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_before_id ?? undefined,
  });
  const entries = list.data?.pages.flatMap((page) => page.entries) ?? [];
  const opened = open != null ? entries.find((e) => e.photos.some((p) => p.id === open)) : undefined;
  const openedPhoto = opened?.photos.find((p) => p.id === open);
  const when = (iso: string) =>
    new Date(iso).toLocaleString(lang, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const caption = (e: DoneEntry) => `${t("ideas.chapterOf", { n: e.position, world: e.world_name })} · ${t(`ideas.kind.${e.kind}`)}`;

  return (
    <section className="card stack" aria-labelledby="ideas-done">
      <div className="row spread wrap">
        <h3 id="ideas-done">{t("ideas.done")}</h3>
        <span className="muted small">{t("ideas.doneHelp")}</span>
      </div>
      {list.isPending && <p className="muted">{t("common.loading")}</p>}
      {list.isError && <p className="error">{errorText(list.error, t)}</p>}
      {list.isSuccess && entries.length === 0 && <p className="muted">{t("ideas.noDone")}</p>}
      {entries.length > 0 && (
        <ul className="done-list stack">
          {entries.map((e) => {
            const tries = triesText(e, t);
            return (
              <li key={e.id} className="row wrap done-entry">
                {e.photos.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    className="thumb photo"
                    aria-label={t("ideas.openPicture", { what: `${caption(e)} · ${e.task_title}` })}
                    onClick={() => setOpen(p.id)}
                  >
                    <img src={p.url_sm} alt="" loading="lazy" />
                  </button>
                ))}
                <div className="stack grow">
                  <strong>{e.task_title}</strong>
                  <span className="muted small">
                    {when(e.done_at)} · {caption(e)}
                  </span>
                  <span className="small">
                    {[
                      tries,
                      t("rewards.starCount", { count: e.stars }),
                      e.photos.length === 0 && PHOTO_KINDS.includes(e.kind) ? t("ideas.noPicture") : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                  <DoneActions entry={e} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {list.hasNextPage && (
        <div className="row">
          <button type="button" disabled={list.isFetchingNextPage} onClick={() => void list.fetchNextPage()}>
            {t("ideas.loadMore")}
          </button>
        </div>
      )}
      {opened && openedPhoto && (
        <Modal title={opened.task_title} onClose={() => setOpen(null)}>
          <img className="photo-lg" src={openedPhoto.url_lg} alt={`${caption(opened)} · ${opened.task_title}`} />
          <p className="muted small">
            {when(openedPhoto.taken_at)} · {caption(opened)}
          </p>
        </Modal>
      )}
    </section>
  );
}

function DoneActions({ entry }: { entry: DoneEntry }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  // The star balance and the log change with both (SPEC §17.3).
  const changed = () => {
    void queryClient.invalidateQueries({ queryKey: REWARDS_KEY });
    void queryClient.invalidateQueries({ queryKey: IDEAS_KEY });
  };
  const bonus = useMutation({
    mutationFn: () => api<DoneEntry>("POST", `/api/admin/activities/done/${entry.id}/bonus`),
    onSuccess: (next) => {
      queryClient.setQueryData(DONE_KEY, (data: InfiniteData<DonePage> | undefined) => withEntry(data, entry.id, next));
      changed();
    },
    // 409 bonus_given: given already (another tab); reloading shows it as given.
    onError: () => void queryClient.invalidateQueries({ queryKey: DONE_KEY }),
  });
  const reopen = useMutation({
    mutationFn: () => api<void>("DELETE", `/api/admin/activities/done/${entry.id}`),
    onSuccess: () => {
      queryClient.setQueryData(DONE_KEY, (data: InfiniteData<DonePage> | undefined) => withEntry(data, entry.id, null));
      changed();
    },
    // 404 unknown_done: opened again already (another tab); reloading drops it.
    onError: () => void queryClient.invalidateQueries({ queryKey: DONE_KEY }),
  });
  const given = entry.bonus || bonus.isSuccess;
  const confirmText = t(entry.photos.length > 0 ? "ideas.confirmOpenAgainPicture" : "ideas.confirmOpenAgain", {
    count: entry.stars,
  });
  return (
    <div className="stack">
      <div className="row wrap">
        {given ? (
          <span role="status" className="ok small">
            {t("ideas.bonusGiven")}
          </span>
        ) : (
          <button type="button" className="small" disabled={bonus.isPending} onClick={() => bonus.mutate()}>
            {t("ideas.giveBonus")}
          </button>
        )}
        <button
          type="button"
          className="danger small"
          disabled={reopen.isPending}
          onClick={() => {
            if (window.confirm(confirmText)) reopen.mutate();
          }}
        >
          {t("ideas.openAgain")}
        </button>
      </div>
      {bonus.error && <p className="error small">{errorText(bonus.error, t)}</p>}
      {reopen.error && <p className="error small">{errorText(reopen.error, t)}</p>}
    </div>
  );
}
