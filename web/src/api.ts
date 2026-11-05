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
