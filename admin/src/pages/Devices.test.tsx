import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import i18n from "../i18n";
import Devices, { volumeChoices } from "./Devices";

const DEVICE = {
  id: 2,
  name: "Kids tablet",
  profile_id: 1,
  status: "active",
  app_version: "0.2.0",
  created_at: "2026-11-01T10:00:00Z",
  paired_at: "2026-11-01T10:01:00Z",
  last_seen_at: "2026-11-05T09:00:00Z",
  max_volume_percent: null,
  pairing: null,
};

let calls: { url: string; init?: RequestInit }[] = [];

beforeEach(async () => {
  await i18n.changeLanguage("en");
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/admin/devices") return new Response(JSON.stringify([DEVICE]));
      if (url === "/api/admin/devices/2") return new Response(JSON.stringify({ ...DEVICE, max_volume_percent: 60 }));
      return new Response("null");
    }),
  );
});
afterEach(() => vi.unstubAllGlobals());

it("sets and clears a tablet's volume cap", async () => {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Devices />
    </QueryClientProvider>,
  );
  const select = await screen.findByLabelText(/Max volume/);
  fireEvent.change(select, { target: { value: "60" } });
  await waitFor(() => {
    const patch = calls.find((c) => c.url === "/api/admin/devices/2" && c.init?.method === "PATCH");
    expect(JSON.parse(String(patch?.init?.body))).toEqual({ max_volume_percent: 60 });
  });
  calls = [];
  fireEvent.change(select, { target: { value: "" } });
  await waitFor(() => {
    const patch = calls.find((c) => c.url === "/api/admin/devices/2" && c.init?.method === "PATCH");
    expect(JSON.parse(String(patch?.init?.body))).toEqual({ max_volume_percent: null });
  });
});

it("keeps an unusual cap selectable", () => {
  expect(volumeChoices(null)).toEqual([90, 80, 70, 60, 50, 40, 30, 20]);
  expect(volumeChoices(65)).toEqual([90, 80, 70, 65, 60, 50, 40, 30, 20]);
});
