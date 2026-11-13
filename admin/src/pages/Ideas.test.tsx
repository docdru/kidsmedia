import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import i18n from "../i18n";
import { contentLang, defaultStoryLang, emptyChapter, emptyTask, move, parseMaterials, pick, toPack, warningText, worldName } from "../lib/ideas";
import type { PackWarning, WorldFull } from "../api/client";
import Ideas from "./Ideas";

const STATE = {
  settings: { days: [0, 1, 3, 5, 6], unlock_time: "07:00", level: "normal", windows: [] as unknown[] },
  days_off: [{ id: 4, from: "2026-11-21", to: "2026-11-22", note: "at grandma's" }],
  queue: [{ world_id: 1, first_date: "2026-11-02", last_date: "2026-11-09", status: "running", lang: "de", langs: ["de", "en"] }],
  worlds: [
    {
      id: 1,
      slug: "lighthouse-island",
      age_band: "6-7",
      color: "#B84A12",
      cover: null,
      texts: { en: { name: "Lighthouse Island", guide: "" }, de: { name: "Leuchtturm-Insel", guide: "" } },
      chapters: 6,
      tasks: 18,
      complete: true,
      warnings: [],
    },
  ],
  log: [],
  examples: [{ slug: "rocket-kiwi", texts: { en: { name: "Rocket Kiwi" }, de: { name: "Rakete Kiwi" } }, imported: false }],
};

const REPORT = {
  slug: "rocket-kiwi",
  world_id: null,
  replaced: false,
  chapters: 6,
  tasks: 17,
  complete: false,
  warnings: [{ code: "missing_kind", chapter: 4, kind: "write", lang: null }],
};

const PICTURE = { url: "/api/thumbs/w1c1-9e4f20.webp", aspect: 1.0 };
const DRAW = { kind: "draw", minutes: 15, texts: { en: { title: "Draw", text: "Draw it.", materials: [], easier: "", harder: "" } } };
const READ = {
  kind: "read",
  minutes: 10,
  texts: {
    en: {
      title: "Read",
      text: "Mira sees a ship on the sea.",
      materials: [],
      easier: "",
      harder: "",
      questions: { normal: [{ question: "What does Mira see?", answers: ["A ship", "A cat", "A tree"], correct: 0 }] },
      audio: { title: "/api/admin/speech/0123456789abcdef0001.m4a" },
      question_audio: {
        normal: [{ question: "/api/admin/speech/0123456789abcdef0002.m4a", answers: [null, null, null] }],
      },
    },
  },
};
const CALC = {
  kind: "calc",
  minutes: 15,
  texts: {
    en: {
      title: "Count the boats",
      text: "Get paper and a pencil ready.",
      materials: ["Paper", "Pencil"],
      easier: "",
      harder: "",
      problems: {
        normal: [
          { text: "Mira sees 4 boats, then 3 more. How many boats?", answer: 7, hint: "Draw 4 boats, then 3 more, and count them all." },
          { text: "Pip has 9 fish and eats 2. How many are left?", answer: 7, hint: "Draw 9 fish and cross out 2." },
        ],
      },
      audio: { title: "/api/admin/speech/0123456789abcdef0003.m4a" },
      problem_audio: { normal: [{ text: "/api/admin/speech/0123456789abcdef0004.m4a", hint: null }, null] },
    },
  },
};
const chapterOf = (title: string, image: typeof PICTURE | null, tasks: object[] = [DRAW]) => ({
  image,
  texts: { en: { title, intro: "", outro: "" } },
  tasks,
});
const WORLD = {
  id: 1,
  format: 1,
  slug: "lighthouse-island",
  age_band: "6-7",
  color: "#B84A12",
  cover: { url: "/api/thumbs/w1-ab12cd.webp", aspect: 1.0 },
  texts: { en: { name: "Lighthouse Island", guide: "", audio: { name: "/api/admin/speech/0123456789abcdef0000.m4a" } } },
  chapters: [chapterOf("The lamp", PICTURE, [DRAW, CALC, READ]), chapterOf("The storm", null)],
};
const SPEECH = {
  voices: [
    { lang: "de", voice: "de_DE-thorsten-medium", source: "bundled", license: "CC0", attribution: null },
    { lang: "en", voice: "en_GB-cori-medium", source: "bundled", license: "CC BY 4.0", attribution: null },
    {
      lang: "el",
      voice: "el_GR-joy-medium",
      source: "data",
      license: "CC BY-NC 4.0",
      attribution: "JOY Greek voice — Gemma4GR project, CC BY-NC 4.0",
    },
  ],
  rendered: 40,
  pending: 2,
  failed: 1,
  worker: true,
};
const NEW_PICTURE = { url: "/api/thumbs/w1c2-77aa00.webp", aspect: 1.0 };
const photo = (id: number) => ({
  id,
  url_sm: `/api/admin/photos/p${id}-ab12cd34.webp?size=sm`,
  url_lg: `/api/admin/photos/p${id}-ab12cd34.webp?size=lg`,
  taken_at: "2026-11-05T15:20:00+01:00",
});
const done = (id: number, kind: string, title: string, extra: { attempts?: number; stars?: number; bonus?: boolean; photos?: object[] } = {}) => ({
  id,
  world_id: 1,
  world_name: "Lighthouse Island",
  position: 1,
  kind,
  task_title: title,
  done_at: "2026-11-05T15:20:00+01:00",
  attempts: extra.attempts ?? null,
  stars: extra.stars ?? 1,
  bonus: extra.bonus ?? false,
  photos: extra.photos ?? [],
});
const DONE = {
  entries: [
    done(12, "draw", "Draw the lamp", { photos: [photo(9)] }),
    done(11, "calc", "Count the boats", { attempts: 3 }),
    done(10, "write", "Write about the storm", { stars: 2, bonus: true }),
  ],
  next_before_id: 10,
};
const OLDER_DONE = { entries: [done(7, "read", "Read about the ship", { attempts: 1, stars: 2 })], next_before_id: null };

/** GET /api/admin/activities/done, page by page (SPEC §17.3). */
function doneResponse(url: string): Response | null {
  if (url === "/api/admin/activities/done") return new Response(JSON.stringify(DONE));
  if (url === "/api/admin/activities/done?before_id=10") return new Response(JSON.stringify(OLDER_DONE));
  return null;
}

let calls: { url: string; init?: RequestInit }[] = [];

beforeEach(async () => {
  await i18n.changeLanguage("en");
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      const method = init?.method ?? "GET";
      if (method === "GET" && url.startsWith("/api/admin/activities/done")) return doneResponse(url) ?? new Response("null");
      if (url === "/api/admin/activities/done/12/bonus" && method === "POST")
        return new Response(JSON.stringify({ ...DONE.entries[0], stars: 2, bonus: true }));
      if (url === "/api/admin/activities/done/12" && method === "DELETE") return new Response(null, { status: 204 });
      if (url === "/api/admin/activities") return new Response(JSON.stringify(STATE));
      if (url === "/api/admin/speech") return new Response(JSON.stringify(SPEECH));
      if (url === "/api/admin/activities/settings") {
        const body = JSON.parse(String(init?.body)) as typeof STATE.settings;
        return new Response(JSON.stringify({ ...STATE, settings: body }));
      }
      if (url === "/api/admin/activities/queue") return new Response(JSON.stringify(STATE));
      if (url === "/api/admin/activities/days-off" && method === "POST")
        return new Response(JSON.stringify({ id: 5, from: "2026-12-24", to: "2026-12-24", note: "" }), { status: 201 });
      if (url.startsWith("/api/admin/worlds/import")) return new Response(JSON.stringify(REPORT));
      if (url === "/api/admin/worlds/1") return new Response(JSON.stringify(WORLD));
      if (url === "/api/admin/worlds/1/chapters/2/image" && method === "PUT")
        return new Response(JSON.stringify({ ...WORLD, chapters: [WORLD.chapters[0], chapterOf("The storm", NEW_PICTURE)] }));
      if (url === "/api/admin/worlds/1/chapters/1/image" && method === "DELETE")
        return new Response(JSON.stringify({ ...WORLD, chapters: [chapterOf("The lamp", null), WORLD.chapters[1]] }));
      return new Response("null");
    }),
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderPage() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Ideas />
    </QueryClientProvider>,
  );
}

const sent = (url: string, method: string) => calls.find((c) => c.url === url && c.init?.method === method);

async function openWorld() {
  renderPage();
  const buttons = await screen.findAllByRole("button", { name: /Lighthouse Island/ });
  const item = buttons.find((b) => b.hasAttribute("aria-pressed"));
  if (!item) throw new Error("world list entry missing");
  fireEvent.click(item);
}

it("v0.13: the editor shows the cover and the chapter picture, and exports a zip", async () => {
  await openWorld();
  expect(await screen.findByAltText("Picture for chapter 1")).toHaveAttribute("src", "/api/thumbs/w1c1-9e4f20.webp?size=md");
  expect(screen.getByAltText("Cover")).toHaveAttribute("src", "/api/thumbs/w1-ab12cd.webp?size=md");
  expect(screen.getByRole("link", { name: "Export (.zip)" })).toHaveAttribute("download", "lighthouse-island.zip");
  fireEvent.click(screen.getByRole("tab", { name: /The storm/ }));
  expect(screen.getByText("No picture: the tablet shows the cover.")).toBeInTheDocument();
});

it("v0.13: uploading a chapter picture PUTs the file to the chapter's image", async () => {
  await openWorld();
  fireEvent.click(await screen.findByRole("tab", { name: /The storm/ }));
  const file = new File(["png"], "storm.png", { type: "image/png" });
  fireEvent.change(screen.getByLabelText("Add picture"), { target: { files: [file] } });
  await waitFor(() => expect(sent("/api/admin/worlds/1/chapters/2/image", "PUT")?.init?.body).toBe(file));
  expect(await screen.findByAltText("Picture for chapter 2")).toHaveAttribute("src", "/api/thumbs/w1c2-77aa00.webp?size=md");
});

it("v0.13: removing a chapter picture DELETEs it", async () => {
  await openWorld();
  fireEvent.click(await screen.findByRole("button", { name: "Remove picture" }));
  await waitFor(() => expect(sent("/api/admin/worlds/1/chapters/1/image", "DELETE")).toBeDefined());
  expect(await screen.findByText("No picture: the tablet shows the cover.")).toBeInTheDocument();
});

it("shows the schedule, the queue and the example worlds", async () => {
  renderPage();
  expect(await screen.findByText("When there are activities")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Mon" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "Wed" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByText("Running now")).toBeInTheDocument();
  expect(screen.getAllByText("Lighthouse Island").length).toBeGreaterThan(0);
  expect(screen.getByText(/at grandma's/)).toBeInTheDocument();
  expect(screen.getByText("Rocket Kiwi")).toBeInTheDocument();
});

it("toggling a weekday saves the activity days", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Wed" }));
  await waitFor(() => {
    const put = sent("/api/admin/activities/settings", "PUT");
    expect(JSON.parse(String(put?.init?.body))).toEqual({ days: [0, 1, 2, 3, 5, 6], unlock_time: "07:00" });
  });
});

it("v0.14: choosing a level PUTs the settings with the level", async () => {
  renderPage();
  const picker = await screen.findByRole("group", { name: "Level" });
  expect(within(picker).getByRole("button", { name: "Normal" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(within(picker).getByRole("button", { name: "Harder" }));
  await waitFor(() => {
    const put = sent("/api/admin/activities/settings", "PUT");
    expect(JSON.parse(String(put?.init?.body))).toEqual({ days: [0, 1, 3, 5, 6], unlock_time: "07:00", level: "harder" });
  });
});

it("v0.14: no windows means always open; adding one PUTs the windows", async () => {
  renderPage();
  expect(await screen.findByText("Always open")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Add opening time" }));
  const days = screen.getByRole("group", { name: "Days of opening time 1" });
  expect(within(days).getByRole("button", { name: "Fri" })).toHaveAttribute("aria-pressed", "true");
  fireEvent.click(within(days).getByRole("button", { name: "Sat" }));
  fireEvent.click(screen.getByRole("button", { name: "Save opening times" }));
  await waitFor(() => {
    const put = sent("/api/admin/activities/settings", "PUT");
    expect(JSON.parse(String(put?.init?.body))).toEqual({
      days: [0, 1, 3, 5, 6],
      unlock_time: "07:00",
      windows: [{ days: [0, 1, 2, 3, 4, 5], from: "15:00", to: "18:00" }],
    });
  });
});

it("v0.14: an opening time that ends before it starts can't be saved", async () => {
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Add opening time" }));
  fireEvent.change(screen.getByLabelText("until"), { target: { value: "14:00" } });
  expect(screen.getByText("The end must be after the start.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save opening times" })).toBeDisabled();
});

it("v0.14: the read task's question editor sets the right answer, and saving leaves the audio out", async () => {
  await openWorld();
  expect(await screen.findByLabelText("Reading text for the level: Normal")).toHaveValue("Mira sees a ship on the sea.");
  expect(screen.getByLabelText("Question 1 (Normal)")).toHaveValue("What does Mira see?");
  const second = screen.getByRole("radio", { name: "Answer 2 to question 1 (Normal) is right" });
  expect(second).not.toBeChecked();
  fireEvent.click(second);
  expect(second).toBeChecked();
  fireEvent.change(screen.getByLabelText("Pronunciation: Name"), { target: { value: "Lait-haus Ai-länd" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(sent("/api/admin/worlds/1", "PUT")).toBeDefined());
  const body = JSON.parse(String(sent("/api/admin/worlds/1", "PUT")?.init?.body));
  const read = body.chapters[0].tasks.find((x: { kind: string }) => x.kind === "read");
  expect(read.texts.en.questions.normal[0]).toEqual({
    question: "What does Mira see?",
    answers: ["A ship", "A cat", "A tree"],
    correct: 1,
  });
  expect(body.texts.en.say).toEqual({ name: "Lait-haus Ai-länd" });
  expect(JSON.stringify(body)).not.toContain("audio");
});

it("v0.14: a play button plays the rendered audio of its field", async () => {
  const played: string[] = [];
  class FakeAudio {
    constructor(readonly src: string) {
      played.push(src);
    }
    play() {
      return Promise.resolve();
    }
    pause() {}
  }
  vi.stubGlobal("Audio", FakeAudio);
  await openWorld();
  fireEvent.click(await screen.findByRole("button", { name: "Listen: Name" }));
  expect(played).toEqual(["/api/admin/speech/0123456789abcdef0000.m4a"]);
  fireEvent.click(screen.getByRole("button", { name: "Listen: Question 1 (Normal)" }));
  expect(played[1]).toBe("/api/admin/speech/0123456789abcdef0002.m4a");
  expect(screen.queryByRole("button", { name: "Listen: Answer 1 to question 1 (Normal)" })).toBeNull();
});

it("v0.14: the read-aloud panel shows the voices with JOY's attribution and the counts", async () => {
  renderPage();
  expect(await screen.findByText("JOY Greek voice — Gemma4GR project, CC BY-NC 4.0")).toBeInTheDocument();
  expect(screen.getByText("el_GR-joy-medium")).toBeInTheDocument();
  expect(screen.getByText("added by you")).toBeInTheDocument();
  expect(screen.getByText("40 ready · 2 waiting · 1 failed")).toBeInTheDocument();
  // JOY is in use, so there is no hint to add it.
  expect(screen.queryByRole("link", { name: "https://huggingface.co/Efso/joy-greek-tts" })).toBeNull();
});

it("v0.14: the hint to add JOY shows when Greek uses another voice", async () => {
  const voices = SPEECH.voices.map((v) =>
    v.lang === "el" ? { ...v, voice: "el_GR-rapunzelina-low", source: "bundled", attribution: null } : v,
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/admin/activities") return new Response(JSON.stringify(STATE));
      if (url === "/api/admin/speech") return new Response(JSON.stringify({ ...SPEECH, voices }));
      return doneResponse(url) ?? new Response("null");
    }),
  );
  renderPage();
  expect(await screen.findByText("el_GR-rapunzelina-low")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "https://huggingface.co/Efso/joy-greek-tts" })).toBeInTheDocument();
});

it("v0.14: the log shows the reading attempts", async () => {
  const log = [{ date: "2026-11-05", world_id: 1, position: 1, kinds: ["read"], done_at: "2026-11-05T15:20:00+01:00", attempts: 2 }];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === "/api/admin/activities"
        ? new Response(JSON.stringify({ ...STATE, log }))
        : (doneResponse(url) ?? new Response("null")),
    ),
  );
  renderPage();
  expect(await screen.findByText("2× through the questions")).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: "Tries" })).toBeInTheDocument();
});

it("adds days off", async () => {
  renderPage();
  fireEvent.change(await screen.findByLabelText("From"), { target: { value: "2026-12-24" } });
  fireEvent.change(screen.getByLabelText("Note"), { target: { value: " Christmas " } });
  fireEvent.click(screen.getByRole("button", { name: "Add days off" }));
  await waitFor(() => {
    const post = sent("/api/admin/activities/days-off", "POST");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ from: "2026-12-24", to: null, note: "Christmas" });
  });
});

it("checks a story pack before importing it", async () => {
  renderPage();
  const input = await screen.findByLabelText("Choose file…");
  const file = new File(["{}"], "world.json", { type: "application/json" });
  fireEvent.change(input, { target: { files: [file] } });
  expect(await screen.findByText("Chapter 4: no Write task")).toBeInTheDocument();
  expect(sent("/api/admin/worlds/import?dry_run=true", "PUT")).toBeDefined();
  expect(sent("/api/admin/worlds/import?dry_run=false", "PUT")).toBeUndefined();
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  await waitFor(() => expect(sent("/api/admin/worlds/import?dry_run=false", "PUT")).toBeDefined());
});

it("v0.14: warnings for missing level texts and questions read like sentences", () => {
  const t = i18n.t.bind(i18n) as unknown as (key: string, options?: Record<string, unknown>) => string;
  expect(warningText({ code: "missing_level", chapter: 3, kind: "write", lang: "en", level: "harder" }, t)).toBe(
    "Chapter 3 · Write: English text for the level “Harder” missing",
  );
  expect(warningText({ code: "missing_questions", chapter: 1, kind: "read", lang: "de", level: "easier" }, t)).toBe(
    "Chapter 1 · Read: Deutsch questions for the level “Easier” missing",
  );
});

it("helpers: move, materials, warnings, empty languages dropped on save", () => {
  expect(move([1, 2, 3], 0, 1)).toEqual([2, 1, 3]);
  expect(move([1, 2, 3], 0, -1)).toEqual([1, 2, 3]);
  expect(parseMaterials(" paper, ,crayons ")).toEqual(["paper", "crayons"]);
  const t = i18n.t.bind(i18n) as unknown as (key: string, options?: Record<string, unknown>) => string;
  expect(warningText({ code: "missing_translation", chapter: 2, kind: null, lang: "de" }, t)).toBe(
    "Chapter 2: Deutsch story missing",
  );
  const world = {
    id: 1,
    format: 1,
    slug: "x",
    age_band: "6-7",
    color: "#112233",
    cover: null,
    texts: { de: { name: "X", guide: "" }, en: { name: "", guide: "" } },
    chapters: [
      {
        texts: { de: { title: "Eins", intro: "", outro: "" } },
        tasks: [{ kind: "draw", minutes: 15, texts: { de: { title: "Mal", text: "Mal etwas.", materials: [], easier: "", harder: "" } } }],
      },
    ],
  } as unknown as WorldFull;
  const pack = toPack(world);
  expect(Object.keys(pack.texts)).toEqual(["de"]);
  expect(pack.chapters[0].tasks[0].kind).toBe("draw");
});

it("Greek (v0.12): texts fall back English → German → Greek, and el is edited like the others", () => {
  expect(pick({ el: "γ", de: "b", en: "a" }, "el")).toBe("γ");
  expect(pick({ el: "γ", de: "b", en: "a" }, "fr")).toBe("a");
  expect(pick({ el: "γ", de: "b" }, "en")).toBe("b");
  expect(pick({ el: "γ" }, "de")).toBe("γ");
  expect(worldName({ slug: "owl", texts: { el: { name: "Το μυστήριο της κουκουβάγιας" } } }, "en")).toBe(
    "Το μυστήριο της κουκουβάγιας",
  );
  expect(contentLang("el")).toBe("el");
  expect(contentLang("fr")).toBe("en");
  const world = {
    id: 1,
    format: 1,
    slug: "owl",
    age_band: "6-7",
    color: "#112233",
    cover: null,
    texts: { el: { name: "Κουκουβάγια", guide: "" }, de: { name: "", guide: "" } },
    chapters: [{ texts: { el: { title: "Ένα", intro: "", outro: "" } }, tasks: [] }],
  } as unknown as WorldFull;
  expect(Object.keys(emptyChapter("el").texts)).toEqual(["el"]);
  expect(Object.keys(emptyTask("draw", "el").texts)).toEqual(["el"]);
  const pack = toPack(world);
  expect(Object.keys(pack.texts)).toEqual(["el"]);
  expect(Object.keys(pack.chapters[0].texts)).toEqual(["el"]);
});

it("shows the page and its dates in Greek", async () => {
  await i18n.changeLanguage("el");
  renderPage();
  expect(await screen.findByText("Σε εξέλιξη")).toBeInTheDocument();
  const day = (d: string) =>
    new Date(`${d}T12:00:00`).toLocaleDateString("el", { weekday: "short", day: "numeric", month: "short" });
  expect(screen.getByText(`${day("2026-11-02")} – ${day("2026-11-09")}`)).toBeInTheDocument();
  const t = i18n.t.bind(i18n) as unknown as (key: string, options?: Record<string, unknown>) => string;
  expect(warningText({ code: "missing_translation", chapter: 2, kind: null, lang: "el" }, t)).toBe(
    "Κεφάλαιο 2: λείπει η ιστορία στα Ελληνικά",
  );
});

it("v0.15: a queued story's language can be changed in its row", async () => {
  renderPage();
  const select = await screen.findByRole("combobox", { name: "Story language of Lighthouse Island" });
  expect(select).toHaveValue("de");
  expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Deutsch", "English"]);
  fireEvent.change(select, { target: { value: "en" } });
  await waitFor(() => {
    const put = sent("/api/admin/activities/queue", "PUT");
    expect(JSON.parse(String(put?.init?.body))).toEqual({ world_ids: [1], langs: { "1": "en" } });
  });
});

it("v0.15: adding a world asks for its story language", async () => {
  const owl = {
    ...STATE.worlds[0],
    id: 2,
    slug: "owl-mystery",
    texts: { en: { name: "The Owl Mystery", guide: "" }, de: { name: "Das Eulen-Rätsel", guide: "" }, el: { name: "Το μυστήριο της κουκουβάγιας", guide: "" } },
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/admin/activities" || url === "/api/admin/activities/queue")
        return new Response(JSON.stringify({ ...STATE, worlds: [...STATE.worlds, owl] }));
      return doneResponse(url) ?? new Response("null");
    }),
  );
  renderPage();
  fireEvent.change(await screen.findByRole("combobox", { name: /Add to the queue/ }), { target: { value: "2" } });
  const lang = screen.getByRole("combobox", { name: /^Story language(?! of)/ });
  expect(lang).toHaveValue("en"); // the UI language, the world has it
  fireEvent.change(lang, { target: { value: "el" } });
  fireEvent.click(within(screen.getByRole("region", { name: "Story queue" })).getByRole("button", { name: "Add" }));
  await waitFor(() => {
    const put = sent("/api/admin/activities/queue", "PUT");
    expect(JSON.parse(String(put?.init?.body))).toEqual({ world_ids: [1, 2], langs: { "2": "el" } });
  });
});

it("v0.15: the story language offered first is the UI language if the world has it", () => {
  expect(defaultStoryLang({ texts: { de: {}, el: {} } }, "el")).toBe("el");
  expect(defaultStoryLang({ texts: { de: {}, el: {} } }, "en")).toBe("de");
  expect(defaultStoryLang({ texts: { el: {} } }, "fr")).toBe("el");
});

it("v0.16: the calc task's problems editor edits, reorders and adds problems, and saving keeps them without audio", async () => {
  await openWorld();
  expect(await screen.findByLabelText("Problem 1 (Normal)")).toHaveValue("Mira sees 4 boats, then 3 more. How many boats?");
  expect(screen.getByRole("button", { name: "Listen: Problem 1 (Normal)" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Listen: Hint for problem 1 (Normal)" })).toBeNull();
  expect(screen.getAllByText("None: the normal problems are used.")).toHaveLength(2); // easier, harder
  fireEvent.change(screen.getByLabelText("Answer to problem 1 (Normal)"), { target: { value: "1234" } });
  expect(screen.getByLabelText("Answer to problem 1 (Normal)")).toHaveValue(999);
  fireEvent.change(screen.getByLabelText("Answer to problem 1 (Normal)"), { target: { value: "12" } });
  fireEvent.change(screen.getByLabelText("Hint for problem 2 (Normal)"), { target: { value: "Draw 9 fish, cross out 2, count the rest." } });
  fireEvent.click(screen.getByRole("button", { name: "Move problem 2 up (Normal)" }));
  expect(screen.getByLabelText("Problem 1 (Normal)")).toHaveValue("Pip has 9 fish and eats 2. How many are left?");
  fireEvent.click(screen.getByRole("button", { name: "Add problem (Easier)" }));
  fireEvent.change(screen.getByLabelText("Problem 1 (Easier)"), { target: { value: "Mira has 2 shells and finds 1. How many?" } });
  fireEvent.change(screen.getByLabelText("Answer to problem 1 (Easier)"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("Hint for problem 1 (Easier)"), { target: { value: "Draw the shells and count." } });
  // An added problem left empty is dropped on save.
  fireEvent.click(screen.getByRole("button", { name: "Add problem (Harder)" }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(sent("/api/admin/worlds/1", "PUT")).toBeDefined());
  const body = JSON.parse(String(sent("/api/admin/worlds/1", "PUT")?.init?.body));
  const calc = body.chapters[0].tasks.find((x: { kind: string }) => x.kind === "calc");
  expect(calc.texts.en.problems).toEqual({
    normal: [
      { text: "Pip has 9 fish and eats 2. How many are left?", answer: 7, hint: "Draw 9 fish, cross out 2, count the rest." },
      { text: "Mira sees 4 boats, then 3 more. How many boats?", answer: 12, hint: "Draw 4 boats, then 3 more, and count them all." },
    ],
    easier: [{ text: "Mira has 2 shells and finds 1. How many?", answer: 3, hint: "Draw the shells and count." }],
  });
  const draw = body.chapters[0].tasks.find((x: { kind: string }) => x.kind === "draw");
  expect(draw.texts.en.problems).toBeUndefined();
  expect(JSON.stringify(body)).not.toContain("audio");
});

it("v0.16: a problem without a hint can't be saved and is marked", async () => {
  await openWorld();
  fireEvent.click(await screen.findByRole("button", { name: "Add problem (Easier)" }));
  fireEvent.change(screen.getByLabelText("Problem 1 (Easier)"), { target: { value: "Mira has 2 shells and finds 1. How many?" } });
  expect(screen.getByLabelText("Hint for problem 1 (Easier)")).toHaveAttribute("aria-invalid", "true");
  expect(screen.getByText("A problem needs a story and a hint: 1 unfinished")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Hint for problem 1 (Easier)"), { target: { value: "Draw the shells and count." } });
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
});

it("v0.16: a new calc task starts with one problem; a problem can be removed", async () => {
  await openWorld();
  fireEvent.click(await screen.findByRole("tab", { name: /The storm/ }));
  fireEvent.click(screen.getByRole("button", { name: "Add Calculate" }));
  expect(screen.getByLabelText("Problem 1 (Normal)")).toHaveValue("");
  fireEvent.click(screen.getByRole("button", { name: "Remove problem 1 (Normal)" }));
  expect(screen.queryByLabelText("Problem 1 (Normal)")).toBeNull();
  expect(screen.getByText(/without problems the tablet asks/)).toBeInTheDocument();
});

it("v0.16: the editor shows the server's warnings for problems and materials", async () => {
  const warnings = [
    { code: "missing_problems", chapter: 1, kind: "calc", lang: "en", level: "harder" },
    { code: "unlisted_material", chapter: 1, kind: "draw", lang: "en", material: null },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url === "/api/admin/activities")
        return new Response(JSON.stringify({ ...STATE, worlds: [{ ...STATE.worlds[0], complete: false, warnings }] }));
      if (url === "/api/admin/worlds/1") return new Response(JSON.stringify(WORLD));
      return doneResponse(url) ?? new Response("null");
    }),
  );
  await openWorld();
  expect(await screen.findByText("Chapter 1 · Calculate: English problems for the level “Harder” missing")).toBeInTheDocument();
  expect(screen.getByText("Chapter 1 · Draw: English materials not on the simple materials list")).toBeInTheDocument();
  const t = i18n.t.bind(i18n) as unknown as (key: string, options?: Record<string, unknown>) => string;
  const named: PackWarning = { code: "unlisted_material", chapter: 2, kind: "write", lang: "de", level: null, material: "Knöpfe" };
  expect(warningText(named, t)).toBe("Chapter 2 · Write: “Knöpfe” (Deutsch) is not on the simple materials list");
});

it("v0.19: done activities lists every done task with its picture, tries and stars, and loads more", async () => {
  renderPage();
  const list = await screen.findByRole("region", { name: "Done activities" });
  const first = await within(list).findByRole("button", { name: "Show picture: Chapter 1 · Lighthouse Island · Draw · Draw the lamp" });
  expect(first.querySelector("img")).toHaveAttribute("src", "/api/admin/photos/p9-ab12cd34.webp?size=sm");
  const rows = within(list).getAllByRole("listitem");
  expect(rows.map((li) => li.querySelector("strong")?.textContent)).toEqual([
    "Draw the lamp",
    "Count the boats",
    "Write about the storm",
  ]);
  expect(within(rows[0]).getByText("1 star")).toBeInTheDocument();
  expect(within(rows[0]).queryByText(/no picture/)).toBeNull();
  expect(within(rows[1]).getByText("Maths: 3 tries · 1 star")).toBeInTheDocument();
  expect(within(rows[2]).getByText("2 stars · no picture")).toBeInTheDocument();
  expect(within(rows[2]).getByText("Bonus star given")).toBeInTheDocument();
  // A bonus star for every kind (SPEC §17.2, decision 49).
  expect(within(rows[1]).getByRole("button", { name: "Give a bonus star ⭐" })).toBeInTheDocument();
  fireEvent.click(within(list).getByRole("button", { name: "Load more" }));
  expect(await within(list).findByText("Read about the ship")).toBeInTheDocument();
  expect(within(list).getByText("1× through the questions · 2 stars")).toBeInTheDocument();
  expect(calls.some((c) => c.url === "/api/admin/activities/done?before_id=10")).toBe(true);
  expect(within(list).queryByRole("button", { name: "Load more" })).toBeNull();
  fireEvent.click(first);
  const dialog = screen.getByRole("dialog", { name: "Draw the lamp" });
  expect(within(dialog).getByRole("img")).toHaveAttribute("src", "/api/admin/photos/p9-ab12cd34.webp?size=lg");
});

it("v0.19: giving a bonus star POSTs it and then shows it as given", async () => {
  renderPage();
  const list = await screen.findByRole("region", { name: "Done activities" });
  const row = (await within(list).findByText("Draw the lamp")).closest("li") as HTMLElement;
  fireEvent.click(within(row).getByRole("button", { name: "Give a bonus star ⭐" }));
  await waitFor(() => expect(sent("/api/admin/activities/done/12/bonus", "POST")).toBeDefined());
  expect(await within(row).findByText("Bonus star given")).toBeInTheDocument();
  expect(within(row).queryByRole("button", { name: "Give a bonus star ⭐" })).toBeNull();
  expect(within(row).getByText("2 stars")).toBeInTheDocument();
});

it("v0.19: open again asks first, then DELETEs the done task and drops its row", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  renderPage();
  const list = await screen.findByRole("region", { name: "Done activities" });
  const row = (await within(list).findByText("Draw the lamp")).closest("li") as HTMLElement;
  fireEvent.click(within(row).getByRole("button", { name: "Open again" }));
  expect(confirm).toHaveBeenCalledWith("Open this task again? Its 1 star is taken back and its picture is deleted.");
  expect(sent("/api/admin/activities/done/12", "DELETE")).toBeUndefined();
  const loads = calls.filter((c) => c.url === "/api/admin/activities").length;
  confirm.mockReturnValue(true);
  fireEvent.click(within(row).getByRole("button", { name: "Open again" }));
  await waitFor(() => expect(sent("/api/admin/activities/done/12", "DELETE")).toBeDefined());
  await waitFor(() => expect(within(list).queryByText("Draw the lamp")).toBeNull());
  expect(within(list).getByText("Write about the storm")).toBeInTheDocument();
  // The log and the star balance change: the state is loaded again.
  await waitFor(() => expect(calls.filter((c) => c.url === "/api/admin/activities").length).toBeGreaterThan(loads));
});

it("v0.19: open again without a picture names only the stars", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  renderPage();
  const list = await screen.findByRole("region", { name: "Done activities" });
  const row = (await within(list).findByText("Write about the storm")).closest("li") as HTMLElement;
  fireEvent.click(within(row).getByRole("button", { name: "Open again" }));
  expect(confirm).toHaveBeenCalledWith("Open this task again? Its 2 stars are taken back.");
});

it("v0.19: the log has no picture column any more (the pictures are in done activities)", async () => {
  const log = [
    { date: "2026-11-05", world_id: 1, position: 1, kinds: ["draw"], done_at: "2026-11-05T15:20:00+01:00", attempts: null },
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url === "/api/admin/activities"
        ? new Response(JSON.stringify({ ...STATE, log }))
        : (doneResponse(url) ?? new Response("null")),
    ),
  );
  renderPage();
  await screen.findByRole("columnheader", { name: "Tries" });
  expect(screen.queryByRole("columnheader", { name: "Picture" })).toBeNull();
});
