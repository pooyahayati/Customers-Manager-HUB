"use client";

import { type ReactNode, useState } from "react";

import { findNavigationItem, navigation, type ModuleId } from "../config/navigation";
import type { BusinessBillingSummary, Tenant, User } from "../lib/api";
import { usePreferences } from "./app-preferences";
import { Icon } from "./icons";
import { PreferenceControls } from "./preference-controls";

interface AppShellProps {
  activeModule: ModuleId;
  billingSummary: BusinessBillingSummary | null;
  children: ReactNode;
  onModuleChange: (module: ModuleId) => void;
  onSignOut: () => Promise<void>;
  onTenantChange: (tenantId: string) => void;
  selectedTenantId: string;
  tenants: Tenant[];
  user: User;
}

export function AppShell({
  activeModule,
  billingSummary,
  children,
  onModuleChange,
  onSignOut,
  onTenantChange,
  selectedTenantId,
  tenants,
  user,
}: Readonly<AppShellProps>) {
  const { locale, t } = usePreferences();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const activeItem = findNavigationItem(activeModule);
  const selectedTenant = tenants.find((tenant) => tenant.id === selectedTenantId);
  const visibleNavigation = navigation
    .map((group) => ({
      ...group,
      items: group.items.filter((item) =>
        user.is_platform_owner
          ? item.ownerOnly === true
          : item.ownerOnly !== true && item.id !== "analytics",
      ),
    }))
    .filter((group) => group.items.length > 0);
  const visibleBalance = billingSummary
    ? new Intl.NumberFormat(locale === "fa" ? "fa-IR" : "en-US").format(
        billingSummary.display_unit === "toman"
          ? billingSummary.balance_rial / 10
          : billingSummary.balance_rial,
      )
    : null;

  const selectModule = (module: ModuleId) => {
    onModuleChange(module);
    setMobileOpen(false);
  };

  return (
    <div className={`app-frame${collapsed ? " app-frame--collapsed" : ""}`}>
      {mobileOpen ? (
        <button
          aria-label={t("nav.close")}
          className="sidebar-backdrop"
          onClick={() => setMobileOpen(false)}
          type="button"
        />
      ) : null}

      <aside className={`sidebar${mobileOpen ? " sidebar--mobile-open" : ""}`}>
        <div className="sidebar-brand">
          <span className="brand-mark">CM</span>
          <span className="sidebar-brand-copy">
            <strong>{t("app.name")}</strong>
            <small>{t("app.shortName")}</small>
          </span>
        </div>

        <nav className="sidebar-nav" aria-label="Primary navigation">
          {visibleNavigation.map((group) => (
            <div className="nav-group" key={group.label}>
              <p className="nav-group-label">{t(group.label)}</p>
              {group.items.map((item) => (
                <button
                  aria-current={activeModule === item.id ? "page" : undefined}
                  className={`nav-item${activeModule === item.id ? " is-active" : ""}`}
                  key={item.id}
                  onClick={() => selectModule(item.id)}
                  title={collapsed ? t(item.label) : undefined}
                  type="button"
                >
                  <Icon name={item.icon} size={19} />
                  <span>{t(item.label)}</span>
                </button>
              ))}
            </div>
          ))}
        </nav>

        <button
          aria-label={collapsed ? t("nav.expand") : t("nav.collapse")}
          className="sidebar-collapse"
          onClick={() => setCollapsed((value) => !value)}
          type="button"
        >
          <Icon className="rtl-mirror" name="chevron" size={17} />
          <span>{collapsed ? t("nav.expand") : t("nav.collapse")}</span>
        </button>
      </aside>

      <div className="app-content">
        <header className="topbar">
          <div className="topbar-heading">
            <button
              aria-label={t("nav.open")}
              className="icon-button mobile-menu-button"
              onClick={() => setMobileOpen(true)}
              type="button"
            >
              <Icon name="menu" size={20} />
            </button>
            <div>
              <span className="topbar-context">
                {user.is_platform_owner ? t("nav.platform") : t("header.tenant")}
              </span>
              <h1>{t(activeItem.label)}</h1>
            </div>
          </div>

          <div className="topbar-actions">
            {!user.is_platform_owner && visibleBalance !== null ? (
              <span className="wallet-balance">
                <small>{locale === "fa" ? "اعتبار" : "Balance"}</small>
                <strong>
                  {visibleBalance} {billingSummary?.display_unit === "toman"
                    ? locale === "fa" ? "تومان" : "Toman"
                    : locale === "fa" ? "ریال" : "Rial"}
                </strong>
              </span>
            ) : null}
            {!user.is_platform_owner && tenants.length > 0 ? (
              <label className="tenant-select">
                <span className="sr-only">{t("header.tenant")}</span>
                <select onChange={(event) => onTenantChange(event.target.value)} value={selectedTenantId}>
                  {tenants.map((tenant) => (
                    <option key={tenant.id} value={tenant.id}>
                      {tenant.name}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <PreferenceControls compact />
            <div className="profile">
              <span className="profile-avatar">{user.email.slice(0, 1).toUpperCase()}</span>
              <span className="profile-copy">
                <strong>{user.email}</strong>
                <small>
                  {user.is_platform_owner
                    ? t("role.platformOwner")
                    : selectedTenant
                      ? t(`role.${selectedTenant.role}`)
                      : t("common.notAvailable")}
                </small>
              </span>
              <button
                aria-label={t("header.signOut")}
                className="icon-button profile-signout"
                onClick={() => void onSignOut()}
                title={t("header.signOut")}
                type="button"
              >
                <Icon className="rtl-mirror" name="signout" size={18} />
              </button>
            </div>
          </div>
        </header>

        <main className="main-content">{children}</main>
      </div>
    </div>
  );
}
