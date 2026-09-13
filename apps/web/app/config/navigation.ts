import type { IconName } from "../components/icons";
import type { TranslationKey } from "../lib/i18n";

export type ModuleId =
  | "dashboard"
  | "inbox"
  | "contacts"
  | "conversations"
  | "agents"
  | "channels"
  | "knowledge"
  | "tools"
  | "policies"
  | "analytics"
  | "businesses"
  | "users"
  | "settings";

export interface NavigationItem {
  id: ModuleId;
  label: TranslationKey;
  icon: IconName;
  apiScope: string;
  ownerOnly?: boolean;
}

export interface NavigationGroup {
  label: TranslationKey;
  items: NavigationItem[];
}

export const navigation: NavigationGroup[] = [
  {
    label: "nav.platform",
    items: [
      {
        id: "businesses",
        label: "nav.businesses",
        icon: "dashboard",
        apiScope: "/platform/businesses",
        ownerOnly: true,
      },
      {
        id: "users",
        label: "nav.users",
        icon: "contacts",
        apiScope: "/platform/businesses/:business_id/users",
        ownerOnly: true,
      },
    ],
  },
  {
    label: "nav.workspace",
    items: [
      { id: "dashboard", label: "nav.dashboard", icon: "dashboard", apiScope: "/analytics/overview" },
      { id: "inbox", label: "nav.inbox", icon: "inbox", apiScope: "/handoffs" },
      { id: "contacts", label: "nav.contacts", icon: "contacts", apiScope: "/contacts" },
      { id: "conversations", label: "nav.conversations", icon: "conversations", apiScope: "/conversations" },
    ],
  },
  {
    label: "nav.automation",
    items: [
      { id: "agents", label: "nav.agents", icon: "agents", apiScope: "/agents" },
      { id: "channels", label: "nav.channels", icon: "channels", apiScope: "/channels" },
      { id: "knowledge", label: "nav.knowledge", icon: "knowledge", apiScope: "/knowledge-sources" },
      { id: "tools", label: "nav.tools", icon: "tools", apiScope: "/tools" },
      { id: "policies", label: "nav.policies", icon: "policies", apiScope: "/policies" },
    ],
  },
  {
    label: "nav.management",
    items: [
      { id: "analytics", label: "nav.analytics", icon: "analytics", apiScope: "/analytics" },
      {
        id: "settings",
        label: "nav.settings",
        icon: "settings",
        apiScope: "/platform/ai",
        ownerOnly: true,
      },
    ],
  },
];

export function findNavigationItem(id: ModuleId): NavigationItem {
  const item = navigation.flatMap((group) => group.items).find((candidate) => candidate.id === id);
  if (item === undefined) {
    throw new Error(`Unknown navigation item: ${id}`);
  }
  return item;
}
