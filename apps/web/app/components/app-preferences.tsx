"use client";

import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { isLocale, translate, type Locale, type TranslationKey } from "../lib/i18n";

export type Theme = "light" | "dark";

interface PreferencesContextValue {
  locale: Locale;
  theme: Theme;
  setLocale: (locale: Locale) => void;
  setTheme: (theme: Theme) => void;
  t: (key: TranslationKey) => string;
}

const PreferencesContext = createContext<PreferencesContextValue | null>(null);

function applyPreferences(locale: Locale, theme: Theme): void {
  document.documentElement.lang = locale;
  document.documentElement.dir = locale === "fa" ? "rtl" : "ltr";
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

export function AppPreferences({ children }: Readonly<{ children: ReactNode }>) {
  const [locale, setLocaleState] = useState<Locale>("en");
  const [theme, setThemeState] = useState<Theme>("light");

  useEffect(() => {
    const savedLocale = window.localStorage.getItem("cmh-locale");
    const savedTheme = window.localStorage.getItem("cmh-theme");
    const documentTheme = document.documentElement.dataset.theme;
    const initialLocale = isLocale(savedLocale) ? savedLocale : "en";
    const initialTheme: Theme =
      savedTheme === "dark" || savedTheme === "light"
        ? savedTheme
        : documentTheme === "dark"
          ? "dark"
          : "light";

    const frame = window.requestAnimationFrame(() => {
      setLocaleState(initialLocale);
      setThemeState(initialTheme);
      applyPreferences(initialLocale, initialTheme);
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  const setLocale = useCallback((nextLocale: Locale) => {
    setLocaleState(nextLocale);
    window.localStorage.setItem("cmh-locale", nextLocale);
    applyPreferences(nextLocale, theme);
  }, [theme]);

  const setTheme = useCallback((nextTheme: Theme) => {
    setThemeState(nextTheme);
    window.localStorage.setItem("cmh-theme", nextTheme);
    applyPreferences(locale, nextTheme);
  }, [locale]);

  const value = useMemo<PreferencesContextValue>(
    () => ({
      locale,
      theme,
      setLocale,
      setTheme,
      t: (key) => translate(locale, key),
    }),
    [locale, setLocale, setTheme, theme],
  );

  return <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>;
}

export function usePreferences(): PreferencesContextValue {
  const value = useContext(PreferencesContext);
  if (value === null) {
    throw new Error("usePreferences must be used inside AppPreferences");
  }
  return value;
}
