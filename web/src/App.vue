<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from './api'
import type { Device, HealthResponse, LogItem, Stats } from './types'

const health = ref<HealthResponse | null>(null)
const devices = ref<Device[]>([])
const deviceReady = ref(false)
const deviceHint = ref('')
const stats = ref<Stats | null>(null)
const logs = ref<LogItem[]>([])

const error = ref('')
const notice = ref('')
const busy = ref(false)
const saving = ref(false)

const target = ref('')
const text = ref('')
const dryRun = ref(true)

const form = ref({
  default_target: '',
  serial: '',
  step_delay: 0.6,
  dry_run: false,
})

const apiOnline = computed(() => health.value?.ok === true)
const deviceText = computed(() => {
  if (deviceReady.value) {
    return devices.value.filter((item) => item.state === 'device').map((item) => item.serial).join(', ')
  }
  const first = devices.value[0]
  if (first && first.state !== 'device') return `${first.serial} 状态 ${first.state}`
  return '未连接'
})

function formatTime(value: string): string {
  return value.slice(0, 19).replace('T', ' ')
}

function resultText(item: LogItem): string {
  if (item.dry_run) return '演练'
  return item.ok ? '成功' : '失败'
}

async function refresh(): Promise<void> {
  try {
    const [h, d, l, s] = await Promise.all([
      api.health(),
      api.devices(),
      api.logs(),
      api.stats(),
    ])
    health.value = h
    devices.value = d.devices
    deviceReady.value = d.ready
    deviceHint.value = d.hint
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
      serial: String(cfg.serial ?? ''),
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
  try {
    const result = await api.send({
      text: text.value,
      target: target.value || undefined,
      dry_run: dryRun.value,
    })
    if (result.dry_run) {
      notice.value = '演练完成：文本已填入输入框，未点击发送。'
    } else if (result.ok) {
      notice.value = '发送完成，文本已在会话中校验到。'
    } else {
      notice.value = '已点击发送，但没校验到文本，请看 debug/ 里的截图。'
    }
    await refresh()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}

async function saveConfig(): Promise<void> {
  error.value = ''
  notice.value = ''
  saving.value = true
  try {
    await api.patchConfig({
      default_target: form.value.default_target,
      serial: form.value.serial,
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
  await Promise.all([refresh(), loadConfig()])
})
</script>

<template>
  <div class="page">
    <header class="topbar">
      <div>
        <h1>wechat-bot 控制台</h1>
        <p class="sub">电脑端消息读取与手机发送</p>
      </div>
      <div class="badges">
        <span class="badge" :class="apiOnline ? 'ok' : 'warn'">API {{ apiOnline ? '在线' : '离线' }}</span>
        <span class="badge" :class="deviceReady ? 'ok' : 'warn'">设备 {{ deviceText }}</span>
      </div>
    </header>

    <p v-if="error" class="message error">{{ error }}</p>
    <p v-if="notice" class="message ok">{{ notice }}</p>

    <div class="grid">
      <section class="card">
        <h2>发送消息</h2>
        <label>目标会话
          <input v-model="target" placeholder="群名或文件传输助手" />
        </label>
        <label>文本
          <textarea v-model="text" rows="8" placeholder="输入要发送的内容，支持中文与多行"></textarea>
        </label>
        <label class="check">
          <input type="checkbox" v-model="dryRun" />
          仅演练（填入输入框但不点击发送）
        </label>
        <div class="row">
          <button class="primary" :disabled="busy || !text.trim()" @click="doSend">
            {{ busy ? '处理中…' : dryRun ? '演练发送' : '实际发送' }}
          </button>
          <button class="ghost" :disabled="busy" @click="refresh">刷新状态</button>
        </div>
        <p class="hint">没插手机时服务端会返回明确错误，不会静默失败。</p>
      </section>

      <section class="card">
        <h2>配置</h2>
        <label>默认目标
          <input v-model="form.default_target" />
        </label>
        <label>设备序列号（留空自动选择）
          <input v-model="form.serial" placeholder="USB 序列号或 IP:端口" />
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
        <p v-if="deviceHint" class="hint warn-text">{{ deviceHint }}</p>
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

    <footer class="foot">
      Python {{ health?.python ?? '—' }} · uiautomator2 {{ health?.uiautomator2 ?? '—' }} · FastAPI {{ health?.fastapi ?? '—' }}
    </footer>
  </div>
</template>
