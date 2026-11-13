import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import i18n from "../i18n";
import Rewards from "./Rewards";

const RULE = {
  id: 4,
  name: "Stories",
  days: [0, 1, 2, 3, 4, 5, 6],
  enabled: true,
  all_groups: false,
  group_ids: [1],
  mode: "limit",
  max_items: 1,
  max_minutes: null,
  lockouts: [],
  grace_minutes: 0,
  count_after_seconds: 10,
  replay_percent: 120,
  message_en: null,
  message_de: null,
  message_el: null,
};

const GROUP = { id: 1, name: "Moon", kind: "audiobook" };

const STATE = {
  stars: 7,
  earned: 12,
  spent: 5,
  swaps: [{ id: 2, stars: 5, rule_id: 4, extra_items: 1, extra_minutes: 0, max_per_day: 1, enabled: true }],
  wishes: [{ id: 3, texts: { en: { name: "Choose dessert" }, de: { name: "Nachtisch aussuchen" } }, stars: 5, enabled: true }],
  requests: [{ id: 9, wish_id: 3, status: "pending", created_at: "2026-11-06T15:00:00Z", decided_at: null }],
  ledger: [{ id: 1, delta: -5, kind: "swap", ref_id: 2, created_at: "2026-11-05T16:00:00Z" }],
};

let calls: { url: string; init?: RequestInit }[] = [];
let approveAnswer: () => Response = () => new Response(JSON.stringify(STATE));

beforeEach(async () => {
  await i18n.changeLanguage("en");
  calls = [];
  approveAnswer = () => new Response(JSON.stringify({ ...STATE, stars: 2, spent: 10, requests: [] }));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/admin/rewards") return new Response(JSON.stringify(STATE));
      if (url === "/api/admin/rules") return new Response(JSON.stringify([RULE]));
      if (url === "/api/admin/groups") return new Response(JSON.stringify([GROUP]));
      if (url === "/api/admin/rewards/requests/9/approve") return approveAnswer();
      if (url === "/api/admin/rewards/swaps") return new Response(JSON.stringify({ id: 5 }), { status: 201 });
      if (url === "/api/admin/rewards/wishes") return new Response(JSON.stringify({ id: 6 }), { status: 201 });
      return new Response("null");
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

function renderRewards() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Rewards />
    </QueryClientProvider>,
  );
}

function bodyOf(url: string) {
  const post = calls.find((c) => c.url === url && c.init?.method === "POST");
  return JSON.parse(String(post?.init?.body)) as unknown;
}

it("shows the balance and an open wish request (SPEC §14.4)", async () => {
  renderRewards();
  expect(await screen.findByText("7 stars now")).toBeInTheDocument();
  expect(screen.getByText("Earned 12 · spent 5")).toBeInTheDocument();
  const requests = screen.getByRole("region", { name: "Open wishes" });
  expect(within(requests).getByText("Choose dessert")).toBeInTheDocument();
  expect(within(requests).getByRole("button", { name: "Approve" })).toBeInTheDocument();
  expect(within(requests).getByRole("button", { name: "Decline" })).toBeInTheDocument();
});

it("shows the wish's name in the UI language", async () => {
  await i18n.changeLanguage("de");
  renderRewards();
  const requests = await screen.findByRole("region", { name: "Offene Wünsche" });
  expect(within(requests).getByText("Nachtisch aussuchen")).toBeInTheDocument();
});

it("approves a request and shows the new balance", async () => {
  renderRewards();
  const requests = await screen.findByRole("region", { name: "Open wishes" });
  fireEvent.click(within(requests).getByRole("button", { name: "Approve" }));
  await waitFor(() =>
    expect(calls.some((c) => c.url === "/api/admin/rewards/requests/9/approve" && c.init?.method === "POST")).toBe(true),
  );
  expect(await screen.findByText("2 stars now")).toBeInTheDocument();
  expect(screen.getByText("No wishes waiting.")).toBeInTheDocument();
});

it("explains not_enough_stars when approving", async () => {
  approveAnswer = () =>
    new Response(JSON.stringify({ reason: "not_enough_stars", params: { stars: 3, needed: 5 } }), { status: 409 });
  renderRewards();
  const requests = await screen.findByRole("region", { name: "Open wishes" });
  fireEvent.click(within(requests).getByRole("button", { name: "Approve" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Not enough stars yet: 3 saved, 5 needed.");
});

it("creates a swap for extra minutes with no daily limit", async () => {
  renderRewards();
  fireEvent.click(await screen.findByRole("button", { name: "New swap" }));
  await screen.findByRole("option", { name: "Stories (Moon)" });
  fireEvent.change(screen.getByLabelText(/^Rule/), { target: { value: "4" } });
  fireEvent.click(screen.getByLabelText("Extra minutes"));
  fireEvent.change(screen.getByLabelText("Amount"), { target: { value: "20" } });
  fireEvent.change(within(document.querySelector("form.editor") as HTMLElement).getByLabelText("Stars"), { target: { value: "8" } });
  fireEvent.change(screen.getByLabelText("Max per day"), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(bodyOf("/api/admin/rewards/swaps")).toEqual({
      stars: 8,
      rule_id: 4,
      extra_items: 0,
      extra_minutes: 20,
      max_per_day: null,
      enabled: true,
    }),
  );
});

it("creates a wish with names in the filled-in languages only", async () => {
  renderRewards();
  fireEvent.click(await screen.findByRole("button", { name: "New wish" }));
  fireEvent.change(screen.getByLabelText("English"), { target: { value: "Pick a film" } });
  fireEvent.change(screen.getByLabelText("Deutsch"), { target: { value: "Film aussuchen" } });
  fireEvent.change(within(document.querySelector("form.editor") as HTMLElement).getByLabelText("Stars"), { target: { value: "15" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(bodyOf("/api/admin/rewards/wishes")).toEqual({
      texts: { en: { name: "Pick a film" }, de: { name: "Film aussuchen" } },
      stars: 15,
      enabled: true,
    }),
  );
});

it("lists the ledger with the swap it paid for", async () => {
  renderRewards();
  const ledger = await screen.findByRole("region", { name: "Spent stars" });
  expect(await within(ledger).findByText("Swap: Stories (Moon), 1 more file today")).toBeInTheDocument();
  expect(within(ledger).getByText("−5")).toBeInTheDocument();
});
