<script setup lang="ts">
import { Switch as ASwitch, Card as ACard } from '@arco-design/web-vue'
import PluginSettings from './PluginSettings.vue'
import AiChatSettings from './AiChatSettings.vue'
import SummaryPreview from './SummaryPreview.vue'
import GlobalAiConfig from './GlobalAiConfig.vue'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, type GroupPlugin, type MonitoredGroup } from './api'

const route = useRoute()
const isGroupPage = computed(() => route.path === '/group')
const groupId = computed(() => typeof route.query.group === 'string' ? route.query.group : '')
const busy = ref(false)
const error = ref('')
const notice = ref('')
const monitoring = ref<Awaited<ReturnType<typeof api.readerStatus>>>()
const interval = ref<number | ''>()
const monitored = ref<MonitoredGroup[]>([])
const currentGroup = computed(() => monitored.value.find(group => group.group_id === groupId.value))
const enabledCount = computed(() => monitored.value.filter(group => group.enabled).length)
const switching = ref(false)
const sections = computed(() => {
  const groups = new Map<string, GroupPlugin[]>()
  const sorted = [...plugins.value].sort((a, b) =>
    (b.order ?? 0) - (a.order ?? 0) || a.name.localeCompare(b.name, 'zh-CN'))
  for (const plugin of sorted) {
    const name = plugin.group || '其他'
    if (!groups.has(name)) groups.set(name, [])
    groups.get(name)!.push(plugin)
  }
  return groups
})
const detailPlugin = computed(() => plugins.value.find(plugin => plugin.id === activePlugin.value))
function updatePlugin(updated: GroupPlugin) {
  plugins.value = plugins.value.map(item => item.id === updated.id ? updated : item)
}
async function togglePlugin(plugin: GroupPlugin, enabled: boolean) {
  const id = groupId.value
  switching.value = true
  pluginError.value = ''
  try {
    const updated = await api.saveGroupPlugin(id, plugin.id, enabled, plugin.assistant_name, plugin.intro_reply)
    if (groupId.value === id) updatePlugin(updated)
  } catch (cause) {
    if (groupId.value === id) pluginError.value = (cause as Error).message
  } finally {
    switching.value = false
  }
}
const activePlugin = ref('')
const plugins = ref<GroupPlugin[]>([])
const pluginError = ref('')
const refreshError = ref('')
let refreshTimer: number | undefined
let refreshing = false

watch(() => currentGroup.value?.group_id, async (id, _, onCleanup) => {
  let stale = false
  onCleanup(() => { stale = true })
  activePlugin.value = typeof route.query.plugin === 'string' ? route.query.plugin : ''
  plugins.value = []
  pluginError.value = ''
  if (!id) return
  try {
    const result = await api.groupPlugins(id)
    if (!stale) plugins.value = result
  } catch (err) {
    if (!stale) pluginError.value = (err as Error).message
  }
}, { immediate: true })

async function load() {
  const [status, groupSettings] = await Promise.all([
    api.readerStatus(), api.monitoredGroups(),
  ])
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

function setGroup(group_id: string, name: string, enabled: boolean) {
  return run(async () => {
    await api.saveMonitoredGroup({ group_id, name, enabled })
    monitored.value = await api.monitoredGroups()
    notice.value = enabled ? '已启用该群监听。' : '已暂停该群监听，插件配置已保留。'
  })
}

async function refreshMonitoring() {
  if (busy.value || refreshing || document.hidden) return
  refreshing = true
  try {
    const status = await api.readerStatus()
    if (!busy.value) {
      monitoring.value = status
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
  <main class="page rules-page">
    <header class="page-heading">
      <RouterLink v-if="isGroupPage" to="/group">返回监听群列表</RouterLink>
      <h1>{{ isGroupPage ? '群机器人配置' : '全局配置' }}</h1>
      <p v-if="!isGroupPage">配置全局监听和默认 AI 模型，或从监听群列表进入群机器人配置。</p>
    </header>

    <a-alert v-if="error" type="error">{{ error }}</a-alert>
    <a-alert v-if="notice" type="success">{{ notice }}</a-alert>

    <GlobalAiConfig v-if="!isGroupPage" />
    <section v-if="!isGroupPage" class="card" aria-labelledby="global-heading">
      <div class="card-head">
        <h2 id="global-heading">全局监听</h2>
        <a-tag v-if="monitoring" :color="monitoring.enabled ? 'green' : 'gray'">
          {{ monitoring.running ? '正在读取' : monitoring.enabled ? '监听开启' : '监听停止' }}
        </a-tag>
      </div>
      <template v-if="monitoring">
        <form class="inline-form" @submit.prevent="saveInterval">
          <label for="poll-interval">轮询间隔</label>
          <input id="poll-interval" v-model.number="interval" type="number" min="1" max="100" step="1" required />
          <span>秒</span>
          <a-button html-type="submit" :disabled="busy || interval === monitoring.interval_seconds">保存间隔</a-button>
          <a-button :type="monitoring.enabled ? 'secondary' : 'primary'" :disabled="busy || (!monitoring.enabled && !enabledCount)"
            @click="toggleMonitoring">{{ monitoring.enabled ? '停止监听' : '启动监听' }}</a-button>
        </form>
        <p class="muted">当前每 {{ monitoring.interval_seconds }} 秒读取一次 · 已启用 {{ enabledCount }} / 10 个群</p>
        <p v-if="monitoring.last_success_at" class="muted">最近成功读取 {{ new Date(monitoring.last_success_at).toLocaleString() }}</p>
        <a-alert v-if="monitoring.error" type="error">{{ monitoring.error }}</a-alert>
      </template>
      <p v-else class="muted">正在读取监听设置…</p>
    </section>

      <section v-if="!isGroupPage" class="card" aria-labelledby="groups-heading">
        <h2 id="groups-heading">监听群</h2>
        <p v-if="!monitored.length" class="muted">还没有监听群。请在聊天记录中打开一个群，点击「启用机器人」。</p>
        <RouterLink to="/history">前往聊天记录</RouterLink>
        <template v-if="monitored.length">
        <ul class="compact-list">
          <li v-for="group in monitored" :key="group.group_id">
            <RouterLink class="identity" :to="{ path: '/group', query: { group: group.group_id } }">
              <strong>{{ group.remark || group.original_name || group.name }}</strong>
              <small v-if="group.remark && group.original_name && group.original_name !== group.remark">{{ group.original_name }}</small>
              <small>{{ group.group_id }}</small>
            </RouterLink>
            <a-tag :color="group.enabled ? 'green' : 'gray'">{{ group.enabled ? '启用' : '暂停' }}</a-tag>
            <RouterLink :to="{ path: '/group', query: { group: group.group_id } }">配置</RouterLink>
          </li>
        </ul>
        </template>
      </section>

      <section v-else class="card" aria-labelledby="rules-heading">
        <div class="card-head">
          <h2 id="rules-heading">群机器人</h2>
          <a-button v-if="currentGroup" size="small" :disabled="busy"
            @click="setGroup(currentGroup.group_id, currentGroup.name, !currentGroup.enabled)">
            {{ currentGroup.enabled ? '暂停此群' : '启用此群' }}
          </a-button>
        </div>
        <template v-if="currentGroup">
          <div class="group-context">
            <strong>{{ currentGroup.remark || currentGroup.original_name || currentGroup.name }}</strong>
            <small v-if="currentGroup.remark && currentGroup.original_name && currentGroup.original_name !== currentGroup.remark">{{ currentGroup.original_name }}</small>
            <small>{{ currentGroup.group_id }}</small>
            <RouterLink :to="{ path: '/history', query: { session: currentGroup.group_id } }">查看群聊记录</RouterLink>
          </div>
          <h3>此群的插件</h3>
          <a-alert v-if="pluginError" type="error">{{ pluginError }}</a-alert>
          <section v-for="[name, items] in sections" :key="name" class="plugin-section">
            <h4>{{ name }}</h4>
            <div class="plugin-cards">
              <a-card v-for="plugin in items" :key="plugin.id" class="plugin-card">
                <div class="card-head">
                  <strong>{{ plugin.name }}</strong>
                  <a-switch :model-value="plugin.enabled" :disabled="switching"
                    :aria-label="plugin.name" @change="value => togglePlugin(plugin, value === true)" />
                </div>
                <p>{{ plugin.description }}</p>
                <a-button size="small" @click="activePlugin = plugin.id">查看详情</a-button>
              </a-card>
            </div>
          </section>
          <a-modal :visible="!!detailPlugin" :title="detailPlugin?.name" :footer="false"
            :width="'min(680px, 94vw)'" @cancel="activePlugin = ''">
            <template v-if="detailPlugin">
              <p>{{ detailPlugin.description }}</p>
              <PluginSettings v-if="detailPlugin.fields?.length" :key="groupId + ':' + detailPlugin.id"
                :plugin="detailPlugin" :group-id="groupId" @saved="updatePlugin" />
              <AiChatSettings v-if="detailPlugin.id === 'ai_chat'" :key="groupId" :group-id="groupId" />
              <SummaryPreview v-if="detailPlugin.id === 'chat_summary'" :group-id="groupId" />
            </template>
          </a-modal>
        </template>
        <p v-else class="muted">{{ busy ? '正在加载群配置…' : groupId ? '没有找到该群配置。请先在聊天记录中为该群启用机器人。' : '请从监听群列表选择一个群。' }}</p>
      </section>

  </main>
</template>

<style scoped>
.plugin-section h4 { margin: 18px 0 8px; }
.plugin-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 255px), 1fr)); gap: 12px; }
.plugin-card { border-radius: 10px; }
.plugin-card p { color: var(--color-text-2); min-height: 2.8em; }

.rules-page { display: grid; gap: 16px; }
.rules-page .card { margin: 0; min-width: 0; }
.page-heading p, .muted, small { color: var(--color-text-3); }
.page-heading p { margin: 4px 0 0; }
.inline-form { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.inline-form label, .inline-form input { margin: 0; }
.inline-form input { width: 76px; }
.compact-list { list-style: none; margin: 12px 0 0; padding: 0; }
.compact-list li { display: flex; align-items: center; gap: 8px; padding: 9px 8px; border-bottom: 1px solid var(--color-border-2); }
.identity { min-width: 0; flex: 1; overflow-wrap: anywhere; }
.identity small, .group-context small { display: block; }
.test-tools summary { cursor: pointer; }
</style>
