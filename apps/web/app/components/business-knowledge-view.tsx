"use client";

import { type ChangeEvent, type FormEvent, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  createKnowledgeBase,
  grantAgentKnowledgePermission,
  listAgentKnowledgePermissions,
  listBusinessAgents,
  listKnowledgeBases,
  listKnowledgeSources,
  revokeAgentKnowledgePermission,
  uploadKnowledgeSource,
  type AgentKnowledgePermission,
  type BusinessAgent,
  type KnowledgeBase,
  type KnowledgeSource,
} from "../lib/api";
import { usePreferences } from "./app-preferences";

function errorMessage(error: unknown): string { return error instanceof ApiError ? error.message : "Unable to save changes."; }

export function BusinessKnowledgeView({ tenantId }: Readonly<{ tenantId: string }>) {
  const { locale } = usePreferences();
  const fa = locale === "fa";
  const [bases, setBases] = useState<KnowledgeBase[]>([]);
  const [agents, setAgents] = useState<BusinessAgent[]>([]);
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [permissions, setPermissions] = useState<AgentKnowledgePermission[]>([]);
  const [selectedBaseId, setSelectedBaseId] = useState("");
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [baseName, setBaseName] = useState("");
  const [baseDescription, setBaseDescription] = useState("");
  const [upload, setUpload] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedBase = useMemo(() => bases.find((base) => base.id === selectedBaseId) ?? null, [bases, selectedBaseId]);

  const loadBases = async () => {
    const [nextBases, nextAgents] = await Promise.all([listKnowledgeBases(tenantId), listBusinessAgents(tenantId)]);
    setBases(nextBases); setAgents(nextAgents);
    setSelectedBaseId((current) => current || nextBases[0]?.id || "");
    setSelectedAgentId((current) => current || nextAgents.find((agent) => agent.is_active)?.id || "");
  };

  useEffect(() => {
    let cancelled = false;
    void Promise.all([listKnowledgeBases(tenantId), listBusinessAgents(tenantId)])
      .then(([nextBases, nextAgents]) => {
        if (cancelled) return;
        setBases(nextBases); setAgents(nextAgents);
        setSelectedBaseId((current) => current || nextBases[0]?.id || "");
        setSelectedAgentId((current) => current || nextAgents.find((agent) => agent.is_active)?.id || "");
      })
      .catch((reason) => { if (!cancelled) setError(errorMessage(reason)); });
    return () => { cancelled = true; };
  }, [tenantId]);
  useEffect(() => {
    if (!selectedBaseId) return;
    let cancelled = false;
    void listKnowledgeSources(tenantId, selectedBaseId)
      .then((nextSources) => { if (!cancelled) setSources(nextSources); })
      .catch((reason) => { if (!cancelled) setError(errorMessage(reason)); });
    return () => { cancelled = true; };
  }, [tenantId, selectedBaseId]);
  useEffect(() => {
    if (!selectedAgentId) return;
    let cancelled = false;
    void listAgentKnowledgePermissions(tenantId, selectedAgentId)
      .then((nextPermissions) => { if (!cancelled) setPermissions(nextPermissions); })
      .catch((reason) => { if (!cancelled) setError(errorMessage(reason)); });
    return () => { cancelled = true; };
  }, [tenantId, selectedAgentId]);

  const createBase = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null); setNotice(null);
    try {
      const base = await createKnowledgeBase(tenantId, baseName, baseDescription);
      setBaseName(""); setBaseDescription(""); await loadBases(); setSelectedBaseId(base.id);
      setNotice(fa ? "پایگاه دانش ایجاد شد." : "Knowledge base created.");
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  const submitUpload = async (event: FormEvent) => {
    event.preventDefault(); if (!selectedBase || !upload) return;
    setBusy(true); setError(null); setNotice(null);
    try {
      await uploadKnowledgeSource(tenantId, selectedBase.id, upload);
      setUpload(null); await listKnowledgeSources(tenantId, selectedBase.id).then(setSources);
      setNotice(fa ? "فایل در صف پردازش و ایندکس قرار گرفت." : "The file was queued for processing and indexing.");
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  const setPermission = async () => {
    if (!selectedBase || !selectedAgentId) return;
    const existing = permissions.some((permission) => permission.knowledge_base_id === selectedBase.id);
    setBusy(true); setError(null); setNotice(null);
    try {
      if (existing) await revokeAgentKnowledgePermission(tenantId, selectedAgentId, selectedBase.id);
      else await grantAgentKnowledgePermission(tenantId, selectedAgentId, selectedBase.id);
      await listAgentKnowledgePermissions(tenantId, selectedAgentId).then(setPermissions);
      setNotice(existing ? (fa ? "دسترسی عامل حذف شد." : "Agent access removed.") : (fa ? "پایگاه دانش به عامل متصل شد." : "Knowledge base connected to the agent."));
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  const handleFile = (event: ChangeEvent<HTMLInputElement>) => setUpload(event.target.files?.[0] ?? null);
  const isAssigned = selectedBase ? permissions.some((permission) => permission.knowledge_base_id === selectedBase.id) : false;

  return <div className="admin-page business-page">
    <header className="admin-heading"><div><span className="section-eyebrow">{fa ? "دانش قابل بازیابی" : "Retrieval knowledge"}</span><h2>{fa ? "پایگاه دانش" : "Knowledge base"}</h2><p>{fa ? "فایل‌های PDF یا Excel را بارگذاری کنید، تا پردازش شوند و فقط به Agentهای انتخابی دسترسی داده شوند." : "Upload PDF or Excel files, let them process, then grant access only to selected agents."}</p></div></header>
    {error ? <p className="notice notice--error">{error}</p> : null}{notice ? <p className="notice notice--success">{notice}</p> : null}
    <div className="business-grid">
      <section className="admin-card"><div className="card-heading card-heading--spaced"><h3>{fa ? "پایگاه جدید" : "New knowledge base"}</h3></div><form className="stack-form" onSubmit={(event) => void createBase(event)}><label><span>{fa ? "نام" : "Name"}</span><input required value={baseName} onChange={(event) => setBaseName(event.target.value)} /></label><label><span>{fa ? "توضیح" : "Description"}</span><input value={baseDescription} onChange={(event) => setBaseDescription(event.target.value)} /></label><button className="button" disabled={busy} type="submit">{fa ? "ایجاد پایگاه" : "Create base"}</button></form></section>
      <section className="admin-card"><div className="card-heading card-heading--spaced"><h3>{fa ? "انتخاب پایگاه" : "Select knowledge base"}</h3></div>{bases.length === 0 ? <p className="empty-panel">{fa ? "ابتدا یک پایگاه دانش بسازید." : "Create a knowledge base first."}</p> : <label className="stack-form"><span>{fa ? "پایگاه فعال" : "Active base"}</span><select value={selectedBaseId} onChange={(event) => setSelectedBaseId(event.target.value)}>{bases.map((base) => <option key={base.id} value={base.id}>{base.name}</option>)}</select></label>}</section>
    </div>
    {selectedBase ? <div className="business-grid">
      <section className="admin-card"><div className="card-heading card-heading--spaced"><h3>{fa ? "افزودن فایل به «" + selectedBase.name + "»" : `Add files to “${selectedBase.name}”`}</h3></div><form className="stack-form" onSubmit={(event) => void submitUpload(event)}><label><span>{fa ? "فایل PDF یا XLSX" : "PDF or XLSX file"}</span><input accept=".pdf,.xlsx,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={handleFile} required type="file" /></label><button className="button" disabled={busy || !upload} type="submit">{fa ? "بارگذاری و پردازش" : "Upload and process"}</button></form></section>
      <section className="admin-card"><div className="card-heading card-heading--spaced"><h3>{fa ? "دسترسی عامل" : "Agent access"}</h3></div>{agents.length === 0 ? <p className="empty-panel">{fa ? "برای استفاده از دانش، ابتدا یک Agent بسازید." : "Create an agent before assigning knowledge."}</p> : <div className="stack-form"><label><span>{fa ? "عامل" : "Agent"}</span><select value={selectedAgentId} onChange={(event) => setSelectedAgentId(event.target.value)}>{agents.filter((agent) => agent.is_active).map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label><button className={isAssigned ? "button button--secondary" : "button"} disabled={busy || !selectedAgentId} onClick={() => void setPermission()} type="button">{isAssigned ? (fa ? "حذف دسترسی عامل" : "Remove agent access") : (fa ? "اتصال به عامل" : "Connect to agent")}</button></div>}</section>
    </div> : null}
    {selectedBase ? <section className="admin-card"><div className="card-heading card-heading--spaced"><h3>{fa ? "فایل‌های پردازشی" : "Processing files"}</h3></div>{sources.length === 0 ? <p className="empty-panel">{fa ? "هنوز فایلی اضافه نشده است." : "No files have been added yet."}</p> : <div className="entity-list">{sources.map((source) => <article className="entity-row" key={source.id}><div><strong>{source.filename}</strong><small>{Math.ceil(source.size_bytes / 1024)} KB · {source.chunk_count} {fa ? "بخش" : "chunks"}</small></div><span className={`status-pill status-pill--quiet source-status source-status--${source.status}`}>{source.status}</span></article>)}</div>}</section> : null}
  </div>;
}
