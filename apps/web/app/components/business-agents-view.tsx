"use client";

import { type FormEvent, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  createAgentPrompt,
  createBusinessAgent,
  listAgentPrompts,
  listBusinessAgents,
  publishAgentPrompt,
  saveAgentPromptDraft,
  updateBusinessAgent,
  type AgentPrompt,
  type BusinessAgent,
} from "../lib/api";
import { usePreferences } from "./app-preferences";

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Unable to save changes.";
}

function PromptEditor({
  busy,
  fa,
  prompt,
  onPublish,
  onSave,
}: Readonly<{
  busy: boolean;
  fa: boolean;
  prompt: AgentPrompt;
  onPublish: (content: string) => void;
  onSave: (content: string) => void;
}>) {
  const editable = prompt.versions.find((version) => version.status === "draft")
    ?? prompt.versions.find((version) => version.status === "published");
  const [content, setContent] = useState(editable?.content ?? "");
  return <><label><span>{fa ? "محتوای پیش‌نویس" : "Draft content"}</span><textarea rows={12} value={content} onChange={(event) => setContent(event.target.value)} /></label><div className="button-row"><button className="button button--secondary" disabled={busy} onClick={() => onSave(content)} type="button">{fa ? "ذخیره پیش‌نویس" : "Save draft"}</button><button className="button" disabled={busy} onClick={() => onPublish(content)} type="button">{fa ? "انتشار نسخه" : "Publish version"}</button></div></>;
}

export function BusinessAgentsView({ tenantId }: Readonly<{ tenantId: string }>) {
  const { locale } = usePreferences();
  const fa = locale === "fa";
  const [prompts, setPrompts] = useState<AgentPrompt[]>([]);
  const [agents, setAgents] = useState<BusinessAgent[]>([]);
  const [selectedPromptId, setSelectedPromptId] = useState("");
  const [agentName, setAgentName] = useState("");
  const [agentDescription, setAgentDescription] = useState("");
  const [promptName, setPromptName] = useState("");
  const [newPromptContent, setNewPromptContent] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    const [nextPrompts, nextAgents] = await Promise.all([listAgentPrompts(tenantId), listBusinessAgents(tenantId)]);
    setPrompts(nextPrompts);
    setAgents(nextAgents);
    setSelectedPromptId((current) => current || nextPrompts[0]?.id || "");
  };

  useEffect(() => {
    let cancelled = false;
    void Promise.all([listAgentPrompts(tenantId), listBusinessAgents(tenantId)])
      .then(([nextPrompts, nextAgents]) => {
        if (cancelled) return;
        setPrompts(nextPrompts); setAgents(nextAgents); setSelectedPromptId((current) => current || nextPrompts[0]?.id || "");
      })
      .catch((reason) => { if (!cancelled) setError(errorMessage(reason)); });
    return () => { cancelled = true; };
  }, [tenantId]);

  const selectedPrompt = useMemo(
    () => prompts.find((prompt) => prompt.id === selectedPromptId) ?? null,
    [prompts, selectedPromptId],
  );

  const createAgent = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true); setError(null); setNotice(null);
    try {
      const prompt = await createAgentPrompt(tenantId, promptName, newPromptContent);
      await publishAgentPrompt(tenantId, prompt.id);
      await createBusinessAgent(tenantId, agentName, prompt.id, agentDescription);
      setAgentName(""); setAgentDescription(""); setPromptName(""); setNewPromptContent("");
      await load();
      setSelectedPromptId(prompt.id);
      setNotice(fa ? "ایجنت ایجاد و نسخهٔ نخست دستور سیستم منتشر شد." : "The agent was created and its initial system-prompt version was published.");
    } catch (reason) {
      setError(errorMessage(reason));
    } finally { setBusy(false); }
  };

  const saveDraft = async (content: string) => {
    if (!selectedPrompt) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      await saveAgentPromptDraft(tenantId, selectedPrompt.id, content);
      await load();
      setNotice(fa ? "پیش‌نویس ذخیره شد." : "Draft saved.");
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  const publishDraft = async (content: string) => {
    if (!selectedPrompt) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      await saveAgentPromptDraft(tenantId, selectedPrompt.id, content);
      await publishAgentPrompt(tenantId, selectedPrompt.id);
      await load();
      setNotice(fa ? "نسخهٔ پیش‌نویس منتشر شد." : "The draft was published.");
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  const toggleAgent = async (agent: BusinessAgent) => {
    setBusy(true); setError(null);
    try {
      await updateBusinessAgent(tenantId, agent.id, { is_active: !agent.is_active });
      await load();
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  return (
    <div className="admin-page business-page">
      <header className="admin-heading">
        <div>
          <span className="section-eyebrow">{fa ? "اتوماسیون کسب‌وکار" : "Business automation"}</span>
          <h2>{fa ? "ایجنت‌ها" : "Agents"}</h2>
          <p>{fa ? "برای هر ایجنت، دستور سیستم نسخه‌دار بسازید و فقط نسخهٔ منتشرشده را در عملیات استفاده کنید." : "Create versioned system prompts and publish the reviewed version used in operations."}</p>
        </div>
      </header>
      {error ? <p className="notice notice--error">{error}</p> : null}
      {notice ? <p className="notice notice--success">{notice}</p> : null}

      <div className="business-grid">
        <section className="admin-card">
          <div className="card-heading card-heading--spaced"><h3>{fa ? "ایجنت جدید" : "New agent"}</h3></div>
          <form className="stack-form" onSubmit={(event) => void createAgent(event)}>
            <label><span>{fa ? "نام ایجنت" : "Agent name"}</span><input required value={agentName} onChange={(event) => setAgentName(event.target.value)} /></label>
            <label><span>{fa ? "توضیح کوتاه" : "Short description"}</span><input value={agentDescription} onChange={(event) => setAgentDescription(event.target.value)} /></label>
            <label><span>{fa ? "نام دستور سیستم" : "System-prompt name"}</span><input required value={promptName} onChange={(event) => setPromptName(event.target.value)} placeholder={fa ? "مثلاً پاسخ‌گوی فروش" : "For example: Sales responder"} /></label>
            <label><span>{fa ? "دستور سیستم اولیه" : "Initial system prompt"}</span><textarea required rows={8} value={newPromptContent} onChange={(event) => setNewPromptContent(event.target.value)} /></label>
            <button className="button" disabled={busy} type="submit">{fa ? "ایجاد ایجنت" : "Create agent"}</button>
          </form>
        </section>

        <section className="admin-card">
          <div className="card-heading card-heading--spaced"><h3>{fa ? "ایجنت‌های تعریف‌شده" : "Configured agents"}</h3></div>
          {agents.length === 0 ? <p className="empty-panel">{fa ? "هنوز ایجنتی ایجاد نشده است." : "No agents have been created yet."}</p> : (
            <div className="entity-list">
              {agents.map((agent) => (
                <article className="entity-row" key={agent.id}>
                  <div><strong>{agent.name}</strong><small>{agent.description || (fa ? "بدون توضیح" : "No description")}</small></div>
                  <label className="switch-label"><input checked={agent.is_active} disabled={busy} onChange={() => void toggleAgent(agent)} type="checkbox" /><span>{agent.is_active ? (fa ? "فعال" : "Active") : (fa ? "غیرفعال" : "Inactive")}</span></label>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>

      <section className="admin-card">
        <div className="card-heading card-heading--spaced"><h3>{fa ? "System Prompt و نسخه‌ها" : "System prompt and versions"}</h3></div>
        {prompts.length === 0 ? <p className="empty-panel">{fa ? "پس از ایجاد اولین ایجنت، دستور سیستم آن را اینجا ویرایش و منتشر کنید." : "Create an agent first, then edit and publish its system prompt here."}</p> : (
          <div className="prompt-editor">
            <label><span>{fa ? "دستور سیستم" : "System prompt"}</span><select value={selectedPromptId} onChange={(event) => setSelectedPromptId(event.target.value)}>{prompts.map((prompt) => <option key={prompt.id} value={prompt.id}>{prompt.name}</option>)}</select></label>
            {selectedPrompt ? <PromptEditor key={selectedPrompt.id} busy={busy} fa={fa} onPublish={(content) => void publishDraft(content)} onSave={(content) => void saveDraft(content)} prompt={selectedPrompt} /> : null}
          </div>
        )}
      </section>
    </div>
  );
}
