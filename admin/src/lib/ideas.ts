import type {
  EditorTask,
  EditorTaskTexts,
  Level,
  Pack,
  PackChapter,
  PackTask,
  PackWarning,
  Problem,
  Question,
  TaskKind,
  Window,
  WorldChapter,
  WorldFull,
} from "../api/client";
import type { Translate } from "./translate";

/** Content languages of a story world (SPEC §13.1). */
export const LANGS = ["de", "en", "el"] as const;
export type Lang = (typeof LANGS)[number];

/** The four kinds of task, in the order the tablet shows them (SPEC §14.2). */
export const KINDS: readonly TaskKind[] = ["draw", "calc", "write", "read"];

/** The child's levels, in the order of the level picker (SPEC §14.1). */
export const LEVELS: readonly Level[] = ["easier", "normal", "harder"];

/** A task's text field per level: `text` is the normal level (SPEC §14.1). */
export const LEVEL_FIELDS = { normal: "text", easier: "easier", harder: "harder" } as const;

/** The editor shows the level texts in this order. */
export const EDIT_LEVELS: readonly Level[] = ["normal", "easier", "harder"];

export const MAX_QUESTIONS = 2; // per level (SPEC §14.2)

export const MAX_PROBLEMS = 6; // per level (SPEC §15.2)

/** The answer of a calc problem: a whole number 0–999 (SPEC §15.2). */
export const MAX_ANSWER = 999;

export const MAX_CHAPTERS = 12;

/** Query key of GET /api/admin/activities. */
export const IDEAS_KEY = ["ideas"] as const;

/** Query key of GET /api/admin/activities/done, all loaded pages (SPEC §17.3). */
export const DONE_KEY = ["done"] as const;

/** Query key of GET /api/admin/speech (SPEC §14.3). */
export const SPEECH_KEY = ["speech"] as const;

/** How often the read-aloud panel refreshes while files are still being rendered. */
export const SPEECH_REFRESH_MS = 10_000;

/** The text in one language, falling back to English, then German, then Greek (like the tablet does, SPEC §13.1). */
export function pick<T>(texts: Record<string, T | undefined>, lang: string): T | undefined {
  return texts[lang] ?? texts.en ?? texts.de ?? texts.el;
}

/** The UI language as a content language; anything else edits English. */
export function contentLang(lang: string | undefined): Lang {
  return (LANGS as readonly string[]).includes(lang ?? "") ? (lang as Lang) : "en";
}

/** The languages a world has its own texts in, in LANGS order (SPEC §14.7). */
export function worldLangs(world: { texts: Record<string, unknown> }): Lang[] {
  return LANGS.filter((l) => world.texts[l] != null);
}

/** The story language offered first when a world is added to the queue: the UI language if the world has it, else English, German, Greek. */
export function defaultStoryLang(world: { texts: Record<string, unknown> }, uiLang: string): Lang {
  const have = worldLangs(world);
  for (const l of [contentLang(uiLang), "en", "de", "el"] as const) {
    if (have.includes(l)) return l;
  }
  return have[0] ?? "en";
}

/** A world's name in the UI language, or its slug when it has none. */
export function worldName(world: { slug: string; texts: Record<string, { name: string } | undefined> }, lang: string): string {
  return pick(world.texts, lang)?.name || world.slug;
}

/** Human text for a pack warning (SPEC §13.4, §14.2, §15.2, §15.5). */
export function warningText(w: PackWarning, t: Translate): string {
  const lang = w.lang ? t(`ideas.lang.${w.lang}`) : "";
  const kind = w.kind ? t(`ideas.kind.${w.kind}`) : "";
  const level = w.level ? t(`ideas.level.${w.level}`) : "";
  if (w.code === "missing_kind") return t("ideas.warn.missingKind", { chapter: w.chapter ?? "", kind });
  if (w.code === "missing_level") return t("ideas.warn.missingLevel", { chapter: w.chapter ?? "", kind, lang, level });
  if (w.code === "missing_questions")
    return t("ideas.warn.missingQuestions", { chapter: w.chapter ?? "", kind, lang, level });
  if (w.code === "missing_problems")
    return t("ideas.warn.missingProblems", { chapter: w.chapter ?? "", kind, lang, level });
  if (w.code === "problems_mismatch")
    return t("ideas.warn.problemsMismatch", { chapter: w.chapter ?? "", kind, lang, level });
  // Materials off the simple list (SPEC §15.5); the server names the material.
  if (w.code === "unlisted_material") {
    const material = w.material;
    return material
      ? t("ideas.warn.materialNamed", { chapter: w.chapter ?? "", kind, lang, material })
      : t("ideas.warn.material", { chapter: w.chapter ?? "", kind, lang });
  }
  if (w.chapter == null) return t("ideas.warn.missingWorldText", { lang });
  if (w.kind) return t("ideas.warn.missingTaskText", { chapter: w.chapter, kind, lang });
  return t("ideas.warn.missingChapterText", { chapter: w.chapter, lang });
}

/** Move one entry of a list up (-1) or down (+1); out of range leaves it as it is. */
export function move<T>(list: readonly T[], index: number, by: -1 | 1): T[] {
  const to = index + by;
  const out = [...list];
  if (index < 0 || index >= list.length || to < 0 || to >= list.length) return out;
  [out[index], out[to]] = [out[to], out[index]];
  return out;
}

/** "Papier, Farbstifte" ⇄ ["Papier", "Farbstifte"]. */
export function parseMaterials(value: string): string[] {
  return value
    .split(",")
    .map((m) => m.trim())
    .filter((m) => m !== "");
}

/** Whether a text block holds any text: strings count, numbers (like `correct`) don't. */
function hasText(value: unknown): boolean {
  if (typeof value === "string") return value.trim() !== "";
  if (Array.isArray(value)) return value.some(hasText);
  if (value && typeof value === "object") return Object.values(value).some(hasText);
  return false;
}

/** Drop language blocks the parent left completely empty, so an untouched language doesn't fail validation. */
function cleanTexts<T extends object>(texts: Record<string, T | undefined>): Record<string, T> {
  const out: Record<string, T> = {};
  for (const lang of LANGS) {
    const block = texts[lang];
    if (block && hasText(block)) out[lang] = block;
  }
  return out;
}

/** A text block without the output-only audio maps (SPEC §14.3) and without empty spoken forms. */
function packBlock<T extends object>(block: T): T {
  const out = { ...block } as Record<string, unknown>;
  delete out.audio;
  delete out.question_audio;
  delete out.problem_audio;
  const say = Object.entries((out.say ?? {}) as Record<string, string | undefined>).filter(
    ([, v]) => typeof v === "string" && v.trim() !== "",
  );
  if (say.length > 0) out.say = Object.fromEntries(say);
  else delete out.say;
  return out as T;
}

function packBlocks<T extends object>(texts: Record<string, T | undefined>): Record<string, T> {
  const out: Record<string, T> = {};
  for (const [lang, block] of Object.entries(texts)) if (block) out[lang] = packBlock(block);
  return cleanTexts(out);
}

/** A read task's questions without the ones left completely empty; no level left = no questions. */
function packQuestions(questions: Record<string, Question[] | undefined> | null | undefined): Record<string, Question[]> | null {
  const out: Record<string, Question[]> = {};
  for (const level of EDIT_LEVELS) {
    const kept = (questions?.[level] ?? []).filter((q) => hasText([q.question, ...q.answers]));
    if (kept.length > 0) out[level] = kept;
  }
  return Object.keys(out).length > 0 ? out : null;
}

/** A calc task's problems without the ones left completely empty; no level left = no problems (SPEC §15.2). */
function packProblems(problems: Record<string, Problem[] | undefined> | null | undefined): Record<string, Problem[]> | null {
  const out: Record<string, Problem[]> = {};
  for (const level of EDIT_LEVELS) {
    const kept = (problems?.[level] ?? []).filter((p) => hasText([p.text, p.hint]));
    if (kept.length > 0) out[level] = kept;
  }
  return Object.keys(out).length > 0 ? out : null;
}

/** A problem with something typed but no story text or no hint: the server would refuse the save (SPEC §15.2). */
export function problemUnfinished(p: Problem): boolean {
  return hasText([p.text, p.hint]) && !(hasText([p.text]) && hasText([p.hint]));
}

/** How many problems of the draft are unfinished; Save waits until there are none. */
export function unfinishedProblems(world: WorldFull): number {
  let n = 0;
  for (const chapter of world.chapters) {
    for (const task of chapter.tasks) {
      if (task.kind !== "calc") continue;
      for (const block of Object.values(task.texts)) {
        for (const level of EDIT_LEVELS) n += (block?.problems?.[level] ?? []).filter(problemUnfinished).length;
      }
    }
  }
  return n;
}

function packTask(task: EditorTask): PackTask {
  const texts: Record<string, EditorTaskTexts> = {};
  for (const [lang, block] of Object.entries(task.texts)) {
    if (!block) continue;
    // Only read tasks have questions and only calc tasks have problems (SPEC §14.2, §15.2).
    const { questions, problems, ...rest } = block;
    texts[lang] =
      task.kind === "read"
        ? ({ ...rest, questions: packQuestions(questions) } as EditorTaskTexts)
        : task.kind === "calc"
          ? ({ ...rest, problems: packProblems(problems) } as EditorTaskTexts)
          : (rest as EditorTaskTexts);
  }
  return { kind: task.kind, minutes: task.minutes, texts: packBlocks(texts) } as PackTask;
}

/** The editor's draft → the body for PUT /api/admin/worlds/{id} (SPEC §13.4); the audio maps stay out. */
export function toPack(world: WorldFull): Pack {
  return {
    format: 1,
    slug: world.slug,
    age_band: world.age_band,
    color: world.color,
    cover: null,
    texts: packBlocks(world.texts),
    chapters: world.chapters.map(
      (c): PackChapter => ({ texts: packBlocks(c.texts), tasks: c.tasks.map(packTask) }) as PackChapter,
    ),
  } as Pack;
}

export function emptyQuestion(): Question {
  return { question: "", answers: ["", "", ""], correct: 0 };
}

export function emptyProblem(): Problem {
  return { text: "", answer: 0, hint: "" };
}

export function emptyTask(kind: TaskKind, lang: Lang): EditorTask {
  const block = { title: "", text: "", materials: [], easier: "", harder: "" };
  const texts =
    kind === "read"
      ? { ...block, questions: { normal: [emptyQuestion()] } }
      : kind === "calc"
        ? { ...block, problems: { normal: [emptyProblem()] } }
        : block;
  return { kind, minutes: kind === "read" ? 10 : 15, texts: { [lang]: texts } } as EditorTask;
}

export function emptyChapter(lang: Lang): WorldChapter {
  return { image: null, texts: { [lang]: { title: "", intro: "", outro: "" } }, tasks: [] } as WorldChapter;
}

/** A new opening window: weekday afternoons (SPEC §14.4a). */
export function newWindow(): Window {
  return { days: [0, 1, 2, 3, 4], from: "15:00", to: "18:00" };
}

/** A window the server accepts: at least one day and from < to, both HH:MM (SPEC §14.4a). */
export function validWindow(w: Window): boolean {
  const time = /^([01]\d|2[0-3]):[0-5]\d$/;
  return w.days.length > 0 && time.test(w.from) && time.test(w.to) && w.from < w.to;
}

/** A stored picture as the server returns it (SPEC §13.4). */
export type ImageRef = { url: string; aspect: number };

/** A chapter's picture in WorldFull (`image: {url, aspect} | null`, SPEC §13.4); new chapters have none. */
export function chapterImage(chapter: { image?: unknown } | undefined): ImageRef | null {
  const image = chapter?.image;
  return image && typeof image === "object" && "url" in image ? (image as ImageRef) : null;
}

/** The world with chapter `index`'s picture replaced (the rest of the draft stays as it is). */
export function withChapterImage(world: WorldFull, index: number, image: ImageRef | null): WorldFull {
  return { ...world, chapters: world.chapters.map((c, i) => (i === index ? { ...c, image } : c)) };
}

/** An answer box's value as a problem's answer: a whole number 0–999 (SPEC §15.2). */
export function parseAnswer(value: string): number {
  return Math.max(0, Math.min(MAX_ANSWER, Math.trunc(Number(value)) || 0));
}

/** Tasks in the tablet's order: draw, calculate, write, read. */
export function sortedTasks<T extends { kind: TaskKind }>(tasks: readonly T[]): T[] {
  return [...tasks].sort((a, b) => KINDS.indexOf(a.kind) - KINDS.indexOf(b.kind));
}
