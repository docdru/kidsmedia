import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import i18n from "../i18n";
import Rules from "./Rules";

const RULE = {
  id: 4,
  name: "Stories",
  days: [5, 6],
  enabled: true,
  all_groups: true,
  group_ids: [],
  mode: "limit",
  max_items: 1,
  max_minutes: null,
  lockouts: [],
  grace_minutes: 0,
  count_after_seconds: 10,
  replay_percent: 120,
  gem_cost: null,
  message_en: null,
  message_de: null,
  message_el: null,
};

const GEMS = {
  balance: 3,
  entries: [
    { id: 7, delta: 2, kind: "admin", title: null, note: "Room tidied", created_at: "2026-11-07T16:00:00Z" },
    { id: 6, delta: -2, kind: "spend", title: "Episode 4: The Storm", note: null, created_at: "2026-11-07T10:00:00Z" },
  ],
};

let calls: { url: string; init?: RequestInit }[] = [];
let rules: unknown[] = [RULE];
let gemsAnswer: () => Response = () => new Response(JSON.stringify(GEMS));

beforeEach(async () => {
  await i18n.changeLanguage("en");
  calls = [];
  rules = [RULE];
  gemsAnswer = () => new Response(JSON.stringify(GEMS));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/admin/rules") return new Response(JSON.stringify(rules));
      if (url === "/api/admin/gems?limit=10") return new Response(JSON.stringify(GEMS));
      if (url === "/api/admin/gems") return gemsAnswer();
      if (url.startsWith("/api/admin/preview")) {
        return new Response(JSON.stringify([{ group_id: 1, name: "Moon", status: { allowed: true, items_left: 1 } }]));
      }
      if (url === "/api/admin/usage/bonus") return new Response(null, { status: 204 });
      return new Response("[]");
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function renderRules() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Rules />
    </QueryClientProvider>,
  );
}

it("grants a one-day bonus file on a rule (SPEC §3.5)", async () => {
  renderRules();
  fireEvent.click(await screen.findByRole("button", { name: "+1 file" }));
  await waitFor(() => {
    const post = calls.find((c) => c.url === "/api/admin/usage/bonus");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ rule_id: 4, extra_items: 1 });
  });
  expect(await screen.findByText("Bonus added for today.")).toBeInTheDocument();
});

it("simulates another moment with the same evaluation", async () => {
  renderRules();
  await screen.findByText("1 file left");
  fireEvent.change(screen.getByLabelText("Simulate a moment"), { target: { value: "2026-11-09T20:00" } });
  await waitFor(() => expect(calls.some((c) => c.url === "/api/admin/preview?at=2026-11-09T20%3A00")).toBe(true));
  expect(await screen.findByText("At that moment")).toBeInTheDocument();
});

it("edits the child's message in English, German and Greek (decision 15)", async () => {
  renderRules();
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Ελληνικά"), { target: { value: "Μία ιστορία τη μέρα." } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => {
    const put = calls.find((c) => c.url === "/api/admin/rules/4" && c.init?.method === "PUT");
    const body = JSON.parse(String(put?.init?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({ message_en: null, message_de: null, message_el: "Μία ιστορία τη μέρα." });
  });
});

function gemPosts() {
  return calls
    .filter((c) => c.url === "/api/admin/gems" && c.init?.method === "POST")
    .map((c) => JSON.parse(String(c.init?.body)) as unknown);
}

it("adds and removes gems, with an optional note (SPEC §16.4)", async () => {
  renderRules();
  expect(await screen.findByText("3 💎")).toBeInTheDocument();
  expect(screen.getByText("Room tidied")).toBeInTheDocument();
  expect(screen.getByText("Episode 4: The Storm")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "+1 💎" }));
  await waitFor(() => expect(gemPosts()).toEqual([{ delta: 1 }]));
  await waitFor(() => expect(screen.getByRole("button", { name: "+5 💎" })).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Note (optional)"), { target: { value: "Teeth brushed" } });
  fireEvent.click(screen.getByRole("button", { name: "+5 💎" }));
  await waitFor(() => expect(gemPosts()).toEqual([{ delta: 1 }, { delta: 5, note: "Teeth brushed" }]));
  await waitFor(() => expect(screen.getByLabelText("Note (optional)")).toHaveValue(""));
  await waitFor(() => expect(screen.getByRole("button", { name: "−1 💎" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "−1 💎" }));
  await waitFor(() => expect(gemPosts()).toEqual([{ delta: 1 }, { delta: 5, note: "Teeth brushed" }, { delta: -1 }]));
});

it("undoes the newest admin entry with a counter entry", async () => {
  const undone = {
    balance: 1,
    entries: [
      { id: 8, delta: -2, kind: "admin", title: null, note: "Undo: Room tidied", created_at: "2026-11-08T09:00:00Z" },
      ...GEMS.entries,
    ],
  };
  gemsAnswer = () => new Response(JSON.stringify(undone));
  renderRules();
  fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
  await waitFor(() => expect(gemPosts()).toEqual([{ delta: -2, note: "Undo: Room tidied" }]));
  expect(await screen.findByText("1 💎")).toBeInTheDocument();
  // The counter entry doesn't offer to undo itself.
  expect(screen.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
});

it("shows a removal below 0 as not_enough_gems", async () => {
  gemsAnswer = () => new Response(JSON.stringify({ reason: "not_enough_gems" }), { status: 422 });
  renderRules();
  fireEvent.click(await screen.findByRole("button", { name: "−1 💎" }));
  expect(await screen.findByText("Not enough gems: the balance can't go below 0.")).toBeInTheDocument();
});

it("saves a gem cost and shows it in the summary (SPEC §16.2)", async () => {
  rules = [{ ...RULE, name: "Cartoons", days: [0, 1, 2, 3, 4, 5, 6], max_items: null, gem_cost: 2 }];
  renderRules();
  expect(await screen.findByText("Each file from all groups costs 2 💎 per listen · every day")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  // The listening budget applies with a gem cost too, without an item limit.
  expect(screen.getByLabelText("Replays up to (% of length)")).toHaveValue(120);
  fireEvent.change(screen.getByLabelText(/Costs gems/), { target: { value: "" } });
  expect(screen.queryByLabelText("Replays up to (% of length)")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/Costs gems/), { target: { value: "25" } });
  fireEvent.blur(screen.getByLabelText(/Costs gems/));
  expect(screen.getByLabelText(/Costs gems/)).toHaveValue(20);
  fireEvent.change(screen.getByLabelText(/Costs gems/), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => {
    const put = calls.find((c) => c.url === "/api/admin/rules/4" && c.init?.method === "PUT");
    const body = JSON.parse(String(put?.init?.body)) as Record<string, unknown>;
    expect(body).toMatchObject({ max_items: null, gem_cost: 3, replay_percent: 120, count_after_seconds: 10 });
  });
});

it("shows the gems reason in the right-now preview", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.startsWith("/api/admin/preview")) {
        return new Response(
          JSON.stringify([
            { group_id: 1, name: "Cartoons", status: { allowed: false, reason: "gems", params: { need: 2, have: 1 } } },
            { group_id: 2, name: "Music", status: { allowed: true, gem_cost: 1 } },
          ]),
        );
      }
      if (url === "/api/admin/gems?limit=10") return new Response(JSON.stringify(GEMS));
      return new Response("[]");
    }),
  );
  renderRules();
  expect(await screen.findByText("Needs 2 💎, has 1")).toBeInTheDocument();
  expect(screen.getByText("1 💎 per new file")).toBeInTheDocument();
});
