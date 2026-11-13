<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Empty as AEmpty, InputSearch as AInputSearch } from '@arco-design/web-vue'
import { api, type Conversation, type MonitoredGroup } from './api'
import ReaderRules from './ReaderRules.vue'

const route = useRoute()
const groups = ref<MonitoredGroup[]>([])
const candidates = ref<Conversation[]>([])
const query = ref('')
const appliedQuery = ref('')
const hasMore = ref(false)
const adding = ref(false)
const removing = ref<MonitoredGroup>()
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const dialogError = ref('')

function name(group: MonitoredGroup) {
  return group.remark || group.original_name || group.name
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    groups.value = await api.monitoredGroups()
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    loading.value = false
  }
}

async function search(append = false) {
  if (busy.value) return
  busy.value = true
  dialogError.value = ''
  if (!append) {
    candidates.value = []
    appliedQuery.value = query.value.trim()
  }
  try {
    const result = await api.conversations(appliedQuery.value, 'group', candidates.value.length)
    candidates.value.push(...result.items)
    hasMore.value = result.has_more
  } catch (cause) {
    dialogError.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}

async function change(action: () => Promise<unknown>) {
  if (busy.value) return
  busy.value = true
  dialogError.value = ''
  try {
    await action()
    adding.value = false
    removing.value = undefined
    await load()
  } catch (cause) {
    dialogError.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}

function remove() {
  const group = removing.value
  if (group) void change(() => api.deleteMonitoredGroup(group.group_id))
}

onMounted(load)
</script>

<template>
  <ReaderRules v-if="route.query.group" />
  <main v-else class="page">
    <header class="topbar">
      <div><h1>群配置</h1><p class="sub">已启用 {{ groups.filter(group => group.enabled).length }} / 10 个群机器人</p></div>
      <a-button type="primary" :disabled="loading" @click="adding = true; query = ''; search()">添加群</a-button>
    </header>
    <a-alert v-if="error" type="error">{{ error }} <a-button @click="load">重试</a-button></a-alert>
    <section v-else class="card">
      <p v-if="loading">正在读取监听群…</p>
      <a-empty v-else-if="!groups.length" description="还没有配置机器人群" />
      <ul v-else class="group-list">
        <li v-for="group in groups" :key="group.group_id">
          <div class="identity"><strong>{{ name(group) }}</strong><small>{{ group.group_id }}</small></div>
          <a-tag :color="group.enabled ? 'green' : 'gray'">{{ group.enabled ? '已启用' : '已暂停' }}</a-tag>
          <RouterLink :to="{ path: '/group', query: { group: group.group_id } }">配置</RouterLink>
          <a-button size="small" status="danger" @click="dialogError = ''; removing = group">删除</a-button>
        </li>
      </ul>
    </section>
    <a-modal :visible="adding" title="添加监听群" :footer="false" :closable="!busy"
      :mask-closable="false" @cancel="!busy && (adding = false)">
      <a-input-search v-model="query" placeholder="搜索群名或群 ID" search-button :disabled="busy" @search="search()" />
      <a-alert v-if="dialogError" type="error">{{ dialogError }}</a-alert>
      <ul class="group-list candidates">
        <li v-for="group in candidates" :key="group.id">
          <div class="identity"><strong>{{ group.name }}</strong><small>{{ group.id }}</small></div>
          <a-button size="small" :disabled="busy || groups.some(item => item.group_id === group.id)"
            @click="change(() => api.saveMonitoredGroup({ group_id: group.id, name: group.name, enabled: true }))">
            {{ groups.some(item => item.group_id === group.id) ? '已添加' : '添加' }}
          </a-button>
        </li>
      </ul>
      <a-empty v-if="!busy && !dialogError && !candidates.length" description="没有匹配的群聊" />
      <a-button v-if="hasMore" :loading="busy" @click="search(true)">加载更多</a-button>
    </a-modal>
    <a-modal :visible="!!removing" title="删除监听群" :ok-loading="busy" :ok-button-props="{ status: 'danger' }"
      :mask-closable="false" ok-text="删除群配置" @ok="remove" @cancel="!busy && (removing = undefined)">
      <p>删除「{{ removing && name(removing) }}」后，该群的插件设置、触发规则和监听进度都会清空。重新添加时会使用默认设置。</p>
      <a-alert v-if="dialogError" type="error">{{ dialogError }}</a-alert>
    </a-modal>
  </main>
</template>

<style scoped>
.group-list { padding: 0; margin: 0; list-style: none; }
.group-list li { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; padding: 12px 0; }
.group-list li + li { border-top: 1px solid var(--console-border); }
.identity { flex: 1; min-width: 120px; overflow-wrap: anywhere; }
.identity small { display: block; color: var(--color-text-3); }
.candidates { max-height: 360px; overflow: auto; margin-top: 8px; }
</style>
