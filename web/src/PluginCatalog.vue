<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from './api'

const plugins = ref<Awaited<ReturnType<typeof api.pluginCatalog>>>([])
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    plugins.value = await api.pluginCatalog()
  } catch (err) {
    error.value = (err as Error).message
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <main class="page">
    <header class="card-head">
      <div><h1>插件管理</h1><p>已接入 {{ plugins.length }} 个插件。每个群可独立启停。</p></div>
      <a-button :loading="loading" @click="load">刷新</a-button>
    </header>
    <a-alert v-if="error" type="error">{{ error }}</a-alert>
    <div class="plugin-grid">
      <section v-for="plugin in plugins" :key="plugin.id" class="card">
        <div class="card-head"><h2>{{ plugin.name }}</h2><a-tag color="arcoblue">v{{ plugin.version }}</a-tag></div>
        <p>{{ plugin.description }}</p>
        <div class="plugin-meta">
          <a-tag>{{ plugin.capability }}</a-tag>
          <a-tag :color="plugin.default_enabled ? 'green' : 'gray'">{{ plugin.default_enabled ? '默认启用' : '默认关闭' }}</a-tag>
        </div>
        <small>{{ plugin.id }}</small>
      </section>
    </div>
    <RouterLink to="/rules">选择一个群配置插件</RouterLink>
  </main>
</template>

<style scoped>
.plugin-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }
.plugin-grid .card { margin: 0; }
.plugin-meta { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 8px; }
small { color: var(--color-text-3); }
</style>
