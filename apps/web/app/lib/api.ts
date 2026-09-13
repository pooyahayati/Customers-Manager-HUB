export interface User {
  id: string;
  email: string;
  is_platform_owner: boolean;
}

export type TenantRole = "owner" | "admin" | "supervisor" | "agent" | "analyst" | "viewer";
export type BusinessRole = "admin" | "supervisor" | "agent" | "viewer";
export type AIProvider = "openai" | "gemini";
export type AITaskType =
  | "customer_response"
  | "voice_transcription"
  | "intent_classification"
  | "conversation_summary"
  | "customer_memory_extraction"
  | "embedding";
export type BillingDisplayUnit = "rial" | "toman";

export interface Tenant {
  id: string;
  slug: string;
  name: string;
  role: TenantRole;
}

export interface Business {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  user_count: number;
}

export interface BusinessUser {
  id: string;
  email: string;
  role: BusinessRole;
  is_active: boolean;
  created_at: string;
}

export interface ProviderStatus {
  provider: AIProvider;
  configured: boolean;
  source: "database" | "environment" | "none";
  last_tested_at: string | null;
  last_test_succeeded: boolean | null;
  last_error_code: string | null;
}

export interface ProviderConnectionTest {
  provider: AIProvider;
  success: boolean;
  model_count: number;
  checked_at: string;
}

export interface ProviderModel {
  id: string;
  display_name: string;
  capabilities: string[];
}

export interface PlatformTaskProfile {
  task_type: AITaskType;
  timeout_seconds: number;
  attempts_per_route: number;
  routes: Array<{
    provider: AIProvider;
    model_id: string;
    priority: number;
    parameters: Record<string, unknown>;
  }>;
}

export interface ModelPrice {
  id: string;
  provider: AIProvider;
  model_id: string;
  input_per_million_rial: number;
  output_per_million_rial: number;
  audio_per_minute_rial: number;
  effective_from: string;
}

export interface Wallet {
  business_id: string;
  balance_rial: number;
  updated_at: string;
}

export interface WalletTransaction {
  id: string;
  kind: "credit" | "ai_debit";
  amount_rial: number;
  balance_after_rial: number;
  message_id: string | null;
  provider: string | null;
  model_id: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  total_tokens: number | null;
  audio_seconds: number | null;
  note: string | null;
  created_at: string;
}

export interface BusinessBillingSummary {
  balance_rial: number;
  display_unit: BillingDisplayUnit;
}

export type PromptVersionStatus = "draft" | "published" | "archived";
export type ChannelCapability = "text" | "voice" | "outbound_text";
export type KnowledgeSourceStatus = "queued" | "processing" | "ready" | "failed";
export type PolicyAutonomyMode = "autonomous" | "assist_only" | "human_only";
export type OutsideBusinessHoursAction = "allow" | "handoff" | "human_only";
export type ToolRiskLevel = "low" | "medium" | "high" | "critical";

export interface AgentPromptVersion {
  id: string;
  version: number;
  status: PromptVersionStatus;
  content: string;
  published_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentPrompt {
  id: string;
  name: string;
  created_at: string;
  updated_at: string;
  versions: AgentPromptVersion[];
}

export interface BusinessAgent {
  id: string;
  name: string;
  description: string | null;
  prompt_id: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ChannelAccount {
  id: string;
  channel_type: "telegram" | "website";
  name: string;
  external_account_id: string;
  external_username: string | null;
  is_active: boolean;
  supported_capabilities: ChannelCapability[];
  enabled_inbound_types: ChannelCapability[];
  credentials_configured: boolean;
}

export interface AgentChannelAssignment {
  id: string;
  agent_id: string;
  channel_account_id: string;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeBase {
  id: string;
  name: string;
  description: string | null;
  is_active: boolean;
}

export interface KnowledgeSource {
  id: string;
  knowledge_base_id: string;
  filename: string;
  media_type: string;
  sha256: string;
  size_bytes: number;
  status: KnowledgeSourceStatus;
  error_code: string | null;
  chunk_count: number;
  embedding_provider: string | null;
  embedding_model_id: string | null;
  embedding_dimension: number | null;
}

export interface AgentKnowledgePermission {
  id: string;
  agent_id: string;
  knowledge_base_id: string;
}

export interface TenantPolicy {
  enabled: boolean;
  revision: number;
  timezone: string;
  business_hours: Record<string, unknown>;
  outside_business_hours_action: OutsideBusinessHoursAction;
  autonomy_mode: PolicyAutonomyMode;
  message_rules: Record<string, unknown>;
  handoff_keywords: string[];
  handoff_on_tool_approval: boolean;
  approval_min_risk: ToolRiskLevel;
  require_approval_for_writes: boolean;
  updated_at: string | null;
}

export interface OperationalOverview {
  conversations: number;
  unique_contacts: number;
  first_response_average_seconds: number | null;
  first_response_samples: number;
  resolution_average_seconds: number | null;
  resolution_samples: number;
  handoff_rate: number | null;
  handoff_conversations: number;
  handoff_average_seconds: number | null;
  handoff_duration_samples: number;
  ai_automation_rate: number | null;
  automated_conversations: number;
  active_conversations: number;
}

export interface AIUsageOverview {
  requests: number;
  succeeded: number;
  failed: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  audio_seconds: number;
  estimated_cost_usd: string;
  unpriced_requests: number;
}

export interface ToolUsageOverview {
  executions: number;
  succeeded: number;
  failed: number;
  denied: number;
  approval_required: number;
  success_rate: number | null;
}

export interface AnalyticsOverview {
  start: string;
  end: string;
  operations: OperationalOverview;
  ai: AIUsageOverview;
  tools: ToolUsageOverview;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!(init?.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = payload.detail ?? detail;
    } catch {
      // The status code remains the reliable error contract for non-JSON failures.
    }
    throw new ApiError(detail || "Request failed", response.status);
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function getCurrentUser(): Promise<User> {
  return request<User>("/api/v1/auth/me", { cache: "no-store" });
}

export function login(email: string, password: string): Promise<User> {
  return request<User>("/api/v1/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
}

export function logout(): Promise<void> {
  return request<void>("/api/v1/auth/logout", { method: "POST" });
}

export function listTenants(): Promise<Tenant[]> {
  return request<Tenant[]>("/api/v1/tenants", { cache: "no-store" });
}

export function getAnalyticsOverview(tenantId: string, days: number): Promise<AnalyticsOverview> {
  const end = new Date();
  const start = new Date(end.getTime() - days * 24 * 60 * 60 * 1000);
  const query = new URLSearchParams({ start: start.toISOString(), end: end.toISOString() });
  return request<AnalyticsOverview>(
    `/api/v1/tenants/${encodeURIComponent(tenantId)}/analytics/overview?${query.toString()}`,
    { cache: "no-store" },
  );
}

export function listBusinesses(): Promise<Business[]> {
  return request<Business[]>("/api/v1/platform/businesses", { cache: "no-store" });
}

export function createBusiness(name: string, slug: string): Promise<Business> {
  return request<Business>("/api/v1/platform/businesses", {
    method: "POST",
    body: JSON.stringify({ name, slug }),
  });
}

export function setBusinessActive(businessId: string, isActive: boolean): Promise<Business> {
  return request<Business>(`/api/v1/platform/businesses/${encodeURIComponent(businessId)}`, {
    method: "PATCH",
    body: JSON.stringify({ is_active: isActive }),
  });
}

export function listBusinessUsers(businessId: string): Promise<BusinessUser[]> {
  return request<BusinessUser[]>(
    `/api/v1/platform/businesses/${encodeURIComponent(businessId)}/users`,
    { cache: "no-store" },
  );
}

export function createBusinessUser(
  businessId: string,
  email: string,
  password: string,
  role: BusinessRole,
): Promise<BusinessUser> {
  return request<BusinessUser>(
    `/api/v1/platform/businesses/${encodeURIComponent(businessId)}/users`,
    { method: "POST", body: JSON.stringify({ email, password, role }) },
  );
}

export function setBusinessUserActive(
  businessId: string,
  userId: string,
  isActive: boolean,
): Promise<BusinessUser> {
  return request<BusinessUser>(
    `/api/v1/platform/businesses/${encodeURIComponent(businessId)}/users/${encodeURIComponent(userId)}`,
    { method: "PATCH", body: JSON.stringify({ is_active: isActive }) },
  );
}

export function listProviderStatuses(): Promise<ProviderStatus[]> {
  return request<ProviderStatus[]>("/api/v1/platform/ai/providers", { cache: "no-store" });
}

export function saveProviderCredential(
  provider: AIProvider,
  apiKey: string,
): Promise<ProviderStatus> {
  return request<ProviderStatus>(`/api/v1/platform/ai/providers/${provider}`, {
    method: "PUT",
    body: JSON.stringify({ api_key: apiKey }),
  });
}

export function testProviderConnection(provider: AIProvider): Promise<ProviderConnectionTest> {
  return request<ProviderConnectionTest>(`/api/v1/platform/ai/providers/${provider}/test`, {
    method: "POST",
  });
}

export function listProviderModels(
  provider: AIProvider,
  taskType?: AITaskType,
): Promise<ProviderModel[]> {
  const query = taskType ? `?task_type=${encodeURIComponent(taskType)}` : "";
  return request<ProviderModel[]>(`/api/v1/platform/ai/providers/${provider}/models${query}`, {
    cache: "no-store",
  });
}

export function listPlatformTaskProfiles(): Promise<PlatformTaskProfile[]> {
  return request<PlatformTaskProfile[]>("/api/v1/platform/ai/task-profiles", {
    cache: "no-store",
  });
}

export function savePlatformTaskProfile(
  taskType: AITaskType,
  provider: AIProvider,
  modelId: string,
): Promise<PlatformTaskProfile> {
  return request<PlatformTaskProfile>(`/api/v1/platform/ai/task-profiles/${taskType}`, {
    method: "PUT",
    body: JSON.stringify({
      timeout_seconds: 30,
      attempts_per_route: 1,
      routes: [{ provider, model_id: modelId, parameters: {} }],
    }),
  });
}

export function getBillingDisplayUnit(): Promise<{ display_unit: BillingDisplayUnit }> {
  return request<{ display_unit: BillingDisplayUnit }>("/api/v1/platform/billing/settings", {
    cache: "no-store",
  });
}

export function setBillingDisplayUnit(
  displayUnit: BillingDisplayUnit,
): Promise<{ display_unit: BillingDisplayUnit }> {
  return request<{ display_unit: BillingDisplayUnit }>("/api/v1/platform/billing/settings", {
    method: "PUT",
    body: JSON.stringify({ display_unit: displayUnit }),
  });
}

export function listModelPricing(): Promise<ModelPrice[]> {
  return request<ModelPrice[]>("/api/v1/platform/billing/pricing", { cache: "no-store" });
}

export function saveModelPricing(payload: Omit<ModelPrice, "id" | "effective_from">): Promise<ModelPrice> {
  return request<ModelPrice>("/api/v1/platform/billing/pricing", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function getBusinessWallet(businessId: string): Promise<Wallet> {
  return request<Wallet>(
    `/api/v1/platform/billing/businesses/${encodeURIComponent(businessId)}/wallet`,
    { cache: "no-store" },
  );
}

export function creditBusinessWallet(
  businessId: string,
  amountRial: number,
  note: string,
): Promise<WalletTransaction> {
  return request<WalletTransaction>(
    `/api/v1/platform/billing/businesses/${encodeURIComponent(businessId)}/wallet/credit`,
    { method: "POST", body: JSON.stringify({ amount_rial: amountRial, note: note || null }) },
  );
}

export function listBusinessWalletTransactions(businessId: string): Promise<WalletTransaction[]> {
  return request<WalletTransaction[]>(
    `/api/v1/platform/billing/businesses/${encodeURIComponent(businessId)}/wallet/transactions`,
    { cache: "no-store" },
  );
}

export function getBusinessBillingSummary(tenantId: string): Promise<BusinessBillingSummary> {
  return request<BusinessBillingSummary>(
    `/api/v1/tenants/${encodeURIComponent(tenantId)}/billing/summary`,
    { cache: "no-store" },
  );
}

export function listAgentPrompts(tenantId: string): Promise<AgentPrompt[]> {
  return request<AgentPrompt[]>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/prompts`, { cache: "no-store" });
}

export function createAgentPrompt(tenantId: string, name: string, content: string): Promise<AgentPrompt> {
  return request<AgentPrompt>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/prompts`, {
    method: "POST", body: JSON.stringify({ name, content }),
  });
}

export function saveAgentPromptDraft(tenantId: string, promptId: string, content: string): Promise<AgentPrompt> {
  return request<AgentPrompt>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/prompts/${encodeURIComponent(promptId)}/draft`, {
    method: "PUT", body: JSON.stringify({ content }),
  });
}

export function publishAgentPrompt(tenantId: string, promptId: string): Promise<AgentPrompt> {
  return request<AgentPrompt>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/prompts/${encodeURIComponent(promptId)}/publish`, { method: "POST" });
}

export function listBusinessAgents(tenantId: string): Promise<BusinessAgent[]> {
  return request<BusinessAgent[]>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/agents`, { cache: "no-store" });
}

export function createBusinessAgent(tenantId: string, name: string, promptId: string, description: string): Promise<BusinessAgent> {
  return request<BusinessAgent>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/agents`, {
    method: "POST", body: JSON.stringify({ name, prompt_id: promptId, description: description || null }),
  });
}

export function updateBusinessAgent(tenantId: string, agentId: string, payload: Partial<Pick<BusinessAgent, "name" | "prompt_id" | "description" | "is_active">>): Promise<BusinessAgent> {
  return request<BusinessAgent>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/agents/${encodeURIComponent(agentId)}`, {
    method: "PATCH", body: JSON.stringify(payload),
  });
}

export function listChannelAccounts(tenantId: string): Promise<ChannelAccount[]> {
  return request<ChannelAccount[]>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/channels`, { cache: "no-store" });
}

export function createTelegramChannel(tenantId: string, name: string, botToken: string, inbound: ChannelCapability[]): Promise<ChannelAccount> {
  return request<ChannelAccount>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/channels/telegram`, {
    method: "POST", body: JSON.stringify({ name, bot_token: botToken, enabled_inbound_types: inbound }),
  });
}

export function updateChannelAccount(tenantId: string, channelId: string, payload: { is_active?: boolean; enabled_inbound_types?: ChannelCapability[] }): Promise<ChannelAccount> {
  return request<ChannelAccount>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/channels/${encodeURIComponent(channelId)}`, {
    method: "PATCH", body: JSON.stringify(payload),
  });
}

export function registerTelegramWebhook(tenantId: string, channelId: string): Promise<{ registered: boolean; url: string }> {
  return request<{ registered: boolean; url: string }>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/channels/${encodeURIComponent(channelId)}/register-webhook`, { method: "POST" });
}

export function getChannelAgentAssignment(tenantId: string, channelId: string): Promise<AgentChannelAssignment> {
  return request<AgentChannelAssignment>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/agents/assignments/channels/${encodeURIComponent(channelId)}`, { cache: "no-store" });
}

export function setChannelAgentAssignment(tenantId: string, channelId: string, agentId: string): Promise<AgentChannelAssignment> {
  return request<AgentChannelAssignment>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/agents/assignments/channels/${encodeURIComponent(channelId)}`, {
    method: "PUT", body: JSON.stringify({ agent_id: agentId }),
  });
}

export function listKnowledgeBases(tenantId: string): Promise<KnowledgeBase[]> {
  return request<KnowledgeBase[]>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/knowledge-bases`, { cache: "no-store" });
}

export function createKnowledgeBase(tenantId: string, name: string, description: string): Promise<KnowledgeBase> {
  return request<KnowledgeBase>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/knowledge-bases`, {
    method: "POST", body: JSON.stringify({ name, description: description || null }),
  });
}

export function listKnowledgeSources(tenantId: string, baseId: string): Promise<KnowledgeSource[]> {
  return request<KnowledgeSource[]>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/knowledge-bases/${encodeURIComponent(baseId)}/sources`, { cache: "no-store" });
}

export async function uploadKnowledgeSource(tenantId: string, baseId: string, file: File): Promise<KnowledgeSource> {
  const body = new FormData();
  body.append("file", file);
  return request<KnowledgeSource>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/knowledge-bases/${encodeURIComponent(baseId)}/sources`, {
    method: "POST", body,
  });
}

export function getTenantPolicy(tenantId: string): Promise<TenantPolicy> {
  return request<TenantPolicy>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/policies`, { cache: "no-store" });
}

export function listAgentKnowledgePermissions(tenantId: string, agentId: string): Promise<AgentKnowledgePermission[]> {
  return request<AgentKnowledgePermission[]>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/agents/${encodeURIComponent(agentId)}/knowledge-bases`, { cache: "no-store" });
}

export function grantAgentKnowledgePermission(tenantId: string, agentId: string, baseId: string): Promise<AgentKnowledgePermission> {
  return request<AgentKnowledgePermission>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/agents/${encodeURIComponent(agentId)}/knowledge-bases/${encodeURIComponent(baseId)}`, { method: "PUT" });
}

export function revokeAgentKnowledgePermission(tenantId: string, agentId: string, baseId: string): Promise<void> {
  return request<void>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/agents/${encodeURIComponent(agentId)}/knowledge-bases/${encodeURIComponent(baseId)}`, { method: "DELETE" });
}

export function saveTenantPolicy(tenantId: string, policy: Omit<TenantPolicy, "revision" | "updated_at">): Promise<TenantPolicy> {
  return request<TenantPolicy>(`/api/v1/tenants/${encodeURIComponent(tenantId)}/policies`, {
    method: "PUT", body: JSON.stringify(policy),
  });
}
