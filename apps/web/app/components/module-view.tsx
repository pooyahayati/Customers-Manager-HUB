"use client";

import { findNavigationItem, type ModuleId } from "../config/navigation";
import { usePreferences } from "./app-preferences";
import { Icon } from "./icons";

export function ModuleView({
  module,
  onBack,
}: Readonly<{ module: ModuleId; onBack: () => void }>) {
  const { t } = usePreferences();
  const item = findNavigationItem(module);

  return (
    <section className="module-page">
      <div className="module-card">
        <span className="module-icon"><Icon name={item.icon} size={24} /></span>
        <span className="status-pill status-pill--quiet">{t("status.foundation")}</span>
        <h2>{t(item.label)}</h2>
        <h3>{t("module.title")}</h3>
        <p>{t("module.body")}</p>
        <div className="api-scope">
          <span>{t("module.endpoint")}</span>
          <code>{item.apiScope}</code>
        </div>
        <button className="button button--secondary" onClick={onBack} type="button">
          {t("module.back")}
        </button>
      </div>
    </section>
  );
}
