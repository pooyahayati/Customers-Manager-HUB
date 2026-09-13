"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import {
  ApiError,
  createBusiness,
  creditBusinessWallet,
  getBillingDisplayUnit,
  getBusinessWallet,
  listBusinesses,
  listBusinessWalletTransactions,
  setBusinessActive,
  type BillingDisplayUnit,
  type Business,
  type Wallet,
  type WalletTransaction,
} from "../lib/api";
import { usePreferences } from "./app-preferences";

function errorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Unexpected request failure";
}

export function OwnerBusinessesView() {
  const { locale } = usePreferences();
  const fa = locale === "fa";
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [displayUnit, setDisplayUnit] = useState<BillingDisplayUnit>("rial");
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [transactions, setTransactions] = useState<WalletTransaction[]>([]);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [credit, setCredit] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadBusinesses = useCallback(async () => {
    const [rows, settings] = await Promise.all([listBusinesses(), getBillingDisplayUnit()]);
    setBusinesses(rows);
    setDisplayUnit(settings.display_unit);
    setSelectedId((current) =>
      rows.some((business) => business.id === current) ? current : (rows[0]?.id ?? ""),
    );
  }, []);

  const loadWallet = useCallback(async (businessId: string) => {
    if (!businessId) {
      setWallet(null);
      setTransactions([]);
      return;
    }
    const [nextWallet, nextTransactions] = await Promise.all([
      getBusinessWallet(businessId),
      listBusinessWalletTransactions(businessId),
    ]);
    setWallet(nextWallet);
    setTransactions(nextTransactions);
  }, []);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([listBusinesses(), getBillingDisplayUnit()])
      .then(([rows, settings]) => {
        if (cancelled) return;
        setBusinesses(rows);
        setDisplayUnit(settings.display_unit);
        setSelectedId((current) =>
          rows.some((business) => business.id === current) ? current : (rows[0]?.id ?? ""),
        );
      })
      .catch((requestError) => {
        if (!cancelled) setError(errorMessage(requestError));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    void Promise.all([getBusinessWallet(selectedId), listBusinessWalletTransactions(selectedId)])
      .then(([nextWallet, nextTransactions]) => {
        if (cancelled) return;
        setWallet(nextWallet);
        setTransactions(nextTransactions);
      })
      .catch((requestError) => {
        if (!cancelled) setError(errorMessage(requestError));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const money = (rial: number) => {
    const shown = displayUnit === "toman" ? rial / 10 : rial;
    return `${new Intl.NumberFormat(fa ? "fa-IR" : "en-US").format(shown)} ${
      displayUnit === "toman" ? (fa ? "تومان" : "Toman") : (fa ? "ریال" : "Rial")
    }`;
  };

  const submitBusiness = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await createBusiness(name, slug);
      setName("");
      setSlug("");
      await loadBusinesses();
      setSelectedId(created.id);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  };

  const submitCredit = async (event: FormEvent) => {
    event.preventDefault();
    const amount = Number(credit);
    if (!Number.isSafeInteger(amount) || amount <= 0 || !selectedId) return;
    setBusy(true);
    setError(null);
    try {
      await creditBusinessWallet(selectedId, amount, note);
      setCredit("");
      setNote("");
      await loadWallet(selectedId);
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  };

  const toggleBusiness = async (business: Business) => {
    setBusy(true);
    setError(null);
    try {
      await setBusinessActive(business.id, !business.is_active);
      await loadBusinesses();
    } catch (requestError) {
      setError(errorMessage(requestError));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="admin-page">
      <header className="admin-heading">
        <div>
          <span className="section-eyebrow">{fa ? "مدیریت مالک سامانه" : "Platform owner"}</span>
          <h2>{fa ? "کسب‌وکارها و اعتبار" : "Businesses and wallet credit"}</h2>
          <p>
            {fa
              ? "موجودی در دیتابیس همیشه ریالی است؛ واحد انتخابی فقط نحوه نمایش به کاربران را تغییر می‌دهد."
              : "Balances are always stored in Rial; the selected unit changes presentation only."}
          </p>
        </div>
      </header>

      {error ? <div className="notice notice--error">{error}</div> : null}

      <div className="admin-grid admin-grid--sidebar">
        <div className="admin-card">
          <div className="card-heading">
            <h3>{fa ? "فهرست کسب‌وکارها" : "Business directory"}</h3>
            <span className="status-pill status-pill--quiet">{businesses.length}</span>
          </div>
          <div className="business-list">
            {businesses.map((business) => (
              <button
                className={`business-row${selectedId === business.id ? " is-selected" : ""}`}
                key={business.id}
                onClick={() => setSelectedId(business.id)}
                type="button"
              >
                <span>
                  <strong>{business.name}</strong>
                  <small>{business.slug} · {business.user_count} {fa ? "کاربر" : "users"}</small>
                </span>
                <span className={`status-dot${business.is_active ? " is-active" : ""}`} />
              </button>
            ))}
          </div>
          <form className="stack-form" onSubmit={submitBusiness}>
            <h4>{fa ? "کسب‌وکار جدید" : "New business"}</h4>
            <label>
              <span>{fa ? "نام" : "Name"}</span>
              <input onChange={(event) => setName(event.target.value)} required value={name} />
            </label>
            <label>
              <span>{fa ? "شناسه انگلیسی" : "Slug"}</span>
              <input
                dir="ltr"
                onChange={(event) => setSlug(event.target.value.toLowerCase())}
                pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
                placeholder="acme-support"
                required
                value={slug}
              />
            </label>
            <button className="button button--primary" disabled={busy} type="submit">
              {fa ? "افزودن کسب‌وکار" : "Add business"}
            </button>
          </form>
        </div>

        <div className="admin-card admin-card--wide">
          {wallet ? (
            <>
              <div className="wallet-hero">
                <span>{fa ? "اعتبار فعلی" : "Current balance"}</span>
                <strong>{money(wallet.balance_rial)}</strong>
                <small>{fa ? "مقدار پایه" : "Canonical amount"}: {wallet.balance_rial.toLocaleString("en-US")} IRR</small>
              </div>
              <form className="inline-form" onSubmit={submitCredit}>
                <label>
                  <span>{fa ? "مبلغ شارژ (ریال)" : "Credit amount (Rial)"}</span>
                  <input
                    dir="ltr"
                    inputMode="numeric"
                    min="1"
                    onChange={(event) => setCredit(event.target.value)}
                    required
                    type="number"
                    value={credit}
                  />
                </label>
                <label className="inline-form__grow">
                  <span>{fa ? "یادداشت" : "Note"}</span>
                  <input onChange={(event) => setNote(event.target.value)} value={note} />
                </label>
                <button className="button button--primary" disabled={busy} type="submit">
                  {fa ? "ثبت شارژ" : "Add credit"}
                </button>
              </form>

              <div className="card-heading card-heading--spaced">
                <h3>{fa ? "گردش کیف پول" : "Wallet activity"}</h3>
                {businesses.find((item) => item.id === selectedId) ? (
                  <button
                    className="button button--secondary button--small"
                    disabled={busy}
                    onClick={() => {
                      const business = businesses.find((item) => item.id === selectedId);
                      if (business) void toggleBusiness(business);
                    }}
                    type="button"
                  >
                    {businesses.find((item) => item.id === selectedId)?.is_active
                      ? (fa ? "غیرفعال‌کردن" : "Deactivate")
                      : (fa ? "فعال‌کردن" : "Activate")}
                  </button>
                ) : null}
              </div>
              <div className="data-table-wrap">
                <table className="data-table">
                  <thead><tr><th>{fa ? "نوع" : "Type"}</th><th>{fa ? "مبلغ" : "Amount"}</th><th>{fa ? "مدل/توضیح" : "Model / note"}</th><th>{fa ? "زمان" : "Time"}</th></tr></thead>
                  <tbody>
                    {transactions.length ? transactions.map((transaction) => (
                      <tr key={transaction.id}>
                        <td>{transaction.kind === "credit" ? (fa ? "شارژ" : "Credit") : (fa ? "مصرف AI" : "AI usage")}</td>
                        <td>{money(transaction.amount_rial)}</td>
                        <td>{transaction.model_id ?? transaction.note ?? "—"}</td>
                        <td>{new Intl.DateTimeFormat(fa ? "fa-IR" : "en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(transaction.created_at))}</td>
                      </tr>
                    )) : <tr><td colSpan={4}>{fa ? "هنوز تراکنشی ثبت نشده است." : "No transactions yet."}</td></tr>}
                  </tbody>
                </table>
              </div>
            </>
          ) : <div className="empty-panel">{fa ? "یک کسب‌وکار را انتخاب کنید." : "Select a business."}</div>}
        </div>
      </div>
    </section>
  );
}
