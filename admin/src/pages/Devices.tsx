import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, putImage, type Device, type Schemas } from "../api/client";
import { errorText } from "../lib/errors";
import { InlineText } from "./Library";

function useNow(intervalMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function countdown(expiresAt: string, now: number): string {
  const left = Math.max(0, Math.floor((Date.parse(expiresAt) - now) / 1000));
  return `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`;
}

const VOLUME_STEPS = [90, 80, 70, 60, 50, 40, 30, 20];

/** The usual steps, plus the current cap if it was set to something else via the API. */
export function volumeChoices(current: number | null | undefined): number[] {
  const steps = current == null || VOLUME_STEPS.includes(current) ? VOLUME_STEPS : [...VOLUME_STEPS, current];
  return [...steps].sort((a, b) => b - a);
}

export default function Devices() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: () => api<Device[]>("GET", "/api/admin/devices"),
    // While a code is shown, notice the tablet pairing without a reload.
    refetchInterval: (q) => (q.state.data?.some((d) => d.pairing) ? 3000 : false),
  });
  const [name, setName] = useState("");
  const now = useNow(1000);

  const changed = () => void queryClient.invalidateQueries({ queryKey: ["devices"] });
  const create = useMutation({
    mutationFn: () => api<Device>("POST", "/api/admin/devices", { name: name.trim() || null }),
    onSuccess: () => {
      setName("");
      changed();
    },
  });
  const rename = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) => api<Device>("PATCH", `/api/admin/devices/${id}`, { name }),
    onSuccess: changed,
  });
  const setVolume = useMutation({
    mutationFn: ({ id, percent }: { id: number; percent: number | null }) =>
      api<Device>("PATCH", `/api/admin/devices/${id}`, { max_volume_percent: percent }),
    onSuccess: changed,
  });
  const newCode = useMutation({
    mutationFn: (id: number) => api<Device>("POST", `/api/admin/devices/${id}/pairing-code`),
    onSuccess: changed,
  });
  const revoke = useMutation({
    mutationFn: (id: number) => api<void>("DELETE", `/api/admin/devices/${id}`),
    onSuccess: changed,
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate();
  };
  const when = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString(i18n.resolvedLanguage, { dateStyle: "medium", timeStyle: "short" }) : t("devices.never");

  return (
    <div className="stack narrow">
      <h2>{t("devices.title")}</h2>
      <form className="card row wrap" onSubmit={submit}>
        <label className="grow">
          {t("devices.name")}
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t("devices.namePlaceholder")} maxLength={80} />
        </label>
        <button type="submit" className="primary" disabled={create.isPending}>
          {t("devices.pair")}
        </button>
        {create.error && <p className="error">{errorText(create.error, t)}</p>}
      </form>
      {devices.isPending && <p className="muted">{t("common.loading")}</p>}
      {devices.data?.length === 0 && <p className="muted">{t("devices.none")}</p>}
      <AppUpdate />
      {devices.data?.map((d) => (
        <article key={d.id} className={d.status === "revoked" ? "card off" : "card"}>
          <div className="row spread wrap">
            <div className="grow">
              <InlineText value={d.name} label={t("devices.name")} onSave={(n) => rename.mutate({ id: d.id, name: n })} className="h3" />
              <p className="muted">
                {t(`devices.status.${d.status}`, { defaultValue: d.status })}
                {d.status === "active" && ` · ${t("devices.lastSeen", { when: when(d.last_seen_at) })}`}
                {d.app_version && ` · ${t("devices.version", { version: d.app_version })}`}
              </p>
            </div>
            {d.status !== "revoked" && (
              <div className="row">
                {d.status === "waiting_for_pairing" && !d.pairing && (
                  <button type="button" onClick={() => newCode.mutate(d.id)}>
                    {t("devices.newCode")}
                  </button>
                )}
                <button
                  type="button"
                  className="danger"
                  onClick={() => {
                    if (window.confirm(t("devices.confirmRevoke", { name: d.name }))) revoke.mutate(d.id);
                  }}
                >
                  {t("devices.revoke")}
                </button>
              </div>
            )}
          </div>
          {d.status === "active" && (
            <label>
              {t("devices.maxVolume")}
              <select
                value={d.max_volume_percent ?? ""}
                disabled={setVolume.isPending}
                onChange={(e) => setVolume.mutate({ id: d.id, percent: e.target.value ? Number(e.target.value) : null })}
              >
                <option value="">{t("devices.noVolumeLimit")}</option>
                {volumeChoices(d.max_volume_percent).map((p) => (
                  <option key={p} value={p}>
                    {p} %
                  </option>
                ))}
              </select>
              <span className="muted">{t("devices.volumeHint")}</span>
            </label>
          )}
          {setVolume.error && <p className="error">{errorText(setVolume.error, t)}</p>}
          {d.pairing && (
            <div className="pairing">
              <p>{t("devices.enterCode")}</p>
              <output className="code" aria-label={t("devices.code")}>
                {d.pairing.code}
              </output>
              <p className="muted">{t("devices.expiresIn", { time: countdown(d.pairing.expires_at, now) })}</p>
            </div>
          )}
        </article>
      ))}
    </div>
  );
}

type Release = Schemas["Release"];

/** Self-update (SPEC §4.3): upload a signed APK; tablets offer it behind the parent gate. */
function AppUpdate() {
  const { t, i18n } = useTranslation();
  const queryClient = useQueryClient();
  const latest = useQuery({ queryKey: ["app-release"], queryFn: () => api<Release | null>("GET", "/api/admin/app") });
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const upload = useMutation({
    mutationFn: () =>
      putImage<Release>(
        `/api/admin/app/apk?version_name=${encodeURIComponent(name.trim())}&version_code=${Number(code)}`,
        file as File,
      ),
    onSuccess: (r) => {
      queryClient.setQueryData(["app-release"], r);
      setFile(null);
    },
  });
  const onFile = (f: File | undefined) => {
    setFile(f ?? null);
    // "kidsmedia-1.2.3.apk" (release.yml) → 1.2.3 and 10203
    const m = f?.name.match(/(\d+)\.(\d+)\.(\d+)/);
    if (m) {
      setName(`${m[1]}.${m[2]}.${m[3]}`);
      setCode(String(Number(m[1]) * 10000 + Number(m[2]) * 100 + Number(m[3])));
    }
  };
  const r = latest.data;
  return (
    <section className="card stack">
      <h3>{t("update.title")}</h3>
      <p className="muted">
        {r
          ? t("update.current", {
              name: r.version_name,
              code: r.version_code,
              when: new Date(r.uploaded_at).toLocaleDateString(i18n.resolvedLanguage),
            })
          : t("update.none")}
      </p>
      <div className="row wrap">
        <label className="button-like">
          {file ? file.name : t("update.choose")}
          <input type="file" accept=".apk,application/vnd.android.package-archive" hidden onChange={(e) => onFile(e.target.files?.[0])} />
        </label>
        <label>
          {t("update.versionName")}
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="1.2.3" size={10} />
        </label>
        <label>
          {t("update.versionCode")}
          <input value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} placeholder="10203" size={10} />
        </label>
        <button
          type="button"
          className="primary"
          disabled={!file || !name.trim() || !code || upload.isPending}
          onClick={() => upload.mutate()}
        >
          {upload.isPending ? t("update.uploading") : t("update.upload")}
        </button>
      </div>
      {upload.error && <p className="error">{errorText(upload.error, t)}</p>}
    </section>
  );
}

