import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { Group, Media } from "../api/client";
import i18n from "../i18n";
import { ThumbPicker } from "./Pickers";

const group: Group = {
  id: 3,
  name: "Cartoons",
  kind: "video",
  color: "#C74545",
  cover: null,
  cover_media_id: null,
  cover_source: "auto",
  item_count: 1,
  resume: true,
  offline: false,
  sort: 0,
  total_duration_s: 600,
};
const media: Media = {
  id: 7,
  group_id: 3,
  title: "Episode 1",
  duration_s: 600,
  faststart: true,
  has_embedded_art: false,
  width: 1280,
  height: 720,
  mime: "video/mp4",
  size: 1000,
  sort: 0,
  thumb: null,
  thumb_frame_ms: null,
  thumb_source: "none",
  offline: false,
};

beforeEach(async () => {
  await i18n.changeLanguage("en");
});
afterEach(() => vi.unstubAllGlobals());

it("sets a chosen video frame as the thumbnail (SPEC §6.4)", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.includes("/frames")) {
        return new Response(JSON.stringify([{ t_ms: 60000, url: "/api/admin/media/7/frame?t_ms=60000" }]));
      }
      return new Response(JSON.stringify({ ...media, thumb_source: "frame", thumb_frame_ms: 60000 }));
    }),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ThumbPicker media={media} group={group} onClose={() => undefined} />
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "1:00" }));
  fireEvent.click(screen.getByRole("button", { name: "Use this frame" }));
  await waitFor(() => {
    const post = calls.find((c) => c.init?.method === "POST");
    expect(post?.url).toBe("/api/admin/media/7/thumb");
    expect(JSON.parse(String(post?.init?.body))).toEqual({ source: "frame", t_ms: 60000 });
  });
  expect(await screen.findByText("Chosen video frame")).toBeInTheDocument();
  // No embedded art in this file, so "From file" isn't offered.
  expect(screen.queryByRole("button", { name: "From file" })).toBeNull();
});
