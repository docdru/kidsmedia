import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { api, putImage, thumbSrc, type Group, type Media } from "../api/client";
import { errorText } from "../lib/errors";
import { formatDuration } from "../lib/format";

const IMAGE_TYPES = "image/jpeg,image/png,image/webp";
const MAX_IMAGE_MB = 20; // SPEC §6.4

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const { t } = useTranslation();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="overlay" onClick={onClose}>
      <div className="card modal stack" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <div className="row spread">
          <h2>{title}</h2>
          <button type="button" className="icon" onClick={onClose} aria-label={t("common.close")}>
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function ImageInput({ label, onFile }: { label: string; onFile: (f: File) => void }) {
  const { t } = useTranslation();
  const [tooBig, setTooBig] = useState(false);
  return (
    <label className="button-like">
      {label}
      <input
        type="file"
        accept={IMAGE_TYPES}
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (!f) return;
          const big = f.size > MAX_IMAGE_MB * 1024 * 1024;
          setTooBig(big);
          if (!big) onFile(f);
        }}
      />
      {tooBig && <span className="error">{t("pickers.imageTooBig", { mb: MAX_IMAGE_MB })}</span>}
    </label>
  );
}

/** Thumbnail picker for one file (SPEC §6.4 "Parent overrides"). */
export function ThumbPicker({ media, group, onClose }: { media: Media; group: Group; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const video = group.kind === "video";
  const durationMs = Math.round(media.duration_s * 1000);
  const [t_ms, setTms] = useState<number>(media.thumb_frame_ms ?? Math.round(durationMs * 0.1));
  const [current, setCurrent] = useState<Media>(media);

  const frames = useQuery({
    queryKey: ["frames", media.id],
    queryFn: () => api<{ t_ms: number; url: string }[]>("GET", `/api/admin/media/${media.id}/frames?count=8`),
    enabled: video,
    staleTime: Infinity,
  });

  const done = (m: Media | Media[]) => {
    if (!Array.isArray(m)) setCurrent(m);
    void queryClient.invalidateQueries({ queryKey: ["media"] });
    void queryClient.invalidateQueries({ queryKey: ["groups"] });
  };
  const choose = useMutation({
    mutationFn: (body: { source: "embedded" | "frame"; t_ms?: number }) =>
      api<Media>("POST", `/api/admin/media/${media.id}/thumb`, body),
    onSuccess: done,
  });
  const upload = useMutation({
    mutationFn: (file: File) => putImage<Media>(`/api/admin/media/${media.id}/thumb`, file),
    onSuccess: done,
  });
  const automatic = useMutation({
    mutationFn: () => api<Media>("DELETE", `/api/admin/media/${media.id}/thumb`),
    onSuccess: done,
  });
  const applyToGroup = useMutation({
    mutationFn: () => api<Media[]>("POST", `/api/admin/groups/${group.id}/thumbs/apply`, { media_id: media.id }),
    onSuccess: done,
  });
  const error = choose.error ?? upload.error ?? automatic.error ?? applyToGroup.error;
  const busy = choose.isPending || upload.isPending || automatic.isPending || applyToGroup.isPending;

  return (
    <Modal title={t("pickers.thumbTitle", { title: media.title })} onClose={onClose}>
      <div className="row wrap top">
        <span className={video ? "preview wide" : "preview"} style={{ background: group.color }}>
          {current.thumb ? <img src={thumbSrc(current.thumb, "md")} alt={t("pickers.current")} /> : <small>{t("pickers.none")}</small>}
        </span>
        <div className="stack grow">
          <p className="muted">{t(`pickers.source.${current.thumb_source}`)}</p>
          <div className="row wrap">
            {media.has_embedded_art && (
              <button type="button" disabled={busy} onClick={() => choose.mutate({ source: "embedded" })}>
                {t("pickers.fromFile")}
              </button>
            )}
            <ImageInput label={t("pickers.upload")} onFile={(f) => upload.mutate(f)} />
            <button type="button" disabled={busy} onClick={() => automatic.mutate()}>
              {t("pickers.automatic")}
            </button>
          </div>
          {current.thumb_source === "upload" && (
            <button type="button" disabled={busy} onClick={() => applyToGroup.mutate()}>
              {t("pickers.useForGroup")}
            </button>
          )}
        </div>
      </div>

      {video && (
        <section className="stack">
          <h3>{t("pickers.videoFrame")}</h3>
          <div className="frames">
            {frames.data?.map((f) => (
              <button
                key={f.t_ms}
                type="button"
                className={f.t_ms === t_ms ? "frame on" : "frame"}
                onClick={() => setTms(f.t_ms)}
                aria-label={formatDuration(f.t_ms / 1000)}
              >
                <img src={`${f.url}&width=160`} alt="" loading="lazy" />
              </button>
            ))}
          </div>
          <label>
            {t("pickers.scrub", { time: formatDuration(t_ms / 1000) })}
            <input type="range" min={0} max={Math.max(0, durationMs - 1)} step={500} value={t_ms} onChange={(e) => setTms(Number(e.target.value))} />
          </label>
          <img className="scrubview" src={`/api/admin/media/${media.id}/frame?t_ms=${t_ms}&width=480`} alt={t("pickers.framePreview")} />
          <button type="button" className="primary" disabled={busy} onClick={() => choose.mutate({ source: "frame", t_ms })}>
            {t("pickers.useFrame")}
          </button>
        </section>
      )}
      {error && <p className="error">{errorText(error, t)}</p>}
    </Modal>
  );
}

/** Group cover: automatic, another item's thumbnail, or an upload (SPEC §6.4). */
export function CoverPicker({ group, items, onClose }: { group: Group; items: Media[]; onClose: () => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const done = () => {
    void queryClient.invalidateQueries({ queryKey: ["groups"] });
    onClose();
  };
  const pick = useMutation({
    mutationFn: (mediaId: number) => api<Group>("POST", `/api/admin/groups/${group.id}/cover`, { media_id: mediaId }),
    onSuccess: done,
  });
  const upload = useMutation({
    mutationFn: (file: File) => putImage<Group>(`/api/admin/groups/${group.id}/cover`, file),
    onSuccess: done,
  });
  const automatic = useMutation({
    mutationFn: () => api<Group>("DELETE", `/api/admin/groups/${group.id}/cover`),
    onSuccess: done,
  });
  const error = pick.error ?? upload.error ?? automatic.error;
  const withThumb = items.filter((m) => m.thumb);

  return (
    <Modal title={t("pickers.coverTitle", { name: group.name })} onClose={onClose}>
      <p className="muted">{t(`pickers.cover.${group.cover_source}`)}</p>
      <div className="row wrap">
        <ImageInput label={t("pickers.upload")} onFile={(f) => upload.mutate(f)} />
        <button type="button" onClick={() => automatic.mutate()} disabled={group.cover_source === "auto"}>
          {t("pickers.automatic")}
        </button>
      </div>
      {withThumb.length > 0 && (
        <>
          <h3>{t("pickers.fromItem")}</h3>
          <div className="frames">
            {withThumb.map((m) => (
              <button
                key={m.id}
                type="button"
                className={group.cover_source === "item" && group.cover_media_id === m.id ? "frame on" : "frame"}
                onClick={() => pick.mutate(m.id)}
                title={m.title}
              >
                <img src={thumbSrc(m.thumb)} alt={m.title} loading="lazy" />
              </button>
            ))}
          </div>
        </>
      )}
      {error && <p className="error">{errorText(error, t)}</p>}
    </Modal>
  );
}
