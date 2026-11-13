import type { Translate } from "./translate";

import { ApiError } from "../api/client";

const KNOWN = new Set([
  "upload_too_large",
  "empty_upload",
  "unsupported_format",
  "unreadable_file",
  "bad_image",
  "image_too_large",
  "network",
  "unknown_groups",
  "unknown_rule",
  "unknown_swap",
  "unknown_wish",
  "unknown_request",
  "request_decided",
  "swap_rule_unsuitable",
  "not_enough_stars",
  "not_enough_gems",
  "not_an_apk",
  "version_not_newer",
  "invalid_pack",
  "invalid_days",
  "pack_too_large",
  "queue_duplicate",
  "world_not_found",
  "days_off_order",
  "slug_mismatch",
  "example_not_found",
  "activities_closed",
  "speech_not_found",
  "bonus_given",
  "unknown_done",
]);

/** The server sends reason codes; the admin turns them into text (SPEC §3.2). */
export function errorText(error: unknown, t: Translate): string {
  if (error instanceof ApiError) {
    if (KNOWN.has(error.reason)) return t(`errors.${error.reason}`, error.params as Record<string, string>);
    if (error.status === 422) return t("errors.invalid");
    return t("errors.generic", { reason: error.reason });
  }
  return t("errors.generic", { reason: String(error) });
}
