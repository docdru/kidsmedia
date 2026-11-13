import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import de from "./de.json";
import el from "./el.json";
import en from "./en.json";

export const SUPPORTED_LOCALES = ["en", "de", "el"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];

export const resources = { en: { translation: en }, de: { translation: de }, el: { translation: el } } as const;

const STORAGE_KEY = "kidsmedia.locale";
const FALLBACK: Locale = "en";

function isLocale(value: string | null | undefined): value is Locale {
  return (SUPPORTED_LOCALES as readonly string[]).includes(value ?? "");
}

/**
 * SPEC §4.2 / decision 13: an explicit choice (remembered) wins, then the
 * browser languages, then the fallback. Once accounts exist the choice is
 * stored per admin user on the server; until then localStorage is used.
 */
export function detectLocale(
  stored: string | null,
  browserLanguages: readonly string[],
  fallback: Locale = FALLBACK,
): Locale {
  if (isLocale(stored)) return stored;
  for (const lang of browserLanguages) {
    const base = lang.toLowerCase().split("-")[0];
    if (isLocale(base)) return base;
  }
  return fallback;
}

function readStored(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function rememberLocale(locale: Locale): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    // Storage may be unavailable (private mode); the switch still works for this session.
  }
  void i18n.changeLanguage(locale);
}

void i18n.use(initReactI18next).init({
  resources,
  lng: detectLocale(readStored(), typeof navigator === "undefined" ? [] : navigator.languages),
  fallbackLng: FALLBACK,
  interpolation: { escapeValue: false },
});

export default i18n;
