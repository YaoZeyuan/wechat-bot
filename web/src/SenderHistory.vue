<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type SenderAiLog, type SenderHistoryItem } from './api'

const records = ref<SenderHistoryItem[]>([])
const selected = ref<SenderHistoryItem>()
const detail = ref<Awaited<ReturnType<typeof api.senderJob>>>()
const aiLogs = ref<SenderAiLog[]>()
const aiError = ref('')
const offset = ref(0)
const busy = ref(false)
const error = ref('')
const sources: Record<string, string> = {
  manual: '手动发送', fixed_reply: '测试', daily_activity: '活跃日榜',
  total_activity: '活跃总榜', total_lurkers: '潜水总榜',
  ai_chat: 'AI 问答',
}
const statuses: Record<string, string> = {
  queued: '排队中', running: '执行中', completed: '完成', failed: '失败', uncertain: '待核对',
}
const dateFormat = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', dateStyle: 'short', timeStyle: 'medium',
})

async function load(page: number) {
  busy.value = true
  error.value = ''
  try {
    records.value = await api.senderHistory(page)
    offset.value = page
    if (selected.value && records.value.some(record => record.id === selected.value?.id)) {
      await select(selected.value)
    } else {
      selected.value = undefined
      detail.value = undefined
    }
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}

async function select(record: SenderHistoryItem) {
  selected.value = record
  detail.value = undefined
  aiLogs.value = undefined
  aiError.value = ''
  error.value = ''
  try {
    const job = await api.senderJob(record.id)
    if (selected.value?.id === record.id) detail.value = job
  } catch (cause) {
    if (selected.value?.id === record.id) error.value = cause instanceof Error ? cause.message : String(cause)
  }
  if (record.source === 'ai_chat' && selected.value?.id === record.id) {
    try {
      const logs = await api.senderHistoryAi(record.id)
      if (selected.value?.id === record.id) aiLogs.value = logs
    } catch (cause) {
      if (selected.value?.id === record.id) aiError.value = cause instanceof Error ? cause.message : String(cause)
    }
  }
}

onMounted(() => void load(0))
</script>

<template>
  <main class="page">
    <h1>Sender 日志</h1>
    <p class="hint">保留最近 1000 条任务。点击任务查看操作步骤和当时的手机画面。</p>
    <a-alert v-if="error" type="error">{{ error }}</a-alert>
    <div class="grid">
      <section class="card">
        <div class="row">
          <a-button :disabled="busy" @click="load(0)">刷新</a-button>
          <a-button :disabled="busy || offset === 0" @click="load(offset - 50)">上一页</a-button>
          <a-button :disabled="busy || records.length < 50 || offset >= 950" @click="load(offset + 50)">下一页</a-button>
        </div>
        <p v-if="!records.length && !busy">暂无发送任务。</p>
        <ul class="records">
          <li v-for="record in records" :key="record.id">
            <button type="button" :aria-pressed="selected?.id === record.id" @click="select(record)">
              <strong>{{ record.target.name }}</strong>
              <span>{{ sources[record.source] ?? record.source }} · {{ statuses[record.status] ?? record.status }}</span>
              <small v-if="record.created_ms">{{ dateFormat.format(record.created_ms) }}</small>
            </button>
          </li>
        </ul>
      </section>
      <section class="card">
        <h2>{{ selected?.target.name ?? '操作步骤' }}</h2>
        <p v-if="!selected">请先选择一条任务。</p>
        <section v-if="selected?.source === 'ai_chat'">
          <h3>模型调用</h3>
          <p v-if="aiError">{{ aiError }}</p>
          <p v-else-if="aiLogs?.length === 0">当前 Server 日志中没有这条任务的模型记录。</p>
          <p v-else-if="!aiLogs">正在读取模型日志…</p>
          <ol v-else>
            <li v-for="(entry, index) in aiLogs" :key="index">
              {{ entry.msg }} · {{ entry.provider }} / {{ entry.model }}
              <span v-if="entry.status"> · HTTP {{ entry.status }}</span>
              <span v-if="entry.elapsed_seconds !== undefined"> · {{ entry.elapsed_seconds }} 秒</span>
              <details v-if="entry.request_body || entry.response_body">
                <summary>{{ entry.request_body ? '查看请求' : '查看响应' }}</summary>
                <small v-if="entry.url">{{ entry.url }}</small>
                <pre>{{ entry.request_body || entry.response_body }}</pre>
              </details>
            </li>
          </ol>
          <h3>手机发送步骤</h3>
        </section>
        <p v-else-if="detail && !detail.steps.length">暂无操作步骤。</p>
        <ol v-if="detail">
          <li v-for="(step, index) in detail.steps" :key="index">
            {{ step }}
            <details v-if="detail.screens?.includes(index)">
              <summary>查看截图</summary>
              <img :src="api.senderHistoryScreen(selected!.id, index)" :alt="'第 ' + (index + 1) + ' 步截图'" loading="lazy" />
            </details>
          </li>
        </ol>
      </section>
    </div>
  </main>
</template>

<style scoped>
.records { list-style: none; padding: 0; }
.records button { width: 100%; text-align: left; padding: 8px; border: 1px solid var(--color-border-2); background: var(--color-bg-2); color: var(--color-text-1); cursor: pointer; }
.records button[aria-pressed="true"] { background: var(--color-fill-2); }
.records span, .records small { display: block; }
li { margin-block: 8px; }
img { display: block; max-width: 100%; max-height: 65vh; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; max-height: 320px; overflow: auto; }
</style>
