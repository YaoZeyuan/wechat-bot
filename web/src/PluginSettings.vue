<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Card as ACard, Input as AInput, Textarea as ATextarea } from '@arco-design/web-vue'
import { api, type GroupPlugin } from './api'

const props = defineProps<{ plugin: GroupPlugin; groupId: string }>()
const emit = defineEmits<{ saved: [GroupPlugin] }>()
const draft = ref({ ...props.plugin })
const busy = ref(false)
const feedback = ref('')
const error = ref('')
const dirty = computed(() => (props.plugin.fields ?? []).some(field => (draft.value[field.key] ?? '') !== (props.plugin[field.key] ?? '')))

function reset() {
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
      draft.value.assistant_name, draft.value.intro_reply)
    emit('saved', plugin)
    await Promise.resolve()
    feedback.value = '配置已保存'
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : String(cause)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <a-card :bordered="false" class="plugin-settings">
    <form @submit.prevent="save">
      <div v-for="field in plugin.fields" :key="field.key" class="settings-row field">
        <label :for="`plugin-${field.key}`">{{ field.label }}<small v-if="field.description">{{ field.description }}</small></label>
        <a-textarea v-if="field.type === 'textarea'" :id="`plugin-${field.key}`" :model-value="draft[field.key] ?? ''"
          :max-length="field.max_length" :auto-size="{ minRows: 3, maxRows: 9 }" :disabled="busy"
          @update:model-value="draft[field.key] = $event" />
        <a-input v-else :id="`plugin-${field.key}`" :model-value="draft[field.key] ?? ''"
          :placeholder="field.placeholder" :max-length="field.max_length" :disabled="busy"
          @update:model-value="draft[field.key] = $event" />
      </div>
      <a-alert v-if="error" type="error">{{ error }}</a-alert>
      <div class="actions">
        <span role="status">{{ dirty ? '有未保存的更改' : feedback }}</span>
        <a-button :disabled="!dirty || busy" @click="reset">还原更改</a-button>
        <a-button type="primary" html-type="submit" :disabled="!dirty" :loading="busy">保存配置</a-button>
      </div>
    </form>
  </a-card>
</template>

<style scoped>
.plugin-settings { background: var(--color-fill-1); border-radius: 12px; }
.settings-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 12px 0; }
.settings-row + .settings-row { border-top: 1px solid var(--color-border-2); }
.settings-row small { display: block; color: var(--color-text-3); margin-top: 4px; }
.field { display: grid; grid-template-columns: minmax(125px, .7fr) minmax(160px, 1.3fr); }
.actions { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.actions span { flex: 1; color: var(--color-text-3); }
@media (max-width: 600px) { .field { grid-template-columns: 1fr; gap: 6px; } }
</style>
