import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  api,
  thumbSrc,
  type ActivitiesAdmin,
  type ImportReport,
  type Level,
  type SettingsIn,
  type SpeechStatus,
  type WebDavPacks,
  type Window,
} from "../api/client";
import { errorText } from "../lib/errors";
import { WEEKDAYS } from "../lib/format";
import {
  IDEAS_KEY,
  LEVELS,
  SPEECH_KEY,
  SPEECH_REFRESH_MS,
  defaultStoryLang,
  move,
  newWindow,
  validWindow,
  worldLangs,
  worldName,
  type Lang,
} from "../lib/ideas";
import { DoneCard } from "./IdeasDone";
import { ImportCard, PackError, PackReport, WorldEditor, WorldList } from "./IdeasWorlds";

/** Off-screen ideas: story worlds whose chapters open by doing ideas (SPEC §13.2, §13.4, §13.7, §14). */
export default function Ideas() {
  const { t, i18n } = useTranslation();
  const state = useQuery({ queryKey: IDEAS_KEY, queryFn: () => api<ActivitiesAdmin>("GET", "/api/admin/activities") });
  const [editing, setEditing] = useState<number | null>(null);
  const lang = i18n.resolvedLanguage ?? "en";

  if (state.isPending) return <p className="muted">{t("common.loading")}</p>;
  if (state.isError) return <p className="error">{errorText(state.error, t)}</p>;
  const data = state.data;

  return (
    <div className="stack">
      <div>
        <h2>{t("ideas.title")}</h2>
        <p className="muted">{t("ideas.intro")}</p>
      </div>
      <ProgressCard data={data} />
      <ChildCard data={data} />
      <QueueCard data={data} lang={lang} />
      <div className="split">
        <aside className="sidebar stack">
          <WorldList data={data} selected={editing} onSelect={setEditing} />
          <ImportCard onImported={(id) => setEditing(id)} />
          <ExamplesCard data={data} lang={lang} onImported={(id) => setEditing(id)} />
          {data.webdav && <WebDavCard onImported={(id) => setEditing(id)} />}
        </aside>
        <section className="grow stack">
          {editing != null && data.worlds.some((w) => w.id === editing) ? (
            <WorldEditor
              key={editing}
              worldId={editing}
              warnings={data.worlds.find((w) => w.id === editing)?.warnings}
              onClose={() => setEditing(null)}
            />
          ) : (
            <p className="card muted">{t("ideas.pickWorld")}</p>
          )}
        </section>
      </div>
      <SpeechCard />
      <DoneCard />
      <LogCard data={data} lang={lang} />
    </div>
  );
}

const MAX_WINDOWS = 14; // SPEC §14.4a, as the server
const NEEDED = [1, 2, 3, 4]; // ideas per chapter (SPEC §13.2); a chapter has at most four
const CHAPTER_STARS = Array.from({ length: 11 }, (_, i) => i); // stars for a finished chapter (SPEC §14.4)
const PER_DAY = Array.from({ length: 12 }, (_, i) => i + 1); // new chapters per day, as the server
const JOY_URL = "https://huggingface.co/Efso/joy-greek-tts";
// Not in the public image (non-commercial licence); the hint to add JOY by hand shows while Greek uses another voice.
const JOY_ID = "el_GR-joy-medium";

/** Mutations that answer with the whole state write it straight into the cache. */
function useStateMutation<V>(fn: (v: V) => Promise<ActivitiesAdmin>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => queryClient.setQueryData(IDEAS_KEY, data),
  });
}

function dayLabel(day: string, lang: string): string {
  return new Date(`${day}T12:00:00`).toLocaleDateString(lang, { weekday: "short", day: "numeric", month: "short" });
}

/**
 * How the story moves on (SPEC §13.2): ideas to do per chapter, and new chapters per day; and
 * the stars for a chapter with every idea done (§14.4).
 */
function ProgressCard({ data }: { data: ActivitiesAdmin }) {
  const { t } = useTranslation();
  const { chapter_needed, max_new_per_day, chapter_stars } = data.settings;
  const starChoices = CHAPTER_STARS.includes(chapter_stars) ? CHAPTER_STARS : [...CHAPTER_STARS, chapter_stars];
  const save = useStateMutation((body: SettingsIn) => api<ActivitiesAdmin>("PUT", "/api/admin/activities/settings", body));

  return (
    <section className="card stack" aria-labelledby="ideas-progress">
      <div className="row spread wrap">
        <h3 id="ideas-progress">{t("ideas.progress")}</h3>
        <span className="muted small">{t("ideas.progressHelp")}</span>
      </div>
      <div className="row wrap top">
        <fieldset className="grow">
          <legend>{t("ideas.needed")}</legend>
          <div className="row wrap">
            {NEEDED.map((n) => (
              <button
                key={n}
                type="button"
                className={n === chapter_needed ? "pill on" : "pill"}
                aria-pressed={n === chapter_needed}
                disabled={save.isPending}
                onClick={() => {
                  if (n !== chapter_needed) save.mutate({ chapter_needed: n });
                }}
              >
                {n}
              </button>
            ))}
          </div>
          <p className="muted small">{t("ideas.neededHelp", { count: chapter_needed })}</p>
        </fieldset>
        <fieldset className="grow">
          <legend>{t("ideas.perDay")}</legend>
          <select
            aria-label={t("ideas.perDay")}
            value={max_new_per_day ?? ""}
            disabled={save.isPending}
            onChange={(e) => save.mutate({ max_new_per_day: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">{t("ideas.noLimit")}</option>
            {PER_DAY.map((n) => (
              <option key={n} value={n}>
                {t("ideas.perDayValue", { count: n })}
              </option>
            ))}
          </select>
          <p className="muted small">
            {max_new_per_day == null ? t("ideas.noLimitHelp") : t("ideas.perDayHelp", { count: max_new_per_day })}
          </p>
        </fieldset>
        <fieldset className="grow">
          <legend>{t("ideas.chapterStars")}</legend>
          <select
            aria-label={t("ideas.chapterStars")}
            value={chapter_stars}
            disabled={save.isPending}
            onChange={(e) => save.mutate({ chapter_stars: Number(e.target.value) })}
          >
            {starChoices.map((n) => (
              <option key={n} value={n}>
                {n === 0 ? t("ideas.chapterStarsNone") : t("rewards.starCount", { count: n })}
              </option>
            ))}
          </select>
          <p className="muted small">
            {chapter_stars === 0 ? t("ideas.chapterStarsNoneHelp") : t("ideas.chapterStarsHelp", { count: chapter_stars })}
          </p>
        </fieldset>
      </div>
      {save.error && <p className="error">{errorText(save.error, t)}</p>}
    </section>
  );
}

/** The child's level (SPEC §14.1) and the opening times of Ideas (SPEC §14.4a). */
function ChildCard({ data }: { data: ActivitiesAdmin }) {
  const { t } = useTranslation();
  const { level } = data.settings;
  const saved = data.settings.windows ?? [];
  const [windows, setWindows] = useState<Window[]>(saved);
  const save = useStateMutation((body: SettingsIn) => api<ActivitiesAdmin>("PUT", "/api/admin/activities/settings", body));
  const dirty = JSON.stringify(windows) !== JSON.stringify(saved);
  const valid = windows.every(validWindow);
  const setWindow = (i: number, next: Window) => setWindows(windows.map((w, j) => (j === i ? next : w)));
  const toggleDay = (i: number, d: number) => {
    const w = windows[i];
    const next = w.days.includes(d) ? w.days.filter((x) => x !== d) : [...w.days, d].sort((a, b) => a - b);
    setWindow(i, { ...w, days: next });
  };

  return (
    <section className="card stack" aria-labelledby="ideas-child">
      <div className="row spread wrap">
        <h3 id="ideas-child">{t("ideas.child")}</h3>
        <span className="muted small">{t("ideas.childHelp")}</span>
      </div>
      <div className="row wrap top">
        <fieldset className="grow">
          <legend>{t("ideas.levelPicker")}</legend>
          <div className="row wrap">
            {LEVELS.map((l: Level) => (
              <button
                key={l}
                type="button"
                className={l === level ? "pill on" : "pill"}
                aria-pressed={l === level}
                disabled={save.isPending}
                onClick={() => {
                  if (l !== level) save.mutate({ level: l });
                }}
              >
                {t(`ideas.level.${l}`)}
              </button>
            ))}
          </div>
          <p className="muted small">{t(`ideas.levelHelp.${level ?? "normal"}`)}</p>
        </fieldset>
        <fieldset className="grow">
          <legend>{t("ideas.openingTimes")}</legend>
          <p className="muted small">{t("ideas.openingHelp")}</p>
          {windows.length === 0 && <p className="ok">{t("ideas.alwaysOpen")}</p>}
          <ul className="plainlist">
            {windows.map((w, i) => (
              <li key={i} className="row wrap">
                <div className="row wrap" role="group" aria-label={t("ideas.windowDays", { n: i + 1 })}>
                  {WEEKDAYS.map((d) => (
                    <button
                      key={d}
                      type="button"
                      className={w.days.includes(d) ? "pill on" : "pill"}
                      aria-pressed={w.days.includes(d)}
                      onClick={() => toggleDay(i, d)}
                    >
                      {t(`days.short.${d}`)}
                    </button>
                  ))}
                </div>
                <label className="check">
                  {t("ideas.windowFrom")}
                  <input type="time" value={w.from} onChange={(e) => setWindow(i, { ...w, from: e.target.value })} required />
                </label>
                <label className="check">
                  {t("ideas.windowTo")}
                  <input type="time" value={w.to} onChange={(e) => setWindow(i, { ...w, to: e.target.value })} required />
                </label>
                <button
                  type="button"
                  className="icon danger"
                  aria-label={t("ideas.removeWindow", { n: i + 1 })}
                  onClick={() => setWindows(windows.filter((_, j) => j !== i))}
                >
                  ✕
                </button>
                {!validWindow(w) && (
                  <span className="error small">{w.days.length === 0 ? t("ideas.windowNoDays") : t("ideas.windowOrder")}</span>
                )}
              </li>
            ))}
          </ul>
          <div className="row wrap">
            <button type="button" className="small" disabled={windows.length >= MAX_WINDOWS} onClick={() => setWindows([...windows, newWindow()])}>
              {t("ideas.addWindow")}
            </button>
            <button
              type="button"
              className="primary small"
              disabled={!dirty || !valid || save.isPending}
              onClick={() => save.mutate({ windows })}
            >
              {t("ideas.saveWindows")}
            </button>
            {dirty && (
              <button type="button" className="small" onClick={() => setWindows(saved)}>
                {t("ideas.discard")}
              </button>
            )}
          </div>
        </fieldset>
      </div>
      {save.error && <p className="error">{errorText(save.error, t)}</p>}
    </section>
  );
}

function QueueCard({ data, lang }: { data: ActivitiesAdmin; lang: string }) {
  const { t } = useTranslation();
  const [adding, setAdding] = useState("");
  const [addLang, setAddLang] = useState<Lang | "">("");
  // The story language per queued world travels with the queue (SPEC §14.7).
  const save = useStateMutation(({ world_ids, langs }: { world_ids: number[]; langs?: Record<string, Lang> }) =>
    api<ActivitiesAdmin>("PUT", "/api/admin/activities/queue", langs ? { world_ids, langs } : { world_ids }),
  );
  const ids = data.queue.map((q) => q.world_id);
  const byId = new Map(data.worlds.map((w) => [w.id, w]));
  const notQueued = data.worlds.filter((w) => !ids.includes(w.id));
  const added = adding ? byId.get(Number(adding)) : undefined;
  const addLangs = added ? worldLangs(added) : [];
  const chosenAddLang = added ? (addLang && addLangs.includes(addLang) ? addLang : defaultStoryLang(added, lang)) : "";

  return (
    <section className="card stack" aria-labelledby="ideas-queue">
      <div className="row spread wrap">
        <h3 id="ideas-queue">{t("ideas.queue")}</h3>
        <span className="muted small">{t("ideas.queueHelp")}</span>
      </div>
      {data.queue.length === 0 && <p className="muted">{t("ideas.queueEmpty")}</p>}
      <ol className="queue">
        {data.queue.map((q, i) => {
          const w = byId.get(q.world_id);
          const name = w ? worldName(w, lang) : `#${q.world_id}`;
          return (
            <li key={q.world_id} className={q.status === "running" ? "queueitem running" : "queueitem"}>
              <span className="swatch small-swatch" style={{ background: w?.color }} aria-hidden="true">
                {w?.cover && <img src={thumbSrc(w.cover)} alt="" />}
              </span>
              <strong className="grow">{name}</strong>
              <span className="muted">
                {q.status === "upcoming"
                  ? t("ideas.upcoming")
                  : q.story_done
                    ? t("ideas.storyDone")
                    : t("ideas.chaptersOpen", { open: q.chapters_open, total: q.chapters })}
              </span>
              <select
                aria-label={t("ideas.storyLangFor", { name })}
                title={t("ideas.storyLang")}
                value={q.lang}
                disabled={save.isPending}
                onChange={(e) => save.mutate({ world_ids: ids, langs: { [q.world_id]: e.target.value as Lang } })}
              >
                {q.langs.map((l) => (
                  <option key={l} value={l}>
                    {t(`ideas.lang.${l}`)}
                  </option>
                ))}
              </select>
              <span className={q.status === "running" ? "badge running" : w?.complete ? "badge ready" : "badge draft"}>
                {q.status === "running" ? t("ideas.running") : w?.complete ? t("ideas.ready") : t("ideas.draft")}
              </span>
              <button
                type="button"
                className="icon"
                aria-label={t("ideas.moveUp", { name })}
                disabled={i === 0 || save.isPending}
                onClick={() => save.mutate({ world_ids: move(ids, i, -1) })}
              >
                ↑
              </button>
              <button
                type="button"
                className="icon"
                aria-label={t("ideas.moveDown", { name })}
                disabled={i === ids.length - 1 || save.isPending}
                onClick={() => save.mutate({ world_ids: move(ids, i, 1) })}
              >
                ↓
              </button>
              <button
                type="button"
                className="icon danger"
                aria-label={t("ideas.unqueue", { name })}
                disabled={save.isPending}
                onClick={() => save.mutate({ world_ids: ids.filter((id) => id !== q.world_id) })}
              >
                ✕
              </button>
            </li>
          );
        })}
      </ol>
      {notQueued.length > 0 && (
        <div className="row wrap">
          <label className="check">
            {t("ideas.addToQueue")}
            <select
              value={adding}
              onChange={(e) => {
                setAdding(e.target.value);
                setAddLang("");
              }}
            >
              <option value="">{t("ideas.chooseWorld")}</option>
              {notQueued.map((w) => (
                <option key={w.id} value={w.id}>
                  {worldName(w, lang)}
                </option>
              ))}
            </select>
          </label>
          {added && (
            <label className="check">
              {t("ideas.storyLang")}
              <select value={chosenAddLang} onChange={(e) => setAddLang(e.target.value as Lang)}>
                {addLangs.map((l) => (
                  <option key={l} value={l}>
                    {t(`ideas.lang.${l}`)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <button
            type="button"
            disabled={!adding || save.isPending}
            onClick={() => {
              const world_ids = [...ids, Number(adding)];
              save.mutate(chosenAddLang ? { world_ids, langs: { [adding]: chosenAddLang } } : { world_ids });
              setAdding("");
              setAddLang("");
            }}
          >
            {t("ideas.add")}
          </button>
        </div>
      )}
      {save.error && <p className="error">{errorText(save.error, t)}</p>}
    </section>
  );
}

function ExamplesCard({ data, lang, onImported }: { data: ActivitiesAdmin; lang: string; onImported: (id: number) => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const add = useMutation({
    mutationFn: (slug: string) => api<{ world_id: number | null }>("POST", `/api/admin/worlds/examples/${slug}`),
    onSuccess: (report) => {
      void queryClient.invalidateQueries({ queryKey: IDEAS_KEY });
      if (report.world_id != null) onImported(report.world_id);
    },
  });
  const open = data.examples.filter((e) => !e.imported);
  if (open.length === 0) return null;
  return (
    <section className="card stack" aria-labelledby="ideas-examples">
      <h3 id="ideas-examples">{t("ideas.examples")}</h3>
      <p className="muted small">{t("ideas.examplesHelp")}</p>
      <ul className="plainlist">
        {open.map((e) => (
          <li key={e.slug} className="row spread">
            <span>{worldName(e, lang)}</span>
            <button type="button" className="small" disabled={add.isPending} onClick={() => add.mutate(e.slug)}>
              {t("ideas.add")}
            </button>
          </li>
        ))}
      </ul>
      {add.error && <p className="error">{errorText(add.error, t)}</p>}
    </section>
  );
}

const WEBDAV_KEY = ["webdav-packs"];

/** Pack zips on the WebDAV folder set in .env (SPEC §13.5a): check one, then import it. */
function WebDavCard({ onImported }: { onImported: (id: number) => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [chosen, setChosen] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const packs = useQuery({
    queryKey: WEBDAV_KEY,
    queryFn: () => api<WebDavPacks>("GET", "/api/admin/worlds/webdav"),
  });
  const path = (name: string, dryRun: boolean) =>
    `/api/admin/worlds/webdav/import?name=${encodeURIComponent(name)}&dry_run=${dryRun ? "true" : "false"}`;
  const check = useMutation({
    mutationFn: (name: string) => api<ImportReport>("POST", path(name, true)),
  });
  const run = useMutation({
    mutationFn: (name: string) => api<ImportReport>("POST", path(name, false)),
    onSuccess: (report, name) => {
      setChosen(null);
      setDone(name);
      check.reset();
      void queryClient.invalidateQueries({ queryKey: IDEAS_KEY });
      if (report.world_id != null) {
        void queryClient.invalidateQueries({ queryKey: ["world", report.world_id] });
        onImported(report.world_id);
      }
    },
  });
  const busy = check.isPending || run.isPending;
  return (
    <section className="card stack" aria-labelledby="ideas-webdav">
      <div className="row spread wrap">
        <h3 id="ideas-webdav">{t("ideas.webdav")}</h3>
        <button type="button" className="small" disabled={packs.isFetching} onClick={() => void packs.refetch()}>
          {t("ideas.webdavRefresh")}
        </button>
      </div>
      <p className="muted small">{t("ideas.webdavHelp")}</p>
      {packs.isPending && <p className="muted">{t("common.loading")}</p>}
      {packs.isError && <p className="error">{errorText(packs.error, t)}</p>}
      {packs.data && packs.data.packs.length === 0 && <p className="muted">{t("ideas.webdavEmpty")}</p>}
      {packs.data && packs.data.packs.length > 0 && (
        <ul className="plainlist">
          {packs.data.packs.map((p) => (
            <li key={p.name} className="stack">
              <div className="row spread">
                <span>
                  {p.name}
                  {p.size != null && <span className="muted small"> · {t("ideas.webdavSize", { mb: (p.size / 1048576).toFixed(1) })}</span>}
                </span>
                <button
                  type="button"
                  className="small"
                  disabled={busy}
                  onClick={() => {
                    setChosen(p.name);
                    setDone(null);
                    run.reset();
                    check.mutate(p.name);
                  }}
                >
                  {t("ideas.webdavCheck")}
                </button>
              </div>
              {chosen === p.name && check.isPending && <p className="muted">{t("ideas.checking")}</p>}
              {chosen === p.name && check.data && (
                <PackReport
                  report={check.data}
                  pending={run.isPending}
                  onImport={() => run.mutate(p.name)}
                  onCancel={() => {
                    setChosen(null);
                    check.reset();
                  }}
                />
              )}
              {chosen === p.name && <PackError error={check.error ?? run.error} />}
              {done === p.name && (
                <p role="status" className="ok">
                  {t("ideas.imported")}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Read aloud on the server: the voice per language and how far the files are (SPEC §14.3). */
function SpeechCard() {
  const { t } = useTranslation();
  const speech = useQuery({
    queryKey: SPEECH_KEY,
    queryFn: () => api<SpeechStatus>("GET", "/api/admin/speech"),
    refetchInterval: (query) => ((query.state.data?.pending ?? 0) > 0 ? SPEECH_REFRESH_MS : false),
  });
  return (
    <section className="card stack" aria-labelledby="ideas-speech">
      <div className="row spread wrap">
        <h3 id="ideas-speech">{t("ideas.speech")}</h3>
        <span className="muted small">{t("ideas.speechHelp")}</span>
      </div>
      {speech.isPending && <p className="muted">{t("common.loading")}</p>}
      {speech.isError && <p className="error">{errorText(speech.error, t)}</p>}
      {speech.data && (
        <>
          <table className="log">
            <thead>
              <tr>
                <th scope="col">{t("ideas.speechLang")}</th>
                <th scope="col">{t("ideas.speechVoice")}</th>
                <th scope="col">{t("ideas.speechSource")}</th>
                <th scope="col">{t("ideas.speechLicense")}</th>
              </tr>
            </thead>
            <tbody>
              {speech.data.voices.map((v) => (
                <tr key={v.lang}>
                  <td>{t(`ideas.lang.${v.lang}`)}</td>
                  <td>{v.voice ?? <span className="muted">{t("ideas.speechNoVoice")}</span>}</td>
                  <td>{v.source ? t(`ideas.speechFrom.${v.source}`) : ""}</td>
                  <td className="grow">
                    {v.license}
                    {v.attribution && <div className="muted small">{v.attribution}</div>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p role="status">
            {t("ideas.speechCounts", {
              rendered: speech.data.rendered,
              pending: speech.data.pending,
              failed: speech.data.failed,
            })}
          </p>
          {!speech.data.worker && <p className="muted small">{t("ideas.speechWorkerOff")}</p>}
        </>
      )}
      {speech.data && !speech.data.voices.some((v) => v.voice === JOY_ID) && (
        <p className="muted small">
          {t("ideas.speechJoy", { folder: "/opt/kidsmedia/data/voices/" })}{" "}
          <a href={JOY_URL} target="_blank" rel="noreferrer">
            {JOY_URL}
          </a>
        </p>
      )}
    </section>
  );
}

/** The day by day log; the pictures are with their task in Done activities (SPEC §17.2). */
function LogCard({ data, lang }: { data: ActivitiesAdmin; lang: string }) {
  const { t } = useTranslation();
  const byId = new Map(data.worlds.map((w) => [w.id, w]));
  const time = (iso: string) => new Date(iso).toLocaleTimeString(lang, { timeStyle: "short" });
  return (
    <section className="card stack" aria-labelledby="ideas-log">
      <h3 id="ideas-log">{t("ideas.log")}</h3>
      {data.log.length === 0 ? (
        <p className="muted">{t("ideas.logEmpty")}</p>
      ) : (
        <table className="log">
          <thead>
            <tr>
              <th scope="col">{t("ideas.logDay")}</th>
              <th scope="col">{t("ideas.logChapter")}</th>
              <th scope="col">{t("ideas.logDone")}</th>
              <th scope="col">{t("ideas.logAttempts")}</th>
              <th scope="col">{t("ideas.logAt")}</th>
            </tr>
          </thead>
          <tbody>
            {data.log.map((r) => {
              const w = byId.get(r.world_id);
              return (
                <tr key={`${r.date}-${r.world_id}-${r.position}`}>
                  <td>{dayLabel(r.date, lang)}</td>
                  <td className="grow">
                    {t("ideas.chapterOf", { n: r.position, world: w ? worldName(w, lang) : `#${r.world_id}` })}
                  </td>
                  <td>{r.kinds.map((k) => t(`ideas.kind.${k}`)).join(", ")}</td>
                  <td>
                    {[
                      r.attempts != null ? t("ideas.attempts", { count: r.attempts }) : null,
                      r.calc_attempts != null
                        ? r.calc_attempts === 1
                          ? t("ideas.calcFirstTry")
                          : t("ideas.calcTries", { count: r.calc_attempts })
                        : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </td>
                  <td>{time(r.done_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
