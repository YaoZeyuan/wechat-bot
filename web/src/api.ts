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

export interface KeywordRule {
  group_id: string
  keyword: string
  handler: string
  url: string | null
  enabled: boolean
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

export const api = {
  pickReaderDirectory: () =>
    request<{ directory: string }>('/reader/accounts/pick-directory', { method: 'POST', body: '{}' }),
  extractReaderKey: () => request<{ key: string }>('/reader/key/extract', { method: 'POST', body: '{}' }),
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
  setReaderStatus: (enabled: boolean) =>
    request<ReaderStatus>('/reader/status', { method: 'PUT', body: JSON.stringify({ enabled }) }),
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
  probeReader: (payload: { query: string; port?: number; token?: string; session_id?: string; database_path?: string; account?: string; database_key?: string }) =>
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
