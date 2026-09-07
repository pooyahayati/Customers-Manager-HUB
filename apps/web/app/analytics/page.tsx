import Link from "next/link";

const kpis = [
  ["Conversations", "Conversation volume in the selected reporting window"],
  ["Unique contacts", "Customers with inbound activity in the reporting window"],
  ["First response", "Average time to the first AI or human outbound response"],
  ["Resolution time", "Current conversation lifecycle resolution-time foundation"],
  ["Human handoff", "Conversation-level handoff rate and average handoff duration"],
  ["AI automation", "Conversations handled by AI without human response or handoff"],
  ["AI usage & cost", "Provider/model/task tokens, audio units, latency, and estimated cost"],
  ["Tool reliability", "Tool executions, success/failure rate, approvals, and denials"],
] as const;

export default function AnalyticsPage() {
  return (
    <main className="analytics-shell">
      <header className="analytics-header">
        <div>
          <p className="eyebrow">Customers Manager HUB</p>
          <h1>Analytics, Usage & Cost</h1>
          <p>
            Tenant-scoped operational metrics are served by the authenticated analytics API. This
            route is the initial admin dashboard surface and intentionally does not duplicate API
            authorization or pricing logic in the web application.
          </p>
        </div>
        <Link href="/">System home</Link>
      </header>

      <section className="analytics-grid" aria-label="Analytics KPI foundation">
        {kpis.map(([title, description]) => (
          <article className="analytics-card" key={title}>
            <h2>{title}</h2>
            <p>{description}</p>
          </article>
        ))}
      </section>

      <section className="analytics-contract">
        <h2>API contract</h2>
        <code>GET /api/v1/tenants/:tenant_id/analytics/overview</code>
        <code>GET /api/v1/tenants/:tenant_id/analytics/ai-usage</code>
        <code>GET /api/v1/tenants/:tenant_id/analytics/tools</code>
        <code>GET /api/v1/tenants/:tenant_id/analytics/pricing</code>
        <code>PUT /api/v1/tenants/:tenant_id/analytics/pricing</code>
      </section>
    </main>
  );
}
