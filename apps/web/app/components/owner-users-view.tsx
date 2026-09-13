"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";

import {
  ApiError,
  createBusinessUser,
  listBusinesses,
  listBusinessUsers,
  setBusinessUserActive,
  type Business,
  type BusinessRole,
  type BusinessUser,
} from "../lib/api";
import { usePreferences } from "./app-preferences";

const roles: BusinessRole[] = ["admin", "supervisor", "agent", "viewer"];

export function OwnerUsersView() {
  const { locale } = usePreferences();
  const fa = locale === "fa";
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [users, setUsers] = useState<BusinessUser[]>([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<BusinessRole>("agent");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadUsers = useCallback(async (businessId: string) => {
    setUsers(businessId ? await listBusinessUsers(businessId) : []);
  }, []);

  useEffect(() => {
    void listBusinesses()
      .then((rows) => {
        setBusinesses(rows);
        setSelectedId(rows[0]?.id ?? "");
      })
      .catch((requestError) => setError(requestError instanceof ApiError ? requestError.message : "Request failed"));
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    void listBusinessUsers(selectedId)
      .then((rows) => {
        if (!cancelled) setUsers(rows);
      })
      .catch((requestError) => {
        if (!cancelled) {
          setError(requestError instanceof ApiError ? requestError.message : "Request failed");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedId) return;
    setBusy(true);
    setError(null);
    try {
      await createBusinessUser(selectedId, email, password, role);
      setEmail("");
      setPassword("");
      await loadUsers(selectedId);
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (user: BusinessUser) => {
    setBusy(true);
    setError(null);
    try {
      await setBusinessUserActive(selectedId, user.id, !user.is_active);
      await loadUsers(selectedId);
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  const roleLabel = (value: BusinessRole) => {
    const labels: Record<BusinessRole, [string, string]> = {
      admin: ["Admin", "مدیر"],
      supervisor: ["Supervisor", "سرپرست"],
      agent: ["Agent", "کارشناس"],
      viewer: ["Viewer", "مشاهده‌گر"],
    };
    return labels[value][fa ? 1 : 0];
  };

  return (
    <section className="admin-page">
      <header className="admin-heading">
        <div>
          <span className="section-eyebrow">{fa ? "مدیریت مالک سامانه" : "Platform owner"}</span>
          <h2>{fa ? "کاربران کسب‌وکار" : "Business users"}</h2>
          <p>{fa ? "ثبت‌نام عمومی بسته است؛ شما حساب‌ها و سطح دسترسی را مدیریت می‌کنید." : "Public signup stays closed; you manage accounts and access levels."}</p>
        </div>
        <label className="admin-business-select">
          <span>{fa ? "کسب‌وکار" : "Business"}</span>
          <select onChange={(event) => setSelectedId(event.target.value)} value={selectedId}>
            {businesses.map((business) => <option key={business.id} value={business.id}>{business.name}</option>)}
          </select>
        </label>
      </header>

      {error ? <div className="notice notice--error">{error}</div> : null}

      <div className="admin-grid admin-grid--users">
        <div className="admin-card">
          <h3>{fa ? "افزودن کاربر" : "Add a user"}</h3>
          <form className="stack-form" onSubmit={submit}>
            <label>
              <span>{fa ? "ایمیل" : "Email"}</span>
              <input dir="ltr" onChange={(event) => setEmail(event.target.value)} required type="email" value={email} />
            </label>
            <label>
              <span>{fa ? "رمز عبور (حداقل ۱۲ نویسه)" : "Password (12+ characters)"}</span>
              <input dir="ltr" minLength={12} onChange={(event) => setPassword(event.target.value)} required type="password" value={password} />
            </label>
            <label>
              <span>{fa ? "سطح دسترسی" : "Access role"}</span>
              <select onChange={(event) => setRole(event.target.value as BusinessRole)} value={role}>
                {roles.map((item) => <option key={item} value={item}>{roleLabel(item)}</option>)}
              </select>
            </label>
            <button className="button button--primary" disabled={busy || !selectedId} type="submit">
              {fa ? "ساخت حساب" : "Create account"}
            </button>
          </form>
        </div>

        <div className="admin-card admin-card--wide">
          <div className="card-heading">
            <h3>{fa ? "فهرست کاربران" : "User directory"}</h3>
            <span className="status-pill status-pill--quiet">{users.length}</span>
          </div>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead><tr><th>{fa ? "کاربر" : "User"}</th><th>{fa ? "نقش" : "Role"}</th><th>{fa ? "وضعیت" : "Status"}</th><th /></tr></thead>
              <tbody>
                {users.length ? users.map((user) => (
                  <tr key={user.id}>
                    <td dir="ltr">{user.email}</td>
                    <td>{roleLabel(user.role)}</td>
                    <td><span className={`status-pill ${user.is_active ? "status-pill--success" : "status-pill--quiet"}`}>{user.is_active ? (fa ? "فعال" : "Active") : (fa ? "غیرفعال" : "Inactive")}</span></td>
                    <td><button className="button button--secondary button--small" disabled={busy} onClick={() => void toggle(user)} type="button">{user.is_active ? (fa ? "غیرفعال" : "Disable") : (fa ? "فعال" : "Enable")}</button></td>
                  </tr>
                )) : <tr><td colSpan={4}>{fa ? "هنوز کاربری برای این کسب‌وکار ساخته نشده است." : "No users have been added to this business."}</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>
  );
}
