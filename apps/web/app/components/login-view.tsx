"use client";

import { type FormEvent, useState } from "react";

import { usePreferences } from "./app-preferences";
import { PreferenceControls } from "./preference-controls";

interface LoginViewProps {
  busy: boolean;
  error: string | null;
  onSubmit: (email: string, password: string) => Promise<void>;
}

export function LoginView({ busy, error, onSubmit }: Readonly<LoginViewProps>) {
  const { t } = usePreferences();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await onSubmit(email, password);
  };

  return (
    <main className="login-page">
      <section className="login-brand-panel" aria-label={t("app.name")}>
        <div className="login-brand-top">
          <span className="brand-mark">CM</span>
          <span>{t("app.name")}</span>
        </div>

        <div className="login-brand-copy">
          <span className="status-pill status-pill--inverse">{t("auth.secure")}</span>
          <h1>{t("app.tagline")}</h1>
          <ul>
            <li>{t("auth.feature.channels")}</li>
            <li>{t("auth.feature.ai")}</li>
            <li>{t("auth.feature.control")}</li>
          </ul>
        </div>

        <p className="login-brand-foot">{t("footer.api")}</p>
      </section>

      <section className="login-form-panel">
        <div className="login-controls">
          <PreferenceControls />
        </div>
        <form className="login-card" onSubmit={submit}>
          <div className="login-card-heading">
            <span className="mobile-brand-mark">CM</span>
            <p className="eyebrow">{t("app.shortName")}</p>
            <h2>{t("auth.welcome")}</h2>
            <p>{t("auth.subtitle")}</p>
          </div>

          {error !== null ? <div className="alert alert--error">{error}</div> : null}

          <label className="field">
            <span>{t("auth.email")}</span>
            <input
              autoComplete="email"
              autoFocus
              dir="ltr"
              disabled={busy}
              onChange={(event) => setEmail(event.target.value)}
              required
              type="email"
              value={email}
            />
          </label>

          <label className="field">
            <span>{t("auth.password")}</span>
            <input
              autoComplete="current-password"
              dir="ltr"
              disabled={busy}
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </label>

          <button className="button button--primary button--large" disabled={busy} type="submit">
            {busy ? <span className="spinner" aria-hidden="true" /> : null}
            {busy ? t("auth.signingIn") : t("auth.signIn")}
          </button>
        </form>
      </section>
    </main>
  );
}
