"use client";

import type { AnalyticsOverview } from "../lib/api";
import type { TranslationKey } from "../lib/i18n";
import { usePreferences } from "./app-preferences";
import { Icon, type IconName } from "./icons";

interface DashboardViewProps {
  days: number;
  error: string | null;
  loading: boolean;
  onDaysChange: (days: number) => void;
  onRefresh: () => void;
  overview: AnalyticsOverview | null;
  variant: "dashboard" | "analytics";
}

const periodLabels: Record<number, TranslationKey> = {
  7: "dashboard.last7",
  30: "dashboard.last30",
  90: "dashboard.last90",
};

function MetricCard({
  icon,
  label,
  loading,
  secondary,
  value,
}: Readonly<{
  icon: IconName;
  label: string;
  loading: boolean;
  secondary?: string;
  value: string;
}>) {
  return (
    <article className="metric-card">
      <div className="metric-card-top">
        <span className="metric-icon"><Icon name={icon} size={18} /></span>
        <span className="metric-label">{label}</span>
      </div>
      {loading ? (
        <>
          <span className="skeleton skeleton--value" />
          <span className="skeleton skeleton--line" />
        </>
      ) : (
        <>
          <strong className="metric-value">{value}</strong>
          <span className="metric-secondary">{secondary ?? "\u00a0"}</span>
        </>
      )}
    </article>
  );
}

function ProgressMetric({
  label,
  loading,
  value,
}: Readonly<{ label: string; loading: boolean; value: number | null }>) {
  const { locale, t } = usePreferences();
  const formatter = new Intl.NumberFormat(locale === "fa" ? "fa-IR" : "en-US", {
    style: "percent",
    maximumFractionDigits: 1,
  });
  const safeValue = value === null ? 0 : Math.max(0, Math.min(1, value));

  return (
    <div className="progress-metric">
      <div className="progress-metric-head">
        <span>{label}</span>
        {loading ? (
          <span className="skeleton skeleton--small" />
        ) : (
          <strong>{value === null ? t("metric.noData") : formatter.format(value)}</strong>
        )}
      </div>
      <div className="progress-track" aria-hidden="true">
        <span style={{ inlineSize: loading ? "28%" : `${safeValue * 100}%` }} />
      </div>
    </div>
  );
}

export function DashboardView({
  days,
  error,
  loading,
  onDaysChange,
  onRefresh,
  overview,
  variant,
}: Readonly<DashboardViewProps>) {
  const { locale, t } = usePreferences();
  const number = new Intl.NumberFormat(locale === "fa" ? "fa-IR" : "en-US", {
    maximumFractionDigits: 1,
  });
  const currency = new Intl.NumberFormat(locale === "fa" ? "fa-IR" : "en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 4,
  });
  const percent = new Intl.NumberFormat(locale === "fa" ? "fa-IR" : "en-US", {
    style: "percent",
    maximumFractionDigits: 1,
  });

  const formatDuration = (seconds: number | null): string => {
    if (seconds === null) return t("metric.noData");
    if (seconds < 60) return `${number.format(seconds)} ${t("common.seconds")}`;
    if (seconds < 3600) return `${number.format(seconds / 60)} ${t("common.minutes")}`;
    return `${number.format(seconds / 3600)} ${t("common.hours")}`;
  };

  const operations = overview?.operations;
  const ai = overview?.ai;
  const tools = overview?.tools;
  const aiSuccessRate = ai && ai.requests > 0 ? ai.succeeded / ai.requests : null;

  return (
    <div className="dashboard-page">
      <section className="page-heading">
        <div>
          <div className="heading-badges">
            <span className="eyebrow">
              {t(variant === "analytics" ? "analytics.eyebrow" : "dashboard.eyebrow")}
            </span>
            <span className="status-pill"><span className="status-dot" />{t("status.live")}</span>
          </div>
          <h2>{t(variant === "analytics" ? "analytics.title" : "dashboard.title")}</h2>
          <p>{t(variant === "analytics" ? "analytics.subtitle" : "dashboard.subtitle")}</p>
        </div>
        <div className="report-controls">
          <label>
            <span>{t("dashboard.period")}</span>
            <select onChange={(event) => onDaysChange(Number(event.target.value))} value={days}>
              {[7, 30, 90].map((value) => (
                <option key={value} value={value}>{t(periodLabels[value])}</option>
              ))}
            </select>
          </label>
          <button
            aria-label={t("common.refresh")}
            className="icon-button icon-button--bordered"
            disabled={loading}
            onClick={onRefresh}
            title={t("common.refresh")}
            type="button"
          >
            <Icon className={loading ? "is-spinning" : undefined} name="refresh" size={18} />
          </button>
        </div>
      </section>

      {error !== null ? (
        <div className="alert alert--error alert--inline">
          <span>{error}</span>
          <button className="text-button" onClick={onRefresh} type="button">{t("common.retry")}</button>
        </div>
      ) : null}

      <section className="metrics-grid" aria-label={t("dashboard.eyebrow")}>
        <MetricCard
          icon="conversations"
          label={t("metric.conversations")}
          loading={loading}
          secondary={`${number.format(operations?.active_conversations ?? 0)} ${t("metric.active")}`}
          value={number.format(operations?.conversations ?? 0)}
        />
        <MetricCard
          icon="contacts"
          label={t("metric.contacts")}
          loading={loading}
          value={number.format(operations?.unique_contacts ?? 0)}
        />
        <MetricCard
          icon="inbox"
          label={t("metric.firstResponse")}
          loading={loading}
          secondary={
            operations
              ? `${number.format(operations.first_response_samples)} ${t("metric.samples")}`
              : undefined
          }
          value={formatDuration(operations?.first_response_average_seconds ?? null)}
        />
        <MetricCard
          icon="agents"
          label={t("metric.automation")}
          loading={loading}
          secondary={
            operations
              ? `${number.format(operations.automated_conversations)} ${t("metric.conversations")}`
              : undefined
          }
          value={
            operations?.ai_automation_rate === null || operations?.ai_automation_rate === undefined
              ? t("metric.noData")
              : percent.format(operations.ai_automation_rate)
          }
        />
        <MetricCard
          icon="analytics"
          label={t("metric.cost")}
          loading={loading}
          secondary={
            ai && ai.unpriced_requests > 0
              ? `${number.format(ai.unpriced_requests)} ${t("metric.unpriced")}`
              : t("dashboard.updated")
          }
          value={currency.format(Number(ai?.estimated_cost_usd ?? 0))}
        />
        <MetricCard
          icon="tools"
          label={t("metric.toolSuccess")}
          loading={loading}
          secondary={tools ? `${number.format(tools.executions)} ${t("nav.tools")}` : undefined}
          value={tools?.success_rate === null || tools?.success_rate === undefined
            ? t("metric.noData")
            : percent.format(tools.success_rate)}
        />
      </section>

      {overview && overview.operations.conversations === 0 ? (
        <section className="empty-notice">
          <span className="empty-notice-mark"><Icon name="dashboard" size={20} /></span>
          <div>
            <strong>{t("dashboard.emptyTitle")}</strong>
            <p>{t("dashboard.emptyBody")}</p>
          </div>
        </section>
      ) : null}

      <section className="dashboard-details">
        <article className="panel">
          <header className="panel-heading">
            <div>
              <h3>{t("section.performance")}</h3>
              <p>{t("section.performanceHint")}</p>
            </div>
            <span className="status-pill status-pill--quiet">{t(periodLabels[days])}</span>
          </header>
          <div className="progress-list">
            <ProgressMetric label={t("section.automation")} loading={loading} value={operations?.ai_automation_rate ?? null} />
            <ProgressMetric label={t("section.handoff")} loading={loading} value={operations?.handoff_rate ?? null} />
            <ProgressMetric label={t("section.aiSuccess")} loading={loading} value={aiSuccessRate} />
            <ProgressMetric label={t("section.tools")} loading={loading} value={tools?.success_rate ?? null} />
          </div>
        </article>

        <article className="panel">
          <header className="panel-heading">
            <div>
              <h3>{t("section.usage")}</h3>
              <p>{t("section.usageHint")}</p>
            </div>
          </header>
          <div className="usage-list">
            <div className="usage-row">
              <span><Icon name="agents" size={17} />{t("metric.requests")}</span>
              {loading ? <span className="skeleton skeleton--small" /> : <strong>{number.format(ai?.requests ?? 0)}</strong>}
            </div>
            <div className="usage-row">
              <span><Icon name="analytics" size={17} />{t("metric.tokens")}</span>
              {loading ? <span className="skeleton skeleton--small" /> : <strong>{number.format(ai?.total_tokens ?? 0)}</strong>}
            </div>
            <div className="usage-row">
              <span><Icon name="tools" size={17} />{t("nav.tools")}</span>
              {loading ? <span className="skeleton skeleton--small" /> : <strong>{number.format(tools?.executions ?? 0)}</strong>}
            </div>
            <div className="usage-row">
              <span><Icon name="inbox" size={17} />{t("metric.handoff")}</span>
              {loading ? <span className="skeleton skeleton--small" /> : <strong>{number.format(operations?.handoff_conversations ?? 0)}</strong>}
            </div>
          </div>
        </article>
      </section>
    </div>
  );
}
