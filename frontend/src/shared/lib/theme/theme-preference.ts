export type ThemePreference = "system" | "light" | "dark";

type PreferenceStorage = Pick<Storage, "getItem" | "setItem">;

export const THEME_PREFERENCE_KEY = "quant-workbench.theme.v1";
const DEFAULT_PREFERENCE: ThemePreference = "system";

export const browserThemeStorage = (): PreferenceStorage | null => {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
};

export const readThemePreference = (
  storage: PreferenceStorage | null = browserThemeStorage(),
): ThemePreference => {
  if (storage === null) return DEFAULT_PREFERENCE;
  try {
    const value: unknown = JSON.parse(
      storage.getItem(THEME_PREFERENCE_KEY) ?? "null",
    );
    const preference =
      typeof value === "object" && value !== null && !Array.isArray(value)
        ? (value as { preference?: unknown }).preference
        : undefined;
    if (
      typeof value === "object" &&
      value !== null &&
      !Array.isArray(value) &&
      (value as { version?: unknown }).version === 1 &&
      typeof preference === "string" &&
      ["system", "light", "dark"].includes(preference)
    ) {
      return preference as ThemePreference;
    }
  } catch {
    // Storage is optional; malformed or inaccessible state fails to the default.
  }
  return DEFAULT_PREFERENCE;
};

export const writeThemePreference = (
  storage: PreferenceStorage | null,
  preference: ThemePreference,
): void => {
  try {
    storage?.setItem(
      THEME_PREFERENCE_KEY,
      JSON.stringify({ version: 1, preference }),
    );
  } catch {
    // Keep the in-memory preference usable when storage is unavailable or full.
  }
};
