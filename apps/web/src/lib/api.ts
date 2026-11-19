import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { userManager } from "./auth";

export const API = (import.meta.env.VITE_API_URL ?? "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function token(): Promise<string | null> {
  const u = await userManager.getUser();
  if (u && !u.expired) return u.access_token;
  try {
    return (await userManager.signinSilent())?.access_token ?? null;
  } catch {
    return null;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const t = await token();
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(t ? { Authorization: `Bearer ${t}` } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

// ---------- Types (mirror apps/api/src/assistant/api/routes/*.py) ----------

export interface Overview {
  generated_at: string;
  conversations_7d: number;
  conversations_prev_7d: number;
  leads_30d: Record<string, number>;
  qualified_rate_30d: number | null;
  booking_conversion_30d: number | null;
  qualified_leads_30d: number;
  escalation_rate_30d: number | null;
  conversations_30d: number;
  unanswered_30d: number;
  bookings_upcoming: number;
  bookings_next_7d: number;
  handovers_open: Record<string, number>;
  tickets_open: number;
  guardrail_replacements_7d: number;
  llm_tokens_today: number;
  llm_calls_today: number;
  degraded_replies_7d: number;
  daily_conversations: { date: string; count: number }[];
}

export interface ConversationRow {
  id: string;
  channel: string;
  status: string;
  verified: boolean;
  created_at: string;
  updated_at: string;
  contact_name: string | null;
  contact_email: string | null;
  company: string | null;
  lead_status: string | null;
  messages: number;
  last_message: string | null;
}

export interface MessageOut {
  id: number;
  role: "user" | "assistant" | "tool" | "system";
  content: string | null;
  tool_name: string | null;
  tool_calls: { id: string; name: string; arguments: Record<string, unknown> }[] | null;
  provider: string | null;
  created_at: string;
}

export interface ConversationDetail {
  id: string;
  channel: string;
  status: string;
  verified: boolean;
  created_at: string;
  updated_at: string;
  messages: MessageOut[];
}

export interface LeadRow {
  id: string;
  conversation_id: string | null;
  status: string;
  name: string | null;
  email: string | null;
  company: string | null;
  need: string | null;
  need_category: string | null;
  company_size: string | null;
  budget_range: string | null;
  timeline: string | null;
  role: string | null;
  summary: string | null;
  created_at: string;
}

export interface BookingRow {
  id: string;
  starts_at: string;
  ends_at: string;
  status: string;
  topic: string | null;
  consultant: string;
  contact_name: string | null;
  contact_email: string | null;
  contact_timezone: string | null;
  company: string | null;
  conversation_id: string | null;
  calendar_event_id: string | null;
}

export interface HandoverRow {
  id: string;
  ref: string;
  conversation_id: string;
  reason: string;
  urgency: "p1" | "high" | "normal" | "low";
  route_to: string;
  assigned_to: string | null; // null = general team queue
  summary: string;
  status: "open" | "acknowledged" | "resolved";
  created_at: string;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  acknowledged_by_name: string | null;
  resolved_at: string | null;
  resolved_by_name: string | null;
  can_take: boolean;
  can_resolve: boolean;
  can_reassign: boolean;
}

export interface TicketRow {
  id: string;
  ref: string;
  summary: string;
  status: string;
  priority: "urgent" | "high" | "normal" | "low";
  company: string;
  contact: string | null;
  system: string;
  external_id: string | null;
  updates: number;
  created_at: string;
  updated_at: string;
  synced: boolean | null; // true = checked live with Jira on this load
}

export interface ConfigEntry {
  key: string;
  description: string;
  value: unknown;
  source: "published" | "default";
  published_version: number | null;
  published_at: string | null;
  draft_versions: number;
}

export interface ConfigVersion {
  key: string;
  version: number;
  status: "draft" | "published" | "archived";
  value: unknown;
  created_by: string | null;
  created_at: string;
  published_at: string | null;
}

// ---------- Queries ----------

const qs = (params: Record<string, string | number | boolean | undefined | null>) => {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const useOverview = () =>
  useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/v1/dashboard/overview"), refetchInterval: 30_000 });

export const useConversations = (f: { status?: string; q?: string }) =>
  useQuery({ queryKey: ["conversations", f], queryFn: () => api<ConversationRow[]>(`/v1/dashboard/conversations${qs(f)}`) });

export const useConversation = (id: string) =>
  useQuery({ queryKey: ["conversation", id], queryFn: () => api<ConversationDetail>(`/v1/conversations/${id}`) });

export const useLeads = (status?: string, q?: string) =>
  useQuery({
    queryKey: ["leads", status, q],
    queryFn: () => api<LeadRow[]>(`/v1/dashboard/leads${qs({ status, q })}`),
    placeholderData: (prev) => prev,
  });

export const useBookings = (scope: "upcoming" | "past", q?: string) =>
  useQuery({
    queryKey: ["bookings", scope, q],
    queryFn: () => api<BookingRow[]>(`/v1/dashboard/bookings${qs({ scope, q })}`),
    placeholderData: (prev) => prev,
  });

export const useHandovers = (status: string) =>
  useQuery({
    queryKey: ["handovers", status],
    queryFn: () => api<HandoverRow[]>(`/v1/handovers${qs({ status })}`),
    refetchInterval: 20_000,
  });

export const useTickets = (openOnly: boolean) =>
  useQuery({ queryKey: ["tickets", openOnly], queryFn: () => api<TicketRow[]>(`/v1/dashboard/tickets${qs({ open_only: openOnly })}`) });

export const useConfigCatalogue = () =>
  useQuery({ queryKey: ["config"], queryFn: () => api<ConfigEntry[]>("/v1/dashboard/config") });

export const useConfigVersions = (key: string, enabled: boolean) =>
  useQuery({ queryKey: ["config", key], queryFn: () => api<ConfigVersion[]>(`/v1/config/${key}`), enabled });

export function useAcknowledge() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<HandoverRow>(`/v1/handovers/${id}/acknowledge`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["handovers"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
    },
  });
}

export function useSaveDraft(key: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (value: unknown) =>
      api<ConfigVersion>(`/v1/config/${key}/drafts`, { method: "POST", body: JSON.stringify({ value }) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
      qc.invalidateQueries({ queryKey: ["content"] });
    },
  });
}

export function usePublish(key: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (version: number) =>
      api<ConfigVersion>(`/v1/config/${key}/versions/${version}/publish`, { method: "POST" }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["config"] });
      qc.invalidateQueries({ queryKey: ["content"] });
    },
  });
}

// ---------- Phase 6 additions: content, insights, internal assistant, client access ----------

export interface ContentPage {
  slug: string;
  key: string;
  title: string;
  doc_type: string;
  raw: string;
  origin: "file" | "dashboard" | "draft";
  published_version: number | null;
  published_at: string | null;
  draft_versions: number;
  sections: number;
  indexed_chunks: number;
}

export interface ContentPreview {
  title: string;
  doc_type: string;
  sections: { section: string; chars: number }[];
  issues: string[];
}

export interface AskHit {
  title: string;
  section: string | null;
  score: number;
  text: string;
}

export interface UnansweredTopic {
  topic: string;
  count: number;
  examples: string[];
  last_asked: string;
  conversation_ids: string[];
}

export interface Source {
  n: number;
  title: string;
  section: string | null;
  client: string | null;
  uri: string | null;
  score: number;
  kind?: "document" | "record"; // record = live data (handovers, leads, calls, tickets)
}

export interface InternalChat {
  conversation_id: string;
  created_at: string;
  title: string;
  turns: number;
}

export interface InternalTurn {
  role: "user" | "assistant";
  content: string;
  created_at: string;
  sources: Source[];
}

export interface InternalReply {
  conversation_id: string;
  reply: string;
  degraded: boolean;
  sources: Source[];
}

export interface Grant {
  id: number;
  staff_email: string;
  company_id: string;
  company: string;
  granted_by: string | null;
  created_at: string;
}

export interface ClientAccessData {
  grants: Grant[];
  companies: { id: string; name: string; domain: string | null }[];
}

export const useContent = () =>
  useQuery({ queryKey: ["content"], queryFn: () => api<ContentPage[]>("/v1/dashboard/content") });

export const previewContent = (raw: string) =>
  api<ContentPreview>("/v1/dashboard/content/preview", { method: "POST", body: JSON.stringify({ raw }) });

export const askContent = (question: string) =>
  api<AskHit[]>("/v1/dashboard/content/ask", { method: "POST", body: JSON.stringify({ question }) });

export const useUnanswered = (enabled = true) =>
  useQuery({ queryKey: ["unanswered"], queryFn: () => api<UnansweredTopic[]>("/v1/dashboard/unanswered"), enabled });

export const useInternalChats = () =>
  useQuery({ queryKey: ["internal-chats"], queryFn: () => api<InternalChat[]>("/v1/internal/conversations") });

export const useInternalChat = (id: string | null) =>
  useQuery({
    queryKey: ["internal-chat", id],
    queryFn: () => api<InternalTurn[]>(`/v1/internal/conversations/${id}`),
    enabled: !!id,
  });

export async function askInternal(conversationId: string | null, text: string) {
  const id =
    conversationId ??
    (await api<{ conversation_id: string }>("/v1/internal/conversations", { method: "POST" })).conversation_id;
  return api<InternalReply>(`/v1/internal/conversations/${id}/messages`, {
    method: "POST",
    body: JSON.stringify({ text }),
  });
}

export const useClientAccess = () =>
  useQuery({ queryKey: ["client-access"], queryFn: () => api<ClientAccessData>("/v1/dashboard/client-access") });

export function useGrant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { staff_email: string; company_id: string }) =>
      api<Grant>("/v1/dashboard/client-access", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["client-access"] }),
  });
}

export function useRevoke() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      const t = await userManager.getUser();
      const res = await fetch(`${API}/v1/dashboard/client-access/${id}`, {
        method: "DELETE",
        headers: t ? { Authorization: `Bearer ${t.access_token}` } : {},
      });
      if (!res.ok) throw new ApiError(res.status, res.statusText);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["client-access"] }),
  });
}

// ---------- Handover actions ----------

function useHandoverAction<T>(fn: (arg: T) => Promise<HandoverRow>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["handovers"] });
      qc.invalidateQueries({ queryKey: ["overview"] });
    },
  });
}

export const useResolve = () =>
  useHandoverAction((id: string) => api<HandoverRow>(`/v1/handovers/${id}/resolve`, { method: "POST" }));

export const useReassign = () =>
  useHandoverAction(({ id, to }: { id: string; to: string | null }) =>
    api<HandoverRow>(`/v1/handovers/${id}/reassign`, { method: "POST", body: JSON.stringify({ to }) }),
  );

export const useAssignees = (enabled: boolean) =>
  useQuery({
    queryKey: ["assignees"],
    queryFn: () => api<{ name: string; email: string }[]>("/v1/handovers/assignees"),
    enabled,
  });

// ---------- Rule editors ----------

export interface RuleOptions {
  needs: string[];
  sizes: string[];
  budgets: string[];
  timelines: string[];
  roles: string[];
  requirable: string[];
  urgencies: string[];
  handover_reasons: string[];
}

export const useRuleOptions = () =>
  useQuery({ queryKey: ["rule-options"], queryFn: () => api<RuleOptions>("/v1/dashboard/rules/options"), staleTime: Infinity });

export const previewRules = (key: string, value: unknown, sample: unknown) =>
  api<{ ok: boolean; result?: Record<string, unknown>; error?: string }>(`/v1/dashboard/rules/${key}/preview`, {
    method: "POST",
    body: JSON.stringify({ value, sample }),
  });

/** Download an API response (e.g. a privacy export) as a file, with the staff token. */
export async function apiDownload(path: string, filename: string) {
  const t = await token();
  const res = await fetch(`${API}${path}`, { headers: t ? { Authorization: `Bearer ${t}` } : {} });
  if (!res.ok) throw new ApiError(res.status, res.statusText);
  const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") ?? "")?.[1] ?? filename;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
