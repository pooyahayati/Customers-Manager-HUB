"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import {
  ApiError,
  getBillingDisplayUnit,
  listModelPricing,
  listPlatformTaskProfiles,
  listProviderModels,
  listProviderStatuses,
  saveModelPricing,
  savePlatformTaskProfile,
  saveProviderCredential,
  setBillingDisplayUnit,
  testProviderConnection,
  type AIProvider,
  type AITaskType,
  type BillingDisplayUnit,
  type ModelPrice,
  type ProviderModel,
  type ProviderStatus,
} from "../lib/api";
import { usePreferences } from "./app-preferences";

const providers: AIProvider[] = ["openai", "gemini"];
const tasks: AITaskType[] = [
  "customer_response",
  "voice_transcription",
  "intent_classification",
  "conversation_summary",
  "customer_memory_extraction",
  "embedding",
];

type TaskDraft = { provider: AIProvider; modelId: string };

function emptyTaskDrafts(): Record<AITaskType, TaskDraft> {
  return Object.fromEntries(
    tasks.map((task) => [task, { provider: "openai", modelId: "" }]),
  ) as Record<AITaskType, TaskDraft>;
}

function requestError(error: unknown): string {
  return error instanceof ApiError ? error.message : "Unexpected request failure";
}

export function OwnerSettingsView() {
  const { locale } = usePreferences();
  const fa = locale === "fa";
  const [statuses, setStatuses] = useState<ProviderStatus[]>([]);
  const [apiKeys, setApiKeys] = useState<Record<AIProvider, string>>({ openai: "", gemini: "" });
  const [taskDrafts, setTaskDrafts] = useState<Record<AITaskType, TaskDraft>>(emptyTaskDrafts);
  const [taskModels, setTaskModels] = useState<Partial<Record<AITaskType, ProviderModel[]>>>({});
  const [prices, setPrices] = useState<ModelPrice[]>([]);
  const [displayUnit, setDisplayUnitState] = useState<BillingDisplayUnit>("rial");
  const [priceProvider, setPriceProvider] = useState<AIProvider>("openai");
  const [priceModel, setPriceModel] = useState("");
  const [inputRate, setInputRate] = useState("");
  const [outputRate, setOutputRate] = useState("");
  const [audioRate, setAudioRate] = useState("");
  const [busyKey, setBusyKey] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    const [nextStatuses, profiles, nextPrices, billing] = await Promise.all([
      listProviderStatuses(),
      listPlatformTaskProfiles(),
      listModelPricing(),
      getBillingDisplayUnit(),
    ]);
    setStatuses(nextStatuses);
    setPrices(nextPrices);
    setDisplayUnitState(billing.display_unit);
    setTaskDrafts((current) => {
      const next = { ...current };
      for (const profile of profiles) {
        const route = profile.routes[0];
        if (route) next[profile.task_type] = { provider: route.provider, modelId: route.model_id };
      }
      return next;
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      listProviderStatuses(),
      listPlatformTaskProfiles(),
      listModelPricing(),
      getBillingDisplayUnit(),
    ])
      .then(([nextStatuses, profiles, nextPrices, billing]) => {
        if (cancelled) return;
        setStatuses(nextStatuses);
        setPrices(nextPrices);
        setDisplayUnitState(billing.display_unit);
        setTaskDrafts((current) => {
          const next = { ...current };
          for (const profile of profiles) {
            const route = profile.routes[0];
            if (route) {
              next[profile.task_type] = { provider: route.provider, modelId: route.model_id };
            }
          }
          return next;
        });
      })
      .catch((errorValue) => {
        if (!cancelled) setError(requestError(errorValue));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const flash = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice(null), 3500);
  };

  const saveKey = async (provider: AIProvider) => {
    const key = apiKeys[provider].trim();
    if (!key) return;
    setBusyKey(`key-${provider}`);
    setError(null);
    try {
      await saveProviderCredential(provider, key);
      setApiKeys((current) => ({ ...current, [provider]: "" }));
      await reload();
      flash(fa ? "کلید API به‌صورت رمزنگاری‌شده ذخیره شد." : "API key saved encrypted.");
    } catch (errorValue) {
      setError(requestError(errorValue));
    } finally {
      setBusyKey("");
    }
  };

  const testKey = async (provider: AIProvider) => {
    setBusyKey(`test-${provider}`);
    setError(null);
    try {
      const result = await testProviderConnection(provider);
      await reload();
      flash(
        fa
          ? `اتصال صحیح است؛ ${result.model_count.toLocaleString("fa-IR")} مدل دریافت شد.`
          : `Connection is valid; ${result.model_count.toLocaleString("en-US")} models found.`,
      );
    } catch (errorValue) {
      setError(requestError(errorValue));
      await reload().catch(() => undefined);
    } finally {
      setBusyKey("");
    }
  };

  const loadModels = async (task: AITaskType) => {
    const draft = taskDrafts[task];
    setBusyKey(`models-${task}`);
    setError(null);
    try {
      const models = await listProviderModels(draft.provider, task);
      setTaskModels((current) => ({ ...current, [task]: models }));
      flash(fa ? "فهرست مدل‌های سازگار به‌روز شد." : "Compatible model list refreshed.");
    } catch (errorValue) {
      setError(requestError(errorValue));
    } finally {
      setBusyKey("");
    }
  };

  const saveTask = async (task: AITaskType) => {
    const draft = taskDrafts[task];
    if (!draft.modelId.trim()) return;
    setBusyKey(`task-${task}`);
    setError(null);
    try {
      await savePlatformTaskProfile(task, draft.provider, draft.modelId.trim());
      flash(fa ? "مدل این وظیفه برای همه کسب‌وکارها ذخیره شد." : "Task model saved for every business.");
    } catch (errorValue) {
      setError(requestError(errorValue));
    } finally {
      setBusyKey("");
    }
  };

  const updateDisplayUnit = async (value: BillingDisplayUnit) => {
    setBusyKey("display-unit");
    setError(null);
    try {
      const result = await setBillingDisplayUnit(value);
      setDisplayUnitState(result.display_unit);
      flash(fa ? "واحد نمایش پنل کاربران ذخیره شد." : "Business display unit saved.");
    } catch (errorValue) {
      setError(requestError(errorValue));
    } finally {
      setBusyKey("");
    }
  };

  const submitPrice = async (event: FormEvent) => {
    event.preventDefault();
    const input = Number(inputRate);
    const output = Number(outputRate);
    const audio = Number(audioRate);
    if (![input, output, audio].every((value) => Number.isSafeInteger(value) && value >= 0)) return;
    setBusyKey("pricing");
    setError(null);
    try {
      await saveModelPricing({
        provider: priceProvider,
        model_id: priceModel.trim(),
        input_per_million_rial: input,
        output_per_million_rial: output,
        audio_per_minute_rial: audio,
      });
      setPriceModel("");
      setInputRate("");
      setOutputRate("");
      setAudioRate("");
      await reload();
      flash(fa ? "نرخ فروش جدید ثبت شد." : "New selling rate saved.");
    } catch (errorValue) {
      setError(requestError(errorValue));
    } finally {
      setBusyKey("");
    }
  };

  const taskLabel = (task: AITaskType) => {
    const labels: Record<AITaskType, [string, string]> = {
      customer_response: ["Customer response", "پاسخ‌گویی به مشتری"],
      voice_transcription: ["Voice transcription", "تبدیل صوت به متن"],
      intent_classification: ["Intent classification", "تشخیص هدف پیام"],
      conversation_summary: ["Conversation summary", "خلاصه‌سازی گفتگو"],
      customer_memory_extraction: ["Customer memory", "استخراج حافظه مشتری"],
      embedding: ["Knowledge embedding", "بردارسازی دانش"],
    };
    return labels[task][fa ? 1 : 0];
  };

  return (
    <section className="admin-page">
      <header className="admin-heading">
        <div>
          <span className="section-eyebrow">{fa ? "تنظیمات سراسری Owner" : "Global owner settings"}</span>
          <h2>{fa ? "هوش مصنوعی و صورتحساب" : "AI and billing"}</h2>
          <p>{fa ? "این تنظیمات برای تمام Businessها اعمال می‌شوند و برای کاربران عادی قابل تغییر نیستند." : "These settings apply to every Business and are not editable by regular users."}</p>
        </div>
      </header>

      {notice ? <div className="notice notice--success">{notice}</div> : null}
      {error ? <div className="notice notice--error">{error}</div> : null}

      <div className="settings-section">
        <div className="settings-section__heading">
          <div><h3>{fa ? "ارائه‌دهندگان AI" : "AI providers"}</h3><p>{fa ? "کلیدها رمزنگاری می‌شوند و هرگز دوباره در UI نمایش داده نمی‌شوند." : "Keys are encrypted and never returned to the UI."}</p></div>
        </div>
        <div className="provider-grid">
          {providers.map((provider) => {
            const status = statuses.find((item) => item.provider === provider);
            return (
              <article className="provider-card" key={provider}>
                <div className="card-heading"><h4>{provider === "openai" ? "OpenAI" : "Google Gemini"}</h4><span className={`status-pill ${status?.configured ? "status-pill--success" : "status-pill--quiet"}`}>{status?.configured ? (fa ? "متصل" : "Configured") : (fa ? "تنظیم نشده" : "Not configured")}</span></div>
                <p>{status?.source === "environment" ? (fa ? "کلید فعلی از متغیر محیطی خوانده می‌شود." : "Current key comes from the environment.") : status?.source === "database" ? (fa ? "کلید رمزنگاری‌شده در سامانه ثبت شده است." : "Encrypted key is stored in the platform.") : (fa ? "برای اتصال یک کلید API وارد کنید." : "Enter an API key to connect.")}</p>
                <label><span>{fa ? "کلید جدید / جایگزین" : "New / replacement key"}</span><input dir="ltr" onChange={(event) => setApiKeys((current) => ({ ...current, [provider]: event.target.value }))} placeholder="••••••••••••" type="password" value={apiKeys[provider]} /></label>
                <div className="button-row">
                  <button className="button button--primary button--small" disabled={busyKey !== "" || !apiKeys[provider].trim()} onClick={() => void saveKey(provider)} type="button">{fa ? "ذخیره امن" : "Save securely"}</button>
                  <button className="button button--secondary button--small" disabled={busyKey !== "" || !status?.configured} onClick={() => void testKey(provider)} type="button">{busyKey === `test-${provider}` ? (fa ? "در حال بررسی…" : "Testing…") : (fa ? "تست اتصال" : "Test connection")}</button>
                </div>
                {status?.last_tested_at ? <small className="muted-line">{status.last_test_succeeded ? (fa ? "آخرین تست موفق" : "Last test succeeded") : `${fa ? "خطا" : "Error"}: ${status.last_error_code ?? "unknown"}`}</small> : null}
              </article>
            );
          })}
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section__heading"><div><h3>{fa ? "مدل هر وظیفه" : "Model by task"}</h3><p>{fa ? "مدل را از فهرست دریافتی انتخاب کنید یا شناسه آن را دستی وارد کنید. برای شروع مصرف، نرخ ریالی همان مدل نیز باید ثبت شود. ساخت تصویر فعلاً در دامنه نیست." : "Choose a discovered model or enter its ID manually. Its Rial selling rate must also be configured before usage can start. Image generation is currently out of scope."}</p></div></div>
        <div className="task-list">
          {tasks.map((task) => {
            const draft = taskDrafts[task];
            const listId = `models-${task}`;
            return (
              <div className="task-row" key={task}>
                <strong>{taskLabel(task)}</strong>
                <select value={draft.provider} onChange={(event) => setTaskDrafts((current) => ({ ...current, [task]: { provider: event.target.value as AIProvider, modelId: "" } }))}><option value="openai">OpenAI</option><option value="gemini">Gemini</option></select>
                <input dir="ltr" list={listId} onChange={(event) => setTaskDrafts((current) => ({ ...current, [task]: { ...current[task], modelId: event.target.value } }))} placeholder={fa ? "شناسه مدل" : "Model ID"} value={draft.modelId} />
                <datalist id={listId}>{(taskModels[task] ?? []).map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}</datalist>
                <button className="button button--secondary button--small" disabled={busyKey !== ""} onClick={() => void loadModels(task)} type="button">{fa ? "دریافت لیست" : "Fetch list"}</button>
                <button className="button button--primary button--small" disabled={busyKey !== "" || !draft.modelId.trim()} onClick={() => void saveTask(task)} type="button">{fa ? "ذخیره" : "Save"}</button>
              </div>
            );
          })}
        </div>
      </div>

      <div className="settings-section">
        <div className="settings-section__heading settings-section__heading--split">
          <div><h3>{fa ? "واحد نمایش و نرخ فروش" : "Display unit and selling rates"}</h3><p>{fa ? "واحد پایه و محاسبات همیشه ریال باقی می‌ماند." : "The canonical storage and calculation unit always remains Rial."}</p></div>
          <label className="unit-selector"><span>{fa ? "نمایش پنل Business" : "Business panel display"}</span><select disabled={busyKey === "display-unit"} onChange={(event) => void updateDisplayUnit(event.target.value as BillingDisplayUnit)} value={displayUnit}><option value="rial">{fa ? "ریال" : "Rial"}</option><option value="toman">{fa ? "تومان" : "Toman"}</option></select></label>
        </div>
        <div className="pricing-layout">
          <form className="pricing-form" onSubmit={submitPrice}>
            <label><span>{fa ? "ارائه‌دهنده" : "Provider"}</span><select onChange={(event) => setPriceProvider(event.target.value as AIProvider)} value={priceProvider}><option value="openai">OpenAI</option><option value="gemini">Gemini</option></select></label>
            <label><span>{fa ? "شناسه مدل" : "Model ID"}</span><input dir="ltr" onChange={(event) => setPriceModel(event.target.value)} required value={priceModel} /></label>
            <label><span>{fa ? "ورودی / یک‌میلیون توکن (ریال)" : "Input / 1M tokens (Rial)"}</span><input dir="ltr" min="0" onChange={(event) => setInputRate(event.target.value)} required type="number" value={inputRate} /></label>
            <label><span>{fa ? "خروجی / یک‌میلیون توکن (ریال)" : "Output / 1M tokens (Rial)"}</span><input dir="ltr" min="0" onChange={(event) => setOutputRate(event.target.value)} required type="number" value={outputRate} /></label>
            <label><span>{fa ? "هر دقیقه صوت (ریال)" : "Audio minute (Rial)"}</span><input dir="ltr" min="0" onChange={(event) => setAudioRate(event.target.value)} required type="number" value={audioRate} /></label>
            <button className="button button--primary" disabled={busyKey !== ""} type="submit">{fa ? "ثبت نرخ جدید" : "Save new rate"}</button>
          </form>
          <div className="price-list">
            {prices.length ? prices.map((price) => <div className="price-row" key={price.id}><span><strong>{price.provider} / {price.model_id}</strong><small>{fa ? "ورودی" : "Input"}: {price.input_per_million_rial.toLocaleString("en-US")} · {fa ? "خروجی" : "Output"}: {price.output_per_million_rial.toLocaleString("en-US")} IRR</small></span><span>{price.audio_per_minute_rial.toLocaleString("en-US")} <small>IRR/min</small></span></div>) : <div className="empty-panel">{fa ? "هنوز نرخ فروشی ثبت نشده است." : "No selling rates configured yet."}</div>}
          </div>
        </div>
      </div>
    </section>
  );
}
