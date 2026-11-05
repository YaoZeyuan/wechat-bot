export interface Device {
  serial: string
  state: string
}

export interface DevicesResponse {
  adb: string | null
  devices: Device[]
  ready: boolean
  hint: string
}

export interface HealthResponse {
  ok: boolean
  python: string
  uiautomator2: string
  fastapi: string
  database: string
  database_exists: boolean
}

export interface AppConfig {
  default_target: string
  serial: string
  dry_run: boolean
  step_delay: number
  wait_timeout: number
  verify_timeout: number
  [key: string]: unknown
}

export interface ConfigPatch {
  default_target?: string
  serial?: string
  dry_run?: boolean
  step_delay?: number
  wait_timeout?: number
  verify_timeout?: number
}

export interface SendPayload {
  text: string
  target?: string
  dry_run?: boolean
  serial?: string
}

export interface SendResult {
  ok: boolean
  target: string
  dry_run: boolean
  chars: number
}

export interface LogItem {
  id: number
  target: string
  text: string
  ok: number
  message: string
  dry_run: number
  created_at: string
}

export interface Stats {
  today: string
  total: number
  ok: number
  dry_run: number
}
