<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, type GlobalAiSettings } from './api'

const settings = ref<GlobalAiSettings>()
const apiKey = ref('')
const busy = ref(false)
const error = ref('')
const saved = ref(false)

async function load() {
  busy.value = true
  error.value = ''
  try {
    settings.value = await api.globalAi()
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}

async function save() {
  if (!settings.value || busy.value) return
  busy.value = true
  error.value = ''
  saved.value = false
  const { has_api_key, ...payload } = settings.value
  try {
    settings.value = await api.saveGlobalAi({ ...payload, api_key: apiKey.value })
    apiKey.value = ''
    saved.value = true
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}

onMounted(load)
</script>

<template>
  <section class="card" aria-labelledby="global-ai-heading">
    <h2 id="global-ai-heading">全局 AI</h2>
    <p>聊天梳理和没有专用模型配置的插件使用此模型。</p>
    <a-alert v-if="error" type="error">{{ error }}</a-alert>
    <a-alert v-if="saved" type="success">已保存全局 AI 配置。</a-alert>
    <form v-if="settings" class="ai-form" @submit.prevent="save">
      <label>服务商
        <select v-model="settings.provider"><option value="deepseek">DeepSeek</option><option value="doubao">豆包</option><option value="compatible">其他兼容服务</option></select>
      </label>
      <label>API Base <input v-model.trim="settings.base_url" type="url" required /></label>
      <label>模型 ID <input v-model.trim="settings.model" required /></label>
      <label>API Key <input v-model="apiKey" type="password" autocomplete="new-password"
        :required="!settings.has_api_key" :placeholder="settings.has_api_key ? '已配置，留空保留' : ''" /></label>
      <label>深度思考
        <select v-model="settings.thinking"><option value="disabled">关闭</option><option value="enabled">开启</option><option :value="null">服务商默认</option></select>
      </label>
      <label>超时（秒） <input v-model.number="settings.timeout_seconds" type="number" min="1" max="600" required /></label>
      <label class="prompt">群聊梳理提示词 <textarea v-model="settings.summary_prompt" rows="14" maxlength="16000" required /></label>
      <a-button html-type="submit" type="primary" :loading="busy">保存 AI 配置</a-button>
    </form>
    <a-button v-else-if="error" @click="load">重新读取</a-button>
  </section>
</template>

<style scoped>
.ai-form { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; align-items: start; }
.ai-form label { display: grid; gap: 4px; }
.prompt { grid-column: 1 / -1; }
.ai-form button { justify-self: start; }
</style>
