/**
 * The part of i18next's `t` the helpers need. A plain function type keeps them
 * independent of i18next's generic TFunction typing (and easy to test).
 *
 * Two overloads rather than one `options?` signature: since i18next 27, `t` takes a
 * union of argument tuples, and a signature with an optional second argument no
 * longer matches any of them; separate one- and two-argument forms each do.
 */
export interface Translate {
  (key: string): string;
  (key: string, options: Record<string, unknown>): string;
}
