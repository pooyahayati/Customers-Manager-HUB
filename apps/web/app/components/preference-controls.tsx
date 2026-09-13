"use client";

import { usePreferences } from "./app-preferences";
import { Icon } from "./icons";

export function PreferenceControls({ compact = false }: Readonly<{ compact?: boolean }>) {
  const { locale, setLocale, setTheme, t, theme } = usePreferences();
  const nextTheme = theme === "light" ? "dark" : "light";

  return (
    <div className={`preference-controls${compact ? " preference-controls--compact" : ""}`}>
      <div className="language-switch" aria-label="Language">
        <button
          aria-pressed={locale === "en"}
          className={locale === "en" ? "is-active" : undefined}
          onClick={() => setLocale("en")}
          type="button"
        >
          EN
        </button>
        <button
          aria-pressed={locale === "fa"}
          className={locale === "fa" ? "is-active" : undefined}
          onClick={() => setLocale("fa")}
          type="button"
        >
          فا
        </button>
      </div>
      <button
        aria-label={t(`theme.${nextTheme}`)}
        className="icon-button"
        onClick={() => setTheme(nextTheme)}
        title={t(`theme.${nextTheme}`)}
        type="button"
      >
        <Icon name={theme === "light" ? "moon" : "sun"} size={18} />
      </button>
    </div>
  );
}
