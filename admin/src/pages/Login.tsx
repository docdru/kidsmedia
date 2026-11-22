import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, api, setCsrfToken, type AuthConfig, type Me } from "../api/client";

/** Password login only, for servers that don't know /api/admin/auth yet or can't answer it. */
const PASSWORD_ONLY: AuthConfig = { password: true, sso_label: null };

/** `?sso_error=` from the sign-in callback (SPEC §7.1), read once and removed from the URL. */
function takeSsoError(): string | null {
  const params = new URLSearchParams(window.location.search);
  const error = params.get("sso_error");
  if (error === null) return null;
  params.delete("sso_error");
  const query = params.toString();
  window.history.replaceState(null, "", window.location.pathname + (query ? `?${query}` : "") + window.location.hash);
  return error;
}

export default function Login() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [ssoError] = useState(takeSsoError);
  const config = useQuery({
    queryKey: ["auth-config"],
    queryFn: async () => {
      try {
        return await api<AuthConfig>("GET", "/api/admin/auth");
      } catch {
        return PASSWORD_ONLY;
      }
    },
  });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const login = useMutation({
    mutationFn: () => api<Me>("POST", "/api/admin/login", { username, password }),
    onSuccess: (me) => {
      setCsrfToken(me.csrf_token);
      queryClient.setQueryData(["me"], me);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };

  const error = login.error;
  const message =
    error instanceof ApiError && error.status === 429
      ? t("auth.rateLimited")
      : error instanceof ApiError && error.status === 401
        ? t("auth.wrong")
        : error
          ? t("common.serverDown")
          : null;

  const ssoMessage =
    ssoError === "not_allowed" ? t("auth.ssoNotAllowed") : ssoError !== null ? t("auth.ssoFailed") : null;

  if (config.isPending) return <p className="center muted">{t("common.loading")}</p>;
  const auth = config.data ?? PASSWORD_ONLY;

  if (auth.sso_label && auth.password === false) {
    return (
      <div className="login card">
        <h1>{t("app.title")}</h1>
        <p className="muted">{t("app.subtitle")}</p>
        {ssoMessage && (
          <p role="alert" className="error">
            {ssoMessage}
          </p>
        )}
        {/* A full page load: the browser goes to the provider and comes back signed in. */}
        <a className="button-like primary sso" href="/api/admin/oidc/login">
          {t("auth.sso", { name: auth.sso_label ?? "SSO" })}
        </a>
      </div>
    );
  }

  return (
    <form className="login card" onSubmit={submit}>
      <h1>{t("app.title")}</h1>
      <p className="muted">{t("app.subtitle")}</p>
      <label>
        {t("auth.username")}
        <input autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
      </label>
      <label>
        {t("auth.password")}
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
      </label>
      {message && (
        <p role="alert" className="error">
          {message}
        </p>
      )}
      <button type="submit" className="primary" disabled={login.isPending}>
        {t("auth.login")}
      </button>
    </form>
  );
}
