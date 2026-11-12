<script setup lang="ts">
import GlobalAiConfig from './GlobalAiConfig.vue'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, type GroupPlugin, type KeywordRule, type MonitoredGroup } from './api'

const route = useRoute()
const isGroupPage = computed(() => route.path === '/group')
const groupId = computed(() => typeof route.query.group === 'string' ? route.query.group : '')
const initialGroup = groupId.value
const rule = ref<KeywordRule>({ group_id: initialGroup, keyword: '', handler: 'open_baidu', url: null, enabled: true })
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
const monitored = ref<MonitoredGroup[]>([])
const editing = ref(false)
const currentGroup = computed(() => monitored.value.find(group => group.group_id === rule.value.group_id))
const currentRules = computed(() => rules.value.filter(item => item.group_id === rule.value.group_id))
const currentDeliveries = computed(() => deliveries.value.filter(item => item.group_id === groupId.value))
const enabledCount = computed(() => monitored.value.filter(group => group.enabled).length)
const plugins = ref<GroupPlugin[]>([])
const pluginError = ref('')
const refreshError = ref('')
let refreshTimer: number | undefined
let refreshing = false

function selectGroup(id: string) {
  rule.value = { group_id: id, keyword: '', handler: 'open_baidu', url: null, enabled: true }
  editing.value = false
  preview.value = undefined
}

function editRule(item: KeywordRule) {
  rule.value = { ...item }
  editing.value = true
  preview.value = undefined
}

watch(groupId, (id) => {
  if (id !== rule.value.group_id) selectGroup(id)
})
watch(() => currentGroup.value?.group_id, async (id, _, onCleanup) => {
  let stale = false
  onCleanup(() => { stale = true })
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

function savePlugin(plugin: GroupPlugin, enabled: boolean) {
  const id = currentGroup.value?.group_id
  if (!id) return
  return run(async () => {
    const updated = await api.saveGroupPlugin(id, plugin.id, enabled, plugin.assistant_name, plugin.intro_reply)
    if (currentGroup.value?.group_id === id) {
      plugins.value = plugins.value.map(item => item.id === updated.id ? updated : item)
      if (updated.id === 'open_baidu' && updated.enabled) {
        rules.value = (await api.readerRules()).items
        const trigger = rules.value.find(rule => rule.group_id === id && rule.keyword === '测试')
        if (trigger?.handler !== 'open_baidu') {
          notice.value = '「测试」已被其他规则使用。可编辑该规则，把动作切换到「打开百度」。'
        } else {
          notice.value = '已启用「打开百度」。群收到「测试」时会在电脑打开网页。'
        }
      }
    }
  })
}

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
      url: null,
    })
    await load()
    notice.value = '规则已保存。'
  })
}

function deleteRule(item: KeywordRule) {
  if (!window.confirm(`删除触发规则「${item.keyword}」？`)) return
  return run(async () => {
    await api.deleteReaderRule(item.group_id, item.keyword)
    rules.value = rules.value.filter(rule => rule.group_id !== item.group_id || rule.keyword !== item.keyword)
    if (editing.value && rule.value.group_id === item.group_id && rule.value.keyword === item.keyword) {
      selectGroup(item.group_id)
    }
    notice.value = '规则已删除。'
  })
}

function previewCurrentRule() {
  preview.value = undefined
  return run(async () => {
    preview.value = await api.previewReaderRule({
      ...rule.value,
      url: null,
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
  <main class="page rules-page">
    <header class="page-heading">
      <RouterLink v-if="isGroupPage" to="/rules">返回监听群列表</RouterLink>
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
          <h2 id="rules-heading">群内关键词规则</h2>
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
          <ul v-if="plugins.length" class="compact-list">
            <li v-for="plugin in plugins" :key="plugin.id">
              <div class="identity"><strong>{{ plugin.name }}</strong><small>{{ plugin.description }}</small></div>
              <div v-if="plugin.id === 'self_intro'" class="identity">
                <label>助手名字 <input v-model.trim="plugin.assistant_name" maxlength="80" placeholder="张八旦" :disabled="busy" /></label>
                <label>介绍文案 <textarea v-model="plugin.intro_reply" maxlength="2500" rows="3" :disabled="busy" /></label>
                <a-button size="small" :disabled="busy" @click="savePlugin(plugin, plugin.enabled)">保存参数</a-button>
              </div>
              <a-tag :color="plugin.enabled ? 'green' : 'gray'">{{ plugin.enabled ? '启用' : '停用' }}</a-tag>
              <a-button size="small" :disabled="busy" @click="savePlugin(plugin, !plugin.enabled)">{{ plugin.enabled ? '停用' : '启用' }}</a-button>
            </li>
          </ul>
          <h3>触发规则</h3>
          <ul v-if="currentRules.length" class="compact-list">
            <li v-for="item in currentRules" :key="item.keyword">
              <div class="identity"><strong>{{ item.keyword }}</strong><small>{{ plugins.find(plugin => plugin.id === item.handler)?.name || item.handler }}</small></div>
              <a-tag v-if="item.enabled && plugins.some(plugin => plugin.id === item.handler && !plugin.enabled)" color="gray">插件停用</a-tag>
              <a-tag v-else :color="item.enabled ? 'green' : 'gray'">{{ item.enabled ? '启用' : '停用' }}</a-tag>
              <a-button size="small" :disabled="busy" @click="editRule(item)">编辑</a-button>
              <a-button size="small" status="danger" :disabled="busy" @click="deleteRule(item)">删除</a-button>
            </li>
          </ul>
          <p v-else class="muted">此群还没有规则，可以在下方添加。</p>

          <form class="rule-form" @submit.prevent="save">
            <div class="card-head">
              <h3>{{ editing ? '编辑规则' : '新增规则' }}</h3>
              <a-button v-if="editing" size="small" type="text" @click="selectGroup(rule.group_id)">新增规则</a-button>
            </div>
            <label>关键词（整条匹配）<input v-model.trim="rule.keyword" :readonly="editing" required placeholder="例如：测试" /></label>
            <label>命中后的动作
              <select v-model="rule.handler">
                <option v-for="handler in handlers" :key="handler" :value="handler">
                  {{ plugins.find(plugin => plugin.id === handler)?.name || handler }}
                </option>
              </select>
            </label>
            <label class="check"><input v-model="rule.enabled" type="checkbox" />启用此规则</label>
            <a-button type="primary" html-type="submit" :disabled="busy || !handlers.length">保存规则</a-button>
          </form>

          <details class="test-tools">
            <summary>试跑与模拟消息</summary>
            <label>试跑文本 <input v-model="sample" maxlength="4000" /></label>
            <a-button :disabled="busy || !rule.keyword" @click="previewCurrentRule">试跑当前规则</a-button>
            <template v-if="monitoring?.mock_enabled">
              <a-button :disabled="busy || !sample.trim()" @click="submitMockMessage">投递模拟消息</a-button>
            </template>
            <div v-if="preview" role="status">
              <p>{{ preview.matched ? '关键词命中' : '关键词未命中' }}</p>
              <pre v-if="preview.reply">{{ JSON.stringify(preview.reply, null, 2) }}</pre>
            </div>
          </details>
        </template>
        <p v-else class="muted">{{ busy ? '正在加载群配置…' : groupId ? '没有找到该群配置。请先在聊天记录中为该群启用机器人。' : '请从监听群列表选择一个群。' }}</p>
      </section>

    <section v-if="isGroupPage && currentGroup" class="card" aria-labelledby="records-heading">
      <div class="card-head">
        <h2 id="records-heading">执行记录与待发送任务</h2>
        <a-button :disabled="busy" @click="run(load)">刷新</a-button>
      </div>
      <p v-if="refreshError" class="message error" role="status">自动刷新失败：{{ refreshError }}</p>
      <p v-if="!currentDeliveries.length" class="muted">暂无执行记录。</p>
      <ul v-else class="compact-list deliveries">
        <li v-for="item in currentDeliveries" :key="item.id">
          <div class="identity">
            <strong>{{ monitored.find(group => group.group_id === item.group_id)?.name || item.group_id }}</strong>
            <small>{{ item.created_at }} UTC · {{ item.handler }}</small>
            <span v-if="item.error">{{ item.error }}</span>
            <details v-if="item.task">
              <summary>待发送任务</summary>
              <pre>{{ JSON.stringify(JSON.parse(item.task), null, 2) }}</pre>
            </details>
          </div>
          <a-tag>{{ item.status }}</a-tag>
          <a-button v-if="item.status === 'failed'" :disabled="busy" @click="retry(item.id)">重试</a-button>
        </li>
      </ul>
    </section>
  </main>
</template>

<style scoped>
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
.rule-form, .test-tools { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--color-border-2); }
.rule-form select { display: block; margin-top: 4px; }
.rule-form h3 { margin: 0 0 8px; }
.test-tools summary { cursor: pointer; }
.deliveries pre { max-width: 100%; overflow: auto; white-space: pre-wrap; }
</style>
