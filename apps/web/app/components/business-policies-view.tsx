"use client";

import { type FormEvent, useEffect, useState } from "react";

import { ApiError, getTenantPolicy, saveTenantPolicy, type TenantPolicy } from "../lib/api";
import { usePreferences } from "./app-preferences";

function errorMessage(error: unknown): string { return error instanceof ApiError ? error.message : "Unable to save changes."; }

export function BusinessPoliciesView({ tenantId }: Readonly<{ tenantId: string }>) {
  const { locale } = usePreferences();
  const fa = locale === "fa";
  const [policy, setPolicy] = useState<TenantPolicy | null>(null);
  const [keywords, setKeywords] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    const next = await getTenantPolicy(tenantId);
    setPolicy(next); setKeywords(next.handoff_keywords.join(", "));
  };
  useEffect(() => {
    let cancelled = false;
    void getTenantPolicy(tenantId)
      .then((next) => {
        if (cancelled) return;
        setPolicy(next); setKeywords(next.handoff_keywords.join(", "));
      })
      .catch((reason) => { if (!cancelled) setError(errorMessage(reason)); });
    return () => { cancelled = true; };
  }, [tenantId]);

  const update = <K extends keyof TenantPolicy>(key: K, value: TenantPolicy[K]) => setPolicy((current) => current ? { ...current, [key]: value } : current);
  const save = async (event: FormEvent) => {
    event.preventDefault(); if (!policy) return;
    setBusy(true); setError(null); setNotice(null);
    const handoff_keywords = keywords.split(/[,\n]/).map((item) => item.trim()).filter(Boolean);
    try {
      const { revision, updated_at, ...payload } = { ...policy, handoff_keywords };
      void revision; void updated_at;
      const saved = await saveTenantPolicy(tenantId, payload);
      setPolicy(saved); setKeywords(saved.handoff_keywords.join(", "));
      setNotice(fa ? `راهنماها ذخیره شدند (نسخه ${saved.revision}).` : `Guidelines saved (revision ${saved.revision}).`);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  if (!policy) return <div className="admin-page business-page"><p className="empty-panel">{error || (fa ? "در حال بارگذاری راهنماها…" : "Loading guidelines…")}</p></div>;

  return <div className="admin-page business-page">
    <header className="admin-heading"><div><span className="section-eyebrow">{fa ? "کنترل پاسخ‌گویی" : "Response controls"}</span><h2>{fa ? "سیاست‌ها و Guardrails" : "Policies and guardrails"}</h2><p>{fa ? "این تنظیمات، مرزهای عملیاتی مشترک همهٔ Agentهای این کسب‌وکار را تعیین می‌کنند؛ System Prompt هر Agent جداگانه در بخش عامل‌هاست." : "These settings define operating boundaries shared by all business agents. Each agent's system prompt is managed separately in AI agents."}</p></div></header>
    {error ? <p className="notice notice--error">{error}</p> : null}{notice ? <p className="notice notice--success">{notice}</p> : null}
    <form className="policy-grid" onSubmit={(event) => void save(event)}>
      <section className="admin-card"><div className="card-heading card-heading--spaced"><h3>{fa ? "سطح خودمختاری" : "Autonomy"}</h3></div><div className="stack-form"><label className="check-label"><input checked={policy.enabled} onChange={(event) => update("enabled", event.target.checked)} type="checkbox" /><span>{fa ? "فعال‌بودن قواعد ایمنی" : "Enable guardrails"}</span></label><label><span>{fa ? "شیوه پاسخ‌گویی" : "Response mode"}</span><select value={policy.autonomy_mode} onChange={(event) => update("autonomy_mode", event.target.value as TenantPolicy["autonomy_mode"])}><option value="autonomous">{fa ? "خودکار" : "Autonomous"}</option><option value="assist_only">{fa ? "فقط پیشنهاد به اپراتور" : "Assist only"}</option><option value="human_only">{fa ? "فقط اپراتور انسانی" : "Human only"}</option></select></label><label className="check-label"><input checked={policy.require_approval_for_writes} onChange={(event) => update("require_approval_for_writes", event.target.checked)} type="checkbox" /><span>{fa ? "برای عملیات نوشتنی تأیید انسانی لازم باشد" : "Require approval for write actions"}</span></label><label><span>{fa ? "حداقل ریسک نیازمند تأیید" : "Approval risk threshold"}</span><select value={policy.approval_min_risk} onChange={(event) => update("approval_min_risk", event.target.value as TenantPolicy["approval_min_risk"])}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option><option value="critical">Critical</option></select></label></div></section>
      <section className="admin-card"><div className="card-heading card-heading--spaced"><h3>{fa ? "ارجاع به انسان" : "Human handoff"}</h3></div><div className="stack-form"><label><span>{fa ? "کلیدواژه‌های ارجاع" : "Handoff keywords"}</span><textarea rows={5} value={keywords} onChange={(event) => setKeywords(event.target.value)} placeholder={fa ? "مثلاً شکایت، مدیر، تماس" : "For example: complaint, manager, call"} /><small>{fa ? "با کاما یا خط جدید جدا کنید." : "Separate with commas or new lines."}</small></label><label className="check-label"><input checked={policy.handoff_on_tool_approval} onChange={(event) => update("handoff_on_tool_approval", event.target.checked)} type="checkbox" /><span>{fa ? "هنگام نیاز ابزار به تأیید، گفتگو ارجاع شود" : "Hand off when a tool needs approval"}</span></label></div></section>
      <section className="admin-card"><div className="card-heading card-heading--spaced"><h3>{fa ? "خارج از ساعت کاری" : "Outside business hours"}</h3></div><div className="stack-form"><label><span>{fa ? "منطقه زمانی" : "Timezone"}</span><input value={policy.timezone} onChange={(event) => update("timezone", event.target.value)} placeholder="Asia/Tehran" /></label><label><span>{fa ? "اقدام" : "Action"}</span><select value={policy.outside_business_hours_action} onChange={(event) => update("outside_business_hours_action", event.target.value as TenantPolicy["outside_business_hours_action"])}><option value="allow">{fa ? "پاسخ‌گویی مجاز" : "Allow responses"}</option><option value="handoff">{fa ? "ارجاع به اپراتور" : "Hand off"}</option><option value="human_only">{fa ? "فقط اپراتور انسانی" : "Human only"}</option></select></label><p className="muted-line">{fa ? "در این نسخه، ساعت‌های دقیق کاری از تنظیمات فعلی حفظ می‌شوند؛ ویرایش تقویم هفتگی در مرحلهٔ بعد اضافه می‌شود." : "This version preserves existing business hours. The weekly calendar editor will be added next."}</p></div></section>
      <div className="policy-actions"><button className="button" disabled={busy} type="submit">{fa ? "ذخیره Guardrails" : "Save guardrails"}</button></div>
    </form>
  </div>;
}
