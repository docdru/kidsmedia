import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type DragEvent, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { api, thumbSrc, uploadFile, type Group, type GroupIn, type Kind, type Media } from "../api/client";
import { errorText } from "../lib/errors";
import { formatDuration } from "../lib/format";
import { CoverPicker, ThumbPicker } from "./Pickers";

const KINDS: Kind[] = ["audiobook", "music", "video"];
const COLORS = ["#4552C7", "#C74545", "#2E8B57", "#D98E04", "#8E44AD", "#127C8A"];

type Upload = { id: string; name: string; progress: number; error?: string };

export default function Library() {
  const { t } = useTranslation();
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => api<Group[]>("GET", "/api/admin/groups") });
  const [selected, setSelected] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);

  const list = groups.data ?? [];
  const current = list.find((g) => g.id === selected) ?? list[0];

  return (
    <div className="split">
      <aside className="card sidebar">
        <div className="row spread">
          <h2>{t("library.groups")}</h2>
          <button type="button" className="primary small" onClick={() => setCreating(true)}>
            {t("library.newGroup")}
          </button>
        </div>
        {groups.isPending && <p className="muted">{t("common.loading")}</p>}
        {list.length === 0 && !groups.isPending && <p className="muted">{t("library.noGroups")}</p>}
        <ul className="grouplist">
          {list.map((g) => (
            <li key={g.id}>
              <button
                type="button"
                className={g.id === current?.id ? "groupbtn active" : "groupbtn"}
                onClick={() => setSelected(g.id)}
              >
                <span className="swatch" style={{ background: g.color }}>
                  {g.cover && <img src={thumbSrc(g.cover)} alt="" />}
                </span>
                <span className="grow">
                  <strong>{g.name}</strong>
                  <small className="muted">
                    {t(`kind.${g.kind}`)} · {t("library.itemCount", { count: g.item_count })}
                  </small>
                </span>
              </button>
            </li>
          ))}
        </ul>
        {creating && (
          <NewGroup
            onDone={(id) => {
              setCreating(false);
              if (id) setSelected(id);
            }}
          />
        )}
      </aside>
      <section className="grow">
        {current ? <GroupPanel key={current.id} group={current} groups={list} /> : null}
      </section>
    </div>
  );
}

function NewGroup({ onDone }: { onDone: (id?: number) => void }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Kind>("audiobook");
  const create = useMutation({
    mutationFn: (body: GroupIn) => api<Group>("POST", "/api/admin/groups", body),
    onSuccess: (g) => {
      void queryClient.invalidateQueries({ queryKey: ["groups"] });
      onDone(g.id);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    create.mutate({ name: name.trim(), kind, color: COLORS[Math.floor(Math.random() * COLORS.length)] });
  };
  return (
    <form className="stack" onSubmit={submit}>
      <label>
        {t("library.groupName")}
        <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} autoFocus />
      </label>
      <label>
        {t("library.kind")}
        <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {t(`kind.${k}`)}
            </option>
          ))}
        </select>
      </label>
      {create.error && <p className="error">{errorText(create.error, t)}</p>}
      <div className="row">
        <button type="submit" className="primary" disabled={create.isPending || !name.trim()}>
          {t("common.create")}
        </button>
        <button type="button" onClick={() => onDone()}>
          {t("common.cancel")}
        </button>
      </div>
    </form>
  );
}

function GroupPanel({ group, groups }: { group: Group; groups: Group[] }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const mediaKey = ["media", group.id];
  const media = useQuery({
    queryKey: mediaKey,
    queryFn: () => api<Media[]>("GET", `/api/admin/groups/${group.id}/media`),
    // Thumbnails are made in the background; poll while any is pending.
    refetchInterval: (q) => (q.state.data?.some((m) => m.thumb_source === "pending") ? 3000 : false),
  });
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [dragging, setDragging] = useState(false);
  const [coverOpen, setCoverOpen] = useState(false);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: ["media"] });
    void queryClient.invalidateQueries({ queryKey: ["groups"] });
  };

  const patchGroup = useMutation({
    mutationFn: (body: { name?: string; resume?: boolean; color?: string; offline?: boolean }) =>
      api<Group>("PATCH", `/api/admin/groups/${group.id}`, body),
    onSuccess: refresh,
  });
  const deleteGroup = useMutation({
    mutationFn: () => api<void>("DELETE", `/api/admin/groups/${group.id}`),
    onSuccess: refresh,
  });
  const reorder = useMutation({
    mutationFn: (ids: number[]) => api<Media[]>("POST", "/api/admin/media/reorder", { group_id: group.id, ids }),
    onSuccess: (items) => queryClient.setQueryData(mediaKey, items),
  });

  const upload = async (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      const id = `${file.name}-${Math.random()}`;
      setUploads((u) => [...u, { id, name: file.name, progress: 0 }]);
      try {
        await uploadFile(group.id, file, (p) =>
          setUploads((u) => u.map((x) => (x.id === id ? { ...x, progress: p } : x))),
        );
        setUploads((u) => u.filter((x) => x.id !== id));
        refresh();
      } catch (e) {
        setUploads((u) => u.map((x) => (x.id === id ? { ...x, error: errorText(e, t) } : x)));
      }
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) void upload(e.dataTransfer.files);
  };

  const items = media.data ?? [];
  const move = (index: number, delta: number) => {
    const ids = items.map((m) => m.id);
    const j = index + delta;
    if (j < 0 || j >= ids.length) return;
    [ids[index], ids[j]] = [ids[j], ids[index]];
    reorder.mutate(ids);
  };

  return (
    <div className="card">
      <div className="row spread wrap">
        <div className="row">
          <button
            type="button"
            className="cover"
            style={{ background: group.color }}
            onClick={() => setCoverOpen(true)}
            title={t("pickers.changeCover")}
          >
            {group.cover && <img src={thumbSrc(group.cover, "md")} alt="" />}
          </button>
          <div>
            <InlineText
              value={group.name}
              label={t("library.groupName")}
              onSave={(name) => patchGroup.mutate({ name })}
              className="h2"
            />
            <p className="muted">
              {t(`kind.${group.kind}`)} · {t("library.itemCount", { count: group.item_count })} ·{" "}
              {formatDuration(group.total_duration_s)}
            </p>
          </div>
        </div>
        <div className="row wrap">
          <button type="button" onClick={() => setCoverOpen(true)}>
            {t("pickers.changeCover")}
          </button>
          <label className="check">
            <input
              type="checkbox"
              checked={group.resume}
              onChange={(e) => patchGroup.mutate({ resume: e.target.checked })}
            />
            {t("library.resume")}
          </label>
          <label className="check" title={t("library.offlineHelp")}>
            <input
              type="checkbox"
              checked={group.offline}
              onChange={(e) => patchGroup.mutate({ offline: e.target.checked })}
            />
            {t("library.offline")}
          </label>
          <button
            type="button"
            className="danger"
            onClick={() => {
              if (window.confirm(t("library.confirmDeleteGroup", { name: group.name }))) deleteGroup.mutate();
            }}
          >
            {t("library.deleteGroup")}
          </button>
        </div>
      </div>

      <label
        className={dragging ? "dropzone over" : "dropzone"}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <input
          type="file"
          multiple
          accept="audio/mpeg,.mp3,video/mp4,.mp4,audio/mp4,.m4a"
          onChange={(e) => {
            if (e.target.files) void upload(e.target.files);
            e.target.value = "";
          }}
        />
        <strong>{t("library.dropHere")}</strong>
        <span className="muted">{t("library.formats")}</span>
      </label>

      {uploads.length > 0 && (
        <ul className="uploads">
          {uploads.map((u) => (
            <li key={u.id}>
              <span className="grow">{u.name}</span>
              {u.error ? (
                <>
                  <span className="error">{u.error}</span>
                  <button type="button" className="link" onClick={() => setUploads((x) => x.filter((y) => y.id !== u.id))}>
                    {t("common.dismiss")}
                  </button>
                </>
              ) : (
                <progress max={1} value={u.progress} aria-label={u.name} />
              )}
            </li>
          ))}
        </ul>
      )}

      {media.isPending && <p className="muted">{t("common.loading")}</p>}
      {items.length === 0 && !media.isPending && <p className="muted">{t("library.empty")}</p>}
      {coverOpen && <CoverPicker group={group} items={items} onClose={() => setCoverOpen(false)} />}
      <table className="medialist">
        <tbody>
          {items.map((m, i) => (
            <MediaRow
              key={m.id}
              media={m}
              group={group}
              groups={groups}
              first={i === 0}
              last={i === items.length - 1}
              onMove={(d) => move(i, d)}
              onChanged={refresh}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MediaRow({
  media,
  group,
  groups,
  first,
  last,
  onMove,
  onChanged,
}: {
  media: Media;
  group: Group;
  groups: Group[];
  first: boolean;
  last: boolean;
  onMove: (delta: number) => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const patch = useMutation({
    mutationFn: (body: { title?: string; group_id?: number; offline?: boolean }) =>
      api<Media>("PATCH", `/api/admin/media/${media.id}`, body),
    onSuccess: onChanged,
  });
  const remove = useMutation({
    mutationFn: () => api<void>("DELETE", `/api/admin/media/${media.id}`),
    onSuccess: onChanged,
  });
  const [picking, setPicking] = useState(false);
  const video = group.kind === "video";
  return (
    <tr>
      <td className="thumbcell">
        <button
          type="button"
          className={video ? "thumb wide" : "thumb"}
          style={{ background: group.color }}
          disabled={media.thumb_source === "pending"}
          onClick={() => setPicking(true)}
          aria-label={t("pickers.thumbTitle", { title: media.title })}
        >
          {media.thumb_source === "pending" ? (
            <small>{t("library.makingThumb")}</small>
          ) : (
            media.thumb && <img src={thumbSrc(media.thumb)} alt="" />
          )}
        </button>
        {picking && <ThumbPicker media={media} group={group} onClose={() => setPicking(false)} />}
      </td>
      <td className="grow">
        <InlineText value={media.title} label={t("library.title")} onSave={(title) => patch.mutate({ title })} />
        <small className="muted">
          {formatDuration(media.duration_s)}
          {media.faststart === false && ` · ${t("library.notStreamable")}`}
        </small>
      </td>
      <td className="actions">
        <label className="check inline-check" title={t("library.offlineItemHelp")}>
          <input
            type="checkbox"
            checked={media.offline || group.offline}
            disabled={group.offline}
            onChange={(e) => patch.mutate({ offline: e.target.checked })}
          />
          {t("library.offlineShort")}
        </label>
        <button type="button" className="icon" disabled={first} onClick={() => onMove(-1)} aria-label={t("library.moveUp")}>
          ↑
        </button>
        <button type="button" className="icon" disabled={last} onClick={() => onMove(1)} aria-label={t("library.moveDown")}>
          ↓
        </button>
        <select
          aria-label={t("library.moveTo")}
          value={group.id}
          onChange={(e) => patch.mutate({ group_id: Number(e.target.value) })}
        >
          {groups.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="icon danger"
          aria-label={t("library.delete")}
          onClick={() => {
            if (window.confirm(t("library.confirmDelete", { title: media.title }))) remove.mutate();
          }}
        >
          ✕
        </button>
      </td>
    </tr>
  );
}

/** Text that turns into an input on click; Enter or blur saves, Escape cancels. */
export function InlineText({
  value,
  label,
  onSave,
  className,
}: {
  value: string;
  label: string;
  onSave: (v: string) => void;
  className?: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  if (draft === null) {
    return (
      <button type="button" className={`inline ${className ?? ""}`} onClick={() => setDraft(value)} title={label}>
        {value}
      </button>
    );
  }
  const commit = () => {
    const v = draft.trim();
    setDraft(null);
    if (v && v !== value) onSave(v);
  };
  return (
    <input
      aria-label={label}
      className={className}
      value={draft}
      autoFocus
      maxLength={200}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") commit();
        if (e.key === "Escape") setDraft(null);
      }}
    />
  );
}
