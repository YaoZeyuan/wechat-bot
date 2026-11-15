<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Card as ACard, Input as AInput, Textarea as ATextarea } from '@arco-design/web-vue'
import { api, type GroupPlugin } from './api'

const props = defineProps<{ plugin: GroupPlugin; groupId: string }>()
const emit = defineEmits<{ saved: [GroupPlugin] }>()
const draft = ref({ ...props.plugin })
const trial = computed(() => props.plugin.id === 'chat_trial')
const tab = ref('models')
const tabs = [
  { id: 'models', label: '参数与模型', fields: ['trial_hours', 'trial_limit', 'trial_cooldown', 'trial_judges', 'trial_clerk', 'trial_auto_warn', 'trial_warning_hours'] },
  { id: 'prompts', label: '提示词', fields: ['trial_intro', 'trial_prompt', 'trial_summary'] },
]
const fields = computed(() => (props.plugin.fields ?? []).filter(field =>
  !trial.value || tabs.find(item => item.id === tab.value)?.fields.includes(field.key)))
const busy = ref(false)
const feedback = ref('')
const error = ref('')
const requestPreview = ref('')
const loadingPreview = ref(false)
const generatingReport = ref(false)
const reportUrl = ref('')

const reportSteps = ref<string[]>([])
let disposed = false
onUnmounted(() => { disposed = true })
onMounted(() => { if (trial.value) void followReport(false) })

async function followReport(start: boolean) {
  generatingReport.value = true
  error.value = ''
  try {
    let report = await (start ? api.generateTrialPreview : api.trialPreviewStatus)(props.groupId)
    while (!disposed) {
      reportSteps.value = report.steps
      reportUrl.value = report.url ?? ''
      if (report.status !== 'running') break
      await new Promise(resolve => window.setTimeout(resolve, 1000))
      if (disposed) break
      report = await api.trialPreviewStatus(props.groupId)
    }
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    generatingReport.value = false
  }
}

async function showRequestPreview() {
  loadingPreview.value = true
  error.value = ''
  requestPreview.value = ''
  try {
    requestPreview.value = (await api.trialRequestPreview(props.groupId)).text
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    loadingPreview.value = false
  }
}
const dirty = computed(() => (props.plugin.fields ?? []).some(field => (draft.value[field.key] ?? '') !== (props.plugin[field.key] ?? '')))

function reset() {
  requestPreview.value = ''
  draft.value = { ...props.plugin }
  feedback.value = ''
  error.value = ''
}

watch(() => props.plugin, reset)

async function save() {
  if (!dirty.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const plugin = await api.saveGroupPlugin(props.groupId, draft.value.id, draft.value.enabled,
      draft.value.assistant_name, draft.value.intro_reply, draft.value.history_count, draft.value.max_chars, undefined, {trial_hours: draft.value.trial_hours, trial_limit: draft.value.trial_limit, trial_cooldown: draft.value.trial_cooldown, trial_prompt: draft.value.trial_prompt, trial_intro: draft.value.trial_intro, trial_summary: draft.value.trial_summary, trial_judges: draft.value.trial_judges, trial_clerk: draft.value.trial_clerk, trial_auto_warn: draft.value.trial_auto_warn, trial_warning_hours: draft.value.trial_warning_hours})
    emit('saved', plugin)
    await Promise.resolve()
    feedback.value = '配置已保存'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}
function setJudge(index: number, value: string) {
  const slots = (draft.value.trial_judges ?? '').split('\n')
  slots[index] = value
  draft.value.trial_judges = slots.join('\n')
}
</script>

<template>
  <a-card :bordered="false" class="plugin-settings">
    <form @submit.prevent="save">
      <a-tabs v-if="trial" v-model:active-key="tab">
        <a-tab-pane v-for="item in tabs" :key="item.id" :title="item.label" />
      </a-tabs>
      <p v-if="trial && tab === 'models'">接口地址和 API Key 在「插件管理 → 审判」统一配置。模型 ID 留空时使用默认模型。</p>
      <div v-if="trial && tab === 'models'" class="judge-slots">
          <label v-for="slot in 3" :key="slot">
            法官{{ slot }}号
            <a-input :model-value="draft.trial_judges?.split('\n')[slot - 1] ?? ''"
              :max-length="256" @update:model-value="value => setJudge(slot - 1, value)" />
          </label>
          <label>
            书记官模型 ID
            <a-input v-model="draft.trial_clerk" :max-length="256" />
          </label>
        </div>
        <div v-for="field in fields?.filter(item => item.key !== 'trial_judges' && item.key !== 'trial_clerk')" :key="field.key" class="settings-row field">
        <label :for="`plugin-${field.key}`">{{ field.label }}<small v-if="field.description">{{ field.description }}</small></label>
        <a-switch v-if="field.key === 'trial_auto_warn'" v-model="draft.trial_auto_warn" />
          <input v-else-if="field.key === 'history_count' || field.key === 'max_chars' || field.key === 'trial_hours' || field.key === 'trial_limit' || field.key === 'trial_cooldown' || field.key === 'trial_warning_hours'"
          :id="`plugin-${field.key}`" v-model.number="draft[field.key]" type="number" min="1" :max="field.max" required :disabled="busy" />
        <a-textarea v-else-if="field.type === 'textarea'" :id="`plugin-${field.key}`" :model-value="draft[field.key] ?? ''"
          :max-length="field.max_length" :auto-size="{ minRows: 3, maxRows: 9 }" :disabled="busy"
          @update:model-value="draft[field.key] = $event" />
        <a-input v-else :id="`plugin-${field.key}`" :model-value="draft[field.key] ?? ''"
          :placeholder="field.placeholder" :max-length="field.max_length" :disabled="busy"
          @update:model-value="draft[field.key] = $event" />
      </div>
      <div v-if="trial && tab === 'prompts'">
        <a-button :disabled="dirty" :loading="loadingPreview" @click="showRequestPreview">预览审判词</a-button>
        <p v-if="dirty">保存配置后可预览。预览使用固定示例聊天。</p>
        <pre v-if="requestPreview && !dirty" class="request-preview">{{ requestPreview }}</pre>
      </div>
      <a-alert v-if="error" type="error">{{ error }}</a-alert>
      <ol v-if="reportSteps.length" aria-live="polite">
        <li v-for="(step, index) in reportSteps" :key="index">{{ step }}</li>
      </ol>
      <div class="actions">
        <a-button v-if="trial" :disabled="dirty || busy" :loading="generatingReport" @click="followReport(true)">执行审判并生成 HTML</a-button>
        <a v-if="trial && reportUrl" :href="reportUrl" target="_blank" rel="noopener">打开 HTML</a>
        <span role="status">{{ dirty ? '有未保存的更改' : feedback }}</span>
        <a-button :disabled="!dirty || busy" @click="reset">还原更改</a-button>
        <a-button type="primary" html-type="submit" :disabled="!dirty" :loading="busy">保存配置</a-button>
      </div>
    </form>
  </a-card>
</template>

<style scoped>
.request-preview { max-height: 38vh; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; padding: 14px; background: white; }
.judge-slots { display: grid; gap: 12px; margin: 12px 0; }
.judge-slots label { display: grid; gap: 6px; }
.plugin-settings { background: var(--color-fill-1); border-radius: 12px; }
.settings-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 12px 0; }
.settings-row + .settings-row { border-top: 1px solid var(--color-border-2); }
.settings-row small { display: block; color: var(--color-text-3); margin-top: 4px; }
.field { display: grid; grid-template-columns: minmax(125px, .7fr) minmax(160px, 1.3fr); }
.actions { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.actions span { flex: 1; color: var(--color-text-3); }
@media (max-width: 600px) { .field { grid-template-columns: 1fr; gap: 6px; } }
</style>
