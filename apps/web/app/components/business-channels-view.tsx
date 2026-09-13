"use client";

import { type FormEvent, useEffect, useState } from "react";

import {
  ApiError,
  createTelegramChannel,
  getChannelAgentAssignment,
  listBusinessAgents,
  listChannelAccounts,
  registerTelegramWebhook,
  setChannelAgentAssignment,
  updateChannelAccount,
  type AgentChannelAssignment,
  type BusinessAgent,
  type ChannelAccount,
  type ChannelCapability,
} from "../lib/api";
import { usePreferences } from "./app-preferences";

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Unable to save changes.";
}

export function BusinessChannelsView({ tenantId }: Readonly<{ tenantId: string }>) {
  const { locale } = usePreferences();
  const fa = locale === "fa";
  const [channels, setChannels] = useState<ChannelAccount[]>([]);
  const [agents, setAgents] = useState<BusinessAgent[]>([]);
  const [assignments, setAssignments] = useState<Record<string, AgentChannelAssignment>>({});
  const [name, setName] = useState("");
  const [botToken, setBotToken] = useState("");
  const [acceptVoice, setAcceptVoice] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    const [nextChannels, nextAgents] = await Promise.all([listChannelAccounts(tenantId), listBusinessAgents(tenantId)]);
    const assignmentRows = await Promise.all(nextChannels.map(async (channel) => {
      try { return await getChannelAgentAssignment(tenantId, channel.id); }
      catch (reason) { if (reason instanceof ApiError && reason.status === 404) return null; throw reason; }
    }));
    setChannels(nextChannels); setAgents(nextAgents);
    setAssignments(Object.fromEntries(assignmentRows.filter((row): row is AgentChannelAssignment => row !== null).map((row) => [row.channel_account_id, row])));
  };

  useEffect(() => {
    let cancelled = false;
    void Promise.all([listChannelAccounts(tenantId), listBusinessAgents(tenantId)])
      .then(async ([nextChannels, nextAgents]) => {
        const rows = await Promise.all(nextChannels.map(async (channel) => {
          try { return await getChannelAgentAssignment(tenantId, channel.id); }
          catch (reason) { if (reason instanceof ApiError && reason.status === 404) return null; throw reason; }
        }));
        if (cancelled) return;
        setChannels(nextChannels); setAgents(nextAgents);
        setAssignments(Object.fromEntries(rows.filter((row): row is AgentChannelAssignment => row !== null).map((row) => [row.channel_account_id, row])));
      })
      .catch((reason) => { if (!cancelled) setError(errorMessage(reason)); });
    return () => { cancelled = true; };
  }, [tenantId]);

  const addTelegram = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setError(null); setNotice(null);
    try {
      const inbound: ChannelCapability[] = acceptVoice ? ["text", "voice"] : ["text"];
      await createTelegramChannel(tenantId, name, botToken, inbound);
      setName(""); setBotToken(""); await load();
      setNotice(fa ? "ربات تلگرام ثبت شد. اکنون webhook را ثبت کنید." : "Telegram bot added. Register its webhook next.");
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  const registerWebhook = async (channel: ChannelAccount) => {
    setBusy(true); setError(null); setNotice(null);
    try {
      const result = await registerTelegramWebhook(tenantId, channel.id);
      setNotice(fa ? `Webhook با موفقیت ثبت شد: ${result.url}` : `Webhook registered: ${result.url}`);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  const toggleChannel = async (channel: ChannelAccount) => {
    setBusy(true); setError(null);
    try { await updateChannelAccount(tenantId, channel.id, { is_active: !channel.is_active }); await load(); }
    catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  const assignAgent = async (channelId: string, agentId: string) => {
    if (!agentId) return;
    setBusy(true); setError(null);
    try { await setChannelAgentAssignment(tenantId, channelId, agentId); await load(); }
    catch (reason) { setError(errorMessage(reason)); }
    finally { setBusy(false); }
  };

  return (
    <div className="admin-page business-page">
      <header className="admin-heading"><div><span className="section-eyebrow">{fa ? "ارتباط با مشتری" : "Customer connections"}</span><h2>{fa ? "کانال‌ها" : "Channels"}</h2><p>{fa ? "توکن BotFather را فقط برای اعتبارسنجی و ذخیرهٔ رمزنگاری‌شده وارد کنید؛ پس از ثبت هرگز در پنل نمایش داده نمی‌شود." : "Enter the BotFather token only for validation and encrypted storage; it is never shown in the panel again."}</p></div></header>
      {error ? <p className="notice notice--error">{error}</p> : null}
      {notice ? <p className="notice notice--success">{notice}</p> : null}
      <div className="business-grid">
        <section className="admin-card">
          <div className="card-heading card-heading--spaced"><h3>{fa ? "اتصال تلگرام" : "Connect Telegram"}</h3></div>
          <form className="stack-form" onSubmit={(event) => void addTelegram(event)}>
            <label><span>{fa ? "نام کانال" : "Channel name"}</span><input required value={name} onChange={(event) => setName(event.target.value)} placeholder={fa ? "پشتیبانی تلگرام" : "Telegram support"} /></label>
            <label><span>{fa ? "توکن ربات از BotFather" : "Bot token from BotFather"}</span><input autoComplete="off" required type="password" value={botToken} onChange={(event) => setBotToken(event.target.value)} /></label>
            <label className="check-label"><input checked={acceptVoice} onChange={(event) => setAcceptVoice(event.target.checked)} type="checkbox" /><span>{fa ? "پذیرش پیام صوتی" : "Accept voice messages"}</span></label>
            <button className="button" disabled={busy} type="submit">{fa ? "اعتبارسنجی و افزودن ربات" : "Validate and add bot"}</button>
          </form>
        </section>
        <section className="admin-card">
          <div className="card-heading card-heading--spaced"><h3>{fa ? "پیش‌نیاز webhook" : "Webhook prerequisite"}</h3></div>
          <p className="muted-line">{fa ? "برای دریافت پیام‌ها، آدرس عمومی HTTPS سامانه باید در تنظیمات Docker به عنوان Telegram Webhook Base URL تنظیم شود. پس از آن از دکمهٔ «ثبت webhook» استفاده کنید." : "Receiving messages requires a public HTTPS application URL configured as the Telegram Webhook Base URL in Docker. Then use Register webhook."}</p>
        </section>
      </div>
      <section className="admin-card">
        <div className="card-heading card-heading--spaced"><h3>{fa ? "کانال‌های متصل" : "Connected channels"}</h3></div>
        {channels.length === 0 ? <p className="empty-panel">{fa ? "هیچ کانالی متصل نشده است." : "No channels are connected."}</p> : <div className="entity-list">{channels.map((channel) => (
          <article className="entity-row entity-row--wide" key={channel.id}>
            <div><strong>{channel.name}</strong><small>{channel.channel_type === "telegram" ? `Telegram${channel.external_username ? ` · @${channel.external_username}` : ""}` : channel.channel_type}</small></div>
            <label><span>{fa ? "عامل پاسخ‌گو" : "Responding agent"}</span><select disabled={busy || agents.length === 0} value={assignments[channel.id]?.agent_id ?? ""} onChange={(event) => void assignAgent(channel.id, event.target.value)}><option value="">{agents.length === 0 ? (fa ? "ابتدا عامل بسازید" : "Create an agent first") : (fa ? "انتخاب عامل" : "Choose an agent")}</option>{agents.filter((agent) => agent.is_active).map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}</select></label>
            <div className="button-row"><button className="button button--secondary button--small" disabled={busy || channel.channel_type !== "telegram"} onClick={() => void registerWebhook(channel)} type="button">{fa ? "ثبت webhook" : "Register webhook"}</button><label className="switch-label"><input checked={channel.is_active} disabled={busy} onChange={() => void toggleChannel(channel)} type="checkbox" /><span>{channel.is_active ? (fa ? "فعال" : "Active") : (fa ? "غیرفعال" : "Inactive")}</span></label></div>
          </article>
        ))}</div>}
      </section>
    </div>
  );
}
