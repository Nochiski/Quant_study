import { createContext, useContext } from "react";

import type { ThemePreference } from "./theme-preference";

export type ThemePreferenceValue = {
  preference: ThemePreference;
  resolved: "light" | "dark";
  setPreference: (preference: ThemePreference) => void;
};

export const ThemePreferenceContext =
  createContext<ThemePreferenceValue | null>(null);

export const useThemePreference = (): ThemePreferenceValue => {
  const value = useContext(ThemePreferenceContext);
  if (value === null) throw new Error("ThemePreferenceProvider is missing");
  return value;
};
