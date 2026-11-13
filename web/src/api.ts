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

export async function previewChatSummary(groupId: string, day: string) {
  const response = await fetch(`${BASE}/reader/plugins/chat_summary/preview`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ group_id: groupId, day }),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string' ? body.detail : `预览失败（HTTP ${response.status}）`)
  }
  return response.blob()
}

export async function downloadChatTranscript(sessionId: string, start: string, end: string) {
  const query = new URLSearchParams({ session_id: sessionId, start, end })
  const response = await fetch(`${BASE}/reader/history/export?${query}`)
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string' ? body.detail : `导出失败（HTTP ${response.status}）`)
  }
  return response.blob()
}

export interface WirelessStatus {
  device: { serial: string; state: string; model: string } | null
  device_count: number
}

export interface GlobalAiSettings {
  provider: 'deepseek' | 'doubao' | 'compatible'
  base_url: string
  model: string
  thinking: 'enabled' | 'disabled' | null
  timeout_seconds: number
  summary_prompt: string
  has_api_key: boolean
}

export interface AiChatRoute {
  id: string
  provider: string
  model_alias: string
  has_api_key: boolean
  base_url: string
  timeout_seconds: number
  prefixes: string[]
  strip_prefixes: string[]
  model: { id: string; thinking: 'enabled' | 'disabled' | null; web_search: boolean }
}

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
  kind: 'text' | 'link' | 'quote' | 'other'
  quotes?: { sender: string; text: string }[]
  message_label?: string | null
  forwarded?: { depth: number; sender: string; time: string; text: string; quotes: { sender: string; text: string }[] }[]
  link?: { url: string; title: string; description: string; source: string; cover: string } | null
  file_name?: string | null
  type_code: number
  sender: { username: string | null; display_name: string | null }
  text: string
}

export interface SenderAiLog {
  msg: string
  timestamp?: string
  provider?: string
  model?: string
  url?: string
  status?: number
  elapsed_seconds?: number
  request_body?: string
  response_body?: string
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
  admin_ids?: string[]
  global_enabled?: boolean
  group?: string
  order?: number
  id: string
  name: string
  description: string
  enabled: boolean
  version: string
  capability: string
  assistant_name?: string | null
  intro_reply?: string | null
  history_count?: number
  max_chars?: number
  fields?: { key: 'assistant_name' | 'intro_reply' | 'history_count' | 'max_chars'; label: string; type: 'input' | 'textarea' | 'number';
    max_length: number; placeholder?: string; description?: string }[]
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
  screens?: number[]
}

export interface SenderHistoryItem {
  id: string
  source: string
  created_ms: number | null
  target: { name: string }
  status: string
}

export const api = {
  aiChatRoutes: (groupId: string) => request<AiChatRoute[]>(`/reader/monitored-groups/${encodeURIComponent(groupId)}/plugins/ai_chat/routes`),
  saveAiChatRoute: (groupId: string, route: AiChatRoute) => request<AiChatRoute[]>(
    `/reader/monitored-groups/${encodeURIComponent(groupId)}/plugins/ai_chat/routes/${encodeURIComponent(route.id)}`, {
      method: 'PUT', body: JSON.stringify({
        base_url: route.base_url, timeout_seconds: route.timeout_seconds, model: route.model,
        prefixes: route.prefixes, strip_prefixes: route.strip_prefixes,
      }),
    },
  ),
  globalAi: () => request<GlobalAiSettings>('/reader/global-ai'),
  saveGlobalAi: (settings: Omit<GlobalAiSettings, 'has_api_key'> & { api_key: string }) =>
    request<GlobalAiSettings>('/reader/global-ai', { method: 'PUT', body: JSON.stringify(settings) }),
  senderHistory: (offset = 0) => request<SenderHistoryItem[]>(`/sender/history?offset=${offset}`),
  senderHistoryScreen: (id: string, step: number) => `${BASE}/sender/history/${id}/screens/${step}`,
  async senderScreen() {
    const response = await fetch(`${BASE}/sender/screen`, { cache: 'no-store' })
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      throw new Error(body.detail || '获取手机截图失败')
    }
    return response.blob()
  },
  uploadSenderAsset: (id: string, file: File) =>
    request(`/sender/assets/${id}?filename=${encodeURIComponent(file.name)}`, { method: 'PUT', body: file }),
  submitSenderJob: (payload: unknown) =>
    request<SenderJobResult>('/sender/jobs', { method: 'POST', body: JSON.stringify(payload) }),
  senderJob: (id: string) => request<SenderJobResult>(`/sender/jobs/${id}`),
  senderHistoryAi: (id: string) => request<SenderAiLog[]>(`/sender/history/${id}/ai`),
  senderHistoryPdf: (id: string) => `${BASE}/sender/history/${id}/pdf`,
  retrySenderJob: (id: string) =>
    request<{ request_id: string }>(`/sender/history/${id}/retry`, { method: 'POST' }),
  senderProgress: () => request<{ running: boolean; steps: string[] }>('/sender/progress'),
  senderStatus: () => request<{ connected: boolean }>('/sender/status', { signal: AbortSignal.timeout(8000) }),
  restartAdb: () => request<{ connected: boolean }>('/sender/adb/restart', { method: 'POST' }),
  pairWireless: (address: string, code: string) =>
    request<WirelessStatus>('/sender/adb/pair', { method: 'POST', body: JSON.stringify({ address, code }) }),
  createWirelessQr: () =>
    request<{ id: string; image: string; expires_in: number }>('/sender/adb/qr', {
      method: 'POST', signal: AbortSignal.timeout(8000),
    }),
  pollWirelessQr: (id: string, signal: AbortSignal) =>
    request<({ status: 'waiting' } | ({ status: 'paired' } & WirelessStatus))>(
      `/sender/adb/qr/${encodeURIComponent(id)}/poll`, { method: 'POST', signal },
    ),
  connectWireless: (address: string) =>
    request<WirelessStatus>('/sender/adb/connect', { method: 'POST', body: JSON.stringify({ address }) }),
  sendFromConsole: (payload: SendPayload) =>
    request<SendResult>('/sender/send', { method: 'POST', body: JSON.stringify(payload) }),
  pluginCatalog: () => request<(Omit<GroupPlugin, 'enabled'> & { default_enabled: boolean })[]>('/reader/plugins'),
  savePluginDefaults: (pluginId: string, settings: { global_enabled: boolean; enabled: boolean;
    assistant_name?: string | null; intro_reply?: string | null; history_count?: number; max_chars?: number }) =>
    request<Omit<GroupPlugin, 'enabled'> & { default_enabled: boolean }>(`/reader/plugins/${encodeURIComponent(pluginId)}`, {
      method: 'PUT', body: JSON.stringify(settings),
    }),
  conversationDetails: (session_id: string) =>
    request<ConversationDetails>(`/reader/history/details?${new URLSearchParams({ session_id })}`),
  groupPlugins: (groupId: string) =>
    request<GroupPlugin[]>(`/reader/monitored-groups/${encodeURIComponent(groupId)}/plugins`),
  groupMembers: (groupId: string) => request<{ id: string; name: string }[]>(
    `/reader/monitored-groups/${encodeURIComponent(groupId)}/members`),
  saveGroupPlugin: (groupId: string, pluginId: string, enabled: boolean, assistant_name?: string | null, intro_reply?: string | null, history_count?: number, max_chars?: number, admin_ids?: string[]) =>
    request<GroupPlugin>(`/reader/monitored-groups/${encodeURIComponent(groupId)}/plugins/${encodeURIComponent(pluginId)}`, {
      method: 'PUT', body: JSON.stringify({ enabled, assistant_name, intro_reply, history_count, max_chars, admin_ids }),
    }),
  monitoredGroups: () => request<MonitoredGroup[]>('/reader/monitored-groups'),
  deleteMonitoredGroup: (id: string) =>
    request<{ ok: boolean }>(`/reader/monitored-groups/${encodeURIComponent(id)}`, { method: 'DELETE' }),
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
  deleteReaderRule: (group_id: string, keyword: string) =>
    request<{ ok: boolean }>(`/reader/rules?${new URLSearchParams({ group_id, keyword })}`, { method: 'DELETE' }),
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
