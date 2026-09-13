"use client";

import { useCallback, useEffect, useState } from "react";

import { findNavigationItem, type ModuleId } from "../config/navigation";
import {
  ApiError,
  getBusinessBillingSummary,
  getAnalyticsOverview,
  getCurrentUser,
  listTenants,
  login as apiLogin,
  logout as apiLogout,
  type AnalyticsOverview,
  type BusinessBillingSummary,
  type Tenant,
  type User,
} from "../lib/api";
import type { TranslationKey } from "../lib/i18n";
import { AppPreferences, usePreferences } from "./app-preferences";
import { AppShell } from "./app-shell";
import { BusinessAgentsView } from "./business-agents-view";
import { BusinessChannelsView } from "./business-channels-view";
import { BusinessKnowledgeView } from "./business-knowledge-view";
import { BusinessPoliciesView } from "./business-policies-view";
import { DashboardView } from "./dashboard-view";
import { LoginView } from "./login-view";
import { ModuleView } from "./module-view";
import { OwnerBusinessesView } from "./owner-businesses-view";
import { OwnerSettingsView } from "./owner-settings-view";
import { OwnerUsersView } from "./owner-users-view";

type SessionState = "checking" | "guest" | "authenticated";

function accessibleModule(module: ModuleId, isPlatformOwner: boolean): ModuleId {
  const ownerOnly = findNavigationItem(module).ownerOnly === true;
  if (isPlatformOwner) {
    return ownerOnly ? module : "businesses";
  }
  if (module === "analytics") return "dashboard";
  return ownerOnly ? "dashboard" : module;
}

function Workspace({ initialModule }: Readonly<{ initialModule: ModuleId }>) {
  const { t } = usePreferences();
  const [session, setSession] = useState<SessionState>("checking");
  const [user, setUser] = useState<User | null>(null);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [selectedTenantId, setSelectedTenantId] = useState("");
  const [activeModule, setActiveModule] = useState<ModuleId>(initialModule);
  const [days, setDays] = useState(30);
  const [overview, setOverview] = useState<AnalyticsOverview | null>(null);
  const [billingSummary, setBillingSummary] = useState<BusinessBillingSummary | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [analyticsError, setAnalyticsError] = useState<TranslationKey | null>(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState<TranslationKey | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);

  const applyTenants = useCallback((availableTenants: Tenant[]) => {
    setTenants(availableTenants);
    setOverview(null);
    setAnalyticsError(null);
    setAnalyticsLoading(availableTenants.length > 0);
    const savedTenantId = window.localStorage.getItem("cmh-tenant");
    const selected =
      availableTenants.find((tenant) => tenant.id === savedTenantId) ?? availableTenants[0];
    setSelectedTenantId(selected?.id ?? "");
  }, []);

  useEffect(() => {
    let cancelled = false;

    const restoreSession = async () => {
      try {
        const currentUser = await getCurrentUser();
        const availableTenants = currentUser.is_platform_owner ? [] : await listTenants();
        if (cancelled) return;
        setUser(currentUser);
        applyTenants(availableTenants);
        setActiveModule((module) => accessibleModule(module, currentUser.is_platform_owner));
        setSession("authenticated");
      } catch (error) {
        if (cancelled) return;
        setUser(null);
        setTenants([]);
        setSelectedTenantId("");
        setSession("guest");
        if (!(error instanceof ApiError && error.status === 401)) {
          setAuthError("auth.unavailable");
        }
      }
    };

    void restoreSession();
    return () => {
      cancelled = true;
    };
  }, [applyTenants]);

  useEffect(() => {
    if (session !== "authenticated" || user?.is_platform_owner || selectedTenantId === "") {
      return;
    }

    let cancelled = false;

    void getAnalyticsOverview(selectedTenantId, days)
      .then((result) => {
        if (!cancelled) setOverview(result);
      })
      .catch(() => {
        if (!cancelled) {
          setOverview(null);
          setAnalyticsError("dashboard.loadError");
        }
      })
      .finally(() => {
        if (!cancelled) setAnalyticsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [days, refreshVersion, selectedTenantId, session, user?.is_platform_owner]);

  useEffect(() => {
    if (session !== "authenticated" || user?.is_platform_owner || selectedTenantId === "") {
      return;
    }
    let cancelled = false;
    void getBusinessBillingSummary(selectedTenantId)
      .then((result) => {
        if (!cancelled) setBillingSummary(result);
      })
      .catch(() => {
        if (!cancelled) setBillingSummary(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedTenantId, session, user?.is_platform_owner]);

  const signIn = async (email: string, password: string) => {
    setAuthBusy(true);
    setAuthError(null);
    try {
      const signedInUser = await apiLogin(email, password);
      const availableTenants = signedInUser.is_platform_owner ? [] : await listTenants();
      setUser(signedInUser);
      applyTenants(availableTenants);
      setActiveModule((module) => accessibleModule(module, signedInUser.is_platform_owner));
      setSession("authenticated");
    } catch (error) {
      setAuthError(
        error instanceof ApiError && error.status === 401 ? "auth.invalid" : "auth.unavailable",
      );
    } finally {
      setAuthBusy(false);
    }
  };

  const signOut = async () => {
    try {
      await apiLogout();
    } finally {
      setSession("guest");
      setUser(null);
      setTenants([]);
      setSelectedTenantId("");
      setOverview(null);
      setBillingSummary(null);
      setActiveModule("dashboard");
    }
  };

  const changeTenant = (tenantId: string) => {
    window.localStorage.setItem("cmh-tenant", tenantId);
    setOverview(null);
    setBillingSummary(null);
    setAnalyticsError(null);
    setAnalyticsLoading(true);
    setSelectedTenantId(tenantId);
  };

  const changeDays = (nextDays: number) => {
    setAnalyticsError(null);
    setAnalyticsLoading(true);
    setDays(nextDays);
  };

  const refreshAnalytics = () => {
    setAnalyticsError(null);
    setAnalyticsLoading(true);
    setRefreshVersion((value) => value + 1);
  };

  if (session === "checking") {
    return (
      <main className="startup-screen">
        <span className="brand-mark brand-mark--large">CM</span>
        <span className="spinner spinner--large" aria-hidden="true" />
        <p>{t("common.loading")}</p>
      </main>
    );
  }

  if (session === "guest" || user === null) {
    return (
      <LoginView
        busy={authBusy}
        error={authError === null ? null : t(authError)}
        onSubmit={signIn}
      />
    );
  }

  const visibleActiveModule = accessibleModule(activeModule, user.is_platform_owner);
  let content;
  if (visibleActiveModule === "businesses" && user.is_platform_owner) {
    content = <OwnerBusinessesView />;
  } else if (visibleActiveModule === "users" && user.is_platform_owner) {
    content = <OwnerUsersView />;
  } else if (visibleActiveModule === "settings" && user.is_platform_owner) {
    content = <OwnerSettingsView />;
  } else if (selectedTenantId === "") {
    content = (
      <section className="module-page">
        <div className="module-card">
          <span className="status-pill status-pill--quiet">{t("status.foundation")}</span>
          <h2>{t("dashboard.noTenant")}</h2>
          <p>{t("dashboard.noTenantHelp")}</p>
        </div>
      </section>
    );
  } else if (visibleActiveModule === "agents") {
    content = <BusinessAgentsView tenantId={selectedTenantId} />;
  } else if (visibleActiveModule === "channels") {
    content = <BusinessChannelsView tenantId={selectedTenantId} />;
  } else if (visibleActiveModule === "knowledge") {
    content = <BusinessKnowledgeView tenantId={selectedTenantId} />;
  } else if (visibleActiveModule === "policies") {
    content = <BusinessPoliciesView tenantId={selectedTenantId} />;
  } else if (visibleActiveModule === "dashboard" || visibleActiveModule === "analytics") {
    content = (
      <DashboardView
        days={days}
        error={analyticsError === null ? null : t(analyticsError)}
        loading={analyticsLoading}
        onDaysChange={changeDays}
        onRefresh={refreshAnalytics}
        overview={overview}
        variant={visibleActiveModule}
      />
    );
  } else {
    content = <ModuleView module={visibleActiveModule} onBack={() => setActiveModule("dashboard")} />;
  }

  return (
    <AppShell
      activeModule={visibleActiveModule}
      billingSummary={billingSummary}
      onModuleChange={setActiveModule}
      onSignOut={signOut}
      onTenantChange={changeTenant}
      selectedTenantId={selectedTenantId}
      tenants={tenants}
      user={user}
    >
      {content}
    </AppShell>
  );
}

export function DashboardApp({
  initialModule = "dashboard",
}: Readonly<{ initialModule?: ModuleId }>) {
  return (
    <AppPreferences>
      <Workspace initialModule={initialModule} />
    </AppPreferences>
  );
}
