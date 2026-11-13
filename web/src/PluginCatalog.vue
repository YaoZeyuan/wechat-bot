<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Card as ACard, Empty as AEmpty, InputSearch as AInputSearch, Spin as ASpin } from '@arco-design/web-vue'
import { IconApps, IconSettings, IconRefresh } from '@arco-design/web-vue/es/icon'
import { api } from './api'

const route = useRoute()
const router = useRouter()
const plugins = ref<Awaited<ReturnType<typeof api.pluginCatalog>>>([])
const search = ref('')
const loading = ref(false)
const error = ref('')
const groupId = computed(() => typeof route.query.group === 'string' ? route.query.group : '')
const filtered = computed(() => {
  const term = search.value.trim().toLowerCase()
  return plugins.value.filter(plugin => [plugin.name, plugin.id, plugin.description, plugin.capability]
    .some(value => value.toLowerCase().includes(term)))
})
const active = computed(() => plugins.value.find(plugin => plugin.id === route.query.plugin) ?? filtered.value[0])

function select(plugin: string, group = groupId.value) {
  void router.replace({ query: { ...(plugin ? { plugin } : {}), ...(group ? { group } : {}) } })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    plugins.value = await api.pluginCatalog()
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    loading.value = false
  }
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
        <RouterLink :to="{ path: '/group', query: groupId ? { group: groupId, plugin: active.id } : {} }">前往群配置</RouterLink>
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
