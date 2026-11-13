<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Card as ACard, Empty as AEmpty, InputSearch as AInputSearch, Select as ASelect, Spin as ASpin } from '@arco-design/web-vue'
import { IconApps, IconSettings, IconRefresh } from '@arco-design/web-vue/es/icon'
import { api, type GroupPlugin, type MonitoredGroup } from './api'
import PluginSettings from './PluginSettings.vue'
import AiChatSettings from './AiChatSettings.vue'
import SummaryPreview from './SummaryPreview.vue'

const route = useRoute()
const router = useRouter()
const plugins = ref<Awaited<ReturnType<typeof api.pluginCatalog>>>([])
const groups = ref<MonitoredGroup[]>([])
const groupPlugins = ref<GroupPlugin[]>([])
const search = ref('')
const loading = ref(false)
const groupLoading = ref(false)
const error = ref('')
const groupError = ref('')
const groupId = computed(() => typeof route.query.group === 'string' ? route.query.group : '')
const filtered = computed(() => {
  const term = search.value.trim().toLowerCase()
  return plugins.value.filter(plugin => [plugin.name, plugin.id, plugin.description, plugin.capability]
    .some(value => value.toLowerCase().includes(term)))
})
const active = computed(() => plugins.value.find(plugin => plugin.id === route.query.plugin) ?? filtered.value[0])
const settings = computed(() => groupPlugins.value.find(plugin => plugin.id === active.value?.id))
const groupOptions = computed(() => groups.value.map(group => ({
  value: group.group_id,
  label: (group.remark || group.original_name || group.name) + (group.enabled ? '' : '（已暂停）'),
})))

function select(plugin: string, group = groupId.value) {
  void router.replace({ query: { ...(plugin ? { plugin } : {}), ...(group ? { group } : {}) } })
}

async function loadGroup(id: string, onCleanup?: (cleanup: () => void) => void) {
  let stale = false
  onCleanup?.(() => { stale = true })
  groupPlugins.value = []
  groupError.value = ''
  groupLoading.value = !!id
  if (!id) return
  try {
    const result = await api.groupPlugins(id)
    if (!stale && groupId.value === id) groupPlugins.value = result
  } catch (cause) {
    if (!stale) groupError.value = (cause as Error).message
  } finally {
    if (!stale) groupLoading.value = false
  }
}
watch(groupId, (id, _, cleanup) => void loadGroup(id, cleanup), { immediate: true })

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [catalog, monitored] = await Promise.all([api.pluginCatalog(), api.monitoredGroups()])
    plugins.value = catalog
    groups.value = monitored
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    loading.value = false
  }
}
function update(plugin: GroupPlugin) {
  groupPlugins.value = groupPlugins.value.map(item => item.id === plugin.id ? plugin : item)
}
onMounted(load)
</script>

<template>
  <main class="page plugin-page">
    <header class="plugin-header">
      <div><h1>插件管理</h1><p>浏览插件，按群配置启用状态和参数。</p></div>
      <a-button :loading="loading" @click="load"><template #icon><icon-refresh /></template>刷新目录</a-button>
    </header>
    <a-alert v-if="error" type="error">{{ error }}</a-alert>
    <div class="workspace">
      <a-card class="catalog" :bordered="false">
        <template #title><icon-apps /> 插件目录 <a-tag>{{ plugins.length }}</a-tag></template>
        <a-input-search v-model="search" placeholder="搜索插件名称或 ID" aria-label="搜索插件" allow-clear />
        <a-spin :loading="loading">
          <ul class="plugin-list">
            <li v-for="plugin in filtered" :key="plugin.id">
              <button type="button" :aria-pressed="active?.id === plugin.id" @click="select(plugin.id)">
                <span class="plugin-icon"><icon-apps /></span>
                <span class="list-label"><strong>{{ plugin.name }}</strong><small>{{ plugin.capability }}</small></span>
                <a-tag v-if="groupPlugins.find(item => item.id === plugin.id)?.enabled" size="small" color="green">启用</a-tag>
              </button>
            </li>
          </ul>
          <a-empty v-if="!loading && !filtered.length" description="没有匹配的插件" />
        </a-spin>
      </a-card>
      <a-card v-if="active" :bordered="false" class="detail">
        <div class="detail-title">
          <span class="plugin-icon large"><icon-settings /></span>
          <div><h2>{{ active.name }}</h2><small>{{ active.id }}</small></div>
          <a-tag color="arcoblue">v{{ active.version }}</a-tag>
        </div>
        <p>{{ active.description }}</p>
        <div class="detail-tags">
          <a-tag>{{ active.capability }}</a-tag>
          <a-tag>{{ active.default_enabled ? '默认启用' : '默认关闭' }}</a-tag>
        </div>
        <div class="scope">
          <label for="plugin-group">配置群聊</label>
          <a-select id="plugin-group" :model-value="groupId || undefined" :options="groupOptions"
            placeholder="请选择监听群" allow-search allow-clear
            @update:model-value="select(active.id, typeof $event === 'string' ? $event : '')" />
          <RouterLink v-if="groupId" :to="{ path: '/group', query: { group: groupId } }">打开群配置</RouterLink>
          <RouterLink v-else to="/rules">查看监听群</RouterLink>
        </div>
        <a-alert v-if="groupError" type="error">{{ groupError }}</a-alert>
        <a-spin v-if="groupLoading" />
        <PluginSettings v-else-if="settings" :key="`${groupId}:${settings.id}`" :plugin="settings" :group-id="groupId" @saved="update" />
        <p v-else-if="!groupError" class="muted">选择一个监听群后即可编辑此插件。</p>
        <AiChatSettings v-if="active.id === 'ai_chat'" />
        <SummaryPreview v-if="active.id === 'chat_summary'" :group-id="groupId" />
      </a-card>
    </div>
  </main>
</template>

<style scoped>
.plugin-page { max-width: 1260px; }
.plugin-header,.detail-title,.scope,.detail-tags { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.plugin-header { justify-content: space-between; margin-bottom: 20px; }
.plugin-header h1,.detail h2 { margin: 0; }
.plugin-header p,.muted,small { color: var(--color-text-3); }
.plugin-header p { margin: 5px 0 0; }
.workspace { display: grid; grid-template-columns: minmax(240px, 300px) minmax(0, 1fr); align-items: start; gap: 18px; }
.catalog,.detail { border: 1px solid var(--color-border-2); border-radius: 14px; }
.catalog :deep(.arco-spin) { display: block; }
.plugin-list { list-style: none; padding: 0; margin: 12px 0 0; display: grid; gap: 4px; }
.plugin-list button { width: 100%; display: flex; align-items: center; gap: 10px; text-align: left; border: 1px solid transparent;
  color: var(--color-text-1); padding: 8px; border-radius: 8px; background: transparent; cursor: pointer; }
.plugin-list button:hover { background: var(--color-fill-1); }
.plugin-list button[aria-pressed="true"] { background: var(--color-primary-light-1); border-color: var(--color-primary-light-3); }
.plugin-icon { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 8px; flex: none;
  color: rgb(var(--primary-6)); background: var(--color-primary-light-1); }
.plugin-icon.large { width: 48px; height: 48px; font-size: 21px; }
.list-label { display: grid; gap: 3px; flex: 1; }
.scope { margin: 20px 0 14px; }
.scope :deep(.arco-select-view) { flex: 0 1 280px; min-width: 0; }
.detail :deep(.arco-card-body) { padding: clamp(16px, 2.4vw, 28px); }
.detail-title > div { min-width: 0; overflow-wrap: anywhere; }
.detail > :deep(.arco-card-body) > a:last-child { display: inline-block; margin-top: 12px; }
@media (max-width: 760px) { .workspace { grid-template-columns: 1fr; } }
</style>
