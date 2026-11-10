<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { api } from './api'
import type { HealthResponse, LogItem, Stats } from './types'

const health = ref<HealthResponse | null>(null)
const deviceReady = ref<boolean | null>(null)
let statusTimer: number | undefined
let checkingDevice = false
const stats = ref<Stats | null>(null)
const logs = ref<LogItem[]>([])

const error = ref('')
const notice = ref('')
const busy = ref(false)
const steps = ref<string[]>([])
const screen = ref('')
const loadingScreen = ref(false)
const screenError = ref('')
let disposed = false

function clearScreen() {
  if (screen.value) URL.revokeObjectURL(screen.value)
  screen.value = ''
}

async function captureScreen() {
  loadingScreen.value = true
  screenError.value = ''
  try {
    const blob = await api.senderScreen()
    if (disposed) return
    clearScreen()
    screen.value = URL.createObjectURL(blob)
  } catch (cause) {
    screenError.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    loadingScreen.value = false
  }
}
const saving = ref(false)

const target = ref('')
const text = ref('')
const dryRun = ref(true)
const kind = ref<'contact' | 'group' | 'file_helper'>('file_helper')
const targetId = ref('')
const attachments = ref<File[]>([])

function pickFiles(event: Event) {
  attachments.value = Array.from((event.target as HTMLInputElement).files ?? [])
}

const form = ref({
  default_target: '',
  step_delay: 0.6,
  dry_run: false,
})

const apiOnline = computed(() => health.value?.ok === true)
const deviceText = computed(() => deviceReady.value === null ? '待确认' : deviceReady.value ? '已连接' : '不可用')

async function refreshDevice() {
  if (checkingDevice) return
  checkingDevice = true
  try {
    deviceReady.value = (await api.senderStatus()).connected
  } catch {
    deviceReady.value = null
  } finally {
    checkingDevice = false
  }
}

function formatTime(value: string): string {
  return value.slice(0, 19).replace('T', ' ')
}

function resultText(item: LogItem): string {
  if (item.dry_run) return '演练'
  return item.ok ? '成功' : '失败'
}

async function refresh(): Promise<void> {
  try {
    const [h, l, s] = await Promise.all([
      api.health(),
      api.logs(),
      api.stats(),
      refreshDevice(),
    ])
    health.value = h
    logs.value = l.items
    stats.value = s
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}

async function loadConfig(): Promise<void> {
  try {
    const cfg = await api.getConfig()
    form.value = {
      default_target: String(cfg.default_target ?? ''),
      step_delay: Number(cfg.step_delay ?? 0.6),
      dry_run: Boolean(cfg.dry_run),
    }
    if (!target.value) target.value = form.value.default_target
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  }
}

async function doSend(): Promise<void> {
  error.value = ''
  notice.value = ''
  busy.value = true
  steps.value = []
  try {
    const messages: ({ type: 'text'; text: string } | { type: 'image' | 'file'; asset_id: string })[] = []
    if (text.value.trim()) messages.push({ type: 'text', text: text.value })
    for (const file of attachments.value) {
      steps.value.push('正在上传附件 ' + file.name)
      const assetId = crypto.randomUUID()
      await api.uploadSenderAsset(assetId, file)
      messages.push({ type: /\.(png|jpe?g)$/i.test(file.name) ? 'image' : 'file', asset_id: assetId })
    }
    const requestId = crypto.randomUUID()
    const helper = kind.value === 'file_helper'
    let result = await api.submitSenderJob({
      version: 1,
      request_id: requestId,
      mode: dryRun.value ? 'dry_run' : 'send',
      target: { kind: kind.value, id: helper ? 'filehelper' : targetId.value.trim(), name: helper ? '文件传输助手' : target.value.trim() },
      messages,
    })
    while (result.status === 'queued' || result.status === 'running') {
      steps.value = result.steps
      await new Promise(resolve => window.setTimeout(resolve, 500))
      result = await api.senderJob(requestId)
    }
    const labels: Record<string, string> = { pending: '未执行', checked: '演练通过', sent: '已发送', uncertain: '待核对', failed: '失败' }
    steps.value = [...result.steps, ...result.items.map((item, index) => `第 ${index + 1} 条消息 · ${labels[item] ?? item}`)]
    if (result.status === 'completed') notice.value = dryRun.value ? '演练完成' : '发送完成'
    else error.value = result.status === 'uncertain' ? '结果待核对，请先查看手机会话' : '任务失败，请查看发送过程'
    await refresh()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}
async function refreshProgress() {
  try {
    steps.value = (await api.senderProgress()).steps
  } catch {
    // The send request itself reports a connection failure.
  }
}

async function saveConfig(): Promise<void> {
  error.value = ''
  notice.value = ''
  saving.value = true
  try {
    await api.patchConfig({
      default_target: form.value.default_target,
      step_delay: Number(form.value.step_delay),
      dry_run: form.value.dry_run,
    })
    notice.value = '配置已写入 config.json。'
    await loadConfig()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  statusTimer = window.setInterval(() => { if (!document.hidden && !busy.value) void refreshDevice() }, 5000)
  await Promise.all([refresh(), loadConfig(), refreshProgress()])
})
onUnmounted(() => {
  disposed = true
  window.clearInterval(statusTimer)
  clearScreen()
})
</script>

<template>
  <div class="page">
    <header class="topbar">
      <div>
        <h1>Sender 发送调试</h1>
        <p class="sub">在手机前台打开微信，然后选择会话并填写文字或上传附件。</p>
      </div>
      <div class="badges">
        <span class="badge" :class="apiOnline ? 'ok' : 'warn'">API {{ apiOnline ? '在线' : '离线' }}</span>
        <span class="badge" :class="deviceReady ? 'ok' : 'warn'">手机{{ deviceText }}</span>
      </div>
    </header>

    <p v-if="error" class="message error">{{ error }}</p>
    <p v-if="notice" class="message ok">{{ notice }}</p>

    <div class="grid">
      <section class="card">
        <h2>发送消息</h2>
        <label>会话类型
          <select v-model="kind">
            <option value="file_helper">文件传输助手</option>
            <option value="contact">联系人</option>
            <option value="group">群聊</option>
          </select>
        </label>
        <label v-if="kind !== 'file_helper'">会话 ID
          <input v-model="targetId" />
        </label>
        <label v-if="kind !== 'file_helper'">目标会话
          <input v-model="target" placeholder="群名或文件传输助手" />
        </label>
        <label>文本
          <textarea v-model="text" rows="8" placeholder="输入要发送的内容，支持中文与多行"></textarea>
        </label>
        <label>附件（PNG、JPEG、TXT、PDF）
          <input type="file" multiple accept=".png,.jpg,.jpeg,.txt,.pdf" @change="pickFiles" />
        </label>
        <label class="check">
          <input type="checkbox" v-model="dryRun" />
          仅演练（填入输入框但不点击发送）
        </label>
        <div class="row">
          <button class="primary" :disabled="busy || deviceReady !== true || (kind !== 'file_helper' && (!target.trim() || !targetId.trim())) || (!text.trim() && !attachments.length)" @click="doSend">
            {{ busy ? '处理中…' : dryRun ? '演练发送' : '实际发送' }}
          </button>
          <button class="ghost" :disabled="busy" @click="refresh">刷新状态</button>
        </div>
        <p v-if="deviceReady === false" class="hint">请连接并授权一台手机，再刷新状态。</p>
        <h3 v-if="steps.length">发送过程</h3>
        <ol v-if="steps.length" aria-live="polite">
          <li v-for="(step, index) in steps" :key="index">{{ step }}</li>
        </ol>
      </section>

      <section class="card">
        <h2>配置</h2>
        <label>默认目标
          <input v-model="form.default_target" />
        </label>
        <label>每步延时（秒）
          <input v-model.number="form.step_delay" type="number" step="0.1" min="0" />
        </label>
        <label class="check">
          <input type="checkbox" v-model="form.dry_run" />
          全局默认演练模式
        </label>
        <div class="row">
          <button class="primary" :disabled="saving" @click="saveConfig">
            {{ saving ? '保存中…' : '保存配置' }}
          </button>
        </div>
        <p class="hint">保存后写入项目根目录的 config.json；UI 选择器候选也在这个文件里。</p>
      </section>
    </div>

    <section class="card">
      <div class="card-head">
        <h2>发送日志</h2>
        <span class="hint">今日 {{ stats?.total ?? 0 }} 次 · 成功 {{ stats?.ok ?? 0 }} · 演练 {{ stats?.dry_run ?? 0 }}</span>
      </div>
      <table v-if="logs.length">
        <thead>
          <tr>
            <th>时间</th><th>目标</th><th>内容</th><th>结果</th><th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in logs" :key="item.id">
            <td class="mono">{{ formatTime(item.created_at) }}</td>
            <td>{{ item.target }}</td>
            <td class="preview">{{ item.text.slice(0, 40) }}{{ item.text.length > 40 ? '…' : '' }}</td>
            <td>
              <span class="tag" :class="item.ok ? 'ok' : item.dry_run ? 'neutral' : 'warn'">{{ resultText(item) }}</span>
            </td>
            <td class="hint">{{ item.message }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="hint">还没有发送记录。接上手机后先在左侧点「演练发送」。</p>
    </section>

    <section class="card">
      <div class="card-head">
        <h2>手机画面</h2>
        <div class="row">
          <button class="ghost" :disabled="loadingScreen || deviceReady !== true" @click="captureScreen">
            {{ loadingScreen ? '读取中…' : '获取当前截图' }}
          </button>
          <button v-if="screen" class="ghost" @click="clearScreen">关闭截图</button>
        </div>
      </div>
      <p v-if="screenError" class="message error">{{ screenError }}</p>
      <img v-if="screen" :src="screen" alt="手机当前截图" style="max-width: 100%; max-height: 70vh" />
    </section>

    <footer class="foot">
      Python {{ health?.python ?? '—' }} · uiautomator2 {{ health?.uiautomator2 ?? '—' }} · FastAPI {{ health?.fastapi ?? '—' }}
    </footer>
  </div>
</template>
