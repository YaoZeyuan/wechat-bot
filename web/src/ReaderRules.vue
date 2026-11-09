<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { api, type KeywordRule, type MonitoredGroup } from './api'
import { readerGroups as groups, selectedReaderGroup as selected } from './readerSelection'

const rule = ref<KeywordRule>({ group_id: selected.value, keyword: '', handler: 'fixed_reply', url: null, enabled: true })
const rules = ref<KeywordRule[]>([])
const handlers = ref<string[]>([])
const deliveries = ref<Awaited<ReturnType<typeof api.readerDeliveries>>['items']>([])
const busy = ref(false)
const error = ref('')
const notice = ref('')
const sample = ref('测试')
const preview = ref<Awaited<ReturnType<typeof api.previewReaderRule>>>()
const monitoring = ref<Awaited<ReturnType<typeof api.readerStatus>>>()
const interval = ref<number | ''>()
const groupQuery = ref('')
const groupMatches = ref<{ sessionId: string; displayName: string }[]>([])
const groupSearchMessage = ref('')
const monitored = ref<MonitoredGroup[]>([])
const refreshError = ref('')
let refreshTimer: number | undefined
let refreshing = false

watch(selected, (id) => { if (id) rule.value.group_id = id })

async function load() {
  const [config, runs, status, groupSettings] = await Promise.all([
    api.readerRules(), api.readerDeliveries(), api.readerStatus(), api.monitoredGroups(),
  ])
  rules.value = config.items
  handlers.value = config.handlers
  deliveries.value = runs.items
  monitoring.value = status
  interval.value ??= status.interval_seconds
  monitored.value = groupSettings
}

async function run(action: () => Promise<void>) {
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await action()
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

function save() {
  return run(async () => {
    await api.saveReaderRule({
      ...rule.value,
      url: rule.value.handler === 'webhook' ? rule.value.url : null,
    })
    await load()
    notice.value = '规则已保存。'
  })
}

function previewCurrentRule() {
  preview.value = undefined
  return run(async () => {
    preview.value = await api.previewReaderRule({
      ...rule.value,
      url: rule.value.handler === 'webhook' ? rule.value.url : null,
    }, sample.value)
  })
}

function submitMockMessage() {
  return run(async () => {
    const result = await api.submitMockMessage(rule.value.group_id, sample.value)
    await load()
    notice.value = `模拟消息处理结果：${result.status}`
  })
}

function toggleMonitoring() {
  return run(async () => {
    monitoring.value = await api.setReaderStatus(!monitoring.value?.enabled)
  })
}

function saveInterval() {
  return run(async () => {
    const seconds = interval.value
    if (typeof seconds !== 'number' || !Number.isInteger(seconds) || seconds < 1 || seconds > 100) {
      throw new Error('请输入 1–100 的整数秒数')
    }
    monitoring.value = await api.setReaderStatus(!!monitoring.value?.enabled, seconds)
    notice.value = `已保存轮询间隔：${seconds} 秒。`
  })
}

function searchGroups() {
  return run(async () => {
    groupMatches.value = []
    groupSearchMessage.value = ''
    const result = await api.monitorGroups(groupQuery.value.trim())
    groupMatches.value = result.items
    groupSearchMessage.value = result.items.length
      ? (result.hasMore ? '仅显示前 50 个结果，请缩小搜索范围。' : '')
      : '没有找到匹配的群聊。'
  })
}

function setGroup(group_id: string, name: string, enabled: boolean) {
  return run(async () => {
    await api.saveMonitoredGroup({ group_id, name, enabled })
    monitored.value = await api.monitoredGroups()
    notice.value = enabled ? '已启用该群监听。' : '已暂停该群监听，群内规则仍保留。'
  })
}

function retry(id: number) {
  return run(async () => {
    await api.retryReaderDelivery(id)
    await load()
  })
}

async function refreshMonitoring() {
  if (busy.value || refreshing || document.hidden) return
  refreshing = true
  try {
    const [status, runs] = await Promise.all([api.readerStatus(), api.readerDeliveries()])
    if (!busy.value) {
      monitoring.value = status
      deliveries.value = runs.items
    }
    refreshError.value = ''
  } catch (err) {
    refreshError.value = err instanceof Error ? err.message : String(err)
  } finally {
    refreshing = false
  }
}

onMounted(() => {
  void run(load)
  refreshTimer = window.setInterval(refreshMonitoring, 5000)
})
onUnmounted(() => window.clearInterval(refreshTimer))
</script>

<template>
  <main class="page">
  <section class="card">
    <h2>监听群</h2>
    <form @submit.prevent="searchGroups">
      <label>按群名或群 ID 查找
        <input v-model="groupQuery" maxlength="100" placeholder="输入群名，或留空列出群聊" />
      </label>
      <a-button html-type="submit" :loading="busy">查找群聊</a-button>
    </form>
    <p v-if="groupSearchMessage">{{ groupSearchMessage }}</p>
    <ul v-if="groupMatches.length">
      <li v-for="group in groupMatches" :key="group.sessionId">
        <strong>{{ group.displayName }}</strong>
        <small>{{ group.sessionId }}</small>
        <a-button size="small" :disabled="busy || monitored.some(item => item.group_id === group.sessionId && item.enabled)"
          @click="setGroup(group.sessionId, group.displayName, true)">启用监听</a-button>
      </li>
    </ul>
    <ul v-if="monitored.length">
      <li v-for="group in monitored" :key="group.group_id">
        {{ group.name }} · {{ group.enabled ? '监听已启用' : '监听已暂停' }}
        <a-button size="small" :disabled="busy" @click="setGroup(group.group_id, group.name, !group.enabled)">
          {{ group.enabled ? '暂停' : '启用' }}
        </a-button>
        <a-button size="small" @click="rule.group_id = group.group_id">配置规则</a-button>
      </li>
    </ul>
    <p v-if="notice" class="message ok" role="status">{{ notice }}</p>
  </section>
  <section class="card">
    <h2>群内关键词规则</h2>
    <form @submit.prevent="save">
      <label>群 ID
        <input v-model.trim="rule.group_id" list="reader-groups" required />
        <datalist id="reader-groups">
          <option v-for="group in groups" :key="group.id" :value="group.id">{{ group.name }}</option>
          <option v-for="group in monitored" :key="group.group_id" :value="group.group_id">{{ group.name }}</option>
        </datalist>
      </label>
      <label>关键词（整条匹配）<input v-model.trim="rule.keyword" required placeholder="测试" /></label>
      <label>处理函数
        <select v-model="rule.handler">
          <option v-for="handler in handlers" :key="handler" :value="handler">
            {{ handler === 'webhook' ? 'HTTP 回调' : handler === 'fixed_reply' ? '固定回复 · 测试成功' : handler }}
          </option>
        </select>
      </label>
      <label v-if="rule.handler === 'webhook'">回调地址
        <input v-model.trim="rule.url" type="url" required placeholder="http://127.0.0.1:9000/hook" />
      </label>
      <label class="check"><input v-model="rule.enabled" type="checkbox" />启用规则</label>
      <a-button type="primary" html-type="submit" :disabled="busy || !handlers.length">保存规则</a-button>
    </form>
    <label>试跑文本 <input v-model="sample" maxlength="4000" /></label>
    <a-button :disabled="busy || !rule.group_id || !rule.keyword" @click="previewCurrentRule">试跑当前规则</a-button>
    <template v-if="monitoring?.mock_enabled">
      <a-button :disabled="busy || !rule.group_id || !sample.trim()" @click="submitMockMessage">投递模拟消息</a-button>
      <p>模拟投递使用已保存的规则，会调用匹配的 HTTP 回调并记录结果。</p>
    </template>
    <div v-if="preview" role="status">
      <p>{{ preview.matched ? '关键词命中' : '关键词未命中' }}</p>
      <p v-if="preview.matched && preview.handler === 'webhook'">真实消息命中时将发送 HTTP 回调。试跑只检查匹配。</p>
      <pre v-if="preview.reply">{{ JSON.stringify(preview.reply, null, 2) }}</pre>
    </div>
    <p v-if="error" class="message error" role="alert">{{ error }}</p>
    <ul v-if="rules.length">
      <li v-for="item in rules" :key="JSON.stringify([item.group_id, item.keyword])">
        {{ item.group_id }} · {{ item.keyword }} · {{ item.handler }} · {{ item.enabled ? '启用' : '停用' }}
        <a-button :disabled="busy" @click="rule = { ...item }">编辑</a-button>
      </li>
    </ul>
  </section>
  <section class="card">
    <div class="card-head">
      <h2>规则执行和待发送记录</h2>
      <a-button :disabled="busy" @click="run(load)">刷新</a-button>
    </div>
    <div v-if="monitoring">
      <p>监听{{ monitoring.enabled ? '已启用' : '已停用' }}{{ monitoring.running ? ' · 正在读取' : '' }}</p>
      <form @submit.prevent="saveInterval">
        <label>轮询间隔（秒）
          <input v-model.number="interval" type="number" min="1" max="100" step="1" required />
        </label>
        <a-button html-type="submit" :disabled="busy">保存间隔</a-button>
      </form>
      <p>当前全局间隔 {{ monitoring.interval_seconds }} 秒。仅轮询已启用的群，群内只执行已启用的规则。</p>
      <p v-if="monitoring.last_success_at">最近成功读取 {{ new Date(monitoring.last_success_at).toLocaleString() }}</p>
      <p v-if="monitoring.error" class="message error" role="alert">{{ monitoring.error }}</p>
      <a-button :disabled="busy" @click="toggleMonitoring">{{ monitoring.enabled ? '停止监听' : '启动监听' }}</a-button>
    </div>
    <p v-if="refreshError" class="message error" role="status">自动刷新失败：{{ refreshError }}</p>
    <p v-if="!deliveries.length">暂无执行记录。</p>
    <ul v-else>
      <li v-for="item in deliveries" :key="item.id">
        {{ item.created_at }} UTC · {{ item.group_id }} · {{ item.handler }} · {{ item.status }}
        <span v-if="item.error"> · {{ item.error }}</span>
        <a-button v-if="item.status === 'failed'" :disabled="busy" @click="retry(item.id)">重试</a-button>
        <details v-if="item.task">
          <summary>待发送任务</summary>
          <pre>{{ JSON.stringify(JSON.parse(item.task), null, 2) }}</pre>
        </details>
      </li>
    </ul>
  </section>
  </main>
</template>
