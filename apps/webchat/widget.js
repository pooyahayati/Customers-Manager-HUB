(() => {
  "use strict";

  const script = document.currentScript;
  if (!(script instanceof HTMLScriptElement)) return;

  const apiBase = (script.dataset.apiBase || "").replace(/\/$/, "");
  const channelAccountId = script.dataset.channelAccountId || "";
  if (!apiBase || !channelAccountId) {
    console.error("Customers Manager HUB Webchat requires data-api-base and data-channel-account-id");
    return;
  }

  const storageKey = `cmh:webchat:${channelAccountId}`;
  let session = null;
  let pollTimer = null;
  const rendered = new Set();

  try {
    const stored = localStorage.getItem(storageKey);
    if (stored) session = JSON.parse(stored);
  } catch (_) {
    session = null;
  }

  const host = document.createElement("div");
  const root = host.attachShadow({ mode: "open" });
  const style = document.createElement("style");
  style.textContent = `
    :host { all: initial; }
    .launcher { position: fixed; right: 20px; bottom: 20px; z-index: 2147483000; border: 0;
      border-radius: 999px; padding: 12px 16px; background: #111827; color: #fff; cursor: pointer;
      font: 600 14px system-ui, sans-serif; box-shadow: 0 8px 30px rgba(0,0,0,.18); }
    .panel { position: fixed; right: 20px; bottom: 76px; z-index: 2147483000; width: min(360px, calc(100vw - 32px));
      height: min(520px, calc(100vh - 110px)); display: none; flex-direction: column; overflow: hidden;
      border: 1px solid rgba(17,24,39,.12); border-radius: 16px; background: #fff;
      box-shadow: 0 18px 60px rgba(0,0,0,.22); font: 14px system-ui, sans-serif; color: #111827; }
    .panel.open { display: flex; }
    .header { padding: 14px 16px; font-weight: 700; border-bottom: 1px solid #e5e7eb; }
    .messages { flex: 1; overflow: auto; padding: 14px; display: flex; flex-direction: column; gap: 8px; }
    .message { max-width: 82%; padding: 9px 11px; border-radius: 12px; white-space: pre-wrap; overflow-wrap: anywhere; }
    .inbound { align-self: flex-end; background: #111827; color: #fff; }
    .outbound { align-self: flex-start; background: #f3f4f6; color: #111827; }
    .composer { display: flex; gap: 8px; padding: 10px; border-top: 1px solid #e5e7eb; }
    input { min-width: 0; flex: 1; border: 1px solid #d1d5db; border-radius: 10px; padding: 9px 10px;
      font: inherit; color: inherit; background: #fff; }
    .send { border: 0; border-radius: 10px; padding: 9px 12px; background: #111827; color: #fff; cursor: pointer; }
    .status { min-height: 18px; padding: 0 12px 8px; color: #6b7280; font-size: 12px; }
  `;

  const panel = document.createElement("section");
  panel.className = "panel";
  const header = document.createElement("div");
  header.className = "header";
  header.textContent = "Chat";
  const messages = document.createElement("div");
  messages.className = "messages";
  const form = document.createElement("form");
  form.className = "composer";
  const input = document.createElement("input");
  input.type = "text";
  input.maxLength = 4096;
  input.placeholder = "Type a message…";
  input.autocomplete = "off";
  const send = document.createElement("button");
  send.type = "submit";
  send.className = "send";
  send.textContent = "Send";
  const statusNode = document.createElement("div");
  statusNode.className = "status";
  const launcher = document.createElement("button");
  launcher.type = "button";
  launcher.className = "launcher";
  launcher.textContent = "Chat";

  form.append(input, send);
  panel.append(header, messages, form, statusNode);
  root.append(style, panel, launcher);
  document.body.append(host);

  const setStatus = (value) => { statusNode.textContent = value || ""; };

  async function ensureSession() {
    if (session?.session_id && session?.session_token) return session;
    const response = await fetch(`${apiBase}/api/v1/public/website/${channelAccountId}/sessions`, {
      method: "POST",
      mode: "cors",
      credentials: "omit",
    });
    if (!response.ok) throw new Error("Unable to start chat session");
    session = await response.json();
    try { localStorage.setItem(storageKey, JSON.stringify(session)); } catch (_) {}
    return session;
  }

  function renderMessage(item) {
    if (!item?.id || rendered.has(item.id) || typeof item.text !== "string") return;
    rendered.add(item.id);
    const node = document.createElement("div");
    node.className = `message ${item.direction === "inbound" ? "inbound" : "outbound"}`;
    node.textContent = item.text;
    messages.append(node);
    messages.scrollTop = messages.scrollHeight;
  }

  async function poll() {
    try {
      const current = await ensureSession();
      const response = await fetch(
        `${apiBase}/api/v1/public/website/${channelAccountId}/sessions/${current.session_id}/messages`,
        {
          method: "GET",
          mode: "cors",
          credentials: "omit",
          headers: { "X-Website-Session-Token": current.session_token },
        },
      );
      if (response.status === 401) {
        session = null;
        rendered.clear();
        try { localStorage.removeItem(storageKey); } catch (_) {}
        return;
      }
      if (!response.ok) throw new Error("Unable to load messages");
      const items = await response.json();
      for (const item of items) renderMessage(item);
      setStatus("");
    } catch (_) {
      setStatus("Connection unavailable. Retrying…");
    }
  }

  function startPolling() {
    if (pollTimer) return;
    void poll();
    pollTimer = window.setInterval(() => { void poll(); }, 1500);
  }

  function stopPolling() {
    if (!pollTimer) return;
    window.clearInterval(pollTimer);
    pollTimer = null;
  }

  launcher.addEventListener("click", () => {
    const opening = !panel.classList.contains("open");
    panel.classList.toggle("open", opening);
    launcher.textContent = opening ? "Close" : "Chat";
    if (opening) {
      startPolling();
      input.focus();
    } else {
      stopPolling();
    }
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    send.disabled = true;
    try {
      const current = await ensureSession();
      const response = await fetch(
        `${apiBase}/api/v1/public/website/${channelAccountId}/sessions/${current.session_id}/messages`,
        {
          method: "POST",
          mode: "cors",
          credentials: "omit",
          headers: {
            "Content-Type": "application/json",
            "X-Website-Session-Token": current.session_token,
          },
          body: JSON.stringify({ client_message_id: crypto.randomUUID(), text }),
        },
      );
      if (!response.ok) throw new Error("Unable to send message");
      input.value = "";
      setStatus("");
      await poll();
    } catch (_) {
      setStatus("Message was not sent. Try again.");
    } finally {
      send.disabled = false;
      input.focus();
    }
  });
})();
