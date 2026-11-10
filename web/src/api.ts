import type {
  DevicesResponse,
  HealthResponse,
  LogItem,
  SendPayload,
  SendResult,
  Stats,
  AppConfig,
  ConfigPatch,
} from './types'

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api'

export interface Conversation {
  id: string
  name: string
  kind: 'group' | 'private'
  timestamp_ms: number
}

export interface HistoryMessage {
  messageId: string
  timestampMs: number
  direction: 'in' | 'out'
  kind: 'text' | 'link' | 'other'
  link?: { url: string; title: string; description: string; source: string; cover: string } | null
  file_name?: string | null
  type_code: number
  sender: { username: string | null; display_name: string | null }
  text: string
}

export interface KeywordRule {
  group_id: string
  keyword: string
  handler: string
  url: string | null
  enabled: boolean
}

export interface MonitoredGroup {
  group_id: string
  name: string
  enabled: boolean
  original_name?: string
  remark?: string
}

export interface ConversationDetails {
  id: string
  name: string
  kind: 'private' | 'group'
  original_name: string
  remark: string
}

export interface GroupPlugin {
  id: string
  name: string
  description: string
  enabled: boolean
  version: string
  capability: string
}

interface Delivery {
  id: number
  group_id: string
  message_id: string
  handler: string
  status: string
  task: string | null
  error: string | null
  created_at: string
}

interface ReaderStatus {
  enabled: boolean
  interval_seconds: number
  running: boolean
  error: string | null
  last_success_at: string | null
  mock_enabled: boolean
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) detail = body.detail
    } catch {
      detail = `${detail}（响应不是 JSON）`
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

interface SenderJobResult {
  status: string
  steps: string[]
  items: string[]
}

export const api = {
  uploadSenderAsset: (id: string, file: File) =>
    request(`/sender/assets/${id}?filename=${encodeURIComponent(file.name)}`, { method: 'PUT', body: file }),
  submitSenderJob: (payload: unknown) =>
    request<SenderJobResult>('/sender/jobs', { method: 'POST', body: JSON.stringify(payload) }),
  senderJob: (id: string) => request<SenderJobResult>(`/sender/jobs/${id}`),
  senderProgress: () => request<{ running: boolean; steps: string[] }>('/sender/progress'),
  senderStatus: () => request<{ connected: boolean }>('/sender/status'),
  sendFromConsole: (payload: SendPayload) =>
    request<SendResult>('/sender/send', { method: 'POST', body: JSON.stringify(payload) }),
  pluginCatalog: () => request<(Omit<GroupPlugin, 'enabled'> & { default_enabled: boolean })[]>('/reader/plugins'),
  sendGroupTest: (groupId: string) =>
    request<{ ok: boolean }>(`/reader/monitored-groups/${encodeURIComponent(groupId)}/test-send`, { method: 'POST' }),
  conversationDetails: (session_id: string) =>
    request<ConversationDetails>(`/reader/history/details?${new URLSearchParams({ session_id })}`),
  groupPlugins: (groupId: string) =>
    request<GroupPlugin[]>(`/reader/monitored-groups/${encodeURIComponent(groupId)}/plugins`),
  saveGroupPlugin: (groupId: string, pluginId: string, enabled: boolean) =>
    request<GroupPlugin>(`/reader/monitored-groups/${encodeURIComponent(groupId)}/plugins/${encodeURIComponent(pluginId)}`, {
      method: 'PUT', body: JSON.stringify({ enabled }),
    }),
  monitoredGroups: () => request<MonitoredGroup[]>('/reader/monitored-groups'),
  saveMonitoredGroup: (group: MonitoredGroup) =>
    request<MonitoredGroup>('/reader/monitored-groups', { method: 'PUT', body: JSON.stringify(group) }),
  monitorGroups: (q: string) =>
    request<{ items: { sessionId: string; displayName: string; original_name: string; remark: string }[]; hasMore: boolean }>(
      `/reader/groups?${new URLSearchParams({ q })}`,
    ),
  conversations: (q: string, kind: string, offset: number) =>
    request<{ items: Conversation[]; has_more: boolean }>(
      `/reader/history/conversations?${new URLSearchParams({ q, kind, offset: String(offset) })}`,
    ),
  conversationMessages: (session_id: string, offset: number) =>
    request<{ items: HistoryMessage[]; has_more: boolean }>(
      `/reader/history/messages?${new URLSearchParams({ session_id, offset: String(offset) })}`,
    ),
  readerKeyProgress: () => request<{ enabled: boolean; stage: string; message: string; steps: { stage: string; message: string }[] }>('/reader/key/progress'),
  readerProfile: () => request<{ database_path: string; account: string } | null>('/reader/profile'),
  readerAccountSelection: () => request<{ database_path: string; account: string } | null>('/reader/account-selection'),
  saveReaderAccountSelection: (database_path: string, account: string) =>
    request<{ database_path: string; account: string }>('/reader/account-selection', {
      method: 'PUT', body: JSON.stringify({ database_path, account }),
    }),
  pickReaderDirectory: () =>
    request<{ directory: string }>('/reader/accounts/pick-directory', { method: 'POST', body: '{}' }),
  scanReaderKey: (kind: 'database' | 'image', database_path: string, account: string, restart = false) =>
    request<{ key?: string; image_xor_key?: string; image_aes_key?: string }>(`/reader/key/scan/${kind}${restart ? '?restart=true' : ''}`, {
      method: 'POST', body: JSON.stringify({ database_path, account }),
    }),
  findReaderAccounts: (directory: string) =>
    request<{ items: { account: string; database_path: string }[] }>('/reader/accounts', {
      method: 'POST', body: JSON.stringify({ directory }),
    }),
  submitMockMessage: (group_id: string, text: string) =>
    request<{ status: string }>('/reader/mock/messages', {
      method: 'POST',
      body: JSON.stringify({
        group_id, text, messageId: crypto.randomUUID(), timestampMs: Date.now(),
        direction: 'in', kind: 'text', sender: { username: 'mock-member' },
      }),
    }),
  readerStatus: () => request<ReaderStatus>('/reader/status'),
  setReaderStatus: (enabled: boolean, interval_seconds?: number) =>
    request<ReaderStatus>('/reader/status', { method: 'PUT', body: JSON.stringify({ enabled, interval_seconds }) }),
  readerRules: () => request<{ items: KeywordRule[]; handlers: string[] }>('/reader/rules'),
  saveReaderRule: (rule: KeywordRule) =>
    request<KeywordRule>('/reader/rules', { method: 'PUT', body: JSON.stringify(rule) }),
  previewReaderRule: (rule: KeywordRule, text: string) =>
    request<{ matched: boolean; handler: string; reply: unknown }>('/reader/rules/preview', {
      method: 'POST', body: JSON.stringify({ rule, text }),
    }),
  readerDeliveries: () => request<{ items: Delivery[] }>('/reader/deliveries'),
  retryReaderDelivery: (id: number) =>
    request<{ status: string }>(`/reader/deliveries/${id}/retry`, { method: 'POST', body: '{}' }),
  probeReader: (payload: { query: string; port?: number; token?: string; session_id?: string; database_path?: string; account?: string; database_key?: string; image_xor_key?: string; image_aes_key?: string; save_profile?: boolean }) =>
    request<{ groups: { id: string; name: string }[]; has_more: boolean; message_readable: boolean | null; has_messages: boolean | null }>('/reader/probe', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  health: () => request<HealthResponse>('/health'),
  devices: () => request<DevicesResponse>('/devices'),
  getConfig: () => request<AppConfig>('/config'),
  patchConfig: (patch: ConfigPatch) =>
    request<{ ok: boolean; config: AppConfig }>('/config', {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  send: (payload: SendPayload) =>
    request<SendResult>('/send', { method: 'POST', body: JSON.stringify(payload) }),
  logs: () => request<{ items: LogItem[] }>('/logs?limit=30'),
  stats: () => request<Stats>('/stats'),
}
