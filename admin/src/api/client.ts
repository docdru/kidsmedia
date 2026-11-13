import type { components } from "./schema";

/** Server types, generated from openapi.json (SPEC §4.2). */
export type Schemas = components["schemas"];
export type Me = Schemas["Me"];
export type Group = Schemas["GroupOut"];
export type GroupIn = Schemas["GroupIn"];
export type GroupPatch = Schemas["GroupPatch"];
export type Media = Schemas["MediaOut"];
export type MediaPatch = Schemas["MediaPatch"];
export type Rule = Schemas["RuleOut"];
export type RuleIn = Schemas["RuleIn"];
export type Device = Schemas["DeviceOut"];
export type GroupStatus = Schemas["GroupStatus"];
export type Status = Schemas["Status"];
export type UsageDay = Schemas["UsageDay"];
export type HistoryDay = Schemas["HistoryDay"];
export type Kind = Group["kind"];
export type ActivitiesAdmin = Schemas["ActivitiesAdminOut"];
export type DayOff = Schemas["DayOffOut"];
export type QueueEntry = Schemas["QueueEntry"];
export type WorldSummary = Schemas["WorldSummary"];
export type WorldFull = Schemas["WorldFull"];
export type Pack = Schemas["Pack"];
export type PackChapter = Schemas["PackChapter"];
export type PackTask = Schemas["PackTask"];
export type PackWarning = Schemas["PackWarning"];
export type ImportReport = Schemas["ImportReport"];
export type ExampleOut = Schemas["ExampleOut"];
export type TaskKind = PackTask["kind"];
export type SettingsIn = Schemas["SettingsIn"];
export type Level = Schemas["SettingsOut"]["level"];
export type Window = Schemas["Window"];
export type LogRow = Schemas["LogRow"];
export type WorldChapter = Schemas["WorldChapter"];
export type EditorTask = Schemas["EditorTask"];
export type EditorTaskTexts = Schemas["EditorTaskTexts"];
export type Question = Schemas["Question"];
export type QuestionAudio = Schemas["QuestionAudio"];
/** One story problem of a calc task (`problems` per level, SPEC §15.2). */
export type Problem = NonNullable<NonNullable<EditorTaskTexts["problems"]>[Level]>[number];
/** The rendered audio of one problem (output-only `problem_audio` per level, SPEC §15.2). */
export type ProblemAudio = NonNullable<NonNullable<NonNullable<EditorTaskTexts["problem_audio"]>[Level]>[number]>;
/** A done task in GET /api/admin/activities/done, with its stars and photos (SPEC §17.3). */
export type DoneEntry = Schemas["DoneOut"];
/** One page of done tasks, newest first; `next_before_id` is null on the last page (SPEC §17.3). */
export type DonePage = Schemas["DoneListOut"];
/** A photo of a drawing or a story of a done task (SPEC §15.3). */
export type DonePhoto = DoneEntry["photos"][number];
export type SpeechStatus = Schemas["SpeechStatus"];
export type SpeechVoice = Schemas["SpeechVoiceOut"];
export type RewardsAdmin = Schemas["RewardsAdminOut"];
export type Swap = Schemas["SwapOut"];
export type SwapIn = Schemas["SwapIn"];
export type SwapPatch = Schemas["SwapPatch"];
export type Wish = Schemas["WishOut"];
export type WishIn = Schemas["WishIn"];
export type WishPatch = Schemas["WishPatch"];
export type WishTexts = Schemas["WishTexts"];
export type WishRequest = Schemas["RequestOut"];
export type LedgerEntry = Schemas["LedgerOut"];
/** The gem balance and its last ledger entries, newest first (SPEC §16.3). */
export type GemsAdmin = Schemas["GemsOut"];
export type GemEntry = GemsAdmin["entries"][number];

/** An error answer: HTTP status plus the server's reason code and params (SPEC §3.2). */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly reason: string,
    readonly params: Record<string, unknown> = {},
  ) {
    super(`${status} ${reason}`);
  }
}

let csrfToken: string | null = null;

/** The session's CSRF token, sent as X-CSRF-Token on every mutation (SPEC §7). */
export function setCsrfToken(token: string | null): void {
  csrfToken = token;
}

async function errorFrom(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as { reason?: string; params?: Record<string, unknown> };
    return new ApiError(response.status, body.reason ?? `http_${response.status}`, body.params ?? {});
  } catch {
    return new ApiError(response.status, `http_${response.status}`);
  }
}

export async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (method !== "GET") {
    if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
    if (body !== undefined) headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) throw await errorFrom(response);
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/**
 * Upload one file as the raw request body (SPEC §5.4). XHR rather than fetch,
 * because fetch can't report upload progress.
 */
export function uploadFile(
  groupId: number,
  file: File,
  onProgress: (fraction: number) => void,
): Promise<Media> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/admin/groups/${groupId}/upload?filename=${encodeURIComponent(file.name)}`);
    if (csrfToken) xhr.setRequestHeader("X-CSRF-Token", csrfToken);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as Media);
        return;
      }
      try {
        const body = JSON.parse(xhr.responseText) as { reason?: string; params?: Record<string, unknown> };
        reject(new ApiError(xhr.status, body.reason ?? `http_${xhr.status}`, body.params ?? {}));
      } catch {
        reject(new ApiError(xhr.status, `http_${xhr.status}`));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "network"));
    xhr.send(file);
  });
}

/** A thumbnail URL from a ThumbRef; the cookie authorizes it (SPEC §7). */
export function thumbSrc(ref: { url: string } | null | undefined, size: "sm" | "md" | "lg" = "sm"): string | undefined {
  return ref ? `${ref.url}?size=${size}` : undefined;
}

/** Send a file as the raw request body (thumbnail, group cover or APK upload). */
export async function putImage<T>(path: string, file: File): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": file.type || "application/octet-stream" };
  if (csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(path, { method: "PUT", headers, credentials: "same-origin", body: file });
  if (!response.ok) throw await errorFrom(response);
  return (await response.json()) as T;
}
