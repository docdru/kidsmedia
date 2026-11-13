import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App, { makeQueryClient } from "./App";
import i18n from "./i18n";

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function serve(handler: Handler) {
  const fetchMock = vi.fn(async (url: string | URL | Request, init?: RequestInit) => handler(String(url), init));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderApp() {
  const client = makeQueryClient();
  client.setDefaultOptions({ queries: { retry: false } });
  return render(<App client={client} />);
}

beforeEach(async () => {
  window.location.hash = "";
  await i18n.changeLanguage("en");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const ME = { username: "parent", csrf_token: "csrf-123", locale: null };

it("shows the login form without a session", async () => {
  serve(() => json({ reason: "not_authenticated", params: {} }, 401));
  renderApp();
  expect(await screen.findByLabelText("Password")).toBeInTheDocument();
});

it("logs in and sends the CSRF token on mutations", async () => {
  let loggedIn = false;
  const fetchMock = serve((url, init) => {
    if (url === "/api/admin/login") {
      loggedIn = true;
      return json(ME);
    }
    if (url === "/api/admin/me") return loggedIn ? json(ME) : json({ reason: "not_authenticated" }, 401);
    if (url === "/api/admin/groups" && init?.method === "POST") return json({ id: 9 });
    if (url === "/api/admin/groups") return json([]);
    return json([]);
  });
  renderApp();
  fireEvent.change(await screen.findByLabelText("User name"), { target: { value: "parent" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "secret" } });
  fireEvent.click(screen.getByRole("button", { name: "Log in" }));
  expect(await screen.findByText("No groups yet. Create one to start uploading.")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "New group" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Moon Pirates" } });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => {
    const post = fetchMock.mock.calls.find(([u, i]) => u === "/api/admin/groups" && i?.method === "POST");
    expect(post).toBeDefined();
    expect((post?.[1]?.headers as Record<string, string>)["X-CSRF-Token"]).toBe("csrf-123");
  });
});

it("shows a wrong-password message from the reason code", async () => {
  serve((url) =>
    url === "/api/admin/login"
      ? json({ reason: "bad_credentials", params: {} }, 401)
      : json({ reason: "not_authenticated" }, 401),
  );
  renderApp();
  fireEvent.change(await screen.findByLabelText("User name"), { target: { value: "x" } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: "y" } });
  fireEvent.click(screen.getByRole("button", { name: "Log in" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Wrong user name or password.");
});

it("uses the admin's saved language", async () => {
  serve((url) => (url === "/api/admin/me" ? json({ ...ME, locale: "de" }) : json([])));
  renderApp();
  expect(await screen.findByText("Eltern-Verwaltung")).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: "Regeln" })).toBeInTheDocument();
});

it("switches the header to Greek and remembers it for the admin user (decision 13)", async () => {
  const fetchMock = serve((url) => (url === "/api/admin/me" ? json(ME) : json([])));
  renderApp();
  const select = await screen.findByLabelText("Language");
  expect(screen.getByRole("option", { name: "Ελληνικά" })).toBeInTheDocument();
  fireEvent.change(select, { target: { value: "el" } });
  expect(await screen.findByRole("link", { name: "Κανόνες" })).toBeInTheDocument();
  expect(screen.getByText("Διαχείριση γονέων")).toBeInTheDocument();
  await waitFor(() => {
    const patch = fetchMock.mock.calls.find(([u, i]) => u === "/api/admin/me" && i?.method === "PATCH");
    expect(JSON.parse(String(patch?.[1]?.body))).toEqual({ locale: "el" });
  });
});
