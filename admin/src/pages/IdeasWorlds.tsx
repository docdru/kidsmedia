import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  ApiError,
  api,
  putImage,
  thumbSrc,
  type ActivitiesAdmin,
  type EditorTask,
  type EditorTaskTexts,
  type ImportReport,
  type Level,
  type PackWarning,
  type Problem,
  type ProblemAudio,
  type Question,
  type QuestionAudio,
  type TaskKind,
  type WorldChapter,
  type WorldFull,
} from "../api/client";
import { errorText } from "../lib/errors";
import {
  EDIT_LEVELS,
  IDEAS_KEY,
  KINDS,
  LANGS,
  LEVEL_FIELDS,
  MAX_ANSWER,
  MAX_CHAPTERS,
  MAX_PROBLEMS,
  MAX_QUESTIONS,
  SPEECH_KEY,
  chapterImage,
  contentLang,
  emptyChapter,
  emptyProblem,
  emptyQuestion,
  emptyTask,
  move,
  parseAnswer,
  parseMaterials,
  problemUnfinished,
  unfinishedProblems,
  sortedTasks,
  toPack,
  warningText,
  withChapterImage,
  worldName,
  type Lang,
} from "../lib/ideas";
import { ImageInput } from "./Pickers";

type TaskTexts = EditorTaskTexts;
/** The spoken form of a text block's fields (SPEC §14.3): `say[field]`, used only for audio. */
type Say = Record<string, string>;

function sayOf(block: { say?: unknown } | undefined): Say {
  return (block?.say ?? {}) as Say;
}

/** The rendered audio of a text block's fields (output only, SPEC §14.3); a field without audio has no entry. */
function audioOf(block: { audio?: unknown } | undefined): Record<string, string> {
  return (block?.audio ?? {}) as Record<string, string>;
}

let playing: HTMLAudioElement | null = null;

/** Play a rendered text (GET /api/admin/speech/<key>.m4a, the admin session authorizes it); one at a time. */
function playAudio(url: string): void {
  playing?.pause();
  const audio = new Audio(url);
  playing = audio;
  const started = audio.play() as Promise<void> | undefined;
  started?.catch(() => undefined);
}

export function PlayButton({ url, label }: { url: string; label: string }) {
  return (
    <button type="button" className="icon" aria-label={label} title={label} onClick={() => playAudio(url)}>
      ▶
    </button>
  );
}

/** The optional pronunciation of one spoken field, plus a play button once its audio is rendered. */
function SpokenForm({
  field,
  label,
  say,
  audio,
  onChange,
}: {
  field: string;
  label: string;
  say: Say;
  audio: Record<string, string>;
  onChange: (say: Say) => void;
}) {
  const { t } = useTranslation();
  const url: string | undefined = audio[field];
  return (
    <div className="row wrap say">
      <label className="grow small muted">
        {t("ideas.say")}
        <input
          aria-label={t("ideas.sayFor", { field: label })}
          value={say[field] ?? ""}
          maxLength={900}
          placeholder={t("ideas.sayPlaceholder")}
          onChange={(e) => onChange({ ...say, [field]: e.target.value })}
        />
      </label>
      {url && <PlayButton url={url} label={t("ideas.play", { field: label })} />}
    </div>
  );
}

const PACK_TYPES = "application/zip,.zip,application/json,.json";
const AGE_BANDS = ["4-5", "6-7", "8-9"] as const;

export function WorldList({
  data,
  selected,
  onSelect,
}: {
  data: ActivitiesAdmin;
  selected: number | null;
  onSelect: (id: number) => void;
}) {
  const { t, i18n } = useTranslation();
  const lang = i18n.resolvedLanguage ?? "en";
  return (
    <section className="card stack" aria-labelledby="ideas-worlds">
      <h3 id="ideas-worlds">{t("ideas.worlds")}</h3>
      {data.worlds.length === 0 && <p className="muted small">{t("ideas.noWorlds")}</p>}
      <ul className="grouplist">
        {data.worlds.map((w) => (
          <li key={w.id}>
            <button
              type="button"
              className={w.id === selected ? "groupbtn active" : "groupbtn"}
              aria-pressed={w.id === selected}
              onClick={() => onSelect(w.id)}
            >
              <span className="swatch" style={{ background: w.color }}>
                {w.cover && <img src={thumbSrc(w.cover)} alt="" />}
              </span>
              <span>
                {worldName(w, lang)}
                <small className="muted">
                  {t("ideas.worldMeta", { age: w.age_band, chapters: w.chapters, tasks: w.tasks })}
                  {" · "}
                  {w.complete ? t("ideas.complete") : t("ideas.warnings", { count: w.warnings.length })}
                </small>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Upload a story pack: a dry run first, then the real import (SPEC §13.4). */
export function ImportCard({ onImported }: { onImported: (id: number) => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [done, setDone] = useState(false);
  const check = useMutation({
    mutationFn: (f: File) => putImage<ImportReport>("/api/admin/worlds/import?dry_run=true", f),
  });
  const run = useMutation({
    mutationFn: (f: File) => putImage<ImportReport>("/api/admin/worlds/import?dry_run=false", f),
    onSuccess: (report) => {
      setFile(null);
      setDone(true);
      check.reset();
      void queryClient.invalidateQueries({ queryKey: IDEAS_KEY });
      if (report.world_id != null) {
        void queryClient.invalidateQueries({ queryKey: ["world", report.world_id] });
        onImported(report.world_id);
      }
    },
  });
  const report = check.data;
  const error = check.error ?? run.error;
  const packErrors =
    error instanceof ApiError && Array.isArray(error.params.errors) ? (error.params.errors as unknown[]).map(String) : [];

  return (
    <section className="card stack" aria-labelledby="ideas-import">
      <h3 id="ideas-import">{t("ideas.import")}</h3>
      <p className="muted small">{t("ideas.importHelp")}</p>
      <label className="button-like">
        {t("ideas.chooseFile")}
        <input
          type="file"
          accept={PACK_TYPES}
          hidden
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (!f) return;
            setFile(f);
            setDone(false);
            run.reset();
            check.mutate(f);
          }}
        />
      </label>
      {check.isPending && <p className="muted">{t("ideas.checking")}</p>}
      {done && (
        <p role="status" className="ok">
          {t("ideas.imported")}
        </p>
      )}
      {report && file && (
        <div className="stack" role="status">
          <p>
            <strong>{report.slug}</strong>{" "}
            {t("ideas.reportCounts", { chapters: report.chapters, tasks: report.tasks })}
          </p>
          {report.complete ? (
            <p className="ok">{t("ideas.reportComplete")}</p>
          ) : (
            <ul className="small warnlist">
              {report.warnings.map((w, i) => (
                <li key={i}>{warningText(w, t)}</li>
              ))}
            </ul>
          )}
          {report.replaced && <p className="muted small">{t("ideas.replacesExisting")}</p>}
          <div className="row">
            <button type="button" className="primary" disabled={run.isPending} onClick={() => run.mutate(file)}>
              {report.replaced ? t("ideas.replace") : t("ideas.doImport")}
            </button>
            <button
              type="button"
              onClick={() => {
                setFile(null);
                check.reset();
              }}
            >
              {t("common.cancel")}
            </button>
          </div>
        </div>
      )}
      {error && <p className="error">{errorText(error, t)}</p>}
      {packErrors.length > 0 && (
        <ul className="small warnlist">
          {packErrors.slice(0, 10).map((m, i) => (
            <li key={i}>{m}</li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function WorldEditor({
  worldId,
  warnings = [],
  onClose,
}: {
  worldId: number;
  warnings?: readonly PackWarning[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const world = useQuery({
    queryKey: ["world", worldId],
    queryFn: () => api<WorldFull>("GET", `/api/admin/worlds/${worldId}`),
  });
  if (world.isPending) return <p className="muted">{t("common.loading")}</p>;
  if (world.isError) return <p className="error">{errorText(world.error, t)}</p>;
  return <Editor initial={world.data} warnings={warnings} onClose={onClose} />;
}

function Editor({
  initial,
  warnings,
  onClose,
}: {
  initial: WorldFull;
  warnings: readonly PackWarning[];
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<WorldFull>(initial);
  const unfinished = unfinishedProblems(draft);
  const [lang, setLang] = useState<Lang>(contentLang(i18n.resolvedLanguage));
  const [chap, setChap] = useState(0);
  const [dirty, setDirty] = useState(false);
  const id = initial.id;

  const stored = (data: WorldFull) => {
    queryClient.setQueryData(["world", id], data);
    void queryClient.invalidateQueries({ queryKey: IDEAS_KEY });
  };
  const save = useMutation({
    mutationFn: () => api<WorldFull>("PUT", `/api/admin/worlds/${id}`, toPack(draft)),
    onSuccess: (data) => {
      setDraft(data);
      setDirty(false);
      stored(data);
      void queryClient.invalidateQueries({ queryKey: SPEECH_KEY });
    },
  });
  const cover = useMutation({
    mutationFn: (file: File | null) =>
      file ? putImage<WorldFull>(`/api/admin/worlds/${id}/cover`, file) : api<WorldFull>("DELETE", `/api/admin/worlds/${id}/cover`),
    onSuccess: (data) => {
      setDraft((d) => ({ ...d, cover: data.cover }));
      queryClient.setQueryData(["world", id], (old: WorldFull | undefined) => (old ? { ...old, cover: data.cover } : data));
      void queryClient.invalidateQueries({ queryKey: IDEAS_KEY });
    },
  });
  // Chapter pictures go by the saved position, so they change only while the draft is saved (SPEC §13.4).
  const picture = useMutation({
    mutationFn: ({ position, file }: { position: number; file: File | null }) =>
      file
        ? putImage<WorldFull>(`/api/admin/worlds/${id}/chapters/${position}/image`, file)
        : api<WorldFull>("DELETE", `/api/admin/worlds/${id}/chapters/${position}/image`),
    onSuccess: (data, { position }) => {
      const image = chapterImage(data.chapters[position - 1]);
      setDraft((d) => withChapterImage(d, position - 1, image));
      queryClient.setQueryData(["world", id], (old: WorldFull | undefined) =>
        old ? withChapterImage(old, position - 1, image) : data,
      );
    },
  });
  const remove = useMutation({
    mutationFn: () => api<void>("DELETE", `/api/admin/worlds/${id}`),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ["world", id] });
      void queryClient.invalidateQueries({ queryKey: IDEAS_KEY });
      onClose();
    },
  });

  const saveErrors =
    save.error instanceof ApiError && Array.isArray(save.error.params.errors)
      ? (save.error.params.errors as unknown[]).map(String)
      : [];
  const change = (next: WorldFull) => {
    setDraft(next);
    setDirty(true);
  };
  const worldTexts = draft.texts[lang] ?? { name: "", guide: "" };
  const setWorldTexts = (patch: Partial<typeof worldTexts>) =>
    change({ ...draft, texts: { ...draft.texts, [lang]: { ...worldTexts, ...patch } } as WorldFull["texts"] });
  const setWorldText = (field: "name" | "guide", value: string) => setWorldTexts({ [field]: value });

  const chapters = draft.chapters;
  const chapter: WorldChapter | undefined = chapters[chap];
  const setChapter = (next: WorldChapter) => change({ ...draft, chapters: chapters.map((c, i) => (i === chap ? next : c)) });
  const image = chapterImage(chapter);
  const chapterTexts = chapter?.texts[lang] ?? { title: "", intro: "", outro: "" };
  const setChapterTexts = (patch: Partial<typeof chapterTexts>) => {
    if (!chapter) return;
    setChapter({ ...chapter, texts: { ...chapter.texts, [lang]: { ...chapterTexts, ...patch } } as WorldChapter["texts"] });
  };
  const setChapterText = (field: "title" | "intro" | "outro", value: string) => setChapterTexts({ [field]: value });
  const chapterSay = (field: "title" | "intro" | "outro", label: string) => (
    <SpokenForm
      field={field}
      label={label}
      say={sayOf(chapterTexts)}
      audio={audioOf(chapterTexts)}
      onChange={(say) => setChapterTexts({ say })}
    />
  );
  const setTask = (kind: TaskKind, next: EditorTask | null) => {
    if (!chapter) return;
    const others = chapter.tasks.filter((x) => x.kind !== kind);
    setChapter({ ...chapter, tasks: sortedTasks(next ? [...others, next] : others) });
  };

  return (
    <div className="card stack">
      <div className="row spread wrap top">
        <div>
          <h3>{worldName(draft, i18n.resolvedLanguage ?? "en")}</h3>
          <span className="muted small">{draft.slug}</span>
        </div>
        <div className="row wrap" role="group" aria-label={t("ideas.editLanguage")}>
          {LANGS.map((l) => (
            <button key={l} type="button" className={l === lang ? "pill on" : "pill"} aria-pressed={l === lang} onClick={() => setLang(l)}>
              {t(`ideas.lang.${l}`)}
            </button>
          ))}
        </div>
      </div>

      <div className="row wrap top">
        <span className="preview" style={{ background: draft.color }}>
          {draft.cover ? <img src={thumbSrc(draft.cover, "md")} alt={t("ideas.cover")} /> : <small>{t("ideas.noCover")}</small>}
        </span>
        <div className="stack">
          <div className="row wrap">
            <ImageInput label={t("ideas.changeCover")} onFile={(f) => cover.mutate(f)} />
            {draft.cover && (
              <button type="button" disabled={cover.isPending} onClick={() => cover.mutate(null)}>
                {t("ideas.removeCover")}
              </button>
            )}
          </div>
          {cover.error && <p className="error">{errorText(cover.error, t)}</p>}
          <div className="row wrap">
            <a className="button-like" href={`/api/admin/worlds/${id}/export`} download={`${draft.slug}.zip`}>
              {t("ideas.export")}
            </a>
            <button
              type="button"
              className="danger"
              disabled={remove.isPending}
              onClick={() => {
                if (window.confirm(t("ideas.confirmDelete", { name: worldName(draft, lang) }))) remove.mutate();
              }}
            >
              {t("ideas.deleteWorld")}
            </button>
          </div>
          <p className="muted small">{t("ideas.exportHelp")}</p>
        </div>
      </div>

      <div className="row wrap">
        <div className="grow stack">
          <label>
            {t("ideas.worldName")}
            <input value={worldTexts.name} maxLength={80} onChange={(e) => setWorldText("name", e.target.value)} />
          </label>
          <SpokenForm
            field="name"
            label={t("ideas.worldName")}
            say={sayOf(worldTexts)}
            audio={audioOf(worldTexts)}
            onChange={(say) => setWorldTexts({ say })}
          />
        </div>
        <label className="grow">
          {t("ideas.guide")}
          <input value={worldTexts.guide ?? ""} maxLength={80} onChange={(e) => setWorldText("guide", e.target.value)} />
        </label>
        <label>
          {t("ideas.color")}
          <input type="color" value={draft.color} onChange={(e) => change({ ...draft, color: e.target.value.toUpperCase() })} />
        </label>
        <label>
          {t("ideas.ageBand")}
          <select value={draft.age_band} onChange={(e) => change({ ...draft, age_band: e.target.value as WorldFull["age_band"] })}>
            {AGE_BANDS.map((a) => (
              <option key={a} value={a}>
                {t(`ideas.age.${a}`)}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="row wrap" role="tablist" aria-label={t("ideas.chapters")}>
        {chapters.map((c, i) => (
          <button
            key={i}
            type="button"
            role="tab"
            aria-selected={i === chap}
            className={i === chap ? "pill on" : "pill"}
            onClick={() => setChap(i)}
          >
            {i + 1} · {c.texts[lang]?.title || c.texts.en?.title || c.texts.de?.title || c.texts.el?.title || "…"}
          </button>
        ))}
        {chapters.length < MAX_CHAPTERS && (
          <button
            type="button"
            className="small"
            onClick={() => {
              change({ ...draft, chapters: [...chapters, emptyChapter(lang)] });
              setChap(chapters.length);
            }}
          >
            {t("ideas.addChapter")}
          </button>
        )}
      </div>

      {chapter && (
        <div className="stack" role="tabpanel">
          <div className="row wrap top">
            <span className="preview" style={{ background: draft.color }}>
              {image ? (
                <img src={thumbSrc(image, "md")} alt={t("ideas.chapterPicture", { n: chap + 1 })} />
              ) : (
                <small>{t("ideas.noChapterPicture")}</small>
              )}
            </span>
            <div className="stack">
              {dirty ? (
                <p className="muted small">{t("ideas.pictureSaveFirst")}</p>
              ) : (
                <div className="row wrap">
                  <ImageInput
                    label={image ? t("ideas.replacePicture") : t("ideas.addPicture")}
                    onFile={(f) => picture.mutate({ position: chap + 1, file: f })}
                  />
                  {image && (
                    <button
                      type="button"
                      disabled={picture.isPending}
                      onClick={() => picture.mutate({ position: chap + 1, file: null })}
                    >
                      {t("ideas.removePicture")}
                    </button>
                  )}
                </div>
              )}
              {picture.error && <p className="error">{errorText(picture.error, t)}</p>}
            </div>
          </div>
          <label>
            {t("ideas.chapterTitle")}
            <input value={chapterTexts.title} maxLength={80} onChange={(e) => setChapterText("title", e.target.value)} />
          </label>
          {chapterSay("title", t("ideas.chapterTitle"))}
          <label>
            {t("ideas.chapterIntro")}
            <textarea rows={2} maxLength={600} value={chapterTexts.intro ?? ""} onChange={(e) => setChapterText("intro", e.target.value)} />
          </label>
          {chapterSay("intro", t("ideas.chapterIntro"))}
          <label>
            {t("ideas.chapterOutro")}
            <textarea rows={2} maxLength={600} value={chapterTexts.outro ?? ""} onChange={(e) => setChapterText("outro", e.target.value)} />
          </label>
          {chapterSay("outro", t("ideas.chapterOutro"))}
          {KINDS.map((kind) => {
            const task = chapter.tasks.find((x) => x.kind === kind);
            return task ? (
              <TaskBlock
                key={`${chap}-${kind}-${lang}`}
                task={task}
                lang={lang}
                onChange={(next) => setTask(kind, next)}
                onRemove={() => setTask(kind, null)}
              />
            ) : (
              <div key={kind} className={`taskblock kind-${kind} row spread`}>
                <span className="muted">{t("ideas.noTask", { kind: t(`ideas.kind.${kind}`) })}</span>
                <button type="button" className="small" onClick={() => setTask(kind, emptyTask(kind, lang))}>
                  {t("ideas.addTask", { kind: t(`ideas.kind.${kind}`) })}
                </button>
              </div>
            );
          })}
          <div className="row">
            <button
              type="button"
              className="danger small"
              disabled={chapters.length <= 1}
              onClick={() => {
                if (!window.confirm(t("ideas.confirmRemoveChapter", { n: chap + 1 }))) return;
                change({ ...draft, chapters: chapters.filter((_, i) => i !== chap) });
                setChap(Math.max(0, chap - 1));
              }}
            >
              {t("ideas.removeChapter")}
            </button>
          </div>
        </div>
      )}

      {warnings.length > 0 && (
        <details>
          <summary className="muted small">{t("ideas.warnings", { count: warnings.length })}</summary>
          <ul className="small warnlist">
            {warnings.map((w, i) => (
              <li key={i}>{warningText(w, t)}</li>
            ))}
          </ul>
        </details>
      )}
      {unfinished > 0 && <p className="error">{t("ideas.problemsUnfinished", { count: unfinished })}</p>}
      {save.error && <p className="error">{errorText(save.error, t)}</p>}
      {saveErrors.length > 0 && (
        <ul className="small warnlist">
          {saveErrors.slice(0, 10).map((m, i) => (
            <li key={i}>{m}</li>
          ))}
        </ul>
      )}
      {save.isSuccess && !dirty && (
        <p role="status" className="ok">
          {t("ideas.saved")}
        </p>
      )}
      <div className="row">
        <button
          type="button"
          className="primary"
          disabled={!dirty || save.isPending || unfinished > 0}
          onClick={() => save.mutate()}
        >
          {t("common.save")}
        </button>
        <button
          type="button"
          disabled={!dirty}
          onClick={() => {
            setDraft(initial);
            setDirty(false);
          }}
        >
          {t("ideas.discard")}
        </button>
      </div>
    </div>
  );
}

function TaskBlock({
  task,
  lang,
  onChange,
  onRemove,
}: {
  task: EditorTask;
  lang: Lang;
  onChange: (t: EditorTask) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const read = task.kind === "read";
  const calc = task.kind === "calc";
  const texts: TaskTexts = task.texts[lang] ?? { title: "", text: "", materials: [], easier: "", harder: "" };
  const set = (patch: Partial<TaskTexts>) =>
    onChange({ ...task, texts: { ...task.texts, [lang]: { ...texts, ...patch } } as EditorTask["texts"] });
  const say = sayOf(texts);
  const audio = audioOf(texts);
  const spoken = (field: string, label: string) => (
    <SpokenForm field={field} label={label} say={say} audio={audio} onChange={(next) => set({ say: next })} />
  );
  return (
    <fieldset className={`taskblock kind-${task.kind}`}>
      <legend>{t(`ideas.kind.${task.kind}`)}</legend>
      <div className="row wrap">
        <label className="grow">
          {t("ideas.taskTitle")}
          <input value={texts.title} maxLength={80} onChange={(e) => set({ title: e.target.value })} />
        </label>
        <label>
          {t("ideas.minutes")}
          <input
            type="number"
            min={1}
            max={60}
            value={task.minutes}
            onChange={(e) => onChange({ ...task, minutes: Math.max(1, Math.min(60, Number(e.target.value) || 1)) })}
          />
        </label>
      </div>
      {spoken("title", t("ideas.taskTitle"))}
      {read && <p className="muted small">{t("ideas.readHelp")}</p>}
      {calc && <p className="muted small">{t("ideas.calcHelp")}</p>}
      {EDIT_LEVELS.map((level) => {
        const field = LEVEL_FIELDS[level];
        const label = t(read ? "ideas.readingFor" : "ideas.textFor", { level: t(`ideas.level.${level}`) });
        return (
          <div key={level} className="stack">
            <label>
              {label}
              <textarea
                rows={read ? 4 : 3}
                maxLength={read ? 900 : 600}
                value={texts[field] ?? ""}
                placeholder={level === "normal" ? undefined : t("ideas.levelFallback")}
                onChange={(e) => set({ [field]: e.target.value })}
              />
            </label>
            {!read && spoken(field, label)}
          </div>
        );
      })}
      <label>
        {read ? t("ideas.materialsOptional") : t("ideas.materials")}
        <input
          defaultValue={(texts.materials ?? []).join(", ")}
          placeholder={t("ideas.materialsPlaceholder")}
          onBlur={(e) => set({ materials: parseMaterials(e.target.value).slice(0, 6) })}
        />
      </label>
      {read &&
        EDIT_LEVELS.map((level) => (
          <QuestionsEditor
            key={level}
            id={`${task.kind}-${lang}-${level}`}
            level={level}
            questions={texts.questions?.[level] ?? []}
            audio={texts.question_audio?.[level] ?? []}
            onChange={(list) => set({ questions: { ...(texts.questions ?? {}), [level]: list } as TaskTexts["questions"] })}
          />
        ))}
      {calc &&
        EDIT_LEVELS.map((level) => (
          <ProblemsEditor
            key={level}
            level={level}
            problems={texts.problems?.[level] ?? []}
            audio={texts.problem_audio?.[level] ?? []}
            onChange={(list, audio) =>
              set({
                problems: { ...(texts.problems ?? {}), [level]: list } as TaskTexts["problems"],
                problem_audio: { ...(texts.problem_audio ?? {}), [level]: audio } as TaskTexts["problem_audio"],
              })
            }
          />
        ))}
      <div className="row">
        <button type="button" className="link danger" onClick={onRemove}>
          {t("ideas.removeTask")}
        </button>
      </div>
    </fieldset>
  );
}

/** 1–2 questions for one level, three answers each, one of them right (SPEC §14.2). */
function QuestionsEditor({
  id,
  level,
  questions,
  audio,
  onChange,
}: {
  id: string;
  level: Level;
  questions: readonly Question[];
  audio: readonly (QuestionAudio | null)[];
  onChange: (questions: Question[]) => void;
}) {
  const { t } = useTranslation();
  const levelName = t(`ideas.level.${level}`);
  const setQuestion = (i: number, next: Question) => onChange(questions.map((q, j) => (j === i ? next : q)));
  return (
    <fieldset className="stack">
      <legend>{t("ideas.questionsFor", { level: levelName })}</legend>
      {questions.length === 0 && (
        <p className="muted small">{level === "normal" ? t("ideas.noQuestions") : t("ideas.questionsFallback")}</p>
      )}
      {questions.map((q, i) => {
        const qa = audio[i] ?? null;
        const qLabel = t("ideas.questionN", { n: i + 1, level: levelName });
        return (
          <div key={i} className="stack">
            <div className="row wrap">
              <label className="grow">
                {qLabel}
                <input value={q.question} maxLength={140} onChange={(e) => setQuestion(i, { ...q, question: e.target.value })} />
              </label>
              {qa?.question && <PlayButton url={qa.question} label={t("ideas.play", { field: qLabel })} />}
              <button
                type="button"
                className="icon danger"
                aria-label={t("ideas.removeQuestion", { n: i + 1, level: levelName })}
                onClick={() => onChange(questions.filter((_, j) => j !== i))}
              >
                ✕
              </button>
            </div>
            {q.answers.map((answer, k) => {
              const aLabel = t("ideas.answerN", { n: k + 1, question: i + 1, level: levelName });
              const url = qa?.answers[k];
              return (
                <div key={k} className="row wrap">
                  <input
                    type="radio"
                    name={`correct-${id}-${i}`}
                    checked={q.correct === k}
                    aria-label={t("ideas.markCorrect", { answer: k + 1, question: i + 1, level: levelName })}
                    onChange={() => setQuestion(i, { ...q, correct: k })}
                  />
                  <input
                    className="grow"
                    aria-label={aLabel}
                    value={answer}
                    maxLength={60}
                    onChange={(e) => setQuestion(i, { ...q, answers: q.answers.map((a, j) => (j === k ? e.target.value : a)) })}
                  />
                  {url && <PlayButton url={url} label={t("ideas.play", { field: aLabel })} />}
                </div>
              );
            })}
          </div>
        );
      })}
      {questions.length < MAX_QUESTIONS && (
        <div className="row">
          <button type="button" className="small" onClick={() => onChange([...questions, emptyQuestion()])}>
            {t("ideas.addQuestion", { level: levelName })}
          </button>
        </div>
      )}
      <p className="muted small">{t("ideas.correctHelp")}</p>
    </fieldset>
  );
}

/** 1–6 small story problems for one level, each with a whole-number answer and a hint (SPEC §15.2). */
function ProblemsEditor({
  level,
  problems,
  audio,
  onChange,
}: {
  level: Level;
  problems: readonly Problem[];
  audio: readonly (ProblemAudio | null)[];
  // The audio travels with its problem when it moves; an edited text loses its (now stale) audio.
  onChange: (problems: Problem[], audio: (ProblemAudio | null)[]) => void;
}) {
  const { t } = useTranslation();
  const levelName = t(`ideas.level.${level}`);
  const sounds = problems.map((_, i) => audio[i] ?? null);
  const setProblem = (i: number, next: Problem) => {
    const old = problems[i];
    const sound = sounds[i];
    const kept =
      sound && old ? { text: next.text === old.text ? sound.text : null, hint: next.hint === old.hint ? sound.hint : null } : null;
    onChange(
      problems.map((p, j) => (j === i ? next : p)),
      sounds.map((a, j) => (j === i ? kept : a)),
    );
  };
  return (
    <fieldset className="stack">
      <legend>{t("ideas.problemsFor", { level: levelName })}</legend>
      {problems.length === 0 && (
        <p className="muted small">{level === "normal" ? t("ideas.noProblems") : t("ideas.problemsFallback")}</p>
      )}
      {problems.map((p, i) => {
        const pa = sounds[i];
        const pLabel = t("ideas.problemN", { n: i + 1, level: levelName });
        const hLabel = t("ideas.hintN", { n: i + 1, level: levelName });
        return (
          <div key={i} className="stack problem">
            <div className="row wrap top">
              <label className="grow">
                {pLabel}
                <textarea
                  rows={2}
                  maxLength={300}
                  value={p.text}
                  aria-invalid={problemUnfinished(p) && !p.text.trim()}
                  onChange={(e) => setProblem(i, { ...p, text: e.target.value })}
                />
              </label>
              {pa?.text && <PlayButton url={pa.text} label={t("ideas.play", { field: pLabel })} />}
              <button
                type="button"
                className="icon"
                aria-label={t("ideas.moveProblemUp", { n: i + 1, level: levelName })}
                disabled={i === 0}
                onClick={() => onChange(move(problems, i, -1), move(sounds, i, -1))}
              >
                ↑
              </button>
              <button
                type="button"
                className="icon"
                aria-label={t("ideas.moveProblemDown", { n: i + 1, level: levelName })}
                disabled={i === problems.length - 1}
                onClick={() => onChange(move(problems, i, 1), move(sounds, i, 1))}
              >
                ↓
              </button>
              <button
                type="button"
                className="icon danger"
                aria-label={t("ideas.removeProblem", { n: i + 1, level: levelName })}
                onClick={() =>
                  onChange(
                    problems.filter((_, j) => j !== i),
                    sounds.filter((_, j) => j !== i),
                  )
                }
              >
                ✕
              </button>
            </div>
            <div className="row wrap">
              <label>
                {t("ideas.answerOf", { n: i + 1, level: levelName })}
                <input
                  type="number"
                  min={0}
                  max={MAX_ANSWER}
                  step={1}
                  value={p.answer}
                  onChange={(e) => setProblem(i, { ...p, answer: parseAnswer(e.target.value) })}
                />
              </label>
              <label className="grow">
                {hLabel}
                <input
                  value={p.hint}
                  maxLength={140}
                  aria-invalid={problemUnfinished(p) && !p.hint.trim()}
                  onChange={(e) => setProblem(i, { ...p, hint: e.target.value })}
                />
              </label>
              {pa?.hint && <PlayButton url={pa.hint} label={t("ideas.play", { field: hLabel })} />}
            </div>
          </div>
        );
      })}
      {problems.length < MAX_PROBLEMS && (
        <div className="row">
          <button type="button" className="small" onClick={() => onChange([...problems, emptyProblem()], [...sounds, null])}>
            {t("ideas.addProblem", { level: levelName })}
          </button>
        </div>
      )}
      <p className="muted small">{t("ideas.problemsHelp")}</p>
    </fieldset>
  );
}
