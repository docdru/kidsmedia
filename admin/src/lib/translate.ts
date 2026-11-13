/**
 * The part of i18next's `t` the helpers need. A plain function type keeps them
 * independent of i18next's generic TFunction typing (and easy to test).
 */
export type Translate = (key: string, options?: Record<string, unknown>) => string;
