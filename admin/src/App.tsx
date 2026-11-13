import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, api, setCsrfToken, type Me } from "./api/client";
import { SUPPORTED_LOCALES, rememberLocale, type Locale } from "./i18n";
import Activity from "./pages/Activity";
import Devices from "./pages/Devices";
import Ideas from "./pages/Ideas";
import Library from "./pages/Library";
import Login from "./pages/Login";
import Rewards from "./pages/Rewards";
import Rules from "./pages/Rules";
import "./styles.css";

const TABS = ["library", "rules", "ideas", "rewards", "devices", "activity"] as const;
type Tab = (typeof TABS)[number];

function tabFromHash(): Tab {
  const name = window.location.hash.replace(/^#\/?/, "");
  return (TABS as readonly string[]).includes(name) ? (name as Tab) : "library";
}

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
        refetchOnWindowFocus: true,
      },
    },
  });
}

export default function App({ client }: { client?: QueryClient }) {
  const [queryClient] = useState(() => client ?? makeQueryClient());
  return (
    <QueryClientProvider client={queryClient}>
      <Shell />
    </QueryClientProvider>
  );
}

/** Loads the session; without one, shows the login form (SPEC §4.2). */
function Shell() {
  const { t } = useTranslation();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api<Me>("GET", "/api/admin/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
  });

  useEffect(() => {
    setCsrfToken(me.data?.csrf_token ?? null);
    // A language chosen earlier by this admin user wins over the browser (decision 13).
    if (me.data?.locale) rememberLocale(me.data.locale);
  }, [me.data]);

  if (me.isPending) return <p className="center muted">{t("common.loading")}</p>;
  if (me.isError) return <p className="center error">{t("common.serverDown")}</p>;
  if (!me.data) return <Login />;
  return <Main me={me.data} />;
}

function Main({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>(tabFromHash);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const logout = useMutation({
    mutationFn: () => api<void>("POST", "/api/admin/logout"),
    onSettled: () => {
      setCsrfToken(null);
      queryClient.clear();
      queryClient.setQueryData(["me"], null);
    },
  });

  const switchLanguage = (locale: Locale) => {
    rememberLocale(locale);
    // Remembered per admin user on the server (decision 13).
    void api<Me>("PATCH", "/api/admin/me", { locale }).catch(() => undefined);
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <strong>{t("app.title")}</strong>
          <span className="muted">{t("app.subtitle")}</span>
        </div>
        <nav className="tabs" aria-label={t("nav.label")}>
          {TABS.map((name) => (
            <a key={name} href={`#/${name}`} className={name === tab ? "tab active" : "tab"} aria-current={name === tab ? "page" : undefined}>
              {t(`nav.${name}`)}
            </a>
          ))}
        </nav>
        <div className="tools">
          <select
            aria-label={t("language.label")}
            value={i18n.resolvedLanguage}
            onChange={(e) => switchLanguage(e.target.value as Locale)}
          >
            {SUPPORTED_LOCALES.map((l) => (
              <option key={l} value={l}>
                {t(`language.${l}`)}
              </option>
            ))}
          </select>
          <button type="button" className="link" onClick={() => logout.mutate()} title={me.username}>
            {t("auth.logout")}
          </button>
        </div>
      </header>
      <main className="content">
        {tab === "library" && <Library />}
        {tab === "rules" && <Rules />}
        {tab === "ideas" && <Ideas />}
        {tab === "rewards" && <Rewards />}
        {tab === "devices" && <Devices />}
        {tab === "activity" && <Activity />}
      </main>
    </div>
  );
}
