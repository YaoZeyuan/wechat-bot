<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Input as AInput, InputNumber as AInputNumber, InputTag as AInputTag, Select as ASelect, Switch as ASwitch } from '@arco-design/web-vue'
import { api, type AiChatRoute } from './api'

const props = defineProps<{ groupId: string }>()
const routes = ref<AiChatRoute[]>([])
const draft = ref<AiChatRoute>()
const busy = ref(false)
const error = ref('')
const saved = ref(false)
const options = computed(() => routes.value.map(route => ({ label: route.id, value: route.id })))
const dirty = computed(() => !!draft.value &&
  JSON.stringify(draft.value) !== JSON.stringify(routes.value.find(route => route.id === draft.value?.id)))
const thinking = [{ label: '关闭', value: 'disabled' }, { label: '开启', value: 'enabled' }, { label: '服务商默认', value: 'default' }]

function select(id: string) {
  const route = routes.value.find(item => item.id === id)
  draft.value = route && { ...route, model: { ...route.model }, prefixes: [...route.prefixes], strip_prefixes: [...route.strip_prefixes] }
  saved.value = false
  error.value = ''
}

async function load() {
  busy.value = true
  error.value = ''
  try {
    routes.value = await api.aiChatRoutes(props.groupId)
    select(routes.value[0]?.id ?? '')
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}

async function save() {
  if (!draft.value || !dirty.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const id = draft.value.id
    routes.value = await api.saveAiChatRoute(props.groupId, draft.value)
    select(id)
    saved.value = true
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}
onMounted(load)
</script>

<template>
  <section class="routes">
    <h3>模型与触发词</h3>
    <p>这些参数仅应用于当前群聊。</p>
    <a-alert v-if="error" type="error">{{ error }}</a-alert>
    <form v-if="draft" @submit.prevent="save">
      <label>模型路由
        <a-select :model-value="draft.id" :options="options" :disabled="busy || dirty"
          @update:model-value="typeof $event === 'string' && select($event)" />
      </label>
      <label>模型 ID <a-input v-model.trim="draft.model.id" :disabled="busy" /></label>
      <label>API Base <a-input v-model.trim="draft.base_url" :disabled="busy" /></label>
      <label>超时（秒） <a-input-number v-model="draft.timeout_seconds" :min="1" :max="600" :disabled="busy" /></label>
      <label>深度思考
        <a-select :model-value="draft.model.thinking ?? 'default'" :options="thinking" :disabled="busy"
          @update:model-value="draft.model.thinking = $event === 'enabled' || $event === 'disabled' ? $event : null" />
      </label>
      <label v-if="draft.provider === 'doubao'">联网搜索 <a-switch v-model="draft.model.web_search" :disabled="busy" /></label>
      <label class="wide">保留的触发词 <a-input-tag v-model="draft.prefixes" :disabled="busy" placeholder="输入后按回车" /></label>
      <label class="wide">从提问中移除的触发词 <a-input-tag v-model="draft.strip_prefixes" :disabled="busy" placeholder="输入后按回车" /></label>
      <div class="footer wide">
        <span role="status">{{ dirty ? '有未保存的更改' : saved ? '配置已保存' : draft.has_api_key ? '已配置 API Key' : '请在本地配置文件中填写 API Key' }}</span>
        <a-button :disabled="!dirty || busy" @click="select(draft.id)">还原更改</a-button>
        <a-button type="primary" html-type="submit" :disabled="!dirty" :loading="busy">保存模型配置</a-button>
      </div>
    </form>
    <a-button v-else-if="error" @click="load">重新加载</a-button>
  </section>
</template>

<style scoped>
.routes { margin-top: 22px; padding: 16px; background: var(--color-fill-1); border-radius: 12px; }
h3 { margin: 0; }
p,.footer span { color: var(--color-text-3); }
form { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); margin-top: 12px; }
label { display: grid; gap: 5px; align-content: start; }
.wide { grid-column: 1 / -1; }
.footer { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.footer span { flex: 1; }
</style>
