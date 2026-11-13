import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { ApiError, api, setCsrfToken, type Me } from "../api/client";

export default function Login() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
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
