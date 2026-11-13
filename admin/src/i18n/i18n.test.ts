import { describe, expect, it } from "vitest";

import de from "./de.json";
import el from "./el.json";
import en from "./en.json";
import { SUPPORTED_LOCALES, detectLocale, resources } from "./index";

type Tree = { [key: string]: string | Tree };

function keys(tree: Tree, prefix = ""): string[] {
  return Object.entries(tree).flatMap(([k, v]) =>
    typeof v === "string" ? [prefix + k] : keys(v, `${prefix}${k}.`),
  );
}

function placeholders(tree: Tree): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  const walk = (t: Tree, prefix: string) => {
    for (const [k, v] of Object.entries(t)) {
      if (typeof v === "string") out[prefix + k] = [...v.matchAll(/{{\s*(\w+)\s*}}/g)].map((m) => m[1]).sort();
      else walk(v, `${prefix}${k}.`);
    }
  };
  walk(tree, "");
  return out;
}

describe("translations (SPEC §4.2: en and de from day one, el since v0.12)", () => {
  it("en and de have exactly the same keys", () => {
    expect(keys(de).sort()).toEqual(keys(en).sort());
  });

  it("en and el have exactly the same keys", () => {
    expect(keys(el).sort()).toEqual(keys(en).sort());
  });

  it("every supported locale has a catalog and a name in every language", () => {
    expect(Object.keys(resources).sort()).toEqual([...SUPPORTED_LOCALES].sort());
    for (const tree of [en, de, el]) {
      for (const l of SUPPORTED_LOCALES) expect(tree.language[l]).toBeTruthy();
    }
  });

  it("no translation is empty", () => {
    for (const [name, tree] of [["en", en], ["de", de], ["el", el]] as const) {
      for (const key of keys(tree)) {
        const value = key.split(".").reduce<string | Tree>((t, k) => (t as Tree)[k], tree);
        expect(String(value).trim(), `${name}:${key}`).not.toBe("");
      }
    }
  });

  it("has the gem texts in every language (SPEC §16.4)", () => {
    for (const tree of [en, de, el]) {
      expect(tree.rules.gems.title).toContain("💎");
      expect(tree.rules.summary.gems).toMatch(/{{scope}}.*{{count}}|{{count}}.*{{scope}}/);
      expect(tree.status.gems).toMatch(/{{need}}.*{{have}}/);
      expect(tree.errors.not_enough_gems).toBeTruthy();
    }
  });

  it("placeholders match between languages", () => {
    expect(placeholders(de)).toEqual(placeholders(en));
    expect(placeholders(el)).toEqual(placeholders(en));
  });
});

describe("detectLocale (decision 13)", () => {
  it("prefers a remembered choice", () => {
    expect(detectLocale("de", ["en-US"])).toBe("de");
  });
  it("follows the browser languages in order", () => {
    expect(detectLocale(null, ["fr-FR", "de-AT", "en"])).toBe("de");
  });
  it("falls back for unsupported browsers", () => {
    expect(detectLocale(null, ["fr-FR"])).toBe("en");
    expect(detectLocale(null, ["fr-FR"], "de")).toBe("de");
  });
  it("supports Greek (v0.12)", () => {
    expect(detectLocale("el", ["de-DE"])).toBe("el");
    expect(detectLocale(null, ["fr-FR", "el-GR", "en"])).toBe("el");
    expect(detectLocale(null, ["fr-FR"], "el")).toBe("el");
  });
  it("ignores invalid stored values", () => {
    expect(detectLocale("xx", ["de-DE"])).toBe("de");
  });
});
